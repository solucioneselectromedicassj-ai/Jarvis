"""Usuarios, claves y permisos. `permisos` es la versión Python de `acl_lines` del script original."""
import hashlib
import hmac
import json
import os
import re
import secrets
from pathlib import Path

MODULE_WRITE = ("desc", "estado", "evento")          # `online` lo escribe solo el servidor
MODULE_MEDIA_WRITE = ("foto", "video", "audio/out")
SERVER_ONLY = ("online",)
PREFIX = {"modulo": "m-", "persona": "p-", "servicio": "s-"}
NOMBRE_RE = re.compile(r"^[a-z0-9_-]{1,32}$")


def nueva_clave(n: int = 24) -> str:
    return secrets.token_urlsafe(n)


def hash_clave(clave: str) -> str:
    salt = os.urandom(16)
    h = hashlib.scrypt(clave.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${h.hex()}"


def verificar_clave(clave: str, guardado: str) -> bool:
    try:
        _, salt, h = guardado.split("$")
        calc = hashlib.scrypt(clave.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1, dklen=32)
        return hmac.compare_digest(calc, bytes.fromhex(h))
    except (ValueError, TypeError):
        return False


def permisos(info: dict) -> dict:
    """Permisos mínimos de un usuario: {'leer': [patrones], 'escribir': [patrones]}. Función pura."""
    if info.get("bloqueado"):
        return {"leer": [], "escribir": []}
    leer, escribir = [], []
    t = info["tipo"]
    if t == "modulo":
        m = info["modulo"]
        escribir += [f"casa/{m}/{k}" for k in MODULE_WRITE]
        leer += [f"casa/{m}/cmd", f"casa/{m}/respuesta"]
        if info.get("camara"):
            escribir += [f"casa/{m}/{k}" for k in MODULE_MEDIA_WRITE]
            leer.append(f"casa/{m}/audio/in")
    elif t == "persona":
        if info["rol"] == "completo":
            leer.append("casa/#")
            escribir += ["casa/+/cmd", "casa/+/audio/in"]
            if info.get("nombre"):         # texto para Jarvis (el puente responde en casa/<nombre>/respuesta)
                escribir.append(f"casa/{info['nombre']}/pregunta")
        else:  # basico: estados y eventos, nunca foto/video/audio; órdenes solo a su lista
            leer += [f"casa/+/{k}" for k in MODULE_WRITE + SERVER_ONLY]
            escribir += [f"casa/{m}/cmd" for m in info.get("cmd", [])]
    elif t == "servicio":
        leer.append("casa/#")
        if info.get("responde"):          # el proceso de análisis de voz: publica la respuesta de Jarvis
            escribir.append("casa/+/respuesta")
    return {"leer": leer, "escribir": escribir}


def fuente_de_audio(usuario: str, info: dict) -> str | None:
    """Nombre bajo el que se guarda el audio que sube este usuario, o None si no puede subir.

    Módulos con `camara` (portero, micrófonos) y personas con rol completo (botón de hablar del teléfono).
    El rol básico nunca sube ni ve audio.
    """
    if info.get("bloqueado"):
        return None
    if info["tipo"] == "modulo" and info.get("camara"):
        return info["modulo"]
    if info["tipo"] == "persona" and info.get("rol") == "completo":
        nombre = info.get("nombre") or usuario.split("-", 1)[-1]
        return nombre if NOMBRE_RE.match(nombre) else None
    return None


def coincide(patron: str, topic: str) -> bool:
    """Comodines estilo MQTT: `+` un nivel, `#` el resto (solo al final)."""
    p, t = patron.split("/"), topic.split("/")
    for i, parte in enumerate(p):
        if parte == "#":
            return i == len(p) - 1
        if i >= len(t) or (parte != "+" and parte != t[i]):
            return False
    return len(p) == len(t)


def permitido(patrones: list[str], topic: str) -> bool:
    return any(coincide(p, topic) for p in patrones)


class Registro:
    def __init__(self, path: Path):
        self.path = path

    def cargar(self) -> dict:
        if self.path.exists():
            return json.loads(self.path.read_text(encoding="utf-8"))
        return {"usuarios": {}}

    def guardar(self, reg: dict) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(reg, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.path)

    def alta(self, tipo: str, nombre: str, **extra) -> tuple[str, str]:
        """Crea un usuario y devuelve (usuario, clave). La clave se muestra una sola vez."""
        if not NOMBRE_RE.match(nombre):
            raise ValueError("Nombre inválido: usá a-z, 0-9, guion y guion bajo (máx. 32)")
        usuario = PREFIX[tipo] + nombre
        reg = self.cargar()
        if usuario in reg["usuarios"]:
            raise ValueError(f"'{usuario}' ya existe")
        clave = nueva_clave()
        info = {"tipo": tipo, "hash": hash_clave(clave), **extra}
        if tipo == "modulo":
            info["modulo"] = nombre
        info["nombre"] = nombre
        reg["usuarios"][usuario] = info
        self.guardar(reg)
        return usuario, clave

    def _modificar(self, usuario: str, **campos) -> None:
        reg = self.cargar()
        if usuario not in reg["usuarios"]:
            raise KeyError(usuario)
        reg["usuarios"][usuario].update(campos)
        self.guardar(reg)

    def cambiar_clave(self, usuario: str) -> str:
        clave = nueva_clave()
        self._modificar(usuario, hash=hash_clave(clave))
        return clave

    def bloquear(self, usuario: str) -> None:
        self._modificar(usuario, bloqueado=True)

    def desbloquear(self, usuario: str) -> str:
        """Vuelve a habilitar con clave nueva (la vieja pudo quedar en el teléfono perdido)."""
        clave = nueva_clave()
        reg = self.cargar()
        if usuario not in reg["usuarios"]:
            raise KeyError(usuario)
        reg["usuarios"][usuario].pop("bloqueado", None)
        reg["usuarios"][usuario]["hash"] = hash_clave(clave)
        self.guardar(reg)
        return clave

    def baja(self, usuario: str) -> None:
        reg = self.cargar()
        if reg["usuarios"].pop(usuario, None) is None:
            raise KeyError(usuario)
        self.guardar(reg)
