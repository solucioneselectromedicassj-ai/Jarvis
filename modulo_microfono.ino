// Micrófono de la casa (ESP32 + INMP441): mantené apretado el botón, hablá, soltá. Sube el audio por HTTPS
// (POST /media/audio, PCM 16 kHz 16 bits mono, chunked) y Jarvis lo procesa en el servidor.
//
// SIN COMPILAR NI PROBAR EN HARDWARE. Escrito para el core ESP32 de Arduino 3.x (biblioteca ESP_I2S): si tu versión
// es la 2.x, la parte de I2S cambia (driver/i2s.h). Revisá los pines contra tu cableado. Primero probá el servidor
// con un audio de prueba y el simulador; después este sketch.
//
// Cableado INMP441: VDD->3V3, GND->GND, L/R->GND (canal izquierdo), SCK->PIN_BCLK, WS->PIN_WS, SD->PIN_DIN.
// Limitaciones de esta primera versión: no se conecta al canal de mensajes (no figura "online" y no recibe la
// respuesta de Jarvis); la respuesta se ve/escucha en el teléfono. El parlante queda para una etapa posterior.
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <ESP_I2S.h>
#include "mbedtls/base64.h"
#include "casa_credenciales.h"

const int PIN_BCLK = 14, PIN_WS = 15, PIN_DIN = 32;
const int PIN_BOTON = 27;                       // a GND, INPUT_PULLUP
const int PIN_LED = 2;
const uint32_t MAX_MS = 20000;                  // el servidor acepta hasta 30 s
const size_t MUESTRAS = 512;                    // por trozo (1 KB de audio)

I2SClass i2s;
WiFiClientSecure cliente;
String auth;

void ledParpadeo(int veces) { for (int i = 0; i < veces; i++) { digitalWrite(PIN_LED, HIGH); delay(80); digitalWrite(PIN_LED, LOW); delay(80); } }

bool wifi() {
  if (WiFi.status() == WL_CONNECTED) return true;
  WiFi.begin(WIFI_SSID, WIFI_CLAVE);
  for (int i = 0; i < 100 && WiFi.status() != WL_CONNECTED; i++) delay(100);
  return WiFi.status() == WL_CONNECTED;
}

void chunk(const uint8_t *d, size_t n) {         // un trozo en Transfer-Encoding: chunked
  char cab[12]; int l = snprintf(cab, sizeof(cab), "%X\r\n", (unsigned)n);
  cliente.write((const uint8_t *)cab, l); cliente.write(d, n); cliente.write((const uint8_t *)"\r\n", 2);
}

void grabarYSubir() {
  if (!wifi()) { ledParpadeo(5); return; }
  cliente.setCACert(CASA_CA);                    // valida el servidor con la CA propia
  if (!cliente.connect(CASA_HOST, CASA_PUERTO)) { ledParpadeo(4); return; }
  cliente.print(String("POST /media/audio HTTP/1.1\r\nHost: casa\r\nAuthorization: ") + auth +
                "\r\nContent-Type: audio/L16;rate=16000\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n");
  digitalWrite(PIN_LED, HIGH);
  int32_t crudo[MUESTRAS]; int16_t pcm[MUESTRAS];
  uint32_t t0 = millis();
  while (digitalRead(PIN_BOTON) == LOW && millis() - t0 < MAX_MS) {
    size_t n = i2s.readBytes((char *)crudo, sizeof(crudo)) / 4;           // el INMP441 entrega 24 bits en una palabra de 32
    for (size_t i = 0; i < n; i++) pcm[i] = (int16_t)(crudo[i] >> 14);    // a 16 bits (con algo de ganancia)
    if (n) chunk((uint8_t *)pcm, n * 2);
  }
  cliente.print("0\r\n\r\n");
  digitalWrite(PIN_LED, LOW);
  String estado = cliente.readStringUntil('\n');                           // "HTTP/1.1 202 Accepted"
  cliente.stop();
  if (estado.indexOf(" 202 ") > 0) ledParpadeo(1); else ledParpadeo(3);
}

void setup() {
  pinMode(PIN_BOTON, INPUT_PULLUP); pinMode(PIN_LED, OUTPUT);
  i2s.setPins(PIN_BCLK, PIN_WS, -1, PIN_DIN);
  if (!i2s.begin(I2S_MODE_STD, 16000, I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_MONO, I2S_STD_SLOT_LEFT)) { ledParpadeo(10); }
  char b64[160]; size_t n = 0; String up = String(CASA_USUARIO) + ":" + CASA_CLAVE;
  mbedtls_base64_encode((unsigned char *)b64, sizeof(b64), &n, (const unsigned char *)up.c_str(), up.length());
  auth = String("Basic ") + String(b64, n);
  WiFi.mode(WIFI_STA);
  wifi();
}

void loop() {
  if (digitalRead(PIN_BOTON) == LOW) { delay(30); if (digitalRead(PIN_BOTON) == LOW) grabarYSubir(); }
  delay(10);
}
