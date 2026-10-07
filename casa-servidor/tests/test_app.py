import asyncio
import ssl
import tempfile
import unittest
from pathlib import Path

from casa import pki
from casa.registro import Registro
from casa.servidor import Servidor, cargar_app, contexto_tls

APP = Path(__file__).resolve().parents[1] / "app"


class ServirApp(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        pki.crear_ca(self.dir)
        pki.emitir_servidor(self.dir, ["127.0.0.1"], [])
        (self.dir / "secreto.txt").write_text("no debe salir")
        self.srv = Servidor(Registro(self.dir / "u.json"), contexto_tls(self.dir), app_dir=APP)
        await self.srv.iniciar("127.0.0.1", 0, ws_port=0)

    async def asyncTearDown(self):
        await self.srv.detener()
        self.tmp.cleanup()

    async def get(self, ruta, metodo="GET"):
        ctx = ssl.create_default_context(cafile=self.dir / "ca.crt")
        r, w = await asyncio.open_connection("127.0.0.1", self.srv.ws_port, ssl=ctx)
        w.write(f"{metodo} {ruta} HTTP/1.1\r\nHost: x\r\n\r\n".encode())
        await w.drain()
        data = await asyncio.wait_for(r.read(-1), 3)
        w.close()
        cab, _, cuerpo = data.partition(b"\r\n\r\n")
        return cab.decode(), cuerpo

    async def test_sirve_la_app_con_cabeceras_de_seguridad(self):
        cab, cuerpo = await self.get("/")
        self.assertTrue(cab.startswith("HTTP/1.1 200"))
        self.assertIn("text/html", cab)
        self.assertIn("Content-Security-Policy: default-src 'self'", cab)
        self.assertIn("X-Content-Type-Options: nosniff", cab)
        self.assertIn(b"<title>Casa</title>", cuerpo)

    async def test_archivos_de_la_app_y_vendor(self):
        for ruta, tipo in (("/manifest.webmanifest", "manifest"), ("/sw.js", "javascript"), ("/vendor/react.production.min.js", "javascript"),
                           ("/icono-192.png", "image/png")):
            cab, cuerpo = await self.get(ruta)
            self.assertTrue(cab.startswith("HTTP/1.1 200"), ruta)
            self.assertIn(tipo, cab)
            self.assertTrue(cuerpo)

    async def test_no_sale_nada_fuera_de_la_carpeta_de_la_app(self):
        for ruta in ("/../secreto.txt", "/secreto.txt", "/%2e%2e/secreto.txt", "/usuarios.json", "/vendor/", "/ca.key", "//etc/passwd"):
            cab, _ = await self.get(ruta)
            self.assertTrue(cab.startswith("HTTP/1.1 404"), ruta)

    async def test_solo_get(self):
        cab, _ = await self.get("/", metodo="POST")
        self.assertTrue(cab.startswith("HTTP/1.1 404"))

    async def test_cargar_app_ignora_ocultos_y_extensiones_raras(self):
        d = self.dir / "web"
        (d / ".git").mkdir(parents=True)
        (d / ".git" / "config").write_text("x"); (d / "a.py").write_text("x"); (d / "index.html").write_text("<p>")
        self.assertEqual(sorted(cargar_app(d)), ["/", "/index.html"])

    async def test_la_app_no_carga_recursos_de_internet(self):
        html = (APP / "index.html").read_text(encoding="utf-8")
        self.assertNotRegex(html, r"""(src|href)\s*=\s*["']?(https?:)?//""")     # ni scripts, ni estilos, ni fuentes externas
        self.assertNotRegex(html, r"@import|url\(\s*[\"']?https?:")
        for f in (APP / "sw.js", APP / "manifest.webmanifest"):
            self.assertNotRegex(f.read_text(encoding="utf-8"), r"https?://")


if __name__ == "__main__":
    unittest.main()
