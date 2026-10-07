"""Subida de audio por HTTPS (fuera del canal de mensajes). POST /media/audio con Authorization: Basic usuario:clave.

Cuerpo: PCM crudo 16 kHz, 16 bits, mono (`Content-Type: audio/L16;rate=16000`), con Content-Length o chunked
(un ESP32 que graba mientras se mantiene apretado un botón no sabe el largo de antemano). Se guarda como WAV con
cuota y retención. Nunca viaja por el canal de mensajes.
"""
import asyncio
import base64
import binascii
import json
import secrets
import struct
import time
from pathlib import Path

RATE = 16000
MAX_SEG = 30
MAX_BYTES = RATE * 2 * MAX_SEG
MIN_BYTES = int(RATE * 2 * 0.3)
TIMEOUT = 40


class HTTPError(Exception):
    def __init__(self, estado: str, detalle: str = ""):
        self.estado, self.detalle = estado, detalle


def wav(pcm: bytes) -> bytes:
    return (b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, RATE, RATE * 2, 2, 16)
            + b"data" + struct.pack("<I", len(pcm)) + pcm)


async def _leer_cuerpo(reader, h: dict) -> bytes:
    if "chunked" in h.get("transfer-encoding", "").lower():
        partes, total = [], 0
        while True:
            try:
                n = int((await reader.readline()).split(b";")[0].strip() or b"x", 16)
            except ValueError:
                raise HTTPError("400 Bad Request", "chunked inválido")
            if n == 0:
                return b"".join(partes)
            total += n
            if total > MAX_BYTES:
                raise HTTPError("413 Payload Too Large", f"máximo {MAX_SEG} s")
            partes.append(await reader.readexactly(n))
            await reader.readexactly(2)
    try:
        n = int(h.get("content-length", ""))
    except ValueError:
        raise HTTPError("411 Length Required")
    if n > MAX_BYTES:
        raise HTTPError("413 Payload Too Large", f"máximo {MAX_SEG} s")
    return await reader.readexactly(n)


def purgar(raiz: Path, cuota_bytes: int, retencion_s: float) -> int:
    """Borra lo vencido y, si todavía no entra, lo más viejo. Devuelve los bytes que quedan."""
    archivos = sorted((f for f in raiz.rglob("*.wav") if f.is_file()), key=lambda f: f.stat().st_mtime)
    ahora = time.time()
    usados = 0
    vivos = []
    for f in archivos:
        if ahora - f.stat().st_mtime > retencion_s:
            f.unlink(missing_ok=True)
        else:
            vivos.append(f)
            usados += f.stat().st_size
    while vivos and usados > cuota_bytes:
        f = vivos.pop(0)
        usados -= f.stat().st_size
        f.unlink(missing_ok=True)
    return usados


async def responder(writer, estado: str, cuerpo: dict | None = None):
    datos = json.dumps(cuerpo or {}).encode()
    extra = "WWW-Authenticate: Basic realm=\"casa\"\r\n" if estado.startswith("401") else ""
    writer.write((f"HTTP/1.1 {estado}\r\nContent-Type: application/json\r\nContent-Length: {len(datos)}\r\n{extra}"
                  "Cache-Control: no-store\r\nConnection: close\r\n\r\n").encode() + datos)
    try:
        await writer.drain()
    except ConnectionError:
        pass
    writer.close()


async def subir(servidor, metodo: str, ruta: str, h: dict, reader, writer):
    """Atiende POST /media/audio. `servidor` aporta: media_dir, cuota, retencion, autenticar(), publicar()."""
    try:
        if ruta.split("?")[0] != "/media/audio" or metodo != "POST" or servidor.media_dir is None:
            raise HTTPError("404 Not Found")
        try:
            usuario, _, clave = base64.b64decode(h.get("authorization", "")[6:], validate=True).decode().partition(":")
        except (binascii.Error, UnicodeDecodeError, ValueError):
            usuario = clave = ""
        fuente = await servidor.autenticar_audio(usuario, clave)
        if fuente is None:
            raise HTTPError("401 Unauthorized")
        if fuente is False:
            raise HTTPError("403 Forbidden", "este usuario no puede subir audio")
        ct = h.get("content-type", "").replace(" ", "").lower()
        if not ct.startswith("audio/l16") or f"rate={RATE}" not in ct:
            raise HTTPError("415 Unsupported Media Type", f"se espera audio/L16;rate={RATE} (PCM 16 bits mono)")
        if servidor.subidas_activas >= 4:
            raise HTTPError("503 Service Unavailable", "demasiadas subidas a la vez")
        servidor.subidas_activas += 1
        try:
            pcm = await asyncio.wait_for(_leer_cuerpo(reader, h), TIMEOUT)
        finally:
            servidor.subidas_activas -= 1
        pcm = pcm[: len(pcm) // 2 * 2]
        if len(pcm) < MIN_BYTES:
            raise HTTPError("400 Bad Request", "audio demasiado corto")
        usados = purgar(servidor.media_dir, servidor.cuota_bytes, servidor.retencion_s)
        if usados + len(pcm) > servidor.cuota_bytes:
            raise HTTPError("507 Insufficient Storage", "cuota de medios llena")
        nombre = f"{int(time.time())}-{secrets.token_hex(4)}"
        destino = servidor.media_dir / "audio" / fuente
        destino.mkdir(parents=True, exist_ok=True)
        (destino / f"{nombre}.wav").write_bytes(wav(pcm))
        segundos = round(len(pcm) / (RATE * 2), 2)
        servidor.publicar_audio(fuente, nombre, len(pcm), segundos)
        await responder(writer, "202 Accepted", {"id": nombre, "segundos": segundos})
    except HTTPError as e:
        await responder(writer, e.estado, {"error": e.detalle or e.estado})
    except (asyncio.TimeoutError, asyncio.IncompleteReadError, ConnectionError):
        writer.close()
