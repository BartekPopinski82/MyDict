"""Monitor zasobów podczas dyktowania: GPU (NVML albo nvidia-smi) i CPU (psutil).

Wątek tła co ``INTERVAL`` s zapisuje próbkę do ``gpu.csv`` i do bufora ostatnich
``HISTORY_SECONDS`` s. Pomiar latencji pyta ``summary(t0, t1)`` o próbki z czasu
jednego przebiegu ASR — oba używają ``time.perf_counter()``, więc da się je zestawić.

Źródła GPU, w kolejności:
* NVML przez pakiet ``nvidia-ml-py`` (moduł ``pynvml``) — wywołanie trwa mikrosekundy,
* ``nvidia-smi -lms`` jako jeden długo działający proces (gdy brak pynvml).
CPU: ``psutil`` (proces i cały system); bez psutil tylko CPU procesu z ``time.process_time``.

Monitor niczego nie optymalizuje i nigdy nie przerywa dyktowania — każdy błąd
odczytu zostawia puste pole w próbce.
"""

from __future__ import annotations

import csv
import logging
import os
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

log = logging.getLogger("dyktowanie.gpu")
IS_WINDOWS = sys.platform.startswith("win")

INTERVAL = 0.2            # s między próbkami
HISTORY_SECONDS = 120     # tyle historii trzymamy w pamięci (do podsumowań przebiegów)
CSV_FLUSH_SECONDS = 1.0

# Powody obniżenia zegara GPU (maska NVML clocks event / throttle reasons).
REASONS = {
    0x1: "bezczynność",
    0x2: "ustawienia aplikacji",
    0x4: "limit mocy (SW)",
    0x8: "spowolnienie sprzętowe",
    0x10: "sync boost",
    0x20: "termika (SW)",
    0x40: "termika (HW)",
    0x80: "hamulec zasilania (HW)",
    0x100: "zegar wyświetlacza",
}

CSV_COLUMNS = [
    "czas", "perf", "zrodlo", "gpu_util", "mem_util", "vram_mb", "vram_total_mb", "sm_mhz",
    "mem_mhz", "temp_c", "moc_w", "pstate", "ograniczenia", "ograniczenia_maska",
    "cpu_proces", "cpu_system",
]

_PERF0 = time.perf_counter()
_WALL0 = time.time()


def _clock(t: float) -> str:
    return datetime.fromtimestamp(_WALL0 + (t - _PERF0)).strftime("%H:%M:%S.%f")[:-3]


def describe_reasons(mask: int | None) -> str:
    """Maska powodów → tekst; bezczynność przy pracy jest normalna dla GPU między przebiegami."""
    if mask is None:
        return ""
    if mask == 0:
        return "brak"
    names = [name for bit, name in REASONS.items() if mask & bit]
    rest = mask & ~sum(REASONS)
    if rest:
        names.append(f"inne 0x{rest:x}")
    return ", ".join(names)


@dataclass
class Sample:
    t: float
    util: float | None = None
    mem_util: float | None = None
    vram_mb: float | None = None
    vram_total_mb: float | None = None
    sm_mhz: int | None = None
    mem_mhz: int | None = None
    temp_c: int | None = None
    power_w: float | None = None
    pstate: str | None = None
    reasons: int | None = None
    cpu_proc: float | None = None    # % jednego rdzenia (100 = jeden rdzeń w pełni)
    cpu_sys: float | None = None     # % całego CPU


# ---------------------------------------------------------------------------
# Źródła GPU
# ---------------------------------------------------------------------------


class NvmlSource:
    name = "NVML"

    def __init__(self, index: int = 0):
        import pynvml

        pynvml.nvmlInit()
        self.nv = pynvml
        self.handle = pynvml.nvmlDeviceGetHandleByIndex(index)
        name = pynvml.nvmlDeviceGetName(self.handle)
        self.device = name.decode() if isinstance(name, bytes) else str(name)
        self._reasons = (getattr(pynvml, "nvmlDeviceGetCurrentClocksEventReasons", None)
                         or getattr(pynvml, "nvmlDeviceGetCurrentClocksThrottleReasons", None))

    def read(self, sample: Sample):
        nv, h = self.nv, self.handle
        try:
            rates = nv.nvmlDeviceGetUtilizationRates(h)
            sample.util, sample.mem_util = float(rates.gpu), float(rates.memory)
        except Exception:
            pass
        try:
            memory = nv.nvmlDeviceGetMemoryInfo(h)
            sample.vram_mb = memory.used / 2**20
            sample.vram_total_mb = memory.total / 2**20
        except Exception:
            pass
        try:
            sample.sm_mhz = int(nv.nvmlDeviceGetClockInfo(h, nv.NVML_CLOCK_SM))
        except Exception:
            pass
        try:
            sample.mem_mhz = int(nv.nvmlDeviceGetClockInfo(h, nv.NVML_CLOCK_MEM))
        except Exception:
            pass
        try:
            sample.temp_c = int(nv.nvmlDeviceGetTemperature(h, nv.NVML_TEMPERATURE_GPU))
        except Exception:
            pass
        try:
            sample.power_w = nv.nvmlDeviceGetPowerUsage(h) / 1000.0
        except Exception:
            pass
        try:
            sample.pstate = f"P{int(nv.nvmlDeviceGetPerformanceState(h))}"
        except Exception:
            pass
        if self._reasons is not None:
            try:
                sample.reasons = int(self._reasons(h))
            except Exception:
                pass

    def close(self):
        try:
            self.nv.nvmlShutdown()
        except Exception:
            pass


class SmiSource:
    """``nvidia-smi --query-gpu=… -lms N`` jako jeden proces; czytamy ostatnią linię."""

    name = "nvidia-smi"
    BASE = ["utilization.gpu", "utilization.memory", "memory.used", "memory.total", "clocks.sm",
            "clocks.mem", "temperature.gpu", "power.draw", "pstate"]
    REASON_FIELDS = ["clocks_event_reasons.active", "clocks_throttle_reasons.active", None]

    def __init__(self, interval: float = INTERVAL):
        self.device = ""
        self.latest: tuple[float, list[str]] | None = None
        self.process = None
        self.fields: list[str] = []
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if IS_WINDOWS else 0
        try:
            name = subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                                  capture_output=True, text=True, timeout=10, creationflags=flags)
            self.device = (name.stdout or "").strip().splitlines()[0] if name.stdout.strip() else ""
        except Exception as exc:
            raise RuntimeError(f"nvidia-smi niedostępne: {exc}") from exc
        for reason in self.REASON_FIELDS:
            fields_ = self.BASE + ([reason] if reason else [])
            test = subprocess.run(["nvidia-smi", f"--query-gpu={','.join(fields_)}",
                                   "--format=csv,noheader,nounits"],
                                  capture_output=True, text=True, timeout=10, creationflags=flags)
            if test.returncode == 0 and test.stdout.strip():
                self.fields = fields_
                break
        if not self.fields:
            raise RuntimeError("nvidia-smi nie zwraca danych")
        self.process = subprocess.Popen(
            ["nvidia-smi", f"--query-gpu={','.join(self.fields)}", "--format=csv,noheader,nounits",
             f"-lms={max(50, int(interval * 1000))}"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1, creationflags=flags,
        )
        self._reader = threading.Thread(target=self._read_loop, name="dyktowanie-nvidia-smi", daemon=True)
        self._reader.start()

    def _read_loop(self):
        try:
            for line in self.process.stdout:
                parts = [part.strip() for part in line.strip().split(",")]
                if len(parts) == len(self.fields):
                    self.latest = (time.perf_counter(), parts)
        except Exception:
            pass

    @staticmethod
    def _num(text: str, kind=float):
        try:
            return kind(float(text))
        except (TypeError, ValueError):
            return None

    def read(self, sample: Sample):
        latest = self.latest
        if latest is None or sample.t - latest[0] > 1.0:
            return
        values = dict(zip(self.fields, latest[1]))
        sample.util = self._num(values.get("utilization.gpu"))
        sample.mem_util = self._num(values.get("utilization.memory"))
        sample.vram_mb = self._num(values.get("memory.used"))
        sample.vram_total_mb = self._num(values.get("memory.total"))
        sample.sm_mhz = self._num(values.get("clocks.sm"), int)
        sample.mem_mhz = self._num(values.get("clocks.mem"), int)
        sample.temp_c = self._num(values.get("temperature.gpu"), int)
        sample.power_w = self._num(values.get("power.draw"))
        sample.pstate = values.get("pstate") or None
        reason = values.get("clocks_event_reasons.active") or values.get("clocks_throttle_reasons.active")
        if reason:
            try:
                sample.reasons = int(reason, 16)
            except ValueError:
                pass

    def close(self):
        process, self.process = self.process, None
        if process is not None:
            try:
                process.terminate()
                process.wait(timeout=2)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass


def open_gpu_source(interval: float = INTERVAL):
    """NVML, a gdy brak pynvml lub sterownika — nvidia-smi. ``None``, gdy żadne nie działa."""
    errors = []
    try:
        return NvmlSource()
    except Exception as exc:
        errors.append(f"NVML: {exc}")
    try:
        return SmiSource(interval)
    except Exception as exc:
        errors.append(f"nvidia-smi: {exc}")
    log.warning("Monitor GPU niedostępny (%s)", "; ".join(errors))
    return None


# ---------------------------------------------------------------------------
# CPU
# ---------------------------------------------------------------------------


class CpuSource:
    def __init__(self):
        self.name = "psutil"
        self._process = None
        self._psutil = None
        try:
            import psutil

            self._psutil = psutil
            self._process = psutil.Process(os.getpid())
            self._process.cpu_percent(None)
            psutil.cpu_percent(None)
        except Exception:
            self.name = "process_time (bez psutil — tylko CPU procesu)"
        self._last = (time.perf_counter(), time.process_time())

    def read(self, sample: Sample):
        if self._process is not None:
            try:
                sample.cpu_proc = float(self._process.cpu_percent(None))
                sample.cpu_sys = float(self._psutil.cpu_percent(None))
                return
            except Exception:
                pass
        wall, cpu = time.perf_counter(), time.process_time()
        last_wall, last_cpu = self._last
        self._last = (wall, cpu)
        if wall > last_wall:
            sample.cpu_proc = 100.0 * (cpu - last_cpu) / (wall - last_wall)


# ---------------------------------------------------------------------------
# Monitor
# ---------------------------------------------------------------------------


class ResourceMonitor:
    def __init__(self, interval: float = INTERVAL):
        self.interval = interval
        self._csv_path: Path | None = None
        self._samples: deque[Sample] = deque(maxlen=max(10, int(HISTORY_SECONDS / interval)))
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._gpu = None
        self._cpu: CpuSource | None = None
        self._source_name = ""
        # do testów: podstawione źródła zamiast prawdziwych
        self.gpu_factory = open_gpu_source
        self.cpu_factory = CpuSource

    def configure(self, csv_path: Path | None):
        self._csv_path = csv_path

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, gpu: bool = True):
        """Uruchamia próbkowanie (GPU tylko gdy ``gpu``; CPU zawsze). Ponowne wywołanie
        przy działającym monitorze zmienia tylko to, czy czytamy GPU."""
        if self.running:
            if gpu and self._gpu is None:
                self._gpu = self.gpu_factory(self.interval)
            elif not gpu and self._gpu is not None:
                self._gpu.close()
                self._gpu = None
            self._describe()
            return
        self._stop.clear()
        self._gpu = self.gpu_factory(self.interval) if gpu else None
        self._cpu = self.cpu_factory()
        self._describe()
        self._thread = threading.Thread(target=self._run, name="dyktowanie-zasoby", daemon=True)
        self._thread.start()

    def _describe(self):
        gpu = self._gpu
        self._source_name = gpu.name if gpu is not None else "brak GPU"
        log.info("Monitor zasobów: GPU %s, co %d ms; CPU: %s; CSV: %s",
                 f"{gpu.device} przez {gpu.name}" if gpu is not None else "wyłączony",
                 int(self.interval * 1000), self._cpu.name if self._cpu else "—", self._csv_path)

    def stop(self):
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=2)
        gpu, self._gpu = self._gpu, None
        if gpu is not None:
            gpu.close()

    def sample_now(self) -> Sample:
        sample = Sample(time.perf_counter())
        gpu = self._gpu
        if gpu is not None:
            try:
                gpu.read(sample)
            except Exception:
                log.debug("Odczyt GPU", exc_info=True)
        if self._cpu is not None:
            try:
                self._cpu.read(sample)
            except Exception:
                log.debug("Odczyt CPU", exc_info=True)
        return sample

    def add(self, sample: Sample):
        with self._lock:
            self._samples.append(sample)

    def _run(self):
        handle, writer = self._open_csv()
        last_flush = time.perf_counter()
        next_t = time.perf_counter()
        try:
            while not self._stop.is_set():
                sample = self.sample_now()
                self.add(sample)
                if writer is not None:
                    try:
                        writer.writerow(self._row(sample))
                        if sample.t - last_flush >= CSV_FLUSH_SECONDS:
                            handle.flush()
                            last_flush = sample.t
                    except Exception:
                        log.debug("Zapis gpu.csv", exc_info=True)
                next_t += self.interval
                delay = next_t - time.perf_counter()
                if delay < 0:
                    next_t = time.perf_counter()
                    delay = 0
                self._stop.wait(delay)
        finally:
            if handle is not None:
                try:
                    handle.close()
                except Exception:
                    pass

    def _open_csv(self):
        path = self._csv_path
        if path is None:
            return None, None
        try:
            new = not path.exists()
            if not new:
                with open(path, encoding="utf-8-sig") as existing:
                    if existing.readline().strip() != ";".join(CSV_COLUMNS):
                        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                        path.rename(path.with_name(f"{path.stem}_stary_{stamp}{path.suffix}"))
                        new = True
            handle = open(path, "a", newline="", encoding="utf-8-sig")
            writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS, delimiter=";")
            if new:
                writer.writeheader()
            return handle, writer
        except OSError as exc:
            log.warning("Nie można zapisywać %s: %s", path, exc)
            return None, None

    def _row(self, s: Sample) -> dict:
        def r(value, digits=0):
            return "" if value is None else round(value, digits)

        return {
            "czas": _clock(s.t), "perf": f"{s.t:.3f}", "zrodlo": self._source_name,
            "gpu_util": r(s.util), "mem_util": r(s.mem_util), "vram_mb": r(s.vram_mb),
            "vram_total_mb": r(s.vram_total_mb), "sm_mhz": r(s.sm_mhz), "mem_mhz": r(s.mem_mhz),
            "temp_c": r(s.temp_c), "moc_w": r(s.power_w, 1), "pstate": s.pstate or "",
            "ograniczenia": describe_reasons(s.reasons),
            "ograniczenia_maska": "" if s.reasons is None else f"0x{s.reasons:x}",
            "cpu_proces": r(s.cpu_proc), "cpu_system": r(s.cpu_sys),
        }

    def summary(self, t0: float, t1: float) -> dict | None:
        """Zestawienie próbek z czasu [t0, t1] (jeden przebieg ASR). Gdy przebieg był
        krótszy niż odstęp próbkowania — najbliższa próbka po t0."""
        with self._lock:
            samples = [s for s in self._samples if t0 <= s.t <= t1]
            if not samples:
                later = [s for s in self._samples if t0 <= s.t <= t1 + self.interval]
                samples = later[:1]
        if not samples:
            return None

        def values(name):
            return [getattr(s, name) for s in samples if getattr(s, name) is not None]

        def stat(name, fn):
            data = values(name)
            return fn(data) if data else None

        def avg(data):
            return sum(data) / len(data)

        reasons = 0
        any_reason = False
        for s in samples:
            if s.reasons is not None:
                any_reason = True
                reasons |= s.reasons
        pstates = []
        for p in values("pstate"):
            if p not in pstates:
                pstates.append(p)
        return {
            "samples": len(samples),
            "util_min": stat("util", min), "util_avg": stat("util", avg), "util_max": stat("util", max),
            "sm_min": stat("sm_mhz", min), "sm_max": stat("sm_mhz", max),
            "mem_mhz_min": stat("mem_mhz", min), "mem_mhz_max": stat("mem_mhz", max),
            "pstate": "→".join(pstates) or None,
            "power_max": stat("power_w", max), "temp_max": stat("temp_c", max),
            "vram_max": None if stat("vram_mb", max) is None else round(stat("vram_mb", max)),
            "reasons": describe_reasons(reasons) if any_reason else None,
            "cpu_proc_avg": stat("cpu_proc", avg), "cpu_proc_max": stat("cpu_proc", max),
            "cpu_sys_max": stat("cpu_sys", max),
        }


monitor = ResourceMonitor()
