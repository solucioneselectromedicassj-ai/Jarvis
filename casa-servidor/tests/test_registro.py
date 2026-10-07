import tempfile
import unittest
from pathlib import Path

from casa.registro import Registro, coincide, hash_clave, permisos, permitido, verificar_clave


class Permisos(unittest.TestCase):
    def test_modulo_solo_lo_suyo(self):
        p = permisos({"tipo": "modulo", "modulo": "riego"})
        self.assertEqual(set(p["escribir"]), {"casa/riego/desc", "casa/riego/estado", "casa/riego/evento"})
        self.assertEqual(p["leer"], ["casa/riego/cmd", "casa/riego/respuesta"])
        self.assertFalse(permitido(p["escribir"], "casa/riego/online"))   # online lo escribe solo el servidor
        self.assertFalse(permitido(p["escribir"], "casa/luces/estado"))
        self.assertFalse(permitido(p["escribir"], "casa/riego/foto"))

    def test_modulo_camara(self):
        p = permisos({"tipo": "modulo", "modulo": "portero", "camara": True})
        for k in ("foto", "video", "audio/out"):
            self.assertTrue(permitido(p["escribir"], f"casa/portero/{k}"))
        self.assertTrue(permitido(p["leer"], "casa/portero/audio/in"))

    def test_completo(self):
        p = permisos({"tipo": "persona", "rol": "completo"})
        self.assertTrue(permitido(p["leer"], "casa/portero/foto"))
        self.assertTrue(permitido(p["escribir"], "casa/riego/cmd"))
        self.assertTrue(permitido(p["escribir"], "casa/portero/audio/in"))
        self.assertFalse(permitido(p["escribir"], "casa/riego/estado"))

    def test_basico_nunca_medios_y_cmd_solo_lista(self):
        p = permisos({"tipo": "persona", "rol": "basico", "cmd": ["riego"]})
        for k in ("estado", "evento", "desc", "online"):
            self.assertTrue(permitido(p["leer"], f"casa/x/{k}"))
        for k in ("foto", "video", "audio/out", "audio/in", "cmd"):
            self.assertFalse(permitido(p["leer"], f"casa/x/{k}"), k)
        self.assertTrue(permitido(p["escribir"], "casa/riego/cmd"))
        self.assertFalse(permitido(p["escribir"], "casa/porton/cmd"))
        self.assertFalse(permitido(p["escribir"], "casa/riego/audio/in"))

    def test_servicio_solo_lectura(self):
        p = permisos({"tipo": "servicio"})
        self.assertTrue(permitido(p["leer"], "casa/a/estado"))
        self.assertEqual(p["escribir"], [])

    def test_bloqueado_no_puede_nada(self):
        for info in ({"tipo": "persona", "rol": "completo"}, {"tipo": "modulo", "modulo": "a"}, {"tipo": "servicio"}):
            self.assertEqual(permisos({**info, "bloqueado": True}), {"leer": [], "escribir": []})

    def test_comodines(self):
        self.assertTrue(coincide("casa/+/estado", "casa/a/estado"))
        self.assertFalse(coincide("casa/+/estado", "casa/a/b/estado"))
        self.assertTrue(coincide("casa/#", "casa/a/audio/in"))
        self.assertFalse(coincide("casa/#", "otra/a"))
        self.assertFalse(coincide("casa/a", "casa/a/b"))
        self.assertFalse(coincide("casa/a/b", "casa/a"))
        self.assertFalse(coincide("casa/#/x", "casa/a/x"))   # # solo al final


class Claves(unittest.TestCase):
    def test_hash(self):
        h = hash_clave("secreta")
        self.assertNotIn("secreta", h)
        self.assertTrue(verificar_clave("secreta", h))
        self.assertFalse(verificar_clave("otra", h))
        self.assertFalse(verificar_clave("x", "basura"))
        self.assertNotEqual(hash_clave("secreta"), h)  # sal distinta

    def test_registro(self):
        with tempfile.TemporaryDirectory() as d:
            r = Registro(Path(d) / "u.json")
            u, c = r.alta("persona", "pablo", rol="completo")
            self.assertEqual(u, "p-pablo")
            self.assertNotIn(c, (Path(d) / "u.json").read_text())   # solo queda el hash
            with self.assertRaises(ValueError):
                r.alta("persona", "pablo", rol="completo")
            with self.assertRaises(ValueError):
                r.alta("modulo", "Mal Nombre")
            r.bloquear(u)
            self.assertTrue(r.cargar()["usuarios"][u]["bloqueado"])
            nueva = r.desbloquear(u)
            info = r.cargar()["usuarios"][u]
            self.assertNotIn("bloqueado", info)
            self.assertTrue(verificar_clave(nueva, info["hash"]))
            self.assertFalse(verificar_clave(c, info["hash"]))   # la clave vieja ya no sirve
            r.baja(u)
            with self.assertRaises(KeyError):
                r.baja(u)


if __name__ == "__main__":
    unittest.main()
