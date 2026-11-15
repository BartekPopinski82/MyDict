"""Automatyczny test opóźnień dyktowania — bez mikrofonu, głośników i klikania.

Odtwarza plik wideo/audio przez prawdziwy silnik dyktowania (DictationEngine, ten sam kod
co w programie) w czasie rzeczywistym, kolejno dla każdego modelu, kilka razy. Zapisuje
dzienniki i CSV w osobnym folderze (testy\\wyniki_auto\\<data_godzina>), więc nie miesza ich
z dziennikami programu.

Przykłady (z folderu BAPO-dyktowanie, środowiskiem .venv):
  ..\\.venv\\Scripts\\python testy\\Test_automatyczny.py
  ..\\.venv\\Scripts\\python testy\\Test_automatyczny.py --modele small,turbo --powtorzenia 2
  ..\\.venv\\Scripts\\python testy\\Test_automatyczny.py --kod _kopia_przed_limit_tokenow --nazwa przed_limitem

Modele: small, medium, turbo (large-v3-turbo), large-v3, deepgram (Nova-3) oraz dowolny model
w chmurze podany kluczem programu, np. api:deepgram:nova-2. Klucze API i adresy — te same, co
w programie („Klucze API…”), albo zmienna środowiskowa / secrets.env.

--kod <folder_kopii> uruchamia test na starszej wersji kodu: pliki .py projektu plus nadpisane
plikami z podanego folderu kopii (_kopia_przed_...). Dzięki temu porównanie „przed i po” robi
się TĄ SAMĄ metodą (to samo odtwarzanie z pliku), a nie dziennikami z innego sposobu testu.

Czasy „wspólne” (tabela na końcu) liczone są jednakowo dla wszystkich modeli, także Deepgrama:
z momentów zdarzeń tekstowych silnika względem początku i końca mowy w nagraniu.
"""

from __future__ import annotations

import argparse
import csv
import gc
import json
import logging
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

import numpy as np

PROJECT = Path(__file__).resolve().parent.parent
SAMPLE_RATE = 16000
BLOCK_S = 0.05               # co ile dostarczamy dźwięk (jak blok z karty dźwiękowej)
TAIL_S = 3.0                 # cisza po nagraniu: pozwala wykryć koniec ostatniej wypowiedzi
GAP_BETWEEN_S = 3.0          # przerwa między odtworzeniami
DEFAULT_FILE = r"E:\filmy\2026-10-01_19-54-28.mp4"
MODEL_KEYS = {
    "small": "small", "medium": "medium", "turbo": "turbo", "large-v3-turbo": "turbo",
    "large-v3": "large-v3", "deepgram": "api:deepgram:nova-3",
}


# -- dźwięk ------------------------------------------------------------------------------------------


def decode_audio(path: str) -> np.ndarray:
    """Plik → mono float32 16 kHz. Najpierw PyAV (jest w środowisku z faster-whisper), potem ffmpeg."""
    try:
        import av

        resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
        chunks = []
        with av.open(path) as container:
            stream = next(s for s in container.streams if s.type == "audio")
            for frame in container.decode(stream):
                for out in resampler.resample(frame):
                    chunks.append(out.to_ndarray().reshape(-1))
            for out in resampler.resample(None):
                chunks.append(out.to_ndarray().reshape(-1))
        return np.concatenate(chunks).astype(np.float32) / 32768.0
    except ImportError:
        pass
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise SystemExit("Brak biblioteki av i programu ffmpeg — nie mogę odczytać pliku.")
    raw = subprocess.run(
        [ffmpeg, "-v", "error", "-i", path, "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "s16le", "-"],
        capture_output=True, check=True,
    ).stdout
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def speech_segments(audio: np.ndarray, merge_gap_s: float = 1.0) -> list[tuple[float, float]]:
    """Przybliżone odcinki mowy z poziomu RMS co 10 ms (próg nad szumem tła)."""
    blocks = len(audio) // 160
    rms = np.sqrt(np.mean(audio[: blocks * 160].reshape(blocks, 160) ** 2, axis=1))
    noise = float(np.percentile(rms, 20))
    threshold = max(0.004, 3.0 * noise)
    active = rms > threshold
    segments: list[list[float]] = []
    for index in np.flatnonzero(active):
        t = index * 0.01
        if segments and t - segments[-1][1] <= merge_gap_s:
            segments[-1][1] = t + 0.01
        else:
            segments.append([t, t + 0.01])
    return [(s, e) for s, e in segments if e - s >= 0.25]


# -- kod do testu (opcjonalnie starsza wersja) ------------------------------------------------------------------


def prepare_code(overlay: str | None) -> Path:
    if not overlay:
        return PROJECT
    source = PROJECT / overlay
    if not source.is_dir():
        raise SystemExit(f"Nie ma folderu kopii: {source}")
    target = Path(tempfile.mkdtemp(prefix="bapo_kod_"))
    for path in PROJECT.glob("*.py"):
        shutil.copy2(path, target / path.name)
    for name in ("dostawcy.json", "secrets.env", ".env"):
        for base in (PROJECT, PROJECT.parent):
            if (base / name).is_file():
                shutil.copy2(base / name, target / name)
                break
    for path in source.glob("*.py"):
        shutil.copy2(path, target / path.name)
        print(f"  nadpisano z kopii: {path.name}")
    return target


# -- callbacki silnika -------------------------------------------------------------------------------------------


class Recorder:
    """Implementuje EngineCallbacks (duck typing) i zapisuje zdarzenia tekstowe z czasem."""

    def __init__(self):
        self.ready_event = threading.Event()
        self.error: str | None = None
        self.events: list[tuple[float, str, str, str]] = []

    def status(self, text, busy):
        logging.getLogger("test").info("status: %s", text)

    def ready(self, info):
        self.ready_event.set()

    def failed(self, text):
        self.error = text
        logging.getLogger("test").warning("błąd silnika: %s", text)
        self.ready_event.set()

    def partial(self, delta, tentative):
        self.events.append((time.perf_counter(), "partial", delta or "", tentative or ""))

    def final(self, delta):
        self.events.append((time.perf_counter(), "final", delta or "", ""))

    def speech(self, active):
        pass

    def listening(self, active):
        pass


# -- odtwarzanie ---------------------------------------------------------------------------------------------------


def feed(engine, audio: np.ndarray, t0: float):
    """Dostarcza dźwięk w czasie rzeczywistym, tak jak mikrofon (przez Microphone._deliver)."""
    step = int(BLOCK_S * SAMPLE_RATE)
    index = 0
    while index < len(audio):
        block = audio[index:index + step].astype(np.float32)
        index += step
        wait = t0 + index / SAMPLE_RATE - time.perf_counter()
        if wait > 0:
            time.sleep(wait)
        engine.microphone._deliver(block, time.perf_counter())


def replay(engine, recorder: Recorder, latency, audio: np.ndarray, settle_timeout: float = 60.0):
    latency.reset_session()
    latency.microphone_clock(False)
    while not engine.audio.empty():
        try:
            engine.audio.get_nowait()
        except Exception:
            break
    recorder.events = []
    engine.listening = True
    engine.commands.put(("start",))
    time.sleep(0.3)
    t0 = time.perf_counter()
    feed(engine, np.concatenate([audio, np.zeros(int(TAIL_S * SAMPLE_RATE), dtype=np.float32)]), t0)
    engine.listening = False
    engine.commands.put(("stop",))
    deadline = time.perf_counter() + settle_timeout
    while (engine._finishing or not engine.audio.empty()) and time.perf_counter() < deadline:
        time.sleep(0.1)
    time.sleep(1.5)
    latency.reset_session()
    return t0, list(recorder.events)


def utterance_texts(events) -> list[str]:
    """Tekst kolejnych wypowiedzi (zatwierdzone fragmenty + final) — do sprawdzania styków."""
    texts, current = [], ""
    for _t, kind, delta, _tentative in events:
        current += delta
        if kind == "final":
            texts.append(current.strip())
            current = ""
    if current.strip():
        texts.append(current.strip())
    return texts


def common_metrics(segments, events, t0, audio_len_s):
    """Dla każdego odcinka mowy: czas do 1. tekstu, do 1. pewnego tekstu i od końca mowy do final."""
    rows = []
    rel = [(t - t0, kind, delta, tentative) for t, kind, delta, tentative in events]
    for number, (start, end) in enumerate(segments, 1):
        next_start = segments[number][0] if number < len(segments) else audio_len_s + TAIL_S + 10
        first_any = next((t for t, k, d, tt in rel if k == "partial" and (d.strip() or tt.strip()) and t >= start), None)
        first_stable = next((t for t, k, d, tt in rel if k == "partial" and d.strip() and t >= start), None)
        final = next((t for t, k, d, tt in rel if k == "final" and t >= end and t < next_start + 8), None)
        if final is None:
            final = next((t for t, k, d, tt in rel if k == "final" and t >= end), None)
        rows.append({
            "odcinek": number, "start_s": round(start, 2), "koniec_s": round(end, 2),
            "pierwszy_tekst_ms": None if first_any is None else round((first_any - start) * 1000),
            "pierwszy_pewny_ms": None if first_stable is None else round((first_stable - start) * 1000),
            "final_po_koncu_ms": None if final is None else round((final - end) * 1000),
        })
    return rows


def resolve_cloud(key: str):
    """Model w chmurze tak, jak widzi go program: klucze, adresy API i własne modele zapisane
    w ustawieniach programu, potem zmienna środowiskowa / secrets.env."""
    try:
        from PySide6.QtCore import QSettings

        from dyktowanie_api_ui import ApiStore

        return ApiStore(QSettings("BAPO", "BAPO-dyktowanie")).resolve(key)
    except Exception:
        logging.getLogger("test").warning("Ustawienia programu niedostępne — klucz tylko z otoczenia", exc_info=True)
        from dyktowanie_api import resolve

        return resolve(key, {}, [])


# -- główny przebieg -----------------------------------------------------------------------------------------------------


def stats(values):
    values = sorted(v for v in values if v is not None)
    if not values:
        return "—"
    p90 = values[min(len(values) - 1, int(round(0.9 * (len(values) - 1))))]
    return f"{int(statistics.median(values))} / {p90} / {values[-1]}"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--plik", default=DEFAULT_FILE, help="nagranie do odtwarzania")
    parser.add_argument("--modele", default="small,medium,turbo,deepgram")
    parser.add_argument("--powtorzenia", type=int, default=2)
    parser.add_argument("--nazwa", default="", help="nazwa serii (domyślnie data_godzina)")
    parser.add_argument("--kod", default="", help="folder kopii (_kopia_przed_...) do nadpisania kodu — test starej wersji")
    parser.add_argument("--final-beam", type=int, default=0,
                        help="beam końcowego przebiegu FINAL na czas testu (domyślnie z ustawienia_asr.json)")
    parser.add_argument("--atrapa", action="store_true", help="atrapa modelu (sprawdzenie samego skryptu, bez GPU)")
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    os.environ.setdefault("PYTHONUTF8", "1")

    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
    name = args.nazwa or stamp
    out = PROJECT / "testy" / "wyniki_auto" / name
    out.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(out / "dyktowanie.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)

    code_dir = prepare_code(args.kod or None)
    sys.path.insert(0, str(code_dir))
    print(f"Kod: {code_dir}{'  (nadpisany z ' + args.kod + ')' if args.kod else ''}")
    print(f"Wyniki: {out}")

    print(f"Czytam plik: {args.plik}")
    audio = decode_audio(args.plik)
    duration = len(audio) / SAMPLE_RATE
    segments = speech_segments(audio)
    print(f"Długość {duration:.1f} s, odcinków mowy (przybliżenie z poziomu): {len(segments)}")

    from dyktowanie_latencja import tracker as latency
    from dyktowanie_gpu import monitor

    latency.configure(out / "latencja.csv")
    monitor.configure(out / "gpu.csv")
    import dyktowanie_asr
    from dyktowanie_asr import DictationEngine

    if args.atrapa:
        install_fake_model(dyktowanie_asr)

    recorder = Recorder()
    engine = DictationEngine(recorder)
    if args.final_beam:
        engine.settings.final_beam = args.final_beam
    final_beam = getattr(engine.settings, "final_beam", None)
    print(f"final_beam: {final_beam}")
    engine.start()

    plan = []
    common_rows = []
    transcripts = []
    wanted = [m.strip().lower() for m in args.modele.split(",") if m.strip()]
    try:
        for model_name in wanted:
            key = MODEL_KEYS.get(model_name) or (model_name if model_name.startswith("api:") else None)
            if key is None:
                print(f"! Nieznany model: {model_name} (dostępne: {', '.join(MODEL_KEYS)}, api:<dostawca>:<model>)")
                continue
            recorder.ready_event.clear()
            recorder.error = None
            if key.startswith("api:"):
                cloud = resolve_cloud(key)
                if cloud is None or cloud.needs_key:
                    print(f"! {model_name}: brak klucza API (w programie: „Klucze API…”, zmienna środowiskowa "
                          f"albo secrets.env) — pomijam")
                    continue
                engine.load(key, "auto", cloud)
            else:
                engine.load(key, "auto")
            print(f"\n=== {model_name}: ładowanie…")
            if not recorder.ready_event.wait(600) or recorder.error:
                print(f"! {model_name}: nie załadowano ({recorder.error}) — pomijam")
                continue
            time.sleep(1.0)
            info = engine.model_info.label if engine.model_info else model_name
            print(f"    gotowy: {info}")
            started = datetime.now()
            for run in range(1, args.powtorzenia + 1):
                begin = time.perf_counter()
                t0, events = replay(engine, recorder, latency, audio)
                rows = common_metrics(segments, events, t0, duration)
                for row in rows:
                    common_rows.append({"model": model_name, "powtorzenie": run, **row})
                transcripts.append(f"=== {model_name}, odtworzenie {run}\n" + "\n".join(utterance_texts(events)))
                finals = [r["final_po_koncu_ms"] for r in rows]
                print(f"    odtworzenie {run}/{args.powtorzenia}: {time.perf_counter() - begin:.0f} s, "
                      f"final po końcu (mediana): {stats(finals).split(' / ')[0]} ms")
                time.sleep(GAP_BETWEEN_S)
            plan.append({"model": model_name, "od": started.strftime("%H:%M:%S"),
                         "do": datetime.now().strftime("%H:%M:%S"), "powtorzenia": args.powtorzenia})
    except KeyboardInterrupt:
        print("\nPrzerwano.")
    finally:
        try:
            engine.shutdown()
        except Exception:
            pass
        gc.collect()

    (out / "plan.json").write_text(json.dumps({
        "plik": args.plik, "kod": args.kod or "bieżący", "data": datetime.now().strftime("%Y-%m-%d"),
        "final_beam": final_beam, "odcinki_mowy_s": [[round(s, 2), round(e, 2)] for s, e in segments], "serie": plan,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    if transcripts:
        (out / "teksty.txt").write_text("\n\n".join(transcripts) + "\n", encoding="utf-8")
    if common_rows:
        with open(out / "wyniki_wspolne.csv", "w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(common_rows[0]), delimiter=";")
            writer.writeheader()
            writer.writerows(common_rows)

    lines = ["", "PODSUMOWANIE (mediana / p90 / maks., w ms; czasy względem początku i końca mowy w nagraniu)",
             f"{'model':<16}{'odcinków':>9}  {'1. tekst':<22}{'1. pewny tekst':<22}{'final po końcu mowy':<22}"]
    for model_name in wanted:
        rows = [r for r in common_rows if r["model"] == model_name]
        if rows:
            lines.append(f"{model_name:<16}{len(rows):>9}  {stats([r['pierwszy_tekst_ms'] for r in rows]):<22}"
                         f"{stats([r['pierwszy_pewny_ms'] for r in rows]):<22}"
                         f"{stats([r['final_po_koncu_ms'] for r in rows]):<22}")
    text = "\n".join(lines)
    print(text)
    (out / "podsumowanie.txt").write_text(text, encoding="utf-8")

    # Pełna analiza wszystkich modeli (ta sama co dla baseline): tylko gdy skrypt analizy jest obok.
    analysis = PROJECT / "testy" / "analiza_testu.py"
    if plan and analysis.is_file():
        od = min(item["od"] for item in plan)[:5]
        do = max(item["do"] for item in plan)[:5]
        command = [sys.executable, str(analysis), "--od", od, "--do", do, "--data", datetime.now().strftime("%Y-%m-%d"),
                   "--folder", str(out)]
        try:
            result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", timeout=120)
            (out / "analiza.txt").write_text(result.stdout + result.stderr, encoding="utf-8")
            print(f"\nPełna analiza modeli: {out / 'analiza.txt'}")
        except Exception as exc:  # analiza jest dodatkiem — nie psuje testu
            print(f"(analiza_testu.py nie ruszyła: {exc})")
    print("\nGotowe. Powiedz Claude: gotowe, folder:", out)


# -- atrapa (sprawdzenie skryptu bez GPU) --------------------------------------------------------------------------------


def install_fake_model(module):
    from types import SimpleNamespace

    class FakeSegments:
        def __init__(self, audio):
            self.audio = audio

        def __iter__(self):
            seconds = len(self.audio) / SAMPLE_RATE
            words = [SimpleNamespace(start=0.0, end=min(seconds, 0.5), word=" test")]
            yield SimpleNamespace(text=" test", words=words, tokens=[1, 2, 3], seek=0, start=0.0, end=seconds,
                                  avg_logprob=-0.2, compression_ratio=1.1, no_speech_prob=0.01, temperature=0.0)

    class FakeModel:
        hf_tokenizer = None

        def transcribe(self, audio, **options):
            time.sleep(0.4)
            return FakeSegments(audio), SimpleNamespace(language="pl")

    def fake_load(key, device="auto", status=lambda text: None):
        info = module.ModelInfo(key, f"atrapa-{key}", "cpu", "int8", "")
        return FakeModel(), info

    module.load_whisper = fake_load


if __name__ == "__main__":
    main()
