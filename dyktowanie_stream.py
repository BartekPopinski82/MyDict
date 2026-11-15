"""Dyktowanie strumieniowe przez WebSocket Deepgram (interim_results, endpointing).

Zamiast wysyłać całą wypowiedź po pauzie (jak ``CloudTranscriber``), dźwięk płynie
do usługi na bieżąco, a tekst wraca w trakcie mówienia:

* wyniki pośrednie (``is_final=false``) → tekst „niepewny”, zmienia się w locie,
* wyniki zatwierdzone (``is_final=true``) → tekst „pewny”,
* ``speech_final=true`` (endpointing: cisza dłuższa niż ``endpointing`` ms) → koniec
  wypowiedzi,
* ``Finalize`` — program sam wykrywa ciszę w mikrofonie (``local_finalize_ms``) i prosi usługę o
  natychmiastowe zatwierdzenie tekstu, nie czekając na jej endpointing (przy szumie w tle
  endpointing usługi potrafi nie zadziałać przez kilka sekund).

Klasa ``DeepgramStreamer`` ma ten sam interfejs co ``LiveStreamer`` z dyktowanie_core
(``push`` / ``step`` / ``reset_session``) i zwraca te same zdarzenia, więc silnik i interfejs
nie muszą wiedzieć, skąd pochodzi tekst:

    ("partial", nowo_zatwierdzony_tekst, tekst_niepewny)
    ("final", nowo_zatwierdzony_tekst)
    ("speech", True/False)

Parametry połączenia (``interim_results``, ``endpointing`` …) pochodzą z pliku
dostawcy.json (``streaming_params``); parametry zaczynające się od ``local_`` (np.
``local_finalize_ms``) dotyczą tylko programu i nie trafiają do adresu połączenia. Wymaga biblioteki ``websockets`` (>= 14).

Pomiar latencji (``on_timing``, te same zdarzenia co ``LiveStreamer``, więc dzienniki i CSV
mają ten sam układ co dla modeli lokalnych). Usługa podaje czasy słów na osi wysłanego
dźwięku; sesja pamięta, kiedy każda próbka powstała, i przelicza je na zegar programu:

* ``speech_start`` — pierwszy wynik z tekstem; ``audio_start`` = początek pierwszego słowa,
* ``pass``         — każdy wynik z tekstem; ``asr`` = opóźnienie wyniku względem końca
                     dźwięku, który obejmuje (sieć + model usługi), ``delta`` = tekst pewny,
* ``speech_end`` / ``final`` — koniec wypowiedzi; ``audio_end`` = koniec ostatniego słowa.
"""

from __future__ import annotations

import json
import logging
import queue
import re
import threading
import time
import urllib.parse
from collections import deque

import numpy as np

from dyktowanie_api import USER_AGENT, ApiError, ResolvedModel, _check
from dyktowanie_core import SAMPLE_RATE, VAD_FRAME, make_vad


log = logging.getLogger("dyktowanie.stream")

KEEPALIVE_AFTER = 5.0      # tyle sekund bez dźwięku → wiadomość KeepAlive
CLOSE_TIMEOUT = 6.0        # tyle czeka na końcowe wyniki po CloseStream
MAX_RECONNECTS = 3
CLOCK_ANCHORS = 8192       # tyle ostatnich porcji dźwięku pamięta pomiar latencji (kilka minut)
_FINISH = object()
LOCAL_PREFIX = "local_"   # parametry tylko dla programu (nie dla usługi)
_FINALIZE = object()


def websockets_available() -> bool:
    try:
        import websockets.sync.client  # noqa: F401
    except ImportError:
        return False
    return True


def stream_url(model: ResolvedModel, language: str | None, hotwords: tuple = ()) -> str:
    """Adres WebSocket: adres API z dostawcy.json (https→wss) + /listen + parametry."""
    base = model.base_url.rstrip("/")
    scheme, _, rest = base.partition("://")
    scheme = {"https": "wss", "http": "ws"}.get(scheme, scheme)
    params = [
        ("model", model.model), ("encoding", "linear16"),
        ("sample_rate", str(SAMPLE_RATE)), ("channels", "1"),
    ]
    taken = {name for name, _ in params}
    for name, value in (*model.params, *model.stream_params):
        if name in taken or name in ("utterances", "utt_split", "detect_language"):
            continue   # parametry tylko dla plików nagranych
        if name.startswith(LOCAL_PREFIX):
            continue   # ustawienia programu, nie usługi
        params.append((name, value))
    if language:
        params.append(("language", language))
    elif model.model.startswith("nova-3"):
        params.append(("language", "multi"))
    hotword_param = "keyterm" if model.model.startswith("nova-3") else "keywords"
    for word in hotwords[:100]:
        params.append((hotword_param, word))
    return f"{scheme}://{rest}/listen?{urllib.parse.urlencode(params)}"


def to_pcm16(samples: np.ndarray) -> bytes:
    data = np.clip(np.asarray(samples, dtype=np.float32).reshape(-1), -1.0, 1.0)
    return (data * 32767.0).astype("<i2").tobytes()


class SilenceWatch:
    """Lokalne wykrywanie końca wypowiedzi: po ``silence_ms`` ciszy następującej po mowie
    ``feed`` zwraca True (jednorazowo — dopiero nowa mowa uzbraja detektor ponownie)."""

    def __init__(self, silence_ms: float, vad=None):
        self.limit = max(0.0, float(silence_ms)) / 1000.0
        self.vad = vad if vad is not None else make_vad()
        self.rest = np.zeros(0, dtype=np.float32)
        self.heard = False
        self.quiet = 0.0

    def reset(self):
        self.rest = np.zeros(0, dtype=np.float32)
        self.heard = False
        self.quiet = 0.0
        reset = getattr(self.vad, "reset", None)
        if reset is not None:
            reset()

    def feed(self, samples: np.ndarray) -> bool:
        data = np.concatenate((self.rest, np.asarray(samples, dtype=np.float32).reshape(-1)))
        frame = getattr(self.vad, "frame", VAD_FRAME)
        count = len(data) // frame
        self.rest = data[count * frame:]
        if count == 0:
            return False
        fire = False
        for probability in self.vad(data[: count * frame]):
            if probability >= 0.5:
                self.heard, self.quiet = True, 0.0
            elif self.heard:
                self.quiet += frame / SAMPLE_RATE
                if self.quiet >= self.limit:
                    self.heard, self.quiet = False, 0.0
                    fire = True
        return fire


class _Session:
    """Jedno połączenie WebSocket obsługujące jedną sesję dyktowania."""

    def __init__(self, url: str, headers: dict, service: str, timing=None):
        self.url = url
        self.headers = headers
        self.service = service
        self._timing = timing or (lambda kind, **data: None)
        # Pomiar latencji: (numer ostatniej próbki porcji, kiedy powstała) — oś dźwięku usługi → zegar.
        self._clock: deque = deque(maxlen=CLOCK_ANCHORS)
        self._clock_lock = threading.Lock()
        self._queued = 0              # próbki przyjęte do wysłania (łącznie)
        self._sent = 0                # próbki wysłane (łącznie, wszystkie połączenia)
        self._base = 0                # próbki wysłane przed bieżącym połączeniem (jego oś zaczyna się od 0)
        self._audio_start: float | None = None
        self._audio_end: float | None = None
        self._start_s: float | None = None
        self._last_result: float | None = None
        self.audio: queue.Queue = queue.Queue()
        self.events: queue.SimpleQueue = queue.SimpleQueue()
        self.error: ApiError | None = None
        self.error_reported = False
        self.finishing = False
        self.done = threading.Event()
        self.abort = threading.Event()
        self._finish_sent = False
        self._finish_time = 0.0
        self._carry = None            # element wyjęty z kolejki, ale niewysłany (zerwane połączenie)
        self._ws = None
        self._speech = False
        self._utterance = ""
        self._interim = ""
        self.thread = threading.Thread(target=self._run, name="dyktowanie-deepgram", daemon=True)
        self.thread.start()

    # -- sterowanie z wątku silnika -------------------------------------------

    def send_audio(self, pcm: bytes, end_time: float | None = None):
        """``end_time`` — kiedy powstała ostatnia próbka porcji (perf_counter)."""
        if not self.finishing and not self.abort.is_set():
            with self._clock_lock:
                self._queued += len(pcm) // 2
                self._clock.append((self._queued, time.perf_counter() if end_time is None else end_time))
            self.audio.put(pcm)

    def finalize(self):
        """Prosi usługę o natychmiastowe zatwierdzenie dotychczasowego tekstu (po zakolejkowanym dźwięku)."""
        if self.active:
            self.audio.put(_FINALIZE)

    def finish(self):
        """Po wysłaniu zakolejkowanego dźwięku prosi usługę o dokończenie (CloseStream)."""
        if not self.finishing:
            self.finishing = True
            self.audio.put(_FINISH)

    def close(self):
        self.abort.set()
        ws = self._ws
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass

    @property
    def active(self) -> bool:
        return not self.finishing and not self.abort.is_set() and not self.done.is_set()

    # -- wątek połączenia -----------------------------------------------------------

    def _run(self):
        try:
            from websockets import exceptions as ws_errors
            from websockets.sync.client import connect
        except ImportError:
            self._fail(ApiError("Brakuje biblioteki „websockets” — doinstaluj ją: pip install websockets"))
            self.done.set()
            return
        failures = 0
        try:
            while not self.abort.is_set():
                try:
                    with connect(self.url, additional_headers=self.headers, open_timeout=10,
                                 close_timeout=2, max_size=None, user_agent_header=USER_AGENT) as ws:
                        self._ws = ws
                        self._base = self._sent
                        failures = 0
                        log.info("Deepgram (stream): połączono")
                        self._serve(ws)
                    if self._finish_sent or self.abort.is_set():
                        break
                except ws_errors.InvalidStatus as exc:
                    status = getattr(exc.response, "status_code", 0)
                    try:
                        _check(self.service, status, b"")
                    except ApiError as api_error:
                        self._fail(api_error)
                    else:
                        self._fail(ApiError(f"{self.service}: nie udało się nawiązać połączenia ({status})."))
                    break
                except (OSError, TimeoutError, ws_errors.WebSocketException) as exc:
                    if self._finish_sent or self.abort.is_set():
                        break
                    failures += 1
                    log.warning("Deepgram (stream): przerwane połączenie (%s), próba %d/%d",
                                exc, failures, MAX_RECONNECTS)
                    if failures > MAX_RECONNECTS:
                        self._fail(ApiError(f"Błąd połączenia z usługą {self.service}: {exc}", retryable=True))
                        break
                    self.abort.wait(1.0)
                finally:
                    self._ws = None
        finally:
            self._flush_pending()
            self.done.set()

    def _serve(self, ws):
        stop_sender = threading.Event()
        sender = threading.Thread(target=self._send_loop, args=(ws, stop_sender),
                                  name="dyktowanie-deepgram-wysylka", daemon=True)
        sender.start()
        try:
            while not self.abort.is_set():
                try:
                    raw = ws.recv(timeout=1.0)
                except TimeoutError:
                    if self._finish_sent and time.monotonic() - self._finish_time > CLOSE_TIMEOUT:
                        log.warning("Deepgram (stream): brak zamknięcia po CloseStream")
                        break
                    continue
                if isinstance(raw, (bytes, bytearray)):
                    continue
                try:
                    self._handle(json.loads(raw))
                except ValueError:
                    log.debug("Deepgram (stream): nieczytelna wiadomość %r", raw[:200])
        except Exception as exc:
            from websockets import exceptions as ws_errors

            if not isinstance(exc, ws_errors.ConnectionClosedOK):
                raise
        finally:
            stop_sender.set()
            sender.join(timeout=1.5)
        if not self._finish_sent and not self.abort.is_set():
            raise ConnectionError("Połączenie z usługą zostało zamknięte.")

    def _send_loop(self, ws, stop: threading.Event):
        last = time.monotonic()
        while not stop.is_set():
            item, self._carry = self._carry, None
            if item is None:
                try:
                    item = self.audio.get(timeout=0.5)
                except queue.Empty:
                    if time.monotonic() - last >= KEEPALIVE_AFTER:
                        try:
                            ws.send(json.dumps({"type": "KeepAlive"}))
                            last = time.monotonic()
                        except Exception:
                            return
                    continue
            try:
                if item is _FINALIZE:
                    ws.send(json.dumps({"type": "Finalize"}))
                    last = time.monotonic()
                    continue
                if item is _FINISH:
                    ws.send(json.dumps({"type": "CloseStream"}))
                    self._finish_sent = True
                    self._finish_time = time.monotonic()
                    return
                ws.send(item)
                self._sent += len(item) // 2
                last = time.monotonic()
            except Exception:
                self._carry = item     # wyślemy po ponownym połączeniu
                return

    # -- wiadomości z usługi ----------------------------------------------------------

    def _handle(self, message: dict):
        kind = message.get("type")
        if kind == "Results":
            self._on_results(message)
        elif kind == "UtteranceEnd":
            if self._speech or self._utterance:
                if self._audio_end is None:
                    self._audio_end = self._perf(message.get("last_word_end"))
                self._end_utterance("")
        elif kind in ("Error", "error"):
            detail = message.get("description") or message.get("message") or message.get("err_msg") or "błąd usługi"
            self._fail(ApiError(f"{self.service}: {detail}"))

    def _on_results(self, message: dict):
        received = time.perf_counter()
        alternatives = (message.get("channel") or {}).get("alternatives") or [{}]
        text = str(alternatives[0].get("transcript") or "").strip()
        timing = self._measure(message, alternatives[0], text, received) if text or self._speech else {}
        if text and not self._speech:
            self._speech = True
            self._timing("speech_start", audio_start=self._audio_start)
            self.events.put(("speech", True))
        if message.get("is_final"):
            delta = " " + text if text else ""
            self._interim = ""
            # Odpowiedź na Finalize (from_finalize) kończy wypowiedź tak jak speech_final; pusta
            # odpowiedź, gdy wypowiedź już zakończył endpointing usługi, jest pomijana.
            finalized = bool(message.get("from_finalize")) and bool(delta or self._speech or self._utterance)
            if message.get("speech_final") or finalized:
                self._end_utterance(delta, timing)
            elif delta:
                self._utterance += delta
                self._timing("pass", delta=delta, **timing)
                self.events.put(("partial", delta, ""))
        else:
            self._interim = " " + text if text else ""
            if text:
                self._timing("pass", delta="", **timing)
            self.events.put(("partial", "", self._interim))

    def _end_utterance(self, delta: str, timing: dict | None = None):
        self._utterance += delta
        if self._speech:
            self._timing("speech_end", audio_end=self._audio_end)
            self._timing("final", delta=delta, **(timing or {}))
        self.events.put(("final", delta))
        if self._speech:
            self.events.put(("speech", False))
        self._speech = False
        self._utterance = ""
        self._interim = ""
        self._audio_start = self._audio_end = self._start_s = self._last_result = None

    # -- pomiar latencji ------------------------------------------------------------

    def _perf(self, seconds) -> float | None:
        """Sekunda na osi dźwięku bieżącego połączenia → chwila (perf_counter), w której
        ta próbka powstała w mikrofonie."""
        if not isinstance(seconds, (int, float)) or seconds < 0:
            return None
        index = self._base + seconds * SAMPLE_RATE
        with self._clock_lock:
            if not self._clock:
                return None
            end, end_time = self._clock[-1]
            for anchor in reversed(self._clock):
                if anchor[0] < index:
                    break
                end, end_time = anchor
        return end_time - (end - index) / SAMPLE_RATE

    def _measure(self, message: dict, alternative: dict, text: str, received: float) -> dict:
        """Dane jednego wyniku dla ``on_timing``; przy okazji zapamiętuje początek i koniec mowy."""
        start, duration = message.get("start"), message.get("duration")
        covered_s = start + duration if isinstance(start, (int, float)) and isinstance(duration, (int, float)) else None
        if text:
            words = alternative.get("words") or []
            first_s = words[0].get("start") if words else start
            last_s = words[-1].get("end") if words else covered_s
            if self._audio_start is None:
                self._audio_start = self._perf(first_s)
                self._start_s = first_s if isinstance(first_s, (int, float)) else None
            last = self._perf(last_s)
            if last is not None:
                self._audio_end = last
        covered = self._perf(covered_s)
        interval = None if self._last_result is None else received - self._last_result
        self._last_result = received
        return {
            "asr": None if covered is None else max(0.0, received - covered),
            "buffer": None if covered_s is None or self._start_s is None else max(0.0, covered_s - self._start_s),
            "interval": interval, "started": covered, "ended": received, "text": text,
        }

    def _flush_pending(self):
        """Koniec połączenia: zatwierdza to, co usługa zdążyła zwrócić tylko jako wynik pośredni."""
        if self._speech or self._interim:
            self._end_utterance(self._interim)

    def _fail(self, error: ApiError):
        if self.error is None:
            self.error = error
        log.warning("Deepgram (stream): %s", error)


class DeepgramStreamer:
    """Zamiennik ``LiveStreamer`` dla modeli Deepgram w trybie strumieniowym."""

    def __init__(self, model: ResolvedModel, settings):
        self.model = model
        self.settings = settings
        self.on_timing = None
        self.history = ""
        self.in_speech = False
        self._sessions: list[_Session] = []
        self._backlog: list = []
        self._watch: SilenceWatch | None = None
        self._watch_failed = False

    @property
    def finalize_ms(self) -> float:
        """Cisza (ms) po mowie, po której program wysyła Finalize; 0 = wyłączone."""
        value = dict((*self.model.params, *self.model.stream_params)).get(LOCAL_PREFIX + "finalize_ms", "")
        try:
            return max(0.0, float(value))
        except (TypeError, ValueError):
            return 0.0

    def _silence_watch(self) -> SilenceWatch | None:
        if self._watch is None and not self._watch_failed and self.finalize_ms > 0:
            try:
                self._watch = SilenceWatch(self.finalize_ms)
            except Exception:
                log.warning("Deepgram (stream): brak detektora ciszy — Finalize wyłączone", exc_info=True)
                self._watch_failed = True
        return self._watch

    # -- sesja ---------------------------------------------------------------------------

    @property
    def endpointing(self) -> str:
        return dict((*self.model.params, *self.model.stream_params)).get("endpointing", "")

    def _current(self) -> _Session | None:
        return self._sessions[-1] if self._sessions else None

    def _new_session(self) -> _Session:
        language = None if self.settings.language == "auto" else self.settings.language
        hotwords = tuple(
            word.strip() for word in re.split(r"[,;\n]", self.settings.hotwords or "") if word.strip()
        )
        url = stream_url(self.model, language, hotwords)
        headers = {"Authorization": f"Token {self.model.api_key}"}
        log.info("Deepgram (stream): %s", re.sub(r"keyterm=[^&]*|keywords=[^&]*", "keyterm=…", url))
        session = _Session(url, headers, self.model.service, self._timing)
        self._sessions.append(session)
        return session

    def _timing(self, kind: str, **data):
        if self.on_timing is not None:
            try:
                self.on_timing(kind, **data)
            except Exception:
                pass

    def reset_session(self):
        """Początek dyktowania: otwiera nowe połączenie (o ile nie ma już świeżego)."""
        current = self._current()
        if current is None or not current.active:
            self._new_session()
        if self._watch is not None:
            self._watch.reset()

    def _reset_utterance(self):
        """Po błędzie: porzuca trwające połączenie, następna sesja otworzy nowe."""
        current = self._current()
        if current is not None and current.error is not None:
            current.close()

    def close(self):
        for session in self._sessions:
            session.close()

    # -- wejście / zdarzenia -----------------------------------------------------------------

    def push(self, samples: np.ndarray, end_time: float | None = None):
        if samples is None or len(samples) == 0:
            return
        session = self._current()
        if session is None:
            session = self._new_session()
        if session.active:
            session.send_audio(to_pcm16(samples), end_time)
            watch = self._silence_watch()
            if watch is not None:
                try:
                    if watch.feed(samples):
                        session.finalize()
                except Exception:
                    log.warning("Deepgram (stream): błąd detektora ciszy — Finalize wyłączone", exc_info=True)
                    self._watch, self._watch_failed = None, True

    def step(self, force_final: bool = False) -> list:
        current = self._current()
        if force_final and current is not None:
            current.finish()
        events, self._backlog = self._backlog, []
        failed: ApiError | None = None
        for session in list(self._sessions):
            while True:
                try:
                    events.append(session.events.get_nowait())
                except queue.Empty:
                    break
            if session.error is not None and not session.error_reported:
                session.error_reported = True
                failed = failed or session.error
            if session.done.is_set() and session.events.empty() and session is not current:
                self._sessions.remove(session)
        for event in events:
            if event[0] == "speech":
                self.in_speech = event[1]
        if failed is not None:
            self._backlog = events
            raise failed
        return events
