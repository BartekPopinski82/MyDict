"""Pomiar latencji: znaczniki czasu od dźwięku do wpisania ostatniego znaku."""

import logging

import numpy as np

from dyktowanie_core import SAMPLE_RATE, LiveStreamer, SessionText, StreamConfig, Word
from dyktowanie_latencja import LatencyTracker


class ThresholdVAD:
    frame = 512

    def reset(self):
        pass

    def __call__(self, samples):
        frames = samples.reshape(-1, self.frame)
        return (np.abs(frames).max(axis=1) > 0.05).astype(np.float32)


def fake_transcribe(audio, prompt, final):
    seconds = len(audio) / SAMPLE_RATE
    words = ["Ala", "ma", "kota", "i", "psa"]
    count = min(len(words), int(seconds / 0.3))
    return [Word(i * 0.3, (i + 1) * 0.3, " " + w) for i, w in enumerate(words[:count])]


def test_latency_timestamps_for_one_utterance(caplog, tmp_path):
    tracker = LatencyTracker()
    tracker.configure(tmp_path / "latencja.csv")
    streamer = LiveStreamer(fake_transcribe, ThresholdVAD(), StreamConfig(preroll=0.0, onset_frames=1))
    streamer.on_timing = tracker.stream_event
    session = SessionText()

    silence = np.zeros(int(SAMPLE_RATE * 0.5), dtype=np.float32)
    speech = np.full(int(SAMPLE_RATE * 2.0), 0.2, dtype=np.float32)
    audio = np.concatenate([silence, speech, np.zeros(SAMPLE_RATE, dtype=np.float32)])
    clock0 = 1000.0
    block = 800
    typed_to = 0
    with caplog.at_level(logging.INFO, logger="dyktowanie.latencja"):
        for index in range(0, len(audio), block):
            end_time = clock0 + (index + block) / SAMPLE_RATE
            streamer.push(audio[index:index + block], end_time=end_time)
            for event in streamer.step():
                if event[0] == "partial":
                    session.partial(event[1], event[2])
                    stable, tentative = session.split()
                    tracker.ui_text(len(stable), False, len(tentative))
                elif event[0] == "final":
                    session.final(event[1])
                    tracker.ui_text(len(session.stable()), True)
            stable = session.stable()
            if len(stable) > typed_to:  # „wpisujemy” po znaku
                tracker.typed(typed_to, typed_to + 1)
                typed_to += 1
        while typed_to < len(session.stable()):
            tracker.typed(typed_to, typed_to + 1)
            typed_to += 1

    text = caplog.text
    for label in ("DŹWIĘK START", "VAD START", "1. PEWNY TEKST", "DŹWIĘK KONIEC", "OKIENKO START",
                  "VAD KONIEC", "FINAL", "WPISYWANIE START", "WPISYWANIE KONIEC", "PODSUMOWANIE"):
        assert label in text, label
    rows = (tmp_path / "latencja.csv").read_text(encoding="utf-8-sig").splitlines()
    assert len(rows) == 2
    header, row = rows[0].split(";"), rows[1].split(";")
    data = dict(zip(header, row))
    assert data["znaki"] == str(len(session.stable()))
    # Mowa trwa 2,0 s; VAD działa w ramkach 32 ms, więc dopuszczamy dwie ramki błędu.
    assert abs(int(data["dlugosc_mowy_ms"]) - 2000) <= 64
    assert data["dzwiek_start"].endswith(("40.480", "40.500")) or data["dzwiek_start"]


def test_unfinished_typing_is_reported_on_new_session(caplog):
    tracker = LatencyTracker()
    with caplog.at_level(logging.INFO, logger="dyktowanie.latencja"):
        tracker.stream_event("speech_start", audio_start=None)
        tracker.stream_event("speech_end", audio_end=None)
        tracker.stream_event("final", delta=" Tekst", asr=0.2)
        tracker.ui_text(6, True)
        tracker.typed(0, 2)
        tracker.reset_session()
    assert "nie wpisano do końca" in caplog.text


def test_exact_timestamps_and_overlay_to_app_gap(tmp_path, monkeypatch, caplog):
    """Sztuczny zegar: sprawdzamy każdą wartość w CSV co do milisekundy."""
    import dyktowanie_latencja

    times = iter([10.150, 10.900, 12.700, 13.420])  # VAD start, 1. pewny tekst, VAD koniec, FINAL
    monkeypatch.setattr(dyktowanie_latencja, "now", lambda: next(times))
    tracker = LatencyTracker()
    tracker.configure(tmp_path / "latencja.csv")
    with caplog.at_level(logging.INFO, logger="dyktowanie.latencja"):
        tracker.stream_event("speech_start", audio_start=10.000)           # dźwięk start
        tracker.ui_text(0, False, tentative_len=4, t=10.600)                # okienko: szary tekst
        tracker.stream_event("pass", delta=" Ala", asr=0.120)              # 1. pewny tekst 10.900
        tracker.ui_text(4, False, tentative_len=3, t=10.910)                # okienko: biały tekst
        tracker.typed(0, 1, t=10.950)                                       # 1. znak w aplikacji
        tracker.typed(1, 4, t=11.000)
        tracker.stream_event("speech_end", audio_end=12.000)               # VAD koniec 12.700
        tracker.stream_event("final", delta=" ma kota.", asr=0.300)        # FINAL 13.420
        tracker.ui_text(13, True, t=13.430)                                 # okienko: final
        tracker.typed(4, 13, t=13.600)                                      # ostatni znak

    rows = (tmp_path / "latencja.csv").read_text(encoding="utf-8-sig").splitlines()
    data = dict(zip(rows[0].split(";"), rows[1].split(";")))
    assert data["vad_wykrycie_ms"] == "150"
    assert data["okienko_start_ms"] == "600"
    assert data["pierwszy_tekst_ms"] == "900"
    assert data["okienko_pewny_ms"] == "910"
    assert data["wpisywanie_start_ms"] == "950"
    assert data["okienko_do_aplikacji_ms"] == "350"      # szary tekst w pasku 350 ms przed aplikacją
    assert data["cisza_do_konca_ms"] == "700"
    assert data["final_po_koncu_ms"] == "1420"
    assert data["okienko_final_po_koncu_ms"] == "1430"
    assert data["wpisywanie_koniec_po_koncu_ms"] == "1600"
    assert data["czas_wpisywania_ms"] == "2650"
    assert data["dlugosc_mowy_ms"] == "2000"
    assert data["znaki"] == "13"
    assert data["przebiegi_asr"] == "1" and data["asr_sredni_ms"] == "120" and data["asr_final_ms"] == "300"
    assert data["uwagi"] == ""
    for label in ("OKIENKO START", "OKIENKO PEWNY", "OKIENKO FINAL", "okienko→aplikacja 350 ms"):
        assert label in caplog.text, label


def test_typing_before_final_completes_on_ui_final(tmp_path):
    """Gdy wszystko wpisano przed FINAL, wypowiedź zamyka się po FINAL w pasku."""
    tracker = LatencyTracker()
    tracker.configure(tmp_path / "latencja.csv")
    tracker.stream_event("speech_start", audio_start=1.0)
    tracker.ui_text(4, False, t=1.5)
    tracker.typed(0, 4, t=1.6)
    tracker.stream_event("speech_end", audio_end=2.0)
    tracker.stream_event("final", delta="", asr=0.1)
    assert not (tmp_path / "latencja.csv").exists()
    tracker.ui_text(4, True, t=2.8)
    rows = (tmp_path / "latencja.csv").read_text(encoding="utf-8-sig").splitlines()
    data = dict(zip(rows[0].split(";"), rows[1].split(";")))
    assert data["wpisywanie_koniec_po_koncu_ms"] == "-400"  # ostatni znak wpisany jeszcze w trakcie mowy


def test_two_utterances_get_separate_char_ranges(tmp_path):
    tracker = LatencyTracker()
    tracker.configure(tmp_path / "latencja.csv")
    for number, (start, text_end) in enumerate([(0.0, 5), (5.0, 11)], start=1):
        tracker.stream_event("speech_start", audio_start=start)
        tracker.stream_event("speech_end", audio_end=start + 1)
        tracker.stream_event("final", delta="x", asr=0.1)
        tracker.ui_text(text_end, True, t=start + 1.5)
    tracker.typed(0, 5, t=1.7)
    tracker.typed(5, 11, t=6.9)
    rows = (tmp_path / "latencja.csv").read_text(encoding="utf-8-sig").splitlines()
    first = dict(zip(rows[0].split(";"), rows[1].split(";")))
    second = dict(zip(rows[0].split(";"), rows[2].split(";")))
    assert (first["nr"], first["znaki"], first["wpisywanie_koniec_po_koncu_ms"]) == ("1", "5", "700")
    assert (second["nr"], second["znaki"], second["wpisywanie_koniec_po_koncu_ms"]) == ("2", "6", "900")
