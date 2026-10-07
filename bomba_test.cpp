// Pruebas de la lógica de la bomba:  g++ -std=c++17 -O1 -o /tmp/bomba_test test/bomba_test.cpp && /tmp/bomba_test
#include "../firmware/bomba/bomba_logic.h"
#include <cstdio>
#include <cstdlib>
#include <vector>
using namespace bomba;

static int fails = 0, passes = 0;
#define CHECK(cond, msg) do { if (cond) { passes++; printf("ok   %s\n", msg); } else { fails++; printf("FALLA %s  (línea %d)\n", msg, __LINE__); } } while (0)

struct Sim {
  Controller c; uint32_t t; Inputs in; Outputs o; std::vector<Event> evs;
  explicit Sim(uint32_t t0 = 1000, const Config& cfg = Config()) : c(cfg), t(t0) { in.cisternOk = true; in.tankNeeds = false; o = c.tick(t, in); }
  void step(uint32_t ms) { t += ms; o = c.tick(t, in); Event e; while (c.popEvent(&e)) evs.push_back(e); }
  void run(uint32_t totalMs, uint32_t stepMs = 500) { for (uint32_t d = 0; d < totalMs; d += stepMs) step(stepMs); }
  int count(EvType ty, int z = -1) { int n = 0; for (auto& e : evs) if (e.type == ty && (z < 0 || e.zone == z)) n++; return n; }
  int openValves() { int n = 0; for (int i = 0; i < N_ZONES; i++) n += o.valve[i]; return n; }
};

int main() {
  { // 1. llenado automático: válvula primero, bomba después; al llenarse, bomba primero y válvula después
    Sim s; s.run(15000);                                     // pasa el debounce de la cisterna
    s.in.tankNeeds = true; s.step(500);
    CHECK(s.o.valve[Z_TANQUE] && !s.o.pump, "tanque bajo: abre la válvula y todavía no arranca la bomba");
    s.run(2000); CHECK(!s.o.pump, "durante la espera de apertura la bomba sigue apagada");
    s.run(3000); CHECK(s.o.pump && s.o.valve[Z_TANQUE], "pasada la espera arranca la bomba");
    s.run(60000); CHECK(s.o.pump, "sigue bombeando mientras el tanque pide agua");
    s.in.tankNeeds = false; s.step(500);
    CHECK(!s.o.pump && s.o.valve[Z_TANQUE], "tanque lleno: apaga la bomba y la válvula sigue abierta");
    s.run(5000); CHECK(!s.o.valve[Z_TANQUE] && s.openValves() == 0, "después de la espera cierra la válvula");
    CHECK(s.count(EV_INICIO, Z_TANQUE) == 1 && s.count(EV_FIN, Z_TANQUE) == 1, "eventos de inicio y fin");
  }
  { // 2. cisterna vacía: la bomba se apaga en el mismo ciclo y no arranca hasta que el agua esté estable
    Sim s; s.run(15000); s.in.tankNeeds = true; s.run(8000);
    CHECK(s.o.pump, "bombeando");
    s.in.cisternOk = false; s.step(100);
    CHECK(!s.o.pump, "cisterna sin agua: la bomba se apaga de inmediato");
    CHECK(s.count(EV_CISTERNA_VACIA) == 1, "aviso de cisterna vacía");
    s.run(10000); CHECK(!s.o.pump && s.openValves() == 0, "con la cisterna vacía no arranca y las válvulas quedan cerradas");
    s.in.cisternOk = true; s.run(3000); CHECK(!s.o.pump, "al volver el agua espera a que se estabilice");
    s.run(30000); CHECK(s.o.pump, "con el agua estable vuelve a arrancar");
  }
  { // 3. el flotante de la cisterna que rebota no hace arrancar la bomba
    Sim s; s.run(15000); s.in.tankNeeds = true;
    bool everPump = false;
    for (int i = 0; i < 40; i++) { s.in.cisternOk = (i % 2 == 0); s.step(2000); everPump |= s.o.pump; }
    CHECK(!everPump, "flotante que rebota cada 2 s: la bomba no arranca");
  }
  { // 4. manguera: prioridad, sin tope, y el automático vuelve al terminar
    Sim s; s.run(15000); s.in.tankNeeds = true; s.run(10000);
    CHECK(s.o.pump && s.o.valve[Z_TANQUE], "llenando el tanque");
    s.c.command(Z_MANGUERA, true, 0, s.t); s.step(500);
    CHECK(!s.o.pump, "al pedir manguera se apaga la bomba primero");
    s.run(30000); CHECK(s.o.valve[Z_MANGUERA] && !s.o.valve[Z_TANQUE], "pasa a la manguera (respetando el descanso del motor)");
    s.run(30000); CHECK(s.o.pump && s.o.valve[Z_MANGUERA] && s.openValves() == 1, "manguera en marcha, una sola válvula");
    s.run(3 * 3600 * 1000UL, 1000);
    CHECK(s.o.pump && s.c.hoseMode(), "la manguera sigue después de 3 horas (sin tope)");
    CHECK(s.count(EV_MANGUERA_LARGA) >= 5, "avisa cada 30 minutos que la manguera sigue");
    s.c.command(Z_MANGUERA, false, 0, s.t); s.step(500);
    CHECK(!s.c.hoseMode() && !s.o.pump, "al apagar la manguera para la bomba");
    s.run(40000); CHECK(s.o.pump && s.o.valve[Z_TANQUE], "vuelve solo al automático: sigue llenando el tanque");
  }
  { // 5. tope del llenado: si el tanque no se llena, para y bloquea el automático
    Sim s; s.run(15000); s.in.tankNeeds = true; s.run(46 * 60 * 1000UL, 1000);
    CHECK(s.count(EV_TANQUE_NO_LLENA) == 1, "tanque que no se llena en 45 min: aviso");
    CHECK(!s.o.pump, "y se apaga");
    s.run(10 * 60 * 1000UL, 1000); CHECK(!s.o.pump, "queda bloqueado el automático (no insiste)");
    s.run(30 * 60 * 1000UL, 1000); CHECK(s.o.pump, "pasado el bloqueo vuelve a intentar");
  }
  { // 6. órdenes con duración
    Sim s; s.run(15000);
    s.c.command(Z_PLANTAS, true, 120000, s.t); s.run(10000);
    CHECK(s.o.pump && s.o.valve[Z_PLANTAS], "riego de plantas en marcha");
    CHECK(s.c.remainingSeconds(Z_PLANTAS) > 100 && s.c.remainingSeconds(Z_PLANTAS) <= 120, "informa el tiempo que resta");
    s.run(125000); CHECK(!s.o.pump && s.count(EV_FIN, Z_PLANTAS) == 1, "termina solo al cumplir el tiempo");
  }
  { // 7. un solo destino a la vez y cola por orden de llegada
    Sim s; s.run(15000);
    s.c.command(Z_PLANTAS, true, 60000, s.t); s.c.command(Z_PASTO, true, 60000, s.t); s.run(10000);
    CHECK(s.o.valve[Z_PLANTAS] && !s.o.valve[Z_PASTO] && s.openValves() == 1, "dos pedidos: atiende primero al que llegó antes");
    s.run(70000); s.run(40000);
    CHECK(s.o.valve[Z_PASTO] || s.count(EV_FIN, Z_PASTO) == 1, "después atiende al segundo");
  }
  { // 8. descanso mínimo entre arranques
    Sim s; s.run(15000);
    s.c.command(Z_PASTO, true, 10000, s.t); s.run(20000);
    uint32_t endT = s.t; (void)endT;
    s.c.command(Z_PASTO, true, 10000, s.t); s.step(500);
    s.run(3000); CHECK(!s.o.pump, "no vuelve a arrancar enseguida (descanso del motor)");
    s.run(30000); CHECK(s.o.pump || s.count(EV_FIN, Z_PASTO) == 2, "arranca pasado el descanso");
  }
  { // 9. cancelar en cualquier momento
    Sim s; s.run(15000);
    s.c.command(Z_PASTO, true, 0, s.t); s.run(8000); CHECK(s.o.pump, "regando");
    s.c.command(Z_PASTO, false, 0, s.t); s.step(500);
    CHECK(!s.o.pump && s.o.valve[Z_PASTO], "cancelar: bomba apagada y válvula todavía abierta");
    s.run(5000); CHECK(s.openValves() == 0, "luego se cierra la válvula");
  }
  { // 10. el contador de millis() da la vuelta (cada ~49 días) y todo sigue igual
    Sim s(0xFFFFFFFFu - 20000u); s.run(15000); s.in.tankNeeds = true; s.run(20000);
    CHECK(s.o.pump && s.o.valve[Z_TANQUE], "arranca aunque millis() dé la vuelta durante la espera");
    s.run(60000); s.in.tankNeeds = false; s.run(10000);
    CHECK(!s.o.pump && s.openValves() == 0, "y termina bien del otro lado");
  }
  { // 11. prueba al azar: 300 000 pasos con órdenes y flotantes aleatorios; las reglas de seguridad no se rompen nunca
    srand(12345); long bad = 0; long pumpTicks = 0;
    for (int run = 0; run < 30; run++) {
      Sim s((uint32_t)rand() * 7u); uint32_t pumpSince = 0; bool prevPump = false; int openFor[N_ZONES] = {0}; (void)openFor;
      uint32_t valveOpenedAt[N_ZONES]; bool prevValve[N_ZONES];
      for (int i = 0; i < N_ZONES; i++) { valveOpenedAt[i] = 0; prevValve[i] = false; }
      uint32_t lastOff = 0; bool hadOff = false;
      for (int i = 0; i < 10000; i++) {
        int r = rand() % 100;
        if (r < 4) s.c.command((Zone)(rand() % N_ZONES), rand() % 2, (rand() % 3) ? (rand() % 90000) : 0, s.t);
        if (r >= 4 && r < 8) s.in.cisternOk = rand() % 4 != 0;
        if (r >= 8 && r < 12) s.in.tankNeeds = rand() % 2;
        s.step(100 + rand() % 3000);
        int open = s.openValves();
        if (open > 1) bad++;                                                      // un solo destino
        if (s.o.pump && open != 1) bad++;                                         // bomba ⇒ exactamente una válvula abierta
        if (s.o.pump && !s.in.cisternOk) bad++;                                   // bomba ⇒ cisterna con agua
        for (int z = 0; z < N_ZONES; z++) {                                       // válvula abierta ≥ openMs antes de arrancar
          if (s.o.valve[z] && !prevValve[z]) valveOpenedAt[z] = s.t;
          if (s.o.pump && !prevPump && s.o.valve[z] && (uint32_t)(s.t - valveOpenedAt[z]) < 4000) bad++;
          if (prevPump && s.o.pump == true && prevValve[z] && !s.o.valve[z]) bad++;   // la válvula no se cierra con la bomba encendida
          if (prevPump && !s.o.pump && false) {}
          prevValve[z] = s.o.valve[z];
        }
        if (prevPump && !s.o.pump) { lastOff = s.t; hadOff = true; }
        if (!prevPump && s.o.pump && hadOff && (uint32_t)(s.t - lastOff) < 20000) bad++;   // descanso mínimo
        if (prevPump && !s.o.pump) { for (int z = 0; z < N_ZONES; z++) if (prevValve[z] && !s.o.valve[z]) bad++; }  // al apagar la bomba la válvula sigue abierta ese ciclo
        if (s.o.pump) pumpTicks++;
        prevPump = s.o.pump; (void)pumpSince;
      }
    }
    char m[120]; snprintf(m, sizeof m, "300 000 pasos al azar: 0 violaciones de seguridad (violaciones=%ld, ciclos con bomba=%ld)", bad, pumpTicks);
    CHECK(bad == 0 && pumpTicks > 1000, m);
  }
  { // 12. "subir agua al tanque" pedido a mano: se detiene solo cuando el flotante marca tanque lleno
    Sim s; s.run(15000); s.in.tankNeeds = true;
    s.c.command(Z_TANQUE, true, 0, s.t); s.run(20000);
    CHECK(s.o.pump, "pedido manual de llenado: bombea mientras el tanque pide agua");
    s.in.tankNeeds = false; s.run(40000);
    CHECK(!s.o.pump && !s.c.wanted(Z_TANQUE) && s.count(EV_FIN, Z_TANQUE) == 1, "al llenarse el tanque termina solo (no espera el tope de 45 min)");
  }
  printf("\n%d ok, %d fallaron\n", passes, fails);
  return fails ? 1 : 0;
}
