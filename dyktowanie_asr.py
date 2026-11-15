"""Model Whisper, mikrofon i wątek roboczy dyktowania."""

from __future__ import annotations

import gc
import importlib.util
import json
import logging
import os
import queue
import re
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

import dyktowanie_gpu_pakiet as gpu_pack
from dyktowanie_api import ApiError, Request, ResolvedModel, clean_text, encode_wav, transcribe
from dyktowanie_app import config_file
from dyktowanie_core import (
    SAMPLE_RATE,
    LiveStreamer,
    Resampler,
    StreamConfig,
    Word,
    is_hallucinated_segment,
    make_vad,
    max_new_tokens_for,
)
from dyktowanie_gpu import monitor as resources
from dyktowanie_latencja import tracker as latency


log = logging.getLogger("dyktowanie.asr")
IS_WINDOWS = sys.platform.startswith("win")

# Klucz → (nazwa modelu faster-whisper, etykieta w interfejsie, przybliżony rozmiar)
MODELS = {
    "turbo": ("large-v3-turbo", "Turbo — szybki i dokładny (zalecany)", "1,6 GB"),
    "small": ("small", "Lekki — dla słabszego komputera", "0,5 GB"),
    "medium": ("medium", "Średni", "1,5 GB"),
    "large-v3": ("large-v3", "Najdokładniejszy — wolniejszy", "3 GB"),
}
DEFAULT_MODEL = "turbo"

LANGUAGES = {
    "pl": "Polski",
    "en": "Angielski",
    "auto": "Wykryj automatycznie",
}


@dataclass
class LiveSettings:
    """Ustawienia czytane przez wątek roboczy przy każdym przebiegu."""

    language: str = "pl"
    hotwords: str = ""
    live_beam: int = 1
    final_beam: int = 5
    stream: StreamConfig = field(default_factory=StreamConfig)


# Ustawienia dekodera do zmiany bez edycji kodu: plik obok programu, np. {"final_beam": 5}.
# Brak pliku albo klucza = wartość domyślna z LiveSettings. Czytany przy starcie programu.
SETTINGS_FILE = config_file("ustawienia_asr.json")
SETTINGS_KEYS = {"live_beam": (1, 10), "final_beam": (1, 10)}   # klucz → dozwolony zakres


def load_live_settings(path: Path | None = None) -> LiveSettings:
    """LiveSettings z wartościami nadpisanymi z pliku JSON; błędne wpisy są pomijane z ostrzeżeniem."""
    settings = LiveSettings()
    path = path or SETTINGS_FILE
    if not path.is_file():
        return settings
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            raise ValueError("oczekiwano obiektu {...}")
    except (OSError, ValueError) as exc:
        log.warning("Nie odczytano %s (%s) — ustawienia domyślne", path.name, exc)
        return settings
    for key, value in data.items():
        if key.startswith("_"):   # komentarz w pliku
            continue
        low, high = SETTINGS_KEYS.get(key, (None, None))
        if low is None or isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            log.warning("%s: pominięto %s=%r (znane klucze: %s)", path.name, key, value, ", ".join(SETTINGS_KEYS))
            continue
        setattr(settings, key, value)
    log.info("Ustawienia z %s: live_beam=%d, final_beam=%d", path.name, settings.live_beam, settings.final_beam)
    return settings


# ---------------------------------------------------------------------------
# Biblioteki CUDA (Windows)
# ---------------------------------------------------------------------------

_GPU_PREPARED = None


def prepare_gpu_libraries() -> bool:
    """Udostępnia biblioteki cuBLAS/cuDNN dla CTranslate2.

    Szukamy ich kolejno: w pakiecie GPU pobranym przez program (katalog danych
    użytkownika), w PyTorchu i w paczkach ``nvidia-*`` — te dwa ostatnie tylko
    przy uruchomieniu ze źródeł. Pliki DLL ładujemy sami, bo bez tego CTranslate2
    potrafi zakończyć proces bez komunikatu, gdy nie znajdzie cuDNN.
    """
    global _GPU_PREPARED
    if _GPU_PREPARED is not None:
        return _GPU_PREPARED
    if not IS_WINDOWS:
        _GPU_PREPARED = True
        return True
    import ctypes

    directories = list(gpu_pack.library_dirs())
    try:
        spec = importlib.util.find_spec("torch")
    except (ImportError, ValueError):
        spec = None
    if spec and spec.origin:
        directories.append(Path(spec.origin).parent / "lib")
    try:
        nvidia = importlib.util.find_spec("nvidia")
        if nvidia and nvidia.submodule_search_locations:
            for base in nvidia.submodule_search_locations:
                directories.extend(Path(base).glob("*/bin"))
    except (ImportError, ValueError):
        pass

    found = {"cublas": False, "cudnn": False}
    for directory in directories:
        if not directory.is_dir():
            continue
        try:
            os.add_dll_directory(str(directory))
        except (OSError, AttributeError):
            pass
        os.environ["PATH"] = str(directory) + os.pathsep + os.environ.get("PATH", "")
        for pattern, key in (("cudart64_*.dll", None), ("cublasLt64_*.dll", None),
                             ("cublas64_*.dll", "cublas"), ("cudnn*64_9.dll", "cudnn")):
            for dll in sorted(directory.glob(pattern)):
                try:
                    ctypes.WinDLL(str(dll))
                    if key:
                        found[key] = True
                except OSError as exc:
                    log.debug("Nie załadowano %s: %s", dll.name, exc)
    _GPU_PREPARED = found["cublas"] and found["cudnn"]
    log.info("Biblioteki CUDA: %s (katalogi: %s)", found, [str(d) for d in directories])
    return _GPU_PREPARED


def reset_gpu_libraries():
    """Po doinstalowaniu pakietu GPU: następne ładowanie modelu sprawdzi biblioteki od nowa."""
    global _GPU_PREPARED
    _GPU_PREPARED = None


def gpu_acceleration_state() -> str:
    """Stan przyspieszenia kartą graficzną — do podpowiedzi w interfejsie.

    ``ready`` — karta NVIDIA i komplet bibliotek; ``missing`` — karta jest, ale
    brakuje bibliotek (można je pobrać); ``none`` — brak karty NVIDIA albo system,
    na którym model i tak liczy procesor.
    """
    if not gpu_pack.supported() or cuda_device_count() <= 0:
        return "none"
    return "ready" if prepare_gpu_libraries() else "missing"


def cuda_device_count() -> int:
    try:
        import ctranslate2

        return int(ctranslate2.get_cuda_device_count())
    except Exception as exc:  # brak CUDA lub sterownika
        log.info("CUDA niedostępna: %s", exc)
        return 0


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


@dataclass
class ModelInfo:
    key: str
    name: str
    device: str
    compute_type: str
    vad: str

    @property
    def label(self) -> str:
        if self.device == "api":
            return f"Chmura (API) · {self.name}"
        device = "GPU (CUDA)" if self.device == "cuda" else "CPU"
        return f"{device} · {self.name} · {self.compute_type}"


def load_whisper(key: str, device_preference: str = "auto", status=lambda text: None):
    """Ładuje model faster-whisper; najpierw próbuje GPU, w razie problemu CPU."""
    name = MODELS.get(key, MODELS[DEFAULT_MODEL])[0]
    want_gpu = device_preference != "cpu"
    gpu_ready = False
    if want_gpu:
        libraries = prepare_gpu_libraries()
        gpu_ready = libraries and cuda_device_count() > 0
        if not gpu_ready:
            log.warning("GPU niedostępne (biblioteki: %s) — używam CPU", libraries)

    from faster_whisper import WhisperModel, download_model

    try:
        path = download_model(name, local_files_only=True)
    except Exception:
        size = MODELS.get(key, MODELS[DEFAULT_MODEL])[2]
        status(f"Pobieranie modelu {name} (jednorazowo, ok. {size})…")
        path = download_model(name)

    status(f"Ładowanie modelu {name}…")
    threads = max(2, min(8, (os.cpu_count() or 4) // 2))
    attempts = [("cuda", "int8_float16"), ("cuda", "float16")] if gpu_ready else []
    attempts.append(("cpu", "int8"))
    last_error = None
    for device, compute_type in attempts:
        try:
            model = WhisperModel(path, device=device, compute_type=compute_type, cpu_threads=threads)
            # Rozgrzewka: ładuje kernele CUDA teraz, a nie przy pierwszym słowie.
            segments, _ = model.transcribe(
                np.zeros(SAMPLE_RATE, dtype=np.float32), language="pl", beam_size=1,
                vad_filter=False, without_timestamps=True, temperature=0.0,
            )
            list(segments)
            log.info("Model %s gotowy: %s/%s", name, device, compute_type)
            return model, ModelInfo(key, name, device, compute_type, "")
        except Exception as exc:
            last_error = exc
            log.warning("Nie udało się uruchomić %s na %s/%s: %s", name, device, compute_type, exc)
    raise RuntimeError(f"Nie udało się uruchomić modelu {name}: {last_error}")


class WhisperTranscriber:
    """Funkcja transkrypcji przekazywana do LiveStreamer.

    Po każdym wywołaniu ``last_stats`` opisuje przebieg (parametry, tokeny, segmenty,
    fazy czasu) — czyta to pomiar latencji. Same parametry dekodowania się nie zmieniają.
    """

    def __init__(self, model, settings: LiveSettings, info: ModelInfo | None = None):
        self.model = model
        self.settings = settings
        self.info = info
        self.last_stats: dict | None = None
        self._token_cache: dict[str, int | None] = {}

    def _count_tokens(self, text: str | None) -> int | None:
        """Liczba tokenów tekstu wg tokenizera modelu (faster-whisper dokleja spację na początku)."""
        if not text:
            return 0
        if text in self._token_cache:
            return self._token_cache[text]
        count = None
        try:
            tokenizer = getattr(self.model, "hf_tokenizer", None)
            if tokenizer is not None:
                count = len(tokenizer.encode(" " + text.strip(), add_special_tokens=False).ids)
        except Exception:
            count = None
        if len(self._token_cache) > 64:
            self._token_cache.clear()
        self._token_cache[text] = count
        return count

    def __call__(self, audio: np.ndarray, prompt: str, final: bool) -> list[Word]:
        settings = self.settings
        language = None if settings.language == "auto" else settings.language
        hotwords = settings.hotwords.strip() or None
        options = dict(
            language=language,
            task="transcribe",
            beam_size=settings.final_beam if final else settings.live_beam,
            best_of=1,
            patience=1.0,            # domyślna wartość faster-whisper — podana jawnie do dziennika
            temperature=0.0,
            condition_on_previous_text=False,
            initial_prompt=prompt or None,
            word_timestamps=True,
            vad_filter=False,
            without_timestamps=False,
            hotwords=hotwords,
            max_new_tokens=max_new_tokens_for(len(audio) / SAMPLE_RATE, final),  # ucina pętle powtórzeń (224 tokeny)
            no_speech_threshold=0.6,
            log_prob_threshold=-1.0,
            compression_ratio_threshold=2.4,
        )
        cpu0 = time.thread_time()
        t0 = time.perf_counter()
        segments, _info = self.model.transcribe(audio, **options)
        t1 = time.perf_counter()
        t_first = None
        words: list[Word] = []
        seg_stats = []
        for segment in segments:
            if t_first is None:
                t_first = time.perf_counter()
            rejected = is_hallucinated_segment(
                segment.text, segment.no_speech_prob, segment.avg_logprob, segment.compression_ratio
            )
            tokens = getattr(segment, "tokens", None)
            # Segment, który dobił do limitu tokenów, to ucięta pętla powtórzeń. Po ucięciu jej
            # kompresja tekstu bywa niska, więc filtr jej nie łapie — odrzucamy ją sami i przerywamy
            # dekodowanie reszty bufora (kolejne okno kosztowałoby drugi przebieg enkodera, ok. 1,5 s).
            limit = options["max_new_tokens"]
            hit_limit = bool(limit and tokens is not None and len(tokens) >= limit)
            rejected = rejected or hit_limit
            seg_stats.append({
                "seek": getattr(segment, "seek", None),
                "start": getattr(segment, "start", None),
                "end": getattr(segment, "end", None),
                "tokens": len(tokens) if tokens is not None else None,
                "avg_logprob": segment.avg_logprob,
                "compression_ratio": segment.compression_ratio,
                "no_speech_prob": segment.no_speech_prob,
                "temperature": getattr(segment, "temperature", None),
                "rejected": rejected,
                "hit_limit": hit_limit,
                "text": (segment.text or "")[:160],
            })
            if rejected:
                log.debug("Pominięto segment: %r", segment.text)
                if hit_limit:
                    break
                continue
            for word in segment.words or []:
                if word.word:
                    words.append(Word(float(word.start), float(word.end), word.word))
        t2 = time.perf_counter()
        token_counts = [s["tokens"] for s in seg_stats if s["tokens"] is not None]
        total = t2 - t0
        tokens_total = sum(token_counts) if token_counts else None
        info = self.info
        self.last_stats = {
            "model": info.name if info else "",
            "compute_type": info.compute_type if info else "",
            "final": final,
            "beam_size": options["beam_size"],
            "best_of": options["best_of"],
            "temperature": options["temperature"],
            "patience": options["patience"],
            "condition_on_previous_text": options["condition_on_previous_text"],
            "word_timestamps": options["word_timestamps"],
            "max_new_tokens": options["max_new_tokens"],
            "audio_s": len(audio) / SAMPLE_RATE,
            "prompt_chars": len(prompt or ""),
            "prompt_tokens": self._count_tokens(prompt),
            "hotwords_tokens": self._count_tokens(hotwords),
            "segments": len(seg_stats),
            "rejected": sum(1 for s in seg_stats if s["rejected"]),
            "windows": len({s["seek"] for s in seg_stats if s["seek"] is not None}) or (1 if seg_stats else 0),
            "tokens": tokens_total,
            "tokens_per_s": (tokens_total / total) if tokens_total and total > 0 else None,
            "words": len(words),
            "prepare_s": t1 - t0,
            "first_segment_s": (t_first - t1) if t_first is not None else None,
            "rest_s": (t2 - t_first) if t_first is not None else t2 - t1,
            "thread_cpu_s": time.thread_time() - cpu0,
            "segment_list": seg_stats,
        }
        return words


class CloudTranscriber:
    """Funkcja transkrypcji dla LiveStreamer korzystająca z API w chmurze.

    W chmurze nie liczymy hipotez co 0,45 s (koszt i opóźnienie sieci) —
    LiveStreamer wywołuje nas tylko raz na wypowiedź, po pauzie. Usługi
    zwracają sam tekst, więc słowa dostają przybliżone czasy.
    """

    def __init__(self, model: ResolvedModel, settings: LiveSettings):
        self.model = model
        self.settings = settings

    def __call__(self, audio: np.ndarray, prompt: str, final: bool) -> list[Word]:
        duration = len(audio) / SAMPLE_RATE
        if duration < 0.1:
            return []
        settings = self.settings
        hotwords = tuple(
            word.strip() for word in re.split(r"[,;\n]", settings.hotwords or "") if word.strip()
        )
        request = Request(
            audio=encode_wav(audio, SAMPLE_RATE),
            language=None if settings.language == "auto" else settings.language,
            hotwords=hotwords,
            prompt=prompt,
        )
        started = time.monotonic()
        text = clean_text(transcribe(self.model, request))
        log.info("API %s: %.1f s dźwięku → %.2f s, %d znaków",
                 self.model.label, duration, time.monotonic() - started, len(text))
        tokens = text.split()
        if not tokens:
            return []
        step = duration / len(tokens)
        return [Word(i * step, (i + 1) * step, " " + token) for i, token in enumerate(tokens)]


# ---------------------------------------------------------------------------
# Mikrofon
# ---------------------------------------------------------------------------


class Microphone:
    """Nagrywa mikrofon przez sounddevice i oddaje dźwięk 16 kHz mono."""

    def __init__(self, on_audio):
        self.on_audio = on_audio
        self.stream = None
        self.system: SystemAudio | None = None
        self.level = 0.0
        self.resampler = None
        self.rate = SAMPLE_RATE

    @staticmethod
    def devices() -> list[str]:
        """Nazwy mikrofonów z domyślnego interfejsu audio systemu
        oraz — na końcu — źródła „dźwięk komputera” (loopback głośników)."""
        import sounddevice as sd

        try:
            default_input = sd.default.device[0]
            hostapi = sd.query_devices(default_input)["hostapi"] if default_input is not None and default_input >= 0 else 0
        except Exception:
            hostapi = 0
        names = []
        for device in sd.query_devices():
            if device["max_input_channels"] > 0 and device["hostapi"] == hostapi:
                if device["name"] not in names:
                    names.append(device["name"])
        try:
            names.extend(SystemAudio.devices())
        except Exception as exc:
            log.info("Dźwięk komputera (loopback) niedostępny: %s", exc)
        return names

    @staticmethod
    def _resolve(name: str | None):
        if not name:
            return None
        import sounddevice as sd

        try:
            default_input = sd.default.device[0]
            hostapi = sd.query_devices(default_input)["hostapi"] if default_input is not None and default_input >= 0 else None
        except Exception:
            hostapi = None
        candidates = [
            index for index, device in enumerate(sd.query_devices())
            if device["max_input_channels"] > 0 and device["name"] == name
        ]
        if not candidates:
            return None
        if hostapi is not None:
            for index in candidates:
                if sd.query_devices(index)["hostapi"] == hostapi:
                    return index
        return candidates[0]

    def start(self, name: str | None = None):
        self.stop()
        if SystemAudio.is_system(name):
            # Dźwięk komputera: SystemAudio sam przelicza do 16 kHz mono.
            system = SystemAudio(self._deliver)
            self.resampler = None
            self.rate = SAMPLE_RATE
            system.start(name)
            self.system = system
            latency.microphone_clock(False)
            return

        import sounddevice as sd

        device = self._resolve(name)
        errors = []
        rates = [SAMPLE_RATE]
        try:
            native = int(round(sd.query_devices(device, "input")["default_samplerate"]))
            if native != SAMPLE_RATE:
                rates.append(native)
        except Exception as exc:
            errors.append(str(exc))
        for rate in rates:
            try:
                stream = sd.InputStream(
                    device=device, samplerate=rate, channels=1, dtype="float32",
                    blocksize=int(rate * 0.05), callback=self._callback,
                )
                self.resampler = Resampler(rate, SAMPLE_RATE)
                self.rate = rate
                stream.start()
                self.stream = stream
                log.info("Mikrofon: %s @ %s Hz", name or "domyślny", rate)
                return
            except Exception as exc:
                errors.append(f"{rate} Hz: {exc}")
        raise RuntimeError("Nie udało się otworzyć mikrofonu.\n" + "\n".join(errors))

    def _callback(self, indata, frames, time_info, status):
        received = time.perf_counter()
        duration = frames / self.rate if self.rate else 0.0
        # Czas ostatniej próbki bloku: z zegara ADC karty (gdy sterownik go podaje),
        # inaczej moment odebrania bloku. Potrzebne do pomiaru latencji.
        end_time = received
        try:
            lag = float(time_info.currentTime) - float(time_info.inputBufferAdcTime)
            adc = 0.0 < lag < 1.0
            if adc:
                end_time = received - lag + duration
        except Exception:
            adc = False
        latency.microphone_clock(adc)
        mono = np.array(indata[:, 0] if indata.ndim == 2 else indata, dtype=np.float32)
        if self.resampler is not None:
            mono = self.resampler(mono)
        self._deliver(mono, end_time)

    def _deliver(self, mono: np.ndarray, end_time: float):
        """Dźwięk 16 kHz mono → poziom, pomiar latencji i kolejka silnika."""
        if len(mono):
            self.level = float(np.sqrt(np.mean(mono * mono)))
            # Poziom co 10 ms: początek głosu niezależnie od VAD (pomiar latencji).
            blocks = len(mono) // 160
            if blocks:
                levels = np.sqrt(np.mean(mono[: blocks * 160].reshape(blocks, 160) ** 2, axis=1))
                latency.mic_levels(levels.tolist(), end_time - (len(mono) - blocks * 160) / SAMPLE_RATE)
            self.on_audio((mono, end_time))

    def stop(self):
        stream, self.stream = self.stream, None
        system, self.system = self.system, None
        self.level = 0.0
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception as exc:
                log.debug("Zamykanie mikrofonu: %s", exc)
        if system is not None:
            system.stop()


# ---------------------------------------------------------------------------
# Dźwięk komputera (WASAPI loopback)
# ---------------------------------------------------------------------------

LOOPBACK_PREFIX = "🔊 Dźwięk komputera: "
LOOPBACK_AUTO = LOOPBACK_PREFIX + "wszystkie wyjścia (automatycznie)"
LOOPBACK_DEFAULT = LOOPBACK_PREFIX + "domyślne głośniki"
_LOOPBACK_SUFFIX = " [Loopback]"


class _Source:
    """Jedno wyjście audio Windows, z którego kopiujemy dźwięk (loopback)."""

    def __init__(self, device: dict, name: str):
        self.index = device["index"]
        self.name = name
        self.rate = int(round(device["defaultSampleRate"]))
        self.channels = max(1, int(device["maxInputChannels"]))
        self.resampler = Resampler(self.rate, SAMPLE_RATE)
        self.stream = None
        self.loud_at = -1e9      # kiedy ostatnio było tu słychać dźwięk
        self.blocks = 0          # ile bloków danych dotarło z tego wyjścia


class SystemAudio:
    """Przechwytuje to, co gra komputer (np. odtwarzacz z nagraniem testowym),
    zamiast mikrofonu — przez WASAPI loopback (biblioteka PyAudioWPatch).

    Dźwięk nie przechodzi przez głośniki i mikrofon: to cyfrowa kopia tego,
    co Windows wysyła na wybrane wyjście. Głośność systemowa nie ma znaczenia
    (Windows oddaje sygnał sprzed regulatora), ale wyciszenie odtwarzacza tak.

    Loopback słyszy tylko to wyjście, na które aplikacja faktycznie gra. Gdy w
    systemie są wirtualne urządzenia (SteelSeries Sonar, Voicemeeter, HDMI, USB),
    „domyślne głośniki” bywają innym wyjściem niż to, którego używa odtwarzacz —
    wtedy jest cisza. Tryb automatyczny słucha wszystkich wyjść naraz i oddaje
    dźwięk z tego, na którym akurat coś gra (nie sumuje ich, żeby nie dublować
    tego samego dźwięku przechodzącego przez kilka urządzeń).

    WASAPI nie dostarcza żadnych danych, gdy nic nie gra. Dla wykrywania mowy
    cisza musi jednak „płynąć” (inaczej wypowiedź nigdy się nie zakończy),
    więc luki dłuższe niż ``GAP`` wypełniamy zerami.
    """

    GAP = 0.12           # po tylu sekundach bez danych dosyłamy ciszę
    BLOCK = 0.05         # wielkość bloku jak przy mikrofonie
    LOUD = 0.0003        # RMS powyżej tego uznajemy za „coś gra” (ok. −70 dBFS)
    HOLD = 0.4           # tyle ciszy musi być na aktywnym wyjściu, by przełączyć na inne
    NOTICE_AFTER = 4.0   # po tylu sekundach bez żadnego dźwięku wpisujemy ostrzeżenie do dziennika

    def __init__(self, deliver):
        self.deliver = deliver          # deliver(mono_16k: np.ndarray, end_time: float)
        self.pa = None
        self.sources: list[_Source] = []
        self.device_name = ""
        self._active: _Source | None = None
        self._continue = 0
        self._lock = threading.Lock()
        self._last = 0.0
        self._started = 0.0
        self._heard = False
        self._noticed = False
        self._errors = 0
        self._stopping = threading.Event()
        self._watchdog: threading.Thread | None = None

    # -- lista urządzeń -----------------------------------------------------------

    @staticmethod
    def is_system(name: str | None) -> bool:
        return bool(name) and name.startswith(LOOPBACK_PREFIX)

    @staticmethod
    def _library():
        if not IS_WINDOWS:
            return None
        try:
            import pyaudiowpatch

            return pyaudiowpatch
        except ImportError:
            return None

    @staticmethod
    def _plain(name: str) -> str:
        return name[: -len(_LOOPBACK_SUFFIX)] if name.endswith(_LOOPBACK_SUFFIX) else name

    @classmethod
    def devices(cls) -> list[str]:
        pyaudio = cls._library()
        if pyaudio is None:
            log.info("Dźwięk komputera: brak biblioteki PyAudioWPatch — opcja niedostępna")
            return []
        pa = pyaudio.PyAudio()
        try:
            names = []
            for device in pa.get_loopback_device_info_generator():
                label = LOOPBACK_PREFIX + cls._plain(device["name"])
                if label not in names:
                    names.append(label)
            log.info("Dźwięk komputera: wyjścia do przechwycenia: %s", [n[len(LOOPBACK_PREFIX):] for n in names])
            return [LOOPBACK_AUTO, LOOPBACK_DEFAULT] + names if names else []
        finally:
            pa.terminate()

    def _find(self, pa, pyaudio, name: str) -> dict:
        loopbacks = list(pa.get_loopback_device_info_generator())
        if not loopbacks:
            raise RuntimeError("Windows nie udostępnia żadnego wyjścia audio do przechwycenia (WASAPI loopback).")
        if name == LOOPBACK_DEFAULT:
            try:
                return pa.get_default_wasapi_loopback()
            except Exception:
                wasapi = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
                wanted = pa.get_device_info_by_index(wasapi["defaultOutputDevice"])["name"]
        else:
            wanted = name[len(LOOPBACK_PREFIX):]
        for device in loopbacks:
            if self._plain(device["name"]) == wanted:
                return device
        for device in loopbacks:
            if wanted in device["name"]:
                return device
        if name == LOOPBACK_DEFAULT:
            return loopbacks[0]
        raise RuntimeError(f"Nie znaleziono wyjścia audio „{wanted}”. Odśwież listę urządzeń.")

    def _choose(self, pa, pyaudio, name: str) -> list[dict]:
        if name == LOOPBACK_AUTO:
            loopbacks = list(pa.get_loopback_device_info_generator())
            if not loopbacks:
                raise RuntimeError("Windows nie udostępnia żadnego wyjścia audio do przechwycenia (WASAPI loopback).")
            return loopbacks
        return [self._find(pa, pyaudio, name)]

    # -- strumień -------------------------------------------------------------------

    def _open(self, pa, pyaudio, device: dict) -> _Source:
        """Otwiera jedno wyjście; gdy domyślny format nie przejdzie, próbuje typowych."""
        source = _Source(device, self._plain(device["name"]))
        formats = [(source.channels, source.rate)]
        for fallback in ((2, 48000), (2, 44100), (1, source.rate)):
            if fallback not in formats:
                formats.append(fallback)
        last_error = None
        for channels, rate in formats:
            try:
                source.channels, source.rate = channels, rate
                source.resampler = Resampler(rate, SAMPLE_RATE)
                source.stream = pa.open(
                    format=pyaudio.paFloat32, channels=channels, rate=rate, input=True,
                    input_device_index=source.index, frames_per_buffer=int(rate * self.BLOCK),
                    stream_callback=self._make_callback(source),
                )
                return source
            except Exception as exc:
                last_error = exc
                log.info("Dźwięk komputera: „%s” nie otwiera się jako %d kan. @ %d Hz (%s)",
                         source.name, channels, rate, exc)
        raise RuntimeError(f"{source.name}: {last_error}")

    def start(self, name: str):
        pyaudio = self._library()
        if pyaudio is None:
            raise RuntimeError(
                "Przechwytywanie dźwięku komputera wymaga biblioteki PyAudioWPatch.\n"
                "Zainstaluj program ponownie."
            )
        self.stop()
        pa = pyaudio.PyAudio()
        errors: list[str] = []
        try:
            devices = self._choose(pa, pyaudio, name)
            self._continue = pyaudio.paContinue
            self._started = self._last = time.perf_counter()
            self._active = None
            self._heard = self._noticed = False
            self._errors = 0
            self._stopping.clear()
            self.pa = pa
            for device in devices:
                try:
                    self.sources.append(self._open(pa, pyaudio, device))
                except Exception as exc:
                    errors.append(str(exc))
                    log.warning("Dźwięk komputera: pominięto wyjście — %s", exc)
            if not self.sources:
                raise RuntimeError("Nie udało się otworzyć żadnego wyjścia audio do przechwycenia.\n" + "\n".join(errors))
        except Exception:
            self._close_streams()
            self.pa = None
            pa.terminate()
            raise
        self.device_name = ", ".join(source.name for source in self.sources)
        self._watchdog = threading.Thread(target=self._fill_silence, name="dyktowanie-cisza", daemon=True)
        self._watchdog.start()
        log.info("Dźwięk komputera (%s): %s", "automatycznie" if name == LOOPBACK_AUTO else "jedno wyjście",
                 "; ".join(f"{s.name} @ {s.rate} Hz, {s.channels} kan." for s in self.sources))

    def _make_callback(self, source: _Source):
        def callback(in_data, frame_count, time_info, status):
            received = time.perf_counter()
            try:
                data = np.frombuffer(in_data, dtype=np.float32)
                if source.channels > 1:
                    data = data[: len(data) - len(data) % source.channels].reshape(-1, source.channels).mean(axis=1)
                self._on_block(source, np.ascontiguousarray(data, dtype=np.float32), received)
            except Exception as exc:
                self._errors += 1
                if self._errors == 1 or self._errors % 200 == 0:
                    log.warning("Dźwięk komputera: błąd przetwarzania bloku (%d): %s", self._errors, exc)
            return None, self._continue

        return callback

    def _on_block(self, source: _Source, native: np.ndarray, end_time: float):
        rms = float(np.sqrt(np.mean(native * native))) if len(native) else 0.0
        with self._lock:
            if self._stopping.is_set():
                return
            source.blocks += 1
            if source.blocks == 1:
                log.info("Dźwięk komputera: pierwsze dane z wyjścia „%s”", source.name)
            if rms >= self.LOUD:
                source.loud_at = end_time
                if not self._heard:
                    self._heard = True
                    log.info("Dźwięk komputera: słychać dźwięk na wyjściu „%s” (RMS %.4f)", source.name, rms)
            active = self._active
            if active is not source:
                if active is not None:
                    if rms < self.LOUD or end_time - active.loud_at < self.HOLD:
                        return      # aktywne wyjście jeszcze gra albo to tylko cisza z innego urządzenia
                    log.info("Dźwięk komputera: przełączono z „%s” na „%s”", active.name, source.name)
                    source.resampler = Resampler(source.rate, SAMPLE_RATE)
                self._active = source
            self._last = end_time
            self.deliver(source.resampler(native), end_time)

    def _fill_silence(self):
        while not self._stopping.wait(self.BLOCK):
            with self._lock:
                now = time.perf_counter()
                if not self._heard and not self._noticed and now - self._started >= self.NOTICE_AFTER:
                    self._noticed = True
                    log.warning(
                        "Dźwięk komputera: przez %.0f s nic nie zagrało na wyjściach (%s). "
                        "Sprawdź, czy odtwarzacz gra na jednym z tych urządzeń i nie jest wyciszony.",
                        self.NOTICE_AFTER, ", ".join(f"{s.name} [{s.blocks} bloków]" for s in self.sources),
                    )
                gap = now - self._last
                if gap < self.GAP:
                    continue
                self._last = now
                self.deliver(np.zeros(int(gap * SAMPLE_RATE), dtype=np.float32), now)

    def _close_streams(self):
        sources, self.sources = self.sources, []
        for source in sources:
            stream, source.stream = source.stream, None
            if stream is None:
                continue
            try:
                stream.stop_stream()
                stream.close()
            except Exception as exc:
                log.debug("Zamykanie dźwięku komputera: %s", exc)

    def stop(self):
        self._stopping.set()
        self._close_streams()
        self._active = None
        pa, self.pa = self.pa, None
        if pa is not None:
            try:
                pa.terminate()
            except Exception:
                pass
        watchdog, self._watchdog = self._watchdog, None
        if watchdog is not None and watchdog is not threading.current_thread():
            watchdog.join(timeout=1)


# ---------------------------------------------------------------------------
# Blokada wspólna z BAPO-transcript
# ---------------------------------------------------------------------------

TRANSCRIPTION_LOCK_PATH = Path(tempfile.gettempdir()) / "bapo-transcript-whisperx.lock"


def batch_transcription_running() -> bool:
    """Czy BAPO-transcript właśnie transkrybuje (i zajmuje GPU)?"""
    if not TRANSCRIPTION_LOCK_PATH.exists():
        return False
    try:
        with open(TRANSCRIPTION_LOCK_PATH, "a+b") as handle:
            if IS_WINDOWS:
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(handle, fcntl.LOCK_UN)
        return False
    except OSError:
        return True


# ---------------------------------------------------------------------------
# Wątek roboczy
# ---------------------------------------------------------------------------


class EngineCallbacks:
    """Interfejs zwrotny — implementuje go most do Qt w warstwie interfejsu."""

    def status(self, text: str, busy: bool): ...
    def ready(self, info: ModelInfo): ...
    def failed(self, text: str): ...
    def partial(self, delta: str, tentative: str): ...
    def final(self, delta: str): ...
    def speech(self, active: bool): ...
    def listening(self, active: bool): ...


class DictationEngine:
    """Posiada model i zamienia dźwięk z mikrofonu na zdarzenia tekstowe."""

    def __init__(self, callbacks: EngineCallbacks):
        self.callbacks = callbacks
        self.settings = load_live_settings()
        self.audio: queue.Queue = queue.Queue()
        self.commands: queue.Queue = queue.Queue()
        self.microphone = Microphone(self.audio.put)
        self.model = None
        self.model_info: ModelInfo | None = None
        self.streamer: LiveStreamer | None = None
        self.listening = False
        self._finishing = False
        self._thread = threading.Thread(target=self._run, name="dyktowanie-asr", daemon=True)
        # Wątek VAD: wykrywa koniec wypowiedzi także wtedy, gdy wątek ASR liczy przebieg.
        self._vad_thread = threading.Thread(target=self._vad_run, name="dyktowanie-vad", daemon=True)
        self._audio_lock = threading.Lock()  # pobranie porcji z kolejki + push() jako jedna operacja
        self._starting = False               # od start_listening() do reset_session() dźwięk czeka w kolejce
        self._quit = threading.Event()

    # -- API wywoływane z wątku interfejsu ------------------------------------

    def start(self):
        self._thread.start()
        self._vad_thread.start()

    def load(self, key: str, device: str = "auto", cloud: ResolvedModel | None = None):
        """Lokalny model faster-whisper albo — gdy podano ``cloud`` — usługa API."""
        self.commands.put(("load", key, device, cloud))

    def start_listening(self, microphone: str | None = None):
        if self.listening:
            return
        self._starting = True
        while not self.audio.empty():
            try:
                self.audio.get_nowait()
            except queue.Empty:
                break
        self.microphone.start(microphone)
        self.listening = True
        self.commands.put(("start",))
        self.callbacks.listening(True)

    def switch_microphone(self, microphone: str | None = None):
        """Zmienia źródło dźwięku w trakcie nasłuchu (bez przerywania sesji)."""
        if not self.listening:
            return
        log.info("Zmiana źródła dźwięku w trakcie nasłuchu: %s", microphone or "domyślny")
        self.microphone.start(microphone)

    def stop_listening(self):
        if not self.listening:
            return
        self.microphone.stop()
        self.listening = False
        self.commands.put(("stop",))
        self.callbacks.listening(False)

    def shutdown(self):
        self.stop_listening()
        self.commands.put(("quit",))
        self._thread.join(timeout=3)
        self._quit.set()
        self._stop_resources()

    @property
    def level(self) -> float:
        return self.microphone.level if self.listening else 0.0

    # -- wątek -------------------------------------------------------------------

    def _run(self):
        while True:
            try:
                command = self.commands.get(timeout=0.03)
            except queue.Empty:
                command = None
            if command is not None:
                kind = command[0]
                if kind == "quit":
                    break
                if kind == "load":
                    if command[3] is not None:
                        self._load_cloud(command[3])
                    else:
                        self._load(command[1], command[2])
                elif kind == "start":
                    self._finishing = False
                    if self.streamer is not None:
                        self.streamer.reset_session()
                    self._starting = False
                elif kind == "stop":
                    self._finishing = True
                continue
            if self.streamer is None:
                continue
            self._process()

    def _vad_threaded(self, streamer) -> bool:
        """Osobny wątek VAD tylko dla modeli lokalnych z przebiegami na żywo."""
        return isinstance(streamer, LiveStreamer) and streamer.config.live_passes

    def _take_audio(self):
        chunks = []
        end_time = None
        oldest_age = 0.0  # jak dawno temu zarejestrowano najstarszy dźwięk z tej porcji
        loop_start = time.perf_counter()
        while True:
            try:
                item = self.audio.get_nowait()
            except queue.Empty:
                break
            if isinstance(item, tuple):
                item, end_time = item
                if not chunks:
                    oldest_age = max(0.0, loop_start - (end_time - len(item) / SAMPLE_RATE))
            chunks.append(item)
        return chunks, end_time, oldest_age

    def _vad_run(self):
        while not self._quit.is_set():
            streamer = self.streamer
            if self._starting or not self._vad_threaded(streamer):
                time.sleep(0.03)
                continue
            with self._audio_lock:
                chunks, end_time, oldest_age = self._take_audio()
                if chunks:
                    try:
                        pushed = time.perf_counter()
                        streamer.push(np.concatenate(chunks), end_time=end_time)
                        latency.engine_loop(time.perf_counter() - pushed, 0.0, oldest_age)
                    except Exception:
                        log.exception("Błąd wykrywania mowy")
            if not chunks:
                time.sleep(0.005)

    def _process(self):
        threaded = self._vad_threaded(self.streamer)
        chunks, end_time, oldest_age = ([], None, 0.0) if threaded else self._take_audio()
        try:
            push_seconds = 0.0
            if chunks:
                pushed = time.perf_counter()
                self.streamer.push(np.concatenate(chunks), end_time=end_time)
                push_seconds = time.perf_counter() - pushed
            with self._audio_lock:  # wątek VAD nie trzyma wtedy porcji „w ręku”
                force = self._finishing and self.audio.empty()
            stepped = time.perf_counter()
            events = self.streamer.step(force_final=force)
            if chunks:
                latency.engine_loop(push_seconds, time.perf_counter() - stepped, oldest_age)
            if force:
                self._finishing = False
        except Exception as exc:
            if isinstance(exc, ApiError):
                log.warning("Błąd API: %s", exc)
            else:
                log.exception("Błąd transkrypcji")
            self.callbacks.failed(f"Błąd transkrypcji: {exc}")
            self.streamer._reset_utterance()
            self.callbacks.final("")
            return
        for event in events:
            if event[0] == "partial":
                self.callbacks.partial(event[1], event[2])
            elif event[0] == "final":
                self.callbacks.final(event[1])
            elif event[0] == "speech":
                self.callbacks.speech(event[1])

    def _start_resources(self, gpu: bool):
        """Monitor GPU/CPU na czas pracy modelu lokalnego; błąd nie przerywa ładowania."""
        try:
            resources.start(gpu=gpu)
            latency.resource_summary = resources.summary
        except Exception:
            log.warning("Nie uruchomiono monitora zasobów", exc_info=True)

    def _stop_resources(self):
        latency.resource_summary = None
        try:
            resources.stop()
        except Exception:
            log.debug("Zatrzymanie monitora zasobów", exc_info=True)

    def _release_model(self):
        closer = getattr(self.streamer, "close", None)
        if closer is not None:
            try:
                closer()
            except Exception:
                log.debug("Zamykanie połączenia strumieniowego", exc_info=True)
        self.streamer = None
        if self.model is not None:
            self.model = None
            gc.collect()

    def _load_cloud(self, cloud: ResolvedModel):
        """Przełącza na usługę w chmurze — zwalnia lokalny model (i pamięć GPU)."""
        self._release_model()
        self._stop_resources()
        if cloud.needs_key:
            self.model_info = None
            self.callbacks.failed(
                f"Brak klucza API dla: {cloud.service}. Kliknij „Klucze API…” i wklej klucz."
            )
            return
        if cloud.stream:
            from dyktowanie_stream import DeepgramStreamer, websockets_available

            if websockets_available():
                self.streamer = DeepgramStreamer(cloud, self.settings)
                self.streamer.on_timing = latency.stream_event
                label = cloud.label
                endpointing = self.streamer.endpointing
                self.model_info = ModelInfo(
                    cloud.key, label, "api", "",
                    "strumień" + (f", endpointing {endpointing} ms" if endpointing else ""),
                )
                latency.model = label
                log.info("Tryb chmurowy (strumień WebSocket): %s, %s", cloud.label, cloud.base_url)
                self.callbacks.ready(self.model_info)
                return
            log.warning("Brak biblioteki websockets — %s działa bez strumienia (zapytanie po pauzie).", cloud.service)
            self.callbacks.status(
                "Brak biblioteki „websockets” (pip install websockets) — tryb strumieniowy wyłączony.", True
            )
        self.callbacks.status(f"Łączenie z {cloud.service}…", True)
        try:
            vad = make_vad()
        except Exception:
            from dyktowanie_core import EnergyVAD

            vad = EnergyVAD()
        self.settings.stream.live_passes = False
        self.settings.stream.max_buffer = 30.0
        info = ModelInfo(cloud.key, cloud.label, "api", "", vad.name)
        self.model_info = info
        self.streamer = LiveStreamer(CloudTranscriber(cloud, self.settings), vad, self.settings.stream)
        self.streamer.on_timing = latency.stream_event
        latency.model = self.model_info.label if self.model_info else ""
        log.info("Tryb chmurowy: %s (%s, %s), VAD: %s", cloud.label, cloud.kind, cloud.base_url, vad.name)
        self.callbacks.ready(info)

    def _load(self, key: str, device: str):
        self._release_model()
        self.settings.stream.live_passes = True
        self.settings.stream.max_buffer = StreamConfig.max_buffer
        self.callbacks.status("Przygotowanie modelu…", True)
        try:
            started = time.monotonic()
            model, info = load_whisper(key, device, status=lambda text: self.callbacks.status(text, True))
            vad = make_vad()
            info.vad = vad.name
            self.model = model
            self.model_info = info
            self.streamer = LiveStreamer(WhisperTranscriber(model, self.settings, info), vad, self.settings.stream)
            self.streamer.on_timing = latency.stream_event
            latency.model = self.model_info.label if self.model_info else ""
            log.info("Model gotowy w %.1f s: %s, VAD: %s", time.monotonic() - started, info.label, vad.name)
            self._start_resources(gpu=info.device == "cuda")
            self.callbacks.ready(info)
        except ImportError as exc:
            log.exception("Brak biblioteki")
            self.callbacks.failed(
                f"Brakuje biblioteki faster-whisper w środowisku .venv ({exc})."
            )
        except Exception as exc:
            log.exception("Ładowanie modelu")
            self.callbacks.failed(str(exc))
