import asyncio
import ssl
import unittest

from simulador_modulo import Modulo
from tests.test_servidor import Base


class ConSimulador(Base):
    async def arrancar(self, usuario="m-riego", tipo="riego"):
        ctx = ssl.create_default_context(cafile=self.dir / "ca.crt")
        mod = Modulo(usuario, tipo)
        tarea = asyncio.create_task(mod.correr("127.0.0.1", self.port, ctx, self.claves[usuario]))
        self.addAsyncCleanup(self._parar, tarea)
        return mod

    async def _parar(self, tarea):
        tarea.cancel()
        await asyncio.gather(tarea, return_exceptions=True)

    async def test_orden_de_punta_a_punta(self):
        mod = await self.arrancar()
        j = await self.conectar("p-pablo")
        await j.enviar(t="sub", topics=["casa/riego/estado", "casa/riego/online"])
        vistos = []
        while not any(m.get("valor") == {"valvula": "cerrada"} for m in vistos):
            vistos.append(await j.recibir())                  # online + estado inicial retenido/nuevo
        await j.enviar(t="pub", topic="casa/riego/cmd", id="o1", accion="abrir")
        while True:
            m = await j.recibir()
            if m.get("cmd_id") == "o1":
                break
        self.assertEqual(m["valor"], {"valvula": "abierta"})
        self.assertEqual(mod.ordenes_cumplidas, ["o1"])
        await asyncio.sleep(0.1)
        self.assertEqual(self.srv.pendientes, {})            # confirmada: no queda pendiente

    async def test_cambio_manual_llega_sin_cmd_id(self):
        mod = await self.arrancar("m-luces", "luces")
        j = await self.conectar("p-pablo")
        await j.enviar(t="sub", topics=["casa/luces/estado"])
        await j.recibir()
        mod.estado["luz"] = "encendida"                       # alguien la prendió a mano
        await mod.publicar_estado()
        m = await j.recibir()
        self.assertNotIn("cmd_id", m)
        self.assertEqual(m["valor"], {"luz": "encendida"})

    async def test_accion_desconocida_termina_en_timeout(self):
        self.srv.espera_cmd = 0.3
        await self.arrancar()
        s = await self.conectar("s-push")
        await s.enviar(t="sub", topics=["casa/riego/evento"])
        j = await self.conectar("p-pablo")
        await asyncio.sleep(0.3)
        await j.enviar(t="pub", topic="casa/riego/cmd", id="o2", accion="volar")
        ev = await s.recibir()
        self.assertEqual((ev["tipo"], ev["cmd_id"]), ("cmd_sin_respuesta", "o2"))

    async def test_reconecta_sola_y_republica_estado(self):
        mod = await self.arrancar()
        j = await self.conectar("p-pablo")
        await j.enviar(t="sub", topics=["casa/riego/online"])
        for esperado in (True,):
            m = await j.recibir(); self.assertEqual(m["valor"], esperado)
        mod._w.close()                                        # se corta el WiFi
        estados = []
        while len(estados) < 2:
            m = await j.recibir(timeout=6)
            estados.append(m["valor"])
        self.assertEqual(estados, [False, True])              # cayó y volvió sin intervención


if __name__ == "__main__":
    unittest.main()
