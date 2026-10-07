"""Simulador de módulo: se comporta como un ESP32 de riego/luces/tanque, para probar sin hardware.

  python simulador_modulo.py --ca datos/ca.crt --host 192.168.1.50 --usuario m-riego --clave XXXX --tipo riego

Cumple el contrato: hola, desc al conectar, estado con cmd_id al cumplir una orden, estado sin cmd_id si algo
cambia "a mano" (escribí 'mano' en la consola), ping de keepalive y reconexión con espera creciente.
"""
import argparse
import asyncio
import json
import random
import ssl


class Modulo:
    TIPOS = {
        "riego": {"acciones": ["abrir", "cerrar"], "estado": {"valvula": "cerrada"}},
        "luces": {"acciones": ["encender", "apagar"], "estado": {"luz": "apagada"}},
        "tanque": {"acciones": [], "estado": {"nivel": 50}},
    }

    def __init__(self, usuario: str, tipo: str, rapido: bool = False):
        self.usuario, self.tipo = usuario, tipo
        self.modulo = usuario.split("-", 1)[1]
        self.estado = dict(self.TIPOS[tipo]["estado"])
        self.topic = lambda k: f"casa/{self.modulo}/{k}"
        self.ordenes_cumplidas = []
        self._w = None

    async def _enviar(self, **m):
        self._w.write((json.dumps({"v": 1, **m}) + "\n").encode())
        await self._w.drain()

    def aplicar(self, accion: str):
        if self.tipo == "riego":
            self.estado["valvula"] = "abierta" if accion == "abrir" else "cerrada"
        elif self.tipo == "luces":
            self.estado["luz"] = "encendida" if accion == "encender" else "apagada"

    async def publicar_estado(self, cmd_id: str | None = None):
        extra = {"cmd_id": cmd_id} if cmd_id else {}     # sin cmd_id = cambio manual o espontáneo
        await self._enviar(t="pub", topic=self.topic("estado"), valor=dict(self.estado), **extra)

    async def sesion(self, host, port, ctx, clave, keepalive_max=None):
        r, w = await asyncio.open_connection(host, port, ssl=ctx)
        self._w = w
        try:
            await self._enviar(t="hola", usuario=self.usuario, clave=clave)
            ok = json.loads(await r.readline())
            if ok.get("t") != "ok":
                raise PermissionError(f"el servidor rechazó la conexión: {ok}")
            keepalive = min(ok["keepalive"], keepalive_max or 1e9)
            await self._enviar(t="pub", topic=self.topic("desc"), nombre=self.modulo, tipo=self.tipo,
                               acciones=self.TIPOS[self.tipo]["acciones"])
            await self.publicar_estado()
            ping = asyncio.create_task(self._ping(keepalive / 2))
            try:
                while True:
                    linea = await r.readline()
                    if not linea:
                        raise ConnectionError("el servidor cerró la conexión")
                    msg = json.loads(linea)
                    if msg.get("t") == "msg" and msg.get("topic") == self.topic("cmd"):
                        await self.cumplir(msg)
            finally:
                ping.cancel()
        finally:
            w.close()

    async def cumplir(self, orden: dict):
        accion = orden.get("accion")
        if accion not in self.TIPOS[self.tipo]["acciones"]:
            return                                        # acción desconocida: no confirma (el servidor avisa por timeout)
        self.aplicar(accion)
        self.ordenes_cumplidas.append(orden["id"])
        await self.publicar_estado(cmd_id=orden["id"])

    async def _ping(self, cada):
        while True:
            await asyncio.sleep(cada)
            await self._enviar(t="ping")

    async def correr(self, host, port, ctx, clave, **kw):
        espera = 1
        while True:                                       # reconexión con espera creciente (1, 2, 4... hasta 60 s)
            try:
                await self.sesion(host, port, ctx, clave, **kw)
            except PermissionError:
                raise
            except (OSError, ConnectionError, asyncio.IncompleteReadError, ssl.SSLError, ValueError) as e:
                print(f"[{self.usuario}] desconectado ({e}); reintento en {espera}s", flush=True)
            await asyncio.sleep(espera + random.random() * 0.3)
            espera = min(espera * 2, 60)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ca", required=True)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--puerto", type=int, default=8883)
    ap.add_argument("--usuario", required=True)
    ap.add_argument("--clave", required=True)
    ap.add_argument("--tipo", choices=list(Modulo.TIPOS), default="riego")
    a = ap.parse_args()
    ctx = ssl.create_default_context(cafile=a.ca)
    await Modulo(a.usuario, a.tipo).correr(a.host, a.puerto, ctx, a.clave)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
