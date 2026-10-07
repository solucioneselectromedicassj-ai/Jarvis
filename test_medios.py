import io
import os
import time
import wave

from casa import medios
from tests.helpers import HttpMedios, pcm
from tests.test_servidor import Base


class Medios(HttpMedios, Base):
    async def test_persona_sube_audio_se_guarda_wav_y_avisa(self):
        obs = await self.conectar("s-push")
        await obs.enviar(t="sub", topics=["casa/+/evento"])
        estado, r = await self.post("p-celu", self.claves["p-celu"], pcm(1.0))
        self.assertEqual(estado, 202)
        archivo = self.dir / "media" / "audio" / "celu" / f"{r['id']}.wav"
        with wave.open(str(archivo)) as w:
            self.assertEqual((w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()), (16000, 1, 2, 16000))
        ev = await obs.recibir()
        self.assertEqual((ev["topic"], ev["tipo"], ev["datos"]["id"]), ("casa/celu/evento", "audio", r["id"]))
        self.assertEqual(ev["datos"]["archivo"], f"celu/{r['id']}.wav")

    async def test_chunked_desde_un_esp32(self):
        estado, r = await self.post("m-mic", self.claves["m-mic"], pcm(2.0), chunked=True)
        self.assertEqual(estado, 202)
        self.assertTrue((self.dir / "media" / "audio" / "mic" / f"{r['id']}.wav").exists())

    async def test_permisos_y_autenticacion(self):
        self.assertEqual((await self.post("p-celu", "mala", pcm()))[0], 401)
        self.assertEqual((await self.post("p-noexiste", "x", pcm()))[0], 401)
        self.assertEqual((await self.post("p-jarvis", self.claves["p-jarvis"], pcm()))[0], 403)     # básico: nunca audio
        self.assertEqual((await self.post("m-riego", self.claves["m-riego"], pcm()))[0], 403)       # módulo sin cámara/mic
        self.assertEqual((await self.post("s-voz", self.claves["s-voz"], pcm()))[0], 403)
        self.reg.bloquear("p-celu")
        self.assertEqual((await self.post("p-celu", self.claves["p-celu"], pcm()))[0], 401)
        self.assertFalse((self.dir / "media" / "audio" / "celu").exists())

    async def test_formato_y_limites(self):
        u, k = "p-celu", self.claves["p-celu"]
        self.assertEqual((await self.post(u, k, pcm(), ct="audio/webm"))[0], 415)
        self.assertEqual((await self.post(u, k, pcm(), ct="audio/L16;rate=44100"))[0], 415)
        self.assertEqual((await self.post(u, k, pcm(0.05)))[0], 400)
        self.assertEqual((await self.post(u, k, pcm(1.0), largo=len(pcm(40.0))))[0], 413)   # lo anuncia en la cabecera: ni lo lee
        self.assertEqual((await self.post(u, k, pcm(31.0), chunked=True))[0], 413)
        self.assertEqual((await self.post(u, k, pcm(), metodo="GET"))[0], 404)
        self.assertEqual((await self.post(u, k, pcm(), ruta="/media/otra"))[0], 404)

    async def test_cuota_y_retencion(self):
        u, k = "p-celu", self.claves["p-celu"]
        self.srv.cuota_bytes = 100_000
        self.assertEqual((await self.post(u, k, pcm(1.0)))[0], 202)       # 32 KB
        self.assertEqual((await self.post(u, k, pcm(1.0)))[0], 202)
        self.assertEqual((await self.post(u, k, pcm(1.0)))[0], 202)
        self.assertEqual((await self.post(u, k, pcm(2.0)))[0], 507)       # no entra
        viejos = list((self.dir / "media" / "audio" / "celu").glob("*.wav"))
        for f in viejos:
            os.utime(f, (time.time() - 3 * 86400,) * 2)
        self.assertEqual((await self.post(u, k, pcm(2.0)))[0], 202)       # lo vencido se purga y entra
        self.assertEqual(len(list((self.dir / "media" / "audio" / "celu").glob("*.wav"))), 1)

    async def test_sin_carpeta_de_medios_esta_apagado(self):
        self.srv.media_dir = None
        self.assertEqual((await self.post("p-celu", self.claves["p-celu"], pcm()))[0], 404)

    async def test_servicio_responde_publica_respuesta_y_el_resto_no(self):
        celu = await self.conectar("p-celu")
        await celu.enviar(t="sub", topics=["casa/celu/respuesta"])
        voz = await self.conectar("s-voz")
        await voz.enviar(t="pub", topic="casa/celu/respuesta", id="x1", texto="listo")
        msg = await celu.recibir()
        self.assertEqual((msg["texto"], msg["id"]), ("listo", "x1"))
        otro = await self.conectar("s-push")
        await otro.enviar(t="pub", topic="casa/celu/respuesta", texto="falso")
        self.assertEqual((await otro.recibir())["codigo"], "permiso")
        mic = await self.conectar("m-mic")
        await mic.enviar(t="pub", topic="casa/celu/respuesta", texto="falso")
        self.assertEqual((await mic.recibir())["codigo"], "permiso")

    def test_wav_bien_formado(self):
        with wave.open(io.BytesIO(medios.wav(pcm(0.5)))) as w:
            self.assertEqual(w.getnframes(), 8000)
