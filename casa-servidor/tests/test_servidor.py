import asyncio
import json
import ssl
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path

from cryptography import x509

from casa import pki
from casa.registro import Registro
from casa.servidor import Servidor, contexto_tls


class Cliente:
    def __init__(self, r, w):
        self.r, self.w = r, w

    async def enviar(self, **m):
        self.w.write((json.dumps({"v": 1, **m}) + "\n").encode())
        await self.w.drain()

    async def recibir(self, timeout=2):
        linea = await asyncio.wait_for(self.r.readline(), timeout)
        return json.loads(linea) if linea else None

    async def nada(self, timeout=0.3):
        try:
            return await asyncio.wait_for(self.r.readline(), timeout) or None
        except asyncio.TimeoutError:
            return None

    async def cerrar(self):
        self.w.close()
        try:
            await self.w.wait_closed()
        except Exception:  # noqa: BLE001
            pass


class Base(unittest.IsolatedAsyncioTestCase):
    srv_args = {}

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        pki.crear_ca(self.dir)
        pki.emitir_servidor(self.dir, ["127.0.0.1"], [])
        self.reg = Registro(self.dir / "usuarios.json")
        self.claves = {}
        for tipo, n, extra in (("modulo", "riego", {}), ("modulo", "luces", {}), ("modulo", "portero", {"camara": True}),
                               ("persona", "pablo", {"rol": "completo"}),
                               ("persona", "jarvis", {"rol": "basico", "cmd": ["riego"]}),
                               ("servicio", "push", {})):
            u, c = self.reg.alta(tipo, n, **extra)
            self.claves[u] = c
        self.srv = Servidor(self.reg, contexto_tls(self.dir), intervalo_vigilante=0.1, **self.srv_args)
        self.port = await self.srv.iniciar("127.0.0.1", 0)
        self.abiertos = []

    async def asyncTearDown(self):
        for c in self.abiertos:
            await c.cerrar()
        await self.srv.detener()
        self.tmp.cleanup()

    async def conectar(self, usuario, clave=None, esperar_ok=True):
        ctx = ssl.create_default_context(cafile=self.dir / "ca.crt")
        r, w = await asyncio.open_connection("127.0.0.1", self.port, ssl=ctx)
        c = Cliente(r, w)
        self.abiertos.append(c)
        await c.enviar(t="hola", usuario=usuario, clave=clave if clave is not None else self.claves.get(usuario, "x"))
        if esperar_ok:
            resp = await c.recibir()
            self.assertEqual(resp["t"], "ok", resp)
        return c


class TLS(Base):
    async def test_certificado_valido_desde_1969(self):
        cert = x509.load_pem_x509_certificate((self.dir / "servidor.crt").read_bytes())
        self.assertLess(cert.not_valid_before_utc, datetime(1970, 1, 1, tzinfo=cert.not_valid_before_utc.tzinfo))
        ca = x509.load_pem_x509_certificate((self.dir / "ca.crt").read_bytes())
        self.assertLess(ca.not_valid_before_utc, datetime(1970, 1, 1, tzinfo=ca.not_valid_before_utc.tzinfo))

    async def test_sin_la_ca_no_conecta(self):
        with self.assertRaises(ssl.SSLError):
            await asyncio.open_connection("127.0.0.1", self.port, ssl=ssl.create_default_context())

    async def test_texto_plano_no_funciona(self):
        r, w = await asyncio.open_connection("127.0.0.1", self.port)
        w.write(b'{"v":1,"t":"hola","usuario":"m-riego","clave":"x"}\n')
        await w.drain()
        data = await asyncio.wait_for(r.read(100), 2)
        self.assertNotIn(b'"ok"', data)
        w.close()


class Auth(Base):
    async def test_errores_de_auth_indistinguibles(self):
        self.reg.bloquear("p-pablo")
        respuestas = []
        for usuario, clave in (("m-riego", "mala"), ("m-noexiste", "x"), ("p-pablo", self.claves["p-pablo"])):
            c = await self.conectar(usuario, clave, esperar_ok=False)
            respuestas.append(await c.recibir())
            self.assertIsNone(await c.recibir())   # y se corta
        self.assertEqual(respuestas[0], respuestas[1])
        self.assertEqual(respuestas[1], respuestas[2])
        self.assertEqual(respuestas[0]["codigo"], "auth")

    async def test_primer_mensaje_debe_ser_hola(self):
        ctx = ssl.create_default_context(cafile=self.dir / "ca.crt")
        r, w = await asyncio.open_connection("127.0.0.1", self.port, ssl=ctx)
        c = Cliente(r, w); self.abiertos.append(c)
        await c.enviar(t="pub", topic="casa/riego/estado", valor=1)
        self.assertEqual((await c.recibir())["codigo"], "formato")

    async def test_json_invalido_y_version(self):
        c = await self.conectar("m-riego")
        c.w.write(b"esto no es json\n"); await c.w.drain()
        self.assertEqual((await c.recibir())["codigo"], "formato")
        self.assertIsNone(await c.recibir())
        ctx = ssl.create_default_context(cafile=self.dir / "ca.crt")
        r, w = await asyncio.open_connection("127.0.0.1", self.port, ssl=ctx)
        c2 = Cliente(r, w); self.abiertos.append(c2)
        w.write(b'{"v":2,"t":"hola"}\n'); await w.drain()
        self.assertEqual((await c2.recibir())["codigo"], "version")

    async def test_bloqueo_en_caliente(self):
        c = await self.conectar("p-pablo")
        self.reg.bloquear("p-pablo")
        self.assertIsNone(await c.recibir(timeout=2))   # el servidor lo corta
        c2 = await self.conectar("p-pablo", esperar_ok=False)
        self.assertEqual((await c2.recibir())["codigo"], "auth")

    async def test_cambio_de_clave_corta_sesion_vieja(self):
        c = await self.conectar("p-pablo")
        self.reg.cambiar_clave("p-pablo")
        self.assertIsNone(await c.recibir(timeout=2))


class Mensajes(Base):
    async def test_modulo_no_escribe_fuera_de_lo_suyo(self):
        m = await self.conectar("m-riego")
        for topic in ("casa/luces/estado", "casa/riego/online", "casa/riego/cmd", "casa/riego/foto"):
            await m.enviar(t="pub", topic=topic, valor=1)
            r = await m.recibir()
            self.assertEqual((r["codigo"], r["topic"]), ("permiso", topic))
        await m.enviar(t="ping")
        self.assertEqual((await m.recibir())["t"], "pong")   # sigue conectado

    async def test_estado_retenido_y_ts_del_servidor(self):
        m = await self.conectar("m-riego")
        await m.enviar(t="pub", topic="casa/riego/estado", valor={"humedad": 40}, ts=0)
        await asyncio.sleep(0.1)
        p = await self.conectar("p-pablo")
        await p.enviar(t="sub", topics=["casa/+/estado"])
        msg = await p.recibir()
        self.assertEqual((msg["topic"], msg["valor"], msg["retenido"]), ("casa/riego/estado", {"humedad": 40}, True))
        self.assertAlmostEqual(msg["ts"], time.time(), delta=5)   # ignora el ts=0 del módulo

    async def test_online_y_offline(self):
        p = await self.conectar("p-pablo")
        await p.enviar(t="sub", topics=["casa/+/online"])
        m = await self.conectar("m-riego")
        self.assertTrue((await p.recibir())["valor"])
        await m.cerrar()
        self.assertFalse((await p.recibir())["valor"])
        p2 = await self.conectar("p-jarvis")                  # el retenido refleja que sigue offline
        await p2.enviar(t="sub", topics=["casa/+/online"])
        msg = await p2.recibir()
        self.assertEqual((msg["valor"], msg["retenido"]), (False, True))

    async def test_reconexion_reemplaza_sesion_sin_marcar_offline(self):
        p = await self.conectar("p-pablo")
        await p.enviar(t="sub", topics=["casa/+/online"])
        m1 = await self.conectar("m-riego")
        self.assertTrue((await p.recibir())["valor"])
        m2 = await self.conectar("m-riego")
        self.assertIsNone(await m1.recibir())                # la vieja se cortó
        self.assertTrue((await p.recibir())["valor"])        # online de la nueva, nunca un offline
        self.assertIsNone(await p.nada())

    async def test_basico_no_ve_medios_y_completo_si(self):
        portero = await self.conectar("m-portero")
        basico = await self.conectar("p-jarvis")
        completo = await self.conectar("p-pablo")
        for c in (basico, completo):
            await c.enviar(t="sub", topics=["casa/#"])
        await completo.recibir(); await basico.recibir()    # online retenido
        await portero.enviar(t="pub", topic="casa/portero/foto", archivo="a.jpg")
        self.assertEqual((await completo.recibir())["topic"], "casa/portero/foto")
        self.assertIsNone(await basico.nada())
        await portero.enviar(t="pub", topic="casa/portero/estado", valor="ok")
        self.assertEqual((await basico.recibir())["topic"], "casa/portero/estado")

    async def test_basico_cmd_solo_a_su_lista(self):
        j = await self.conectar("p-jarvis")
        await j.enviar(t="pub", topic="casa/luces/cmd", id="c1", accion="encender")
        self.assertEqual((await j.recibir())["codigo"], "permiso")

    async def test_servicio_no_escribe(self):
        s = await self.conectar("s-push")
        await s.enviar(t="pub", topic="casa/riego/cmd", id="c1", accion="x")
        self.assertEqual((await s.recibir())["codigo"], "permiso")

    async def test_orden_a_modulo_offline_se_descarta(self):
        j = await self.conectar("p-jarvis")
        await j.enviar(t="pub", topic="casa/riego/cmd", id="c1", accion="abrir")
        r = await j.recibir()
        self.assertEqual((r["codigo"], r["id"]), ("offline", "c1"))
        m = await self.conectar("m-riego")                   # al reconectar NO recibe la orden vieja
        self.assertIsNone(await m.nada())

    async def test_orden_y_confirmacion(self):
        m = await self.conectar("m-riego")
        j = await self.conectar("p-jarvis")
        await j.enviar(t="sub", topics=["casa/+/evento", "casa/+/estado"])
        await j.enviar(t="pub", topic="casa/riego/cmd", id="c1", accion="abrir", params={"minutos": 10})
        orden = await m.recibir()
        self.assertEqual((orden["accion"], orden["id"], orden["de"]), ("abrir", "c1", "p-jarvis"))
        await m.enviar(t="pub", topic="casa/riego/estado", valor="abierta", cmd_id="c1")
        est = await j.recibir()
        self.assertEqual(est["cmd_id"], "c1")
        await asyncio.sleep(0.5)
        self.assertEqual(self.srv.pendientes, {})

    async def test_sin_confirmacion_genera_evento(self):
        self.srv.espera_cmd = 0.2
        m = await self.conectar("m-riego")
        s = await self.conectar("s-push")
        await s.enviar(t="sub", topics=["casa/+/evento"])
        j = await self.conectar("p-jarvis")
        await j.enviar(t="pub", topic="casa/riego/cmd", id="c9", accion="abrir")
        await m.recibir()
        ev = await s.recibir()
        self.assertEqual((ev["tipo"], ev["cmd_id"]), ("cmd_sin_respuesta", "c9"))

    async def test_orden_sin_id_o_repetida(self):
        m = await self.conectar("m-riego")
        j = await self.conectar("p-jarvis")
        await j.enviar(t="pub", topic="casa/riego/cmd", accion="abrir")
        self.assertEqual((await j.recibir())["codigo"], "formato")
        await j.enviar(t="pub", topic="casa/riego/cmd", id="c1", accion="abrir")
        await j.enviar(t="pub", topic="casa/riego/cmd", id="c1", accion="abrir")
        self.assertEqual((await j.recibir())["codigo"], "formato")

    async def test_un_modulo_no_se_hace_pasar_por_orden(self):
        m = await self.conectar("m-riego")
        await m.enviar(t="pub", topic="casa/riego/estado", valor=1, **{"de": "p-pablo"})
        p = await self.conectar("p-pablo")
        await p.enviar(t="sub", topics=["casa/riego/estado"])
        self.assertNotIn("de", await p.recibir())

    async def test_topic_malformado(self):
        m = await self.conectar("m-riego")
        for topic in ("casa/riego", "casa/../x/estado", 5, "casa/riego/estado/a/b"):
            await m.enviar(t="pub", topic=topic, valor=1)
            self.assertIn((await m.recibir())["codigo"], ("formato", "permiso"))


class LimitesTamano(Base):
    async def test_linea_demasiado_larga(self):
        m = await self.conectar("m-riego")
        m.w.write(b'{"v":1,"t":"pub","topic":"casa/riego/estado","valor":"' + b"a" * 10000 + b'"}\n')
        await m.w.drain()
        self.assertEqual((await m.recibir())["codigo"], "tamano")
        self.assertIsNone(await m.recibir())


class LimitesFrecuencia(Base):
    srv_args = {"tasa": 5, "rafaga": 10, "ventana_frecuencia": 0.5}

    async def test_exceso_se_descarta_y_luego_se_corta(self):
        m = await self.conectar("m-riego")
        p = await self.conectar("p-pablo")
        await p.enviar(t="sub", topics=["casa/riego/estado"])
        for i in range(30):
            await m.enviar(t="pub", topic="casa/riego/estado", valor=i)
        recibidos = 0
        while await p.nada(0.3):
            recibidos += 1
        self.assertLess(recibidos, 30)                       # se descartó parte
        for i in range(200):                                 # y si insiste, lo cortan
            try:
                await m.enviar(t="pub", topic="casa/riego/estado", valor=i)
                await asyncio.sleep(0.01)
            except ConnectionError:
                break
        ultimo = None
        while True:
            msg = await m.recibir(timeout=2)
            if msg is None:
                break
            ultimo = msg
        self.assertEqual(ultimo["codigo"], "frecuencia")


class Keepalive(Base):
    srv_args = {"keepalive": 0.2}

    async def test_sin_trafico_se_marca_offline(self):
        p = await self.conectar("p-pablo")
        await p.enviar(t="sub", topics=["casa/+/online"])

        async def pinguear():                                 # el observador sí mantiene viva su conexión
            while True:
                await p.enviar(t="ping")
                await asyncio.sleep(0.1)
        tarea = asyncio.create_task(pinguear())
        await self.conectar("m-riego")                        # este no manda ping
        try:
            vistos = []
            while len(vistos) < 2:
                msg = await p.recibir(timeout=2)
                if msg["t"] == "msg":
                    vistos.append(msg["valor"])
            self.assertEqual(vistos, [True, False])
        finally:
            tarea.cancel()


if __name__ == "__main__":
    unittest.main()
