import asyncio
import base64
import json
import ssl

from casa.servidor import cargar_app


def pcm(segundos=1.0):
    return b"\x01\x00\x02\x00" * int(16000 * segundos / 2)


class HttpMedios:
    """Mezcla para pruebas: habilita el modo HTTP (app + /media) del puerto WebSocket y da `post()`."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.srv.estaticos = cargar_app(self.dir)           # habilita el modo HTTP del puerto WebSocket
        self.srv.media_dir = self.dir / "media"
        self.srv._ws_srv = await asyncio.start_server(self.srv._cliente_ws, "127.0.0.1", 0, ssl=self.srv.ctx, limit=16384)
        self.srv._origenes = None
        self.http_port = self.srv._ws_srv.sockets[0].getsockname()[1]
        u, c = self.reg.alta("persona", "celu", rol="completo", canal="app"); self.claves[u] = c
        u, c = self.reg.alta("modulo", "mic", camara=True); self.claves[u] = c
        u, c = self.reg.alta("servicio", "voz", responde=True); self.claves[u] = c

    async def post(self, usuario, clave, cuerpo, ct="audio/L16;rate=16000", ruta="/media/audio", chunked=False, metodo="POST", largo=None):
        ctx = ssl.create_default_context(cafile=self.dir / "ca.crt")
        r, w = await asyncio.open_connection("127.0.0.1", self.http_port, ssl=ctx)
        auth = "Basic " + base64.b64encode(f"{usuario}:{clave}".encode()).decode()
        cab = f"{metodo} {ruta} HTTP/1.1\r\nHost: x\r\nAuthorization: {auth}\r\nContent-Type: {ct}\r\n"
        if chunked:
            w.write((cab + "Transfer-Encoding: chunked\r\n\r\n").encode())
            for i in range(0, len(cuerpo), 3000):
                trozo = cuerpo[i:i + 3000]
                w.write(f"{len(trozo):x}\r\n".encode() + trozo + b"\r\n")
            w.write(b"0\r\n\r\n")
        else:
            w.write((cab + f"Content-Length: {largo or len(cuerpo)}\r\n\r\n").encode() + cuerpo)
        await w.drain()
        data = await asyncio.wait_for(r.read(-1), 5)
        w.close()
        cab_r, _, cu = data.partition(b"\r\n\r\n")
        return int(cab_r.split(b" ")[1]), (json.loads(cu) if cu else {})

