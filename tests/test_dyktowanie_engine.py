"""Test wątku roboczego na atrapie modelu — bez GPU, mikrofonu i Windows."""

import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import dyktowanie_asr  # noqa: E402
from dyktowanie_asr import DictationEngine, ModelInfo  # noqa: E402
from dyktowanie_core import SAMPLE_RATE, EnergyVAD, SessionText  # noqa: E402


SCRIPT = [" Szanowni", " Państwo,", " przesyłam", " raport."]


class FakeModel:
    """Udaje WhisperModel.transcribe: słowa co 0,4 s od początku bufora."""

    def __init__(self):
        self.calls = []

    def transcribe(self, audio, **options):
        self.calls.append(options)
        duration = len(audio) / SAMPLE_RATE
        words = [
            SimpleNamespace(start=i * 0.4, end=(i + 1) * 0.4, word=text)
            for i, text in enumerate(SCRIPT)
            if (i + 1) * 0.4 <= duration - 0.05
        ]
        segment = SimpleNamespace(
            text="".join(w.word for w in words), no_speech_prob=0.01, avg_logprob=-0.2,
            compression_ratio=1.2, words=words,
        )
        return iter([segment]), SimpleNamespace(language="pl")


class Recorder:
    def __init__(self):
        self.session = SessionText()
        self.ready_event = threading.Event()
        self.final_event = threading.Event()
        self.errors = []
        self.partials = 0

    def status(self, text, busy): pass
    def ready(self, info): self.ready_event.set()
    def failed(self, text): self.errors.append(text)
    def speech(self, active): pass
    def listening(self, active): pass

    def partial(self, delta, tentative):
        self.partials += 1
        self.session.partial(delta, tentative)

    def final(self, delta):
        self.session.final(delta)
        self.final_event.set()


def test_engine_end_to_end(monkeypatch):
    model = FakeModel()
    monkeypatch.setattr(dyktowanie_asr, "load_whisper",
                        lambda key, device, status: (model, ModelInfo(key, "fake", "cpu", "int8", "")))
    monkeypatch.setattr(dyktowanie_asr, "make_vad", EnergyVAD)
    monkeypatch.setattr(dyktowanie_asr.Microphone, "start", lambda self, name=None: None)
    monkeypatch.setattr(dyktowanie_asr.Microphone, "stop", lambda self: None)

    recorder = Recorder()
    engine = DictationEngine(recorder)
    engine.start()
    engine.load("turbo", "cpu")
    assert recorder.ready_event.wait(5)

    engine.start_listening()
    rng = np.random.default_rng(3)
    t = np.arange(int(1.8 * SAMPLE_RATE)) / SAMPLE_RATE
    voice = (0.25 * np.sin(2 * np.pi * 170 * t) * (1 + 0.3 * rng.standard_normal(len(t)))).astype(np.float32)
    audio = np.concatenate([np.zeros(SAMPLE_RATE // 2, np.float32), voice, np.zeros(SAMPLE_RATE, np.float32)])
    block = int(0.05 * SAMPLE_RATE)
    for index in range(0, len(audio), block):  # tempo zbliżone do mikrofonu
        engine.audio.put(audio[index:index + block])
        time.sleep(0.004)
    assert recorder.final_event.wait(5), recorder.errors
    engine.stop_listening()
    engine.shutdown()

    assert recorder.errors == []
    assert recorder.session.full() == "Szanowni Państwo, przesyłam raport."
    assert recorder.partials >= 1
    live = [c for c in model.calls if c["beam_size"] == 1]
    final = [c for c in model.calls if c["beam_size"] == 5]
    assert live and final  # szybkie przebiegi na żywo + jeden dokładny na końcu
    assert all(c["language"] == "pl" and c["word_timestamps"] for c in model.calls)
