// Módulo de riego para casa-servidor (protocolo v1: ver docs/protocolo.md).
// SIN COMPILAR NI PROBAR en un ESP32 real: revisalo y probalo primero con el simulador (simulador_modulo.py).
// Biblioteca necesaria: ArduinoJson 7 (Gestor de bibliotecas). Placa: ESP32 (core de Arduino).
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <ArduinoJson.h>
#include "casa_credenciales.h"

const int PIN_VALVULA = 26;          // relé de la electroválvula (HIGH = abierta)
const int PIN_BOTON   = 27;          // botón manual a GND (INPUT_PULLUP)
const unsigned long TOPE_MS = 90UL * 60UL * 1000UL;   // tope LOCAL: la válvula nunca queda abierta más que esto
const char *T_DESC = "casa/riego/desc", *T_ESTADO = "casa/riego/estado", *T_CMD = "casa/riego/cmd";

WiFiClientSecure cliente;
bool abierta = false;
unsigned long cierraEn = 0;          // millis() en que se cierra sola (0 = sin plazo)
unsigned long ultimoPing = 0, keepaliveMs = 15000, proximoIntento = 0, espera = 1000;
bool botonAntes = true;
String linea;

void enviar(JsonDocument &d) {
  d["v"] = 1;
  serializeJson(d, cliente);
  cliente.print('\n');
}

void publicarEstado(const char *cmdId) {          // cmdId == nullptr: cambio manual o espontáneo
  JsonDocument d;
  d["t"] = "pub"; d["topic"] = T_ESTADO;
  d["valor"]["valvula"] = abierta ? "abierta" : "cerrada";
  if (cmdId) d["cmd_id"] = cmdId;
  enviar(d);
}

void poner(bool abrir, unsigned long plazoMs) {
  abierta = abrir;
  digitalWrite(PIN_VALVULA, abrir ? HIGH : LOW);
  cierraEn = (abrir ? millis() + (plazoMs ? min(plazoMs, TOPE_MS) : TOPE_MS) : 0);
}

bool conectarServidor() {
  if (WiFi.status() != WL_CONNECTED) { WiFi.begin(WIFI_SSID, WIFI_CLAVE); return false; }
  cliente.setCACert(CASA_CA);                      // valida el certificado del servidor con la CA propia
  if (!cliente.connect(CASA_HOST, CASA_PUERTO)) return false;
  JsonDocument h;
  h["t"] = "hola"; h["usuario"] = CASA_USUARIO; h["clave"] = CASA_CLAVE;
  enviar(h);
  String r = cliente.readStringUntil('\n');        // {"v":1,"t":"ok","keepalive":30}
  JsonDocument ok;
  if (deserializeJson(ok, r) || strcmp(ok["t"] | "", "ok") != 0) { cliente.stop(); return false; }
  keepaliveMs = (ok["keepalive"] | 30) * 500UL;    // ping a la mitad del keepalive
  JsonDocument d;
  d["t"] = "pub"; d["topic"] = T_DESC; d["nombre"] = "riego"; d["tipo"] = "riego";
  d["acciones"][0] = "abrir"; d["acciones"][1] = "cerrar";
  enviar(d);
  publicarEstado(nullptr);
  ultimoPing = millis(); espera = 1000;
  return true;
}

void procesar(const String &l) {
  JsonDocument m;
  if (deserializeJson(m, l)) return;
  if (strcmp(m["t"] | "", "msg") != 0 || strcmp(m["topic"] | "", T_CMD) != 0) return;
  const char *accion = m["accion"] | "", *id = m["id"] | nullptr;
  if (!id) return;
  if (strcmp(accion, "abrir") == 0)       poner(true, (m["params"]["minutos"] | 0) * 60000UL);
  else if (strcmp(accion, "cerrar") == 0) poner(false, 0);
  else return;                                      // acción desconocida: no confirma
  publicarEstado(id);                               // confirma con el mismo id
}

void setup() {
  pinMode(PIN_VALVULA, OUTPUT); digitalWrite(PIN_VALVULA, LOW);   // al arrancar siempre cerrada
  pinMode(PIN_BOTON, INPUT_PULLUP);
  WiFi.mode(WIFI_STA);
}

void loop() {
  unsigned long ahora = millis();
  if (abierta && cierraEn && (long)(ahora - cierraEn) >= 0) {    // plazo vencido: se cierra aunque no haya red
    poner(false, 0);
    if (cliente.connected()) publicarEstado(nullptr);
  }
  bool boton = digitalRead(PIN_BOTON);
  if (!boton && botonAntes) {                       // flanco: alterna a mano y avisa SIN cmd_id
    poner(!abierta, 0);
    if (cliente.connected()) publicarEstado(nullptr);
    delay(50);
  }
  botonAntes = boton;

  if (!cliente.connected()) {
    if (ahora >= proximoIntento) {
      if (!conectarServidor()) { proximoIntento = ahora + espera; espera = min(espera * 2, 60000UL); }
    }
    return;
  }
  if (ahora - ultimoPing >= keepaliveMs) { cliente.print("{\"v\":1,\"t\":\"ping\"}\n"); ultimoPing = ahora; }
  while (cliente.available()) {
    char c = cliente.read();
    if (c == '\n') { procesar(linea); linea = ""; }
    else if (linea.length() < 1024) linea += c;
    else { linea = ""; cliente.stop(); }            // línea absurda: se corta y se reconecta
  }
}
