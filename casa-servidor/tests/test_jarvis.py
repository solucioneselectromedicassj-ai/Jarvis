"""Jarvis (cliente del jarvis-mcp) contra el casa-servidor real y módulos simulados."""
import asyncio
import json
import ssl
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "jarvis-mcp"))
from casa_cliente import CasaCliente  # noqa: E402

from casa.politica import Politica  # noqa: E402
from simulador_modulo import Modulo  # noqa: E402
from tests.test_servidor import Base  # noqa: E402

REGLAS = {"modulos": {"porton": {"prohibido_en": ["voz", "agente"]},
                      "calefon": {"confirmar": ["encender"], "tope_minutos": 60}}}


class JarvisCasa(Base):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        (self.dir / "politica.json").write_text(json.dumps(REGLAS))
        self.srv.politica = Politica(self.dir / "politica.json")
        for tipo, n, extra in (("persona", "j", {"rol": "basico", "canal": "agente", "cmd": ["riego", "porton", "calefon"]}),
                               ("persona", "app", {"rol": "completo", "canal": "app"}),
                               ("modulo", "porton", {}), ("modulo", "calefon", {})):
            u, c = self.reg.alta(tipo, n, **extra)
            self.claves[u] = c
        self.avisos = []

        async def aviso(t):
            self.avisos.append(t)
        self.tareas = []
        self.jarvis = CasaCliente("127.0.0.1", self.port, str(self.dir / "ca.crt"), "p-j", self.claves["p-j"], on_aviso=aviso)
        self.tareas.append(asyncio.create_task(self.jarvis.correr()))

    async def asyncTearDown(self):
        for t in self.tareas:
            t.cancel()
        await asyncio.gather(*self.tareas, return_exceptions=True)
        await super().asyncTearDown()

    async def modulo(self, usuario, tipo):
        ctx = ssl.create_default_context(cafile=self.dir / "ca.crt")
        m = Modulo(usuario, tipo)
        self.tareas.append(asyncio.create_task(m.correr("127.0.0.1", self.port, ctx, self.claves[usuario])))
        return m

    async def hasta(self, cond, t=3):
        fin = asyncio.get_running_loop().time() + t
        while not cond():
            self.assertLess(asyncio.get_running_loop().time(), fin, "no se cumplió la condición a tiempo")
            await asyncio.sleep(0.05)

    async def test_estado_visible(self):
        await self.modulo("m-riego", "riego")
        await self.hasta(lambda: self.jarvis.online.get("riego") and "riego" in self.jarvis.estados)
        e = self.jarvis.ver_estado("riego")
        self.assertEqual((e["online"], e["valor"], e["acciones"]), (True, {"valvula": "cerrada"}, ["abrir", "cerrar"]))
        self.assertIsNone(self.jarvis.ver_estado("noexiste"))

    async def test_ordenar_y_confirmar(self):
        await self.modulo("m-riego", "riego")
        await self.hasta(lambda: self.jarvis.online.get("riego"))
        r = await self.jarvis.ordenar("riego", "abrir", {"minutos": 5})
        self.assertEqual((r["ok"], r["estado"], r["valor"]), (True, "cumplida", {"valvula": "abierta"}))

    async def test_porton_prohibido_para_jarvis(self):
        m = await self.modulo("m-porton", "luces")
        await self.hasta(lambda: self.jarvis.online.get("porton"))
        r = await self.jarvis.ordenar("porton", "abrir")
        self.assertEqual((r["ok"], r["error"]), (False, "politica"))
        self.assertEqual(m.ordenes_cumplidas, [])

    async def test_modulo_fuera_de_su_lista(self):
        await self.modulo("m-luces", "luces")
        await self.hasta(lambda: self.jarvis.online.get("luces"))
        r = await self.jarvis.ordenar("luces", "encender")
        self.assertEqual((r["ok"], r["error"]), (False, "permiso"))

    async def test_modulo_offline(self):
        await self.hasta(lambda: self.jarvis.conectado)
        r = await self.jarvis.ordenar("riego", "abrir")
        self.assertEqual((r["ok"], r["error"]), (False, "offline"))

    async def test_jarvis_no_puede_confirmar_pero_una_persona_si(self):
        m = await self.modulo("m-calefon", "luces")
        await self.hasta(lambda: self.jarvis.online.get("calefon"))
        app = await self.conectar("p-app")
        r = await self.jarvis.ordenar("calefon", "encender", {"minutos": 30}, espera=1)
        self.assertEqual((r["ok"], r["estado"]), (None, "esperando_confirmacion_de_una_persona"))
        self.assertEqual(m.ordenes_cumplidas, [])
        pedido = await app.recibir()
        self.assertEqual(pedido["t"], "confirmar")
        await app.enviar(t="confirmo", id=pedido["id"])
        await self.hasta(lambda: m.ordenes_cumplidas == [pedido["id"]])
        await self.hasta(lambda: (self.jarvis.estados["calefon"]["valor"] == {"luz": "encendida"}))

    async def test_avisos_de_conexion_solo_estado(self):
        await self.hasta(lambda: self.jarvis.conectado)
        m = Modulo("m-riego", "riego")
        ctx = ssl.create_default_context(cafile=self.dir / "ca.crt")
        t = asyncio.create_task(m.correr("127.0.0.1", self.port, ctx, self.claves["m-riego"]))
        self.tareas.append(t)
        await self.hasta(lambda: any("volvió a conectarse" in a for a in self.avisos))
        m._w.close()
        await self.hasta(lambda: any("se desconectó" in a for a in self.avisos))
        self.assertTrue(all(len(a) < 60 for a in self.avisos))     # nada de datos crudos

    async def test_sin_confirmacion_del_modulo_termina_en_error(self):
        self.srv.espera_cmd = 0.3
        await self.modulo("m-riego", "riego")
        await self.hasta(lambda: self.jarvis.online.get("riego"))
        r = await self.jarvis.ordenar("riego", "volar", espera=3)
        self.assertEqual((r["ok"], r["error"]), (False, "sin_respuesta"))
        self.assertTrue(any(a for a in self.avisos if "cmd_sin_respuesta" in a))

    async def test_sin_conexion(self):
        c = CasaCliente("127.0.0.1", 1, str(self.dir / "ca.crt"), "p-j", "x")
        self.assertEqual((await c.ordenar("riego", "abrir"))["error"], "sin_conexion")
