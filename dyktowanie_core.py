"""Logika dyktowania na żywo — niezależna od interfejsu, modelu i systemu.

Moduł zawiera wyłącznie czyste obliczenia, dzięki czemu da się go testować
bez mikrofonu, karty graficznej i Windows:

* ``LocalAgreement`` — zatwierdza słowa, na które zgadzają się dwie kolejne
  hipotezy modelu (algorytm LocalAgreement-2 znany z whisper_streaming),
* ``SileroVAD`` / ``EnergyVAD`` — wykrywanie mowy w ramkach 32 ms,
* ``LiveStreamer`` — dzieli dźwięk na wypowiedzi, co chwilę liczy hipotezę
  i zamienia ją na zdarzenia „częściowy tekst” / „koniec wypowiedzi”,
* ``SessionText`` — składa tekst sesji z poprawnymi odstępami,
* ``Resampler`` — przelicza dźwięk mikrofonu do 16 kHz.
"""

from __future__ import annotations

import math
import re
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable

import numpy as np

from dyktowanie_komendy import render_dictation


SAMPLE_RATE = 16000
VAD_FRAME = 512  # 32 ms przy 16 kHz — rozmiar ramki modelu Silero


# ---------------------------------------------------------------------------
# Słowa i zatwierdzanie hipotez
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Word:
    start: float
    end: float
    text: str


def words_text(words) -> str:
    return "".join(word.text for word in words)


def _agreement_key(text: str) -> str:
    return text.strip()


def _dedup_key(text: str) -> str:
    return re.sub(r"[^\w]", "", text.casefold())


class LocalAgreement:
    """Zatwierdza najdłuższy wspólny początek dwóch kolejnych hipotez.

    Whisper potrafi zmieniać końcówkę transkrypcji, gdy dostaje więcej
    dźwięku. Słowo uznajemy za pewne dopiero wtedy, gdy pojawi się w tym
    samym miejscu w dwóch kolejnych przebiegach. Wszystko za nim to tekst
    „niepewny”, pokazywany na szaro i ewentualnie poprawiany.
    """

    def __init__(self):
        self.committed: list[Word] = []
        self.pending: list[Word] = []
        self._new: list[Word] = []
        self._has_new = False
        self.last_end = 0.0

    def insert(self, words, offset: float = 0.0):
        shifted = [Word(w.start + offset, w.end + offset, w.text) for w in words]
        fresh = [w for w in shifted if w.start > self.last_end - 0.1]
        # Po przycięciu bufora model bywa, że powtarza ostatnie zatwierdzone
        # słowa. Usuwamy powtórzone n-gramy (do 5 słów) na styku.
        if fresh and self.committed and abs(fresh[0].start - self.last_end) < 1.0:
            limit = min(len(self.committed), len(fresh), 5)
            for size in range(limit, 0, -1):
                tail = " ".join(_dedup_key(w.text) for w in self.committed[-size:])
                head = " ".join(_dedup_key(w.text) for w in fresh[:size])
                if tail and tail == head:
                    fresh = fresh[size:]
                    break
        self._new = fresh
        self._has_new = True

    def flush(self) -> list[Word]:
        if not self._has_new:
            return []
        new = self._new
        index = 0
        while (
            index < len(new)
            and index < len(self.pending)
            and _agreement_key(new[index].text) == _agreement_key(self.pending[index].text)
        ):
            index += 1
        commit = new[:index]
        self.pending = new[index:]
        self._new = []
        self._has_new = False
        self._remember(commit)
        return commit

    def commit_all(self) -> list[Word]:
        """Na końcu wypowiedzi zatwierdza także niepotwierdzony ogon."""
        commit = self.flush()
        rest = self.pending
        self.pending = []
        self._remember(rest)
        return commit + rest

    def _remember(self, words):
        if words:
            self.committed.extend(words)
            self.last_end = words[-1].end


# ---------------------------------------------------------------------------
# Filtr halucynacji Whispera
# ---------------------------------------------------------------------------

# Frazy, które Whisper „dopowiada” na ciszy lub szumie — pochodzą z napisów
# filmowych, na których był trenowany. Usuwamy całe segmenty, które je zawierają.
HALLUCINATION_RE = re.compile(
    r"amara\.org"
    r"|napisy\s+(?:stworzone|wykonane|przygotowane|zrobione)\s+przez"
    r"|napisy\s+wykonała?"
    r"|subskrybuj(?:cie)?\s+(?:kanał|nas)"
    r"|dzięki\s+za\s+(?:obejrzenie|oglądanie)"
    r"|dziękuję\s+za\s+(?:obejrzenie|oglądanie)"
    r"|zdjęcia\s+i\s+montaż"
    r"|thanks?\s+(?:you\s+)?for\s+watching"
    r"|transcribed\s+by",
    re.IGNORECASE,
)


def is_hallucinated_segment(
    text: str,
    no_speech_prob: float = 0.0,
    avg_logprob: float = 0.0,
    compression_ratio: float = 1.0,
) -> bool:
    if HALLUCINATION_RE.search(text or ""):
        return True
    # Ta sama reguła, której Whisper używa do pomijania ciszy.
    if no_speech_prob > 0.6 and avg_logprob < -1.0:
        return True
    # Zapętlone powtórzenia („i i i i i …”) mają bardzo wysoki współczynnik kompresji.
    if compression_ratio > 2.4:
        return True
    return False


# ---------------------------------------------------------------------------
# Wykrywanie mowy
# ---------------------------------------------------------------------------


class EnergyVAD:
    """Zapasowy detektor mowy oparty na głośności z adaptacyjnym poziomem szumu."""

    frame = VAD_FRAME
    name = "energia"

    def __init__(self):
        self.reset()

    def reset(self):
        self.floor = -60.0

    def __call__(self, samples: np.ndarray) -> np.ndarray:
        count = len(samples) // self.frame
        if count == 0:
            return np.zeros(0, dtype=np.float32)
        frames = samples[: count * self.frame].reshape(count, self.frame)
        rms = np.sqrt(np.mean(frames.astype(np.float64) ** 2, axis=1))
        result = np.empty(count, dtype=np.float32)
        for index, value in enumerate(rms):
            db = 20.0 * math.log10(float(value) + 1e-9)
            if db < self.floor:
                self.floor = 0.7 * self.floor + 0.3 * db
            else:
                self.floor += (db - self.floor) * 0.004
            floor = max(self.floor, -72.0)
            margin = db - floor
            probability = 0.0 if db < -55.0 else min(1.0, max(0.0, (margin - 6.0) / 10.0))
            result[index] = probability
        return result


class SileroVAD:
    """Strumieniowy Silero VAD z modelu dołączonego do faster-whisper.

    Model z faster-whisper przyjmuje sekwencję ramek 512 próbek (+64 próbki
    kontekstu) i zwraca stan LSTM, który przekazujemy do kolejnego wywołania —
    dzięki temu działa ciągle, ramka po ramce.
    """

    frame = VAD_FRAME
    context = 64
    name = "Silero"

    def __init__(self):
        from faster_whisper.vad import get_vad_model

        self.session = get_vad_model().session
        self.reset()
        # Próba na ciszy — jeżeli format modelu się zmieni, wolimy wiedzieć
        # od razu i przełączyć się na detektor energii.
        probe = self(np.zeros(self.frame * 2, dtype=np.float32))
        if probe.shape != (2,):
            raise RuntimeError("Nieoczekiwany format wyjścia Silero VAD")
        self.reset()

    def reset(self):
        self.h = np.zeros((1, 1, 128), dtype=np.float32)
        self.c = np.zeros((1, 1, 128), dtype=np.float32)
        self.last_context = np.zeros(self.context, dtype=np.float32)

    def __call__(self, samples: np.ndarray) -> np.ndarray:
        count = len(samples) // self.frame
        if count == 0:
            return np.zeros(0, dtype=np.float32)
        chunks = samples[: count * self.frame].astype(np.float32).reshape(count, self.frame)
        contexts = np.concatenate(
            [self.last_context[None, :], chunks[:-1, -self.context:]], axis=0
        )
        batch = np.concatenate([contexts, chunks], axis=1)
        output, self.h, self.c = self.session.run(
            None, {"input": batch, "h": self.h, "c": self.c}
        )
        self.last_context = chunks[-1, -self.context:].copy()
        return np.asarray(output, dtype=np.float32).reshape(-1)[:count]


def make_vad():
    """Zwraca Silero VAD, a gdy jest niedostępny — detektor energii."""
    try:
        return SileroVAD()
    except Exception:
        return EnergyVAD()


# ---------------------------------------------------------------------------
# Strumieniowanie wypowiedzi
# ---------------------------------------------------------------------------


# Limit nowych tokenów na przebieg. Bez niego Whisper w pętli powtórzeń generuje 224 tokeny
# (limit modelu), a przebieg trwa 4-12 s zamiast 0,5-1,7 s. Normalne przebiegi mają do ~120 tokenów
# nawet przy buforze 15-30 s, więc limity poniżej ich nie ucinają.
LIVE_TOKENS_MIN = 32
LIVE_TOKENS_MAX = 128
LIVE_TOKENS_BASE = 16
LIVE_TOKENS_PER_SECOND = 8
FINAL_TOKENS_MAX = 192


def max_new_tokens_for(duration_s: float, final: bool = False) -> int:
    """Maksymalna liczba nowych tokenów dla przebiegu na buforze o danej długości."""
    if final:
        return FINAL_TOKENS_MAX
    limit = LIVE_TOKENS_BASE + LIVE_TOKENS_PER_SECOND * max(0.0, float(duration_s))
    return int(min(LIVE_TOKENS_MAX, max(LIVE_TOKENS_MIN, round(limit))))


@dataclass
class StreamConfig:
    end_silence: float = 0.7      # ile sekund ciszy kończy wypowiedź
    step: float = 0.45            # co ile sekund nowego dźwięku liczyć hipotezę
    min_speech: float = 0.25      # krótsze „wypowiedzi” (stuknięcia, kaszel) pomijamy
    preroll: float = 0.35         # ile dźwięku sprzed wykrycia mowy dołączyć
    trim_after: float = 14.0      # po tylu sekundach bufor jest przycinany
    max_buffer: float = 24.0      # twardy limit długości bufora
    speech_threshold: float = 0.5
    silence_threshold: float = 0.35
    onset_frames: int = 3         # tyle ramek mowy z rzędu rozpoczyna wypowiedź
    prompt_chars: int = 220       # ile wcześniejszego tekstu podać modelowi jako kontekst
    keep_trailing_silence: float = 0.3
    pass_hold_silence: float = 0.3  # przy takiej ciszy nie zaczynamy przebiegu na żywo (blokowałby FINAL)
    live_passes: bool = True      # False = tekst tylko po pauzie (modele w chmurze)


TranscribeFn = Callable[[np.ndarray, str, bool], list]


class LiveStreamer:
    """Zamienia ciągły dźwięk na zdarzenia tekstowe w czasie rzeczywistym.

    Zdarzenia zwracane przez ``step()``:
      ("partial", nowo_zatwierdzony_tekst, tekst_niepewny)
      ("final", nowo_zatwierdzony_tekst)
      ("speech", True/False) — początek / koniec wykrytej mowy

    ``push()`` (VAD) i ``step()`` (model) mogą działać w osobnych wątkach: wspólny stan
    chroni blokada, zwalniana tylko na czas wywołania modelu. Koniec wypowiedzi wykrywa
    ``push()`` — także w trakcie przebiegu — a dźwięk, który przyjdzie po nim, czeka
    w ``_held`` na następną wypowiedź.
    """

    def __init__(self, transcribe: TranscribeFn, vad=None, config: StreamConfig | None = None,
                 sample_rate: int = SAMPLE_RATE):
        self.transcribe = transcribe
        self.vad = vad if vad is not None else EnergyVAD()
        self.config = config or StreamConfig()
        self.sr = sample_rate
        self.frame = getattr(self.vad, "frame", VAD_FRAME)
        self.frame_seconds = self.frame / self.sr
        self._pending = np.zeros(0, dtype=np.float32)
        self._events: list = []
        # Pomiar latencji: on_timing(rodzaj, **dane) — ustawiany przez silnik.
        self.on_timing: Callable[..., None] | None = None
        self._frame_end: float | None = None
        self._lock = threading.RLock()
        self._held: deque = deque()
        self.reset_session()

    # -- stan ---------------------------------------------------------------

    def reset_session(self):
        with self._lock:
            self.history = ""
            self._pending = np.zeros(0, dtype=np.float32)
            self._held.clear()
            try:
                self.vad.reset()
            except AttributeError:
                pass
            self._reset_utterance()

    def _reset_utterance(self):
        with self._lock:
            self._reset_utterance_state()

    def _reset_utterance_state(self):
        self.in_speech = False
        self._ended = False
        self._chunks: list[np.ndarray] = []
        self._samples = 0
        self.audio_offset = 0.0
        self._preroll: deque = deque()
        self._onset = 0
        self._last_speech = False
        self.silence_run = 0.0
        self.speech_time = 0.0
        self.since_pass = 0.0
        self.agreement = LocalAgreement()
        self.utterance_text = ""
        self._outside_index = 0
        self._outside_text = ""
        self.speech_start_time: float | None = None
        self.last_speech_time: float | None = None
        self._last_pass_start: float | None = None
        # Diagnostyka: ile dźwięku łącznie trafiło do wypowiedzi (niezależnie od przycinania
        # bufora) i ile było go przy poprzednim przebiegu — „nowe audio” między przebiegami.
        self._received = 0
        self._received_at_pass = 0

    def _timing(self, kind: str, **data):
        if self.on_timing is not None:
            try:
                self.on_timing(kind, **data)
            except Exception:
                pass

    @property
    def buffer_seconds(self) -> float:
        return self._samples / self.sr

    def _joined(self) -> np.ndarray:
        if len(self._chunks) > 1:
            self._chunks = [np.concatenate(self._chunks)]
        return self._chunks[0] if self._chunks else np.zeros(0, dtype=np.float32)

    # -- wejście --------------------------------------------------------------

    def push(self, samples: np.ndarray, end_time: float | None = None):
        """``end_time`` — czas (perf_counter) ostatniej próbki; służy do pomiaru latencji."""
        if samples is None or len(samples) == 0:
            return
        with self._lock:
            self._drain_held()
            if self._ended:
                self._held.append(("raw", samples, end_time))
            else:
                self._feed(samples, end_time)

    def _drain_held(self):
        """Odtwarza dźwięk odłożony po końcu poprzedniej wypowiedzi."""
        while self._held and not self._ended:
            item = self._held.popleft()
            if item[0] == "frame":
                self._frame_end = item[3]
                self._frame(item[1], item[2])
            else:
                self._feed(item[1], item[2])

    def _feed(self, samples: np.ndarray, end_time: float | None):
        data = np.asarray(samples, dtype=np.float32).reshape(-1)
        buffer = np.concatenate([self._pending, data]) if len(self._pending) else data
        count = len(buffer) // self.frame
        self._pending = buffer[count * self.frame:].copy()
        if count == 0:
            return
        frames = buffer[: count * self.frame].reshape(count, self.frame)
        probabilities = self.vad(frames.reshape(-1))
        total = len(buffer)
        scored = []
        for index, (frame, probability) in enumerate(zip(frames, probabilities)):
            if end_time is not None:
                frame_end = end_time - (total - (index + 1) * self.frame) / self.sr
            else:
                frame_end = None
            if self._ended:
                # Ramki po końcu wypowiedzi (VAD już je ocenił) należą do następnej.
                scored.append(("frame", frame.copy(), float(probability), frame_end))
                continue
            self._frame_end = frame_end
            self._frame(frame.copy(), float(probability))
        self._held.extendleft(reversed(scored))

    def _frame(self, frame: np.ndarray, probability: float):
        cfg = self.config
        fs = self.frame_seconds
        if not self.in_speech:
            self._preroll.append(frame)
            while len(self._preroll) * fs > cfg.preroll + 1e-9:
                self._preroll.popleft()
            self._onset = self._onset + 1 if probability >= cfg.speech_threshold else 0
            if self._onset >= cfg.onset_frames:
                if self._frame_end is not None:
                    self.speech_start_time = self._frame_end - self._onset * fs
                    self.last_speech_time = self._frame_end
                self._timing("speech_start", audio_start=self.speech_start_time)
                self.in_speech = True
                self._chunks = list(self._preroll)
                self._samples = sum(len(chunk) for chunk in self._chunks)
                self._received = self._samples
                self._received_at_pass = 0
                self._preroll.clear()
                self.speech_time = self._onset * fs
                self.since_pass = self._samples / self.sr
                self.silence_run = 0.0
                self._last_speech = True
                self._onset = 0
                self._events.append(("speech", True))
            return
        self._chunks.append(frame)
        self._samples += len(frame)
        self._received += len(frame)
        self.since_pass += fs
        threshold = cfg.silence_threshold if self._last_speech else cfg.speech_threshold
        speech = probability >= threshold
        if speech:
            self.silence_run = 0.0
            self.speech_time += fs
            if self._frame_end is not None:
                self.last_speech_time = self._frame_end
        else:
            self.silence_run += fs
            if self.silence_run >= cfg.end_silence:
                self._ended = True
                self._timing("speech_end", audio_end=self.last_speech_time)
        self._last_speech = speech

    # -- przetwarzanie ---------------------------------------------------------

    def step(self, force_final: bool = False) -> list:
        with self._lock:
            self._drain_held()
            events, self._events = self._events, []
            cfg = self.config
            if not self.in_speech:
                pass
            elif force_final or self._ended or self.silence_run >= cfg.end_silence:
                events.extend(self._finalize())
            elif self.buffer_seconds >= cfg.max_buffer and not self._can_trim():
                events.extend(self._finalize())
            elif (cfg.live_passes and self.since_pass >= cfg.step and self.speech_time >= cfg.min_speech
                  and self.silence_run < cfg.pass_hold_silence):
                events.extend(self._pass())
                self._trim()
            # Zatrzymanie: dźwięk odłożony po końcu wypowiedzi też musi zostać przepisany.
            while force_final and self.in_speech:
                events.extend(self._finalize())
            return events

    def _transcribe(self, audio: np.ndarray, prompt: str, final: bool) -> list:
        """Model liczy bez blokady — ``push()`` może w tym czasie dopisywać dźwięk."""
        self._lock.release()
        try:
            return self.transcribe(audio, prompt, final)
        finally:
            self._lock.acquire()

    def _prompt(self) -> str:
        text = (self.history + self._outside_text).strip()
        limit = self.config.prompt_chars
        if len(text) > limit:
            text = text[-limit:]
            space = text.find(" ")
            if 0 <= space < 40:
                text = text[space + 1:]
        return text

    def _new_audio(self) -> float:
        """Sekundy dźwięku, które doszły od poprzedniego przebiegu (i oznacza je jako zużyte)."""
        new = (self._received - self._received_at_pass) / self.sr
        self._received_at_pass = self._received
        return new

    def _stats(self):
        """Statystyki ostatniego wywołania modelu (gdy transkrybator je zbiera)."""
        stats = getattr(self.transcribe, "last_stats", None)
        return dict(stats) if isinstance(stats, dict) else None

    def _pass(self) -> list:
        audio = self._joined()
        new_audio = self._new_audio()
        received = self._received
        started = time.perf_counter()
        interval = None if self._last_pass_start is None else started - self._last_pass_start
        self._last_pass_start = started
        words = self._transcribe(audio, self._prompt(), False)
        ended = time.perf_counter()
        asr = ended - started
        self.agreement.insert(words, self.audio_offset)
        committed = self.agreement.flush()
        self.since_pass = (self._received - received) / self.sr  # dźwięk, który doszedł w trakcie
        delta = words_text(committed)
        self.utterance_text += delta
        self._timing("pass", delta=delta, asr=asr, buffer=len(audio) / self.sr,
                     interval=interval, step=self.config.step, started=started, ended=ended,
                     new_audio=new_audio, stats=self._stats())
        return [("partial", delta, words_text(self.agreement.pending))]

    def _committed_in_buffer(self):
        return self.agreement.committed[self._outside_index:]

    def _can_trim(self) -> bool:
        return any(w.end > self.audio_offset for w in self._committed_in_buffer())

    def _trim(self):
        cfg = self.config
        if self.buffer_seconds < cfg.trim_after:
            return
        inside = [w for w in self._committed_in_buffer() if w.end > self.audio_offset]
        if not inside:
            return
        cut_word = None
        for word in reversed(inside):
            if word.text.strip()[-1:] in ".?!…":
                cut_word = word
                break
        if cut_word is None and self.buffer_seconds >= (cfg.trim_after + cfg.max_buffer) / 2:
            cut_word = inside[-1]
        if cut_word is None:
            return
        cut_samples = int(round((cut_word.end - self.audio_offset) * self.sr))
        cut_samples = max(0, min(cut_samples, self._samples))
        audio = self._joined()[cut_samples:]
        self._chunks = [audio] if len(audio) else []
        self._samples = len(audio)
        self.audio_offset = cut_word.end
        position = self.agreement.committed.index(cut_word, self._outside_index) + 1
        self._outside_text += words_text(self.agreement.committed[self._outside_index:position])
        self._outside_index = position

    def _finalize(self) -> list:
        cfg = self.config
        events = []
        delta = ""
        asr = None
        audio_seconds = None
        started = ended = None
        stats = None
        new_audio = self._new_audio()
        if not self._ended:
            self._timing("speech_end", audio_end=self.last_speech_time)
            self._ended = True  # od teraz nowy dźwięk czeka na następną wypowiedź
        if self.speech_time >= cfg.min_speech:
            audio = self._joined()
            extra = max(0.0, self.silence_run - cfg.keep_trailing_silence)
            drop = int(extra * self.sr)
            if 0 < drop < len(audio):
                audio = audio[: len(audio) - drop]
            audio_seconds = len(audio) / self.sr
            started = time.perf_counter()
            words = self._transcribe(audio, self._prompt(), True)
            ended = time.perf_counter()
            asr = ended - started
            stats = self._stats()
            self.agreement.insert(words, self.audio_offset)
            delta = words_text(self.agreement.commit_all())
        else:
            delta = words_text(self.agreement.commit_all())
        self.utterance_text += delta
        self._timing("final", delta=delta, asr=asr, buffer=audio_seconds, started=started,
                     ended=ended, new_audio=new_audio, stats=stats)
        events.append(("final", delta))
        events.append(("speech", False))
        utterance = self.utterance_text.strip()
        if utterance:
            self.history = (self.history + " " + utterance).strip()[-2000:]
        self._reset_utterance()
        self._drain_held()
        events.extend(self._events)
        self._events = []
        return events


# ---------------------------------------------------------------------------
# Tekst sesji
# ---------------------------------------------------------------------------


class SessionText:
    """Składa tekst jednej sesji dyktowania (od startu do zatrzymania)."""

    def __init__(self, leading_space: bool = False):
        self.leading_space = leading_space
        self.committed = ""
        self.utterance = ""
        self.tentative = ""
        self._right_glue = False

    def partial(self, delta: str, tentative: str):
        self.utterance += delta
        self.tentative = tentative

    def final(self, delta: str):
        self.utterance += delta
        addition, self._right_glue = self._render(self.utterance)
        self.committed += addition
        self.utterance = ""
        self.tentative = ""

    def _format(self, raw: str, *, final: bool = True) -> str:
        return self._render(raw, final=final)[0]

    def _render(self, raw: str, *, final: bool = True) -> tuple[str, bool]:
        return render_dictation(raw, self.committed, final=final,
                                leading_space=self.leading_space and not self.committed,
                                right_glue=self._right_glue)

    def stable(self) -> str:
        return self.committed + self._format(self.utterance, final=False)

    def full(self) -> str:
        return self.committed + self._format(self.utterance + self.tentative)

    def split(self) -> tuple[str, str]:
        """(pewny tekst, niepewny ogon) — do kolorowania w interfejsie."""
        stable = self.stable()
        full = self.full()
        return stable, full[len(stable):]


def common_prefix_len(a: str, b: str) -> int:
    limit = min(len(a), len(b))
    index = 0
    while index < limit and a[index] == b[index]:
        index += 1
    return index


# ---------------------------------------------------------------------------
# Przeliczanie częstotliwości próbkowania
# ---------------------------------------------------------------------------


class Resampler:
    """Strumieniowe przeliczanie do 16 kHz bez nieciągłości między blokami."""

    def __init__(self, source_rate: int, target_rate: int = SAMPLE_RATE):
        self.source = int(source_rate)
        self.target = int(target_rate)
        self.ratio = self.source / self.target
        self.factor = self.source // self.target if self.source % self.target == 0 else 0
        self.buffer = np.zeros(0, dtype=np.float32)
        self.position = 0.0

    def __call__(self, samples: np.ndarray) -> np.ndarray:
        data = np.asarray(samples, dtype=np.float32).reshape(-1)
        if self.source == self.target:
            return data
        buffer = np.concatenate([self.buffer, data]) if len(self.buffer) else data
        if self.factor:
            count = len(buffer) // self.factor
            output = buffer[: count * self.factor].reshape(count, self.factor).mean(axis=1)
            self.buffer = buffer[count * self.factor:].copy()
            return output.astype(np.float32)
        if len(buffer) < 2:
            self.buffer = buffer.copy()
            return np.zeros(0, dtype=np.float32)
        count = int((len(buffer) - 1 - self.position) // self.ratio) + 1
        if count <= 0:
            self.buffer = buffer.copy()
            return np.zeros(0, dtype=np.float32)
        positions = self.position + np.arange(count) * self.ratio
        output = np.interp(positions, np.arange(len(buffer)), buffer)
        next_position = self.position + count * self.ratio
        drop = min(int(next_position), len(buffer))
        self.buffer = buffer[drop:].copy()
        self.position = next_position - drop
        return output.astype(np.float32)


def level_to_unit(rms: float) -> float:
    """Zamienia RMS sygnału na 0–1 do wskaźnika poziomu (skala decybelowa)."""
    db = 20.0 * math.log10(max(rms, 1e-9))
    return min(1.0, max(0.0, (db + 58.0) / 46.0))


def display_tail(text: str, limit: int = 140) -> tuple[str, bool]:
    """Zwraca końcówkę tekstu do wyświetlenia oraz informację, czy ją ucięto."""
    if len(text) <= limit:
        return text, False
    tail = text[-limit:]
    space = tail.find(" ")
    if 0 <= space < 25:
        tail = tail[space + 1:]
    return tail, True
