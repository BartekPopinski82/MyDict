"""Dźwięk komputera (WASAPI loopback) — bez Windows i bez karty dźwiękowej."""

import sys
import time
import types
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import dyktowanie_asr  # noqa: E402
from dyktowanie_asr import LOOPBACK_AUTO, LOOPBACK_DEFAULT, LOOPBACK_PREFIX, Microphone, SystemAudio  # noqa: E402
from dyktowanie_core import SAMPLE_RATE  # noqa: E402


class FakeStream:
    def __init__(self, callback):
        self.callback = callback
        self.closed = False

    def stop_stream(self):
        pass

    def close(self):
        self.closed = True


def fake_library():
    module = types.SimpleNamespace(paFloat32=1, paContinue=0, paWASAPI=13)
    devices = [
        {"index": 7, "name": "Głośniki (Realtek) [Loopback]", "defaultSampleRate": 48000.0, "maxInputChannels": 2},
        {"index": 8, "name": "Słuchawki USB [Loopback]", "defaultSampleRate": 44100.0, "maxInputChannels": 2},
    ]

    class PyAudio:
        opened = []
        refuse = None

        def get_loopback_device_info_generator(self):
            yield from devices

        def get_default_wasapi_loopback(self):
            return devices[0]

        def open(self, **kwargs):
            if PyAudio.refuse and (kwargs["channels"], kwargs["rate"]) == PyAudio.refuse:
                raise OSError("Invalid number of channels")
            stream = FakeStream(kwargs["stream_callback"])
            stream.kwargs = kwargs
            PyAudio.opened.append(stream)
            return stream

        def terminate(self):
            pass

    module.PyAudio = PyAudio
    return module


def install(monkeypatch):
    library = fake_library()
    monkeypatch.setattr(SystemAudio, "_library", staticmethod(lambda: library))
    return library


def test_devices_list(monkeypatch):
    install(monkeypatch)
    assert SystemAudio.devices() == [
        LOOPBACK_AUTO,
        LOOPBACK_DEFAULT,
        LOOPBACK_PREFIX + "Głośniki (Realtek)",
        LOOPBACK_PREFIX + "Słuchawki USB",
    ]
    assert SystemAudio.is_system(LOOPBACK_DEFAULT)
    assert not SystemAudio.is_system("Mikrofon (Realtek)")
    assert not SystemAudio.is_system(None)


def test_no_library_no_devices(monkeypatch):
    monkeypatch.setattr(SystemAudio, "_library", staticmethod(lambda: None))
    assert SystemAudio.devices() == []


def test_capture_stereo_48k_to_16k_mono(monkeypatch):
    library = install(monkeypatch)
    received = []
    system = SystemAudio(lambda mono, end: received.append(mono))
    system.GAP = 10.0  # bez dosyłania ciszy w tym teście
    system.start(LOOPBACK_PREFIX + "Słuchawki USB")
    stream = library.PyAudio.opened[-1]
    assert stream.kwargs["input_device_index"] == 8 and stream.kwargs["rate"] == 44100
    system.stop()

    system.start(LOOPBACK_DEFAULT)
    stream = library.PyAudio.opened[-1]
    assert stream.kwargs["input_device_index"] == 7
    left = np.full(2400, 0.5, dtype=np.float32)
    right = np.full(2400, 0.1, dtype=np.float32)
    interleaved = np.stack([left, right], axis=1).reshape(-1)
    result = stream.callback(interleaved.tobytes(), 2400, {}, 0)
    assert result == (None, 0)
    system.stop()
    assert stream.closed
    audio = np.concatenate(received)
    assert len(audio) == 800  # 50 ms @ 16 kHz
    assert np.allclose(audio, 0.3, atol=1e-6)


def test_silence_is_filled_when_nothing_plays(monkeypatch):
    install(monkeypatch)
    received = []
    system = SystemAudio(lambda mono, end: received.append(mono.copy()))
    system.start(LOOPBACK_DEFAULT)
    time.sleep(0.5)
    system.stop()
    audio = np.concatenate(received) if received else np.zeros(0)
    seconds = len(audio) / SAMPLE_RATE
    assert 0.25 <= seconds <= 0.6
    assert not audio.any()


def test_microphone_routes_to_system_audio(monkeypatch):
    install(monkeypatch)
    monkeypatch.setattr(dyktowanie_asr.latency, "mic_levels", lambda *a, **k: None)
    queue = []
    microphone = Microphone(queue.append)
    microphone.start(LOOPBACK_DEFAULT)
    assert microphone.system is not None and microphone.stream is None
    stream = dyktowanie_asr.SystemAudio._library().PyAudio.opened[-1]
    tone = np.tile(np.full(2400, 0.2, dtype=np.float32)[:, None], (1, 2)).reshape(-1)
    stream.callback(tone.tobytes(), 2400, {}, 0)
    assert microphone.level > 0.1
    microphone.stop()
    assert microphone.system is None and microphone.level == 0.0
    chunks = [item[0] for item in queue]
    assert any(chunk.any() for chunk in chunks)


def block(level, frames=2400):
    return np.full(frames * 2, level, dtype=np.float32).tobytes()


def test_auto_listens_to_all_outputs_and_follows_the_one_that_plays(monkeypatch):
    library = install(monkeypatch)
    library.PyAudio.opened.clear()
    received = []
    system = SystemAudio(lambda mono, end: received.append(mono.copy()))
    system.GAP = 10.0
    system.start(LOOPBACK_AUTO)
    first, second = library.PyAudio.opened[-2:]
    assert {first.kwargs["input_device_index"], second.kwargs["input_device_index"]} == {7, 8}
    speakers = first if first.kwargs["input_device_index"] == 7 else second
    headset = second if speakers is first else first

    speakers.callback(block(0.0), 2400, {}, 0)           # głośniki: cisza
    headset.callback(block(0.3, 2205), 2205, {}, 0)       # gra słuchawka USB
    assert system._active.name == "Słuchawki USB"
    speakers.callback(block(0.3), 2400, {}, 0)            # to samo na drugim wyjściu: nie dublujemy
    assert system._active.name == "Słuchawki USB"
    audio = np.concatenate(received)
    assert audio.any() and np.allclose(audio[audio != 0], 0.3, atol=1e-3)
    total = sum(len(chunk) for chunk in received)
    assert total <= 800 * 2                                 # nie więcej niż dwa bloki 50 ms @ 16 kHz

    system._active.loud_at -= 5.0                           # słuchawka ucichła na dłużej
    speakers.callback(block(0.3), 2400, {}, 0)
    assert system._active.name == "Głośniki (Realtek)"
    system.stop()
    assert first.closed and second.closed


def test_unsupported_default_format_falls_back(monkeypatch):
    library = install(monkeypatch)
    library.PyAudio.opened.clear()
    library.PyAudio.refuse = (2, 48000)       # domyślny format głośników odrzucony
    try:
        system = SystemAudio(lambda mono, end: None)
        system.start(LOOPBACK_DEFAULT)
        stream = library.PyAudio.opened[-1]
        assert (stream.kwargs["channels"], stream.kwargs["rate"]) == (2, 44100)
        system.stop()
    finally:
        library.PyAudio.refuse = None


def test_callback_errors_are_reported_not_swallowed(monkeypatch, caplog):
    library = install(monkeypatch)
    library.PyAudio.opened.clear()

    def broken(mono, end):
        raise ValueError("kolejka pęknięta")

    system = SystemAudio(broken)
    system.start(LOOPBACK_DEFAULT)
    stream = library.PyAudio.opened[-1]
    with caplog.at_level("WARNING", logger="dyktowanie.asr"):
        assert stream.callback(block(0.3), 2400, {}, 0) == (None, 0)
    system.stop()
    assert "kolejka pęknięta" in caplog.text
