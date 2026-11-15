"""Diagnostyka przebiegu ASR i monitor zasobów — na atrapach, bez GPU i Windows."""

import csv
import logging
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import dyktowanie_gpu  # noqa: E402
from dyktowanie_asr import LiveSettings, ModelInfo, WhisperTranscriber  # noqa: E402
from dyktowanie_core import SAMPLE_RATE, LiveStreamer, StreamConfig  # noqa: E402
from dyktowanie_gpu import ResourceMonitor, Sample, describe_reasons  # noqa: E402
from dyktowanie_latencja import PASS_CSV_COLUMNS, LatencyTracker  # noqa: E402


class FakeTokenizer:
    def encode(self, text, add_special_tokens=False):
        return SimpleNamespace(ids=text.split())


class FakeModel:
    """Zwraca dwa segmenty z dwóch okien (seek 0 i 3000); drugi z zapętleniem."""

    hf_tokenizer = FakeTokenizer()

    def __init__(self):
        self.calls = []

    def transcribe(self, audio, **options):
        self.calls.append(options)
        words = [SimpleNamespace(start=0.0, end=0.4, word=" Ala"), SimpleNamespace(start=0.4, end=0.8, word=" ma")]
        good = SimpleNamespace(
            text=" Ala ma", words=words, tokens=[1, 2, 3], seek=0, start=0.0, end=0.8,
            avg_logprob=-0.2, compression_ratio=1.1, no_speech_prob=0.01, temperature=0.0,
        )
        loop = SimpleNamespace(
            text=" i i i i i i", words=[], tokens=list(range(300)), seek=3000, start=30.0, end=31.0,
            avg_logprob=-1.4, compression_ratio=3.5, no_speech_prob=0.02, temperature=0.0,
        )
        return iter([good, loop]), SimpleNamespace(language="pl")


def make_transcriber():
    info = ModelInfo("turbo", "large-v3-turbo", "cuda", "int8_float16", "")
    return WhisperTranscriber(FakeModel(), LiveSettings(hotwords="BAPO"), info)


def test_transcriber_stats_and_unchanged_options():
    transcriber = make_transcriber()
    words = transcriber(np.zeros(SAMPLE_RATE * 2, dtype=np.float32), "Poprzedni tekst", False)
    assert [w.text for w in words] == [" Ala", " ma"]            # zapętlony segment odrzucony
    options = transcriber.model.calls[0]
    assert options["beam_size"] == 1 and options["temperature"] == 0.0
    assert options["condition_on_previous_text"] is False and options["word_timestamps"] is True
    stats = transcriber.last_stats
    assert stats["segments"] == 2 and stats["rejected"] == 1 and stats["windows"] == 2
    assert stats["tokens"] == 303 and stats["tokens_per_s"] > 0
    assert stats["model"] == "large-v3-turbo" and stats["compute_type"] == "int8_float16"
    assert stats["prompt_chars"] == len("Poprzedni tekst") and stats["prompt_tokens"] == 2
    assert stats["hotwords_tokens"] == 1 and stats["audio_s"] == 2.0 and stats["final"] is False
    assert stats["segment_list"][1]["rejected"] is True
    transcriber(np.zeros(SAMPLE_RATE, dtype=np.float32), "", True)
    assert transcriber.model.calls[1]["beam_size"] == 5 and transcriber.last_stats["final"] is True


class ThresholdVAD:
    frame = 512

    def reset(self):
        pass

    def __call__(self, samples):
        return (np.abs(samples.reshape(-1, self.frame)).max(axis=1) > 0.05).astype(np.float32)


def test_new_audio_and_pass_log_and_csv(caplog, tmp_path):
    tracker = LatencyTracker()
    tracker.configure(tmp_path / "latencja.csv")
    tracker.resource_summary = lambda t0, t1: {
        "samples": 4, "util_min": 90.0, "util_avg": 95.0, "util_max": 100.0, "sm_min": 1800,
        "sm_max": 2100, "mem_mhz_min": 9000, "mem_mhz_max": 9500, "pstate": "P0", "power_max": 150.0,
        "temp_max": 65, "vram_max": 3000, "reasons": "brak", "cpu_proc_avg": 40.0,
        "cpu_proc_max": 100.0, "cpu_sys_max": 20.0,
    }
    transcriber = make_transcriber()
    streamer = LiveStreamer(transcriber, ThresholdVAD(), StreamConfig(preroll=0.0, onset_frames=1, step=0.45))
    streamer.on_timing = tracker.stream_event
    speech = np.full(int(SAMPLE_RATE * 2.0), 0.2, dtype=np.float32)
    audio = np.concatenate([np.zeros(SAMPLE_RATE // 2, dtype=np.float32), speech, np.zeros(SAMPLE_RATE, dtype=np.float32)])
    with caplog.at_level(logging.INFO, logger="dyktowanie.latencja"):
        block = 800
        for i in range(0, len(audio), block):
            streamer.push(audio[i:i + block], end_time=1000.0 + (i + block) / SAMPLE_RATE)
            streamer.step()
        streamer.step(force_final=True)
    text = caplog.text
    assert "PRZEBIEG 1 szczegóły" in text and "tokeny 303" in text and "okna 2" in text
    assert "zasoby (4 próbek)" in text and "GPU util 90/95/100" in text
    assert "FINAL szczegóły" in text
    rows = list(csv.DictReader(open(tmp_path / "przebiegi.csv", encoding="utf-8-sig"), delimiter=";"))
    assert rows and list(rows[0].keys()) == PASS_CSV_COLUMNS
    first = rows[0]
    assert first["tokeny"] == "303" and first["okna"] == "2" and float(first["gpu_util_max"]) == 100
    assert float(first["nowe_audio_s"]) > 0.3          # pierwszy przebieg: całe audio od startu
    assert any(r["przebieg"] == "final" for r in rows)
    later = [r for r in rows if r["przebieg"] not in ("1", "final")]
    assert later and all(0.3 < float(r["nowe_audio_s"]) < 0.7 for r in later)  # ~krok 0,45 s


def test_anomaly_flag(caplog, tmp_path):
    tracker = LatencyTracker()
    tracker.stream_event("speech_start", audio_start=None)
    stats = {"segments": 1, "segment_list": [{"text": " i i i i", "tokens": 400, "seek": 0}], "tokens": 400}
    with caplog.at_level(logging.INFO, logger="dyktowanie.latencja"):
        tracker.stream_event("pass", delta="", asr=7.5, buffer=4.0, interval=0.5, step=0.45,
                             started=1.0, ended=8.5, new_audio=0.45, stats=stats)
    assert "ANOMALIA" in caplog.text and "ANOMALIA segment 1" in caplog.text


class FakeGpu:
    name = "fake"
    device = "Fake GPU"

    def __init__(self):
        self.closed = False

    def read(self, sample):
        sample.util, sample.sm_mhz, sample.mem_mhz = 50.0, 1500, 9000
        sample.vram_mb, sample.power_w, sample.temp_c, sample.pstate, sample.reasons = 2000.0, 100.0, 60, "P2", 0x4

    def close(self):
        self.closed = True


class FakeCpu:
    name = "fake-cpu"

    def read(self, sample):
        sample.cpu_proc, sample.cpu_sys = 80.0, 30.0


def test_monitor_samples_summary_and_csv(tmp_path):
    monitor = ResourceMonitor(interval=0.02)
    monitor.gpu_factory = lambda interval: FakeGpu()
    monitor.cpu_factory = FakeCpu
    monitor.configure(tmp_path / "gpu.csv")
    monitor.start(gpu=True)
    t0 = time.perf_counter()
    time.sleep(0.3)
    t1 = time.perf_counter()
    summary = monitor.summary(t0, t1)
    monitor.stop()
    assert summary["samples"] >= 5 and summary["util_avg"] == 50.0 and summary["pstate"] == "P2"
    assert summary["reasons"] == "limit mocy (SW)" and summary["cpu_proc_max"] == 80.0
    rows = list(csv.DictReader(open(tmp_path / "gpu.csv", encoding="utf-8-sig"), delimiter=";"))
    assert len(rows) >= 5 and list(rows[0].keys()) == dyktowanie_gpu.CSV_COLUMNS
    assert rows[0]["zrodlo"] == "fake" and rows[0]["sm_mhz"] == "1500"


def test_monitor_without_gpu_and_short_pass(tmp_path):
    monitor = ResourceMonitor(interval=0.05)
    monitor.cpu_factory = FakeCpu
    monitor.start(gpu=False)
    time.sleep(0.2)
    now = time.perf_counter()
    summary = monitor.summary(now - 0.001, now)      # krótszy niż odstęp próbkowania
    monitor.stop()
    assert summary is None or summary["util_avg"] is None
    assert monitor.summary(0.0, 0.1) is None


def test_gpu_source_failure_is_harmless(monkeypatch):
    monitor = ResourceMonitor(interval=0.02)
    monitor.gpu_factory = lambda interval: None
    monitor.cpu_factory = FakeCpu
    monitor.start(gpu=True)
    time.sleep(0.1)
    monitor.stop()


def test_describe_reasons():
    assert describe_reasons(None) == "" and describe_reasons(0) == "brak"
    assert describe_reasons(0x1 | 0x20) == "bezczynność, termika (SW)"
