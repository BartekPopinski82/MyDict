"""Drugi silnik rozpoznawania mowy: whisper.cpp — karty AMD i Intel (Vulkan) oraz Mac (Metal).

Pierwszy silnik (faster-whisper / CTranslate2) liczy na karcie graficznej tylko przez
NVIDIA CUDA. whisper.cpp obsługuje pozostałe: Vulkan na Windows i Metal na macOS.

Silnik działa jako osobny, lokalny proces ``whisper-server`` (dołączony do programu),
z którym rozmawiamy przez HTTP na 127.0.0.1. Osobny proces ma dwie zalety: błąd
sterownika karty graficznej nie zamyka całego programu, a program nie zależy od
układu struktur biblioteki C. Dźwięk nie opuszcza komputera.

Dla reszty programu to po prostu kolejna funkcja transkrypcji:
``WhisperCppTranscriber(audio, prompt, final) -> list[Word]`` — z czasami słów,
więc działa z nią to samo strumieniowanie na żywo co z pierwszym silnikiem.
"""

from __future__ import annotations

import http.client
import json
import logging
import os
import re
import socket
import subprocess
import sys
import threading
import time
import uuid
import zlib
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from dyktowanie_api import encode_wav
from dyktowanie_app import IS_MAC, IS_WINDOWS, data_dir, log_dir, resource_dir
from dyktowanie_core import SAMPLE_RATE, Word, is_hallucinated_segment
from dyktowanie_pobieranie import DownloadError, Progress, download, fetch_json


log = logging.getLogger("dyktowanie.whispercpp")

# Modele w formacie GGML (kwantyzowane — mniejsze i szybsze na słabszych kartach).
REPO = "ggerganov/whisper.cpp"
MODELS = {
    "turbo": ("ggml-large-v3-turbo-q5_0.bin", "0,6 GB"),
    "small": ("ggml-small-q5_1.bin", "0,2 GB"),
    "medium": ("ggml-medium-q5_0.bin", "0,5 GB"),
    "large-v3": ("ggml-large-v3-q5_0.bin", "1,1 GB"),
}
# Nazwa zestawu głowic uwagi dla wyrównania DTW (dokładne czasy słów) — osobna dla każdego modelu.
DTW_PRESETS = {"turbo": "large.v3.turbo", "small": "small", "medium": "medium", "large-v3": "large.v3"}
BACKEND = "metal" if IS_MAC else "vulkan"
BACKEND_LABEL = {"vulkan": "Vulkan", "metal": "Metal", "cpu": "CPU"}
START_TIMEOUT = 120.0
REQUEST_TIMEOUT = 120.0


class WhisperCppError(Exception):
    """Silnik whisper.cpp nie wystartował albo nie odpowiedział."""


# ---------------------------------------------------------------------------
# Pliki silnika i modele
# ---------------------------------------------------------------------------


def binary_dir() -> Path:
    override = os.environ.get("MYDICT_WHISPERCPP")
    return Path(override) if override else resource_dir() / "whispercpp"


def server_executable() -> Path:
    return binary_dir() / ("whisper-server.exe" if IS_WINDOWS else "whisper-server")


def available() -> bool:
    """Czy silnik jest dołączony do tej kopii programu."""
    return server_executable().is_file()


def models_dir() -> Path:
    return data_dir() / "modele"


def model_file(key: str) -> str:
    return MODELS.get(key, MODELS["small"])[0]


def model_path(key: str) -> Path:
    return models_dir() / model_file(key)


def model_cached(key: str) -> bool:
    path = model_path(key)
    return path.is_file() and path.stat().st_size > 1_000_000


def _model_entry(name: str, fetch=fetch_json) -> dict:
    for item in fetch(f"https://huggingface.co/api/models/{REPO}/tree/main"):
        if item.get("path") == name:
            return item
    raise DownloadError(f"Brak pliku modelu {name} na serwerze")


def download_model(key: str, progress: Progress | None = None, cancel: threading.Event | None = None,
                   *, fetch=fetch_json, fetch_file=download) -> Path:
    """Pobiera model GGML (z kontrolą sumy SHA-256 i wznawianiem)."""
    name = model_file(key)
    entry = _model_entry(name, fetch)
    checksum = str((entry.get("lfs") or {}).get("oid") or "")
    return fetch_file(
        f"https://huggingface.co/{REPO}/resolve/main/{name}", model_path(key),
        sha256=checksum, progress=progress, cancel=cancel,
    )


# ---------------------------------------------------------------------------
# Proces serwera
# ---------------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


_JOB = None


def _kill_with_parent(process: subprocess.Popen) -> None:
    """Windows: proces silnika kończy się razem z programem, także po jego awarii."""
    global _JOB
    if not IS_WINDOWS:
        return
    try:
        import ctypes
        from ctypes import wintypes

        class BASIC(ctypes.Structure):
            _fields_ = (
                ("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            )

        class IO(ctypes.Structure):
            _fields_ = tuple((name, ctypes.c_uint64) for name in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount"))

        class EXTENDED(ctypes.Structure):
            _fields_ = (
                ("BasicLimitInformation", BASIC), ("IoInfo", IO),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t),
            )

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
        kernel32.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
        kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
        if _JOB is None:
            job = kernel32.CreateJobObjectW(None, None)
            info = EXTENDED()
            info.BasicLimitInformation.LimitFlags = 0x2000   # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not job or not kernel32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
                return
            _JOB = job   # uchwyt żyje do końca programu; jego zamknięcie kończy proces silnika
        kernel32.AssignProcessToJobObject(_JOB, wintypes.HANDLE(int(process._handle)))
    except Exception:
        log.debug("Nie powiązano procesu silnika z programem", exc_info=True)


@dataclass
class ServerInfo:
    gpu: bool = False
    backend: str = "cpu"
    device: str = ""
    devices: list = field(default_factory=list)   # [(indeks, nazwa, czy_zintegrowana)]


def parse_server_log(text: str) -> ServerInfo:
    """Czyta z dziennika silnika, czy i jakiej karty graficznej używa."""
    info = ServerInfo()
    # Wiersz ma postać: „ggml_vulkan: 0 = Nazwa karty (sterownik) | uma: 1 | fp16: 1 | …”;
    # „uma: 1” oznacza kartę zintegrowaną (pamięć wspólna z procesorem).
    for match in re.finditer(r"ggml_vulkan:\s*(\d+)\s*=\s*([^|\n]+?)\s*\|\s*uma:\s*(\d)", text):
        name = re.sub(r"\s*\([^()]*\)\s*$", "", match.group(2).strip())   # bez dopisku o sterowniku
        info.devices.append((int(match.group(1)), name, match.group(3) == "1"))
    used = re.search(r"whisper_backend_init_gpu:\s*using\s+(\S+)\s+backend", text)
    if used and "no GPU found" not in text:
        name = used.group(1)
        info.gpu = not name.upper().startswith("CPU")
        if info.gpu:
            info.backend = "metal" if "metal" in name.lower() or name.upper().startswith("MTL") else "vulkan"
            index = re.search(r"(\d+)$", name)
            if info.devices and index:
                wanted = int(index.group(1))
                info.device = next((n for i, n, _u in info.devices if i == wanted), "")
            if not info.device:
                described = re.search(r"whisper_backend_init_gpu:\s*device\s+\d+:\s*(.+?)\s*\(type", text)
                info.device = described.group(1).strip() if described else ""
    return info


class WhisperServer:
    """Uruchamia i nadzoruje lokalny proces ``whisper-server``."""

    def __init__(self):
        self.process: subprocess.Popen | None = None
        self.port = 0
        self.info = ServerInfo()
        self.model: Path | None = None
        self._log_path = log_dir() / "whispercpp.log"
        self._lock = threading.Lock()
        self._args: tuple = ()

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def start(self, model: Path, *, use_gpu: bool = True, device: int | None = None,
              threads: int | None = None, dtw: str = ""):
        self.stop()
        executable = server_executable()
        if not executable.is_file():
            raise WhisperCppError("Silnik whisper.cpp nie jest dołączony do tej wersji programu.")
        self._args = (model, use_gpu, device, threads, dtw)
        self.model = model
        self.port = _free_port()
        threads = threads or max(2, min(8, (os.cpu_count() or 4) // 2))
        command = [str(executable), "-m", str(model), "--host", "127.0.0.1", "--port", str(self.port),
                   "-t", str(threads), "-l", "auto"]
        if dtw:
            # Czasy słów z wyrównania DTW są stabilne między przebiegami (±0,02 s) — bez nich
            # strumieniowanie gubi słowa. DTW wymaga wyłączenia „flash attention”.
            command += ["--dtw", dtw, "--no-flash-attn"]
        if not use_gpu:
            command.append("--no-gpu")
        elif device is not None:
            command += ["--device", str(device)]
        flags = 0x08000000 if IS_WINDOWS else 0   # CREATE_NO_WINDOW
        log.info("Start whisper.cpp: %s", " ".join(command[1:]))
        with open(self._log_path, "wb") as output:
            self.process = subprocess.Popen(
                command, cwd=str(executable.parent), stdin=subprocess.DEVNULL,
                stdout=output, stderr=subprocess.STDOUT, creationflags=flags,
            )
        _kill_with_parent(self.process)
        deadline = time.monotonic() + START_TIMEOUT
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise WhisperCppError(f"Silnik zakończył się przy starcie (kod {self.process.returncode}). "
                                      f"{self._log_tail()}")
            try:
                connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=1.0)
                connection.request("GET", "/")
                connection.getresponse().read()
                connection.close()
                break
            except OSError:
                time.sleep(0.15)
        else:
            self.stop()
            raise WhisperCppError("Silnik nie odpowiedział w wyznaczonym czasie.")
        self.info = parse_server_log(self._log_text())
        log.info("whisper.cpp gotowy: port %d, karta: %s (%s), urządzenia: %s",
                 self.port, self.info.device or "—", self.info.backend, self.info.devices)

    def _log_text(self) -> str:
        try:
            return self._log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    def _log_tail(self) -> str:
        lines = [line for line in self._log_text().splitlines() if line.strip()]
        return " | ".join(lines[-3:])[:400]

    def stop(self):
        process, self.process = self.process, None
        if process is None:
            return
        try:
            process.terminate()
            process.wait(timeout=3)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass

    def restart(self):
        if self._args:
            model, use_gpu, device, threads, dtw = self._args
            self.start(model, use_gpu=use_gpu, device=device, threads=threads, dtw=dtw)

    def infer(self, wav: bytes, fields: dict) -> dict:
        """Jedno zapytanie o transkrypcję. Po awarii procesu próbuje go raz wznowić."""
        with self._lock:
            for attempt in (1, 2):
                if not self.running:
                    if attempt == 2 or not self._args:
                        raise WhisperCppError(f"Silnik whisper.cpp przestał działać. {self._log_tail()}")
                    log.warning("whisper.cpp nie działa — uruchamiam ponownie")
                    self.restart()
                try:
                    return self._post(wav, fields)
                except (OSError, http.client.HTTPException, ValueError) as exc:
                    if attempt == 2:
                        raise WhisperCppError(f"Silnik whisper.cpp nie odpowiedział: {exc}") from exc
                    log.warning("whisper.cpp: błąd zapytania (%s) — ponawiam", exc)
                    if self.running:
                        self.stop()
            raise WhisperCppError("Silnik whisper.cpp nie odpowiedział.")

    def _post(self, wav: bytes, fields: dict) -> dict:
        boundary = uuid.uuid4().hex
        parts = []
        for name, value in fields.items():
            parts.append(
                f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode("utf-8")
            )
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="audio.wav"\r\n'
            "Content-Type: audio/wav\r\n\r\n".encode("utf-8")
        )
        body = b"".join(parts) + wav + f"\r\n--{boundary}--\r\n".encode("utf-8")
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=REQUEST_TIMEOUT)
        try:
            connection.request("POST", "/inference", body,
                               {"Content-Type": f"multipart/form-data; boundary={boundary}"})
            response = connection.getresponse()
            data = response.read()
        finally:
            connection.close()
        if response.status != 200:
            raise ValueError(f"HTTP {response.status}: {data[:200]!r}")
        result = json.loads(data.decode("utf-8", errors="replace"))
        if isinstance(result, dict) and result.get("error"):
            raise ValueError(str(result["error"]))
        return result


# ---------------------------------------------------------------------------
# Funkcja transkrypcji
# ---------------------------------------------------------------------------

_SPECIAL = re.compile(r"\[_[A-Za-z]+_?\d*\]|<\|[^|]*\|>")
MIN_AUDIO_S = 1.05      # whisper.cpp odrzuca dźwięk krótszy niż 1 s


def audio_context(seconds: float, final: bool) -> int:
    """Rozmiar kontekstu dźwięku (1500 = pełne okno 30 s). Krótki bufor nie musi
    przechodzić przez koder jako 30 s ciszy — to największa oszczędność czasu przebiegu."""
    if seconds >= 24.0:
        return 0
    frames = int(seconds * 50) + (192 if final else 96)
    return max(256, min(1500, frames))


def words_from_segment(segment: dict) -> list[Word]:
    """Składa słowa z fragmentów (tokenów): nowy wyraz zaczyna się od spacji."""
    words: list[Word] = []
    previous_end = float(segment.get("start") or 0.0)
    for piece in segment.get("words") or ():
        text = str(piece.get("word") or "")
        if not text or _SPECIAL.fullmatch(text.strip() or "x"):
            continue
        aligned = piece.get("t_dtw")
        if isinstance(aligned, (int, float)) and aligned >= 0:
            # DTW podaje chwilę, w której fragment wybrzmiewa (setne części sekundy):
            # słowo trwa od końca poprzedniego do swojego ostatniego fragmentu.
            end = float(aligned) / 100.0
            start = min(previous_end, end)
        else:
            start, end = float(piece.get("start") or 0.0), float(piece.get("end") or 0.0)
        if words and not text.startswith(" "):
            last = words[-1]
            words[-1] = Word(last.start, max(last.end, end), last.text + text)
        else:
            words.append(Word(start, max(start, end), text))
        previous_end = words[-1].end
    if not words and str(segment.get("text") or "").strip():
        # Serwer bez czasów fragmentów: rozkładamy słowa równo w segmencie.
        tokens = str(segment["text"]).split()
        start, end = float(segment.get("start") or 0.0), float(segment.get("end") or 0.0)
        step = max(0.0, end - start) / max(1, len(tokens))
        words = [Word(start + i * step, start + (i + 1) * step, " " + token) for i, token in enumerate(tokens)]
    return [word for word in words if word.text.strip()]


def compression_ratio(text: str) -> float:
    raw = text.encode("utf-8")
    return len(raw) / len(zlib.compress(raw)) if raw else 0.0


class WhisperCppTranscriber:
    """Funkcja transkrypcji przekazywana do LiveStreamer (jak WhisperTranscriber)."""

    def __init__(self, server: WhisperServer, settings, info=None):
        self.server = server
        self.settings = settings
        self.info = info
        self.last_stats: dict | None = None

    def __call__(self, audio: np.ndarray, prompt: str, final: bool) -> list[Word]:
        settings = self.settings
        seconds = len(audio) / SAMPLE_RATE
        if seconds < 0.1:
            return []
        if seconds < MIN_AUDIO_S:
            audio = np.concatenate([audio, np.zeros(int(MIN_AUDIO_S * SAMPLE_RATE) - len(audio), dtype=np.float32)])
        hotwords = (settings.hotwords or "").strip()
        context = " ".join(part for part in (hotwords, (prompt or "").strip()) if part)
        beam = settings.final_beam if final else settings.live_beam
        fields = {
            "response_format": "verbose_json",
            "language": settings.language or "auto",
            "temperature": "0.0",
            "temperature_inc": "0.0",          # bez ponawiania z wyższą temperaturą — stały czas przebiegu
            "beam_size": str(max(1, int(beam))),
            "best_of": "1",
            "no_language_probabilities": "true",
            "suppress_nst": "true",
            "audio_ctx": str(audio_context(seconds, final)),
        }
        if context:
            fields["prompt"] = context
        t0 = time.perf_counter()
        result = self.server.infer(encode_wav(audio, SAMPLE_RATE), fields)
        elapsed = time.perf_counter() - t0
        words: list[Word] = []
        rejected = 0
        segments = result.get("segments") or []
        for segment in segments:
            text = str(segment.get("text") or "")
            if is_hallucinated_segment(
                text, float(segment.get("no_speech_prob") or 0.0),
                float(segment.get("avg_logprob") or 0.0), compression_ratio(text),
            ):
                rejected += 1
                log.debug("Pominięto segment: %r", text)
                continue
            words.extend(word for word in words_from_segment(segment) if word.start <= seconds + 0.5)
        info = self.info
        self.last_stats = {
            "model": info.name if info else "",
            "compute_type": info.compute_type if info else "",
            "final": final,
            "beam_size": int(beam),
            "best_of": 1,
            "temperature": 0.0,
            "patience": None,
            "condition_on_previous_text": False,
            "word_timestamps": True,
            "max_new_tokens": None,
            "audio_s": seconds,
            "prompt_chars": len(context),
            "prompt_tokens": None,
            "hotwords_tokens": None,
            "segments": len(segments),
            "rejected": rejected,
            "windows": 1 if segments else 0,
            "tokens": None,
            "tokens_per_s": None,
            "words": len(words),
            "prepare_s": 0.0,
            "first_segment_s": elapsed,
            "rest_s": 0.0,
            "thread_cpu_s": 0.0,
            "segment_list": [],
        }
        return words


def pick_device(info: ServerInfo) -> int | None:
    """Gdy są dwie karty Vulkan, wolimy osobną (dedykowaną) od zintegrowanej."""
    discrete = [index for index, _name, integrated in info.devices if not integrated]
    if len(info.devices) > 1 and discrete and discrete[0] != 0:
        return discrete[0]
    return None


def start_server(model: Path, status=lambda text: None, key: str = "") -> WhisperServer:
    """Uruchamia silnik na karcie graficznej; gdy kart jest kilka — na dedykowanej."""
    server = WhisperServer()
    status("Uruchamianie silnika rozpoznawania…")
    dtw = DTW_PRESETS.get(key, "")
    server.start(model, dtw=dtw)
    better = pick_device(server.info) if server.info.backend == "vulkan" else None
    if better is not None:
        log.info("Przełączam whisper.cpp na kartę dedykowaną (urządzenie %d)", better)
        server.start(model, device=better, dtw=dtw)
    # Rozgrzewka: pierwszy przebieg kompiluje programy karty graficznej.
    server.infer(encode_wav(np.zeros(int(MIN_AUDIO_S * SAMPLE_RATE), dtype=np.float32), SAMPLE_RATE),
                 {"response_format": "verbose_json", "language": "pl", "temperature": "0.0",
                  "temperature_inc": "0.0", "no_language_probabilities": "true", "audio_ctx": "256"})
    return server


__all__ = [
    "BACKEND", "BACKEND_LABEL", "MODELS", "ServerInfo", "WhisperCppError", "WhisperCppTranscriber",
    "WhisperServer", "audio_context", "available", "binary_dir", "download_model", "model_cached",
    "model_path", "parse_server_log", "pick_device", "start_server", "words_from_segment",
]

if __name__ == "__main__":   # szybka diagnostyka: python dyktowanie_whispercpp.py
    sys.stdout.write(f"silnik: {server_executable()} ({'jest' if available() else 'brak'})\n")
