# Protocolo de `casa-servidor` — v1

Estado: **implementado en la Fase 1 con los valores sugeridos** (puerto 8883, línea 4 KB, 20 msg/s con ráfaga de 40, espera de confirmación 30 s, órdenes a módulos offline descartadas, un usuario por dispositivo). Los puntos marcados **[DECIDIR]** siguen abiertos: si Pablo cambia alguno, es una constante en `casa/servidor.py`. WebSocket (§1b) y política/confirmación (§4.2b) también están implementados. Mensajes extra: `pendiente` (el servidor espera confirmación), `rechazo` (una persona rechaza). El canal de cada usuario (`app`, `telegram`, `voz`, `agente`) lo fija el alta, no el cliente.

Cortan la conexión: JSON inválido, tipo desconocido, versión distinta, línea demasiado larga y exceso sostenido de frecuencia. El resto de los errores no.

## 1. Transporte

- TCP + **TLS estándar** (módulo `ssl`), puerto propio **[DECIDIR: sugerido 8883]**. El servidor presenta el certificado de la CA propia; el cliente lo valida con la CA.
- Los certificados deben ser válidos desde 1969 (los ESP32 arrancan en 1970).
- **Un mensaje = una línea JSON UTF-8 terminada en `\n`.** Máximo **4 KB por línea** **[DECIDIR]**; si se pasa, el servidor corta la conexión.
- Todo mensaje lleva `"v": 1`. Un servidor ignora campos desconocidos; si recibe un `v` mayor al que soporta, responde `error` y cierra.
- Los medios (foto, video, audio) **no viajan por acá**: van por HTTPS aparte (ver §8).

### 1b. WebSocket

El servidor ofrece **el mismo protocolo (mismas líneas JSON, un mensaje por frame de texto)** también por WebSocket sobre TLS **[DECIDIR: puerto, sugerido 8884]**, porque un navegador no abre sockets TLS crudos. La PWA usa este canal. Los permisos son idénticos.

## 2. Autenticación y sesión

Primer mensaje del cliente, obligatorio, dentro de los 5 s:

```json
{"v":1,"t":"hola","usuario":"riego1","clave":"...","ultimo_id":0}
```

Respuesta del servidor:

```json
{"v":1,"t":"ok","keepalive":30}
```
o `{"v":1,"t":"error","codigo":"auth"}` y se cierra. Las claves se guardan solo como hash (scrypt) en el servidor. **Alternativa a decidir para módulos:** certificado de cliente (mTLS) en lugar de clave; se revoca por huella y el ESP32 lo soporta, pero cuesta más RAM y flash y hay que probarlo en un ESP32 real. Un usuario `bloqueado` se rechaza igual que una clave mala (sin revelar cuál).

- **Keepalive:** el cliente manda `{"v":1,"t":"ping"}` cada `keepalive` s; el servidor responde `{"t":"pong"}`. Sin tráfico durante 2×`keepalive`, el servidor cierra y marca al módulo `offline`.
- **Reconexión:** responsabilidad del cliente, con espera creciente (1, 2, 4… hasta 60 s). Al reconectar, vuelve a mandar `hola`.
- Una sola sesión por usuario: una conexión nueva reemplaza a la vieja.

## 3. Topics y permisos

Se conserva el esquema del script original:

| Topic | Escribe | Lee |
|---|---|---|
| `casa/<modulo>/desc` | el módulo | según rol |
| `casa/<modulo>/estado` | el módulo | según rol |
| `casa/<modulo>/evento` | el módulo | según rol |
| `casa/<modulo>/online` | **solo el servidor** | según rol |
| `casa/<modulo>/cmd` | personas/servicios autorizados | el módulo |
| `casa/<modulo>/{foto,video,audio/out}` | módulo con `camara` | solo `completo` y `servicio` |
| `casa/<modulo>/audio/in` | `completo` | módulo con `camara` |

Permisos por rol (igual que `acl_lines` del script de referencia):

- **modulo**: escribe `desc`, `estado`, `evento` propios; lee su `cmd`. Con `camara`, además medios.
- **persona completo**: lee `casa/#`; manda `cmd` y `audio/in` a cualquier módulo.
- **persona basico**: lee `desc`, `estado`, `evento`, `online` de todos (nunca medios); manda `cmd` solo a los módulos de su lista.
- **servicio**: solo lectura de `casa/#`.
- **bloqueado**: nada.

Jarvis usa `persona basico` con lista de `cmd` explícita.

## 4. Mensajes

### 4.1 Publicar (módulo → servidor, y persona → `cmd`)

```json
{"v":1,"t":"pub","topic":"casa/riego1/estado","valor":{"valvula":"abierta","humedad":41}}
```

- `estado`: `valor` libre (número, texto u objeto). Si es respuesta a una orden, incluye `"cmd_id":"<id>"`.
- `evento`: `{"t":"pub","topic":"casa/pasillo/evento","tipo":"movimiento","datos":{...}}` (`datos` opcional).
- `desc`: descripción del módulo (nombre, tipo, acciones que acepta). Se manda al conectar.
- Una escritura en un topic sin permiso → `{"t":"error","codigo":"permiso","topic":"..."}` y se **descarta** (no se corta la conexión).

### 4.2 Órdenes

Persona/servicio → servidor:

```json
{"v":1,"t":"pub","topic":"casa/riego1/cmd","id":"c-7f3a","accion":"abrir","params":{"minutos":10}}
```

El servidor la reenvía tal cual al módulo (agregando `de` y `ts`). El `id` lo genera quien manda; debe ser único.

**Confirmación:** el módulo publica un `estado` con `"cmd_id":"c-7f3a"`. Un `estado` **sin** `cmd_id` significa que el cambio fue manual o espontáneo ("se activó a mano").

El servidor guarda cada orden pendiente; si pasan **N s sin confirmación** **[DECIDIR: sugerido 30 s]** emite un evento `cmd_sin_respuesta` en el topic del módulo. Si el módulo está offline al mandarla, la orden se **descarta** y se avisa al emisor con `{"t":"error","codigo":"offline","id":"c-7f3a"}` — no se encolan órdenes viejas **[DECIDIR: ¿hay órdenes que sí deban esperar?]** (ejemplo del riesgo: una bomba que arranca sola al reconectar).

### 4.2b Política y confirmación (en el servidor)

Toda orden pasa por la política de la casa antes de llegar al módulo, sea cual sea el canal (voz, app, Telegram, agente):
- Acciones **prohibidas por canal** (portón, portero, cerraduras por voz/agente) → `error` con `codigo:"politica"`.
- Acciones **con confirmación** (calefón, estufas) → el servidor responde `{"t":"confirmar","id":"c-7f3a","accion":"..."}` y espera una confirmación que solo se acepta de una **sesión de persona** (app o Telegram): `{"t":"confirmo","id":"c-7f3a"}`. Un agente o una herramienta con modelo de lenguaje no puede emitir `confirmo`.
- **Topes de tiempo** por actuador: el servidor rechaza o recorta duraciones excesivas, y el módulo debe tener además su propio tope local.
- Cada orden lleva el sello `origen` (canal y usuario) puesto por el servidor, no por el cliente.

### 4.2c Voz y texto (Fase 4)

- `casa/<fuente>/evento` con `tipo:"audio"` y `datos:{id, archivo, bytes, segundos}`: lo publica el servidor cuando termina una subida `POST /media/audio` (ver §8). `<fuente>` es el módulo con `camara` o la persona (rol completo) que subió.
- `casa/<usuario>/pregunta` `{id, texto}`: una persona (rol completo) le escribe a Jarvis; solo puede escribir en su propio nombre.
- `casa/<fuente>/respuesta` `{id, texto, respuesta}`: la publica el usuario `servicio --responde` (puente de voz); `texto` es lo que se entendió y `respuesta` lo que contesta Jarvis. Los módulos leen su propia `respuesta`; las personas de rol completo leen todas.

### 4.3 Suscripción (lectores → servidor)

```json
{"v":1,"t":"sub","topics":["casa/+/estado","casa/+/evento","casa/+/online"]}
```

Comodines `+` (un nivel) y `#` (resto), como MQTT. Solo se entregan los topics que el rol permite; el resto se ignora en silencio.

Entrega:

```json
{"v":1,"t":"msg","topic":"casa/riego1/estado","ts":1760000000.5,"retenido":false,"valor":{...}}
```

### 4.4 Estado retenido

El servidor guarda el **último** `desc`, `estado` y `online` de cada módulo. Al suscribirse, el cliente recibe primero esos retenidos (`"retenido":true`). `evento` y `cmd` **no** se retienen.

### 4.5 Presencia

Al autenticarse un módulo, el servidor publica `casa/<modulo>/online = true`; al cerrarse la conexión o vencer el keepalive, `false` (equivale al "último testamento"). El módulo no lo escribe.

## 5. Tiempo

El servidor pone `ts` (segundos Unix, UTC) en todo lo que entrega. **Se ignora cualquier hora que mande el módulo.**

## 6. Límites

- Línea máxima: 4 KB.
- Frecuencia por conexión: **20 mensajes/s** con ráfaga de 40 **[DECIDIR]**. Superado: se descarta y se cuenta; si persiste 10 s, se cierra la conexión y se registra.
- Máx. 100 suscripciones por conexión; nombres de módulo `[a-z0-9_-]{1,32}`.

## 7. Errores

`{"v":1,"t":"error","codigo":"...","detalle":"..."}` con códigos: `auth`, `permiso`, `politica`, `formato`, `tamano`, `frecuencia`, `offline`, `version`. Los de `formato`/`tamano` cortan la conexión; los demás no.

## 8. Medios (fuera de este canal)

Implementado para audio: `POST /media/audio` en el puerto web (8884), cabecera `Authorization: Basic usuario:clave`, `Content-Type: audio/L16;rate=16000` (PCM 16 bits mono), con `Content-Length` o `Transfer-Encoding: chunked`; entre 0,3 y 30 s. Responde `202 {"id", "segundos"}`; errores 401, 403 (rol básico, módulo sin `camara`), 413, 415, 507 (cuota llena). Se guarda como WAV en `datos/media/audio/<fuente>/` con cuota (200 MB) y retención (24 h) configurables. Al terminar, el servidor publica un `evento` (`tipo: "foto"`, `datos: {archivo, bytes}`) para que quien tenga permiso pueda pedir el archivo. Detalle en el documento de la Fase 4.

## 9. Pendiente para el firmware ESP32

Un ESP32 sin mucha RAM puede sostener TLS pero conviene que el JSON sea chico; por eso la línea máxima es 4 KB. La clave del módulo va en su `mqtt_credenciales.h` equivalente (no se sube a ningún repo).

## Preguntas que cierran este borrador

1. Puerto TCP. 2. Límite de línea (4 KB) y de frecuencia (20/s). 3. Tiempo de espera de confirmación (30 s). 4. ¿Órdenes encoladas para módulos offline: nunca, o solo en algunos casos? 5. ¿Un solo `persona` por teléfono, o un usuario por dispositivo (para bloquear uno solo)? Sugerencia: uno por dispositivo.
