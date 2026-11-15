"""Sprawdza KAŻDE wejście audio: czy się otwiera i czy przychodzi z niego dźwięk.

Uruchom plikiem Test_Mikrofony.bat i w trakcie testu mów do mikrofonu.
Wynik zapisuje też do Test_Mikrofony.txt (obok tego pliku) - wklej go do Claude.
"""
import sys
import time
from pathlib import Path

import numpy as np
import sounddevice as sd

OUT = Path(__file__).with_name("Test_Mikrofony.txt")
lines = []


def say(text=""):
    print(text)
    lines.append(text)


say(f"sounddevice {sd.__version__}")
default_in = sd.default.device[0]
say(f"Domyślne wejście: {default_in} -> {sd.query_devices(default_in)['name'] if default_in is not None and default_in >= 0 else '-'}")
apis = sd.query_hostapis()
say("Interfejsy: " + ", ".join(f"{i}={a['name']}" for i, a in enumerate(apis)))
say()
say("MÓW TERAZ do mikrofonu - test trwa kilkadziesiąt sekund.")
say()

for index, dev in enumerate(sd.query_devices()):
    if dev["max_input_channels"] <= 0:
        continue
    api = apis[dev["hostapi"]]["name"]
    native = int(round(dev["default_samplerate"]))
    head = f"[{index}] {api} | {dev['name']} | kan.{dev['max_input_channels']} | {native} Hz"
    results = []
    for rate in dict.fromkeys([16000, native]):
        try:
            peaks = []

            def cb(indata, frames, t, status):
                peaks.append(float(np.max(np.abs(indata))))

            with sd.InputStream(device=index, samplerate=rate, channels=1, dtype="float32",
                                blocksize=int(rate * 0.05), callback=cb):
                time.sleep(1.5)
            peak = max(peaks) if peaks else 0.0
            results.append(f"{rate} Hz: OK, szczyt={peak:.4f}{'  <-- CISZA' if peak < 0.001 else ''}")
        except Exception as exc:
            results.append(f"{rate} Hz: BŁĄD {str(exc)[:90]}")
    say(head)
    for r in results:
        say("      " + r)

OUT.write_text("\n".join(lines), encoding="utf-8")
say()
say(f"Zapisano: {OUT}")
