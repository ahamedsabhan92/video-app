"""VoiceProvider backed by Kokoro-82M (ONNX, CPU). Small and fast; leaves the GPU to ComfyUI.

Model files (models/kokoro/): kokoro-v1.0.onnx + voices-v1.0.bin (scripts/install-models voice).
Output: 24 kHz mono WAV, a short silence trimmed/padded at the ends.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from app.providers.base import Progress, VoiceProvider

DEFAULT_VOICE = "af_heart"   # warm, clear US English female; good for kids' narration
LANGS = {"English": "en-us", "en": "en-us", "en-us": "en-us", "en-gb": "en-gb", "British English": "en-gb"}


class KokoroVoiceProvider(VoiceProvider):
    name = "kokoro"

    def __init__(self, model_dir: Path, speed: float = 0.95):
        self.model_dir, self.speed = model_dir, speed
        self._tts = None

    def _load(self):
        if self._tts is None:
            from kokoro_onnx import Kokoro  # imported lazily: only voice jobs pay for it
            model, voices = self.model_dir / "kokoro-v1.0.onnx", self.model_dir / "voices-v1.0.bin"
            if not model.exists() or not voices.exists():
                raise FileNotFoundError(f"Kokoro model files missing in {self.model_dir} "
                                        "(run: scripts/install-models voice)")
            self._tts = Kokoro(str(model), str(voices))
        return self._tts

    def voices(self) -> list[str]:
        return sorted(self._load().get_voices())

    def speak(self, text: str, out: Path, *, voice: str = DEFAULT_VOICE, language: str = "English",
              progress: Progress | None = None) -> Path:
        import soundfile as sf
        tts = self._load()
        if progress:
            progress(None, "speaking")
        samples, sr = tts.create(text, voice=voice, speed=self.speed, lang=LANGS.get(language, "en-us"))
        samples = _trim_silence(np.asarray(samples, dtype=np.float32), sr)
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".part.wav")
        sf.write(tmp, samples, sr, subtype="PCM_16")
        tmp.replace(out)
        return out

    def unload(self) -> None:
        self._tts = None


def _trim_silence(x: np.ndarray, sr: int, threshold: float = 0.01, keep_s: float = 0.05) -> np.ndarray:
    """Trim leading/trailing near-silence, keeping a few ms so consonants aren't clipped."""
    loud = np.flatnonzero(np.abs(x) > threshold)
    if loud.size == 0:
        return x
    keep = int(keep_s * sr)
    return x[max(0, loud[0] - keep): min(len(x), loud[-1] + keep)]
