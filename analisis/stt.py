"""Transcriptores de voz a texto. Plugins: se elige con STT=vosk|falso. Local primero: ningún audio sale de la PC."""
import json
import time
import wave
from pathlib import Path


class Vosk:
    """Vosk con el modelo chico en español (https://alphacephei.com/vosk/models, ~40 MB; usa unos cientos de MB de RAM).

    El modelo se carga recién con el primer audio y se descarga tras `inactivo_s` sin uso, para no ocupar RAM en una
    PC compartida de 4 GB. Biblioteca: `pip install vosk`.
    """

    def __init__(self, modelo: str, inactivo_s: int = 300):
        self.ruta, self.inactivo_s = modelo, inactivo_s
        self._modelo, self._uso = None, 0.0

    def transcribir(self, wav: Path) -> str:
        from vosk import KaldiRecognizer, Model, SetLogLevel
        if self._modelo is None:
            if not Path(self.ruta).is_dir():
                raise FileNotFoundError(f"No encuentro el modelo de Vosk en {self.ruta}")
            SetLogLevel(-1)
            self._modelo = Model(self.ruta)
        self._uso = time.monotonic()
        rec = KaldiRecognizer(self._modelo, 16000)
        with wave.open(str(wav), "rb") as w:
            if (w.getframerate(), w.getnchannels(), w.getsampwidth()) != (16000, 1, 2):
                raise ValueError("el audio tiene que ser WAV 16 kHz, mono, 16 bits")
            while datos := w.readframes(4000):
                rec.AcceptWaveform(datos)
        return json.loads(rec.FinalResult()).get("text", "").strip()

    def liberar_si_inactivo(self):
        if self._modelo is not None and time.monotonic() - self._uso > self.inactivo_s:
            self._modelo = None


class Falso:
    """Para probar sin modelo: devuelve lo que se le configure (texto fijo o función del archivo)."""

    def __init__(self, texto="abrí el riego 10 minutos"):
        self.texto = texto

    def transcribir(self, wav: Path) -> str:
        return self.texto(wav) if callable(self.texto) else self.texto

    def liberar_si_inactivo(self):
        pass


def crear(nombre: str, modelo: str = ""):
    if nombre == "vosk":
        return Vosk(modelo)
    if nombre == "falso":
        return Falso()
    raise SystemExit(f"STT desconocido: {nombre} (usá vosk o falso)")
