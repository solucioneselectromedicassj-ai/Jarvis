"""Política de la casa: vale igual para todos los canales (app, voz, agente, Telegram). Vive en el servidor.

Archivo `politica.json` (ver politica.ejemplo.json):
{"modulos": {"porton": {"prohibido_en": ["voz", "agente"]},
             "calefon": {"confirmar": ["encender"], "tope_minutos": 60}}}
"""
import json
from pathlib import Path

CANALES = ("app", "telegram", "voz", "agente")
CANALES_PERSONA = ("app", "telegram")      # los únicos que pueden confirmar acciones
ACCIONES_CON_TOPE = ("encender", "abrir")  # si el módulo define tope_minutos


class Politica:
    def __init__(self, path: Path | None = None):
        self.path, self._mtime, self._reglas = path, None, {}

    def _recargar(self):
        if not self.path or not self.path.exists():
            self._reglas = {}
            return
        m = self.path.stat().st_mtime
        if m != self._mtime:
            self._reglas = json.loads(self.path.read_text(encoding="utf-8")).get("modulos", {})
            self._mtime = m

    def evaluar(self, modulo: str, accion, params, canal: str) -> tuple[str, object]:
        """Devuelve ('ok', params_ajustados) | ('confirmar', params_ajustados) | ('prohibido'|'tope', motivo)."""
        try:
            self._recargar()
        except (OSError, ValueError):
            return "prohibido", "política ilegible: se rechaza por seguridad"
        r = self._reglas.get(modulo)
        if not r:
            return "ok", params
        if canal in r.get("prohibido_en", []):
            return "prohibido", f"'{modulo}' no se puede manejar por {canal}"
        params = dict(params) if isinstance(params, dict) else {}
        tope = r.get("tope_minutos")
        if tope and accion in ACCIONES_CON_TOPE:
            minutos = params.get("minutos", tope)
            if not isinstance(minutos, (int, float)) or isinstance(minutos, bool) or not 0 < minutos <= tope:
                return "tope", f"el máximo para '{modulo}' es {tope} minutos"
            params["minutos"] = minutos
        if accion in r.get("confirmar", []):
            return "confirmar", params
        return "ok", params
