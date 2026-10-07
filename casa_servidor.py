"""CLI de casa-servidor. Datos en CASA_DIR (por defecto ./datos). Las claves se muestran una sola vez."""
import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from casa import pki
from casa.registro import PREFIX, Registro, permisos
from casa.politica import Politica
from casa.servidor import Servidor, contexto_tls

BASE = Path(os.environ.get("CASA_DIR", "datos"))
PUERTO = 8883
PUERTO_WS = 8884


def registro() -> Registro:
    return Registro(BASE / "usuarios.json")


def die(msg):
    sys.exit(f"Error: {msg}")


def cmd_init(a):
    if (BASE / "ca.crt").exists():
        die(f"Ya existe una CA en {BASE}. Para cambiar la IP usá 'renovar-servidor'.")
    pki.crear_ca(BASE)
    pki.emitir_servidor(BASE, a.ip, a.dns)
    (BASE / "config.json").write_text(json.dumps({"puerto": a.puerto}), encoding="utf-8")
    print(f"CA y certificado del servidor creados en {BASE}.\nGuardá una copia de {BASE / 'ca.key'} fuera de la PC.")


def cmd_renovar(a):
    pki.emitir_servidor(BASE, a.ip, a.dns)
    print("Certificado del servidor renovado con la misma CA.")


def _mostrar(usuario, clave):
    print(f"Usuario: {usuario}\nClave:   {clave}\n(se muestra una sola vez; en el servidor queda solo el hash)")


def cmd_alta_modulo(a):
    try:
        u, c = registro().alta("modulo", a.id, camara=a.camara)
    except ValueError as e:
        die(e)
    _mostrar(u, c)
    print(f'\nCredenciales para el firmware (casa_credenciales.h, NO subir a ningún repo):\n#define CASA_USUARIO "{u}"\n#define CASA_CLAVE "{c}"')


def cmd_alta_persona(a):
    cmd = [m for m in (a.cmd or "").split(",") if m]
    if a.rol == "basico" and not cmd:
        die("El rol basico necesita --cmd con los módulos a los que puede dar órdenes")
    try:
        u, c = registro().alta("persona", a.nombre, rol=a.rol, canal=a.canal, cmd=cmd if a.rol == "basico" else [])
    except ValueError as e:
        die(e)
    _mostrar(u, c)


def cmd_alta_servicio(a):
    try:
        u, c = registro().alta("servicio", a.nombre, responde=a.responde)
    except ValueError as e:
        die(e)
    _mostrar(u, c)


def _usuario(a):
    return a.usuario


def cmd_clave(a):
    try:
        _mostrar(a.usuario, registro().cambiar_clave(a.usuario))
    except KeyError:
        die(f"No existe '{a.usuario}'")


def cmd_baja(a):
    try:
        registro().baja(a.usuario)
    except KeyError:
        die(f"No existe '{a.usuario}'")
    print(f"'{a.usuario}' eliminado.")


def cmd_bloquear(a):
    try:
        registro().bloquear(a.usuario)
    except KeyError:
        die(f"No existe '{a.usuario}'")
    print(f"'{a.usuario}' bloqueado (se corta en pocos segundos si está conectado).")


def cmd_desbloquear(a):
    try:
        _mostrar(a.usuario, registro().desbloquear(a.usuario))
    except KeyError:
        die(f"No existe '{a.usuario}'")


def cmd_lista(a):
    for u, info in sorted(registro().cargar()["usuarios"].items()):
        p = permisos(info)
        estado = " [BLOQUEADO]" if info.get("bloqueado") else ""
        print(f"{u}{estado}  ({info['tipo']}{' ' + info['rol'] if 'rol' in info else ''})")
        for k in ("leer", "escribir"):
            for patron in p[k]:
                print(f"    {k}: {patron}")


def cmd_ca(a):
    print((BASE / "ca.crt").read_text(), end="")


def cmd_servir(a):
    cfg = json.loads((BASE / "config.json").read_text(encoding="utf-8")) if (BASE / "config.json").exists() else {}
    srv = Servidor(registro(), contexto_tls(BASE), politica=Politica(BASE / "politica.json"),
                   app_dir=Path(a.app) if a.app else Path(__file__).parent / "app",
                   media_dir=BASE / "media", cuota_mb=cfg.get("cuota_media_mb", 200), retencion_h=cfg.get("retencion_media_h", 24))

    async def main():
        wsp = a.ws_puerto if a.ws_puerto is not None else cfg.get("ws_puerto", PUERTO_WS)
        port = await srv.iniciar(a.host, a.puerto or cfg.get("puerto", PUERTO), wsp or None, cfg.get("origenes"))
        print(f"casa-servidor escuchando en {a.host}:{port}" + (f" (WebSocket {srv.ws_port})" if wsp else ""), flush=True)
        await asyncio.Event().wait()

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass


def main():
    ap = argparse.ArgumentParser(prog="casa_servidor")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def nuevo(nombre, f, ayuda):
        s = sub.add_parser(nombre, help=ayuda)
        s.set_defaults(f=f)
        return s

    s = nuevo("init", cmd_init, "crea la CA y el certificado del servidor")
    s.add_argument("--ip", action="append", default=[], help="IP fija del servidor (repetible)")
    s.add_argument("--dns", action="append", default=[], help="nombre local o de Tailscale (repetible)")
    s.add_argument("--puerto", type=int, default=PUERTO)
    s = nuevo("renovar-servidor", cmd_renovar, "reemite el certificado del servidor con la misma CA")
    s.add_argument("--ip", action="append", default=[]); s.add_argument("--dns", action="append", default=[])
    s = nuevo("alta-modulo", cmd_alta_modulo, "usuario de un módulo")
    s.add_argument("id"); s.add_argument("--camara", action="store_true", help="permite foto/video/audio (portero)")
    s = nuevo("alta-persona", cmd_alta_persona, "usuario de una persona (uno por dispositivo)")
    s.add_argument("nombre"); s.add_argument("--rol", choices=("completo", "basico"), default="completo")
    s.add_argument("--canal", choices=("app", "telegram", "voz", "agente"), required=True,
                   help="desde dónde opera: app y telegram pueden confirmar acciones; voz y agente no (Jarvis MCP = agente)")
    s.add_argument("--cmd", help="(rol basico) módulos a los que puede dar órdenes, separados por coma")
    s = nuevo("alta-servicio", cmd_alta_servicio, "usuario de solo lectura (con --responde, el análisis de voz)")
    s.add_argument("nombre")
    s.add_argument("--responde", action="store_true", help="puede publicar casa/<fuente>/respuesta (proceso de análisis de voz)")
    for n, f, h in (("clave", cmd_clave, "cambia la clave"), ("baja", cmd_baja, "elimina un usuario"),
                    ("bloquear", cmd_bloquear, "corta ya a un usuario (teléfono perdido)"),
                    ("desbloquear", cmd_desbloquear, "rehabilita con clave nueva")):
        nuevo(n, f, h).add_argument("usuario", help="con prefijo: m-, p- o s-")
    nuevo("lista", cmd_lista, "usuarios y permisos")
    nuevo("ca", cmd_ca, "imprime el certificado de la CA")
    s = nuevo("servir", cmd_servir, "arranca el servidor")
    s.add_argument("--host", default="127.0.0.1", help="interfaz donde escucha (LAN o Tailscale a propósito)")
    s.add_argument("--puerto", type=int)
    s.add_argument("--ws-puerto", type=int, help="puerto WebSocket y de la app web (0 = apagado)")
    s.add_argument("--app", help="carpeta de la app web (por defecto ./app)")
    a = ap.parse_args()
    a.f(a)


if __name__ == "__main__":
    main()
