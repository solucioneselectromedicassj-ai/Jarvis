"""Cerebro de Jarvis: qué hacer con una frase. Local primero; el modelo de lenguaje solo conversa.

1. Intérprete local (órdenes y estado de la casa) -> casa-servidor aplica la política, sin internet.
2. Si no entiende y Pablo lo habilitó (JARVIS_INTERNET_TEXTO=1 + clave), pregunta a Gemini (nivel gratuito).
   Gemini NO tiene herramientas: no puede mover nada de la casa, aunque un texto lo engañe.
Afuera sale solo texto; nunca audio, foto ni video. El estado de la casa solo si JARVIS_ENVIAR_ESTADO_CASA=1.
"""
import json
import os
import sqlite3
import time
from collections import defaultdict, deque
from datetime import date

import httpx

from interprete import SENSIBLES, interpretar, norm

GEMINI_KEY = os.environ.get("JARVIS_GEMINI_KEY", "")
GEMINI_MODEL = os.environ.get("JARVIS_GEMINI_MODEL", "gemini-2.5-flash")   # verificar en AI Studio qué modelos tienen nivel gratuito
INTERNET_TEXTO = os.environ.get("JARVIS_INTERNET_TEXTO", "0") == "1"
ENVIAR_ESTADO = os.environ.get("JARVIS_ENVIAR_ESTADO_CASA", "0") == "1"
MAX_POR_DIA = int(os.environ.get("JARVIS_GEMINI_MAX_DIA", "100"))
URL = "https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent"

SISTEMA = ("Sos Jarvis, el asistente de la casa y el taller de Pablo, en Pocito, San Juan, Argentina. Respondé en español "
           "rioplatense, en una o dos oraciones cortas, sin markdown, sin listas y sin emojis, porque la respuesta se lee en "
           "voz alta. Si no sabés algo, decilo. No podés mover nada de la casa: si piden una orden, decí que usen las "
           "órdenes de la casa.")


class Cerebro:
    def __init__(self, casa, db_path: str, transporte: httpx.AsyncBaseTransport | None = None):
        self.casa, self.db_path, self.transporte = casa, db_path, transporte
        self.historial: dict[str, deque] = defaultdict(lambda: deque(maxlen=6))

    # ------------------------------------------------------------ entrada
    async def atender(self, texto: str, fuente: str = "voz") -> str:
        texto = (texto or "").strip()[:500]
        if not texto:
            return "No te escuché."
        if self.casa is None:
            modulos = {}
        else:
            modulos = {m["modulo"]: {"tipo": (self.casa.descripciones.get(m["modulo"]) or {}).get("tipo"), "acciones": m["acciones"] or []}
                       for m in self.casa.ver_estado()}
        r = interpretar(texto, modulos)
        if r["tipo"] == "estado":
            return self._estado(r["modulo"])
        if r["tipo"] == "orden":
            return await self._orden(r, modulos)
        return await self._conversar(texto, fuente)

    # ------------------------------------------------------------ casa
    def _estado(self, modulo):
        if self.casa is None:
            return "No estoy conectado a la casa."
        if modulo:
            e = self.casa.ver_estado(modulo)
            return f"{modulo}: " + ("desconectado." if not e["online"] else self._valor(e["valor"]))
        todos = self.casa.ver_estado()
        if not todos:
            return "No hay módulos conectados."
        return " ".join(f"{e['modulo']}: " + ("desconectado." if not e["online"] else self._valor(e["valor"])) for e in todos)

    @staticmethod
    def _valor(v):
        if v is None:
            return "sin datos."
        if isinstance(v, dict):
            return ", ".join(f"{k} {x}" for k, x in v.items()) + "."
        return f"{v}."

    async def _orden(self, r, modulos):
        m, a = r["modulo"], r["accion"]
        if SENSIBLES.search(norm(m)):
            return f"{m} no se maneja por voz ni por texto. Usá el botón en la aplicación."
        if self.casa is None:
            return "No estoy conectado a la casa."
        if a not in modulos[m]["acciones"]:
            return f"{m} no entiende {a}. Acepta: {', '.join(modulos[m]['acciones']) or 'ninguna orden'}."
        params = {"minutos": r["minutos"]} if r["minutos"] and a in ("abrir", "encender") else None
        res = await self.casa.ordenar(m, a, params)
        if res.get("ok"):
            return f"Listo: {a} {m}" + (f" por {r['minutos']} minutos." if params else ".")
        err = res.get("error")
        if res.get("ok") is None:
            if "persona" in (res.get("estado") or ""):
                return "Necesito que alguien confirme en la aplicación."
            return "Mandé la orden pero todavía no confirmó. Fijate en unos segundos."
        return {"offline": f"{m} está desconectado.", "politica": res.get("detalle") or "La política de la casa no lo permite.",
                "permiso": f"No tengo permiso para manejar {m}.", "sin_respuesta": f"{m} no confirmó la orden.",
                "sin_conexion": "Perdí la conexión con la casa."}.get(err, "No pude hacerlo.")

    # ------------------------------------------------------------ conversación (Gemini, opcional)
    def _contar(self) -> bool:
        """Cuenta una consulta del día; False si ya llegó al tope local (cuidar el cupo gratis)."""
        hoy = date.today().isoformat()
        with sqlite3.connect(self.db_path) as c:
            c.execute("CREATE TABLE IF NOT EXISTS uso (dia TEXT PRIMARY KEY, n INTEGER NOT NULL)")
            n = (c.execute("SELECT n FROM uso WHERE dia=?", (hoy,)).fetchone() or (0,))[0]
            if n >= MAX_POR_DIA:
                return False
            c.execute("INSERT INTO uso(dia, n) VALUES (?, 1) ON CONFLICT(dia) DO UPDATE SET n = n + 1", (hoy,))
        return True

    async def _conversar(self, texto: str, fuente: str) -> str:
        if not (GEMINI_KEY and INTERNET_TEXTO):
            return ("No entendí esa orden de la casa. Para conversar de otros temas hay que habilitar el modelo de lenguaje "
                    "en la configuración.")
        if not self._contar():
            return "Hoy ya usé todas las consultas gratuitas que me permití. Mañana sigo."
        sistema = SISTEMA
        if ENVIAR_ESTADO and self.casa is not None:
            sistema += " Estado actual de la casa: " + self._estado(None)
        h = self.historial[fuente]
        contenido = [{"role": r, "parts": [{"text": t}]} for r, t in h] + [{"role": "user", "parts": [{"text": texto}]}]
        cuerpo = {"system_instruction": {"parts": [{"text": sistema}]}, "contents": contenido,
                  "generationConfig": {"maxOutputTokens": 300, "temperature": 0.6}}
        try:
            async with httpx.AsyncClient(timeout=20, transport=self.transporte) as c:
                r = await c.post(URL.format(modelo=GEMINI_MODEL), headers={"x-goog-api-key": GEMINI_KEY}, json=cuerpo)
        except httpx.HTTPError:
            return "No pude consultar a internet en este momento."
        if r.status_code == 429:
            return "Llegué al límite gratuito del servicio. Probá en un rato."
        if r.status_code != 200:
            return "No pude consultar el modelo ahora."
        try:
            partes = r.json()["candidates"][0]["content"]["parts"]
            resp = " ".join(p.get("text", "") for p in partes).strip()
        except (KeyError, IndexError, TypeError, ValueError):
            return "No me llegó una respuesta clara."
        if not resp:
            return "No me llegó una respuesta clara."
        h.append(("user", texto))
        h.append(("model", resp))
        return resp[:800]
