import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dyktowanie_core import max_new_tokens_for  # noqa: E402


def test_live_limit_grows_with_buffer():
    assert max_new_tokens_for(0.5) == 32
    assert max_new_tokens_for(3.0) == 40
    assert max_new_tokens_for(7.0) == 72
    assert max_new_tokens_for(10.0) == 96
    assert max_new_tokens_for(15.0) == 128
    assert max_new_tokens_for(30.0) == 128


def test_live_limit_never_below_minimum_or_above_cap():
    assert max_new_tokens_for(0.0) == 32
    assert max_new_tokens_for(-1.0) == 32
    assert max_new_tokens_for(1000.0) == 128


def test_final_limit_is_fixed_and_higher():
    assert max_new_tokens_for(0.5, final=True) == 192
    assert max_new_tokens_for(30.0, final=True) == 192


def test_limit_does_not_cut_normal_passes():
    # Maksima z dzisiejszego testu (tokeny w normalnych przebiegach wg długości bufora).
    for buffer_s, tokens in [(1, 17), (3, 24), (6, 41), (10, 65), (15, 89), (30, 120)]:
        assert max_new_tokens_for(buffer_s) >= tokens


# --- przerwanie ściętej pętli -------------------------------------------------

import numpy as np  # noqa: E402
from types import SimpleNamespace  # noqa: E402

from dyktowanie_asr import LiveSettings, ModelInfo, WhisperTranscriber  # noqa: E402
from dyktowanie_core import SAMPLE_RATE  # noqa: E402


class _Segments:
    """Generator segmentów, który liczy, ile z nich faktycznie pobrano (jak leniwy faster-whisper)."""

    def __init__(self, items):
        self.items = items
        self.pulled = 0

    def __iter__(self):
        for item in self.items:
            self.pulled += 1
            yield item


def _segment(text, tokens, compression, seek=0, words=()):
    return SimpleNamespace(
        text=text, words=[SimpleNamespace(start=0.0, end=0.3, word=w) for w in words], tokens=list(range(tokens)),
        seek=seek, start=0.0, end=1.0, avg_logprob=-0.1, compression_ratio=compression,
        no_speech_prob=0.01, temperature=0.0,
    )


class _Model:
    hf_tokenizer = None

    def __init__(self, items):
        self.segments = _Segments(items)

    def transcribe(self, audio, **options):
        self.options = options
        return self.segments, SimpleNamespace(language="pl")


def _transcriber(items):
    info = ModelInfo("small", "small", "cuda", "int8_float16", "")
    return WhisperTranscriber(_Model(items), LiveSettings(), info)


def test_segment_at_token_limit_is_rejected_even_with_low_compression_and_stops_decoding():
    # Bufor 2 s -> limit 32. Pierwszy segment dobił do limitu (ucięta pętla, kompresja 1,7 < 2,4).
    items = [_segment(" dziesięć dziesięć", 33, 1.7, words=[" dziesięć"]),
             _segment(" następne okno", 5, 1.0, seek=3000, words=[" następne"])]
    transcriber = _transcriber(items)
    words = transcriber(np.zeros(SAMPLE_RATE * 2, dtype=np.float32), "", False)
    assert words == []
    assert transcriber.model.options["max_new_tokens"] == 32
    assert transcriber.model.segments.pulled == 1          # drugiego okna nie dekodowano
    stats = transcriber.last_stats
    assert stats["rejected"] == 1 and stats["segment_list"][0]["hit_limit"] is True


def test_segment_below_limit_is_kept_and_decoding_continues():
    items = [_segment(" Ala ma", 3, 1.1, words=[" Ala", " ma"]),
             _segment(" kota", 2, 1.0, seek=3000, words=[" kota"])]
    transcriber = _transcriber(items)
    words = transcriber(np.zeros(SAMPLE_RATE * 2, dtype=np.float32), "", False)
    assert [w.text for w in words] == [" Ala", " ma", " kota"]
    assert transcriber.model.segments.pulled == 2
    assert transcriber.last_stats["rejected"] == 0
