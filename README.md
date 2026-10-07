# Puente de voz y texto (proceso aparte y opcional)

Transcribe el audio que suben los micrófonos y el botón de hablar, se lo pasa a Jarvis y publica la respuesta.
Está separado del servidor a propósito: si el reconocimiento de voz consume memoria o se cuelga, el control de la casa sigue andando.

```
pip install vosk                      # biblioteca de reconocimiento (local)
# bajar el modelo chico en español: https://alphacephei.com/vosk/models  (vosk-model-small-es-*), descomprimirlo
python casa_servidor.py alta-servicio voz --responde          # (en casa-servidor) crea s-voz
export CASA_DIR=datos CASA_CA=datos/ca.crt CASA_USUARIO=s-voz CASA_CLAVE=... JARVIS_VOZ_TOKEN=... STT=vosk VOSK_MODEL=/ruta/al/modelo
python puente.py
```
- El audio se borra apenas se transcribe (`BORRAR_AUDIO=1`). Nunca sale de la PC.
- El modelo se carga con el primer audio y se descarga tras 5 minutos sin uso (la PC tiene poca RAM).
- `STT=falso` sirve para probar el circuito sin modelo.
- **Vosk con un modelo real no se probó todavía** (no se pudo bajar el modelo en el entorno de desarrollo).
