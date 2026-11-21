"""Punkt startowy MyDict — transkrypcja mowy na żywo do aktywnego okna."""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent))

from dyktowanie_app import APP_NAME, FROZEN, IS_MAC, __version__, log_dir  # noqa: E402


def _log_directory() -> Path:
    return log_dir()


# Zmiana tej wartości jednorazowo czyści stare dzienniki i CSV pomiaru przy następnym starcie.
LOG_VERSION = "2026-10-01-diagnostyka-asr-gpu"


def _reset_old_logs(directory: Path):
    """Raz po każdej zmianie ``LOG_VERSION`` usuwa dzienniki i CSV z pomiarami,
    żeby nowe pomiary nie mieszały się z danymi sprzed zmiany."""
    marker = directory / "wersja_pomiaru.txt"
    try:
        if marker.exists() and marker.read_text(encoding="utf-8").strip() == LOG_VERSION:
            return
        for pattern in ("dyktowanie.log*", "konsola.log*", "latencja*.csv", "przebiegi*.csv", "gpu*.csv"):
            for path in directory.glob(pattern):
                try:
                    path.unlink()
                except OSError:
                    try:
                        path.write_text("", encoding="utf-8")  # plik zajęty — przynajmniej opróżnij
                    except OSError:
                        pass
        marker.write_text(LOG_VERSION, encoding="utf-8")
    except OSError:
        pass


def _setup_logging() -> Path:
    directory = _log_directory()
    _reset_old_logs(directory)
    log_path = directory / "dyktowanie.log"
    handler = RotatingFileHandler(log_path, maxBytes=5_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    # pythonw.exe nie ma konsoli: biblioteki piszące na stdout/stderr (np. pasek
    # postępu pobierania modelu) kończyłyby się błędem. Kierujemy je do dziennika.
    if sys.stdout is None or sys.stderr is None:
        stream = open(log_path.with_name("konsola.log"), "a", encoding="utf-8", buffering=1)
        if sys.stdout is None:
            sys.stdout = stream
        if sys.stderr is None:
            sys.stderr = stream
    # Awarie natywne (DLL, CUDA, Qt) nie zostawiają śladu w Pythonie — faulthandler zapisze je w konsola.log.
    try:
        import faulthandler

        faulthandler.enable(file=sys.stderr)
    except Exception:
        pass
    return log_path


def _show_error(message: str):
    if sys.platform.startswith("win"):
        import ctypes

        ctypes.windll.user32.MessageBoxW(0, message, APP_NAME, 0x10)
        return
    if IS_MAC:
        import subprocess

        script = 'display alert "{}" message "{}" as critical'.format(
            APP_NAME, message.replace("\\", "\\\\").replace('"', '\\"')
        )
        try:
            subprocess.run(["/usr/bin/osascript", "-e", script], timeout=120, check=False)
            return
        except (OSError, subprocess.SubprocessError):
            pass
    sys.stderr.write(message + "\n")


def _use_system_certificates():
    """Połączenia HTTPS (aktualizacje, modele, API) ufają certyfikatom z magazynu
    systemu — działa też w sieciach firmowych i w aplikacji spakowanej na macOS."""
    try:
        import truststore

        truststore.inject_into_ssl()
    except Exception:
        logging.getLogger("dyktowanie").info("truststore niedostępny — domyślne certyfikaty Pythona")


def selftest() -> int:
    """Szybki test instalacji (``--selftest``): czy komplet bibliotek i zasobów jest
    na miejscu. Używany przy budowaniu wydań — nie otwiera okna i nie pobiera modeli."""
    import importlib

    problems = []
    for name in ("numpy", "sounddevice", "websockets", "ctranslate2", "faster_whisper",
                 "PySide6.QtWidgets", "PySide6.QtSvg", "dyktowanie_ui", "dyktowanie_aktualizacje",
                 "dyktowanie_zgloszenia_ui"):
        try:
            importlib.import_module(name)
        except Exception as exc:  # brak modułu albo biblioteki systemowej
            problems.append(f"{name}: {exc!r}")
    try:
        from dyktowanie_api import CLOUD_MODELS
        from dyktowanie_app import resource_dir
        from dyktowanie_core import make_vad
        from dyktowanie_platforma import foreground_window, typing_permission_granted

        if not CLOUD_MODELS:
            problems.append("dostawcy.json: brak modeli")
        if not (resource_dir() / "zasoby" / "logo.svg").is_file():
            problems.append("brak zasoby/logo.svg")
        vad = type(make_vad()).__name__
        if vad != "SileroVAD":
            problems.append(f"wykrywanie mowy: {vad} zamiast SileroVAD (brak modelu VAD w pakiecie)")
        foreground_window()
        typing_permission_granted()
        if os.environ.get("MYDICT_SELFTEST_SILNIK"):
            # Wydanie musi zawierać drugi silnik (whisper.cpp) i musi on dać się uruchomić.
            import subprocess

            import dyktowanie_whispercpp as whispercpp

            if not whispercpp.available():
                problems.append("brak drugiego silnika (whispercpp/whisper-server)")
            else:
                result = subprocess.run(
                    [str(whispercpp.server_executable()), "--help"], capture_output=True, timeout=60,
                    creationflags=0x08000000 if sys.platform.startswith("win") else 0,
                )
                if b"--model" not in result.stdout + result.stderr:
                    problems.append(f"drugi silnik nie startuje (kod {result.returncode})")
    except Exception as exc:
        problems.append(f"test składników: {exc!r}")
    report = "\n".join(problems) if problems else f"OK {APP_NAME} {__version__}"
    logging.getLogger("dyktowanie").info("Selftest: %s", report)
    try:
        (_log_directory() / "selftest.txt").write_text(report + "\n", encoding="utf-8")
        sys.stdout.write(report + "\n")
    except Exception:
        pass
    return 1 if problems else 0


def main():
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    os.environ.setdefault("PYTHONUTF8", "1")
    log_path = _setup_logging()
    logging.getLogger("dyktowanie").info(
        "Start %s %s (%s, %s)", APP_NAME, __version__, sys.platform, "aplikacja" if FROZEN else "źródła"
    )
    _use_system_certificates()
    if "--selftest" in sys.argv:
        return selftest()

    def excepthook(kind, value, traceback):
        logging.getLogger("dyktowanie").critical("Nieobsłużony błąd", exc_info=(kind, value, traceback))
        try:
            import traceback as tb

            from dyktowanie_zgloszenia import record_crash

            record_crash("".join(tb.format_exception(kind, value, traceback)))
        except Exception:
            pass
        _show_error(f"Wystąpił nieoczekiwany błąd:\n\n{value}\n\nSzczegóły: {log_path}\n\n"
                    "Przy następnym uruchomieniu program zaproponuje wysłanie zgłoszenia.")

    sys.excepthook = excepthook
    try:
        from dyktowanie_latencja import tracker

        tracker.configure(log_path.with_name("latencja.csv"))
        from dyktowanie_gpu import monitor

        monitor.configure(log_path.with_name("gpu.csv"))
    except Exception:
        logging.getLogger("dyktowanie").warning("Pomiar latencji niedostępny", exc_info=True)
    try:
        if "--przygotuj" in sys.argv:
            # Wywołanie z instalatora: pobierz składniki (karta graficzna, model) i zakończ.
            from dyktowanie_przygotowanie import run_setup

            return run_setup(sys.argv)
        from dyktowanie_ui import run
    except ImportError as exc:
        logging.getLogger("dyktowanie").critical("Brak bibliotek", exc_info=True)
        hint = (
            "Zainstaluj program ponownie." if FROZEN
            else "Zainstaluj biblioteki: pip install -r requirements.txt"
        )
        _show_error(f"Brakuje bibliotek programu.\n{hint}\n\nSzczegóły: {exc}")
        return 1
    return run(sys.argv)


if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()   # wymagane w aplikacji spakowanej, gdy biblioteka uruchamia procesy
    sys.exit(main())
