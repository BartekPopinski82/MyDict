import sys
import threading
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dyktowanie_core import (  # noqa: E402
    SAMPLE_RATE,
    EnergyVAD,
    LiveStreamer,
    LocalAgreement,
    Resampler,
    SessionText,
    StreamConfig,
    Word,
    common_prefix_len,
    display_tail,
    is_hallucinated_segment,
    words_text,
)


def W(start, end, text):
    return Word(start, end, text)


# --- LocalAgreement -----------------------------------------------------------


def test_agreement_commits_only_confirmed_prefix():
    agreement = LocalAgreement()
    agreement.insert([W(0, .4, " Ala"), W(.4, .8, " ma")])
    assert agreement.flush() == []
    assert words_text(agreement.pending) == " Ala ma"

    agreement.insert([W(0, .4, " Ala"), W(.4, .8, " ma"), W(.8, 1.2, " kot")])
    assert words_text(agreement.flush()) == " Ala ma"
    assert words_text(agreement.pending) == " kot"

    agreement.insert([W(0, .4, " Ala"), W(.4, .8, " ma"), W(.8, 1.2, " kota.")])
    assert agreement.flush() == []  # "kot" → "kota." — jeszcze niepewne
    assert words_text(agreement.pending) == " kota."

    # Bez nowej hipotezy koniec wypowiedzi zatwierdza niepewny ogon.
    assert words_text(agreement.commit_all()) == " kota."
    assert words_text(agreement.committed) == " Ala ma kota."


def test_agreement_commit_all_after_final_insert():
    agreement = LocalAgreement()
    agreement.insert([W(0, .4, " Dzień")])
    agreement.flush()
    agreement.insert([W(0, .4, " Dzień"), W(.4, .9, " dobry.")])
    assert words_text(agreement.commit_all()) == " Dzień dobry."
    assert agreement.pending == []


def test_agreement_removes_repeated_ngram_after_trim():
    agreement = LocalAgreement()
    agreement.insert([W(0, .5, " Raz"), W(.5, 1.0, " dwa")])
    agreement.flush()
    agreement.insert([W(0, .5, " Raz"), W(.5, 1.0, " dwa")])
    agreement.flush()
    # Model powtarza "dwa" z początku nowego bufora — powtórka nie może się zdublować.
    agreement.insert([W(1.0, 1.4, " dwa"), W(1.4, 1.9, " trzy")])
    agreement.flush()
    agreement.insert([W(1.0, 1.4, " dwa"), W(1.4, 1.9, " trzy")])
    agreement.flush()
    assert words_text(agreement.committed) == " Raz dwa trzy"


# --- filtr halucynacji ----------------------------------------------------------


@pytest.mark.parametrize("text", [
    "Napisy stworzone przez społeczność Amara.org",
    " Dziękuję za obejrzenie!",
    "Subskrybuj kanał",
])
def test_hallucinations_are_dropped(text):
    assert is_hallucinated_segment(text)


def test_real_speech_is_kept():
    assert not is_hallucinated_segment("Dziękuję, to wszystko na dziś.", 0.1, -0.3, 1.4)
    assert is_hallucinated_segment("cokolwiek", no_speech_prob=0.9, avg_logprob=-1.5)
    assert is_hallucinated_segment("i i i i i i", compression_ratio=3.1)


# --- strumieniowanie --------------------------------------------------------------


class ThresholdVAD:
    frame = 512
    name = "test"

    def reset(self):
        pass

    def __call__(self, samples):
        count = len(samples) // self.frame
        frames = samples[: count * self.frame].reshape(count, self.frame)
        return (np.sqrt(np.mean(frames ** 2, axis=1)) > 0.05).astype(np.float32)


SCRIPT = [" Ala", " ma", " kota,", " a", " kot", " ma", " Alę."]


def speech(seconds):
    rng = np.random.default_rng(0)
    return (rng.standard_normal(int(seconds * SAMPLE_RATE)) * 0.3).astype(np.float32)


def silence(seconds):
    return np.zeros(int(seconds * SAMPLE_RATE), dtype=np.float32)


class FakeWhisper:
    """Zwraca słowa, które „zmieściły się” w buforze; ostatnie bywa przekręcone."""

    def __init__(self, script, word_seconds=0.4, start=0.0):
        self.words = [
            W(start + i * word_seconds, start + (i + 1) * word_seconds, text)
            for i, text in enumerate(script)
        ]
        self.streamer = None
        self.calls = []

    def __call__(self, audio, prompt, final):
        offset = self.streamer.audio_offset + self.base
        duration = len(audio) / SAMPLE_RATE
        visible = [w for w in self.words if w.start >= offset - 0.05 and w.end <= offset + duration - 0.05]
        result = [W(w.start - offset, w.end - offset, w.text) for w in visible]
        if result and not final and len(self.calls) % 2 == 0:
            last = result[-1]
            result[-1] = W(last.start, last.end, last.text.rstrip(".,") + "…")
        self.calls.append((round(duration, 2), prompt, final))
        return result


def run_stream(streamer, audio, block=0.1):
    events = []
    step = int(block * SAMPLE_RATE)
    for index in range(0, len(audio), step):
        streamer.push(audio[index:index + step])
        events.extend(streamer.step())
    return events


def test_streamer_emits_partials_then_final_text():
    fake = FakeWhisper(SCRIPT)
    streamer = LiveStreamer(fake, ThresholdVAD(), StreamConfig(preroll=0.0, onset_frames=1))
    fake.streamer = streamer
    fake.base = 0.0
    audio = np.concatenate([speech(len(SCRIPT) * 0.4), silence(1.2)])
    events = run_stream(streamer, audio)

    kinds = [event[0] for event in events]
    assert kinds[0] == "speech" and kinds[-1] == "speech"
    assert "partial" in kinds and kinds.count("final") == 1

    session = SessionText()
    stable_history = []
    for event in events:
        if event[0] == "partial":
            session.partial(event[1], event[2])
            stable_history.append(session.stable())
        elif event[0] == "final":
            session.final(event[1])
    assert session.full() == "Ala ma kota, a kot ma Alę."
    # Pewny tekst tylko rośnie — nigdy się nie cofa.
    for before, after in zip(stable_history, stable_history[1:]):
        assert after.startswith(before)
    # Coś zostało zatwierdzone jeszcze przed końcem wypowiedzi (efekt „na żywo”).
    assert any(len(text) > 0 for text in stable_history)
    assert fake.calls[-1][2] is True  # ostatni przebieg = końcowy, dokładniejszy


def test_streamer_ignores_short_noise_and_silence():
    calls = []
    streamer = LiveStreamer(lambda a, p, f: calls.append(1) or [], ThresholdVAD(),
                            StreamConfig(onset_frames=3))
    audio = np.concatenate([silence(1.0), speech(0.05), silence(1.0)])
    events = run_stream(streamer, audio)
    assert calls == []
    assert [e for e in events if e[0] in ("partial", "final")] == []


def test_streamer_force_final_on_stop():
    fake = FakeWhisper(SCRIPT[:3])
    streamer = LiveStreamer(fake, ThresholdVAD(), StreamConfig(preroll=0.0, onset_frames=1))
    fake.streamer = streamer
    fake.base = 0.0
    run_stream(streamer, speech(1.3))
    events = streamer.step(force_final=True)
    finals = [e for e in events if e[0] == "final"]
    assert len(finals) == 1
    assert not streamer.in_speech


def test_streamer_trims_long_utterance_and_uses_prompt():
    script = [f" słowo{i}." if i % 5 == 4 else f" słowo{i}" for i in range(60)]
    fake = FakeWhisper(script, word_seconds=0.4)
    config = StreamConfig(preroll=0.0, onset_frames=1, trim_after=6.0, max_buffer=10.0)
    streamer = LiveStreamer(fake, ThresholdVAD(), config)
    fake.streamer = streamer
    fake.base = 0.0
    audio = np.concatenate([speech(len(script) * 0.4), silence(1.0)])
    session = SessionText()
    max_buffer = 0.0
    step = int(0.1 * SAMPLE_RATE)
    for index in range(0, len(audio), step):
        streamer.push(audio[index:index + step])
        max_buffer = max(max_buffer, streamer.buffer_seconds)
        for event in streamer.step():
            if event[0] == "partial":
                session.partial(event[1], event[2])
            elif event[0] == "final":
                session.final(event[1])
    assert max_buffer <= config.max_buffer + 0.5
    assert session.full() == "".join(script).strip()
    assert any(prompt for _, prompt, _ in fake.calls)  # po przycięciu model dostaje kontekst


def test_streamer_two_utterances_build_history():
    first = FakeWhisper([" Pierwsze", " zdanie."])
    streamer = LiveStreamer(first, ThresholdVAD(), StreamConfig(preroll=0.0, onset_frames=1))
    first.streamer = streamer
    first.base = 0.0
    run_stream(streamer, np.concatenate([speech(0.8), silence(1.0)]))
    assert streamer.history == "Pierwsze zdanie."
    second = FakeWhisper([" Drugie."])
    second.streamer = streamer
    second.base = 0.0
    streamer.transcribe = second
    run_stream(streamer, np.concatenate([speech(0.4), silence(1.0)]))
    assert second.calls and second.calls[0][1] == "Pierwsze zdanie."


# --- tekst sesji -------------------------------------------------------------------


def test_session_text_spacing():
    session = SessionText()
    session.partial("", " Dzień")
    assert session.full() == "Dzień"
    session.partial(" Dzień", " dobry")
    assert session.split() == ("Dzień", " dobry")
    session.final(" dobry.")
    session.partial("", " Jak")
    assert session.full() == "Dzień dobry. Jak"
    session.final(" Jak się masz?")
    assert session.full() == "Dzień dobry. Jak się masz?"
    session.final("")
    assert session.full() == "Dzień dobry. Jak się masz?"


def test_session_text_leading_space():
    session = SessionText(leading_space=True)
    session.final(" Tak.")
    assert session.full() == " Tak."


def test_common_prefix_and_tail():
    assert common_prefix_len("Ala ma kota", "Ala ma psa") == 7
    assert common_prefix_len("", "abc") == 0
    tail, cut = display_tail("x" * 10 + " " + "y" * 200, 50)
    assert cut and len(tail) <= 50


# --- dźwięk -------------------------------------------------------------------------


@pytest.mark.parametrize("rate", [48000, 44100, 32000, 16000])
def test_resampler_length_is_continuous(rate):
    resampler = Resampler(rate)
    t = np.arange(rate * 2) / rate
    signal = np.sin(2 * np.pi * 220 * t).astype(np.float32)
    parts = [resampler(signal[i:i + 777]) for i in range(0, len(signal), 777)]
    out = np.concatenate(parts)
    assert abs(len(out) - 2 * SAMPLE_RATE) <= 3
    # Brak trzasków na granicach bloków: sąsiednie próbki sinusa 220 Hz są bliskie.
    assert np.max(np.abs(np.diff(out))) < 0.2


def test_energy_vad_separates_tone_from_silence():
    vad = EnergyVAD()
    quiet = (np.random.default_rng(1).standard_normal(SAMPLE_RATE) * 0.001).astype(np.float32)
    t = np.arange(SAMPLE_RATE) / SAMPLE_RATE
    loud = (0.2 * np.sin(2 * np.pi * 180 * t)).astype(np.float32)
    assert vad(quiet).mean() < 0.1
    assert vad(loud).mean() > 0.8


# ---------------------------------------------------------------------------
# Koniec wypowiedzi wykrywany niezależnie od trwającego przebiegu modelu
# ---------------------------------------------------------------------------


class SlowModel:
    """Atrapa modelu: przebieg na żywo trwa, dopóki test go nie zwolni."""

    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.calls = []

    def __call__(self, audio, prompt, final):
        self.calls.append((len(audio) / SAMPLE_RATE, final))
        if not final:
            self.entered.set()
            assert self.release.wait(5)
        return [W(0.0, 0.3, f" w{len(self.calls)}")]


def test_end_of_speech_is_detected_while_pass_is_running():
    model = SlowModel()
    streamer = LiveStreamer(model, ThresholdVAD(), StreamConfig(preroll=0.0, onset_frames=1))
    timings = []
    streamer.on_timing = lambda kind, **data: timings.append(kind)
    events = []

    streamer.push(speech(1.6))
    worker = threading.Thread(target=lambda: events.extend(streamer.step()))
    worker.start()
    assert model.entered.wait(5)

    # Przebieg trwa, a w tym czasie: cisza kończąca wypowiedź i początek następnej.
    streamer.push(np.concatenate([silence(1.0), speech(1.0), silence(1.0)]))
    assert "speech_end" in timings and model.calls == [(pytest.approx(1.6, abs=0.05), False)]

    model.release.set()
    worker.join(5)
    assert [e[0] for e in events] == ["speech", "partial"]

    events = streamer.step()   # FINAL zaraz po zakończeniu przebiegu
    assert [e[0] for e in events] == ["final", "speech", "speech"]
    # Dźwięk FINAL kończy się w punkcie końca wypowiedzi — bez następnej wypowiedzi.
    duration, final = model.calls[-1]
    assert final and duration == pytest.approx(1.6 + 0.3, abs=0.1)

    events = streamer.step()   # druga wypowiedź: dźwięk odłożony w trakcie przebiegu
    assert [e[0] for e in events] == ["final", "speech"]
    duration, final = model.calls[-1]
    assert final and duration == pytest.approx(1.0 + 0.3, abs=0.1)
    assert not streamer.in_speech and streamer.step() == []
    assert timings.count("speech_end") == 2 and timings.count("final") == 2


def test_no_live_pass_starts_during_trailing_silence():
    calls = []
    streamer = LiveStreamer(lambda a, p, f: calls.append(f) or [], ThresholdVAD(),
                            StreamConfig(preroll=0.0, onset_frames=1))
    streamer.push(np.concatenate([speech(1.0), silence(0.4)]))
    assert [e[0] for e in streamer.step()] == ["speech"] and calls == []
    streamer.push(speech(0.3))   # mowa wróciła — przebieg na żywo rusza
    streamer.step()
    assert calls == [False]


def test_force_final_covers_audio_held_after_end():
    calls = []
    streamer = LiveStreamer(lambda a, p, f: calls.append(f) or [W(0.0, 0.3, " x")], ThresholdVAD(),
                            StreamConfig(preroll=0.0, onset_frames=1, live_passes=False))
    streamer.push(np.concatenate([speech(1.0), silence(1.0), speech(1.0)]))
    events = streamer.step(force_final=True)
    assert [e[0] for e in events].count("final") == 2 and calls == [True, True]
    assert not streamer.in_speech
