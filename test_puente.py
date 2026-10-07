"""Circuito completo de voz: audio subido -> puente (STT falso) -> cerebro de Jarvis -> casa-servidor (política) -> módulo -> respuesta."""
import asyncio
import json
import ssl
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(RAIZ / "analisis"), str(RAIZ / "jarvis-mcp")]
import cerebro as cb  # noqa: E402
import puente as pt  # noqa: E402
import stt  # noqa: E402
from casa_cliente import CasaCliente  # noqa: E402

from casa.politica import Politica  # noqa: E402
from simulador_modulo import Modulo  # noqa: E402
from tests.helpers import HttpMedios, pcm  # noqa: E402
from tests.test_servidor import Base  # noqa: E402


class Circuito(HttpMedios, Base):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        cb.INTERNET_TEXTO = False
        (self.dir / "politica.json").write_text(json.dumps({"modulos": {"porton": {"prohibido_en": ["voz", "agente"]}}}))
        self.srv.politica = Politica(self.dir / "politica.json")
        self.reg._modificar("p-jarvis", canal="agente", cmd=["riego", "porton"])        # Jarvis: canal agente
        u, c = self.reg.alta("modulo", "porton"); self.claves[u] = c
        self.tareas = []
        self.heard = {"texto": "abrí el riego 5 minutos"}
        ca = str(self.dir / "ca.crt")
        self.casa = CasaCliente("127.0.0.1", self.port, ca, "p-jarvis", self.claves["p-jarvis"])
        self.tareas.append(asyncio.create_task(self.casa.correr()))
        ctx = ssl.create_default_context(cafile=ca)
        for u, tipo in (("m-riego", "riego"), ("m-porton", "luces")):
            self.tareas.append(asyncio.create_task(Modulo(u, tipo).correr("127.0.0.1", self.port, ctx, self.claves[u])))
        import tempfile
        self.db = str(Path(tempfile.mkdtemp()) / "j.db")
        self.cerebro = cb.Cerebro(self.casa, self.db)
        self.stt = stt.Falso(lambda f: self.heard["texto"])
        self.puente = pt.Puente("127.0.0.1", self.port, ca, "s-voz", self.claves["s-voz"], self.dir / "media", self.stt, self.cerebro.atender)
        self.tareas.append(asyncio.create_task(self.puente.correr()))
        for _ in range(100):
            if self.casa.online.get("riego") and self.casa.online.get("porton") and self.puente.conectado:
                break
            await asyncio.sleep(0.05)
        self.celu = await self.conectar("p-celu")
        await self.celu.enviar(t="sub", topics=["casa/celu/respuesta"])

    async def asyncTearDown(self):
        for t in self.tareas:
            t.cancel()
        await asyncio.gather(*self.tareas, return_exceptions=True)
        await super().asyncTearDown()

    async def respuesta(self, timeout=6):
        m = await self.celu.recibir(timeout)
        return m

    async def test_voz_abre_el_riego_de_punta_a_punta(self):
        estado, r = await self.post("p-celu", self.claves["p-celu"], pcm(1.5))
        self.assertEqual(estado, 202)
        m = await self.respuesta()
        self.assertEqual((m["id"], m["texto"], m["respuesta"]), (r["id"], "abrí el riego 5 minutos", "Listo: abrir riego por 5 minutos."))
        self.assertEqual(self.casa.ver_estado("riego")["valor"], {"valvula": "abierta"})
        self.assertFalse(list((self.dir / "media" / "audio" / "celu").glob("*.wav")))      # el audio se borra tras transcribir

    async def test_por_voz_el_porton_nunca(self):
        self.heard["texto"] = "abrí el portón"
        await self.post("p-celu", self.claves["p-celu"], pcm())
        self.assertIn("no se maneja por voz", (await self.respuesta())["respuesta"])
        self.assertEqual(self.casa.ver_estado("porton")["valor"], {"luz": "apagada"})

    async def test_pregunta_escrita_llega_a_jarvis(self):
        self.heard["texto"] = "no se usa"
        await self.celu.enviar(t="pub", topic="casa/celu/pregunta", id="q1", texto="cómo está todo")
        m = await self.respuesta()
        self.assertEqual((m["id"], m["texto"]), ("q1", "cómo está todo"))
        self.assertIn("riego:", m["respuesta"])

    async def test_nadie_pregunta_a_nombre_de_otro(self):
        await self.celu.enviar(t="pub", topic="casa/otra-persona/pregunta", id="q1", texto="hola")
        self.assertEqual((await self.celu.recibir())["codigo"], "permiso")

    async def test_stt_falla_o_no_oye_nada(self):
        self.stt.texto = lambda f: (_ for _ in ()).throw(RuntimeError("modelo roto"))
        await self.post("p-celu", self.claves["p-celu"], pcm())
        self.assertEqual((await self.respuesta())["respuesta"], "No pude entender el audio.")
        self.stt.texto = lambda f: ""
        await self.post("p-celu", self.claves["p-celu"], pcm())
        self.assertIn("No te escuché", (await self.respuesta())["respuesta"])

    async def test_aviso_falso_no_hace_leer_otros_archivos(self):
        mic = await self.conectar("m-mic")
        otro = self.dir / "media" / "audio" / "celu"
        otro.mkdir(parents=True, exist_ok=True)
        ajeno = otro / "ajeno.wav"
        ajeno.write_bytes(b"x")
        for archivo in ("celu/ajeno.wav", "../../usuarios.json", "mic/../celu/ajeno.wav", "/etc/passwd", "mic/x.txt"):
            await mic.enviar(t="pub", topic="casa/mic/evento", tipo="audio", datos={"archivo": archivo, "id": "falso"})
        await asyncio.sleep(0.5)
        self.assertTrue(ajeno.exists())
        self.assertIsNone(await self.celu.nada(0.3))

    async def test_jarvis_caido(self):
        llamar = pt.jarvis_http("http://127.0.0.1:1", "t")
        self.assertEqual(await llamar("hola", "celu"), "No pude comunicarme con Jarvis.")
