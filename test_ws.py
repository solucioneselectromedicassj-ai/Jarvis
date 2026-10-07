import asyncio
import json
import ssl
import unittest

try:
    import websockets
except ImportError:  # solo se usa como cliente independiente en las pruebas
    websockets = None

from tests.test_servidor import Base


@unittest.skipIf(websockets is None, "falta `websockets` (solo para pruebas)")
class WebSocket(Base):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.ws_port = await self._abrir_ws()

    async def _abrir_ws(self):
        self.srv._ws_srv = await asyncio.start_server(self.srv._cliente_ws, "127.0.0.1", 0, ssl=self.srv.ctx, limit=16384)
        self.srv._origenes = None
        return self.srv._ws_srv.sockets[0].getsockname()[1]

    async def ws(self, usuario, clave=None):
        ctx = ssl.create_default_context(cafile=self.dir / "ca.crt")
        c = await websockets.connect(f"wss://127.0.0.1:{self.ws_port}", ssl=ctx)
        self.abiertos.append(_Cierre(c))
        await c.send(json.dumps({"v": 1, "t": "hola", "usuario": usuario, "clave": clave or self.claves[usuario]}))
        return c

    async def test_hola_pub_sub_entre_ws_y_tls(self):
        app = await self.ws("p-pablo")
        self.assertEqual(json.loads(await app.recv())["t"], "ok")
        await app.send(json.dumps({"v": 1, "t": "sub", "topics": ["casa/+/estado"]}))
        m = await self.conectar("m-riego")
        await m.enviar(t="pub", topic="casa/riego/estado", valor=7)
        msg = json.loads(await asyncio.wait_for(app.recv(), 2))
        self.assertEqual((msg["topic"], msg["valor"]), ("casa/riego/estado", 7))

    async def test_orden_desde_ws_llega_al_modulo(self):
        m = await self.conectar("m-riego")
        app = await self.ws("p-pablo")
        await app.recv()
        await app.send(json.dumps({"v": 1, "t": "pub", "topic": "casa/riego/cmd", "id": "w1", "accion": "abrir"}))
        orden = await m.recibir()
        self.assertEqual((orden["id"], orden["de"]), ("w1", "p-pablo"))

    async def test_auth_mala_por_ws(self):
        c = await self.ws("m-riego", clave="mala")
        self.assertEqual(json.loads(await c.recv())["codigo"], "auth")
        with self.assertRaises(websockets.ConnectionClosed):
            await asyncio.wait_for(c.recv(), 2)

    async def test_mensaje_grande(self):
        c = await self.ws("m-riego")
        await c.recv()
        await c.send(json.dumps({"v": 1, "t": "pub", "topic": "casa/riego/estado", "valor": "a" * 10000}))
        self.assertEqual(json.loads(await c.recv())["codigo"], "tamano")

    async def test_mensaje_fragmentado(self):
        c = await self.ws("m-riego")
        await c.recv()
        await c.send(['{"v":1,"t":', '"ping"}'])
        self.assertEqual(json.loads(await asyncio.wait_for(c.recv(), 2))["t"], "pong")

    async def test_ping_de_websocket(self):
        c = await self.ws("m-riego")
        await c.recv()
        pong = await c.ping()
        await asyncio.wait_for(pong, 2)

    async def test_binario_se_rechaza(self):
        c = await self.ws("m-riego")
        await c.recv()
        await c.send(b"\x00\x01")
        with self.assertRaises(websockets.ConnectionClosed):
            await asyncio.wait_for(c.recv(), 2)

    async def test_http_comun_no_es_websocket(self):
        ctx = ssl.create_default_context(cafile=self.dir / "ca.crt")
        r, w = await asyncio.open_connection("127.0.0.1", self.ws_port, ssl=ctx)
        w.write(b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
        await w.drain()
        self.assertTrue((await r.read(100)).startswith(b"HTTP/1.1 400"))
        w.close()

    async def test_origen_no_permitido(self):
        self.srv._origenes = ["https://casa.lan"]
        ctx = ssl.create_default_context(cafile=self.dir / "ca.crt")
        with self.assertRaises(websockets.InvalidStatus) as e:
            await websockets.connect(f"wss://127.0.0.1:{self.ws_port}", ssl=ctx, origin="https://malo.example")
        self.assertEqual(e.exception.response.status_code, 403)
        c = await websockets.connect(f"wss://127.0.0.1:{self.ws_port}", ssl=ctx, origin="https://casa.lan")
        await c.close()


class _Cierre:
    def __init__(self, c):
        self.c = c

    async def cerrar(self):
        await self.c.close()


if __name__ == "__main__":
    unittest.main()
