# Hablar con Jarvis: voz, texto y conversación

**Es un solo Jarvis** para la casa, el taller y lo que venga. Decisiones de Pablo (octubre 2026):
- **Conversación:** Gemini, nivel gratuito, como primer proveedor.
- **Micrófonos:** un ESP32 con micrófono en la casa **y** el botón 🎤 de la app, los dos desde el principio.
- **Voz de Jarvis:** una sola voz sintética, lo más simple posible (la del propio teléfono); más adelante se cambia por una más humana.

## Cómo funciona

```
 🎤 teléfono (app)  ─┐   POST /media/audio (HTTPS, PCM 16 kHz)        ┌─ puente de voz (proceso aparte, opcional)
 🎤 ESP32 + INMP441 ─┴──────────────► casa-servidor ── evento "audio" ─┤   Vosk (local) → texto → Jarvis POST /voz
                                       (cuota, retención)              └── publica casa/<fuente>/respuesta
 texto escrito en la app que el intérprete local no entiende → casa/<usuario>/pregunta ───────────┘
```
1. **Intérprete local** (sin internet): "abrí el riego 10 minutos", "cómo está el tanque". Da las órdenes por `casa-servidor`, que aplica la política: portón, portero y cerraduras nunca por voz ni texto; calefón y estufas piden confirmación en la app; topes de tiempo.
2. **Gemini** solo si no es una orden de la casa **y** Pablo lo habilitó (`JARVIS_INTERNET_TEXTO=1` + `JARVIS_GEMINI_KEY`). Gemini **no tiene herramientas**: no puede mover nada, aunque un texto lo quiera engañar.
3. **Qué sale a internet:** solo el texto de lo que se le pregunta a Gemini (y las últimas 6 frases de esa charla). **Nunca audio, foto ni video** (el audio se transcribe en la PC y se borra). El estado de la casa solo sale si `JARVIS_ENVIAR_ESTADO_CASA=1` (apagado por defecto). Tope diario de consultas (`JARVIS_GEMINI_MAX_DIA`, por defecto 100) para cuidar el cupo gratis.
4. **Aviso:** en el nivel gratuito Google puede usar lo que se envía para mejorar sus productos y los límites cambian; revisalo en la consola de AI Studio antes de habilitarlo. El modelo por defecto (`gemini-2.5-flash`) hay que confirmar que tenga nivel gratuito en tu cuenta.
5. **Respuesta:** la app la muestra y la dice con la voz del teléfono (solo si el teléfono trae una voz en español *local*; si no, queda en texto). No se usa el reconocimiento de voz del navegador porque manda el audio a Google.

## Qué falta
- Probar **Vosk con el modelo real** en la PC (no se pudo bajar el modelo en el entorno de desarrollo; todo el circuito está probado con un transcriptor falso).
- Probar el sketch del micrófono en un ESP32 real. Hoy no se conecta al canal de mensajes (no figura "online" y no recibe respuestas): la respuesta se ve en el teléfono.
- "Ey Jarvis" siempre escuchando: un navegador no puede escuchar con la pantalla apagada; hace falta una app nativa de Android o una palabra de activación en el ESP32. Hoy: botón.
- Parlante en la casa (síntesis de voz en el servidor, por ejemplo Piper) y voz más humana.
- Que Gemini consulte la web o use herramientas de la casa: a propósito no.
