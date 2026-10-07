#!/usr/bin/env python3
"""Lista las palabras de la gramática de Jarvis que el modelo Vosk no conoce.

Uso:
  node jarvis/exportar_frases.js > frases.json
  python jarvis/verificar_vocabulario.py frases.json RUTA_AL_MODELO_VOSK

Las palabras que aparezcan acá (por ejemplo voseo como "prendé" o "regá") hay que cambiarlas
por sinónimos o por la forma sin acento/voseo que el modelo sí tiene. Se compara en minúsculas,
tal cual está escrita la palabra y también sin tildes.
"""
import json
import os
import sys
import unicodedata


def sin_tildes(s):
    return ''.join(c for c in unicodedata.normalize('NFD', s) if unicodedata.category(c) != 'Mn')


def palabras_modelo(ruta):
    for sub in ('graph/words.txt', 'words.txt', 'am/words.txt'):
        p = os.path.join(ruta, sub)
        if os.path.isfile(p):
            with open(p, encoding='utf-8') as f:
                return {l.split()[0].lower() for l in f if l.strip()}
    sys.exit('No encontré words.txt dentro de %s (probé graph/, raíz y am/).' % ruta)


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    with open(sys.argv[1], encoding='utf-8') as f:
        frases = json.load(f)
    modelo = palabras_modelo(sys.argv[2])
    modelo_sin = {sin_tildes(w) for w in modelo}
    faltan = {}
    for fr in frases:
        if fr == '[unk]':
            continue
        for w in fr.lower().split():
            if w not in modelo:
                faltan.setdefault(w, []).append(fr)
    if not faltan:
        print('Todas las palabras están en el modelo.')
        return
    print('%d palabras que el modelo no conoce:\n' % len(faltan))
    for w in sorted(faltan):
        pista = '  (existe sin tildes: usá "%s")' % sin_tildes(w) if sin_tildes(w) in modelo_sin and sin_tildes(w) in modelo else ''
        print('  %-14s en %d frases, ej: "%s"%s' % (w, len(faltan[w]), faltan[w][0], pista))
    sys.exit(1)


if __name__ == '__main__':
    main()
