"""Rozpoznawanie mowy przez API w chmurze — alternatywa dla lokalnego Whispera.

Moduł nie zależy od Qt ani od SDK dostawców (tylko biblioteka standardowa
i numpy). Obsługuje cztery rodzaje API:

* ``openai``     — OpenAI i każde API zgodne z OpenAI (Groq, własny serwer…),
* ``elevenlabs`` — ElevenLabs Speech to Text,
* ``deepgram``   — Deepgram /listen,
* ``assemblyai`` — AssemblyAI (wysyłka + kolejka).

Dyktowanie wysyła do usługi jedną wypowiedź naraz (dźwięk do pauzy) jako
plik WAV 16 kHz i dostaje z powrotem gotowy tekst.
"""

from __future__ import annotations

import base64
import http.client
import io
import json
import os
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request
import uuid
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np


IS_WINDOWS = sys.platform.startswith("win")
HERE = Path(__file__).resolve().parent
USER_AGENT = "BAPO-dyktowanie/1.1"
REQUEST_TIMEOUT = 45.0


class ApiError(Exception):
    """Błąd zgłoszony przez usługę lub połączenie z nią."""

    def __init__(self, message: str, *, status: int | None = None, retryable: bool = False):
        super().__init__(message)
        self.status = status
        self.retryable = retryable


# ---------------------------------------------------------------------------
# Katalog usług i modeli
# ---------------------------------------------------------------------------

API_KINDS = {
    "openai": "Zgodne z OpenAI (OpenAI, Groq, własny serwer…)",
    "elevenlabs": "ElevenLabs",
    "deepgram": "Deepgram",
    "assemblyai": "AssemblyAI",
}


def normalize_base_url(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    # Ktoś może wkleić pełny adres metody zamiast adresu bazowego.
    for suffix in ("/audio/transcriptions", "/speech-to-text", "/listen", "/transcript", "/upload"):
        if url.endswith(suffix):
            url = url[: -len(suffix)]
    if url and "://" not in url:
        url = "https://" + url
    return url


@dataclass(frozen=True)
class Provider:
    id: str
    name: str
    kind: str
    base_url: str
    env_vars: tuple
    key_url: str
    extra_hosts: tuple = ()        # inne adresy tej samej usługi (np. serwer USA obok EU)
    params: tuple = ()             # ((nazwa, wartość), …) dodawane do zapytania HTTP
    streaming_params: tuple = ()   # tylko tryb strumieniowy (WebSocket) — HTTP ich nie wysyła
    endpoint_note: str = ""
    stream: bool = False           # True = dyktowanie strumieniowe przez WebSocket (Deepgram)


@dataclass(frozen=True)
class CloudModel:
    key: str
    provider: str
    model: str
    label: str
    note: str
    params: tuple = ()
    streaming_params: tuple = ()
    stream: bool | None = None     # None = jak dostawca


CATALOG_FILE = HERE / "dostawcy.json"


def _pairs(value) -> tuple:
    if not isinstance(value, dict):
        return ()
    return tuple((str(k), str(v)) for k, v in value.items() if not str(k).startswith("_"))


def load_catalog(path: Path = CATALOG_FILE) -> tuple[dict, dict]:
    """Dostawcy i modele z pliku dostawcy.json (obok programu)."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"Nie można wczytać {path.name} ({path}): {exc}") from exc
    providers = {}
    for provider_id, item in (raw.get("providers") or {}).items():
        kind = str(item.get("kind") or "openai")
        if kind not in API_KINDS:
            continue
        providers[provider_id] = Provider(
            id=provider_id,
            name=str(item.get("name") or provider_id),
            kind=kind,
            base_url=normalize_base_url(str(item["base_url"])),
            env_vars=tuple(item.get("env_vars") or ()),
            key_url=str(item.get("key_url") or ""),
            extra_hosts=tuple(str(h).casefold() for h in item.get("extra_hosts") or ()),
            params=_pairs(item.get("params")),
            streaming_params=_pairs(item.get("streaming_params")),
            endpoint_note=str(item.get("endpoint_note") or ""),
            stream=bool(item.get("stream")) and kind == "deepgram",
        )
    models = {}
    for item in raw.get("models") or ():
        if item.get("provider") not in providers:
            continue
        model = CloudModel(
            key=str(item["key"]), provider=str(item["provider"]), model=str(item["model"]),
            label=str(item.get("label") or item["model"]), note=str(item.get("note") or ""),
            params=_pairs(item.get("params")),
            streaming_params=_pairs(item.get("streaming_params")),
            stream=bool(item["stream"]) if "stream" in item else None,
        )
        models[model.key] = model
    return providers, models


PROVIDERS, CLOUD_MODELS = load_catalog()

# Domyślny adres dla każdego rodzaju API (podpowiedź w formularzu własnego modelu).
DEFAULT_BASE_URLS: dict = {}
for _provider in PROVIDERS.values():
    DEFAULT_BASE_URLS.setdefault(_provider.kind, _provider.base_url)


def is_cloud_key(key: str | None) -> bool:
    return bool(key) and (key.startswith("api:") or key.startswith("custom:"))


@dataclass
class CustomModel:
    """Model wpisany ręcznie przez użytkownika (formularz „Własny model API”)."""

    id: str
    name: str
    kind: str
    base_url: str
    model: str
    key: str = ""          # zaszyfrowany (protect_secret); pusty = klucz dostawcy

    @property
    def combo_key(self) -> str:
        return f"custom:{self.id}"

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "kind": self.kind,
            "base_url": self.base_url, "model": self.model, "key": self.key,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CustomModel | None":
        try:
            kind = str(data.get("kind") or "openai")
            if kind not in API_KINDS:
                kind = "openai"
            model = cls(
                id=str(data["id"]),
                name=str(data.get("name") or data.get("model") or "Własny model"),
                kind=kind,
                base_url=normalize_base_url(str(data.get("base_url") or DEFAULT_BASE_URLS[kind])),
                model=str(data.get("model") or ""),
                key=str(data.get("key") or ""),
            )
        except (KeyError, TypeError, AttributeError):
            return None
        return model if model.id and model.model else None

    @staticmethod
    def new_id() -> str:
        return uuid.uuid4().hex[:10]


def provider_for_url(kind: str, base_url: str) -> str | None:
    """Rozpoznaje znanego dostawcę po adresie — wtedy można użyć jego klucza."""
    base = normalize_base_url(base_url).casefold()
    for provider in PROVIDERS.values():
        if provider.kind == kind and normalize_base_url(provider.base_url).casefold() == base:
            return provider.id
    host = urllib.parse.urlsplit(base).hostname or ""
    for provider in PROVIDERS.values():
        if provider.kind != kind:
            continue
        hosts = {(urllib.parse.urlsplit(provider.base_url).hostname or "").casefold(), *provider.extra_hosts}
        hosts.discard("")
        if host in hosts:
            return provider.id
    return None


@dataclass
class ResolvedModel:
    """Wszystko, czego silnik potrzebuje do wywołania usługi."""

    key: str
    label: str
    kind: str
    base_url: str
    model: str
    api_key: str
    service: str          # nazwa usługi do komunikatów (np. „OpenAI”)
    key_required: bool = True   # False: własny serwer bez uwierzytelniania
    params: tuple = ()          # dodatkowe parametry zapytania z dostawcy.json
    stream: bool = False        # dyktowanie strumieniowe (WebSocket) zamiast POST po pauzie
    stream_params: tuple = ()   # parametry połączenia strumieniowego (interim_results, endpointing…)

    @property
    def needs_key(self) -> bool:
        return self.key_required and not self.api_key


# ---------------------------------------------------------------------------
# Klucze API
# ---------------------------------------------------------------------------

ENTROPY = b"BAPO-dyktowanie/v1"
LITE_ENTROPY = b"BAPO-transcript-lite/v1"


def _dpapi(data: bytes, protect: bool, entropy_bytes: bytes = ENTROPY) -> bytes:
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def blob(raw: bytes):
        buffer = ctypes.create_string_buffer(raw, len(raw))
        return DATA_BLOB(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))), buffer

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    data_in, _keep_data = blob(data)
    entropy, _keep_entropy = blob(entropy_bytes)
    data_out = DATA_BLOB()
    flags = 0x01  # CRYPTPROTECT_UI_FORBIDDEN
    function = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    ok = function(ctypes.byref(data_in), None, ctypes.byref(entropy), None, None, flags, ctypes.byref(data_out))
    if not ok:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(data_out.pbData, data_out.cbData)
    finally:
        kernel32.LocalFree(data_out.pbData)


def protect_secret(secret: str) -> str:
    """Szyfruje klucz kontem Windows (DPAPI); na innych systemach tylko koduje."""
    secret = (secret or "").strip()
    if not secret:
        return ""
    raw = secret.encode("utf-8")
    if IS_WINDOWS:
        try:
            return "dpapi:" + base64.b64encode(_dpapi(raw, True)).decode("ascii")
        except Exception:
            pass
    return "plain:" + base64.b64encode(raw).decode("ascii")


def unprotect_secret(stored: str, entropy: bytes = ENTROPY) -> str:
    try:
        kind, _, payload = (stored or "").partition(":")
        raw = base64.b64decode(payload.encode("ascii"))
        if kind == "dpapi":
            return _dpapi(raw, False, entropy).decode("utf-8") if IS_WINDOWS else ""
        if kind == "plain":
            return raw.decode("utf-8")
    except Exception:
        pass
    return ""


def _secrets_files():
    """secrets.env obok programu, w folderze Whisperer oraz w BAPO-transcript Lite."""
    seen = set()
    for directory in (HERE, HERE.parent, HERE.parent.parent / "Whisperer-lite", Path.cwd()):
        for name in ("secrets.env", ".env"):
            path = directory / name
            try:
                resolved = path.resolve()
            except OSError:
                continue
            if resolved in seen or not path.is_file():
                continue
            seen.add(resolved)
            yield path


def environment_key(provider_id: str) -> str:
    """Klucz ze zmiennej środowiskowej lub pliku secrets.env (np. GROQ_API_KEY=…)."""
    names = PROVIDERS[provider_id].env_vars
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    wanted = {name.upper() for name in names}
    for path in _secrets_files():
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            for separator in ("=", ":"):
                if separator in line:
                    name, value = line.split(separator, 1)
                    if name.strip().replace("-", "_").upper() in wanted:
                        value = value.strip().strip("'\"")
                        if value:
                            return value
                    break
    return ""


def lite_settings_path() -> Path:
    override = os.environ.get("BAPO_LITE_CONFIG_DIR")
    if override:
        return Path(override) / "ustawienia.json"
    if IS_WINDOWS:
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return base / "BAPO-transcript-lite" / "ustawienia.json"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "BAPO-transcript-lite" / "ustawienia.json"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "bapo-transcript-lite" / "ustawienia.json"


def lite_key(provider_id: str) -> str:
    """Klucz zapisany wcześniej w BAPO-transcript Lite (to samo konto Windows)."""
    try:
        stored = json.loads(lite_settings_path().read_text(encoding="utf-8"))
        value = (stored.get("keys") or {}).get(provider_id)
    except (OSError, ValueError, AttributeError):
        return ""
    return unprotect_secret(value, LITE_ENTROPY) if value else ""


def find_key(provider_id: str, stored: str = "") -> tuple[str, str]:
    """Zwraca (klucz, skąd): zapisany tutaj → zmienna/secrets.env → BAPO-transcript Lite."""
    key = unprotect_secret(stored) if stored else ""
    if key:
        return key, "saved"
    key = environment_key(provider_id)
    if key:
        return key, "environment"
    key = lite_key(provider_id)
    if key:
        return key, "lite"
    return "", ""


KEY_SOURCES = {
    "saved": "zapisany w programie",
    "environment": "ze zmiennej środowiskowej / secrets.env",
    "lite": "z BAPO-transcript Lite",
}


def provider_endpoint(provider_id: str, stored_endpoints: dict | None = None) -> str:
    """Adres API dostawcy: wpisany przez użytkownika albo domyślny z dostawcy.json."""
    custom = normalize_base_url((stored_endpoints or {}).get(provider_id, ""))
    return custom or PROVIDERS[provider_id].base_url


def _default_provider(kind: str) -> str:
    """Pierwszy dostawca danego rodzaju API (jego parametry dotyczą też własnych modeli)."""
    return next(p.id for p in PROVIDERS.values() if p.kind == kind)


def _merged(provider_pairs: tuple, model_pairs: tuple = ()) -> tuple:
    merged = dict(provider_pairs)
    merged.update(dict(model_pairs))
    return tuple(merged.items())


def _merged_params(provider: Provider, model: CloudModel | None = None) -> tuple:
    return _merged(provider.params, model.params if model else ())


def _merged_stream(provider: Provider, model: CloudModel | None = None) -> tuple:
    return _merged(provider.streaming_params, model.streaming_params if model else ())


def _wants_stream(provider: Provider, model: CloudModel | None = None) -> bool:
    if model is not None and model.stream is not None:
        return model.stream and provider.kind == "deepgram"
    return provider.stream


def resolve(key: str, stored_keys: dict, custom_models: list,
            stored_endpoints: dict | None = None) -> ResolvedModel | None:
    """Zamienia pozycję z listy modeli na komplet danych do wywołania API.

    ``stored_keys`` — {id_dostawcy: zaszyfrowany klucz}, ``custom_models`` — lista CustomModel,
    ``stored_endpoints`` — {id_dostawcy: własny adres API} (puste = adres z dostawcy.json).
    """
    if key in CLOUD_MODELS:
        model = CLOUD_MODELS[key]
        provider = PROVIDERS[model.provider]
        api_key, _source = find_key(provider.id, stored_keys.get(provider.id, ""))
        return ResolvedModel(
            key, model.label, provider.kind, provider_endpoint(provider.id, stored_endpoints),
            model.model, api_key, provider.name, params=_merged_params(provider, model),
            stream=_wants_stream(provider, model), stream_params=_merged_stream(provider, model),
        )
    if key and key.startswith("custom:"):
        wanted = key.split(":", 1)[1]
        for custom in custom_models:
            if custom.id != wanted:
                continue
            provider_id = provider_for_url(custom.kind, custom.base_url)
            api_key = unprotect_secret(custom.key) if custom.key else ""
            if not api_key and provider_id:
                api_key, _source = find_key(provider_id, stored_keys.get(provider_id, ""))
            service = PROVIDERS[provider_id].name if provider_id else (
                urllib.parse.urlsplit(custom.base_url).hostname or custom.name
            )
            return ResolvedModel(
                key, f"{custom.name} (własny)", custom.kind, custom.base_url, custom.model, api_key, service,
                # Własny serwer zgodny z OpenAI może działać bez klucza (np. lokalny).
                key_required=bool(provider_id) or custom.kind != "openai",
                params=_merged_params(PROVIDERS[provider_id or _default_provider(custom.kind)]),
                stream=_wants_stream(PROVIDERS[provider_id or _default_provider(custom.kind)]),
                stream_params=_merged_stream(PROVIDERS[provider_id or _default_provider(custom.kind)]),
            )
    return None


# ---------------------------------------------------------------------------
# Dźwięk
# ---------------------------------------------------------------------------


def encode_wav(audio: np.ndarray, sample_rate: int = 16000) -> bytes:
    """float32 −1…1 → WAV PCM 16-bit mono (ok. 32 KB na sekundę)."""
    data = np.clip(np.asarray(audio, dtype=np.float32).reshape(-1), -1.0, 1.0)
    pcm = (data * 32767.0).astype("<i2").tobytes()
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


class Multipart:
    def __init__(self):
        self.boundary = "----BAPODyktowanie" + uuid.uuid4().hex
        self._parts: list[bytes] = []

    def field(self, name: str, value):
        self._parts.append(
            (f"--{self.boundary}\r\n"
             f'Content-Disposition: form-data; name="{name}"\r\n\r\n').encode("utf-8")
            + str(value).encode("utf-8") + b"\r\n"
        )

    def file(self, name: str, filename: str, data: bytes, content_type: str = "audio/wav"):
        self._parts.append(
            (f"--{self.boundary}\r\n"
             f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
             f"Content-Type: {content_type}\r\n\r\n").encode("utf-8")
            + data + b"\r\n"
        )

    @property
    def content_type(self) -> str:
        return f"multipart/form-data; boundary={self.boundary}"

    def body(self) -> bytes:
        return b"".join(self._parts) + f"--{self.boundary}--\r\n".encode("ascii")


def _proxy_for(parts):
    host = (parts.hostname or "").lower()
    if host in {"localhost", "127.0.0.1", "::1"}:
        return None
    try:
        if urllib.request.proxy_bypass(host):
            return None
    except Exception:
        pass
    proxy = urllib.request.getproxies().get(parts.scheme)
    return urllib.parse.urlsplit(proxy if "://" in proxy else "http://" + proxy) if proxy else None


def http_request(method: str, url: str, *, headers: dict | None = None, body: bytes | None = None,
                 timeout: float = REQUEST_TIMEOUT):
    """Zwraca (status, treść). Błędy sieci zamienia na ApiError."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ApiError(f"Nieprawidłowy adres API: {url}")
    secure = parts.scheme == "https"
    port = parts.port or (443 if secure else 80)
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    proxy = _proxy_for(parts)
    context = ssl.create_default_context() if secure else None
    if proxy and secure:
        connection = http.client.HTTPSConnection(proxy.hostname, proxy.port or 8080, timeout=timeout, context=context)
        tunnel_headers = {}
        if proxy.username:
            credentials = f"{urllib.parse.unquote(proxy.username)}:{urllib.parse.unquote(proxy.password or '')}"
            tunnel_headers["Proxy-Authorization"] = "Basic " + base64.b64encode(credentials.encode()).decode()
        connection.set_tunnel(parts.hostname, port, headers=tunnel_headers)
    elif proxy:
        connection = http.client.HTTPConnection(proxy.hostname, proxy.port or 8080, timeout=timeout)
        path = url
    elif secure:
        connection = http.client.HTTPSConnection(parts.hostname, port, timeout=timeout, context=context)
    else:
        connection = http.client.HTTPConnection(parts.hostname, port, timeout=timeout)

    all_headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    all_headers.update(headers or {})
    if body is not None:
        all_headers["Content-Length"] = str(len(body))
    try:
        connection.request(method, path, body=body, headers=all_headers)
        response = connection.getresponse()
        return response.status, response.read()
    except (OSError, http.client.HTTPException) as exc:
        raise ApiError(f"Błąd połączenia z usługą: {exc}", retryable=True) from exc
    finally:
        connection.close()


def _error_detail(data: bytes) -> str:
    try:
        payload = json.loads(data.decode("utf-8", errors="replace"))
    except (ValueError, UnicodeDecodeError):
        return data.decode("utf-8", errors="replace")[:300].strip()
    if isinstance(payload, dict):
        for key in ("error", "detail", "err_msg", "message", "reason"):
            value = payload.get(key)
            if isinstance(value, dict):
                return str(value.get("message") or value.get("msg") or json.dumps(value, ensure_ascii=False))[:300]
            if isinstance(value, list) and value:
                first = value[0]
                return str(first.get("msg") if isinstance(first, dict) else first)[:300]
            if value:
                return str(value)[:300]
    return json.dumps(payload, ensure_ascii=False)[:300]


def _check(service: str, status: int, data: bytes):
    if 200 <= status < 300:
        return
    detail = _error_detail(data)
    if status in (401, 403):
        message = f"{service} odrzucił klucz API ({status}). Sprawdź klucz w „Klucze API…”."
    elif status == 402:
        message = f"{service}: brak środków na koncie lub wyczerpany limit."
    elif status == 404:
        message = f"{service}: nie znaleziono modelu lub adresu API (404)."
    elif status == 429:
        message = f"{service}: przekroczono limit zapytań albo skończyły się środki."
    elif status >= 500:
        message = f"{service}: chwilowy błąd serwera ({status})."
    else:
        message = f"{service} zwrócił błąd {status}."
    if detail:
        message += f" Szczegóły: {detail}"
    raise ApiError(message, status=status, retryable=status in (408, 409, 429) or status >= 500)


def _json(service: str, status: int, data: bytes):
    _check(service, status, data)
    try:
        return json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        # Część serwerów zgodnych z OpenAI zwraca czysty tekst.
        text = data.decode("utf-8", errors="replace").strip()
        if text and not text.startswith("<"):
            return {"text": text}
        raise ApiError(f"{service} zwrócił niezrozumiałą odpowiedź.") from None


# ---------------------------------------------------------------------------
# Wywołania usług
# ---------------------------------------------------------------------------


@dataclass
class Request:
    audio: bytes                 # WAV
    language: str | None = "pl"  # None = wykryj automatycznie
    hotwords: tuple = ()
    prompt: str = ""             # wcześniejszy tekst — kontekst dla modelu


def _bearer(api_key: str) -> dict:
    return {"Authorization": f"Bearer {api_key}"} if api_key else {}


def _openai(model: ResolvedModel, request: Request, minimal: bool = False) -> str:
    form = Multipart()
    form.field("model", model.model)
    if not minimal:
        if model.model == "gpt-transcribe":
            if request.language:
                form.field("languages[]", request.language)
            for word in request.hotwords[:100]:
                form.field("keywords[]", word)
        else:
            if request.language:
                form.field("language", request.language)
            parts = []
            if request.hotwords:
                parts.append("Terminy: " + ", ".join(request.hotwords) + ".")
            if request.prompt:
                parts.append(request.prompt[-400:])
            prompt = " ".join(parts)[-800:]
            if prompt:
                form.field("prompt", prompt)
        form.field("temperature", "0")
    form.field("response_format", "json")
    form.file("file", "wypowiedz.wav", request.audio)
    status, data = http_request(
        "POST", f"{model.base_url}/audio/transcriptions",
        headers={**_bearer(model.api_key), "Content-Type": form.content_type},
        body=form.body(),
    )
    if status == 400 and not minimal:
        # Model (lub serwer zgodny z OpenAI) odrzucił któryś parametr — próbujemy
        # jeszcze raz z samym modelem i plikiem.
        return _openai(model, request, minimal=True)
    payload = _json(model.service, status, data)
    return str(payload.get("text", "") if isinstance(payload, dict) else "").strip()


def _elevenlabs(model: ResolvedModel, request: Request) -> str:
    form = Multipart()
    form.field("model_id", model.model)
    if request.language:
        form.field("language_code", request.language)
    form.field("diarize", "false")
    form.field("tag_audio_events", "false")
    form.field("timestamps_granularity", "none")
    for word in request.hotwords[:100]:
        form.field("keyterms", word[:50])
    form.file("file", "wypowiedz.wav", request.audio)
    status, data = http_request(
        "POST", f"{model.base_url}/speech-to-text",
        headers={"xi-api-key": model.api_key, "Content-Type": form.content_type},
        body=form.body(),
    )
    payload = _json(model.service, status, data)
    return str(payload.get("text", "")).strip()


def _deepgram(model: ResolvedModel, request: Request) -> str:
    params = [("model", model.model)]
    params.extend((name, value) for name, value in model.params if name != "model")
    params.append(("language", request.language) if request.language else ("detect_language", "true"))
    hotword_param = "keyterm" if model.model.startswith("nova-3") else "keywords"
    for word in request.hotwords[:100]:
        params.append((hotword_param, word))
    status, data = http_request(
        "POST", f"{model.base_url}/listen?{urllib.parse.urlencode(params)}",
        headers={"Authorization": f"Token {model.api_key}", "Content-Type": "audio/wav"},
        body=request.audio,
    )
    payload = _json(model.service, status, data)
    try:
        return str(payload["results"]["channels"][0]["alternatives"][0]["transcript"]).strip()
    except (KeyError, IndexError, TypeError):
        return ""


def _assemblyai(model: ResolvedModel, request: Request, poll_interval: float = 0.4) -> str:
    headers = {"authorization": model.api_key}
    status, data = http_request(
        "POST", f"{model.base_url}/upload",
        headers={**headers, "Content-Type": "application/octet-stream"}, body=request.audio,
    )
    upload_url = _json(model.service, status, data).get("upload_url")
    if not upload_url:
        raise ApiError("AssemblyAI nie przyjął nagrania.")
    job = {"audio_url": upload_url, "speech_models": [model.model], "punctuate": True, "format_text": True}
    if request.language:
        job["language_code"] = request.language
    else:
        job["language_detection"] = True
    status, data = http_request(
        "POST", f"{model.base_url}/transcript",
        headers={**headers, "Content-Type": "application/json"}, body=json.dumps(job).encode("utf-8"),
    )
    transcript_id = _json(model.service, status, data).get("id")
    if not transcript_id:
        raise ApiError("AssemblyAI nie utworzył zadania transkrypcji.")
    deadline = time.monotonic() + 90
    while True:
        status, data = http_request("GET", f"{model.base_url}/transcript/{transcript_id}", headers=headers)
        result = _json(model.service, status, data)
        state = result.get("status")
        if state == "completed":
            return str(result.get("text") or "").strip()
        if state == "error":
            raise ApiError("AssemblyAI: " + str(result.get("error") or "błąd transkrypcji."))
        if time.monotonic() > deadline:
            raise ApiError("AssemblyAI nie zwrócił tekstu w rozsądnym czasie.", retryable=False)
        time.sleep(poll_interval)


_CALLS = {"openai": _openai, "elevenlabs": _elevenlabs, "deepgram": _deepgram, "assemblyai": _assemblyai}


def transcribe(model: ResolvedModel, request: Request, retries: int = 1) -> str:
    """Wysyła jedną wypowiedź do usługi i zwraca rozpoznany tekst."""
    if model.needs_key:
        raise ApiError(f"Brak klucza API dla: {model.service}. Wpisz go w „Klucze API…”.")
    call = _CALLS.get(model.kind)
    if call is None:
        raise ApiError(f"Nieznany rodzaj API: {model.kind}")
    for attempt in range(retries + 1):
        try:
            return call(model, request)
        except ApiError as exc:
            if not exc.retryable or attempt == retries:
                raise
            time.sleep(0.8)
    return ""


def check_key(kind: str, base_url: str, api_key: str) -> tuple[bool, str]:
    """Lekkie zapytanie sprawdzające klucz (bez wysyłania dźwięku)."""
    api_key = (api_key or "").strip()
    if not api_key and kind != "openai":
        return False, "Wpisz klucz."
    base = normalize_base_url(base_url)
    requests = {
        "openai": (f"{base}/models", _bearer(api_key)),
        "elevenlabs": (f"{base}/models", {"xi-api-key": api_key}),
        "deepgram": (f"{base}/projects", {"Authorization": f"Token {api_key}"}),
        "assemblyai": (f"{base}/transcript?limit=1", {"authorization": api_key}),
    }
    url, headers = requests.get(kind, requests["openai"])
    try:
        status, data = http_request("GET", url, headers=headers, timeout=15)
    except ApiError as exc:
        return False, str(exc)
    if 200 <= status < 300:
        return True, "Klucz działa ✓"
    if status in (401, 403):
        hint = " — nadaj kluczowi dostęp do Speech to Text" if kind == "elevenlabs" else ""
        return False, f"Klucz odrzucony ({status}){hint}."
    return False, f"Nie udało się sprawdzić ({status}): {_error_detail(data)}"


# ---------------------------------------------------------------------------
# Porządkowanie tekstu
# ---------------------------------------------------------------------------

# Te same „halucynacje” co lokalnie — Whisper w chmurze też dopowiada je na ciszy.
_HALLUCINATION_SENTENCE = re.compile(
    r"[^.!?…]*(?:amara\.org"
    r"|napisy\s+(?:stworzone|wykonane|przygotowane|zrobione)\s+przez"
    r"|napisy\s+wykonała?"
    r"|subskrybuj(?:cie)?\s+(?:kanał|nas)"
    r"|dzięki\s+za\s+(?:obejrzenie|oglądanie)"
    r"|dziękuję\s+za\s+(?:obejrzenie|oglądanie)"
    r"|zdjęcia\s+i\s+montaż"
    r"|thanks?\s+(?:you\s+)?for\s+watching"
    r"|transcribed\s+by)[^.!?…]*[.!?…]*",
    re.IGNORECASE,
)


def clean_text(text: str) -> str:
    text = _HALLUCINATION_SENTENCE.sub(" ", text or "")
    return " ".join(text.split())
