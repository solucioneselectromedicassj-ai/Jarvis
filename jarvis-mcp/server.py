"""Jarvis MCP Server.

Servidor MCP (streamable HTTP) con:
- Autenticación por token Bearer
- Tareas definidas por Pablo en un archivo (JARVIS_TASKS); el modelo solo puede listarlas y ejecutarlas
- Historial de resultados e informes (SQLite)
- Notificaciones (Telegram / webhook) cuando algo termina o falla
- Programación simple de tareas recurrentes (scheduler interno)

Variables de entorno: ver .env.example
"""
import asyncio
import contextlib
import hmac
import json
import os
import sqlite3
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx
import uvicorn
from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from casa_cliente import CasaCliente
from cerebro import Cerebro

# ---------------------------------------------------------------- config
AUTH_TOKEN = os.environ.get("JARVIS_TOKEN", "")
DB_PATH = os.environ.get("JARVIS_DB", "jarvis.db")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
WEBHOOK_URL = os.environ.get("NOTIFY_WEBHOOK_URL", "")
# Las tareas las define Pablo en este archivo; Jarvis no puede crearlas ni modificarlas
TASKS_FILE = Path(os.environ.get("JARVIS_TASKS", "tareas.json"))
# Los scripts que una tarea puede correr viven en esta carpeta (solo por nombre, sin argumentos)
SCRIPTS_DIR = Path(os.environ.get("JARVIS_SCRIPTS", "scripts"))
TASK_TIMEOUT = int(os.environ.get("JARVIS_TASK_TIMEOUT", "60"))
# Por defecto solo escucha en la propia PC; para Tailscale/LAN poner la IP o 0.0.0.0 a propósito
HOST = os.environ.get("JARVIS_HOST", "127.0.0.1")

# Conexión a casa-servidor (opcional: sin CASA_USUARIO las herramientas casa_* avisan que no está configurado)
CASA_HOST = os.environ.get("CASA_HOST", "127.0.0.1")
CASA_PORT = int(os.environ.get("CASA_PORT", "8883"))
CASA_CA = os.environ.get("CASA_CA", "")
CASA_USUARIO = os.environ.get("CASA_USUARIO", "")
CASA_CLAVE = os.environ.get("CASA_CLAVE", "")
CASA_AVISAR_EVENTOS = tuple(x for x in os.environ.get("CASA_AVISAR_EVENTOS", "cmd_sin_respuesta").split(",") if x)
casa: Optional[CasaCliente] = None
cerebro: Optional[Cerebro] = None
# Token aparte, solo para /voz (lo usa el puente de voz y texto): no sirve para el resto de las herramientas
VOZ_TOKEN = os.environ.get("JARVIS_VOZ_TOKEN", "")

mcp = FastMCP("jarvis", stateless_http=True, json_response=True)


# ---------------------------------------------------------------- storage
def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with db() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                name TEXT PRIMARY KEY,
                kind TEXT NOT NULL,            -- http | script
                spec TEXT NOT NULL,            -- JSON con los parámetros
                description TEXT DEFAULT '',
                every_minutes INTEGER,         -- NULL = solo manual
                notify TEXT DEFAULT 'on_change', -- never | on_change | always
                last_run REAL,
                last_ok INTEGER                -- resultado de la última corrida (para avisar solo al cambiar)
            );
            CREATE TABLE IF NOT EXISTS results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task TEXT NOT NULL,
                ok INTEGER NOT NULL,
                output TEXT,
                started REAL NOT NULL,
                duration REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project TEXT NOT NULL,
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                created REAL NOT NULL
            );
            """
        )
        try:  # bases creadas antes de existir last_ok
            c.execute("ALTER TABLE tasks ADD COLUMN last_ok INTEGER")
        except sqlite3.OperationalError:
            pass


VALID_KINDS = ("http", "script")
VALID_NOTIFY = ("never", "on_change", "always")


def load_tasks() -> None:
    """Sincroniza la tabla tasks con el archivo de configuración (la fuente de verdad)."""
    tasks = {}
    if TASKS_FILE.exists():
        tasks = json.loads(TASKS_FILE.read_text(encoding="utf-8"))
    for name, t in tasks.items():
        if t.get("tipo") not in VALID_KINDS:
            raise SystemExit(f"{TASKS_FILE}: la tarea '{name}' tiene tipo inválido (http | script)")
        if t.get("notificar", "on_change") not in VALID_NOTIFY:
            raise SystemExit(f"{TASKS_FILE}: la tarea '{name}' tiene 'notificar' inválido")
    with db() as c:
        c.execute(f"DELETE FROM tasks WHERE name NOT IN ({','.join('?' * len(tasks))})", list(tasks))
        for name, t in tasks.items():
            c.execute(
                """INSERT INTO tasks(name, kind, spec, description, every_minutes, notify)
                   VALUES (?,?,?,?,?,?)
                   ON CONFLICT(name) DO UPDATE SET kind=excluded.kind, spec=excluded.spec,
                   description=excluded.description, every_minutes=excluded.every_minutes,
                   notify=excluded.notify""",
                (name, t["tipo"], json.dumps(t.get("parametros", {})), t.get("descripcion", ""),
                 t.get("cada_minutos"), t.get("notificar", "on_change")),
            )


def iso(ts: Optional[float]) -> Optional[str]:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------- notify
async def _notify(text: str) -> dict:
    sent = {}
    async with httpx.AsyncClient(timeout=15) as client:
        if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
            try:
                r = await client.post(
                    f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                    json={"chat_id": TELEGRAM_CHAT_ID, "text": text[:4000]},
                )
                sent["telegram"] = r.status_code == 200
            except Exception as e:  # noqa: BLE001
                sent["telegram"] = f"error: {e}"
        if WEBHOOK_URL:
            try:
                r = await client.post(WEBHOOK_URL, json={"text": text})
                sent["webhook"] = r.status_code < 300
            except Exception as e:  # noqa: BLE001
                sent["webhook"] = f"error: {e}"
    return sent


# ---------------------------------------------------------------- runner
async def _run_task(name: str) -> dict:
    with db() as c:
        row = c.execute("SELECT * FROM tasks WHERE name=?", (name,)).fetchone()
    if not row:
        return {"ok": False, "error": f"La tarea '{name}' no existe"}

    spec = json.loads(row["spec"])
    started = time.time()
    ok, output = False, ""
    try:
        if row["kind"] == "script":
            script = (SCRIPTS_DIR / spec["script"]).resolve()
            if script.parent != SCRIPTS_DIR.resolve() or not script.is_file():
                raise PermissionError(f"Script no permitido: {spec['script']}")
            proc = await asyncio.create_subprocess_exec(
                str(script),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            try:
                out, _ = await asyncio.wait_for(proc.communicate(), TASK_TIMEOUT)
            except asyncio.TimeoutError:
                proc.kill()
                raise TimeoutError(f"Timeout de {TASK_TIMEOUT}s")
            output = out.decode(errors="replace")
            ok = proc.returncode == 0
        elif row["kind"] == "http":
            async with httpx.AsyncClient(timeout=TASK_TIMEOUT) as client:
                r = await client.request(
                    spec.get("method", "GET"),
                    spec["url"],
                    headers=spec.get("headers"),
                    json=spec.get("json"),
                )
            output = f"HTTP {r.status_code}\n{r.text[:2000]}"
            expected = spec.get("expect_status")
            ok = (r.status_code == expected) if expected else r.status_code < 400
        else:
            raise ValueError(f"Tipo desconocido: {row['kind']}")
    except Exception as e:  # noqa: BLE001
        output = f"{type(e).__name__}: {e}"

    duration = time.time() - started
    output = output[-4000:]
    with db() as c:
        c.execute(
            "INSERT INTO results(task, ok, output, started, duration) VALUES (?,?,?,?,?)",
            (name, int(ok), output, started, duration),
        )
        c.execute("UPDATE tasks SET last_run=?, last_ok=? WHERE name=?", (started, int(ok), name))

    # Aviso solo al cambiar de estado (cayó / volvió). La salida de la tarea nunca sale a internet.
    mode = row["notify"]
    changed = row["last_ok"] is None and not ok or (row["last_ok"] is not None and bool(row["last_ok"]) != ok)
    if mode == "always" or (mode == "on_change" and changed):
        texto = f"✅ Jarvis · {name} volvió a funcionar" if ok else f"❌ Jarvis · {name} falló"
        await _notify(texto if mode == "on_change" else f"{texto} ({duration:.1f}s)")

    return {"ok": ok, "task": name, "duration_s": round(duration, 2), "output": output}


async def _scheduler() -> None:
    """Revisa cada 30s qué tareas recurrentes están vencidas."""
    while True:
        try:
            with db() as c:
                rows = c.execute(
                    "SELECT name, every_minutes, last_run FROM tasks WHERE every_minutes IS NOT NULL"
                ).fetchall()
            now = time.time()
            for r in rows:
                if r["last_run"] is None or now - r["last_run"] >= r["every_minutes"] * 60:
                    await _run_task(r["name"])
        except Exception as e:  # noqa: BLE001
            print("scheduler error:", e, flush=True)
        await asyncio.sleep(30)


# ---------------------------------------------------------------- tools
@mcp.tool()
def listar_tareas() -> list[dict]:
    """Lista las tareas definidas por Pablo con su programación y última ejecución."""
    with db() as c:
        rows = c.execute("SELECT * FROM tasks ORDER BY name").fetchall()
    return [
        {
            "nombre": r["name"],
            "tipo": r["kind"],
            "descripcion": r["description"],
            "cada_minutos": r["every_minutes"],
            "notificar": r["notify"],
            "ultima_ejecucion": iso(r["last_run"]),
        }
        for r in rows
    ]


@mcp.tool()
async def ejecutar_tarea(nombre: str) -> dict:
    """Ejecuta una tarea definida por Pablo ahora mismo y devuelve el resultado."""
    return await _run_task(nombre)


@mcp.tool()
def ver_resultados(tarea: Optional[str] = None, limite: int = 10) -> list[dict]:
    """Últimos resultados de ejecución, opcionalmente filtrados por tarea."""
    limite = max(1, min(limite, 100))
    with db() as c:
        if tarea:
            rows = c.execute(
                "SELECT * FROM results WHERE task=? ORDER BY id DESC LIMIT ?", (tarea, limite)
            ).fetchall()
        else:
            rows = c.execute("SELECT * FROM results ORDER BY id DESC LIMIT ?", (limite,)).fetchall()
    return [
        {
            "id": r["id"],
            "tarea": r["task"],
            "ok": bool(r["ok"]),
            "inicio": iso(r["started"]),
            "duracion_s": round(r["duration"], 2),
            "salida": r["output"],
        }
        for r in rows
    ]


@mcp.tool()
def resumen_estado(horas: int = 24) -> dict:
    """Resumen de las últimas N horas: ejecuciones, fallas y estado por tarea."""
    since = time.time() - horas * 3600
    with db() as c:
        rows = c.execute(
            "SELECT task, SUM(ok) AS oks, COUNT(*) AS total, MAX(started) AS last "
            "FROM results WHERE started>=? GROUP BY task",
            (since,),
        ).fetchall()
    por_tarea = [
        {
            "tarea": r["task"],
            "ejecuciones": r["total"],
            "fallas": r["total"] - (r["oks"] or 0),
            "ultima": iso(r["last"]),
        }
        for r in rows
    ]
    return {
        "ventana_horas": horas,
        "total_ejecuciones": sum(x["ejecuciones"] for x in por_tarea),
        "total_fallas": sum(x["fallas"] for x in por_tarea),
        "por_tarea": por_tarea,
    }


@mcp.tool()
def guardar_informe(proyecto: str, titulo: str, cuerpo: str) -> dict:
    """Guarda un informe de un proyecto (para que Jarvis deje constancia de su trabajo)."""
    with db() as c:
        cur = c.execute(
            "INSERT INTO reports(project, title, body, created) VALUES (?,?,?,?)",
            (proyecto, titulo, cuerpo, time.time()),
        )
    return {"ok": True, "id": cur.lastrowid}


@mcp.tool()
def leer_informes(proyecto: Optional[str] = None, limite: int = 10) -> list[dict]:
    """Lee los últimos informes guardados, opcionalmente de un proyecto."""
    limite = max(1, min(limite, 50))
    with db() as c:
        if proyecto:
            rows = c.execute(
                "SELECT * FROM reports WHERE project=? ORDER BY id DESC LIMIT ?", (proyecto, limite)
            ).fetchall()
        else:
            rows = c.execute("SELECT * FROM reports ORDER BY id DESC LIMIT ?", (limite,)).fetchall()
    return [
        {"id": r["id"], "proyecto": r["project"], "titulo": r["title"],
         "cuerpo": r["body"], "fecha": iso(r["created"])}
        for r in rows
    ]


@mcp.tool()
async def notificar(mensaje: str) -> dict:
    """Envía un mensaje a Pablo (Telegram y/o webhook, según lo configurado)."""
    sent = await _notify(mensaje)
    if not sent:
        return {"ok": False, "error": "No hay canal configurado (TELEGRAM_* o NOTIFY_WEBHOOK_URL)"}
    return {"ok": True, "canales": sent}


# ---------------------------------------------------------------- casa
SIN_CASA = {"ok": False, "error": "casa-servidor no está configurado (CASA_USUARIO, CASA_CLAVE, CASA_CA)"}


@mcp.tool()
def casa_estado(modulo: Optional[str] = None) -> dict | list[dict]:
    """Estado actual de la casa: último valor y si está conectado cada módulo (o uno solo)."""
    if not casa:
        return SIN_CASA
    r = casa.ver_estado(modulo)
    return r if r is not None else {"ok": False, "error": f"no conozco el módulo '{modulo}'"}


@mcp.tool()
async def casa_ordenar(modulo: str, accion: str, params: Optional[dict] = None) -> dict:
    """Da una orden a un módulo (ej. modulo='riego', accion='abrir', params={'minutos': 10}) y espera la confirmación.

    La política de la casa la aplica el servidor: hay cosas que nunca se pueden por acá (portón, portero,
    cerraduras), otras necesitan que una persona confirme en la app, y hay topes de tiempo. Si la respuesta es
    'esperando_confirmacion_de_una_persona', avisale a Pablo; Jarvis no puede confirmar por sí mismo.
    """
    if not casa:
        return SIN_CASA
    return await casa.ordenar(modulo, accion, params)


@mcp.tool()
def casa_eventos(limite: int = 20, modulo: Optional[str] = None) -> list[dict] | dict:
    """Últimos eventos de la casa (movimiento, órdenes sin respuesta, etc.), del más nuevo al más viejo."""
    if not casa:
        return SIN_CASA
    ev = [e for e in reversed(casa.eventos) if not modulo or e["modulo"] == modulo]
    return ev[: max(1, min(limite, 100))]


# ---------------------------------------------------------------- app + auth
class BearerAuth:
    """Middleware ASGI: exige 'Authorization: Bearer <JARVIS_TOKEN>' (salvo /health)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"] != "/health":
            headers = dict(scope["headers"])
            supplied = headers.get(b"authorization", b"")
            token = VOZ_TOKEN if scope["path"] == "/voz" else AUTH_TOKEN
            expected = f"Bearer {token}".encode()
            if not token or not hmac.compare_digest(supplied, expected):
                resp = JSONResponse({"error": "unauthorized"}, status_code=401)
                await resp(scope, receive, send)
                return
        await self.app(scope, receive, send)


async def voz(request: Request):
    """POST /voz {"texto": "...", "fuente": "celu"} -> {"respuesta": "..."}. Lo llama el puente de voz y texto."""
    if cerebro is None or int(request.headers.get("content-length", "0") or 0) > 4096:
        return JSONResponse({"error": "no disponible"}, status_code=503)
    try:
        datos = await request.json()
        texto, fuente = str(datos["texto"]), str(datos.get("fuente", "voz"))[:32]
    except (ValueError, KeyError, TypeError):
        return JSONResponse({"error": "se espera {'texto': ...}"}, status_code=400)
    return JSONResponse({"respuesta": await cerebro.atender(texto, fuente)})


async def health(_: Request):
    return JSONResponse({"status": "ok"})


@contextlib.asynccontextmanager
async def lifespan(app: Starlette):
    global casa, cerebro
    init_db()
    load_tasks()
    if CASA_USUARIO:
        casa = CasaCliente(CASA_HOST, CASA_PORT, CASA_CA, CASA_USUARIO, CASA_CLAVE, on_aviso=_notify,
                           eventos_a_avisar=CASA_AVISAR_EVENTOS)
    cerebro = Cerebro(casa, DB_PATH)
    async with contextlib.AsyncExitStack() as stack:
        await stack.enter_async_context(mcp.session_manager.run())
        tasks = [asyncio.create_task(_scheduler())]
        if casa:
            tasks.append(asyncio.create_task(casa.correr()))
        try:
            yield
        finally:
            for t in tasks:
                t.cancel()


def build_app() -> Starlette:
    inner = Starlette(
        routes=[
            Route("/health", health),
            Route("/voz", voz, methods=["POST"]),
            Mount("/", app=mcp.streamable_http_app()),
        ],
        lifespan=lifespan,
    )
    return BearerAuth(inner)


app = build_app()

if __name__ == "__main__":
    if not AUTH_TOKEN:
        raise SystemExit("Falta JARVIS_TOKEN (generá uno: python -c \"import secrets;print(secrets.token_urlsafe(32))\")")
    uvicorn.run(app, host=HOST, port=int(os.environ.get("PORT", "8000")))
