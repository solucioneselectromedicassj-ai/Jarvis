// ============================================================
//  Lógica de la bomba y sus destinos (tanque, plantas, pasto, manguera).
//  Código puro (sin Arduino): se prueba en la PC con test/bomba_test.cpp.
//
//  Reglas que garantiza (y que los tests verifican con miles de casos al azar):
//   1. Un solo destino a la vez.
//   2. La bomba solo arranca con la válvula de su destino abierta desde hace al menos openMs.
//   3. La bomba NUNCA está encendida si la cisterna no tiene agua (flotante).
//   4. La válvula no se cierra mientras la bomba está encendida: primero se apaga la bomba,
//      se espera closeMs y recién ahí se cierra.
//   5. Descanso mínimo entre arranques (cuida el motor).
//   6. Prioridad: manguera > órdenes del usuario > llenado automático del tanque.
// ============================================================
#pragma once
#include <stdint.h>

namespace bomba {

enum Zone : uint8_t { Z_TANQUE = 0, Z_PLANTAS = 1, Z_PASTO = 2, Z_MANGUERA = 3, N_ZONES = 4 };
enum Phase : uint8_t { P_REPOSO = 0, P_ABRIENDO = 1, P_BOMBEANDO = 2, P_PARANDO = 3 };
enum EvType : uint8_t {
  EV_NONE = 0, EV_INICIO, EV_FIN, EV_CISTERNA_VACIA, EV_TANQUE_NO_LLENA, EV_TOPE, EV_MANGUERA_LARGA
};
struct Event { EvType type; int8_t zone; uint32_t seg; };

struct Config {
  uint32_t openMs = 4000;            // válvula abierta antes de arrancar la bomba
  uint32_t closeMs = 4000;           // bomba apagada antes de cerrar la válvula (alivia la presión)
  uint32_t restMs = 20000;           // descanso mínimo entre dos arranques
  uint32_t tankRestMs = 600000;      // después de llenar, no vuelve a llenar solo antes de esto
  uint32_t tankMinRunMs = 30000;     // el llenado automático dura al menos esto (evita rebotes del flotante)
  uint32_t tankLockMs = 1800000;     // si el tanque no se llena en el tope, bloquea el automático este tiempo
  uint32_t cisternDebounceMs = 10000;// la cisterna debe tener agua estable este tiempo para arrancar
  uint32_t hoseWarnMs = 1800000;     // aviso cada vez que la manguera cumple este tiempo funcionando
  // Tope de marcha por destino. 0 = sin tope (la manguera queda sin tope a pedido del dueño).
  uint32_t capMs[N_ZONES] = { 2700000UL, 3600000UL, 3600000UL, 0UL };
};

struct Inputs  { bool cisternOk; bool tankNeeds; };      // cisternOk: hay agua. tankNeeds: el tanque pide agua.
struct Outputs { bool pump; bool valve[N_ZONES]; };

class Controller {
 public:
  explicit Controller(const Config& c = Config()) : cfg(c) {
    for (int i = 0; i < N_ZONES; i++) { want[i] = false; durMs[i] = 0; ranMs[i] = 0; order[i] = 0; nextWarn[i] = 0; }
  }

  // Orden del usuario (app, voz, botón). durMs = 0 → hasta el tope del destino (o sin tope).
  void command(Zone z, bool on, uint32_t duration, uint32_t now) {
    (void)now;
    if (z >= N_ZONES) return;
    if (on) {
      if (!want[z]) { want[z] = true; ranMs[z] = 0; order[z] = ++counter; nextWarn[z] = cfg.hoseWarnMs; }
      durMs[z] = duration;
    } else {
      want[z] = false; ranMs[z] = 0;
    }
  }

  Outputs tick(uint32_t now, const Inputs& in) {
    uint32_t dt = started ? (uint32_t)(now - lastTick) : 0;
    started = true; lastTick = now;

    // cisterna: estable antes de dar permiso
    if (!in.cisternOk) { cisternReadySince = 0; cisternSeenOk = false; }
    else if (!cisternSeenOk) { cisternSeenOk = true; cisternReadySince = now; }
    bool cisternReady = in.cisternOk && cisternSeenOk && (uint32_t)(now - cisternReadySince) >= cfg.cisternDebounceMs;

    // llenado automático del tanque
    bool tankAuto = in.tankNeeds && !tankBlocked(now);

    // Seguridad inmediata: sin agua en la cisterna, la bomba se apaga en este mismo ciclo
    if (phase == P_BOMBEANDO && !in.cisternOk) {
      push(EV_CISTERNA_VACIA, active, ranMs[active] / 1000);
      beginStop(now);
    }

    if (phase == P_BOMBEANDO) {
      ranMs[active] += dt;
      Zone z = (Zone)active;
      // aviso de manguera larga
      if (z == Z_MANGUERA && cfg.hoseWarnMs && ranMs[z] >= nextWarn[z]) {
        push(EV_MANGUERA_LARGA, z, ranMs[z] / 1000);
        nextWarn[z] += cfg.hoseWarnMs;
      }
      bool done = false;
      uint32_t cap = cfg.capMs[z];
      if (durMs[z] && ranMs[z] >= durMs[z]) {                       // terminó lo pedido
        push(EV_FIN, z, ranMs[z] / 1000); finish(z, now); done = true;
      } else if (cap && ranMs[z] >= cap) {                          // tope de seguridad
        if (z == Z_TANQUE) { push(EV_TANQUE_NO_LLENA, z, ranMs[z] / 1000); tankLockUntil = now + cfg.tankLockMs; tankLocked = true; }
        else push(EV_TOPE, z, ranMs[z] / 1000);
        finish(z, now); done = true;
      } else if (z == Z_TANQUE && !in.tankNeeds && ranMs[z] >= cfg.tankMinRunMs) {   // tanque lleno (también en pedido manual)
        push(EV_FIN, z, ranMs[z] / 1000); finish(z, now); done = true;
      } else if (!want[z] && !(z == Z_TANQUE && tankAuto)) {        // cancelada, o el tanque ya no pide agua
        if (z == Z_TANQUE && ranMs[z] < cfg.tankMinRunMs) {         // llenado automático: duración mínima
          // sigue un poco más para no hacer rebotar el flotante
        } else {
          push(EV_FIN, z, ranMs[z] / 1000);
          if (z == Z_TANQUE) { tankBlockUntil = now + cfg.tankRestMs; tankBlocked_ = true; ranMs[z] = 0; }
          done = true;
        }
      } else {
        int c = candidate(tankAuto);                                // ¿llegó algo de mayor prioridad?
        if (c >= 0 && c != active && prio(c, tankAuto) > prio(active, tankAuto)) done = true;
      }
      if (done) beginStop(now);
    }

    switch (phase) {
      case P_REPOSO: {
        int c = candidate(tankAuto);
        bool rested = !everStopped || (uint32_t)(now - lastPumpStop) >= cfg.restMs;
        if (c >= 0 && cisternReady && rested) { active = c; phase = P_ABRIENDO; phaseSince = now; }
        break; }
      case P_ABRIENDO:
        if (!cisternReady) { pumpWasOn = false; phase = P_PARANDO; phaseSince = now; }          // abortar sin haber arrancado
        else if ((uint32_t)(now - phaseSince) >= cfg.openMs) {
          int c = candidate(tankAuto);
          if (c != active && c >= 0 && prio(c, tankAuto) > prio(active, tankAuto)) { pumpWasOn = false; phase = P_PARANDO; phaseSince = now; }
          else if (!isWanted(active, tankAuto)) { pumpWasOn = false; phase = P_PARANDO; phaseSince = now; }
          else { phase = P_BOMBEANDO; phaseSince = now; pumpWasOn = true; push(EV_INICIO, active, 0); }
        }
        break;
      case P_PARANDO:
        if ((uint32_t)(now - phaseSince) >= (pumpWasOn ? cfg.closeMs : 0)) {
          phase = P_REPOSO; phaseSince = now; active = -1;
        }
        break;
      default: break;
    }

    // desbloqueos por tiempo
    if (tankBlocked_ && (int32_t)(now - tankBlockUntil) >= 0) tankBlocked_ = false;
    if (tankLocked && (int32_t)(now - tankLockUntil) >= 0) tankLocked = false;

    Outputs o; o.pump = (phase == P_BOMBEANDO);
    for (int i = 0; i < N_ZONES; i++) o.valve[i] = (active == i) && phase != P_REPOSO;
    return o;
  }

  Phase phase = P_REPOSO;
  int8_t active = -1;
  bool hoseMode() const { return want[Z_MANGUERA]; }
  bool wanted(Zone z) const { return want[z]; }
  uint32_t ranSeconds(Zone z) const { return ranMs[z] / 1000; }
  uint32_t remainingSeconds(Zone z) const {
    if (!want[z] || !durMs[z] || ranMs[z] >= durMs[z]) return 0;
    return (durMs[z] - ranMs[z]) / 1000;
  }
  bool tankAutoBlocked(uint32_t now) const { return tankBlocked(now); }
  bool popEvent(Event* e) {
    if (evHead == evTail) return false;
    *e = ev[evTail]; evTail = (evTail + 1) % EVN; return true;
  }

 private:
  static const int EVN = 12;
  Config cfg;
  bool want[N_ZONES]; uint32_t durMs[N_ZONES], ranMs[N_ZONES], order[N_ZONES], nextWarn[N_ZONES];
  uint32_t counter = 0, lastTick = 0, phaseSince = 0, lastPumpStop = 0;
  bool started = false, everStopped = false, pumpWasOn = false;
  bool cisternSeenOk = false; uint32_t cisternReadySince = 0;
  bool tankBlocked_ = false; uint32_t tankBlockUntil = 0;
  bool tankLocked = false; uint32_t tankLockUntil = 0;
  Event ev[EVN]; int evHead = 0, evTail = 0;

  bool tankBlocked(uint32_t now) const { (void)now; return tankBlocked_ || tankLocked; }
  void push(EvType t, int8_t z, uint32_t s) {
    int nx = (evHead + 1) % EVN; if (nx == evTail) return;       // cola llena: se descarta
    ev[evHead].type = t; ev[evHead].zone = z; ev[evHead].seg = s; evHead = nx;
  }
  bool isWanted(int z, bool tankAuto) const { return want[z] || (z == Z_TANQUE && tankAuto); }
  int prio(int z, bool tankAuto) const {
    if (z == Z_MANGUERA && want[z]) return 3;
    if (want[z]) return 2;
    if (z == Z_TANQUE && tankAuto) return 1;
    return 0;
  }
  int candidate(bool tankAuto) const {
    int best = -1, bp = 0; uint32_t bo = 0;
    for (int z = 0; z < N_ZONES; z++) {
      int p = prio(z, tankAuto);
      if (p == 0) continue;
      if (best < 0 || p > bp || (p == bp && order[z] < bo)) { best = z; bp = p; bo = order[z]; }
    }
    return best;
  }
  void finish(Zone z, uint32_t now) {
    want[z] = false; durMs[z] = 0; ranMs[z] = 0;
    if (z == Z_TANQUE) { tankBlocked_ = true; tankBlockUntil = now + cfg.tankRestMs; }
  }
  void beginStop(uint32_t now) {
    phase = P_PARANDO; phaseSince = now; lastPumpStop = now; everStopped = true;
  }
};

}  // namespace bomba
