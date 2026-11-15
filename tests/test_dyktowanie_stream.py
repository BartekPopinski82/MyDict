"""Dyktowanie strumieniowe Deepgram — na lokalnej atrapie serwera WebSocket, bez sieci i klucza."""

import json
import sys
import threading
import time
import urllib.parse
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("websockets")
from websockets.sync.server import serve  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import dyktowanie_api as api  # noqa: E402
from dyktowanie_api import ApiError, ResolvedModel, resolve  # noqa: E402
from dyktowanie_asr import DictationEngine, EngineCallbacks, LiveSettings  # noqa: E402
from dyktowanie_core import SessionText  # noqa: E402
from dyktowanie_stream import DeepgramStreamer, stream_url, to_pcm16  # noqa: E402


def results(text, final=False, speech_final=False):
    return json.dumps({
        "type": "Results", "is_final": final, "speech_final": speech_final,
        "channel": {"alternatives": [{"transcript": text}]},
    })


class FakeDeepgram:
    """Serwer udający /v1/listen: skrypt reaguje na kolejne porcje dźwięku."""

    def __init__(self, script=None, key="dg-key", drop_first=False, reject=None):
        self.script = script or self.default_script
        self.key = key
        self.drop_first = drop_first
        self.reject = reject
        self.connections = []     # {"path", "headers", "binary", "text"}
        self.lock = threading.Lock()
        self.server = serve(self.handle, "127.0.0.1", 0, process_request=self.check)
        self.port = self.server.socket.getsockname()[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base(self):
        return f"http://127.0.0.1:{self.port}/v1"

    def check(self, connection, request):
        if self.reject:
            return connection.respond(self.reject, "odrzucone")
        if request.headers.get("Authorization") != f"Token {self.key}":
            return connection.respond(401, "zły klucz")
        return None

    @staticmethod
    def default_script(ws, binary_count):
        if binary_count == 1:
            ws.send(results("dzień"))
            ws.send(results("dzień dobry"))
        elif binary_count == 2:
            ws.send(results("dzień dobry", final=True))
        elif binary_count == 3:
            ws.send(results("wszystkim", final=True, speech_final=True))

    def handle(self, ws):
        entry = {"path": ws.request.path, "auth": ws.request.headers.get("Authorization"), "binary": 0, "text": []}
        with self.lock:
            self.connections.append(entry)
            index = len(self.connections)
        try:
            for message in ws:
                if isinstance(message, bytes):
                    entry["binary"] += 1
                    if self.drop_first and index == 1:
                        ws.close(1011, "awaria")
                        return
                    self.script(ws, entry["binary"])
                else:
                    entry["text"].append(json.loads(message).get("type"))
                    if entry["text"][-1] == "Finalize":
                        ws.send(json.dumps({
                            "type": "Results", "is_final": True, "speech_final": False, "from_finalize": True,
                            "channel": {"alternatives": [{"transcript": "dzień dobry"}]},
                        }))
                    if entry["text"][-1] == "CloseStream":
                        ws.send(results("na koniec", final=True, speech_final=True))
                        ws.close()
                        return
        except Exception:
            pass

    def stop(self):
        self.server.shutdown()


def make_model(server, **overrides):
    values = dict(
        key="api:deepgram:nova-3", label="Deepgram · Nova-3", kind="deepgram", base_url=server.base,
        model="nova-3", api_key="dg-key", service="Deepgram",
        params=(("smart_format", "true"), ("punctuate", "true")),
        stream=True, stream_params=(("interim_results", "true"), ("endpointing", "200")),
    )
    values.update(overrides)
    return ResolvedModel(**values)


def chunk():
    return np.full(1600, 0.1, dtype=np.float32)


def collect(streamer, until, timeout=6.0):
    events = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        events.extend(streamer.step())
        if until(events):
            return events
        time.sleep(0.02)
    raise AssertionError(f"Nie doczekano zdarzeń: {events}")


@pytest.fixture
def server():
    fake = FakeDeepgram()
    yield fake
    fake.stop()


def test_stream_url_params_and_scheme():
    model = ResolvedModel("k", "l", "deepgram", "https://api.eu.deepgram.com/v1", "nova-3", "x", "Deepgram",
                          params=(("smart_format", "true"), ("utterances", "true")),
                          stream_params=(("interim_results", "true"), ("endpointing", "200")))
    url = stream_url(model, "pl", ("BAPO", "Whisper"))
    parts = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qsl(parts.query)
    assert url.startswith("wss://api.eu.deepgram.com/v1/listen?")
    assert ("encoding", "linear16") in query and ("sample_rate", "16000") in query
    assert ("interim_results", "true") in query and ("endpointing", "200") in query
    assert ("language", "pl") in query and ("keyterm", "BAPO") in query and ("keyterm", "Whisper") in query
    assert not any(name in ("utterances", "utt_split", "detect_language") for name, _ in query)
    auto = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(stream_url(model, None)).query))
    assert auto["language"] == "multi"
    old = ResolvedModel("k", "l", "deepgram", "http://h:1/v1", "nova-2", "x", "Deepgram")
    assert stream_url(old, "en", ("x",)).startswith("ws://h:1/v1/listen?") and "keywords=x" in stream_url(old, "en", ("x",))


def test_pcm_conversion():
    pcm = np.frombuffer(to_pcm16(np.array([0.0, 1.0, -1.0, 2.0], dtype=np.float32)), dtype="<i2")
    assert pcm.tolist() == [0, 32767, -32767, 32767]


def test_stream_events_and_session_text(server):
    streamer = DeepgramStreamer(make_model(server), LiveSettings(language="pl", hotwords="BAPO"))
    streamer.reset_session()
    session = SessionText()
    events = []
    for _ in range(3):
        streamer.push(chunk())
        time.sleep(0.15)
        events.extend(streamer.step())
    events.extend(collect(streamer, lambda ev: any(e[0] == "final" for e in events + ev)))
    kinds = [e[0] for e in events]
    assert kinds[0] == "speech" and events[0][1] is True
    assert ("partial", "", " dzień") in events and ("partial", "", " dzień dobry") in events
    assert ("partial", " dzień dobry", "") in events
    final = [e for e in events if e[0] == "final"][0]
    assert final[1] == " wszystkim"
    assert events[-1] == ("speech", False)
    for event in events:
        if event[0] == "partial":
            session.partial(event[1], event[2])
        elif event[0] == "final":
            session.final(event[1])
    assert session.committed == "dzień dobry wszystkim" and session.tentative == ""
    streamer.close()

    entry = server.connections[0]
    query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(entry["path"]).query))
    assert entry["path"].startswith("/v1/listen?")
    assert query["interim_results"] == "true" and query["endpointing"] == "200"
    assert query["language"] == "pl" and query["keyterm"] == "BAPO" and query["smart_format"] == "true"
    assert entry["auth"] == "Token dg-key"
    assert entry["binary"] == 3


def timed(text, start, duration, words, final=False, speech_final=False):
    return json.dumps({
        "type": "Results", "is_final": final, "speech_final": speech_final, "start": start, "duration": duration,
        "channel": {"alternatives": [{
            "transcript": text, "words": [{"word": w, "start": s, "end": e} for w, s, e in words],
        }]},
    })


def test_timing_events_feed_latency_logs(tmp_path):
    def script(ws, binary_count):
        if binary_count == 1:
            ws.send(timed("dzień", 0.0, 0.1, [("dzień", 0.02, 0.08)]))
        elif binary_count == 2:
            ws.send(timed("dzień dobry", 0.0, 0.2, [("dzień", 0.02, 0.08), ("dobry", 0.11, 0.19)], final=True))
        elif binary_count == 3:
            ws.send(timed("", 0.2, 0.1, [], final=True, speech_final=True))

    import csv

    from dyktowanie_latencja import LatencyTracker

    fake = FakeDeepgram(script=script)
    tracker = LatencyTracker()
    tracker.configure(tmp_path / "latencja.csv")
    tracker.model = "Deepgram · Nova-3"
    timing = []

    def on_timing(kind, **data):
        timing.append((kind, data))
        tracker.stream_event(kind, **data)

    streamer = DeepgramStreamer(make_model(fake), LiveSettings(language="pl"))
    streamer.on_timing = on_timing
    streamer.reset_session()
    t0 = time.perf_counter() - 1.0          # porcje po 0,1 s; n-ta kończy się w t0 + n·0,1
    for n in range(1, 4):
        streamer.push(chunk(), end_time=t0 + n * 0.1)
        time.sleep(0.15)
    collect(streamer, lambda ev: any(e[0] == "final" for e in ev))
    streamer.close()
    fake.stop()

    assert [kind for kind, _ in timing] == ["speech_start", "pass", "pass", "speech_end", "final"]
    data = [d for _, d in timing]
    assert data[0]["audio_start"] == pytest.approx(t0 + 0.02, abs=1e-6)
    assert data[1]["delta"] == "" and data[1]["started"] == pytest.approx(t0 + 0.1, abs=1e-6)
    assert data[1]["asr"] == pytest.approx(data[1]["ended"] - (t0 + 0.1), abs=1e-6) and data[1]["asr"] > 0.5
    assert data[2]["delta"] == " dzień dobry" and data[2]["buffer"] == pytest.approx(0.18)
    assert data[2]["interval"] > 0
    assert data[3]["audio_end"] == pytest.approx(t0 + 0.19, abs=1e-6)
    assert data[4]["delta"] == "" and data[4]["started"] == pytest.approx(t0 + 0.3, abs=1e-6)

    tracker.ui_text(11, False)
    tracker.ui_text(11, True)
    tracker.typed(0, 11)
    row = list(csv.DictReader(open(tmp_path / "latencja.csv", encoding="utf-8-sig"), delimiter=";"))[0]
    assert row["model"] == "Deepgram · Nova-3" and row["przebiegi_asr"] == "2"
    assert row["dlugosc_mowy_ms"] == "170" and int(row["final_po_koncu_ms"]) > 0
    assert row["pierwszy_tekst_ms"] and row["asr_final_ms"]
    passes = list(csv.DictReader(open(tmp_path / "przebiegi.csv", encoding="utf-8-sig"), delimiter=";"))
    assert [p["przebieg"] for p in passes] == ["1", "2", "final"]
    assert all(p["model"] == "Deepgram · Nova-3" and p["asr_ms"] for p in passes)
    assert passes[1]["tekst"] == "dzień dobry" and passes[2]["final"] == "True"


def test_stop_closes_stream_and_returns_tail(server):
    streamer = DeepgramStreamer(make_model(server), LiveSettings())
    streamer.reset_session()
    streamer.push(chunk())
    time.sleep(0.2)
    streamer.step(force_final=True)       # = stop: CloseStream po wysłaniu dźwięku
    events = collect(streamer, lambda ev: ("final", " na koniec") in ev)
    assert ("final", " na koniec") in events
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and "CloseStream" not in server.connections[0]["text"]:
        time.sleep(0.02)
    assert server.connections[0]["text"] == ["CloseStream"]
    streamer.close()


def test_unfinished_interim_is_committed_when_connection_ends():
    fake = FakeDeepgram(script=lambda ws, n: ws.send(results("niedokończone")) if n == 1 else None)
    try:
        streamer = DeepgramStreamer(make_model(fake), LiveSettings())
        streamer.reset_session()
        streamer.push(chunk())
        time.sleep(0.3)
        streamer.step(force_final=True)
        events = collect(streamer, lambda ev: ("speech", False) in ev)
        finals = [e[1] for e in events if e[0] == "final"]
        assert " niedokończone" in finals or " na koniec" in finals
        streamer.close()
    finally:
        fake.stop()


def test_wrong_key_reports_polish_error():
    fake = FakeDeepgram(key="inny")
    try:
        streamer = DeepgramStreamer(make_model(fake), LiveSettings())
        streamer.reset_session()
        streamer.push(chunk())
        with pytest.raises(ApiError, match="odrzucił klucz"):
            collect(streamer, lambda ev: False, timeout=5)
    finally:
        fake.stop()


def test_reconnects_after_dropped_connection():
    fake = FakeDeepgram(drop_first=True)
    try:
        streamer = DeepgramStreamer(make_model(fake), LiveSettings())
        streamer.reset_session()
        events = []
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and not any(e == ("partial", "", " dzień") for e in events):
            streamer.push(chunk())
            time.sleep(0.1)
            events.extend(streamer.step())
        assert ("partial", "", " dzień") in events
        assert len(fake.connections) >= 2
        streamer.close()
    finally:
        fake.stop()


def test_restart_opens_new_connection_and_keeps_old_tail(server):
    streamer = DeepgramStreamer(make_model(server), LiveSettings())
    streamer.reset_session()
    streamer.push(chunk())
    time.sleep(0.2)
    streamer.step(force_final=True)
    streamer.reset_session()              # nowa sesja, zanim stara dokończy
    streamer.push(chunk())
    events = collect(streamer, lambda ev: ("final", " na koniec") in ev)
    assert ("final", " na koniec") in events
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and len(server.connections) < 2:
        time.sleep(0.02)
    assert len(server.connections) == 2
    streamer.close()


def test_catalog_flags_stream_only_for_deepgram():
    dg = resolve("api:deepgram:nova-3", {"deepgram": api.protect_secret("k")}, [])
    assert dg.stream and dict(dg.stream_params) == {"interim_results": "true", "endpointing": "200", "local_finalize_ms": "400"}
    groq = resolve("api:groq:whisper-large-v3-turbo", {}, [])
    assert not groq.stream and groq.stream_params == ()
    custom = api.CustomModel("d1", "DG własny", "deepgram", "https://api.deepgram.com/v1", "nova-3-medical")
    assert resolve("custom:d1", {"deepgram": api.protect_secret("k")}, [custom]).stream


class _Callbacks(EngineCallbacks):
    def __init__(self):
        self.ready_info = None
        self.messages = []

    def ready(self, info):
        self.ready_info = info

    def status(self, text, busy):
        self.messages.append(text)

    def failed(self, text):
        self.messages.append("BŁĄD " + text)


def test_engine_uses_streamer_for_deepgram(server):
    callbacks = _Callbacks()
    engine = DictationEngine(callbacks)
    engine._load_cloud(make_model(server))
    assert isinstance(engine.streamer, DeepgramStreamer)
    assert callbacks.ready_info is not None and "endpointing 200" in callbacks.ready_info.vad
    engine._release_model()
    assert engine.streamer is None


def test_engine_falls_back_without_stream_flag(server):
    callbacks = _Callbacks()
    engine = DictationEngine(callbacks)
    engine._load_cloud(make_model(server, stream=False))
    assert engine.streamer is not None and not isinstance(engine.streamer, DeepgramStreamer)


# -- lokalne wykrywanie ciszy i Finalize --------------------------------------------------------


class _ScriptVAD:
    """VAD z gotowym przebiegiem: 1 = mowa, 0 = cisza (jedna wartość na ramkę 512 próbek)."""

    frame = 512

    def __init__(self, pattern):
        self.pattern = list(pattern)

    def __call__(self, samples):
        count = len(samples) // self.frame
        out, self.pattern = self.pattern[:count], self.pattern[count:]
        return np.array(out + [0.0] * (count - len(out)), dtype=np.float32)


def _frames(count):
    return np.zeros(count * 512, dtype=np.float32)


def test_stream_url_skips_local_params():
    model = ResolvedModel(
        key="k", label="l", kind="deepgram", base_url="https://api.eu.deepgram.com/v1", model="nova-3",
        api_key="x", service="Deepgram", stream=True,
        stream_params=(("interim_results", "true"), ("local_finalize_ms", "400")),
    )
    query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(stream_url(model, "pl")).query))
    assert query["interim_results"] == "true" and "local_finalize_ms" not in query


def test_silence_watch_fires_once_after_speech_then_silence():
    from dyktowanie_stream import SilenceWatch

    # 13 ramek ciszy po mowie = 416 ms >= 400 ms
    watch = SilenceWatch(400, vad=_ScriptVAD([0, 0, 1, 1, 1] + [0] * 20))
    assert watch.feed(_frames(5)) is False            # sama mowa
    assert watch.feed(_frames(10)) is False           # 320 ms ciszy
    assert watch.feed(_frames(3)) is True             # 416 ms — próg przekroczony
    assert watch.feed(_frames(5)) is False            # jednorazowo, dalsza cisza nic nie robi


def test_silence_watch_ignores_silence_without_speech_and_rearms_on_new_speech():
    from dyktowanie_stream import SilenceWatch

    watch = SilenceWatch(100, vad=_ScriptVAD([0] * 10 + [1, 0, 0, 0, 0, 1, 0, 0, 0, 0]))
    assert watch.feed(_frames(10)) is False           # cisza bez wcześniejszej mowy
    assert watch.feed(_frames(5)) is True             # 1, potem 4 ramki ciszy (128 ms)
    assert watch.feed(_frames(5)) is True             # nowa mowa uzbraja ponownie


def test_local_finalize_ms_parsing(server):
    assert DeepgramStreamer(make_model(server), LiveSettings()).finalize_ms == 0.0
    model = make_model(server, stream_params=(("local_finalize_ms", "350"),))
    assert DeepgramStreamer(model, LiveSettings()).finalize_ms == 350.0
    model = make_model(server, stream_params=(("local_finalize_ms", "abc"),))
    assert DeepgramStreamer(model, LiveSettings()).finalize_ms == 0.0


def test_finalize_is_sent_and_from_finalize_ends_utterance():
    from dyktowanie_stream import SilenceWatch

    fake = FakeDeepgram(script=lambda ws, n: ws.send(results("dzień")) if n == 1 else None)
    streamer = DeepgramStreamer(
        make_model(fake, stream_params=(("interim_results", "true"), ("local_finalize_ms", "100"))),
        LiveSettings(language="pl"),
    )
    try:
        streamer._watch = SilenceWatch(100, vad=_ScriptVAD([1, 1] + [0] * 10))
        streamer.reset_session()
        streamer.push(_frames(2) + 0.1)                # mowa → wynik pośredni „dzień”
        events = collect(streamer, lambda ev: any(e == ("partial", "", " dzień") for e in ev))
        streamer.push(_frames(4))                      # 128 ms ciszy >= 100 ms → Finalize
        events += collect(streamer, lambda ev: any(e[0] == "final" for e in events + ev))
        assert "Finalize" in fake.connections[0]["text"]
        assert ("final", " dzień dobry") in events and events[-1] == ("speech", False)
    finally:
        streamer.close()
        fake.stop()


def test_empty_from_finalize_without_speech_is_ignored():
    from dyktowanie_stream import _Session

    session = _Session.__new__(_Session)
    session.events = __import__("queue").SimpleQueue()
    session._timing = lambda *a, **k: None
    session._speech, session._utterance, session._interim = False, "", ""
    session._clock, session._clock_lock = __import__("collections").deque(), threading.Lock()
    session._base, session._audio_start, session._audio_end, session._start_s, session._last_result = 0, None, None, None, None
    session._handle({"type": "Results", "is_final": True, "speech_final": False, "from_finalize": True,
                     "channel": {"alternatives": [{"transcript": ""}]}})
    assert session.events.empty()
