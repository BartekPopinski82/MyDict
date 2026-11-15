"""Szybki test: ile trwa jeden przebieg modelu na tym GPU (bez mikrofonu i UI).
Wynik zapisuje do katalogu danych programu (test_gpu.txt)."""
import os, subprocess, sys, time
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dyktowanie_asr import prepare_gpu_libraries

out = []
def p(*a):
    line = " ".join(str(x) for x in a); print(line, flush=True); out.append(line)

try:
    smi = subprocess.run(["nvidia-smi", "--query-gpu=name,driver_version,memory.total,memory.used,"
                          "utilization.gpu,pstate,clocks.sm,clocks.max.sm,power.draw",
                          "--format=csv"], capture_output=True, text=True, timeout=15)
    p("nvidia-smi:\n" + (smi.stdout or smi.stderr).strip())
except Exception as e:
    p("nvidia-smi niedostępne:", e)

p("Biblioteki CUDA:", prepare_gpu_libraries())
import ctranslate2
from faster_whisper import WhisperModel, download_model
p("ctranslate2", ctranslate2.__version__, "| GPU:", ctranslate2.get_cuda_device_count(),
  "| typy cuda:", sorted(ctranslate2.get_supported_compute_types("cuda")))

rng = np.random.default_rng(0)
def audio(sec):  # cichy szum - wymusza pełny encoder, dekoder krótki
    return (rng.standard_normal(int(16000 * sec)) * 0.003).astype(np.float32)

def bench(name, device, ctype, runs=4):
    try:
        t = time.perf_counter()
        m = WhisperModel(download_model(name, local_files_only=True), device=device, compute_type=ctype)
        p(f"\n== {name} {device}/{ctype} (ładowanie {time.perf_counter()-t:.1f} s)")
        for sec, wts, beam in ((1, False, 1), (1, True, 1), (10, True, 1), (10, True, 5)):
            times = []
            for _ in range(runs):
                t = time.perf_counter()
                segs, _ = m.transcribe(audio(sec), language="pl", beam_size=beam, best_of=1, temperature=0.0,
                                       vad_filter=False, word_timestamps=wts, condition_on_previous_text=False)
                list(segs); times.append((time.perf_counter() - t) * 1000)
            p(f"  audio {sec:>2} s | word_ts={wts!s:5} | beam {beam}: "
              f"min {min(times[1:]):.0f} ms, śr {np.mean(times[1:]):.0f} ms (1. przebieg {times[0]:.0f})")
        del m
    except Exception as e:
        p(f"\n== {name} {device}/{ctype}: BŁĄD {e}")

bench("large-v3-turbo", "cuda", "int8_float16")
bench("large-v3-turbo", "cuda", "float16")
bench("small", "cuda", "float16")
try:
    smi = subprocess.run(["nvidia-smi", "--query-gpu=pstate,clocks.sm,utilization.gpu,memory.used",
                          "--format=csv"], capture_output=True, text=True, timeout=15)
    p("\nnvidia-smi po teście:\n" + smi.stdout.strip())
except Exception:
    pass
dest = Path(os.environ.get("LOCALAPPDATA", ".")) / "MyDict" / "test_gpu.txt"
dest.write_text("\n".join(out), encoding="utf-8")
p("\nZapisano:", dest)
