"""Pomiar składników opóźnienia: co zjada czas od mowy do tekstu w aplikacji."""

import logging
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import dyktowanie_latencja as lat  # noqa: E402
from dyktowanie_core import SAMPLE_RATE, LiveStreamer, StreamConfig, Word  # noqa: E402
from dyktowanie_latencja import LatencyTracker  # noqa: E402
from dyktowanie_windows import TypingEngine, WindowInfo  # noqa: E402


class FakeClock:
    def __init__(self, start=1000.0):
        self.t = start

    def __call__(self):
        return self.t


def read_csv(path):
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    return dict(zip(lines[0].split(";"), lines[1].split(";")))


def run_utterance(tracker, clock, *, blocked=0.0, qt=0.2, asr_first=(0.4, 0.5)):
    """Wypowiedź o znanych czasach: dźwięk 1000.0, VAD +0,1 s, 1. tekst +1,8 s itd."""
    clock.t = 1000.1
    tracker.stream_event("speech_start", audio_start=1000.0)
    clock.t = 1000.9
    tracker.stream_event("pass", delta="", asr=asr_first[0], buffer=0.8, interval=None, step=0.45)
    clock.t = 1001.8
    tracker.stream_event("pass", delta=" Ala", asr=asr_first[1], buffer=1.7, interval=0.9, step=0.45)
    clock.t = 1001.8 + qt
    tracker.ui_text(4, False, 0)
    if blocked:
        tracker.blocked("wciśnięty Ctrl/Alt/Shift/Win", blocked)
    tracker.typed(0, 4, 1001.8 + qt + blocked + 0.1, 0.002)
    clock.t = 1004.0
    tracker.stream_event("speech_end", audio_end=1003.0)
    clock.t = 1005.0
    tracker.stream_event("final", delta=" ma kota", asr=0.6, buffer=3.3)
    clock.t = 1005.2
    tracker.ui_text(12, True, 0)
    tracker.typed(4, 12, 1005.6, 0.002)


def test_budget_shows_biggest_component(monkeypatch, tmp_path, caplog):
    clock = FakeClock()
    monkeypatch.setattr(lat, "now", clock)
    tracker = LatencyTracker()
    tracker.configure(tmp_path / "latencja.csv")
    tracker.model = "cuda · turbo · int8_float16"
    with caplog.at_level(logging.INFO, logger="dyktowanie.latencja"):
        run_utterance(tracker, clock, blocked=0.5)
    row = read_csv(tmp_path / "latencja.csv")
    # dźwięk 1000.0 → 1. znak 1002.6 (1001.8 + 0,2 Qt + 0,5 wstrzymanie + 0,1 wpisywanie)
    assert row["b_start_vad_ms"] == "100"
    assert row["b_start_asr_ms"] == "900"             # 0,4 + 0,5 s przebiegów do 1. tekstu
    assert row["b_start_silnik_czeka_ms"] == "800"    # 1,7 s od VAD do tekstu − 0,9 s ASR
    assert row["b_start_qt_ms"] == "200"
    assert row["b_start_wstrzymane_ms"] == "500"
    assert row["b_start_kolejka_wpisywania_ms"] == "100"
    assert row["b_start_najwiecej"].startswith("ASR")
    # koniec: dźwięk do 1003.0, VAD 1004.0, final 1005.0 (ASR 0,6 s), UI 1005.2, ostatni znak 1005.6
    assert row["b_koniec_cisza_ms"] == "1000"
    assert row["b_koniec_asr_final_ms"] == "600"
    assert row["b_koniec_silnik_zajety_ms"] == "400"
    assert row["b_koniec_najwiecej"].startswith("pauza")
    assert row["model"] == "cuda · turbo · int8_float16"
    assert row["przebiegi_wolniejsze_niz_krok"] == "1"  # tylko przebieg 0,5 s przekracza krok 0,45 s
    text = caplog.text
    for label in ("PRZEBIEG 1", "BILANS START", "BILANS KONIEC", "NAJWIĘCEJ", "DIAGNOSTYKA", "WSTRZYMANE"):
        assert label in text, label


def test_slow_pass_counter_uses_step(monkeypatch, tmp_path):
    clock = FakeClock()
    monkeypatch.setattr(lat, "now", clock)
    tracker = LatencyTracker()
    tracker.configure(tmp_path / "latencja.csv")
    run_utterance(tracker, clock, asr_first=(0.2, 0.5))
    assert read_csv(tmp_path / "latencja.csv")["przebiegi_wolniejsze_niz_krok"] == "1"  # 0,2 s mieści się w kroku


def test_per_char_lag_between_bar_and_window(monkeypatch, tmp_path):
    clock = FakeClock()
    monkeypatch.setattr(lat, "now", clock)
    tracker = LatencyTracker()
    tracker.configure(tmp_path / "latencja.csv")
    run_utterance(tracker, clock, qt=0.2)
    row = read_csv(tmp_path / "latencja.csv")
    # 1. porcja: przekazana 1002.0, wpisana 1002.1 → 100 ms; 2. porcja: 1005.2 → 1005.6 = 400 ms
    assert row["wpisywanie_lag_max_ms"] == "400"
    assert row["wpisywanie_lag_sr_ms"] == "250"
    assert row["sendinput_max_ms"] == "2.0"


def test_ui_block_and_render_cost_are_attributed(monkeypatch, tmp_path, caplog):
    clock = FakeClock()
    monkeypatch.setattr(lat, "now", clock)
    tracker = LatencyTracker()
    tracker.configure(tmp_path / "latencja.csv")
    clock.t = 1000.1
    tracker.stream_event("speech_start", audio_start=1000.0)
    clock.t = 1001.0
    tracker.ui_tick()
    clock.t = 1001.033
    tracker.ui_tick()                       # normalnie — bez ostrzeżenia
    clock.t = 1002.0
    with caplog.at_level(logging.INFO, logger="dyktowanie.latencja"):
        tracker.ui_tick()                   # 967 ms spóźnienia
        tracker.ui_text(3, False, 2)
        tracker.ui_cost(0.004, 0.8, 41, 5200)
    assert caplog.text.count("WĄTEK UI ZABLOKOWANY") == 1
    assert "odświeżanie transkrypcji 800 ms" in caplog.text
    clock.t = 1003.0
    tracker.stream_event("speech_end", audio_end=1002.5)
    tracker.stream_event("final", delta=" Ala", asr=0.2, buffer=2.0)
    tracker.ui_text(3, True, 0)
    tracker.typed(0, 3, 1003.1, 0.001)
    row = read_csv(tmp_path / "latencja.csv")
    assert row["ui_zablokowany_max_ms"] == "967"
    assert row["ui_odswiezanie_max_ms"] == "800"


def test_engine_loop_reports_audio_backlog(monkeypatch, tmp_path, caplog):
    clock = FakeClock()
    monkeypatch.setattr(lat, "now", clock)
    tracker = LatencyTracker()
    tracker.configure(tmp_path / "latencja.csv")
    clock.t = 1000.1
    tracker.stream_event("speech_start", audio_start=1000.0)
    with caplog.at_level(logging.INFO, logger="dyktowanie.latencja"):
        tracker.engine_loop(0.004, 0.02, 0.05)     # zdrowo — cisza w dzienniku
        assert "ZALEGŁOŚĆ" not in caplog.text
        tracker.engine_loop(0.004, 3.2, 3.1)       # model liczył 3 s, w tym czasie audio się kolejkowało
    assert "ZALEGŁOŚĆ AUDIO" in caplog.text
    clock.t = 1002.0
    tracker.stream_event("speech_end", audio_end=1001.5)
    tracker.stream_event("final", delta="", asr=0.1, buffer=1.0)
    tracker.ui_text(0, True, 0)
    row = read_csv(tmp_path / "latencja.csv")
    assert row["kolejka_audio_max_ms"] == "3100"


def test_old_csv_layout_is_moved_aside(monkeypatch, tmp_path):
    csv_path = tmp_path / "latencja.csv"
    csv_path.write_text("nr;data\n1;2026-10-01\n", encoding="utf-8-sig")
    clock = FakeClock()
    monkeypatch.setattr(lat, "now", clock)
    tracker = LatencyTracker()
    tracker.configure(csv_path)
    run_utterance(tracker, clock)
    assert list(tmp_path.glob("latencja_stary_*.csv"))
    assert read_csv(csv_path)["nr"] == "1"


# --- rdzeń: przebiegi niosą bufor i odstęp -------------------------------------------------

class ThresholdVAD:
    frame = 512

    def reset(self):
        pass

    def __call__(self, samples):
        frames = samples.reshape(-1, self.frame)
        return (np.abs(frames).max(axis=1) > 0.05).astype(np.float32)


def test_core_pass_events_carry_buffer_and_interval():
    def transcribe(audio, prompt, final):
        seconds = len(audio) / SAMPLE_RATE
        return [Word(i * 0.3, (i + 1) * 0.3, " x") for i in range(int(seconds / 0.3))]

    streamer = LiveStreamer(transcribe, ThresholdVAD(), StreamConfig(preroll=0.0, onset_frames=1))
    events = []
    streamer.on_timing = lambda kind, **data: events.append((kind, data))
    speech = np.full(int(SAMPLE_RATE * 2.0), 0.2, dtype=np.float32)
    silence = np.zeros(SAMPLE_RATE, dtype=np.float32)
    audio = np.concatenate([speech, silence])
    for index in range(0, len(audio), 800):
        streamer.push(audio[index:index + 800])
        streamer.step()
    passes = [data for kind, data in events if kind == "pass"]
    finals = [data for kind, data in events if kind == "final"]
    assert len(passes) >= 2
    assert all(p["buffer"] > 0 and p["step"] == 0.45 for p in passes)
    assert passes[0]["interval"] is None
    assert all(p["interval"] is not None and p["interval"] >= 0 for p in passes[1:])
    assert finals and finals[0]["buffer"] > 0


def test_core_short_noise_does_not_break_finalize():
    streamer = LiveStreamer(lambda audio, prompt, final: [], ThresholdVAD(),
                            StreamConfig(preroll=0.0, onset_frames=1, min_speech=0.5))
    events = []
    streamer.on_timing = lambda kind, **data: events.append((kind, data))
    blip = np.full(1024, 0.2, dtype=np.float32)       # 64 ms „kaszlnięcia”
    audio = np.concatenate([blip, np.zeros(SAMPLE_RATE, dtype=np.float32)])
    out = []
    for index in range(0, len(audio), 800):
        streamer.push(audio[index:index + 800])
        out += streamer.step()
    assert ("speech", False) in out
    final = [data for kind, data in events if kind == "final"][0]
    assert final["asr"] is None and final["buffer"] is None


# --- wpisywanie: wstrzymanie i czas SendInput ----------------------------------------------

def make_typer(modifiers):
    window = WindowInfo(1, 111, "Dokument1 - Word", "WINWORD.EXE", "OpusApp")
    typed, blocked = [], []
    engine = TypingEngine(inject=lambda text: True, window_provider=lambda: window,
                          modifiers=lambda: modifiers["down"])
    engine.smooth = False
    engine.on_typed = lambda *args: typed.append(args)
    engine.on_blocked = lambda reason, seconds: blocked.append((reason, seconds))
    return engine, typed, blocked


def test_typer_reports_blocked_time_and_sendinput_duration():
    modifiers = {"down": True}
    engine, typed, blocked = make_typer(modifiers)
    engine.set_text("Ala")
    assert not engine.step()                 # Ctrl wciśnięty — czeka
    time.sleep(0.05)
    assert not engine.step()                 # nadal czeka, to samo wstrzymanie
    assert not blocked
    modifiers["down"] = False
    assert engine.step()
    assert len(blocked) == 1
    reason, seconds = blocked[0]
    assert "Ctrl" in reason and seconds >= 0.05
    start, end, _stamp, inject = typed[0]
    assert (start, end) == (0, 3) and inject >= 0.0


def test_typer_without_blocking_reports_nothing():
    engine, typed, blocked = make_typer({"down": False})
    engine.set_text("Ala")
    assert engine.step()
    assert not blocked and len(typed) == 1
