import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cerebro as cb  # noqa: E402
from interprete import interpretar, minutos  # noqa: E402

MODS = {"riego": {"tipo": "riego", "acciones": ["abrir", "cerrar"]}, "luces": {"tipo": "luces", "acciones": ["encender", "apagar"]},
        "tanque": {"tipo": "tanque", "acciones": []}, "porton": {"tipo": "porton", "acciones": ["abrir"]}}


class Interprete(unittest.TestCase):
    def test_ordenes(self):
        casos = [("abrí el riego 10 minutos", "orden", "riego", "abrir", 10), ("Cerrá el riego", "orden", "riego", "cerrar", None),
                 ("prendé la luz", "orden", "luces", "encender", None), ("apagá las luces", "orden", "luces", "apagar", None),
                 ("abrí el riego media hora", "orden", "riego", "abrir", 30), ("abrime el riego una hora", "orden", "riego", "abrir", 60),
                 ("abrí el riego 2 horas", "orden", "riego", "abrir", 120)]
        for texto, tipo, mod, acc, mins in casos:
            r = interpretar(texto, MODS)
            self.assertEqual((r["tipo"], r.get("modulo"), r.get("accion"), r.get("minutos")), (tipo, mod, acc, mins), texto)

    def test_estado_y_nada(self):
        self.assertEqual(interpretar("¿cómo está el tanque?", MODS), {"tipo": "estado", "modulo": "tanque"})
        self.assertEqual(interpretar("cuánto hay en el tanque", MODS)["tipo"], "estado")
        self.assertEqual(interpretar("cómo está todo", MODS), {"tipo": "estado", "modulo": None})
        for t in ("contame un chiste", "quién ganó el mundial", "abrí", "el riego", ""):
            self.assertEqual(interpretar(t, MODS)["tipo"], "nada", t)

    def test_minutos(self):
        self.assertIsNone(minutos("abrí el riego"))
        self.assertEqual(minutos("30 min"), 30)


class CasaFalsa:
    def __init__(self):
        self.descripciones = {m: {"tipo": v["tipo"]} for m, v in MODS.items()}
        self.ordenes, self.resultado = [], {"ok": True, "estado": "cumplida"}

    def ver_estado(self, modulo=None):
        todos = [{"modulo": m, "online": m != "luces", "valor": {"nivel": 50} if m == "tanque" else {"x": 1}, "acciones": v["acciones"]}
                 for m, v in MODS.items()]
        return next(e for e in todos if e["modulo"] == modulo) if modulo else todos

    async def ordenar(self, modulo, accion, params=None, espera=10):
        self.ordenes.append((modulo, accion, params))
        return self.resultado


class Cerebro(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "j.db")
        self.casa, self.pedidos = CasaFalsa(), []
        cb.GEMINI_KEY, cb.INTERNET_TEXTO, cb.ENVIAR_ESTADO, cb.MAX_POR_DIA = "clave-de-prueba", True, False, 100
        self.respuesta = lambda req: httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "Respuesta de prueba."}]}}]})
        self.cerebro = cb.Cerebro(self.casa, self.db, transporte=httpx.MockTransport(self._manejar))

    def tearDown(self):
        self.tmp.cleanup()

    def _manejar(self, req):
        self.pedidos.append(req)
        return self.respuesta(req)

    async def test_orden_local_no_usa_internet(self):
        r = await self.cerebro.atender("abrí el riego 10 minutos")
        self.assertEqual(self.casa.ordenes, [("riego", "abrir", {"minutos": 10})])
        self.assertEqual((r, self.pedidos), ("Listo: abrir riego por 10 minutos.", []))

    async def test_estado_local(self):
        self.assertEqual(await self.cerebro.atender("cómo está el tanque"), "tanque: nivel 50.")
        self.assertIn("luces: desconectado.", await self.cerebro.atender("cómo está todo"))
        self.assertEqual(self.pedidos, [])

    async def test_portón_nunca_por_voz(self):
        r = await self.cerebro.atender("abrí el portón")
        self.assertIn("no se maneja por voz", r)
        self.assertEqual((self.casa.ordenes, self.pedidos), ([], []))

    async def test_accion_que_el_modulo_no_acepta(self):
        self.assertIn("no entiende", await self.cerebro.atender("encendé el riego"))
        self.assertEqual(self.casa.ordenes, [])

    async def test_resultados_de_la_orden(self):
        for res, esperado in (({"ok": None, "estado": "esperando_confirmacion_de_una_persona"}, "confirme en la aplicación"),
                              ({"ok": False, "error": "offline"}, "desconectado"), ({"ok": False, "error": "politica", "detalle": "tope 60"}, "tope 60"),
                              ({"ok": False, "error": "sin_conexion"}, "Perdí la conexión")):
            self.casa.resultado = res
            self.assertIn(esperado, await self.cerebro.atender("abrí el riego"))

    async def test_conversacion_con_gemini(self):
        r = await self.cerebro.atender("contame un chiste", fuente="celu")
        self.assertEqual(r, "Respuesta de prueba.")
        req = self.pedidos[0]
        self.assertEqual(req.headers["x-goog-api-key"], "clave-de-prueba")
        self.assertNotIn("clave-de-prueba", str(req.url))                   # la clave va en cabecera, no en la URL
        cuerpo = json.loads(req.content)
        self.assertNotIn("tools", cuerpo)                                    # el modelo no tiene herramientas: no puede mover la casa
        self.assertEqual(cuerpo["contents"][-1]["parts"][0]["text"], "contame un chiste")
        self.assertNotIn("Estado actual", cuerpo["system_instruction"]["parts"][0]["text"])   # el estado de la casa no sale por defecto

    async def test_historial_para_seguir_la_charla(self):
        await self.cerebro.atender("quién descubrió América", fuente="celu")
        await self.cerebro.atender("¿y en qué año?", fuente="celu")
        roles = [c["role"] for c in json.loads(self.pedidos[1].content)["contents"]]
        self.assertEqual(roles, ["user", "model", "user"])
        await self.cerebro.atender("hola", fuente="otro")
        self.assertEqual(len(json.loads(self.pedidos[2].content)["contents"]), 1)           # cada fuente tiene su charla

    async def test_estado_de_la_casa_solo_si_se_habilita(self):
        cb.ENVIAR_ESTADO = True
        await self.cerebro.atender("contame un chiste")
        self.assertIn("Estado actual", json.loads(self.pedidos[0].content)["system_instruction"]["parts"][0]["text"])

    async def test_apagado_por_defecto(self):
        for key, inet in (("", True), ("k", False)):
            cb.GEMINI_KEY, cb.INTERNET_TEXTO = key, inet
            self.assertIn("habilitar el modelo", await self.cerebro.atender("contame un chiste"))
        self.assertEqual(self.pedidos, [])

    async def test_tope_diario_y_errores(self):
        cb.MAX_POR_DIA = 2
        await self.cerebro.atender("uno de charla"); await self.cerebro.atender("dos de charla")
        self.assertIn("todas las consultas gratuitas", await self.cerebro.atender("tres de charla"))
        self.assertEqual(len(self.pedidos), 2)
        cb.MAX_POR_DIA = 100
        self.respuesta = lambda r: httpx.Response(429)
        self.assertIn("límite gratuito", await self.cerebro.atender("otra pregunta"))
        self.respuesta = lambda r: httpx.Response(500)
        self.assertIn("No pude consultar", await self.cerebro.atender("otra pregunta más"))
        self.respuesta = lambda r: httpx.Response(200, json={"candidates": []})
        self.assertIn("respuesta clara", await self.cerebro.atender("y otra"))

    async def test_sin_conexion_a_internet(self):
        def falla(req): raise httpx.ConnectError("sin red")
        self.respuesta = falla
        self.assertIn("No pude consultar a internet", await self.cerebro.atender("contame algo"))

    async def test_vacio_y_largo(self):
        self.assertEqual(await self.cerebro.atender("   "), "No te escuché.")
        await self.cerebro.atender("x" * 5000)
        self.assertLessEqual(len(json.loads(self.pedidos[0].content)["contents"][-1]["parts"][0]["text"]), 500)


if __name__ == "__main__":
    unittest.main()
