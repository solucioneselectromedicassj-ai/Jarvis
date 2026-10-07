// ============================================================
//  Configuración del módulo. Editá SOLO este archivo por módulo.
//  Un mismo firmware sirve para todos: activás lo que lleva cada placa.
// ============================================================
#pragma once
#include <Arduino.h>

// ---------- Identidad (el id va en los topics MQTT: casa/<MODULE_ID>/...) ----------
#define MODULE_ID    "sala"            // minúsculas, sin espacios ni /
#define MODULE_NAME  "Módulo Sala"

// ---------- Red ----------
#define WIFI_SSID    "TU_WIFI"
#define WIFI_PASS    "TU_CLAVE"
#define OTA_PASS     "cambiame"        // actualización por WiFi (ArduinoOTA): poné una clave larga y propia

// ---------- Servidor MQTT propio de la casa ----------
// Lo normal: copiá acá el archivo mqtt_credenciales.h que genera "casa-servidor.py alta-modulo <id>".
// Trae host, usuario, clave y el certificado de TU autoridad (CA). Es secreto: no lo subas a ningún repositorio.
#if __has_include("mqtt_credenciales.h")
  #include "mqtt_credenciales.h"
#else
  #define MQTT_HOST    "192.168.1.50"       // IP fija del servidor (reservala en el router)
  #define MQTT_PORT    8883
  #define MQTT_USE_TLS 1
  #define MQTT_TLS_INSECURE 0               // 0 = valida el certificado con ROOT_CA. No lo pases a 1 en la casa real.
  #define MQTT_USER    "m-sala"
  #define MQTT_PASS    "TU_CLAVE_MQTT"
  static const char ROOT_CA[] PROGMEM = R"EOF(
-----BEGIN CERTIFICATE-----
PEGAR_ACA_LA_CA_DE_TU_CASA
-----END CERTIFICATE-----
)EOF";
#endif

// ============================================================
//  SALIDAS (relés / contactores / electroválvulas)
//  tipo: "switch" (luz) | "valve" (riego) | "heater" (calefón, estufa)
//  icono: luz | riego | estufa | calefon  (solo cambia el dibujo y las palabras de voz)
//  maxOn: tope de seguridad en segundos. El FIRMWARE apaga solo al llegar,
//         aunque se caiga el WiFi, el broker o la app. 0 = sin tope (solo luces).
//  Pines sugeridos (seguros al arranque): 16 17 18 19 21 22 23 25 26 27 32 33
// ============================================================
struct RelayCfg {
  const char* id; const char* nombre; const char* habitacion;
  const char* tipo; const char* icono;
  uint8_t pin; bool activoBajo; uint32_t maxOn;
};

#define N_RELAYS 3
static const RelayCfg RELAYS[N_RELAYS] = {
  // id   nombre           habitación  tipo      icono     pin  activoBajo maxOn(s)
  { "c1", "Luz living",    "Living",   "switch", "luz",    16,  true,      0    },
  { "c2", "Luz cocina",    "Cocina",   "switch", "luz",    17,  true,      0    },
  { "c3", "Estufa living", "Living",   "heater", "estufa", 18,  true,      7200 },
};

// ============================================================
//  SENSOR DE MOVIMIENTO (PIR, por ejemplo HC-SR501 o AM312)
// ============================================================
#define ENABLE_PIR   1
#define PIR_ID       "mov1"
#define PIR_NAME     "Movimiento living"
#define PIR_ROOM     "Living"
#define PIR_PIN      27
#define PIR_MIN_GAP_MS 10000           // mínimo entre avisos seguidos

// ============================================================
//  MEDIDOR DE ENERGÍA (PZEM-004T v3, 220 V)  — libre de relés propios
//  Librería: "PZEM-004T-v30" de Jakub Mandula
// ============================================================
#define ENABLE_PZEM  0
#define PZEM_ID      "en1"
#define PZEM_NAME    "Consumo general"
#define PZEM_ROOM    "Tablero"
#define PZEM_RX_PIN  25                // al TX del PZEM
#define PZEM_TX_PIN  26                // al RX del PZEM
#define PZEM_PERIOD_MS 5000

// ============================================================
//  PORTÓN (relé en pulso, en paralelo al pulsador de la botonera o del control)
//  El relé solo cierra el contacto un instante, igual que apretar el botón: las seguridades
//  del motor (fotocélulas, límite de fuerza) siguen funcionando y no se saltean.
//  El estado se lee con un sensor magnético (reed) en la posición CERRADO y, opcional, otro en ABIERTO.
//  Con un solo sensor, "no cerrado" se informa como "abierto" (no se distingue el movimiento).
// ============================================================
#define ENABLE_GATE           0
#define GATE_ID               "p1"
#define GATE_NAME             "Portón"
#define GATE_ROOM             "Entrada"
#define GATE_PULSE_PIN        19
#define GATE_PULSE_ACTIVE_LOW 1      // 1 = el relé se activa con LOW (placas de relé comunes)
#define GATE_PULSE_MS         600    // duración del contacto
#define GATE_MIN_GAP_MS       3000   // ignora pulsos más seguidos que esto (doble toque, comandos repetidos)
#define GATE_CLOSED_PIN       21     // reed entre el pin y GND (INPUT_PULLUP): LOW = portón cerrado
#define GATE_OPEN_PIN         -1     // opcional: reed en la posición abierto (LOW = abierto). -1 = no hay
#define GATE_WARN_S           300    // avisa si queda abierto más de esto

// ============================================================
//  EMISOR / RECEPTOR INFRARROJO (TV, aire, etc.)
//  Librería: "IRremoteESP8266". El emisor va por transistor, no directo al pin.
//  Los botones se APRENDEN desde la app (🎓) apuntando el control al receptor.
// ============================================================
#define ENABLE_IR    0
#define IR_SEND_PIN  4
#define IR_RECV_PIN  14

struct IrBtn { const char* cmd; const char* label; bool power; const char* voz; };  // voz: frases separadas por '|'

struct IrCfg {
  const char* id; const char* nombre; const char* habitacion; const char* icono;
  const IrBtn* btns; uint8_t n;
};

static const IrBtn BTN_TV[] = {
  { "tv_power",    "⏻",     true,  ""                           },
  { "tv_vol_up",   "Vol +", false, "subir volumen|volumen mas"   },
  { "tv_vol_down", "Vol −", false, "bajar volumen|volumen menos"},
  { "tv_mute",     "Mute",  false, "silencio|mute"              },
  { "tv_ch_up",    "CH +",  false, ""                           },
  { "tv_ch_down",  "CH −",  false, ""                           },
};
static const IrBtn BTN_AC[] = {
  { "ac_power",     "⏻",      true,  ""                  },
  { "ac_temp_up",   "Temp +", false, "subir temperatura" },
  { "ac_temp_down", "Temp −", false, "bajar temperatura" },
  { "ac_modo",      "Modo",   false, ""                  },
};

#define N_IR 2
static const IrCfg IRS[N_IR] = {
  { "ir1", "TV living",   "Living", "tv",   BTN_TV, sizeof(BTN_TV) / sizeof(IrBtn) },
  { "ir2", "Aire living", "Living", "aire", BTN_AC, sizeof(BTN_AC) / sizeof(IrBtn) },
};
