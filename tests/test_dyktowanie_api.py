"""Modele w chmurze — na lokalnej atrapie API, bez sieci, mikrofonu i GPU."""

import email.parser
import email.policy
import json
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import dyktowanie_api as api  # noqa: E402
from dyktowanie_api import (  # noqa: E402
    ApiError,
    CustomModel,
    Request,
    ResolvedModel,
    clean_text,
    encode_wav,
    resolve,
    transcribe,
)
from dyktowanie_asr import CloudTranscriber, DictationEngine, LiveSettings  # noqa: E402
from dyktowanie_core import SAMPLE_RATE, SessionText  # noqa: E402


# ---------------------------------------------------------------------------
# Atrapa API
# ---------------------------------------------------------------------------


def _multipart(content_type, body):
    message = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(
        b"Content-Type: " + content_type.encode() + b"\r\n\r\n" + body
    )
    fields, files = [], {}
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        payload = part.get_payload(decode=True) or b""
        if part.get_filename():
            files[name] = payload
        else:
            fields.append((name, payload.decode()))
    return fields, files


class State:
    def __init__(self):
        self.requests = []
        self.reject_fields = set()   # pola, które „serwer” odrzuca błędem 400
        self.fail_once = 0
        self.polls = 0


def make_server(state):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def _send(self, status, payload):
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _entry(self, body=b""):
            parsed = urllib.parse.urlsplit(self.path)
            entry = {"method": self.command, "path": parsed.path, "query": urllib.parse.parse_qsl(parsed.query),
                     "headers": dict(self.headers), "fields": [], "files": {}, "body": body}
            ctype = self.headers.get("Content-Type", "")
            if ctype.startswith("multipart/form-data"):
                entry["fields"], entry["files"] = _multipart(ctype, body)
            state.requests.append(entry)
            return entry

        def do_GET(self):
            entry = self._entry()
            if self.headers.get("Authorization") == "Bearer bad":
                return self._send(401, {"error": {"message": "Incorrect API key"}})
            if entry["path"].endswith("/models") or entry["path"].endswith("/projects"):
                return self._send(200, {"data": []})
            if entry["path"].startswith("/aai/transcript/"):
                state.polls += 1
                if state.polls < 2:
                    return self._send(200, {"status": "processing"})
                return self._send(200, {"status": "completed", "text": "Tekst z AssemblyAI."})
            self._send(404, {"error": "not found"})

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            entry = self._entry(body)
            path = entry["path"]
            if state.fail_once:
                state.fail_once -= 1
                return self._send(503, {"error": {"message": "overloaded"}})
            if path.endswith("/audio/transcriptions"):
                names = {name for name, _ in entry["fields"]}
                if names & state.reject_fields:
                    return self._send(400, {"error": {"message": "Unsupported parameter"}})
                return self._send(200, {"text": "Dzień dobry, piszę z chmury."})
            if path.endswith("/speech-to-text"):
                return self._send(200, {"text": "Tekst z ElevenLabs."})
            if path.endswith("/listen"):
                return self._send(200, {"results": {"channels": [{"alternatives": [{"transcript": "Tekst z Deepgram."}]}]}})
            if path == "/aai/upload":
                return self._send(200, {"upload_url": "https://cdn/x"})
            if path == "/aai/transcript":
                return self._send(200, {"id": "t1"})
            self._send(404, {"error": "not found"})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture
def mock():
    state = State()
    server = make_server(state)
    state.base = f"http://127.0.0.1:{server.server_address[1]}"
    yield state
    server.shutdown()


def model(state, kind="openai", name="gpt-4o-transcribe", path="/v1", key="sk-test"):
    return ResolvedModel("api:test", "Test", kind, state.base + path, name, key, "Test")


def speech(seconds=1.0):
    t = np.arange(int(SAMPLE_RATE * seconds)) / SAMPLE_RATE
    return (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


# ---------------------------------------------------------------------------
# Testy
# ---------------------------------------------------------------------------


def test_wav_is_16khz_mono_pcm():
    import io
    import wave

    data = encode_wav(speech(0.5))
    with wave.open(io.BytesIO(data)) as handle:
        assert handle.getframerate() == 16000
        assert handle.getnchannels() == 1
        assert handle.getsampwidth() == 2
        assert handle.getnframes() == 8000


def test_openai_request_fields(mock):
    text = transcribe(model(mock), Request(encode_wav(speech()), "pl", ("BAPO", "Kraków"), "Wcześniej było tak."))
    assert text == "Dzień dobry, piszę z chmury."
    entry = mock.requests[-1]
    fields = dict(entry["fields"])
    assert entry["path"] == "/v1/audio/transcriptions"
    assert entry["headers"]["Authorization"] == "Bearer sk-test"
    assert fields["model"] == "gpt-4o-transcribe"
    assert fields["language"] == "pl"
    assert "BAPO, Kraków" in fields["prompt"] and "Wcześniej było tak." in fields["prompt"]
    assert entry["files"]["file"][:4] == b"RIFF"


def test_gpt_transcribe_uses_keywords(mock):
    transcribe(model(mock, name="gpt-transcribe"), Request(encode_wav(speech()), "pl", ("BAPO",)))
    fields = mock.requests[-1]["fields"]
    assert ("keywords[]", "BAPO") in fields
    assert ("languages[]", "pl") in fields


def test_openai_falls_back_to_minimal_request(mock):
    mock.reject_fields = {"prompt"}
    text = transcribe(model(mock), Request(encode_wav(speech()), "pl", ("BAPO",)))
    assert text.startswith("Dzień dobry")
    names = [name for name, _ in mock.requests[-1]["fields"]]
    assert names == ["model", "response_format"]


def test_retry_after_server_error(mock):
    mock.fail_once = 1
    assert transcribe(model(mock), Request(encode_wav(speech())))
    assert len(mock.requests) == 2


def test_elevenlabs_deepgram_assemblyai(mock):
    wav = encode_wav(speech())
    assert transcribe(model(mock, "elevenlabs", "scribe_v2"), Request(wav, "pl", ("BAPO",))) == "Tekst z ElevenLabs."
    fields = dict(mock.requests[-1]["fields"])
    assert fields["model_id"] == "scribe_v2" and fields["language_code"] == "pl" and fields["keyterms"] == "BAPO"
    assert mock.requests[-1]["headers"]["xi-api-key"] == "sk-test"

    assert transcribe(model(mock, "deepgram", "nova-3"), Request(wav, "pl", ("BAPO",))) == "Tekst z Deepgram."
    query = mock.requests[-1]["query"]
    assert ("model", "nova-3") in query and ("keyterm", "BAPO") in query and ("language", "pl") in query
    assert mock.requests[-1]["headers"]["Authorization"] == "Token sk-test"

    aai = model(mock, "assemblyai", "universal-2", path="/aai")
    assert api._assemblyai(aai, Request(wav), poll_interval=0.01) == "Tekst z AssemblyAI."


def test_missing_key_is_reported():
    resolved = ResolvedModel("api:x", "X", "openai", "http://127.0.0.1:9", "m", "", "OpenAI")
    with pytest.raises(ApiError, match="Brak klucza"):
        transcribe(resolved, Request(b""))


def test_custom_local_server_without_key(mock):
    custom = CustomModel("abc", "Mój serwer", "openai", mock.base + "/v1", "whisper-local")
    resolved = resolve("custom:abc", {}, [custom])
    assert resolved is not None and not resolved.needs_key
    assert transcribe(resolved, Request(encode_wav(speech()), None)).startswith("Dzień dobry")
    entry = mock.requests[-1]
    assert "Authorization" not in entry["headers"]
    assert "language" not in dict(entry["fields"])


def test_custom_model_reuses_provider_key(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-env")
    custom = CustomModel("g1", "Groq nowy", "openai", "https://api.groq.com/openai/v1/", "whisper-next")
    resolved = resolve("custom:g1", {}, [custom])
    assert resolved.api_key == "gsk-env" and resolved.service == "Groq" and resolved.model == "whisper-next"


def test_resolve_catalog_and_saved_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(api, "lite_key", lambda _pid: "")
    monkeypatch.setattr(api, "_secrets_files", lambda: iter(()))
    resolved = resolve("api:openai:gpt-4o-transcribe", {"openai": api.protect_secret("sk-saved")}, [])
    assert resolved.api_key == "sk-saved" and resolved.base_url == "https://api.openai.com/v1"
    assert resolve("api:openai:gpt-4o-transcribe", {}, []).needs_key


def test_lite_keys_are_reused(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    monkeypatch.setattr(api, "_secrets_files", lambda: iter(()))
    monkeypatch.setenv("BAPO_LITE_CONFIG_DIR", str(tmp_path))
    import base64
    (tmp_path / "ustawienia.json").write_text(json.dumps(
        {"keys": {"deepgram": "plain:" + base64.b64encode(b"dg-lite").decode()}}
    ), encoding="utf-8")
    assert api.find_key("deepgram") == ("dg-lite", "lite")


def test_normalize_url_and_custom_roundtrip():
    assert api.normalize_base_url("api.groq.com/openai/v1/audio/transcriptions/") == "https://api.groq.com/openai/v1"
    model_ = CustomModel("x1", "Nazwa", "deepgram", "https://api.deepgram.com/v1", "nova-3-medical", "plain:YQ==")
    assert CustomModel.from_dict(model_.to_dict()) == model_
    assert CustomModel.from_dict({"id": "x", "model": ""}) is None


def test_clean_text_removes_subtitle_hallucinations():
    assert clean_text("Napisy stworzone przez społeczność Amara.org") == ""
    assert clean_text("Wysyłam raport. Dziękuję za obejrzenie!") == "Wysyłam raport."


def test_check_key(mock):
    assert api.check_key("openai", mock.base + "/v1", "sk-test")[0]
    ok, message = api.check_key("openai", mock.base + "/v1", "bad")
    assert not ok and "401" in message


# ---------------------------------------------------------------------------
# Silnik dyktowania w trybie chmurowym
# ---------------------------------------------------------------------------


class Recorder:
    def __init__(self):
        self.session = SessionText()
        self.ready_event = threading.Event()
        self.final_event = threading.Event()
        self.failures = []
        self.partials = 0
        self.info = None

    def status(self, text, busy): pass
    def speech(self, active): pass
    def listening(self, active): pass

    def ready(self, info):
        self.info = info
        self.ready_event.set()

    def failed(self, text):
        self.failures.append(text)
        self.ready_event.set()

    def partial(self, delta, tentative):
        self.partials += 1
        self.session.partial(delta, tentative)

    def final(self, delta):
        self.session.final(delta)
        if delta:
            self.final_event.set()


def test_cloud_transcriber_returns_words(mock):
    transcriber = CloudTranscriber(model(mock), LiveSettings(hotwords="BAPO; Kraków"))
    words = transcriber(speech(2.0), "kontekst", True)
    assert "".join(w.text for w in words) == " Dzień dobry, piszę z chmury."
    assert words[-1].end == pytest.approx(2.0)
    assert ("prompt" in dict(mock.requests[-1]["fields"]))


def test_engine_cloud_mode_one_request_per_utterance(mock, monkeypatch):
    # Sygnał testowy to czysty ton: detektor energii uznaje go za mowę, a Silero VAD
    # (słusznie) nie — dlatego test nie może zależeć od tego, czy Silero jest zainstalowany.
    import dyktowanie_asr
    from dyktowanie_core import EnergyVAD

    monkeypatch.setattr(dyktowanie_asr, "make_vad", EnergyVAD)
    recorder = Recorder()
    engine = DictationEngine(recorder)
    engine.start()
    try:
        engine.load("api:test", "auto", model(mock))
        assert recorder.ready_event.wait(5)
        assert recorder.info.device == "api" and "Chmura" in recorder.info.label
        assert engine.settings.stream.live_passes is False
        engine.listening = True
        engine.commands.put(("start",))
        silence = np.zeros(int(SAMPLE_RATE * 0.5), dtype=np.float32)
        for block in (silence, speech(2.5), np.zeros(int(SAMPLE_RATE * 1.2), dtype=np.float32)):
            for start in range(0, len(block), 800):
                engine.audio.put(block[start:start + 800])
            time.sleep(0.05)
        assert recorder.final_event.wait(5), recorder.failures
    finally:
        engine.shutdown()
    assert recorder.session.full() == "Dzień dobry, piszę z chmury."
    assert recorder.partials == 0
    posts = [r for r in mock.requests if r["method"] == "POST"]
    assert len(posts) == 1


def test_engine_cloud_without_key_fails_cleanly():
    recorder = Recorder()
    engine = DictationEngine(recorder)
    engine.start()
    try:
        engine.load("api:x", "auto", ResolvedModel("api:x", "X", "openai", "http://127.0.0.1:9", "m", "", "OpenAI"))
        assert recorder.ready_event.wait(5)
    finally:
        engine.shutdown()
    assert recorder.failures and "Brak klucza" in recorder.failures[0]
    assert engine.streamer is None


# ---------------------------------------------------------------------------
# Katalog z dostawcy.json, endpointy, parametry
# ---------------------------------------------------------------------------


def test_deepgram_defaults_to_eu_server_from_json():
    assert api.PROVIDERS["deepgram"].base_url == "https://api.eu.deepgram.com/v1"
    assert api.DEFAULT_BASE_URLS["deepgram"] == "https://api.eu.deepgram.com/v1"
    resolved = resolve("api:deepgram:nova-3", {"deepgram": api.protect_secret("dg")}, [])
    assert resolved.base_url == "https://api.eu.deepgram.com/v1"
    assert ("smart_format", "true") in resolved.params and ("punctuate", "true") in resolved.params


def test_endpoint_override_per_provider():
    stored = {"deepgram": "api.deepgram.com/v1/listen/"}
    resolved = resolve("api:deepgram:nova-3", {}, [], stored)
    assert resolved.base_url == "https://api.deepgram.com/v1"
    assert api.provider_endpoint("deepgram", {"deepgram": ""}) == "https://api.eu.deepgram.com/v1"


def test_us_host_still_matches_deepgram_provider():
    assert api.provider_for_url("deepgram", "https://api.deepgram.com/v1") == "deepgram"
    assert api.provider_for_url("deepgram", "https://api.eu.deepgram.com/v1") == "deepgram"
    assert api.provider_for_url("deepgram", "https://example.com/v1") is None


def test_deepgram_params_come_from_catalog(mock):
    resolved = model(mock, "deepgram", "nova-3")
    resolved.params = (("smart_format", "true"), ("endpointing", "200"))
    assert transcribe(resolved, Request(encode_wav(speech()), "pl"))
    query = mock.requests[-1]["query"]
    assert ("endpointing", "200") in query and ("smart_format", "true") in query
    assert ("interim_results", "true") not in query  # streaming_params nie idą do HTTP


def test_catalog_file_loads_and_survives_bad_input(tmp_path):
    providers, models = api.load_catalog()
    assert "deepgram" in providers and "api:deepgram:nova-3" in models
    assert providers["deepgram"].streaming_params == (("interim_results", "true"), ("endpointing", "200"), ("local_finalize_ms", "400"))
    bad = tmp_path / "dostawcy.json"
    bad.write_text("{nie json", encoding="utf-8")
    with pytest.raises(RuntimeError, match="dostawcy.json"):
        api.load_catalog(bad)
