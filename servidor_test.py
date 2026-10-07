#!/usr/bin/env python3
"""Pruebas de la parte pura de servidor/casa-servidor.py (permisos). Uso: python3 -I test/servidor_test.py"""
import importlib.util
import os
import sys

ruta = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'servidor', 'casa-servidor.py')
spec = importlib.util.spec_from_file_location('casa_servidor', ruta)
cs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cs)

ok = fail = 0


def t(nombre, cond):
    global ok, fail
    if cond:
        ok += 1
        print('  ok   ' + nombre)
    else:
        fail += 1
        print('  FAIL ' + nombre)


completo = {'tipo': 'persona', 'rol': 'completo', 'cmd': []}
basico = {'tipo': 'persona', 'rol': 'basico', 'cmd': ['sala']}
l = cs.acl_lines('p-pablo', completo)
t('persona completa puede mandar órdenes', 'topic write casa/+/cmd' in l)
t('persona básica solo a sus módulos', 'topic write casa/sala/cmd' in cs.acl_lines('p-hija', basico) and 'topic write casa/+/cmd' not in cs.acl_lines('p-hija', basico))
for info in (completo, basico, {'tipo': 'servicio'}, {'tipo': 'modulo', 'modulo': 'sala', 'camara': True}):
    bloq = dict(info, bloqueado=True)
    lineas = cs.acl_lines('u', bloq)
    t('bloqueado (%s) queda sin ningún permiso' % info['tipo'], lineas[0] == 'user u' and not any(x.startswith('topic') for x in lineas))
acl = cs.build_acl({'usuarios': {'p-pablo': dict(completo, bloqueado=True), 'p-hija': basico}})
t('el ACL completo mantiene a los demás', 'topic write casa/sala/cmd' in acl and 'topic read casa/#' not in acl)
print('\n%d ok, %d con fallas' % (ok, fail))
sys.exit(1 if fail else 0)
