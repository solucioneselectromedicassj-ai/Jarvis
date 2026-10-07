"""Intérprete local de órdenes de la casa (sin internet, sin modelo): "abrí el riego 10 minutos", "cómo está el tanque".

Es lo primero que se prueba con lo que Pablo dice o escribe. Si no entiende, recién ahí se consulta al modelo de
lenguaje (cerebro.py), que NO puede dar órdenes: solo conversa.
"""
import re
import unicodedata

SENSIBLES = re.compile(r"porton|portero|cerradura|puerta")      # nunca por voz ni texto: solo con el botón de la app
SINONIMOS = {
    "abrir": {"abri", "abre", "abrir", "abrime", "abran"},
    "cerrar": {"cerra", "cerrar", "cierra", "cerrame"},
    "encender": {"encende", "encender", "enciende", "prende", "prender", "prendeme", "encendeme"},
    "apagar": {"apaga", "apagar", "apagame"},
}
ESTADO = re.compile(r"como esta|como andan?|estado|nivel|cuanto|que hay|esta (abierto|abierta|cerrado|cerrada|prendid|apagad|encendid)")


def norm(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", t.lower()) if unicodedata.category(c) != "Mn")


def raiz(p: str) -> str:
    """luces -> luz, riegos -> riego: para que 'la luz' encuentre el módulo 'luces'."""
    if p.endswith("ces"):
        return p[:-3] + "z"
    return p[:-1] if p.endswith("s") and len(p) > 3 else p


def minutos(t: str) -> int | None:
    if re.search(r"media hora", t):
        return 30
    m = re.search(r"(\d+)\s*(minutos?|min\b|horas?|h\b)?", t)
    if m:
        n = int(m.group(1))
        return n * 60 if m.group(2) and m.group(2).startswith("h") else n
    if re.search(r"\buna hora\b", t):
        return 60
    return None


def interpretar(texto: str, modulos: dict) -> dict:
    """modulos: {nombre: {'tipo': str, 'acciones': [..]}}. Devuelve {'tipo': 'estado'|'orden'|'nada', ...}."""
    t = norm(texto)
    palabras = {raiz(p) for p in re.split(r"[^a-z0-9]+", t) if p}
    modulo = next((n for n, m in modulos.items()
                   if raiz(norm(n)) in palabras or (m.get("tipo") and raiz(norm(m["tipo"])) in palabras)), None)
    if ESTADO.search(t) or (modulo is None and re.search(r"\btodo\b|\bcasa\b", t)):
        return {"tipo": "estado", "modulo": modulo}
    accion = next((a for a, ws in SINONIMOS.items() if any(raiz(w) in palabras or w in palabras for w in ws)), None)
    if modulo and accion:
        return {"tipo": "orden", "modulo": modulo, "accion": accion, "minutos": minutos(t)}
    return {"tipo": "nada"}
