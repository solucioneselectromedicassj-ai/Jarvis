import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from casa.politica import Politica
from tests.test_servidor import Base

REGLAS = {"modulos": {
    "porton": {"prohibido_en": ["voz", "agente"]},
    "calefon": {"confirmar": ["encender"], "tope_minutos": 60},
    "riego": {"tope_minutos": 90},
}}


class Unidad(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "politica.json"
        self.path.write_text(json.dumps(REGLAS))
        self.p = Politica(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_prohibido_por_canal(self):
        self.assertEqual(self.p.evaluar("porton", "abrir", None, "agente")[0], "prohibido")
        self.assertEqual(self.p.evaluar("porton", "abrir", None, "voz")[0], "prohibido")
        self.assertEqual(self.p.evaluar("porton", "abrir", None, "app")[0], "ok")

    def test_tope(self):
        self.assertEqual(self.p.evaluar("riego", "abrir", {"minutos": 120}, "app")[0], "tope")
        self.assertEqual(self.p.evaluar("riego", "abrir", {"minutos": -5}, "app")[0], "tope")
        self.assertEqual(self.p.evaluar("riego", "abrir", {"minutos": "10"}, "app")[0], "tope")
        self.assertEqual(self.p.evaluar("riego", "abrir", {"minutos": True}, "app")[0], "tope")
        self.assertEqual(self.p.evaluar("riego", "abrir", {"minutos": 30}, "app"), ("ok", {"minutos": 30}))
        self.assertEqual(self.p.evaluar("riego", "abrir", None, "app"), ("ok", {"minutos": 90}))  # sin tiempo: se aplica el tope
        self.assertEqual(self.p.evaluar("riego", "cerrar", None, "app"), ("ok", {}))               # apagar no tiene tope

    def test_confirmar(self):
        self.assertEqual(self.p.evaluar("calefon", "encender", {"minutos": 30}, "app")[0], "confirmar")
        self.assertEqual(self.p.evaluar("calefon", "apagar", None, "agente")[0], "ok")

    def test_modulo_sin_reglas(self):
        self.assertEqual(self.p.evaluar("luces", "encender", None, "agente"), ("ok", None))

    def test_politica_ilegible_rechaza(self):
        self.path.write_text("{ esto no es json")
        self.assertEqual(self.p.evaluar("luces", "encender", None, "app")[0], "prohibido")

    def test_recarga_en_caliente(self):
        self.assertEqual(self.p.evaluar("luces", "encender", None, "app")[0], "ok")
        self.path.write_text(json.dumps({"modulos": {"luces": {"prohibido_en": ["app"]}}}))
        import os; os.utime(self.path, (1, 1))   # fuerza mtime distinto
        self.assertEqual(self.p.evaluar("luces", "encender", None, "app")[0], "prohibido")


class EnElServidor(Base):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.path = self.dir / "politica.json"
        self.path.write_text(json.dumps(REGLAS))
        self.srv.politica = Politica(self.path)
        self.srv.espera_cmd = 5
        for tipo, n, extra in (("persona", "app", {"rol": "completo", "canal": "app"}),
                               ("persona", "agente", {"rol": "basico", "canal": "agente", "cmd": ["porton", "calefon", "riego"]}),
                               ("persona", "voz", {"rol": "completo", "canal": "voz"}),
                               ("persona", "tg", {"rol": "completo", "canal": "telegram"}),
                               ("modulo", "porton", {}), ("modulo", "calefon", {})):
            u, c = self.reg.alta(tipo, n, **extra)
            self.claves[u] = c

    async def test_porton_nunca_por_agente_ni_voz_pero_si_por_app(self):
        m = await self.conectar("m-porton")
        for u in ("p-agente", "p-voz"):
            c = await self.conectar(u)
            await c.enviar(t="pub", topic="casa/porton/cmd", id=f"x-{u}", accion="abrir")
            r = await c.recibir()
            self.assertEqual(r["codigo"], "politica", u)
        self.assertIsNone(await m.nada())
        app = await self.conectar("p-app")
        await app.enviar(t="pub", topic="casa/porton/cmd", id="ok1", accion="abrir")
        orden = await m.recibir()
        self.assertEqual((orden["id"], orden["origen"], orden["de"]), ("ok1", "app", "p-app"))

    async def test_tope_de_tiempo(self):
        m = await self.conectar("m-riego")
        j = await self.conectar("p-agente")
        await j.enviar(t="pub", topic="casa/riego/cmd", id="a", accion="abrir", params={"minutos": 500})
        self.assertEqual((await j.recibir())["codigo"], "politica")
        await j.enviar(t="pub", topic="casa/riego/cmd", id="b", accion="abrir")
        self.assertEqual((await m.recibir())["params"], {"minutos": 90})

    async def test_confirmacion_por_app(self):
        m = await self.conectar("m-calefon")
        j = await self.conectar("p-agente")
        app = await self.conectar("p-app")
        await j.enviar(t="pub", topic="casa/calefon/cmd", id="c1", accion="encender", params={"minutos": 30})
        self.assertEqual((await j.recibir())["t"], "pendiente")
        pedido = await app.recibir()
        self.assertEqual((pedido["t"], pedido["id"], pedido["origen"]), ("confirmar", "c1", "agente"))
        self.assertIsNone(await m.nada())                    # todavía no llegó al módulo
        await app.enviar(t="confirmo", id="c1")
        orden = await m.recibir()
        self.assertEqual((orden["accion"], orden["origen"], orden["confirmo"], orden["de"]),
                         ("encender", "agente", "p-app", "p-agente"))
        await app.enviar(t="confirmo", id="c1")              # no se puede confirmar dos veces
        self.assertEqual((await app.recibir())["codigo"], "formato")

    async def test_el_agente_no_puede_confirmarse_a_si_mismo(self):
        m = await self.conectar("m-calefon")
        j = await self.conectar("p-agente")
        await self.conectar("p-app")
        await j.enviar(t="pub", topic="casa/calefon/cmd", id="c1", accion="encender")
        await j.recibir()
        await j.enviar(t="confirmo", id="c1")
        r = await j.recibir()
        self.assertEqual(r["codigo"], "permiso")
        self.assertIsNone(await m.nada())

    async def test_la_voz_tampoco_confirma(self):
        m = await self.conectar("m-calefon")
        j = await self.conectar("p-agente")
        v = await self.conectar("p-voz")
        app = await self.conectar("p-app")
        await j.enviar(t="pub", topic="casa/calefon/cmd", id="c1", accion="encender")
        await j.recibir()
        self.assertIsNone(await v.nada())                    # a la voz ni siquiera se le pide
        await v.enviar(t="confirmo", id="c1")
        self.assertEqual((await v.recibir())["codigo"], "permiso")
        self.assertIsNone(await m.nada())

    async def test_telegram_si_puede_confirmar(self):
        m = await self.conectar("m-calefon")
        j = await self.conectar("p-agente")
        tg = await self.conectar("p-tg")
        await j.enviar(t="pub", topic="casa/calefon/cmd", id="c1", accion="encender")
        await j.recibir()
        self.assertEqual((await tg.recibir())["t"], "confirmar")
        await tg.enviar(t="confirmo", id="c1")
        self.assertEqual((await m.recibir())["confirmo"], "p-tg")

    async def test_rechazo_y_vencimiento(self):
        self.srv.espera_confirmacion = 0.3
        await self.conectar("m-calefon")
        j = await self.conectar("p-agente")
        app = await self.conectar("p-app")
        await j.enviar(t="pub", topic="casa/calefon/cmd", id="c1", accion="encender")
        await j.recibir(); await app.recibir()
        await app.enviar(t="rechazo", id="c1")
        self.assertEqual((await j.recibir())["detalle"], "rechazada")
        await j.enviar(t="pub", topic="casa/calefon/cmd", id="c2", accion="encender")
        await j.recibir()
        r = await j.recibir()
        self.assertEqual(r["detalle"], "confirmación vencida")

    async def test_sin_app_conectada_no_se_puede(self):
        await self.conectar("m-calefon")
        j = await self.conectar("p-agente")
        await j.enviar(t="pub", topic="casa/calefon/cmd", id="c1", accion="encender")
        self.assertEqual((await j.recibir())["codigo"], "politica")

    async def test_no_se_puede_falsificar_el_sello(self):
        m = await self.conectar("m-riego")
        j = await self.conectar("p-agente")
        await j.enviar(t="pub", topic="casa/riego/cmd", id="f1", accion="cerrar", origen="app", confirmo="p-app")
        orden = await m.recibir()
        self.assertEqual(orden["origen"], "agente")
        self.assertNotIn("confirmo", orden)

    async def test_apagar_no_pide_confirmacion(self):
        m = await self.conectar("m-calefon")
        j = await self.conectar("p-agente")
        await j.enviar(t="pub", topic="casa/calefon/cmd", id="c1", accion="apagar")
        self.assertEqual((await m.recibir())["accion"], "apagar")


if __name__ == "__main__":
    unittest.main()
