"""Puente de voz y texto: proceso APARTE y opcional (si se cuelga o gasta RAM, el control de la casa no se entera).

Escucha en casa-servidor los avisos de audio subido y las preguntas escritas, transcribe el audio con el plugin STT,
se lo pasa a Jarvis (POST /voz del jarvis-mcp) y publica la respuesta en casa/<fuente>/respuesta.
Entra como usuario `servicio --responde`: lee todo, pero solo puede escribir respuestas.

  CASA_HOST CASA_PORT CASA_CA CASA_USUARIO CASA_CLAVE   conexión a casa-servidor
  CASA_DIR              carpeta de datos del servidor (ahí está media/)
  JARVIS_URL            http://127.0.0.1:8000     JARVIS_VOZ_TOKEN   token solo para /voz
  STT=vosk|falso        VOSK_MODEL=ruta del modelo    BORRAR_AUDIO=1 (borra el audio apenas se transcribe)
"""
import asyncio
import json
import os
import ssl
import urllib.error
import urllib.request
from pathlib import Path

import stt as stt_mod


class Puente:
    def __init__(self, host, port, ca, usuario, clave, media: Path, stt, jarvis, borrar_audio=True):
        self.host, self.port, self.usuario, self.clave = host, port, usuario, clave
        self.ctx = ssl.create_default_context(cafile=ca)
        self.media, self.stt, self.jarvis, self.borrar = Path(media).resolve(), stt, jarvis, borrar_audio
        self._w = None
        self._cupo = asyncio.Semaphore(1)       # un audio por vez: el reconocimiento es lo que más RAM usa
        self.conectado = False

    async def _enviar(self, **m):
        self._w.write((json.dumps({"v": 1, **m}) + "\n").encode())
        await self._w.drain()

    async def correr(self):
        espera = 1
        liberar = asyncio.create_task(self._liberar())
        try:
            while True:
                try:
                    await self._sesion()
                    espera = 1
                except PermissionError:
                    raise
                except (OSError, ConnectionError, asyncio.IncompleteReadError, ssl.SSLError, ValueError):
                    pass
                self.conectado = False
                await asyncio.sleep(espera)
                espera = min(espera * 2, 60)
        finally:
            liberar.cancel()

    async def _liberar(self):
        while True:
            await asyncio.sleep(60)
            self.stt.liberar_si_inactivo()

    async def _sesion(self):
        r, w = await asyncio.open_connection(self.host, self.port, ssl=self.ctx)
        self._w = w
        try:
            await self._enviar(t="hola", usuario=self.usuario, clave=self.clave)
            ok = json.loads(await asyncio.wait_for(r.readline(), 10) or b"{}")
            if ok.get("t") != "ok":
                raise PermissionError(f"casa-servidor rechazó a {self.usuario}: {ok.get('codigo')}")
            await self._enviar(t="sub", topics=["casa/+/evento", "casa/+/pregunta"])
            self.conectado = True
            ping = asyncio.create_task(self._ping(ok.get("keepalive", 30) / 2))
            try:
                while linea := await r.readline():
                    m = json.loads(linea)
                    if m.get("t") != "msg" or m.get("retenido"):
                        continue
                    _, fuente, k = m["topic"].split("/", 2)
                    if k == "evento" and m.get("tipo") == "audio":
                        asyncio.create_task(self.audio(fuente, m.get("datos") or {}))
                    elif k == "pregunta":
                        asyncio.create_task(self.pregunta(fuente, m))
            finally:
                ping.cancel()
        finally:
            w.close()

    async def _ping(self, cada):
        while True:
            await asyncio.sleep(cada)
            await self._enviar(t="ping")

    async def _responder(self, fuente, id_, heard, respuesta):
        if self._w and not self._w.is_closing():
            await self._enviar(t="pub", topic=f"casa/{fuente}/respuesta", id=id_, texto=heard, respuesta=respuesta)

    # ------------------------------------------------------------ audio
    def _ruta(self, fuente, archivo) -> Path | None:
        """Solo archivos .wav dentro de media/audio/<fuente>/: un aviso falso no puede apuntar a otro lado."""
        if not isinstance(archivo, str) or not archivo.startswith(f"{fuente}/") or not archivo.endswith(".wav"):
            return None
        p = (self.media / "audio" / archivo).resolve()
        return p if (self.media / "audio" / fuente) in p.parents and p.is_file() else None

    async def audio(self, fuente, datos):
        ruta = self._ruta(fuente, datos.get("archivo"))
        if ruta is None:
            return
        try:
            async with self._cupo:
                texto = await asyncio.to_thread(self.stt.transcribir, ruta)
        except Exception as e:  # noqa: BLE001
            print(f"[puente] no pude transcribir {ruta.name}: {e}", flush=True)
            return await self._responder(fuente, datos.get("id"), "", "No pude entender el audio.")
        finally:
            if self.borrar:
                ruta.unlink(missing_ok=True)
        if not texto:
            return await self._responder(fuente, datos.get("id"), "", "No te escuché bien. Probá de nuevo.")
        await self._responder(fuente, datos.get("id"), texto, await self.jarvis(texto, fuente))

    async def pregunta(self, fuente, m):
        texto = m.get("texto")
        if isinstance(texto, str) and texto.strip():
            await self._responder(fuente, m.get("id"), texto.strip()[:500], await self.jarvis(texto, fuente))


def jarvis_http(url: str, token: str):
    """Llama a POST /voz del jarvis-mcp (solo biblioteca estándar)."""
    def llamar(texto, fuente):
        req = urllib.request.Request(url.rstrip("/") + "/voz", data=json.dumps({"texto": texto, "fuente": fuente}).encode(),
                                     headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=45) as r:
                return json.load(r)["respuesta"]
        except (urllib.error.URLError, OSError, ValueError, KeyError):
            return "No pude comunicarme con Jarvis."

    async def f(texto, fuente):
        return await asyncio.to_thread(llamar, texto, fuente)
    return f


async def main():
    e = os.environ
    for k in ("CASA_CA", "CASA_USUARIO", "CASA_CLAVE", "CASA_DIR", "JARVIS_VOZ_TOKEN"):
        if not e.get(k):
            raise SystemExit(f"Falta la variable {k}")
    p = Puente(e.get("CASA_HOST", "127.0.0.1"), int(e.get("CASA_PORT", "8883")), e["CASA_CA"], e["CASA_USUARIO"], e["CASA_CLAVE"],
               Path(e["CASA_DIR"]) / "media", stt_mod.crear(e.get("STT", "vosk"), e.get("VOSK_MODEL", "")),
               jarvis_http(e.get("JARVIS_URL", "http://127.0.0.1:8000"), e["JARVIS_VOZ_TOKEN"]), e.get("BORRAR_AUDIO", "1") == "1")
    await p.correr()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
