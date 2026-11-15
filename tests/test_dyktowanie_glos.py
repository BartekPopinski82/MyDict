"""Początek głosu z poziomu mikrofonu: zero odliczania niezależne od VAD."""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import dyktowanie_latencja as lat  # noqa: E402
from dyktowanie_latencja import LatencyTracker  # noqa: E402


class FakeClock:
    def __init__(self, start=1000.0):
        self.t = start

    def __call__(self):
        return self.t


def feed(tracker, start, seconds, level):
    """Podaje trackerowi poziom mikrofonu jak z callbacku: bloki 50 ms po 5×10 ms."""
    t = start
    end = start + seconds
    while t < end - 1e-9:
        t += 0.05
        tracker.mic_levels([level] * 5, t)
    return end


def read_csv(path):
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    return dict(zip(lines[0].split(";"), lines[1].split(";")))


def finish(tracker, clock, tmp_path_csv, *, vad_clock, audio_start, chars=3):
    """VAD zobaczył głos od ``audio_start`` (w chwili ``vad_clock``); potem tekst i wpisanie."""
    clock.t = vad_clock
    tracker.stream_event("speech_start", audio_start=audio_start)
    clock.t = vad_clock + 1.0
    tracker.stream_event("pass", delta=" Ala", asr=0.2, buffer=1.0, interval=None, step=0.45)
    clock.t = vad_clock + 1.1
    tracker.ui_text(chars, False, 0)
    tracker.typed(0, chars, vad_clock + 1.2, 0.001)
    clock.t = vad_clock + 2.0
    tracker.stream_event("speech_end", audio_end=vad_clock + 0.9)
    tracker.stream_event("final", delta="", asr=0.2, buffer=1.0)
    clock.t = vad_clock + 2.1
    tracker.ui_text(chars, True, 0)
    tracker.typed(0, chars, vad_clock + 2.2, 0.001)
    return read_csv(tmp_path_csv)


def test_voice_start_is_when_level_rises_not_when_vad_decides(monkeypatch, tmp_path, caplog):
    clock = FakeClock()
    monkeypatch.setattr(lat, "now", clock)
    tracker = LatencyTracker()
    tracker.configure(tmp_path / "l.csv")
    t = feed(tracker, 1000.0, 1.0, 0.002)         # cisza (szum tła)
    feed(tracker, t, 1.5, 0.05)                    # głos od 1001.000
    with caplog.at_level(logging.INFO, logger="dyktowanie.latencja"):
        row = finish(tracker, clock, tmp_path / "l.csv", vad_clock=1001.45, audio_start=1001.25)
    # VAD „zobaczył” głos 250 ms po jego realnym początku, program wykrył go po 450 ms
    assert abs(int(row["glos_przed_vad_ms"]) - 250) <= 10
    assert abs(int(row["glos_do_wykrycia_ms"]) - 450) <= 10
    # liczby „od głosu” zawierają te 450 ms, w przeciwieństwie do liczb od VAD
    assert abs(int(row["glos_do_1_znaku_ms"]) - (450 + 1200)) <= 10
    assert abs(int(row["wpisywanie_start_ms"]) - (200 + 1200)) <= 10   # kolumna starego zera (VAD)
    assert abs(int(row["b_start_vad_ms"]) - 450) <= 10
    assert "GŁOS START" in caplog.text and "OD GŁOSU (poziom mikrofonu)" in caplog.text


def test_click_before_speech_is_ignored(monkeypatch, tmp_path):
    clock = FakeClock()
    monkeypatch.setattr(lat, "now", clock)
    tracker = LatencyTracker()
    tracker.configure(tmp_path / "l.csv")
    t = feed(tracker, 1000.0, 1.0, 0.002)
    tracker.mic_levels([0.2], t + 0.01)            # pojedynczy impuls 10 ms (kliknięcie)
    t = feed(tracker, t + 0.01, 0.6, 0.002)        # znowu cisza
    feed(tracker, t, 1.5, 0.05)                    # głos od ok. 1001.61
    row = finish(tracker, clock, tmp_path / "l.csv", vad_clock=1002.0, audio_start=1001.75)
    assert 100 <= int(row["glos_przed_vad_ms"]) <= 160     # nie ~750 ms od kliknięcia


def test_threshold_adapts_to_noisy_room(monkeypatch, tmp_path):
    clock = FakeClock()
    monkeypatch.setattr(lat, "now", clock)
    tracker = LatencyTracker()
    tracker.configure(tmp_path / "l.csv")
    t = feed(tracker, 1000.0, 1.0, 0.010)          # głośny pokój: próg ≈ 0,03
    t = feed(tracker, t, 0.5, 0.02)                # trochę głośniej, ale to nie głos
    feed(tracker, t, 1.5, 0.08)                    # głos od 1001.5
    row = finish(tracker, clock, tmp_path / "l.csv", vad_clock=1001.9, audio_start=1001.7)
    assert abs(int(row["glos_przed_vad_ms"]) - 200) <= 10


def test_late_engine_still_matches_the_right_segment(monkeypatch, tmp_path):
    clock = FakeClock()
    monkeypatch.setattr(lat, "now", clock)
    tracker = LatencyTracker()
    tracker.configure(tmp_path / "l.csv")
    t = feed(tracker, 1000.0, 1.0, 0.002)
    t = feed(tracker, t, 0.8, 0.05)                # odcinek A: 1001.0–1001.8
    t = feed(tracker, t, 1.0, 0.002)               # cisza
    feed(tracker, t, 0.8, 0.05)                    # odcinek B: 1002.8–1003.6
    # silnik był zajęty i dopiero teraz (1004.0) przetwarza początek A
    row = finish(tracker, clock, tmp_path / "l.csv", vad_clock=1004.0, audio_start=1001.2)
    assert abs(int(row["glos_przed_vad_ms"]) - 200) <= 10


def test_level_never_crossing_threshold_falls_back_to_vad(monkeypatch, tmp_path):
    clock = FakeClock()
    monkeypatch.setattr(lat, "now", clock)
    tracker = LatencyTracker()
    tracker.configure(tmp_path / "l.csv")
    t = feed(tracker, 1000.0, 1.0, 0.001)
    feed(tracker, t, 1.5, 0.003)                   # cichy głos poniżej −48 dBFS
    row = finish(tracker, clock, tmp_path / "l.csv", vad_clock=1001.45, audio_start=1001.25)
    assert row["glos_start"] == "—" and row["glos_przed_vad_ms"] == ""
    assert "zero = VAD" in row["uwagi"]
    assert int(row["glos_do_1_znaku_ms"]) == int(row["wpisywanie_start_ms"])   # zero = VAD


def test_noise_from_long_before_is_not_taken_as_voice_start(monkeypatch, tmp_path):
    clock = FakeClock()
    monkeypatch.setattr(lat, "now", clock)
    tracker = LatencyTracker()
    tracker.configure(tmp_path / "l.csv")
    t = feed(tracker, 1000.0, 1.0, 0.002)
    feed(tracker, t, 3.0, 0.05)                    # ciągły hałas/mowa od 1001.0 do 1004.0
    row = finish(tracker, clock, tmp_path / "l.csv", vad_clock=1003.0, audio_start=1002.9)
    assert row["glos_start"] == "—"                # 1,9 s przed VAD > limit 1 s → zero = VAD
