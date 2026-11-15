"""Drugi silnik (whisper.cpp) — bez uruchamiania prawdziwego procesu."""

import hashlib
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import dyktowanie_sprzet as sprzet  # noqa: E402
import dyktowanie_whispercpp as wc  # noqa: E402
from dyktowanie_asr import LiveSettings  # noqa: E402
from dyktowanie_core import SAMPLE_RATE, LocalAgreement  # noqa: E402


def piece(word, dtw=-1, start=0.0, end=0.0):
    return {"word": word, "t_dtw": dtw, "start": start, "end": end}


def test_words_are_joined_from_pieces_with_dtw_times():
    segment = {"start": 0.0, "end": 2.0, "text": " Dzień dobry, to", "words": [
        piece("[_BEG_]", 0), piece(" D", 10), piece("zie", 14), piece("ń", 18),
        piece(" dobry", 60), piece(",", 102), piece(" to", 114), piece("[_TT_100]", 114),
    ]}
    words = wc.words_from_segment(segment)
    assert [w.text for w in words] == [" Dzień", " dobry,", " to"]
    assert words[0].end == pytest.approx(0.18)
    assert words[1].start == pytest.approx(0.18) and words[1].end == pytest.approx(1.02)
    assert words[2].start == pytest.approx(1.02) and words[2].end == pytest.approx(1.14)


def test_words_fall_back_to_token_times_without_dtw():
    segment = {"start": 0.0, "end": 1.0, "words": [piece(" raz", -1, 0.1, 0.4), piece(" dwa", -1, 0.5, 0.9)]}
    words = wc.words_from_segment(segment)
    assert [(w.text, w.start, w.end) for w in words] == [(" raz", 0.1, 0.4), (" dwa", 0.5, 0.9)]


def test_words_spread_evenly_when_server_gives_only_text():
    words = wc.words_from_segment({"start": 1.0, "end": 2.0, "text": " raz dwa"})
    assert [w.text for w in words] == [" raz", " dwa"]
    assert words[0].start == pytest.approx(1.0) and words[1].end == pytest.approx(2.0)


def test_dtw_times_keep_streaming_agreement_stable():
    """Dwa kolejne przebiegi z minimalnie różnymi czasami nie mogą gubić słów
    (to był błąd przy zwykłych czasach fragmentów)."""
    first = {"start": 0, "words": [piece(" Dzień", 34), piece(" dobry", 100), piece(" to", 112)]}
    second = {"start": 0, "words": [piece(" Dzień", 36), piece(" dobry", 102), piece(" to", 114),
                                    piece(" jest", 136), piece(" próba", 170)]}
    third = {"start": 0, "words": [piece(" Dzień", 34), piece(" dobry", 100), piece(" to", 112),
                                   piece(" jest", 136), piece(" próba", 168), piece(" dyktowania", 222)]}
    agreement = LocalAgreement()
    text = []
    for segment in (first, second, third):
        agreement.insert(wc.words_from_segment(segment))
        text += [w.text for w in agreement.flush()]
    text += [w.text for w in agreement.commit_all()]
    assert "".join(text) == " Dzień dobry to jest próba dyktowania"


@pytest.mark.parametrize("seconds,final,expected", [
    (1.0, False, 256), (3.0, False, 256), (6.0, False, 396), (6.0, True, 492), (23.9, False, 1291), (24.0, False, 0),
])
def test_audio_context_scales_with_buffer(seconds, final, expected):
    assert wc.audio_context(seconds, final) == expected


VULKAN_LOG = """
ggml_vulkan: Found 2 Vulkan devices:
ggml_vulkan: 0 = Intel(R) UHD Graphics 630 (Intel Corporation) | uma: 1 | fp16: 1 | bf16: 0 | warp size: 32
ggml_vulkan: 1 = AMD Radeon RX 6600 (AMD proprietary driver) | uma: 0 | fp16: 1 | bf16: 0 | warp size: 64
whisper_backend_init_gpu: device 0: Vulkan0 (type: 1)
whisper_backend_init_gpu: using Vulkan0 backend
"""


def test_server_log_reports_vulkan_devices_and_prefers_discrete_card():
    info = wc.parse_server_log(VULKAN_LOG)
    assert info.gpu and info.backend == "vulkan"
    assert info.devices == [(0, "Intel(R) UHD Graphics 630", True), (1, "AMD Radeon RX 6600", False)]
    assert info.device == "Intel(R) UHD Graphics 630"
    assert wc.pick_device(info) == 1


def test_server_log_without_gpu():
    info = wc.parse_server_log(
        "whisper_backend_init_gpu: device 0: CPU (type: 0)\nwhisper_backend_init_gpu: no GPU found\n"
    )
    assert not info.gpu and info.backend == "cpu"
    assert wc.pick_device(info) is None


def test_single_integrated_card_is_used_as_is():
    info = wc.parse_server_log(
        "ggml_vulkan: 0 = AMD Radeon(TM) Graphics (AMD) | uma: 1 | fp16: 1\n"
        "whisper_backend_init_gpu: using Vulkan0 backend\n"
    )
    assert info.gpu and wc.pick_device(info) is None


class FakeServer:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def infer(self, wav, fields):
        self.calls.append((len(wav), dict(fields)))
        return self.result


def test_transcriber_builds_request_and_filters_hallucinations():
    result = {"segments": [
        {"text": " Dzień dobry", "start": 0.0, "end": 1.0, "avg_logprob": -0.1, "no_speech_prob": 0.01,
         "words": [piece(" Dzień", 30), piece(" dobry", 90)]},
        {"text": " Napisy stworzone przez społeczność Amara.org", "start": 1.0, "end": 2.0,
         "avg_logprob": -0.2, "no_speech_prob": 0.0, "words": [piece(" Napisy", 150)]},
    ]}
    server = FakeServer(result)
    settings = LiveSettings(language="pl", hotwords="MyDict, BAPO")
    transcriber = wc.WhisperCppTranscriber(server, settings)
    words = transcriber(np.zeros(SAMPLE_RATE * 2, dtype=np.float32), "poprzednie zdanie", True)
    assert [w.text for w in words] == [" Dzień", " dobry"]
    size, fields = server.calls[0]
    assert fields["language"] == "pl" and fields["beam_size"] == str(settings.final_beam)
    assert fields["prompt"] == "MyDict, BAPO poprzednie zdanie"
    assert fields["temperature_inc"] == "0.0" and fields["audio_ctx"] == "292"
    assert transcriber.last_stats["rejected"] == 1 and transcriber.last_stats["words"] == 2
    transcriber(np.zeros(SAMPLE_RATE * 2, dtype=np.float32), "", False)
    assert server.calls[1][1]["beam_size"] == str(settings.live_beam) and "prompt" in server.calls[1][1]


def test_short_audio_is_padded_and_silence_is_skipped():
    server = FakeServer({"segments": []})
    transcriber = wc.WhisperCppTranscriber(server, LiveSettings())
    assert transcriber(np.zeros(100, dtype=np.float32), "", False) == []
    assert not server.calls                        # poniżej 0,1 s nie pytamy silnika
    transcriber(np.zeros(SAMPLE_RATE // 2, dtype=np.float32), "", False)
    assert server.calls[0][0] >= int(wc.MIN_AUDIO_S * SAMPLE_RATE) * 2   # 16-bit PCM


def test_model_download_uses_checksum_from_repository(tmp_path, monkeypatch):
    monkeypatch.setattr(wc, "models_dir", lambda: tmp_path)
    digest = hashlib.sha256(b"model").hexdigest()
    seen = {}

    def fetch(url, headers=None):
        return [{"path": "README.md"}, {"path": "ggml-small-q5_1.bin", "lfs": {"oid": digest}}]

    def fetch_file(url, destination, *, sha256, progress, cancel):
        seen.update(url=url, sha256=sha256)
        destination.write_bytes(b"x" * 2_000_000)
        return destination

    path = wc.download_model("small", fetch=fetch, fetch_file=fetch_file)
    assert path == tmp_path / "ggml-small-q5_1.bin" and wc.model_cached("small")
    assert seen["sha256"] == digest and seen["url"].endswith("/resolve/main/ggml-small-q5_1.bin")
    assert not wc.model_cached("turbo")


# -- dobór silnika do sprzętu ---------------------------------------------------------


@pytest.mark.parametrize("cuda,cards,engine,backend,model", [
    (1, ["NVIDIA GeForce RTX 3060"], True, "cuda", "turbo"),
    (0, ["AMD Radeon RX 6600"], True, "vulkan", "small"),
    (0, ["Intel(R) UHD Graphics 630"], True, "vulkan", "small"),
    (0, ["NVIDIA GeForce GTX 1050"], True, "vulkan", "small"),      # NVIDIA bez działającego CUDA
    (0, ["Apple Silicon"], True, "metal", "turbo"),
    (0, ["AMD Radeon RX 6600"], False, "cpu", "small"),             # kopia programu bez drugiego silnika
    (0, [], True, "cpu", "small"),
])
def test_engine_choice(cuda, cards, engine, backend, model):
    hardware = sprzet.detect(cuda, cards, vram_mb=8192, second_engine=engine)
    assert hardware.backend == backend
    assert hardware.recommended_model == model
    if backend in ("vulkan", "metal"):
        assert "karta" in hardware.summary
