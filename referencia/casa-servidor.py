#!/usr/bin/env python3
"""
casa-servidor: broker MQTT propio para la casa (Mosquitto + autoridad certificadora propia + permisos mínimos).

Qué hace
  - Crea una CA propia y el certificado del servidor (válidos desde 1969, para que un ESP32 sin hora igual los acepte).
  - Da de alta módulos y personas. Cada uno tiene su usuario, su clave y SOLO los permisos que necesita.
  - Escribe la configuración de Mosquitto (TLS, sin acceso anónimo) y, si se pide, la de Caddy para servir la app por VPN.
Las claves se muestran una sola vez; en el servidor solo queda el hash.

Uso (como root):  casa-servidor.py init --ip 192.168.1.50 --vpn-host casa.mi-red.ts.net
                  casa-servidor.py alta-modulo sala > mqtt_credenciales.h
                  casa-servidor.py alta-persona hija --rol basico --cmd sala,ir-living
"""
import argparse, ipaddress, json, os, re, secrets, shutil, string, subprocess, sys

BASE = os.environ.get('CASA_DIR', '/etc/casa-servidor')
NO_RESTART = os.environ.get('CASA_NO_RESTART') == '1'
VALID_FROM = '19691231000000Z'      # el ESP32 arranca con la hora en 1970: el certificado ya tiene que ser válido
VALID_TO = '20491231235959Z'
MODULE_WRITE = ('desc', 'estado', 'online', 'evento')
MODULE_MEDIA_WRITE = ('foto', 'video', 'audio/out')
PREFIX = {'modulo': 'm-', 'persona': 'p-', 'servicio': 's-'}


def p(*parts):
    return os.path.join(BASE, *parts)


def die(msg):
    print('Error: ' + msg, file=sys.stderr)
    sys.exit(1)


def log(msg):
    print(msg, file=sys.stderr)


def run(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode != 0:
        die('falló %s\n%s' % (' '.join(cmd[:2]), (r.stderr or r.stdout).strip()))
    return r.stdout


def write(path, text, mode=0o644):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)
    os.chmod(path, mode)


def load_json(path, default=None):
    if not os.path.exists(path):
        if default is None:
            die('no está inicializado (falta %s). Corré primero: casa-servidor.py init --ip ...' % path)
        return default
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def save_json(path, obj):
    write(path, json.dumps(obj, indent=2, ensure_ascii=False) + '\n', 0o600)


def new_password(n=24):
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(n))


# ---------------------------------------------------------------- permisos (ACL)
def acl_lines(user, info):
    """Permisos mínimos de cada usuario. Función pura: es lo que prueban los tests."""
    L = ['user ' + user]
    if info.get('bloqueado'):          # teléfono perdido o robado: sigue en el registro pero no puede leer ni escribir nada
        return L + ['# BLOQUEADO']
    t = info['tipo']
    if t == 'modulo':
        m = info['modulo']
        for k in MODULE_WRITE:
            L.append('topic write casa/%s/%s' % (m, k))
        L.append('topic read casa/%s/cmd' % m)
        if info.get('camara'):
            for k in MODULE_MEDIA_WRITE:
                L.append('topic write casa/%s/%s' % (m, k))
            L.append('topic read casa/%s/audio/in' % m)
    elif t == 'persona':
        if info['rol'] == 'completo':
            L += ['topic read casa/#', 'topic write casa/+/cmd', 'topic write casa/+/audio/in']
        else:  # basico: ve estados y eventos, nunca foto/video/audio; manda órdenes solo a los módulos indicados
            for k in MODULE_WRITE:
                L.append('topic read casa/+/%s' % k)
            for m in info.get('cmd', []):
                L.append('topic write casa/%s/cmd' % m)
    elif t == 'servicio':
        L.append('topic read casa/#')
    return L


def build_acl(reg):
    out = ['# Generado por casa-servidor.py. No editar a mano: se rehace en cada alta o baja.', '']
    for user in sorted(reg['usuarios']):
        out += acl_lines(user, reg['usuarios'][user]) + ['']
    return '\n'.join(out)


# ---------------------------------------------------------------- certificados
def write_cnf(sans):
    alt = []
    counts = {'IP': 0, 'DNS': 0}
    for kind, val in sans:
        counts[kind] += 1
        alt.append('%s.%d = %s' % (kind, counts[kind], val))
    text = """[ ca ]
default_ca = CA_default
[ CA_default ]
dir = %(d)s/ca
database = $dir/index.txt
new_certs_dir = $dir/newcerts
serial = $dir/serial
default_md = sha256
unique_subject = no
policy = policy_any
[ policy_any ]
commonName = supplied
[ req ]
distinguished_name = dn
prompt = no
[ dn ]
CN = Casa
[ v3_ca ]
basicConstraints = critical, CA:true, pathlen:0
keyUsage = critical, keyCertSign, cRLSign
subjectKeyIdentifier = hash
[ v3_server ]
basicConstraints = critical, CA:false
keyUsage = critical, digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
subjectAltName = @alt
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid
[ alt ]
%(alt)s
""" % {'d': BASE, 'alt': '\n'.join(alt)}
    write(p('ca', 'openssl.cnf'), text, 0o600)


def parse_sans(ip, dns, extra):
    sans = [('IP', str(ipaddress.ip_address(ip)))]
    for d in dns:
        sans.append(('DNS', d))
    for e in extra:
        kind, _, val = e.partition(':')
        kind = kind.upper()
        if kind not in ('IP', 'DNS') or not val:
            die('--san debe ser IP:x.x.x.x o DNS:nombre (recibí %r)' % e)
        sans.append((kind, val))
    return sans


def issue_server_cert(sans):
    write_cnf(sans)
    cnf = p('ca', 'openssl.cnf')
    run(['openssl', 'genrsa', '-out', p('server', 'server.key'), '2048'])
    run(['openssl', 'req', '-new', '-key', p('server', 'server.key'), '-out', p('server', 'server.csr'), '-config', cnf, '-subj', '/CN=casa-servidor'])
    run(['openssl', 'ca', '-batch', '-config', cnf, '-cert', p('ca', 'ca.crt'), '-keyfile', p('ca', 'ca.key'),
         '-in', p('server', 'server.csr'), '-out', p('server', 'server.crt'), '-startdate', VALID_FROM, '-enddate', VALID_TO,
         '-extensions', 'v3_server', '-notext'])
    os.remove(p('server', 'server.csr'))


# ---------------------------------------------------------------- configuración de servicios
def render_mosquitto(cfg):
    return """# Generado por casa-servidor.py. No editar a mano.
allow_anonymous false
password_file %(d)s/mosquitto/passwd
acl_file %(d)s/mosquitto/acl
allow_zero_length_clientid false
max_connections 60
max_packet_size 131072
max_queued_messages 200
connection_messages true
log_type error
log_type warning
log_type notice

# Módulos de la casa: solo por la red local, con TLS y la CA propia
listener 8883 %(ip)s
protocol mqtt
cafile %(d)s/server/ca.crt
certfile %(d)s/server/server.crt
keyfile %(d)s/server/server.key

# Aplicación web: solo en este equipo; Caddy la publica por la VPN con TLS
listener 9001 127.0.0.1
protocol websockets
""" % {'d': BASE, 'ip': cfg['ip']}


def render_caddy(cfg):
    bind = ('    bind %s\n' % cfg['vpn_ip']) if cfg.get('vpn_ip') else ''
    port = ':%s' % cfg['web_port'] if str(cfg['web_port']) != '443' else ''
    return """# Generado por casa-servidor.py. Sirve la app y el WebSocket de MQTT, solo por la VPN.
{
    auto_https off
}
https://%(host)s%(port)s {
%(bind)s    tls %(d)s/vpn-cert/casa.crt %(d)s/vpn-cert/casa.key
    header {
        Strict-Transport-Security "max-age=31536000"
        X-Content-Type-Options "nosniff"
        Referrer-Policy "no-referrer"
        Permissions-Policy "geolocation=(self), microphone=(self), camera=()"
        -Server
    }
    handle /mqtt {
        reverse_proxy 127.0.0.1:9001
    }
    handle {
        root * %(www)s
        file_server
    }
}
""" % {'host': cfg['vpn_host'], 'port': port, 'bind': bind, 'd': BASE, 'www': cfg['www']}


def fix_perms():
    """CA solo para root; lo que Mosquitto lee en caliente, para el grupo mosquitto."""
    if os.name != 'posix' or os.geteuid() != 0:
        return
    try:
        import grp
        grp.getgrnam('mosquitto')
    except KeyError:
        return
    for path, mode in ((BASE, 0o750), (p('server'), 0o750), (p('mosquitto'), 0o750), (p('ca'), 0o700)):
        if os.path.exists(path):
            os.chmod(path, mode)
    for path in (BASE, p('server'), p('mosquitto')):
        shutil.chown(path, 'root', 'mosquitto')
    for path in (p('server', 'server.key'), p('server', 'server.crt'), p('server', 'ca.crt'), p('mosquitto', 'passwd'),
                 p('mosquitto', 'acl'), p('mosquitto', 'casa.conf')):
        if os.path.exists(path):
            owner = 'mosquitto' if path.endswith(('passwd', 'acl')) else 'root'   # Mosquitto 2.x exige ser dueño de passwd y acl
            shutil.chown(path, owner, 'mosquitto')
            os.chmod(path, 0o640 if path.endswith(('.key', 'passwd', 'acl')) else 0o644)


def restart_broker():
    if NO_RESTART or not shutil.which('systemctl'):
        log('(no reinicié Mosquitto: hacelo con  sudo systemctl restart mosquitto )')
        return
    r = subprocess.run(['systemctl', 'restart', 'mosquitto'], capture_output=True, text=True)
    log('Mosquitto reiniciado.' if r.returncode == 0 else 'No pude reiniciar Mosquitto: ' + r.stderr.strip())


def apply_acl(reg):
    write(p('mosquitto', 'acl'), build_acl(reg), 0o640)
    fix_perms()


# ---------------------------------------------------------------- comandos
def cmd_init(a):
    if os.path.exists(p('config.json')):
        die('ya hay un servidor inicializado en %s. Para cambiar la IP usá "renovar-servidor". '
            'Rehacerlo todo implicaría recargar TODOS los módulos (cambia la CA).' % BASE)
    if not shutil.which('openssl'):
        die('falta openssl')
    sans = parse_sans(a.ip, a.dns, a.san)
    for d in ('ca/newcerts', 'server', 'mosquitto', 'vpn-cert', 'caddy'):
        os.makedirs(p(*d.split('/')), exist_ok=True)
    write(p('ca', 'index.txt'), '', 0o600)
    write(p('ca', 'index.txt.attr'), 'unique_subject = no\n', 0o600)
    write(p('ca', 'serial'), '01\n', 0o600)
    write_cnf(sans)
    cnf = p('ca', 'openssl.cnf')
    run(['openssl', 'genrsa', '-out', p('ca', 'ca.key'), '3072'])
    os.chmod(p('ca', 'ca.key'), 0o600)
    run(['openssl', 'req', '-new', '-key', p('ca', 'ca.key'), '-out', p('ca', 'ca.csr'), '-config', cnf, '-subj', '/CN=Casa CA'])
    run(['openssl', 'ca', '-batch', '-selfsign', '-config', cnf, '-in', p('ca', 'ca.csr'), '-keyfile', p('ca', 'ca.key'),
         '-out', p('ca', 'ca.crt'), '-startdate', VALID_FROM, '-enddate', VALID_TO, '-extensions', 'v3_ca', '-notext'])
    os.remove(p('ca', 'ca.csr'))
    shutil.copy(p('ca', 'ca.crt'), p('server', 'ca.crt'))
    issue_server_cert(sans)
    cfg = {'ip': a.ip, 'dns': a.dns, 'san': a.san, 'vpn_host': a.vpn_host, 'vpn_ip': a.vpn_ip, 'web_port': a.web_port, 'www': a.www}
    save_json(p('config.json'), cfg)
    save_json(p('registro.json'), {'usuarios': {}})
    write(p('mosquitto', 'passwd'), '', 0o640)
    write(p('mosquitto', 'casa.conf'), render_mosquitto(cfg))
    apply_acl({'usuarios': {}})
    if a.vpn_host:
        write(p('caddy', 'Caddyfile'), render_caddy(cfg))
    fix_perms()
    conf_d = '/etc/mosquitto/conf.d'
    if BASE == '/etc/casa-servidor' and os.path.isdir(conf_d) and os.geteuid() == 0:
        link = os.path.join(conf_d, 'casa.conf')
        if not os.path.exists(link):
            os.symlink(p('mosquitto', 'casa.conf'), link)
            log('Enlacé %s -> %s' % (link, p('mosquitto', 'casa.conf')))
    log('Listo. CA y certificado del servidor creados en %s (válidos hasta 2049).' % BASE)
    log('Siguiente: dar de alta módulos y personas, y reiniciar Mosquitto.')


def cmd_renovar_servidor(a):
    cfg = load_json(p('config.json'))
    if a.ip:
        cfg['ip'] = a.ip
    if a.dns:
        cfg['dns'] = a.dns
    if a.san:
        cfg['san'] = a.san
    issue_server_cert(parse_sans(cfg['ip'], cfg['dns'], cfg['san']))
    save_json(p('config.json'), cfg)
    write(p('mosquitto', 'casa.conf'), render_mosquitto(cfg))
    fix_perms()
    log('Certificado del servidor renovado con la misma CA: los módulos NO hay que recargarlos, salvo que cambie MQTT_HOST.')
    restart_broker()


def add_user(user, info, password=None):
    reg = load_json(p('registro.json'))
    if user in reg['usuarios']:
        die('"%s" ya existe. Para cambiarle la clave usá: clave %s' % (user, user))
    pw = password or new_password()
    pf = p('mosquitto', 'passwd')
    cmd = ['mosquitto_passwd', '-b'] + (['-c'] if not os.path.getsize(pf) else []) + [pf, user, pw]
    run(cmd)
    reg['usuarios'][user] = info
    save_json(p('registro.json'), reg)
    apply_acl(reg)
    fix_perms()
    return pw


def check_name(name, what):
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,30}', name):
        die('%s inválido: usá minúsculas, números y guiones (máx. 31): %r' % (what, name))


def header_for(user, pw, mod):
    cfg = load_json(p('config.json'))
    with open(p('ca', 'ca.crt'), encoding='utf-8') as f:
        ca = f.read().strip()
    return """// mqtt_credenciales.h — generado por casa-servidor para el módulo "%(mod)s".
// Es SECRETO: copialo a la carpeta del sketch y no lo subas a ningún repositorio.
#pragma once
#define MQTT_HOST         "%(ip)s"
#define MQTT_PORT         8883
#define MQTT_USE_TLS      1
#define MQTT_TLS_INSECURE 0
#define MQTT_USER         "%(user)s"
#define MQTT_PASS         "%(pw)s"
static const char ROOT_CA[] PROGMEM = R"EOF(
%(ca)s
)EOF";
""" % {'mod': mod, 'ip': cfg['ip'], 'user': user, 'pw': pw, 'ca': ca}


def cmd_alta_modulo(a):
    check_name(a.id, 'id de módulo')
    if a.id == 'config':
        die('"config" está reservado')
    user = 'm-' + a.id
    pw = add_user(user, {'tipo': 'modulo', 'modulo': a.id, 'camara': bool(a.camara)})
    sys.stdout.write(header_for(user, pw, a.id))
    log('Alta de %s. En el firmware, MODULE_ID debe ser exactamente "%s". Reiniciá Mosquitto para aplicar.' % (user, a.id))
    restart_broker()


def cmd_alta_persona(a):
    check_name(a.nombre, 'nombre')
    cmd = [m for m in (a.cmd or '').split(',') if m]
    for m in cmd:
        check_name(m, 'módulo en --cmd')
    if a.rol == 'basico' and not cmd:
        log('Aviso: rol básico sin --cmd: podrá ver estados pero no mandar órdenes a nada.')
    user = 'p-' + a.nombre
    pw = add_user(user, {'tipo': 'persona', 'rol': a.rol, 'cmd': cmd if a.rol == 'basico' else []})
    print('Usuario: %s\nClave:   %s' % (user, pw))
    log('Anotá la clave ahora: no se vuelve a mostrar (en el servidor queda solo el hash).')
    restart_broker()


def cmd_alta_servicio(a):
    check_name(a.nombre, 'nombre')
    user = 's-' + a.nombre
    pw = add_user(user, {'tipo': 'servicio'})
    print('Usuario: %s\nClave:   %s' % (user, pw))
    restart_broker()


def cmd_clave(a):
    reg = load_json(p('registro.json'))
    if a.usuario not in reg['usuarios']:
        die('no existe %s' % a.usuario)
    pw = new_password()
    run(['mosquitto_passwd', '-b', p('mosquitto', 'passwd'), a.usuario, pw])
    fix_perms()
    print('Usuario: %s\nClave:   %s' % (a.usuario, pw))
    log('Clave cambiada. Las sesiones abiertas se cortan al reiniciar Mosquitto.')
    restart_broker()


def cmd_baja(a):
    reg = load_json(p('registro.json'))
    if a.usuario not in reg['usuarios']:
        die('no existe %s' % a.usuario)
    run(['mosquitto_passwd', '-D', p('mosquitto', 'passwd'), a.usuario])
    del reg['usuarios'][a.usuario]
    save_json(p('registro.json'), reg)
    apply_acl(reg)
    log('Baja de %s. Se corta su sesión al reiniciar Mosquitto (lo hago ahora).' % a.usuario)
    restart_broker()


def cmd_bloquear(a):
    """Corte inmediato y reversible: clave descartada + sin permisos. Sirve desde cualquier PC con acceso al servidor."""
    reg = load_json(p('registro.json'))
    if a.usuario not in reg['usuarios']:
        die('no existe %s' % a.usuario)
    run(['mosquitto_passwd', '-b', p('mosquitto', 'passwd'), a.usuario, new_password(32)])   # clave nueva que nadie conoce
    reg['usuarios'][a.usuario]['bloqueado'] = True
    save_json(p('registro.json'), reg)
    apply_acl(reg)
    log('%s bloqueado: sin clave y sin permisos. Para volver a habilitarlo: desbloquear %s' % (a.usuario, a.usuario))
    restart_broker()


def cmd_desbloquear(a):
    reg = load_json(p('registro.json'))
    if a.usuario not in reg['usuarios']:
        die('no existe %s' % a.usuario)
    pw = new_password()
    run(['mosquitto_passwd', '-b', p('mosquitto', 'passwd'), a.usuario, pw])
    reg['usuarios'][a.usuario].pop('bloqueado', None)
    save_json(p('registro.json'), reg)
    apply_acl(reg)
    print('Usuario: %s\nClave:   %s' % (a.usuario, pw))
    log('%s habilitado con clave nueva (la anterior no sirve). Cargala en el teléfono.' % a.usuario)
    restart_broker()


def cmd_lista(a):
    reg = load_json(p('registro.json'))
    if not reg['usuarios']:
        print('(sin usuarios)')
    for user in sorted(reg['usuarios']):
        i = reg['usuarios'][user]
        extra = {'modulo': 'módulo %s%s' % (i.get('modulo'), ' + cámara' if i.get('camara') else ''),
                 'persona': 'rol %s%s' % (i.get('rol'), (' · órdenes a: ' + ', '.join(i['cmd'])) if i.get('cmd') else ''),
                 'servicio': 'servicio de solo lectura'}[i['tipo']]
        print('%-18s %s%s' % (user, extra, '  [BLOQUEADO]' if i.get('bloqueado') else ''))


def cmd_ca(a):
    with open(p('ca', 'ca.crt'), encoding='utf-8') as f:
        sys.stdout.write(f.read())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    s = sub.add_parser('init', help='crea la CA, el certificado del servidor y la configuración')
    s.add_argument('--ip', required=True, help='IP fija del servidor en la red de la casa (reservala en el router)')
    s.add_argument('--dns', action='append', default=[], help='nombre local opcional (casa.lan)')
    s.add_argument('--san', action='append', default=[], help='otro IP:x o DNS:y para el certificado')
    s.add_argument('--vpn-host', help='nombre del servidor en la VPN (casa.xxxx.ts.net): genera el Caddyfile')
    s.add_argument('--vpn-ip', help='IP del servidor en la VPN (100.x.y.z): Caddy escucha solo ahí')
    s.add_argument('--web-port', default='443')
    s.add_argument('--www', default='/var/www/casa', help='carpeta con los archivos de la app')
    s.set_defaults(f=cmd_init)
    s = sub.add_parser('renovar-servidor', help='vuelve a emitir el certificado del servidor con la misma CA')
    s.add_argument('--ip'); s.add_argument('--dns', action='append'); s.add_argument('--san', action='append')
    s.set_defaults(f=cmd_renovar_servidor)
    s = sub.add_parser('alta-modulo', help='usuario de un módulo; imprime mqtt_credenciales.h')
    s.add_argument('id'); s.add_argument('--camara', action='store_true', help='permite foto/video/audio (portero)')
    s.set_defaults(f=cmd_alta_modulo)
    s = sub.add_parser('alta-persona', help='usuario para la app de una persona')
    s.add_argument('nombre'); s.add_argument('--rol', choices=('completo', 'basico'), default='completo')
    s.add_argument('--cmd', help='(rol basico) módulos a los que puede dar órdenes, separados por coma')
    s.set_defaults(f=cmd_alta_persona)
    s = sub.add_parser('alta-servicio', help='usuario de solo lectura (avisos push)')
    s.add_argument('nombre'); s.set_defaults(f=cmd_alta_servicio)
    s = sub.add_parser('clave', help='cambia la clave de un usuario'); s.add_argument('usuario'); s.set_defaults(f=cmd_clave)
    s = sub.add_parser('baja', help='elimina un usuario (teléfono perdido, módulo retirado)'); s.add_argument('usuario'); s.set_defaults(f=cmd_baja)
    s = sub.add_parser('bloquear', help='corta ya a un usuario (teléfono perdido o robado); se puede deshacer'); s.add_argument('usuario'); s.set_defaults(f=cmd_bloquear)
    s = sub.add_parser('desbloquear', help='vuelve a habilitar un usuario con clave nueva'); s.add_argument('usuario'); s.set_defaults(f=cmd_desbloquear)
    s = sub.add_parser('lista', help='muestra usuarios y permisos'); s.set_defaults(f=cmd_lista)
    s = sub.add_parser('ca', help='imprime el certificado de la CA'); s.set_defaults(f=cmd_ca)
    a = ap.parse_args()
    if a.cmd == 'renovar-servidor':
        a.dns = a.dns or None
        a.san = a.san or None
    a.f(a)


if __name__ == '__main__':
    main()
