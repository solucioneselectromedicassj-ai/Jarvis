# casa-servidor (Fase 1: núcleo)

Servidor de mensajes de la casa: TLS estándar + una línea JSON por mensaje (ver `../docs/protocolo.md`). Un solo proceso asyncio, sin Mosquitto ni Caddy.

Dependencia: `cryptography` (solo para generar la CA y los certificados; sin el programa `openssl`). Claves de personas y módulos con `hashlib.scrypt` de la biblioteca estándar.

```bash
pip install -r requirements.txt
export CASA_DIR=datos                       # en Windows: set CASA_DIR=datos
python casa_servidor.py init --ip 192.168.1.50 --dns casa.lan
python casa_servidor.py alta-modulo riego                      # imprime usuario y clave una sola vez
python casa_servidor.py alta-persona jarvis --rol basico --canal agente --cmd riego
python casa_servidor.py alta-persona pablo-celu --rol completo --canal app
python casa_servidor.py servir --host 192.168.1.50             # por defecto solo 127.0.0.1; TLS 8883 + WebSocket 8884
python simulador_modulo.py --ca datos/ca.crt --host 192.168.1.50 --usuario m-riego --clave XXXX --tipo riego
python casa_servidor.py lista | bloquear p-celu | desbloquear p-celu | baja ... | clave ...
python -m unittest discover -s . -t .                          # pruebas
```

Política de la casa: copiar `politica.ejemplo.json` a `datos/politica.json` y editarla (se recarga sola). Portón/portero/cerradura nunca por voz ni agente; calefón y estufa con confirmación y tope; el tope de riego vale para todos los canales. Quien confirma es solo una sesión de canal `app` o `telegram`.

Qué está hecho: PKI (certificados válidos desde 1969), usuarios y permisos por rol, bloqueo en caliente, estado retenido, `online`/offline gestionado por el servidor, órdenes con `id` y confirmación por `cmd_id`, límites de tamaño y frecuencia, keepalive, una sesión por usuario. WebSocket propio (RFC 6455) para la app, política y confirmaciones, simulador de módulo y cliente de Jarvis. 62 pruebas en `casa-servidor/tests` (más `websockets` en el entorno de pruebas como cliente independiente; si no está, esas se saltan).

La app web está en `app/` y la sirve el servidor en el puerto WebSocket (`https://<ip>:8884/`). Origen externo: `servir --origen https://tu-app.vercel.app` habilita ese sitio web (CORS y WebSocket); ver el README de la raíz. Audio: `POST /media/audio` (ver `../docs/protocolo.md` §8) con cuota y retención; el análisis corre aparte (`../analisis`). Qué falta (ver `CLAUDE.md`): escenas y más tarjetas en la app, certificados de cliente para módulos (opcional), restricción de `usuarios.json` en Windows, y todo lo probado solo en Linux: **no se probó en Windows ni con un ESP32 real**.
