# Jarvis MCP

Servidor MCP con auth por token, tareas programables, historial de resultados, informes y notificaciones.

## Correr local

```bash
pip install -r requirements.txt
export JARVIS_TOKEN=$(python -c "import secrets;print(secrets.token_urlsafe(32))")
python server.py        # http://localhost:8000/mcp
```

## Herramientas

| Herramienta | Qué hace |
|---|---|
| `casa_estado` / `casa_ordenar` / `casa_eventos` | Leer y controlar la casa vía `casa-servidor` (la política y las confirmaciones las aplica el servidor) |
| `listar_tareas` | Tareas definidas por Pablo en `tareas.json` (ver `tareas.ejemplo.json`) |
| `ejecutar_tarea` | Corre una tarea existente ahora (Jarvis no puede crear ni modificar tareas) |
| `ver_resultados` / `resumen_estado` | Historial y resumen por ventana de horas |
| `guardar_informe` / `leer_informes` | Informes por proyecto |
| `notificar` | Mensaje a Telegram/webhook |

Las tareas con `cada_minutos` corren solas (scheduler interno, revisa cada 30 s) y notifican según `notificar`: `never`, `on_change` (por defecto: avisa solo cuando cae o vuelve) o `always`. La salida de la tarea nunca se manda a Telegram/webhook.

Tipos de tarea: `http` y `script` (un archivo de la carpeta `JARVIS_SCRIPTS`, por nombre y sin argumentos). No hay tareas `shell`.

## Conectar Jarvis (cliente Python)

```python
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

async with streamablehttp_client(
    "http://127.0.0.1:8000/mcp",
    headers={"Authorization": "Bearer TU_TOKEN"},
) as (r, w, _):
    async with ClientSession(r, w) as s:
        await s.initialize()
        res = await s.call_tool("resumen_estado", {"horas": 24})
```

## Conectar desde Claude

Ajustes → Conectores → agregar conector personalizado con la URL la URL `/mcp` del servidor (por Tailscale).
Nota: Claude no envía headers Bearer personalizados en todos los planes; si tu plan no lo permite,
usá un cliente propio (el de arriba) o poné el servidor detrás de un proxy que inyecte el token.

## Instalación

Corre en la misma PC Windows de la casa (ver `CLAUDE.md`, sección 6). Por defecto escucha solo en `127.0.0.1`; para acceso por Tailscale definí `JARVIS_HOST` con la IP de Tailscale de la PC. Pendiente: correrlo como servicio con inicio automático.

## Voz y conversación

`POST /voz {"texto": "...", "fuente": "celu"}` (token propio `JARVIS_VOZ_TOKEN`, que no sirve para el resto) → `{"respuesta": "..."}`. Lo usa el puente de voz (`../analisis`). Primero el intérprete local; si no es una orden de la casa y Pablo lo habilitó, consulta a Gemini (nivel gratuito, sin herramientas, tope diario). Ver `../docs/jarvis-conversacion.md`.

## Conexión a la casa

Crear el usuario de Jarvis en `casa-servidor` (canal `agente`, lista de módulos explícita) y completar `CASA_*` en `.env`:

```
python casa_servidor.py alta-persona jarvis --rol basico --canal agente --cmd riego,luces
```

Jarvis nunca puede confirmar acciones que piden confirmación: eso lo hace una persona desde la app o Telegram.

## Seguridad

- Las tareas las define Pablo en un archivo; el modelo solo puede listarlas y ejecutarlas, así un texto malicioso no puede hacer que el servidor llame a direcciones arbitrarias.
- Usá un token largo y no expongas el puerto fuera de Tailscale/LAN; sin `JARVIS_TOKEN` el servidor no arranca.
