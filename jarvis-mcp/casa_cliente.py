"""Cliente de Jarvis hacia casa-servidor (protocolo v1, ver docs/protocolo.md). Solo biblioteca estándar.

Mantiene una conexión TLS, guarda el último estado/online de cada módulo y los eventos recientes.
Jarvis entra con un usuario `persona basico` de canal `agente` y lista --cmd explícita: la política de la
casa (portón nunca, calefón con confirmación, topes) la aplica el servidor, no este cliente.
"""
import asyncio
import json
import secrets
import ssl
import time
from collections import deque


class CasaCliente:
    def __init__(self, host, port, ca, usuario, clave, on_aviso=None, eventos_a_avisar=("cmd_sin_respuesta",)):
        self.host, self.port, self.usuario, self.clave = host, port, usuario, clave
        self.ctx = ssl.create_default_context(cafile=ca)
        self.on_aviso = on_aviso                      # async callable(texto): solo estado, nunca datos crudos
        self.eventos_a_avisar = set(eventos_a_avisar)
        self.estados: dict[str, dict] = {}
        self.descripciones: dict[str, dict] = {}
        self.online: dict[str, bool] = {}
        self.eventos: deque = deque(maxlen=200)
        self.conectado = False
        self._w = None
        self._esperas: dict[str, dict] = {}           # id de orden -> {fut, topic, pendiente}

    # ------------------------------------------------------------ conexión
    async def correr(self):
        espera = 1
        while True:
            try:
                await self._sesion()
                espera = 1
            except PermissionError:
                raise
            except (OSError, ConnectionError, asyncio.IncompleteReadError, ssl.SSLError, ValueError):
                pass
            self.conectado = False
            self._fallar_esperas("sin_conexion", "se perdió la conexión con casa-servidor")
            await asyncio.sleep(espera)
            espera = min(espera * 2, 60)

    async def _enviar(self, **m):
        self._w.write((json.dumps({"v": 1, **m}) + "\n").encode())
        await self._w.drain()

    async def _sesion(self):
        r, w = await asyncio.open_connection(self.host, self.port, ssl=self.ctx)
        self._w = w
        try:
            await self._enviar(t="hola", usuario=self.usuario, clave=self.clave)
            ok = json.loads(await asyncio.wait_for(r.readline(), 10) or b"{}")
            if ok.get("t") != "ok":
                raise PermissionError(f"casa-servidor rechazó a {self.usuario}: {ok.get('codigo')}")
            await self._enviar(t="sub", topics=[f"casa/+/{k}" for k in ("desc", "estado", "evento", "online")])
            self.conectado = True
            ping = asyncio.create_task(self._ping(ok.get("keepalive", 30) / 2))
            try:
                while True:
                    linea = await r.readline()
                    if not linea:
                        raise ConnectionError("cerrada")
                    await self._mensaje(json.loads(linea))
            finally:
                ping.cancel()
        finally:
            w.close()

    async def _ping(self, cada):
        while True:
            await asyncio.sleep(cada)
            await self._enviar(t="ping")

    # ------------------------------------------------------------ mensajes
    async def _mensaje(self, m: dict):
        t = m.get("t")
        if t == "msg":
            _, modulo, *resto = m["topic"].split("/")
            k = "/".join(resto)
            datos = {x: v for x, v in m.items() if x not in ("v", "t", "topic", "retenido")}
            if k == "online":
                antes = self.online.get(modulo)
                self.online[modulo] = bool(m.get("valor"))
                if not m.get("retenido") and antes != self.online[modulo]:
                    await self._avisar(f"🏠 {modulo} {'volvió a conectarse' if self.online[modulo] else 'se desconectó'}")
            elif k == "desc":
                self.descripciones[modulo] = datos
            elif k == "estado":
                self.estados[modulo] = datos
                e = self._esperas.get(m.get("cmd_id"))
                if e and not e["fut"].done():
                    e["fut"].set_result({"ok": True, "estado": "cumplida", "valor": m.get("valor")})
            elif k == "evento":
                self.eventos.append({"modulo": modulo, **datos})
                if m.get("tipo") in self.eventos_a_avisar and not m.get("retenido"):
                    await self._avisar(f"🏠 {modulo}: {m.get('tipo')}")
                e = self._esperas.get(m.get("cmd_id"))
                if m.get("tipo") == "cmd_sin_respuesta" and e and not e["fut"].done():
                    e["fut"].set_result({"ok": False, "error": "sin_respuesta", "detalle": "el módulo no confirmó la orden"})
        elif t == "pendiente":
            e = self._esperas.get(m.get("id"))
            if e:
                e["pendiente"] = True                 # esperando que una persona confirme en la app/Telegram
        elif t == "error":
            e = self._esperas.get(m.get("id"))
            if not e and m.get("topic"):              # algunos errores (permiso) traen topic en vez de id
                e = next((x for x in self._esperas.values() if x["topic"] == m["topic"] and not x["fut"].done()), None)
            if e and not e["fut"].done():
                e["fut"].set_result({"ok": False, "error": m.get("codigo"), "detalle": m.get("detalle")})

    def _fallar_esperas(self, codigo, detalle):
        for e in self._esperas.values():
            if not e["fut"].done():
                e["fut"].set_result({"ok": False, "error": codigo, "detalle": detalle})

    async def _avisar(self, texto):
        if self.on_aviso:
            try:
                await self.on_aviso(texto)
            except Exception:  # noqa: BLE001  (un aviso fallido no debe tumbar la conexión)
                pass

    # ------------------------------------------------------------ API para las herramientas
    def ver_estado(self, modulo=None):
        def uno(m):
            e = self.estados.get(m, {})
            return {"modulo": m, "online": self.online.get(m), "valor": e.get("valor"), "ts": e.get("ts"),
                    "acciones": self.descripciones.get(m, {}).get("acciones")}
        if modulo:
            return uno(modulo) if modulo in self.online or modulo in self.estados else None
        return [uno(m) for m in sorted(set(self.online) | set(self.estados))]

    async def ordenar(self, modulo: str, accion: str, params: dict | None = None, espera: float = 10) -> dict:
        if not self.conectado:
            return {"ok": False, "error": "sin_conexion", "detalle": "Jarvis no está conectado a casa-servidor"}
        oid = "j-" + secrets.token_hex(6)
        e = {"fut": asyncio.get_running_loop().create_future(), "topic": f"casa/{modulo}/cmd", "pendiente": False}
        self._esperas[oid] = e
        try:
            msg = {"t": "pub", "topic": e["topic"], "id": oid, "accion": accion}
            if params:
                msg["params"] = params
            await self._enviar(**msg)
            try:
                res = await asyncio.wait_for(asyncio.shield(e["fut"]), espera)
            except asyncio.TimeoutError:
                res = {"ok": None, "estado": "esperando_confirmacion_de_una_persona" if e["pendiente"] else "enviada_sin_confirmar",
                       "detalle": "la orden sigue en curso; consultá casa_estado en unos segundos"}
            return {**res, "orden": oid}
        finally:
            if e["fut"].done():
                self._esperas.pop(oid, None)
            else:                                     # sigue pendiente: se limpia más tarde para no perder la respuesta tardía
                asyncio.get_running_loop().call_later(120, self._esperas.pop, oid, None)
