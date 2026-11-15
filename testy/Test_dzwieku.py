"""Test przechwytywania dźwięku: mikrofon i „Dźwięk komputera” (WASAPI loopback).

Uruchom przez Test_dzwieku.bat. Test sam odtwarza krótki ton przez domyślne
głośniki i sprawdza, które wyjścia (loopback) i które mikrofony go słyszą.
Potem prosi o puszczenie Twojego nagrania i robi to samo na prawdziwym dźwięku.
Wynik jest wypisany w konsoli i zapisany w Test_dzwieku_wynik.txt (obok programu).
"""

from __future__ import annotations

import io
import math
import platform
import struct
import sys
import threading
import time
import wave
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
RESULT = HERE / "Test_dzwieku_wynik.txt"
LOUD = 0.0003          # tyle RMS uznajemy za „słychać”
lines: list[str] = []


def say(text: str = ""):
    print(text, flush=True)
    lines.append(text)


def save():
    try:
        RESULT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError:
        pass


def db(value: float) -> str:
    return f"{20 * math.log10(max(value, 1e-9)):6.1f} dBFS"


def tone_wav(seconds: float = 3.0, freq: float = 1000.0, level: float = 0.3) -> bytes:
    rate = 44100
    t = np.arange(int(rate * seconds)) / rate
    # ton z modulacją 4 Hz — łatwo odróżnić od szumu
    samples = level * np.sin(2 * math.pi * freq * t) * (0.6 + 0.4 * np.sign(np.sin(2 * math.pi * 4 * t)))
    pcm = (samples * 32767).astype("<i2").tobytes()
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(pcm)
    return buffer.getvalue()


def play_tone(seconds: float = 3.0):
    import winsound

    winsound.PlaySound(tone_wav(seconds), winsound.SND_MEMORY | winsound.SND_ASYNC)


def stop_tone():
    try:
        import winsound

        winsound.PlaySound(None, winsound.SND_PURGE)
    except Exception:
        pass


class Meter:
    def __init__(self, name: str):
        self.name = name
        self.blocks = 0
        self.peak = 0.0
        self.sum_sq = 0.0
        self.count = 0

    def add(self, data: np.ndarray):
        if not len(data):
            return
        self.blocks += 1
        self.peak = max(self.peak, float(np.max(np.abs(data))))
        self.sum_sq += float(np.sum(data * data))
        self.count += len(data)

    @property
    def rms(self) -> float:
        return math.sqrt(self.sum_sq / self.count) if self.count else 0.0


def capture_loopbacks(seconds: float) -> list[Meter]:
    import pyaudiowpatch as pyaudio

    pa = pyaudio.PyAudio()
    meters: list[Meter] = []
    streams = []
    try:
        for device in pa.get_loopback_device_info_generator():
            meter = Meter(device["name"].removesuffix(" [Loopback]"))
            channels = max(1, int(device["maxInputChannels"]))

            def callback(in_data, frames, info, status, meter=meter, channels=channels):
                data = np.frombuffer(in_data, dtype=np.float32)
                if channels > 1:
                    data = data[: len(data) - len(data) % channels].reshape(-1, channels).mean(axis=1)
                meter.add(data)
                return None, pyaudio.paContinue

            try:
                stream = pa.open(
                    format=pyaudio.paFloat32, channels=channels, rate=int(device["defaultSampleRate"]),
                    input=True, input_device_index=device["index"],
                    frames_per_buffer=int(device["defaultSampleRate"] * 0.05), stream_callback=callback,
                )
            except Exception as exc:
                say(f"    ! nie udało się otworzyć „{meter.name}”: {exc}")
                continue
            streams.append(stream)
            meters.append(meter)
        time.sleep(seconds)
    finally:
        for stream in streams:
            try:
                stream.stop_stream()
                stream.close()
            except Exception:
                pass
        pa.terminate()
    return meters


def capture_microphones(seconds: float) -> list[Meter]:
    import sounddevice as sd

    meters: list[Meter] = []
    streams = []
    seen = set()
    try:
        default_input = sd.default.device[0]
        hostapi = sd.query_devices(default_input)["hostapi"] if default_input is not None and default_input >= 0 else 0
    except Exception:
        hostapi = 0
    try:
        for index, device in enumerate(sd.query_devices()):
            if device["max_input_channels"] < 1 or device["hostapi"] != hostapi or device["name"] in seen:
                continue
            seen.add(device["name"])
            meter = Meter(device["name"])

            def callback(indata, frames, info, status, meter=meter):
                meter.add(np.array(indata[:, 0], dtype=np.float32))

            try:
                stream = sd.InputStream(device=index, samplerate=16000, channels=1, dtype="float32",
                                        blocksize=800, callback=callback)
                stream.start()
            except Exception as exc:
                say(f"    ! mikrofon „{device['name']}” się nie otwiera: {exc}")
                continue
            streams.append(stream)
            meters.append(meter)
        time.sleep(seconds)
    finally:
        for stream in streams:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass
    return meters


def report(title: str, meters: list[Meter], noise: dict[str, float] | None = None) -> list[Meter]:
    say(title)
    if not meters:
        say("    (brak urządzeń)")
    heard = []
    for meter in meters:
        verdict = "SŁYCHAĆ" if meter.rms >= LOUD else "cisza"
        extra = ""
        if noise is not None and meter.name in noise:
            extra = f" | tło przed tonem {db(noise[meter.name])}"
        say(f"    {verdict:8} {meter.name[:48]:48} bloki {meter.blocks:4d} | RMS {db(meter.rms)} | szczyt {db(meter.peak)}{extra}")
        if meter.rms >= LOUD:
            heard.append(meter)
    return heard


def main():
    say(f"BAPO-dyktowanie — test dźwięku, {time.strftime('%Y-%m-%d %H:%M:%S')}")
    say(f"Python {platform.python_version()} · {platform.platform()}")
    try:
        import sounddevice as sd

        say(f"sounddevice {sd.__version__}")
        default = sd.default.device
        say(f"Domyślne urządzenia sounddevice (wejście, wyjście): {default[0]}, {default[1]}")
    except Exception as exc:
        say(f"! sounddevice: {exc}")
    try:
        import pyaudiowpatch as pyaudio

        pa = pyaudio.PyAudio()
        say("PyAudioWPatch: działa")
        try:
            default_loopback = pa.get_default_wasapi_loopback()
            say(f"Domyślne wyjście (loopback): {default_loopback['name']}")
        except Exception as exc:
            say(f"! domyślne wyjście WASAPI: {exc}")
        say("Wyjścia dostępne do przechwycenia:")
        for device in pa.get_loopback_device_info_generator():
            say(f"    • {device['name']} — {int(device['defaultSampleRate'])} Hz, {device['maxInputChannels']} kan.")
        pa.terminate()
    except Exception as exc:
        say(f"! PyAudioWPatch: {exc}")
        say("  Bez tej biblioteki opcja „Dźwięk komputera” nie działa. Uruchom Diagnostyka.bat — doinstaluje ją.")
        save()
        return

    say()
    say("TEST 1 — dźwięk komputera (loopback): program gra ton i sprawdza, które wyjście go słyszy.")
    say("            Ton trwa 3 s, nie musisz nic robić. Zrób go głośniej, niż zwykle, jeśli masz wyciszone głośniki.")
    thread = threading.Thread(target=lambda: (time.sleep(0.7), play_tone(3.0)), daemon=True)
    thread.start()
    heard = report("  Wynik:", capture_loopbacks(4.5))
    stop_tone()
    if heard:
        say(f"  ✔ Ton usłyszały: {', '.join(m.name for m in heard)}.")
        say("    Ta pozycja (albo tryb „wszystkie wyjścia (automatycznie)”) zadziała w programie.")
    else:
        say("  ✘ Żadne wyjście nie usłyszało tonu. Sprawdź, czy w Windows w ogóle coś gra (ikona głośnika, miks głośności),")
        say("    czy aplikacja Sonar/Voicemeeter nie przekierowuje dźwięku poza Windows.")

    say()
    say("TEST 2 — czy mikrofon słyszy głośniki: 1,5 s ciszy (tło), potem ton 3 s.")
    quiet = capture_microphones(1.5)
    noise = {m.name: m.rms for m in quiet}
    thread = threading.Thread(target=lambda: play_tone(3.0), daemon=True)
    thread.start()
    time.sleep(0.2)
    heard_mic = report("  Wynik (ton z głośników przez mikrofony):", capture_microphones(3.0), noise)
    stop_tone()
    loud = [m for m in heard_mic if m.rms > noise.get(m.name, 0.0) * 3]
    if loud:
        say(f"  ✔ Głośniki słyszą: {', '.join(m.name for m in loud)}.")
    else:
        say("  ✘ Żaden mikrofon nie słyszy wyraźnie głośników (poziom nie rośnie ponad tło).")
        say("    Windows (Realtek, „Poprawa dźwięku”, tłumienie echa) potrafi wycinać to, co gra z głośników.")
        say("    Rozwiązanie: wyłącz „Ulepszenia dźwięku” mikrofonu albo użyj opcji „Dźwięk komputera”.")

    say()
    try:
        input("TEST 3 — Twoje nagranie. Przygotuj odtwarzacz, naciśnij Enter i od razu puść nagranie (8 s)… ")
    except EOFError:
        pass
    results: dict[str, list[Meter]] = {}
    thread = threading.Thread(target=lambda: results.setdefault("mic", capture_microphones(8.0)))
    thread.start()
    loop = capture_loopbacks(8.0)
    thread.join()
    report("  Wyjścia (dźwięk komputera):", loop)
    report("  Mikrofony:", results.get("mic", []))
    say("  Jeśli słychać tylko jedno wyjście z listy — wybierz je w programie albo użyj trybu automatycznego.")
    say()
    say(f"Wynik zapisano w: {RESULT}")
    save()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        save()
    except Exception as exc:  # test nie może po cichu paść
        say(f"! Błąd testu: {exc!r}")
        import traceback

        say(traceback.format_exc())
        save()
