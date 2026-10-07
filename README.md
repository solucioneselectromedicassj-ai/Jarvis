# Casa — domótica propia (fases 1 y 2)

PWA de un solo HTML (React por CDN, sin build) + firmware ESP32 genérico, hablando MQTT.
Servidor propio y seguro (Mosquitto + VPN). Bomba, cisterna y riego. Luces, riego, calefón y estufas eléctricas, TV y aire por infrarrojo, sensor de movimiento, medidor de energía, avisos y comandos por voz, **portón** (pulso + estado + aviso al llegar) y **portero** (timbre con foto, video a demanda, hablar y escuchar, abrir la puerta).

```
index.html  manifest.json  sw.js  icon-*.png      ← la app (la sirve tu servidor)
servidor/casa-servidor.py, renovar-cert-vpn.sh      ← servidor propio: CA, usuarios, permisos, Mosquitto, Caddy
firmware/modulo_casa/                               ← un firmware para los módulos comunes (luces, riego, IR, energía, portón)
firmware/portero/                                   ← ESP32-S3 con cámara: timbre, video, voz y cerradura
firmware/bomba/                                     ← bomba + 4 electroválvulas (tanque, plantas, pasto, manguera) + flotantes
test/logic.test.js                                  ← pruebas de la app: node test/logic.test.js
test/bomba_test.cpp                                 ← pruebas de la lógica de la bomba: g++ -std=c++17 -O1 -o /tmp/bt test/bomba_test.cpp && /tmp/bt
```

## Probarla ya (sin hardware)
Abrí `index.html` (mejor servida por https para voz e instalación). Arranca en **modo demo**: módulos simulados que se comportan como los reales, con los mismos topes de seguridad. Probá "prender la luz del living", "regar el jardín 10 minutos", "apagar todo".

## Ponerla en la casa (servidor propio, sin nada expuesto a internet)

```
 módulos ESP32 ──TLS 8883──┐
                           ├─ Mosquitto (mini-servidor en tu casa) ── Caddy (https + wss, solo por la VPN)
 teléfonos ──VPN Tailscale─┘                                                   └─ la app (esta carpeta)
```
Nada se publica en el router: ningún puerto abierto. Desde afuera se entra únicamente por la VPN. Los datos (fotos, audio, estados) nunca pasan por un tercero.

**Qué necesitás:** una mini-PC o Raspberry Pi 4/5 con Linux (mejor con SSD y UPS chico), conectada por cable y con IP fija (reservala en el router), y una cuenta de Tailscale. Verificá en su página los límites vigentes del plan gratuito (usuarios y dispositivos) antes de sumar a tu señora y a tu hija.

1. **Instalar:** `sudo apt install mosquitto mosquitto-clients openssl caddy` y copiar `servidor/` y esta carpeta de la app (a `/var/www/casa`).
2. **Crear la autoridad y la configuración (una sola vez):**
   `sudo ./casa-servidor.py init --ip 192.168.1.50 --vpn-host casa.xxxx.ts.net --vpn-ip 100.x.y.z`
   Genera tu CA propia, el certificado del servidor, la configuración de Mosquitto (sin acceso anónimo, TLS, un solo usuario por cosa) y el `Caddyfile`.
3. **VPN:** instalá Tailscale en el servidor y en cada teléfono; en el panel activá MagicDNS y HTTPS. Luego `sudo ./renovar-cert-vpn.sh casa.xxxx.ts.net` y ponelo en el cron semanal. Copiá `/etc/casa-servidor/caddy/Caddyfile` a `/etc/caddy/Caddyfile` y reiniciá Caddy y Mosquitto.
4. **Cada módulo:** `sudo ./casa-servidor.py alta-modulo sala > mqtt_credenciales.h` (el portero con `--camara`). Copiá ese archivo junto al sketch: el `MODULE_ID` del `config.h` tiene que ser el mismo id. Compilá y cargá.
5. **Cada persona:** `sudo ./casa-servidor.py alta-persona pablo` (completo), `alta-persona señora` y para tu hija `alta-persona hija --rol basico --cmd sala,ir-living` (ve estados y avisos, y solo da órdenes a esos módulos; no puede abrir portón ni puerta, ni ver cámara ni escuchar). La clave se muestra una vez.
6. **App:** abrila desde `https://casa.xxxx.ts.net` en el teléfono (con la VPN encendida), ⚙️ → *Casa real*, tu usuario y tu clave. Se puede instalar como app.

Otros comandos: `lista` (quién existe y qué puede), `clave <usuario>` (nueva clave), `baja <usuario>` (teléfono perdido: se elimina y se corta la sesión), `renovar-servidor` (si cambia la IP; la CA no cambia y los módulos no se recargan).

## Protocolo (todo JSON)
| Topic | Quién | Contenido |
|---|---|---|
| `casa/<id>/desc` (retenido) | módulo | `{id,nombre,canales:[{id,tipo,icono,nombre,habitacion,maxOn?,botones?}]}` |
| `casa/<id>/estado` (retenido) | módulo | por canal: salidas `{on,resta}`, movimiento `{activo}`, energía `{w,v,a,kwh,fp}` |
| `casa/<id>/online` (retenido) | módulo / broker | `"1"` / `"0"` (el `"0"` lo publica el broker si el módulo se cae) |
| `casa/<id>/evento` | módulo | `movimiento`, `auto_apagado`, `temporizador_fin`, `ir_enviado`, `ir_aprendido`, `ir_error` |
| `casa/<id>/cmd` | app | `{"c":"c1","on":true,"seg":600}` · `{"c":"ir1","ir":"tv_power"}` · `{"c":"ir1","aprender":"tv_power"}` · portón `{"c":"p1","pulso":true}` · portero `{"c":"cam","foto":true}` `{"ver":30}` `{"escuchar":true}` `{"abrir":true}` |
| `casa/<id>/foto`, `casa/<id>/video` | portero | JPEG binario (foto al sonar el timbre o al pedirla; cuadros mientras dura `ver`) |
| `casa/<id>/audio/out` | portero | PCM 16 bit, mono, 8 kHz, binario: lo que capta el micrófono mientras dura `escuchar` |
| `casa/<id>/audio/in` | app | el mismo PCM pero en **base64** (texto): el navegador no puede publicar bytes crudos con mqtt.js |

Tipos de canal: `switch`, `valve`, `heater`, `motion`, `energy`, `ir`, `gate`, `doorbell`. Eventos nuevos: `timbre`, `puerta_abierta`, `porton_abierto_mucho`. No uses `config` como id de módulo. Para sumar un módulo propio nuevo alcanza con que publique su `desc` y su `estado` con ese formato.

## Portón
- **Hardware:** un relé en paralelo al pulsador de la botonera (o a los contactos del control remoto) y un sensor magnético (reed) en la posición *cerrado*. Opcional: otro en *abierto* para distinguir "moviéndose". Activá `ENABLE_GATE` en `config.h` del módulo.
- **Cómo opera:** cada orden es un pulso de 0,6 s, igual que apretar el botón. Las fotocélulas y el límite de fuerza del motor siguen mandando: el módulo no las saltea.
- **En la app:** tarjeta con el estado (Cerrado / Abierto / Moviéndose) y botón con confirmación. Por voz: "abrir el portón" / "cerrar el portón"; solo pulsa si el estado lo justifica (no manda "abrir" si ya está abierto ni si se está moviendo).
- **Aviso:** si queda abierto más de 5 min (`GATE_WARN_S`) llega un aviso.
- **Al llegar (⚙️ Ajustes):** definís la ubicación de casa y un radio. Al cruzarlo, la app te **pregunta** si abre el portón, o lo abre sola si lo activás (solo si está cerrado y como mucho una vez cada 10 min). **Funciona solo con la app abierta**: un navegador no sigue la ubicación con el teléfono bloqueado. Para abrir de verdad al llegar sin tocar nada, usá una automatización del teléfono (Tasker o MacroDroid en Android, Atajos en iPhone) que publique `{"c":"p1","pulso":true}` en `casa/<módulo>/cmd` al entrar en la zona; ahí el criterio de seguridad lo ponés vos en la automatización.

## Portero
- **Hardware:** ESP32-S3 con cámara (probable: Seeed XIAO ESP32S3 Sense), pulsador de timbre, micrófono I2S INMP441, amplificador I2S MAX98357A con parlante y un relé para la cerradura eléctrica. Los pines están en `firmware/portero/config.h`; la cámara es `CAM_BOARD 1` (XIAO) o `2` (AI-Thinker, sin audio).
- **Timbre:** al apretarlo el módulo manda el evento y una foto; la app (si está abierta) abre la pantalla del portero con la foto.
- **Pantalla del portero:** 📷 foto, 🎥 ver 30 s (video a 3 cuadros por segundo, se corta solo), 👂 escuchar la puerta, 🎤 mantener apretado para hablar (por turnos: mientras hablás, el micrófono de la puerta se calla para evitar acople) y 🔓 abrir la puerta (con confirmación; el ancho del pulso lo fija el módulo).
- **Por qué MQTT y no WebRTC:** no necesita servidor TURN ni configuración de red y funciona desde cualquier lado con el mismo broker. El costo es calidad de "walkie-talkie": video de baja resolución y ~0,3–0,8 s de demora en el audio. Un video fluido con voz simultánea requeriría WebRTC (más adelante, si hace falta).
- **Consumo de datos:** ~30 KB/s de video y ~16 KB/s de audio, solo mientras hay una sesión abierta (tope de 60 s por pedido). Es tráfico de tu propia red; con la VPN cuenta contra el plan de datos del teléfono.
- **Privacidad:** el servidor es tuyo, así que imágenes y audio no salen de tu casa. Cada módulo y cada persona tiene su usuario; solo el rol completo puede ver cámara y escuchar.

## Bomba y riego (tanque, plantas, pasto, manguera)
**Idea:** una bomba de ½ HP y cuatro salidas con electroválvula. El sistema está **siempre en automático**: llena el tanque cuando el flotante lo pide. El modo **Manguera** (sin tiempo, para regar o limpiar) pasa por delante de todo y, al apagarlo, el sistema retoma solo lo que estaba haciendo.

**Reglas de seguridad (están en `firmware/bomba/bomba_logic.h` y se prueban con miles de casos al azar):**
1. Un solo destino a la vez; si piden dos, el segundo espera. La manguera tiene prioridad; después, lo que pide el usuario; al final, el llenado automático.
2. Primero se abre la válvula, se espera 4 s y recién ahí arranca la bomba. Al terminar, primero se apaga la bomba, se espera 4 s y se cierra la válvula. La bomba nunca trabaja contra todo cerrado.
3. Con la cisterna sin agua la bomba se apaga en ese mismo instante y no vuelve hasta que el flotante esté estable 10 s.
4. Descanso mínimo de 20 s entre arranques. Si el tanque no se llena en 45 min: aviso y el automático se bloquea 30 min (puede haber una pérdida).
5. Topes: pasto y plantas 60 min. **La manguera no tiene tope** (a pedido), pero avisa cada 30 min mientras siga abierta y la cisterna vacía siempre la corta.
6. Todo arranca apagado. Si se cae el WiFi o el servidor, el módulo sigue con sus reglas y botones locales; al reiniciarse no retoma pedidos pendientes.

**En la app:** una tarjeta "Bomba" (reposo / en marcha y a dónde, cisterna con agua o sin, tanque bajo u ok, Automático o Modo manguera) y una tarjeta por destino. Por voz: "llenar el tanque", "subir agua al tanque", "regar el pasto 10 minutos", "abrir la manguera", "cerrar la manguera".

**Módulo:** `firmware/bomba/` (ESP32 + placa de 5 relés: bomba + 4 válvulas). Alta en el servidor: `sudo ./casa-servidor.py alta-modulo bomba > firmware/bomba/mqtt_credenciales.h`.

**Esquema para el electricista (propuesta a revisar, no un proyecto firmado):**
```
 Fase ─ térmica ─ llave [Manual · Auto · 0] ─┬─ Auto ──── relé del ESP32 ──┐
                                              └─ Manual ───────────────────┤
                                                                           ├─ flotante cisterna (cierra con agua)
                                                                           ├─ flotante tanque (cierra si el tanque NO está lleno)
                                                                           └─ bobina del contactor ─ Neutro
```
- Los flotantes quedan **en serie con la bobina**: si todo lo electrónico falla, la bomba igual no trabaja en seco ni rebalsa el tanque.
- Los mismos contactos de los flotantes se leen con módulos de entrada con optoacoplador (nunca directo al ESP32 si están a 220 V).
- **Modo Manual (sin ESP32):** esa posición debe alimentar también la electroválvula del **tanque** y el contactor con un relé temporizado de unos 5 s, para que la bomba no arranque con todo cerrado. En Manual solo se llena el tanque; los demás destinos se manejan con la app o con botones locales (que necesitan el ESP32, no el WiFi).
- Contactor de unos 20 A (el arranque de un motor de ½ HP es varias veces la corriente nominal), térmica y diferencial propios.
- Electroválvulas de **12 o 24 V** con fuente aparte (están a la intemperie); verificá en la ficha que sean para la presión de tu bomba. Válvula de retención después de la bomba.
- Si la cisterna sin agua apaga todo, lo primero que hay que revisar es el flotante y el nivel de la red.

**Lo que todavía no hace (siguiente paso):** el riego **automático por humedad de tierra** (módulo "jardín" con sensor capacitivo y temperatura; hoy el riego de plantas y pasto se pide a mano, por voz o por botón), horarios guardados en el módulo y placa con **Ethernet** (hoy solo WiFi). Con el sensor resistivo de dos puntas que tenés, la lectura se deteriora en semanas: para dejarlo conviene el capacitivo.

## Seguridad (lo que hace y lo que te toca a vos)
- **Los topes viven en el módulo.** Calefón y estufas llevan `maxOn` (por defecto 1 h y 2 h): el ESP32 los apaga solo aunque se caiga WiFi, broker o app. Todas las salidas arrancan apagadas.
- **Calefón y estufas son cargas grandes:** relé de señal → **contactor** dimensionado para la corriente (típico 25 A para un calefón de 2–3 kW), protegido con térmica y diferencial propios, y la parte de 220 V en caja separada del ESP32. Esto conviene que lo revise un electricista matriculado.
- **Aire y TV** por infrarrojo (emisor con transistor). Los botones se aprenden desde la app con el 🎓 apuntando el control al receptor del módulo.
- **Portón y cerradura:** el relé solo da un pulso corto y el ancho no viene en el comando. Poné la cerradura eléctrica con su fuente propia y un diodo en antiparalelo en la bobina, y no alimentes nada de 12 V desde el ESP32.
- **Quién puede abrir:** solo los usuarios con rol completo. El servidor lo impone (permisos por usuario), no la app: aunque alguien modifique la app, un usuario básico no puede publicar en el `cmd` del portón. Cada módulo solo escribe en sus propios topics y solo lee su `cmd`; un módulo comprometido no puede dar órdenes a otros.
- **Cifrado y validación:** los módulos validan el certificado del servidor con tu CA (`MQTT_TLS_INSECURE 0`); la CA es tuya y vale hasta 2049, con fecha de inicio 1969 para que funcione aunque el ESP32 arranque sin hora. La CA privada (`/etc/casa-servidor/ca/ca.key`) queda solo para root: **guardala en un respaldo fuera del servidor**.
- **Qué te toca a vos:** teléfono con bloqueo de pantalla; claves distintas por persona; si se pierde un teléfono, `sudo ./casa-servidor.py bloquear p-nombre` lo corta al instante desde cualquier PC con acceso al servidor (se deshace con `desbloquear`, que da clave nueva), o `baja` para borrarlo; actualizar el sistema del servidor; respaldo de `/etc/casa-servidor`; UPS y SSD. El servidor es un punto único de falla: si se cae, los módulos mantienen sus topes de seguridad pero no hay control remoto hasta que vuelva.
- Nunca pases `MQTT_TLS_INSECURE` a 1 en la casa real.

## Límites conocidos
- **Los avisos (timbre, movimiento, consumo alto, portón abierto) llegan solo con la app abierta:** un navegador no mantiene MQTT con el teléfono bloqueado. Para recibirlos con el teléfono guardado hace falta un servicio siempre encendido que se suscriba al servidor y mande push (un Node chico en el mismo servidor; usaría el usuario `alta-servicio`, de solo lectura). Es el siguiente paso, y conviene hacerlo antes de usar el portero en serio.
- **Cámaras fijas** (más allá del portero), aire y estufas con medición por circuito: fase 3.
- **Estado de la verificación:** el servidor se probó con un Mosquitto 2.0.18 real: TLS con la CA propia, rechazo de conexiones sin clave o sin verificar, 27 pruebas de permisos (módulos, portero, rol completo y rol básico), WebSocket y baja de usuarios. **No probé** Caddy ni Tailscale (no están en mi entorno): seguí los pasos y revisá `caddy validate`. La lógica de la app (voz, portón, geovalla, audio) está probada con 50 pruebas y la pantalla se renderizó en un navegador simulado. El firmware se verificó compilando contra la API real de ArduinoJson y la de `ESP_I2S`, con el resto de las librerías simuladas. **Falta la primera compilación y prueba sobre las placas**, sobre todo cámara, micrófono y parlante, que son lo más sensible a cableado y a la versión del core.


## Jarvis en la app (paso 3)
- La barra de arriba entiende frases como «Ey Jarvis, prendé la luz del living», «qué está prendido» o «apagá todo» y responde en pantalla (y en voz alta si lo activás en Ajustes).
- Estufas y calefón piden confirmación. «Dentro de casa» es estar conectado al servidor por la red de casa; por la VPN cuenta como afuera, y ahí encender por voz o texto pide tu PIN (Ajustes). Las tarjetas siguen funcionando igual.
- El portón no se abre por voz. Al llegar (aviso de ubicación) aparece «¿Abro el portón?» con un botón.
- El micrófono del navegador envía el audio a la nube: viene apagado. Probar la PWA entera: `CDN_DIR=<carpeta con react, react-dom, @babel/standalone y mqtt instalados con npm> NODE_PATH=$(npm root -g) node test/pwa.test.js`.

## Hablarle a Jarvis (voz local, sin nube)
La app trae un reconocedor de voz que corre en el propio dispositivo (Vosk): el audio no sale. Primero escucha solo «Ey Jarvis»; después reconoce únicamente frases de tu casa (por eso entiende mejor y no te escucha charlar). Mientras espera una confirmación solo oye «sí» o «no». Mientras Jarvis habla, no se oye a sí mismo.
1. Preparar el modelo: seguí `modelo/LEEME.txt`.
2. En la PC: ejecutá `servir.ps1` (clic derecho > Ejecutar con PowerShell). Se abre http://localhost:8080 y ahí el micrófono funciona. Abriendo `index.html` con doble clic solo anda escribir, porque el navegador no deja usar el micrófono ni cargar el modelo desde un archivo.
3. Tocá 🎤: carga la voz (unos segundos la primera vez), y queda «👂 decí Ey Jarvis». Tocá 🎤 de nuevo para hablar sin decir la palabra, o el cartel de arriba para apagar la escucha.
4. Las frases que el modelo no conoce (por ejemplo «prendé», «apagá») se detectan con `jarvis/verificar_vocabulario.py`; si falla alguna, hay que cambiarla por un sinónimo.
- En el teléfono el micrófono exige https (o activar en Chrome la bandera «Insecure origins treated as secure» para la dirección de tu PC). Cuando tengamos el servidor de Windows con https esto se resuelve.
- Solo escucha con la app abierta y la pantalla encendida. Escuchar en segundo plano requiere la app Android nativa (paso 5).
- `servir.ps1` y la voz real no pude probarlos desde acá (no tengo PowerShell ni el modelo): las pruebas usan un reconocedor de mentira para comprobar la lógica.
