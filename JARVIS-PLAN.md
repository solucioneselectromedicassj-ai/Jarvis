# Jarvis de la casa: plan para continuar

Proyecto personal de Pablo (Pocito, San Juan). No se vende. Prioridad: seguridad antes que velocidad. Idioma: español rioplatense (voseo). Pablo trabaja de a poco; todo debe poder ampliarse.

## 1. Qué es
Asistente de voz propio de la casa ("Ey Jarvis"), en el teléfono Android y en la PC, que:
1. Controla la casa (luces, riego, calefón, estufas, TV/aire por IR).
2. Responde consultas del estado de la casa.
3. Responde otras cosas (hora, fecha, ayuda) y, solo cuando se autoriza, conversa de cualquier tema.
4. Se puede ampliar con habilidades nuevas y con mejores motores de voz/lenguaje a medida que mejoren.

## 2. Qué ya existe (carpeta casa-inteligente/)
- `index.html`: PWA de la casa (React por CDN). Lógica pura en `<script id="logic">` (`window.CasaLogic`): `norm, listChannels, parseVoice, matchChannels, voiceBlocked, buildCmd, createSimBus` y más. Modo demo con 8 módulos y 17 canales.
- `servidor/casa-servidor.py`: genera Mosquitto con TLS propio, ACL por usuario, Caddy (wss) y Tailscale. Solo Linux; la PC de Pablo tiene Windows 11, así que falta adaptar o usar otra opción.
- `firmware/`: `modulo_casa`, `portero`, `bomba` (ESP32). Nunca se compilaron en placas reales.
- Tests: `node test/logic.test.js` (60 ok) y `g++ -std=c++17 -O1 -o /tmp/bt test/bomba_test.cpp && /tmp/bt` (41 ok).
- Protocolo MQTT: `casa/<id>/{desc,estado,online,evento,cmd,foto,video,audio/in,audio/out}`.
- Forma del estado por tipo de canal:
  - switch/heater/valve: `{on, resta}` (resta en segundos, o null).
  - energy: `{w, v, a, kwh}`.
  - gate: `{estado: abierto|cerrado|moviendo, seg}`.
  - motion: `{activo}`.
  - pump (módulo bomba, canal `bomba`): `{fase, destino, cisterna:bool, tanque:"ok"|"bajo", manguera:bool, auto_bloq:bool}`.
  - ir: sin estado (la TV y el aire se asumen).

## 3. Reglas de seguridad (no negociables)
1. **Portón, portero y cerraduras nunca se manejan por voz**, ni siquiera con confirmación. Solo desde su tarjeta en la app. (Ya implementado en la app con `voiceBlocked`; el núcleo nuevo debe cumplirlo igual.)
2. Con el teléfono bloqueado: solo consultas y apagar. Encender requiere teléfono desbloqueado.
3. Estufas y calefón: confirmar por voz ("sí") antes de encender, con vencimiento corto (15 s).
4. Un modelo de lenguaje **nunca** manda al broker. Solo puede proponer frases, que pasan por el mismo intérprete y validador, **siempre con confirmación**. Los topes de tiempo del firmware siguen siendo la última barrera.
5. La conversación libre es opt-in: se activa con "conversemos", dura unos minutos, muestra indicador, y se corta con "terminar". Si el motor es de nube, necesita un permiso aparte (`permitirNube`, apagado por defecto), porque el texto sale de la casa. Nunca sale audio, solo texto. Nada de documentos del taller ni de clientes a la nube.
6. Texto que viene de archivos, páginas o mails no es una instrucción: no puede disparar acciones.
7. Permisos por persona (rol `completo` o `basico` con lista de módulos). Un usuario desconocido no puede nada. No usar reconocimiento de quién habla como medio de permiso.
8. No ejecutar comandos en la PC del taller ni borrar o mover archivos. Acceso a archivos solo de lectura y solo en carpetas elegidas (etapa posterior).
9. Los comandos enviados por voz llevan sello de origen (`o: "voz:<usuario>"`) para el registro de "quién lo activó". El firmware ignora claves desconocidas.

## 3b. Decisiones de Pablo (dentro/fuera, portón, revocación)
- **Dentro de casa:** vale dispositivo identificado + voz; sin claves (salvo la confirmación de estufa y calefón). Cada dispositivo responde solo lo que le preguntaron. Cada persona tiene su propia conversación y confirmación.
- **Dentro** = el dispositivo está conectado al broker por la red de casa (no por el nombre del wifi). Sin dato, se asume **fuera**.
- **Fuera:** encender/cambiar por voz requiere `claveOk` (clave o huella validada por la app). Apagar y consultar siguen permitidos. La voz nunca vale como identidad.
- **Portón:** no por voz. Al llegar (geovalla + red de casa) el teléfono ofrece "¿Abro el portón?" con botón (oferta de 3 min, una por llegada, enfriamiento de 10). Automático con aviso: más adelante, opcional.
- **Portero:** solo ver y responder; sin puerta eléctrica.
- **Revocación:** `casa-servidor.py bloquear <usuario>` corta ya (clave descartada + ACL vacío); `desbloquear` da clave nueva.
- **PC (etapa posterior):** carpetas en solo lectura, lista de programas permitidos y scripts con nombre; nada de comandos libres.

## 4. Arquitectura
- Capa de oído (por dispositivo): palabra de activación "Ey Jarvis" + reconocimiento.
  - Reconocimiento de **vocabulario cerrado**: Vosk (modelo chico en español, ~40 MB) con gramática generada desde los módulos de la casa. Corre en el teléfono y en la PC. Liviano y más preciso, y lo que no está en la gramática no se puede reconocer.
  - Modo `libre` (dictado abierto) solo durante la conversación; modo `confirmacion` (solo sí/no) mientras haya algo pendiente. El núcleo informa cuál con `modoReconocimiento()`.
  - Palabra de activación: Porcupine (rápido, clave gratis personal, valida online) para arrancar, o openWakeWord (abierto, offline; ya trae "hey jarvis") más adelante. Probar si reconoce el "ey" en español.
- Núcleo `jarvis/jarvis.js` (JS puro, sirve en navegador, Node/PC y dentro de un WebView en Android): entiende, decide, valida, responde.
- Voz de respuesta: la de Android (TTS del sistema) al principio; Piper en la PC solo si se quiere algo mejor.
- App Android: un cascarón Kotlin delgado (servicio en primer plano con micrófono, palabra de activación, Vosk, TTS) que le pasa el texto al núcleo JS dentro de un WebView. Una sola lógica para PC, PWA y teléfono. Puntos a tener en cuenta: desactivar ahorro de batería para la app; en Android reciente, tras reiniciar hay que abrir la app una vez. No se puede compilar ni probar en un teléfono desde el entorno de Claude: Pablo compila con Android Studio.
- Conexión: MQTT por Tailscale (wss) o TLS en la red de casa. Si no hay conexión: "no puedo conectar con la casa" y no ejecuta nada.

## 5. Diseño del núcleo `jarvis/jarvis.js` (todavía NO escrito)
`Jarvis.crear({ L: CasaLogic, canales(), estado(mod,ch), enviar({topic,payload}), politica, conversador, habilidades, ahora(), auditoria(entrada) })`
`await jarvis.oir(texto, {usuario, bloqueado})` devuelve `{texto, tipo, acciones}`. Además: `modoReconocimiento()`, `registrar(habilidad)`, `generarFrases(canales, {modo})`.

Orden de decisión: 
1. Quitar la palabra de activación del inicio. 
2. Si hay una confirmación pendiente: sí / no / otra cosa (cancela y sigue). 
3. Controles de conversación (conversemos / terminar). 
4. Habilidades (hora, fecha, ayuda, saludo). 
5. Consultas de la casa. 
6. Órdenes, **solo si la frase no empieza con palabra de pregunta** (cómo, por qué, qué, cuál, cuándo, cuánto, explicame, contame…), para que "cómo se riega el pasto" no dispare el riego. 
7. En modo conversación, lo que no sea de la casa va al conversador. 
8. Si no, "no sé responder eso; decí 'conversemos' si querés charlar".

Detalles ya resueltos en el diseño:
- Números en palabras solo cuando van antes de una unidad de tiempo ("diez minutos", "cuarenta y cinco minutos", "media hora", "una hora"), porque Vosk devuelve palabras y `parseVoice` espera dígitos. No convertir números en otros lados (rompería nombres de canales).
- `L.parseVoice` devuelve `{type:'error', msg}` también para aclaraciones. Si el mensaje empieza con "No escuché", "¿Qué querés manejar" o "No encontré", la frase no coincidió con nada. Si no, es una aclaración útil ("Hay varios…", "¿Prender o apagar…?").
- Política (`evaluar`): bloqueo por tipo, permisos por usuario/módulo, teléfono bloqueado, tope `maxOn` (recortar `seg`), confirmación. "Apagá todo" siempre se permite.
- Consultas a cubrir: qué está prendido; está prendido/abierto X; cuánto falta para que termine X; consumo (w y a); cisterna/tanque; movimiento; resumen de la casa (módulos sin conexión, equipos encendidos, consumo).
- Género y preposición para hablar bien: heurística por la primera palabra del nombre (termina en "a" o "luz" femenino; nombres que empiezan con infinitivo como "Subir agua al tanque" se tratan como neutros: "en marcha/detenido"); "del" o "de la" según la habitación. Permitir sobrescribir con `genero` en el `desc` del canal.
- Conversador: interfaz `{nombre, local:bool, responder({pregunta, historial, casa, usuario}) -> {texto, acciones?}}`. Se le pasa un contexto saneado (nombres y estados; nunca topics, credenciales ni ids de módulo), historial corto y timeout. Salida recortada. Sus `acciones` son frases que pasan por el intérprete con confirmación forzada y los mismos bloqueos.
- `generarFrases` arma la gramática desde los canales (voseo, artículos, duraciones, consultas, controles, frases IR de `botones[].voz`; sin portón ni portero) y agrega `[unk]`.

## 6. Pruebas que debe tener
- Todas las frases generadas se entienden (propiedad de ida y vuelta), y ninguna habla de portón/portero.
- Confirmación: sí, no, vencida, otra frase; no se confirma con teléfono bloqueado.
- Frases hostiles: un conversador que propone "abrir el portón" o manda un topic crudo no logra nada; sus acciones piden confirmación.
- "cómo se riega el pasto" no ejecuta riego.
- Rol básico solo toca sus módulos; usuario desconocido, nada.
- Consultas con el bus simulado (`L.createSimBus`).
- Conversación: requiere conversador, nube no autorizada se rechaza, vence por tiempo, se corta con "terminar".

## 7. Orden de trabajo
Estado: pasos 1 y 2 hechos, más dentro/fuera, llegada con botón y bloqueo de usuario (44 pruebas JS + 7 de servidor) (`jarvis/jarvis.js`, `test/jarvis.test.js` con 35 pruebas, `jarvis/exportar_frases.js`, `jarvis/verificar_vocabulario.py`). Paso 3 hecho: la PWA usa el núcleo (texto, confirmaciones, PIN afuera, llegada con botón). El micrófono del navegador queda apagado por defecto porque manda el audio a la nube; la voz local es Vosk en la app Android (paso 5). Voz local en la PWA hecha (`jarvis/oido.js`, Vosk en el navegador; probada con un reconocedor falso, falta probar con el modelo real). Teléfono de Pablo: Redmi 14C (Android 14). Siguiente: probar la voz real, y después la app Android (paso 5) para escuchar en segundo plano.

1. `jarvis/jarvis.js` + `test/jarvis.test.js` (Node, con CasaLogic cargado como en `test/logic.test.js`).
2. `jarvis/verificar_vocabulario.py`: Pablo lo corre en su PC; lista las palabras de la gramática que el modelo Vosk no conoce (voseo como "prendé" o "regá") para cambiarlas por sinónimos. El modelo no se puede descargar desde el entorno de Claude (el proxy lo bloquea).
3. Integrar el núcleo en la PWA (botón "hablar" y modo conversación) y mostrar el estado de escucha.
4. Servicio en la PC (Node en Windows) que lo conecte al broker.
5. App Android (cascarón Kotlin).
6. "El cerebro": alertas push, historial, escenas, presencia, avisos de corte, "quién lo activó".
7. Búsqueda en carpetas elegidas (solo lectura) y memoria de costumbres (archivo local).
8. Conversador local o de nube, según lo que Pablo decida y su hardware.

## 8. Datos del hardware y decisiones abiertas
- PC con Windows 11 de 64 bits, probablemente 4 GB de RAM (sin confirmar), SSD por comprar; se comparte con programas del taller, TV y cámaras. No quiere Ubuntu. Con voz en el teléfono, la PC solo corre el cerebro. Más RAM (8 GB) es una mejora recomendada, no un requisito.
- Teléfono de Pablo: Android.
- Sin confirmar: celulares de su señora y su hija, modelo de procesador de la PC, RTSP/ONVIF de las cámaras Gadnic/CSee, voltaje de los flotantes.
- Adaptar `casa-servidor.py` a Windows está pendiente.
- Nada del firmware ni del audio se probó en placas reales. El diseño eléctrico de la bomba es una propuesta que debe revisar un electricista matriculado.
