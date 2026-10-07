# Casa + Jarvis — proyecto de Pablo

Leé este archivo entero antes de tocar nada. Está escrito para vos (Claude Code) y se actualiza a medida que el proyecto avanza.

## 1. Qué estamos construyendo

Un **servidor local propio** para la casa de Pablo (Pocito, San Juan, Argentina) y, encima, **Jarvis**: un asistente que ejecuta tareas, controla la casa y le informa resultados aunque Pablo no esté conectado.

- Uso **personal**, no para vender: Pablo, su esposa y, más adelante, su hija.
- Prioridades: **seguridad**, que todo sea **propio** y que quede **local**. Sin apuro: el tiempo no es un problema, es preferible hacerlo bien y de a poco.
- El servidor puede salir a internet para sumar funciones, pero **nada entra**. El acceso desde afuera es por VPN (Tailscale).
- Debe poder recibir **audio, foto y video** y analizarlos. Hay que dejarlo previsto desde el diseño.

## 1b. Un solo Jarvis (decisión de Pablo)

Hay **un único Jarvis**: un asistente con el que se puede **hablar y controlar** tanto la casa como las tareas del taller y otras funciones, pensado para ir creciendo. No hay un "Jarvis de voz" y otro "de agente": son **canales** del mismo asistente (voz, app, Telegram, agente con modelo de lenguaje), y todos pasan por **la misma política, que vive en el servidor** (`casa-servidor`), no en cada cliente.

Política de la casa (igual para todos los canales):
- **Portón, portero y cerraduras: nunca por voz ni por agente.** Solo con acción explícita de una persona en la app.
- **Calefón y estufas: con confirmación.** La confirmación llega por un canal de **persona** (app o Telegram), **nunca por una herramienta que el modelo pueda llamar**.
- **Tope de tiempo** en cada actuador y canal (bomba, riego, estufa). El modo "manguera" es la excepción ya definida.
- **El modelo no define tareas**: las tareas de Jarvis las escribe Pablo en `jarvis-mcp/tareas.json`; el modelo solo puede listarlas y ejecutarlas. Sin tareas `shell`: solo `http` o scripts con nombre en una carpeta controlada.
- Lo que sale a internet (Telegram, webhook) es solo estado ("cayó / volvió"), nunca la salida cruda de una tarea.
- Voz: el reconocimiento corre en el dispositivo (teléfono o PC) por latencia; en el servidor solo el audio grabado del portero.
- Hay trabajo previo de Pablo en otra sesión (PWA, núcleo de voz, política en JS con bloqueos, topes, confirmaciones y sello de origen; decisiones: dentro/fuera de casa, portón con botón, bloqueo de usuario, voz local, PIN afuera). Se adapta al protocolo nuevo; la política compartida se pasa a Python con los **mismos casos de prueba** que el JS. Esas decisiones deben anotarse acá cuando Pablo las confirme.

Decisiones de voz (Pablo, octubre 2026): Gemini gratis como primer proveedor de conversación (el texto sale a internet solo con `JARVIS_INTERNET_TEXTO=1`; nunca audio/foto/video; Gemini sin herramientas); micrófono ESP32 y botón 🎤 de la app desde el principio; una sola voz sintética lo más simple posible (la del teléfono), a mejorar después con voces más humanas.

La app web también puede abrirse desde Vercel/GitHub Pages (decisión de Pablo): campo "Dirección de la casa" en el login + `--origen` en el servidor (apagado por defecto). Riesgo asumido y documentado en el README de la raíz: el código de la app viene de un tercero y maneja las credenciales; mitigar con 2FA en esas cuentas, un usuario por dispositivo y lista de orígenes explícita.

## 2. Reglas duras (no negociables sin preguntarle a Pablo)

1. **Software propio.** Nada de programas empaquetados de terceros como columna del sistema (Mosquitto, Caddy, Home Assistant, Node-RED, etc.). Las **bibliotecas** sí valen (biblioteca estándar de Python, OpenCV, Vosk, etc.). Si dudás si algo cuenta como programa de terceros, preguntá.
2. **Nunca inventar criptografía ni TLS.** Se usa TLS estándar (módulo `ssl` de Python, certificados X.509). Lo propio es el protocolo y la lógica, no el cifrado.
3. **Local primero.** Todo funciona sin internet. Lo que sale a internet lo decide Pablo, por tipo de dato (el video nunca sale; el audio solo con autorización explícita). Nunca abrir puertos del router.
4. **Permisos mínimos.** Cada módulo y cada persona tiene su usuario y solo lo que necesita. Las claves se muestran una sola vez y en el servidor queda solo el hash. Tiene que existir `bloquear`/`desbloquear` (teléfono perdido o robado).
5. **Certificados válidos desde 1969.** Los ESP32 arrancan con la hora en 1970; si el certificado no es válido en esa fecha, rechazan la conexión. Mantener este detalle.
6. **Windows primero, Linux también.** La PC es Windows 11 de 64 bits y no se le va a poner Ubuntu (la usan además para el TV, las cámaras y los programas del taller). Todo el código tiene que correr en Windows y no atarse a Windows (rutas con `pathlib`, nada de `systemctl`/`chown` sin una alternativa).
7. **Liviano.** La PC tiene probablemente **4 GB de RAM (sin confirmar)**, es de hace un par de años y todavía no tiene el SSD. El servidor comparte la máquina con uso diario. Avisá antes de sumar dependencias pesadas.
8. **Los secretos no se suben a ningún repositorio:** `mqtt_credenciales.h` o su equivalente, `.env`, `JARVIS_TOKEN`, claves de la CA.

## 3. La casa (hardware y deseos)

Estado actual (lo que Pablo ya tiene):
- ESP32 sueltos (incluido un kit Hemmel TEK-002 con ESP32 de 38 pines), sensores de humedad de tierra resistivos de dos puntas (módulo LM393) y sensor de nivel del tanque de agua.
- 2 cámaras Gadnic (una funciona; la otra está desarmada y no logró configurarla). Televisores TCL con Android. Teléfono Android.
- Fibra óptica y WiFi en toda la casa. Le preocupa que sin WiFi no se pueda operar nada: evaluar cablear lo crítico.
- Agua: cisterna al nivel del piso (se llena de la red) → bomba monofásica de ½ HP → tanque elevado. Un flotante en cada uno. Hoy una llave mecánica manda la bomba al tanque o al riego.
- TV y aire acondicionado se manejan por infrarrojo; calefón y estufas son eléctricos; las luces se manejan desde la app o por voz.

Lo que quiere (a su ritmo, de a poco):
- Regar (goteo según humedad de tierra y calor, aspersores para el pasto, electroválvulas), subir agua al tanque automáticamente. **Modo "manguera"** manual sin límite de tiempo; al apagarlo vuelve solo al automático. La llave mecánica de la manguera se queda manual.
- Luces, control de TV, calefón, aire acondicionado, estufas, control del consumo de energía, sensores de movimiento y de energía con aviso, cámaras, portero eléctrico (responder desde donde esté), portón que se abre al llegar.
- Escenas, avisos de corte de WiFi y de luz, saber quién está en casa sin que avise (detectando teléfono o tag), control de temperatura (invierno y verano), saber si algo se activó a mano o no.
- Más adelante: paneles solares y posiblemente pileta (prever tarjetas para ambos).
- App con **tarjetas por cada parte de la casa**.
- Voz: asistente "Ey Jarvis" en el teléfono, con programas propios y livianos (reconocimiento liviano, sin voces ni modelos de más). Que pueda conversar cuando Pablo lo autorice y responder también otras preguntas.

## 4. Arquitectura propuesta (acordada en la conversación previa)

Un proceso Python asíncrono **mínimo de control** (`casa-servidor`) que reemplaza a Mosquitto + Caddy. El **análisis pesado (OpenCV, Vosk) va en un proceso aparte y opcional**, para que si consume memoria o se cuelga no se caiga el control del riego y las luces:

- **PKI propia**: CA propia y certificado del servidor, con la lógica de `referencia/casa-servidor.py`.
- **Canal de mensajes** por TLS con protocolo propio (una línea JSON por mensaje). Hay que reimplementar lo que MQTT daba gratis: reconexión, último estado guardado (retenido), aviso de módulo desconectado y keepalive. Conservar el **esquema de topics y de permisos** del script original: `casa/<modulo>/{desc,estado,evento}` (el módulo escribe; `online` lo gestiona el servidor según la conexión), `casa/<modulo>/cmd` (el módulo lee), y para cámaras `foto`, `video`, `audio/out`, `audio/in`.
- **Canal de medios aparte, por HTTPS**: audio, foto y video se suben con POST y el token del módulo, a una carpeta con cuota y retención. **Nunca** por el canal de mensajes (el script original limitaba el paquete a 128 KB justamente por eso).
- **Cola de análisis con plugins**, locales primero: movimiento (OpenCV), voz (Vosk o whisper.cpp pequeño). Analizar **fotogramas clave o eventos de movimiento, no el stream completo**. Lo pesado puede ir a la nube, solo con permiso de Pablo.
- **Roles**: `modulo`, `persona completo`, `persona basico` (ve estados y eventos, nunca foto/video/audio, y manda órdenes solo a los módulos de su lista `--cmd`), `servicio` (solo lectura). **Jarvis debe usar el rol `basico` con una lista `--cmd` explícita**, no `completo`.
- **WebSocket sobre TLS** además del socket TLS crudo: un navegador no abre sockets crudos, y la PWA lo necesita. El HTTPS de la app necesita certificado de confianza en el teléfono: instalar la CA propia, o pedir uno real para el nombre `ts.net` con Tailscale.
- **App web (PWA)** servida por el propio servidor. Estilo habitual de Pablo: un solo archivo HTML, React por CDN, sin herramientas de build.
- **Acceso remoto**: Tailscale en la PC y en el teléfono.
- **Jarvis MCP** (ya construido, ver sección 6) se conecta como cliente.

**Decisión tomada:** Pablo todavía **no tiene firmware** escrito para los módulos y no vio ningún formato de mensajes, así que se arranca **directo con el protocolo propio**, sin compatibilidad con MQTT. El contrato de mensajes lo definimos nosotros, y como todavía no existe ningún módulo, se puede acertar desde el principio.

**Contrato de mensajes (propuesta de partida, a validar con Pablo antes de congelarla):**
- Una línea JSON por mensaje, siempre con un campo de versión `"v": 1` para poder evolucionar sin romper módulos viejos.
- `estado`: `{"v":1,"valor":...}` (el valor puede ser un número, un texto o un objeto, según el módulo). `evento`: `{"v":1,"tipo":"movimiento"}` con datos opcionales. `online`: lo gestiona el servidor a partir de la conexión (equivalente a "último testamento" de MQTT).
- `cmd`: `{"v":1,"id":"<identificador único>","accion":"encender"}`; el módulo confirma publicando un `estado` que incluya el `id` de la orden. Así Jarvis y la app saben si la orden se cumplió y si algo se activó a mano (un estado sin `id` de orden).
- **No confiar en el reloj del ESP32** (arranca en 1970): el servidor pone la marca de tiempo al recibir.
- Definir también tamaños máximos por mensaje y límites de frecuencia por módulo, para que un módulo defectuoso no sature el servidor.

## 5. Referencia: `referencia/casa-servidor.py`

Es el script original de Pablo (Linux, con Mosquitto y Caddy). **No es el destino**, es la referencia del modelo de seguridad. Comandos: `init`, `renovar-servidor`, `alta-modulo`, `alta-persona`, `alta-servicio`, `clave`, `baja`, `bloquear`, `desbloquear`, `lista`, `ca`. La función pura `acl_lines` define los permisos de cada rol; reusar su lógica y sus nombres, y escribirle pruebas automáticas.

Problemas conocidos al llevar la parte de PKI a Windows (detectados al leerlo, sin probar en Windows):
- En `openssl.cnf` las rutas deben llevar **barras `/`**; la barra invertida es un carácter de escape de OpenSSL.
- `-subj '/CN=...'` puede mezclarse con la conversión de rutas de OpenSSL de Git for Windows; mejor poner el CN dentro del `.cnf`.
- Para restringir archivos de la CA en Windows, `icacls` con **SIDs** (`*S-1-5-32-544` Administradores, `*S-1-5-18` SYSTEM), no con nombres: en Windows en español el grupo se llama "Administradores".
- `os.geteuid`, `chown` y `systemctl` no existen en Windows. Firewall: regla de entrada solo para la red local (`netsh advfirewall`).
- Si se genera o usa `openssl` en Windows, no está en el PATH por defecto (Git for Windows trae uno).

## 6. Jarvis MCP — ya construido y probado (`jarvis-mcp/`)

- `server.py`: servidor MCP con **FastMCP** (`mcp[cli]>=1.9,<2`; la versión 2 renombró `FastMCP`, por eso se fija `<2`: usar un venv), transporte streamable HTTP en `/mcp`, `/health` sin autenticación.
- **Auth Bearer** con `JARVIS_TOKEN` (comparación en tiempo constante; sin token el servidor no arranca).
- SQLite (`JARVIS_DB`) para tareas, resultados e informes.
- 10 herramientas: `casa_estado`, `casa_ordenar`, `casa_eventos` (cliente a casa-servidor, ver abajo), `listar_tareas`, `ejecutar_tarea`, `ver_resultados`, `resumen_estado`, `guardar_informe`, `leer_informes`, `notificar`.
- **Scheduler interno** (revisa cada 30 s) para tareas con `cada_minutos`; avisa por Telegram o webhook según `never` / `on_fail` / `always`.
- Probado: auth (401 sin token y con token malo), las herramientas con un cliente MCP real y los avisos por cambio de estado.
- **Va a vivir en la misma PC Windows**, no en Render ni en Docker (poca RAM). Falta: correrlo como servicio de Windows (Programador de tareas) con inicio automático. La sección de Render del `README.md` quedó obsoleta.
- **Hecho (Fase 3):** `casa_cliente.py` + herramientas `casa_*`. Jarvis entra como `persona basico`, `--canal agente` y `--cmd` explícito; la política la aplica el servidor. `casa_ordenar` espera la confirmación por `cmd_id`; si hace falta que confirme una persona devuelve `esperando_confirmacion_de_una_persona`. Avisa por Telegram/webhook solo estado (módulo se desconectó / volvió, orden sin respuesta). Probado de punta a punta en Linux con servidor y módulo simulado; no en Windows.
- **Hecho:** tareas definidas solo por Pablo en `tareas.json` (ver `tareas.ejemplo.json`), sin `shell`; avisos por cambio de estado; el servidor escucha por defecto solo en `127.0.0.1` (`JARVIS_HOST`); token comparado en bytes (antes un token con caracteres no ASCII daba error 500).
- **Pendiente de seguridad:** TLS con la CA propia (hoy HTTP plano; por eso solo localhost o Tailscale), y un token por cliente con permisos separados (leer / ejecutar).
- Para correrlo como servicio en Windows usar el **Programador de tareas** con reinicio automático (NSSM es de terceros y choca con la regla 1).
- Pendiente a futuro: aviso si el propio servidor cae (si se corta la luz o internet, la PC no puede avisar que se cortó: hace falta un chequeo externo o un aviso desde el teléfono).

## 7. Hoja de ruta sugerida

Cada fase termina cuando sus pruebas automáticas pasan y Pablo lo probó. No avances a la siguiente sin avisar.

0. **Cerrar el contrato de mensajes** (borrador en `docs/protocolo.md`, falta que Pablo responda los [DECIDIR]): partir de la propuesta de la sección 4, discutirla con Pablo y dejarla escrita en `docs/protocolo.md`. Ya está decidido que el protocolo es propio y que no hay firmware previo que respetar.
1. **Núcleo del servidor** (HECHO en `casa-servidor/`, 62 pruebas pasan en Linux, incluye WebSocket propio y política de la casa; no se probó en Windows ni con ESP32; faltan certificados de cliente para módulos, opcional):  PKI, usuarios y permisos, canal de mensajes por TLS, estado retenido, aviso de desconexión, CLI compatible. Pruebas de permisos, TLS y reconexión.
2. **Módulo de referencia** (HECHO sin hardware: `casa-servidor/simulador_modulo.py` probado con 4 pruebas; `firmware/modulo_riego/modulo_riego.ino` escrito pero SIN COMPILAR NI PROBAR en un ESP32): sketch de ESP32 y un simulador de módulo en Python para probar sin hardware.
3. **Jarvis conecta** con herramientas `casa_*` y avisos (HECHO, 71 pruebas en total pasan en Linux).
4. **Medios y análisis** (PARCIAL: HECHA la subida de audio por HTTPS con cuota y retención, el puente de voz y texto en `analisis/` como proceso aparte, el cerebro de Jarvis con intérprete local + Gemini gratis opcional, el botón 🎤 de la app y el sketch `firmware/modulo_microfono` sin probar en hardware; todo probado con un transcriptor falso. FALTA probar Vosk con el modelo real, foto/video y OpenCV. Ver `docs/jarvis-conversacion.md`): subida por HTTPS con cuota, cola de análisis, movimiento y voz.
5. **App web** con tarjetas, acceso por Tailscale, escenas. (HECHO la primera versión en `casa-servidor/app/`: login, tarjetas por módulo, órdenes con confirmación, avisos, pestaña Jarvis solo con órdenes de la casa; probada en Chromium contra el servidor real. Faltan escenas, tarjetas de energía/paneles/pileta, probarla en el Redmi y el HTTPS de confianza en el teléfono. La conversación general con Jarvis está en `docs/jarvis-conversacion.md`, pendiente de decisión.)
6. **Instalación en Windows**: servicio con inicio automático, sin suspensión, backups de la base y de la CA, aviso externo de caída, guía de instalación.
7. **Después**: portón, paneles solares, pileta, quién está en casa, voz "Ey Jarvis" en Android.

## 8. Preguntas abiertas para Pablo

- Cerrar los puntos **[DECIDIR]** de `docs/protocolo.md` (puerto, límites, espera de confirmación, órdenes a módulos offline, un usuario por dispositivo, y si los módulos usan certificado de cliente en vez de clave).
- ¿Se puede sumar la biblioteca `cryptography` (PKI sin el programa `openssl`, mucho más simple en Windows)? Es una dependencia nueva (regla 7). Para claves de personas, `hashlib.scrypt` de la biblioteca estándar.
- ¿Qué módulo quiere construir primero? Conviene uno simple con sensor y actuador (por ejemplo riego o nivel del tanque) para probar de punta a punta.
- ¿Aprueba el contrato de mensajes propuesto en la sección 4, o quiere cambiarlo?
- RAM real de la PC y cuándo compra el SSD.
- ¿Ya usa Tailscale? ¿Hay IP fija reservada para la PC en el router?
- ¿Qué se permite que salga a internet y qué no (por tipo de dato)?
- ¿Hay UPS o alguna forma de aguantar cortes de luz?

## 9. Cómo trabajar con Pablo

- **Idioma:** español rioplatense en la conversación, los comentarios, los mensajes de error y la documentación.
- Respuestas **concisas**, sin saludos de relleno ni cierres de cortesía. Pensá antes de actuar y leé los archivos existentes antes de escribir código.
- **Editá en vez de reescribir** archivos enteros. No vuelvas a leer lo que ya leíste salvo que pueda haber cambiado.
- **Probá el código antes de decir que está terminado.** Para todo lo de seguridad (permisos, autenticación, TLS) escribí pruebas automáticas.
- Soluciones simples y directas. Nada de abstracciones por las dudas.
- Si algo no se puede probar en esta máquina (por ejemplo Windows real, un ESP32 real), decilo claramente en vez de afirmar que funciona.
- Si una decisión es difícil de deshacer o toca la seguridad, preguntá antes.
- Perfil técnico de Pablo: dueño de una empresa de servicio técnico biomédico y desarrollador de PWAs de salud con un enfoque consistente (un solo HTML, React por CDN, Supabase o Firebase). Diseña él mismo los módulos de hardware.
