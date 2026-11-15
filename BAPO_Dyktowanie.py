"""Punkt startowy BAPO-dyktowanie — transkrypcja mowy na żywo do aktywnego okna."""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


def _log_directory() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    path = Path(base) / "BAPO-dyktowanie"
    path.mkdir(parents=True, exist_ok=True)
    return path


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

        faulthandler.enable(file=sys.stderr if sys.stderr is not None else open(log_path.with_name("konsola.log"), "a", encoding="utf-8"))
    except Exception:
        pass
    return log_path


def _show_error(message: str):
    if sys.platform.startswith("win"):
        import ctypes

        ctypes.windll.user32.MessageBoxW(0, message, "BAPO-dyktowanie", 0x10)
    else:
        print(message, file=sys.stderr)


def main():
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    os.environ.setdefault("PYTHONUTF8", "1")
    log_path = _setup_logging()
    sys.path.insert(0, str(Path(__file__).resolve().parent))

    def excepthook(kind, value, traceback):
        logging.getLogger("dyktowanie").critical("Nieobsłużony błąd", exc_info=(kind, value, traceback))
        _show_error(f"Wystąpił nieoczekiwany błąd:\n\n{value}\n\nSzczegóły: {log_path}")

    sys.excepthook = excepthook
    try:
        from dyktowanie_latencja import tracker

        tracker.configure(log_path.with_name("latencja.csv"))
        from dyktowanie_gpu import monitor

        monitor.configure(log_path.with_name("gpu.csv"))
    except Exception:
        logging.getLogger("dyktowanie").warning("Pomiar latencji niedostępny", exc_info=True)
    try:
        from dyktowanie_ui import run
    except ImportError as exc:
        _show_error(
            "Brakuje bibliotek programu (PySide6 / numpy).\n"
            "Uruchom „Uruchom BAPO-dyktowanie.bat”, który korzysta ze środowiska .venv BAPO-transcript.\n\n"
            f"Szczegóły: {exc}"
        )
        return 1
    return run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
