# Casa + Jarvis

Servidor local propio para la casa (Pocito, San Juan) y un asistente, Jarvis, que la controla. Uso personal, local primero.

| Carpeta | Qué es |
|---|---|
| `casa-servidor/` | Servidor de mensajes (TLS + JSON por línea), PKI propia, usuarios y permisos, política de la casa, WebSocket y **app web (PWA)** en `casa-servidor/app/`. Ver su README. |
| `jarvis-mcp/` | Servidor MCP de Jarvis: tareas definidas por Pablo, informes, avisos y herramientas `casa_*`. |
| `analisis/` | Puente de voz y texto (proceso aparte y opcional): transcribe con Vosk y le pasa el texto a Jarvis. |
| `firmware/` | Sketches de ESP32 (riego y micrófono), sin probar en hardware. |
| `docs/` | Protocolo de mensajes y notas de diseño. |
| `referencia/` | Script original (Linux/Mosquitto), solo como referencia del modelo de seguridad. |
| `CLAUDE.md` | Contexto, reglas y hoja de ruta del proyecto. |

## App web
Un solo `index.html` con React (copias locales en `app/vendor/`, sin internet ni build). La sirve el propio servidor por HTTPS en el puerto WebSocket (8884 por defecto): `https://<ip-del-servidor>:8884/`. Para instalarla en el teléfono y que el service worker funcione hace falta que el teléfono confíe en la CA propia (`python casa_servidor.py ca`) o usar un certificado real del nombre de Tailscale.

Crear el usuario de cada teléfono (uno por dispositivo, canal `app` para poder confirmar acciones):
```
python casa_servidor.py alta-persona pablo-celu --rol completo --canal app
```

## Abrir la app desde Vercel (opcional, con riesgo)

Lo más seguro es servirla desde la propia casa (`https://<ip>:8884/`). Si igual la querés en Vercel (o GitHub Pages):
1. En Vercel, el *Root Directory* del proyecto es `casa-servidor/app` (así entra el `vercel.json` con cabeceras de seguridad).
2. En el servidor, habilitá ese sitio: `python casa_servidor.py servir --origen https://TU-APP.vercel.app` (o `"origenes": [...]` en `datos/config.json`). Con alguna dirección en la lista se rechazan los demás sitios web; sin lista se acepta cualquiera que tenga usuario y clave.
3. En el login de la app, poné **Dirección de la casa**: la IP de Tailscale de la PC con el puerto, `100.x.y.z:8884`. Hace falta Tailscale en el teléfono y que el teléfono confíe en la CA de la casa.
4. El navegador puede pedir un permiso de "acceso a la red local" al conectar desde un sitio público a una IP privada; no lo pude probar en un teléfono real.

**Riesgo:** el código de la app viene de Vercel/GitHub y maneja tu usuario y tu clave. Quien entre a esas cuentas puede cambiarlo para robarlas. Activá verificación en dos pasos en GitHub y Vercel, y usá un usuario por dispositivo para poder bloquear solo ese (`bloquear p-celu`).

## Secretos
No se suben: `.env`, `datos/` (CA, claves, usuarios), `tareas.json`, `casa_credenciales.h`. Ya están en `.gitignore`.
