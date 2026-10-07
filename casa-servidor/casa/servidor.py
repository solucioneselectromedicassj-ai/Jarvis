"""Servidor de mensajes de la casa: TLS + una línea JSON por mensaje. Ver docs/protocolo.md."""
import asyncio
import json
import re
import ssl
import time
from pathlib import Path

from . import medios, ws
from .politica import CANALES_PERSONA, Politica
from .registro import Registro, coincide, fuente_de_audio, hash_clave, permisos, permitido, verificar_clave

LINEA_MAX = 4096
ESPERA_HOLA = 5
KEEPALIVE = 30
TASA, RAFAGA = 20, 40
VENTANA_FRECUENCIA = 10
ESPERA_CMD = 30
MAX_SUBS = 100
ESPERA_CONFIRMACION = 60
BUFFER_MAX = 1_000_000
TOPIC_RE = re.compile(r"^casa/[a-z0-9_-]{1,32}/[a-z]+(/[a-z]+)?$")
DUMMY_HASH = hash_clave("x")  # para tardar lo mismo con usuarios que no existen


class Cerrar(Exception):
    """Corta la conexión (el error ya se informó al cliente)."""


class Conn:
    def __init__(self, writer, usuario, info, rafaga):
        self.writer, self.usuario, self.info = writer, usuario, info
        self.perm = permisos(info)
        self.subs: set[str] = set()
        self.tokens, self.t = float(rafaga), time.monotonic()
        self.drop_desde = None
        self.descartados = 0

    @property
    def canal(self):
        """Canal desde el que opera (lo fija el alta del usuario, no el cliente)."""
        return self.info.get("canal", "app") if self.info["tipo"] == "persona" else self.info["tipo"]

    @property
    def modulo(self):
        return self.info.get("modulo") if self.info["tipo"] == "modulo" else None


class Servidor:
    def __init__(self, registro: Registro, ctx: ssl.SSLContext, keepalive=KEEPALIVE, espera_cmd=ESPERA_CMD,
                 tasa=TASA, rafaga=RAFAGA, linea_max=LINEA_MAX, ventana_frecuencia=VENTANA_FRECUENCIA,
                 intervalo_vigilante=2.0, app_dir: Path | None = None, media_dir: Path | None = None, cuota_mb=200,
                 retencion_h=24, politica: Politica | None = None, espera_confirmacion=ESPERA_CONFIRMACION):
        self.registro, self.ctx = registro, ctx
        self.keepalive, self.espera_cmd, self.tasa, self.rafaga = keepalive, espera_cmd, tasa, rafaga
        self.linea_max, self.ventana, self.intervalo = linea_max, ventana_frecuencia, intervalo_vigilante
        self.estaticos = cargar_app(app_dir) if app_dir else None
        self.media_dir, self.cuota_bytes, self.retencion_s = media_dir, cuota_mb * 1_000_000, retencion_h * 3600
        self.subidas_activas = 0
        self.politica = politica or Politica()
        self.espera_confirmacion = espera_confirmacion
        self.confirmaciones: dict[str, dict] = {}
        self.conns: dict[str, Conn] = {}
        self.retenido: dict[str, dict] = {}
        self.pendientes: dict[str, asyncio.TimerHandle] = {}
        self._srv = None
        self._vig = None

    # ------------------------------------------------------------ ciclo de vida
    async def iniciar(self, host: str, port: int, ws_port: int | None = None, origenes: list[str] | None = None):
        """ws_port: puerto WebSocket para la app (None = apagado, 0 = el sistema elige). Devuelve el puerto TLS."""
        self._srv = await asyncio.start_server(self._cliente, host, port, ssl=self.ctx, limit=self.linea_max)
        self._origenes = origenes
        self._ws_srv, self.ws_port = None, None
        if ws_port is not None:
            self._ws_srv = await asyncio.start_server(self._cliente_ws, host, ws_port, ssl=self.ctx, limit=ws.HEADERS_MAX)
            self.ws_port = self._ws_srv.sockets[0].getsockname()[1]
        self._vig = asyncio.create_task(self._vigilar())
        return self._srv.sockets[0].getsockname()[1]

    async def detener(self):
        if self._vig:
            self._vig.cancel()
        for h in list(self.pendientes.values()) + [x["timer"] for x in self.confirmaciones.values()]:
            h.cancel()
        for c in list(self.conns.values()):
            c.writer.close()
        for srv in (self._srv, self._ws_srv):
            if srv:
                srv.close()
                await srv.wait_closed()

    async def _vigilar(self):
        """Corta a quien se bloqueó, se dio de baja o cambió de clave mientras estaba conectado."""
        while True:
            await asyncio.sleep(self.intervalo)
            try:
                usuarios = self.registro.cargar()["usuarios"]
            except (OSError, ValueError):
                continue
            for c in list(self.conns.values()):
                u = usuarios.get(c.usuario)
                if not u or u.get("bloqueado") or u.get("hash") != c.info.get("hash"):
                    c.writer.close()

    # ------------------------------------------------------------ conexión
    async def _cliente(self, reader, writer):
        conn = None
        try:
            conn = await self._hola(reader, writer)
            if conn:
                await self._bucle(conn, reader)
        except (Cerrar, ConnectionError, asyncio.IncompleteReadError, asyncio.TimeoutError, ssl.SSLError, OSError):
            pass
        finally:
            if conn:
                self._desconectar(conn)
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass

    async def _cliente_ws(self, reader, writer):
        try:
            par = await ws.aceptar(reader, writer, self.linea_max, self._origenes, self.estaticos, self._http_medios)
        except (ConnectionError, ssl.SSLError, OSError):
            writer.close()
            return
        if par:
            await self._cliente(*par)

    async def _http_medios(self, metodo, ruta, h, reader, writer):
        await medios.subir(self, metodo, ruta, h, reader, writer)

    async def autenticar_audio(self, usuario: str, clave: str):
        """Fuente del audio (str), None si usuario/clave no valen, False si no tiene permiso de subir audio."""
        info = self.registro.cargar()["usuarios"].get(usuario)
        ok = await asyncio.to_thread(verificar_clave, clave, info["hash"] if info else DUMMY_HASH)
        if not (info and ok and not info.get("bloqueado")):
            return None
        return fuente_de_audio(usuario, info) or False

    def publicar_audio(self, fuente: str, nombre: str, bytes_: int, segundos: float):
        self._publicar(f"casa/{fuente}/evento", {"tipo": "audio", "datos": {"id": nombre, "archivo": f"{fuente}/{nombre}.wav",
                                                                            "bytes": bytes_, "segundos": segundos}})

    def _escribir(self, writer, obj):
        writer.write((json.dumps(obj, separators=(",", ":"), ensure_ascii=False) + "\n").encode())

    def _error(self, writer, codigo, cerrar=False, **extra):
        self._escribir(writer, {"v": 1, "t": "error", "codigo": codigo, **extra})
        if cerrar:
            raise Cerrar(codigo)

    async def _leer(self, reader, writer, timeout) -> dict:
        try:
            linea = await asyncio.wait_for(reader.readline(), timeout)
        except ValueError:
            self._error(writer, "tamano", cerrar=True)
        if not linea:
            raise ConnectionError("cerrada")
        try:
            msg = json.loads(linea)
        except ValueError:
            self._error(writer, "formato", cerrar=True)
        if not isinstance(msg, dict) or not isinstance(msg.get("t"), str):
            self._error(writer, "formato", cerrar=True)
        if msg.get("v") != 1:
            self._error(writer, "version", cerrar=True)
        return msg

    async def _hola(self, reader, writer):
        msg = await self._leer(reader, writer, ESPERA_HOLA)
        if msg["t"] != "hola":
            self._error(writer, "formato", cerrar=True)
        usuario, clave = msg.get("usuario"), msg.get("clave")
        info = self.registro.cargar()["usuarios"].get(usuario) if isinstance(usuario, str) else None
        if not isinstance(clave, str):
            clave = ""
        ok = await asyncio.to_thread(verificar_clave, clave, info["hash"] if info else DUMMY_HASH)
        if not (info and ok and not info.get("bloqueado")):
            self._error(writer, "auth", cerrar=True)  # igual para clave mala, usuario inexistente o bloqueado
        viejo = self.conns.get(usuario)
        conn = Conn(writer, usuario, info, self.rafaga)
        self.conns[usuario] = conn
        if viejo:
            viejo.writer.close()  # una sola sesión por usuario; no publica offline porque ya no es la vigente
        if conn.modulo:
            m = conn.modulo
            conn.subs |= {f"casa/{m}/cmd", f"casa/{m}/audio/in"}
        self._escribir(writer, {"v": 1, "t": "ok", "keepalive": self.keepalive})
        if conn.modulo:
            self._publicar(f"casa/{conn.modulo}/online", {"valor": True}, retener=True)
        return conn

    def _desconectar(self, conn: Conn):
        if self.conns.get(conn.usuario) is conn:
            del self.conns[conn.usuario]
            if conn.modulo:
                self._publicar(f"casa/{conn.modulo}/online", {"valor": False}, retener=True)

    # ------------------------------------------------------------ mensajes
    async def _bucle(self, conn: Conn, reader):
        w = conn.writer
        while True:
            msg = await self._leer(reader, w, 2 * self.keepalive)
            if not self._tasa(conn):
                continue
            t = msg["t"]
            if t == "ping":
                self._escribir(w, {"v": 1, "t": "pong"})
            elif t == "sub":
                self._sub(conn, msg)
            elif t == "pub":
                self._pub(conn, msg)
            elif t in ("confirmo", "rechazo"):
                self._confirmar(conn, msg, t == "confirmo")
            else:
                self._error(w, "formato", cerrar=True)
            if w.transport.get_write_buffer_size() > BUFFER_MAX:
                raise Cerrar("cliente lento")

    def _tasa(self, conn: Conn) -> bool:
        ahora = time.monotonic()
        conn.tokens = min(self.rafaga, conn.tokens + (ahora - conn.t) * self.tasa)
        conn.t = ahora
        if conn.tokens >= 1:
            conn.tokens -= 1
            if conn.tokens >= self.rafaga / 2:
                conn.drop_desde = None
            return True
        conn.descartados += 1
        if conn.drop_desde is None:
            conn.drop_desde = ahora
        elif ahora - conn.drop_desde > self.ventana:
            self._error(conn.writer, "frecuencia", cerrar=True)
        return False

    def _sub(self, conn: Conn, msg: dict):
        topics = msg.get("topics")
        if not isinstance(topics, list) or not all(isinstance(x, str) for x in topics):
            return self._error(conn.writer, "formato")
        if len(conn.subs) + len(topics) > MAX_SUBS:
            return self._error(conn.writer, "formato", detalle="demasiadas suscripciones")
        conn.subs |= set(topics)
        for topic, entry in list(self.retenido.items()):
            if permitido(conn.perm["leer"], topic) and any(coincide(p, topic) for p in topics):
                self._escribir(conn.writer, {**entry, "retenido": True})

    def _pub(self, conn: Conn, msg: dict):
        topic = msg.get("topic")
        if not isinstance(topic, str) or not TOPIC_RE.match(topic):
            return self._error(conn.writer, "formato", detalle="topic inválido")
        if not permitido(conn.perm["escribir"], topic):
            return self._error(conn.writer, "permiso", topic=topic)
        payload = {k: v for k, v in msg.items() if k not in ("v", "t", "topic", "ts", "retenido", "de", "origen", "confirmo")}
        _, modulo, *resto = topic.split("/")
        sub = "/".join(resto)
        if sub == "cmd":
            return self._orden(conn, modulo, topic, payload)
        if sub == "estado" and payload.get("cmd_id") in self.pendientes:
            self.pendientes.pop(payload["cmd_id"]).cancel()
        self._publicar(topic, payload, retener=sub in ("desc", "estado"))

    def _orden(self, conn: Conn, modulo: str, topic: str, payload: dict):
        oid = payload.get("id")
        if not isinstance(oid, str) or not 0 < len(oid) <= 64:
            return self._error(conn.writer, "formato", detalle="falta id de la orden")
        if oid in self.pendientes:
            return self._error(conn.writer, "formato", detalle="id repetido", id=oid)
        if f"m-{modulo}" not in self.conns:
            return self._error(conn.writer, "offline", id=oid)  # no se encolan órdenes: nada arranca solo al reconectar
        veredicto, resultado = self.politica.evaluar(modulo, payload.get("accion"), payload.get("params"), conn.canal)
        if veredicto in ("prohibido", "tope"):
            return self._error(conn.writer, "politica", detalle=resultado, id=oid)
        if veredicto == "confirmar":
            return self._pedir_confirmacion(conn, modulo, topic, oid, payload, resultado)
        if resultado:
            payload["params"] = resultado
        self._entregar_orden(conn, modulo, topic, oid, payload)

    def _entregar_orden(self, conn: Conn, modulo: str, topic: str, oid: str, payload: dict, confirmo: str | None = None):
        self.pendientes[oid] = asyncio.get_running_loop().call_later(self.espera_cmd, self._sin_respuesta, oid, modulo)
        sello = {"de": conn.usuario, "origen": conn.canal}   # sello puesto por el servidor, no por el cliente
        if confirmo:
            sello["confirmo"] = confirmo
        self._publicar(topic, {**payload, **sello})

    # ---- confirmación: solo la acepta una sesión de persona (app/telegram), nunca el agente ni la voz
    def _quien_confirma(self, conn: Conn, topic: str) -> bool:
        return conn.info["tipo"] == "persona" and conn.canal in CANALES_PERSONA and permitido(conn.perm["escribir"], topic)

    def _pedir_confirmacion(self, conn, modulo, topic, oid, payload, params):
        if params:
            payload["params"] = params
        destinos = [c for c in self.conns.values() if self._quien_confirma(c, topic)]
        if not destinos:
            return self._error(conn.writer, "politica", detalle="requiere confirmación y no hay ninguna app conectada", id=oid)
        timer = asyncio.get_running_loop().call_later(self.espera_confirmacion, self._vence_confirmacion, oid)
        self.confirmaciones[oid] = {"conn": conn, "modulo": modulo, "topic": topic, "payload": payload, "timer": timer}
        aviso = {"v": 1, "t": "confirmar", "id": oid, "topic": topic, "accion": payload.get("accion"),
                 "params": payload.get("params"), "de": conn.usuario, "origen": conn.canal}
        for c in destinos:
            self._escribir(c.writer, aviso)
        self._escribir(conn.writer, {"v": 1, "t": "pendiente", "id": oid, "detalle": "esperando confirmación"})

    def _vence_confirmacion(self, oid):
        c = self.confirmaciones.pop(oid, None)
        if c:
            self._escribir(c["conn"].writer, {"v": 1, "t": "error", "codigo": "politica", "detalle": "confirmación vencida", "id": oid})

    def _confirmar(self, conn: Conn, msg: dict, acepta: bool):
        oid = msg.get("id")
        c = self.confirmaciones.get(oid) if isinstance(oid, str) else None
        if not c:
            return self._error(conn.writer, "formato", detalle="no hay nada para confirmar con ese id")
        if not self._quien_confirma(conn, c["topic"]):
            return self._error(conn.writer, "permiso", detalle="este canal no puede confirmar", id=oid)
        del self.confirmaciones[oid]
        c["timer"].cancel()
        if not acepta:
            return self._escribir(c["conn"].writer, {"v": 1, "t": "error", "codigo": "politica", "detalle": "rechazada", "id": oid})
        if f"m-{c['modulo']}" not in self.conns:
            return self._escribir(c["conn"].writer, {"v": 1, "t": "error", "codigo": "offline", "id": oid})
        self._entregar_orden(c["conn"], c["modulo"], c["topic"], oid, c["payload"], confirmo=conn.usuario)

    def _sin_respuesta(self, oid: str, modulo: str):
        self.pendientes.pop(oid, None)
        self._publicar(f"casa/{modulo}/evento", {"tipo": "cmd_sin_respuesta", "cmd_id": oid})

    def _publicar(self, topic: str, payload: dict, retener=False):
        entry = {**payload, "v": 1, "t": "msg", "topic": topic, "ts": time.time(), "retenido": False}
        if retener:
            self.retenido[topic] = entry
        for c in list(self.conns.values()):
            if permitido(c.perm["leer"], topic) and any(coincide(p, topic) for p in c.subs):
                self._escribir(c.writer, entry)


def contexto_tls(dir_: Path) -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(dir_ / "servidor.crt", dir_ / "servidor.key")
    return ctx


TIPOS = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".json": "application/json",
         ".webmanifest": "application/manifest+json", ".png": "image/png", ".svg": "image/svg+xml", ".txt": "text/plain; charset=utf-8"}


def cargar_app(dir_: Path) -> dict:
    """Archivos de la app web en memoria: {'/ruta': (bytes, tipo)}. Solo lo que hay en la carpeta, sin ocultos."""
    out = {}
    for f in sorted(Path(dir_).rglob("*")):
        rel = f.relative_to(dir_)
        if f.is_file() and not any(p.startswith(".") for p in rel.parts) and f.suffix in TIPOS:
            out["/" + rel.as_posix()] = (f.read_bytes(), TIPOS[f.suffix])
    if "/index.html" in out:
        out["/"] = out["/index.html"]
    return out
