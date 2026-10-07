"""WebSocket mínimo (RFC 6455) propio, para la app web: un navegador no abre sockets TLS crudos.

Va dentro del mismo TLS que el resto. Adapta cada mensaje de texto a "una línea" para reutilizar el servidor.
"""
import asyncio
import base64
import hashlib
import struct
from urllib.parse import urlparse

GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
HEADERS_MAX = 16384


def origen_permitido(origen: str | None, host: str | None, origenes: list[str] | None) -> bool:
    """Sin Origin (no es un navegador) o mismo origen: siempre. Otro sitio web: solo si está en la lista (`origenes`).

    Sin lista configurada se acepta cualquiera (igual hace falta usuario y clave); con lista, solo los nombrados.
    """
    if not origen or origenes is None:
        return True
    return origen in origenes or urlparse(origen).netloc == (host or "")


class WSError(Exception):
    pass


async def _rechazar(writer, estado: str):
    writer.write(f"HTTP/1.1 {estado}\r\nConnection: close\r\nContent-Length: 0\r\n\r\n".encode())
    try:
        await writer.drain()
    except ConnectionError:
        pass
    writer.close()


class WSWriter:
    def __init__(self, writer):
        self._w, self.transport = writer, writer.transport
        self._cerrado = False

    def _frame(self, opcode: int, payload: bytes):
        n = len(payload)
        cab = bytes([0x80 | opcode])
        cab += bytes([n]) if n < 126 else b"\x7e" + struct.pack("!H", n) if n < 65536 else b"\x7f" + struct.pack("!Q", n)
        self._w.write(cab + payload)

    def write(self, data: bytes):
        if not self._cerrado:
            self._frame(0x1, data.rstrip(b"\n"))

    def pong(self, payload: bytes):
        if not self._cerrado:
            self._frame(0xA, payload[:125])

    def close(self, codigo: int = 1000):
        if not self._cerrado:
            self._cerrado = True
            try:
                self._frame(0x8, struct.pack("!H", codigo))
            except Exception:  # noqa: BLE001
                pass
        self._w.close()

    async def wait_closed(self):
        await self._w.wait_closed()


class WSReader:
    def __init__(self, reader, writer: WSWriter, max_bytes: int):
        self._r, self._ws, self.max = reader, writer, max_bytes

    async def readline(self) -> bytes:
        """Siguiente mensaje de texto + b'\\n'. b'' = cerrado. ValueError = mensaje demasiado grande."""
        partes, total = [], 0
        while True:
            b1, b2 = await self._r.readexactly(2)
            fin, opcode, enmascarado, n = bool(b1 & 0x80), b1 & 0x0F, bool(b2 & 0x80), b2 & 0x7F
            if b1 & 0x70 or not enmascarado:          # bits reservados o cliente sin máscara: protocolo roto
                self._ws.close(1002)
                return b""
            if n == 126:
                (n,) = struct.unpack("!H", await self._r.readexactly(2))
            elif n == 127:
                (n,) = struct.unpack("!Q", await self._r.readexactly(8))
            if n > self.max or total + n > self.max:
                raise ValueError("mensaje demasiado grande")
            mask = await self._r.readexactly(4)
            datos = bytearray(await self._r.readexactly(n))
            for i in range(n):
                datos[i] ^= mask[i % 4]
            if opcode == 0x8:
                self._ws.close(1000)
                return b""
            if opcode == 0x9:
                self._ws.pong(bytes(datos))
                continue
            if opcode == 0xA:
                continue
            if opcode not in (0x0, 0x1) or (opcode == 0x0 and not partes) or (opcode == 0x1 and partes):
                self._ws.close(1003 if opcode == 0x2 else 1002)    # binario o fragmentación inválida
                return b""
            partes.append(bytes(datos))
            total += n
            if fin:
                return b"".join(partes) + b"\n"


CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")


async def _servir(writer, metodo: str, ruta: str, estaticos: dict):
    """Responde un GET de un archivo de la app (solo los que se cargaron al arrancar)."""
    ruta = ruta.split("?", 1)[0]
    item = estaticos.get(ruta) if metodo == "GET" else None
    if item is None:
        return await _rechazar(writer, "404 Not Found")
    cuerpo, tipo = item
    cache = "no-cache" if ruta in ("/", "/sw.js", "/index.html") else "public, max-age=3600"
    writer.write((f"HTTP/1.1 200 OK\r\nContent-Type: {tipo}\r\nContent-Length: {len(cuerpo)}\r\nCache-Control: {cache}\r\n"
                  f"Content-Security-Policy: {CSP}\r\nX-Content-Type-Options: nosniff\r\nReferrer-Policy: no-referrer\r\nPermissions-Policy: microphone=(self), camera=(), geolocation=()\r\n"
                  "Connection: close\r\n\r\n").encode() + cuerpo)
    try:
        await writer.drain()
    except ConnectionError:
        pass
    writer.close()


async def aceptar(reader, writer, max_bytes: int, origenes: list[str] | None = None, estaticos: dict | None = None, medios=None):
    """Handshake HTTP -> WebSocket. Devuelve (WSReader, WSWriter) o None si lo rechazó."""
    try:
        cabecera = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
    except (asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionError):
        writer.close()
        return None
    lineas = cabecera.decode("latin-1").split("\r\n")
    h = {}
    for ln in lineas[1:]:
        if ":" in ln:
            k, v = ln.split(":", 1)
            h[k.strip().lower()] = v.strip()
    if estaticos is not None and h.get("upgrade", "").lower() != "websocket":
        partes = lineas[0].split(" ")
        if medios and partes[0] in ("POST", "OPTIONS") and len(partes) > 1 and partes[1].startswith("/media/"):
            await medios(partes[0], partes[1], h, reader, writer)
            return None
        await _servir(writer, partes[0], partes[1] if len(partes) > 1 else "/", estaticos)
        return None
    if not lineas[0].startswith("GET ") or h.get("upgrade", "").lower() != "websocket" \
            or "upgrade" not in h.get("connection", "").lower() or h.get("sec-websocket-version") != "13":
        await _rechazar(writer, "400 Bad Request")
        return None
    try:
        if len(base64.b64decode(h["sec-websocket-key"], validate=True)) != 16:
            raise ValueError
    except (KeyError, ValueError):
        await _rechazar(writer, "400 Bad Request")
        return None
    if not origen_permitido(h.get("origin"), h.get("host"), origenes):
        await _rechazar(writer, "403 Forbidden")
        return None
    acepta = base64.b64encode(hashlib.sha1(h["sec-websocket-key"].encode() + GUID).digest()).decode()
    writer.write(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                  f"Sec-WebSocket-Accept: {acepta}\r\n\r\n").encode())
    await writer.drain()
    ww = WSWriter(writer)
    return WSReader(reader, ww, max_bytes), ww
