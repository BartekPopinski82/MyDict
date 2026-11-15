"""Tożsamość programu i jego katalogi — jedno miejsce dla wersji, nazw i ścieżek.

Program działa w dwóch postaciach: ze źródeł (``python BAPO_Dyktowanie.py``) oraz
jako zainstalowana aplikacja zbudowana PyInstallerem. Ten moduł ukrywa różnicę:
reszta kodu pyta tylko o „plik zasobu” albo „katalog danych użytkownika”.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


APP_NAME = "BAPO-dyktowanie"
APP_ID = "pl.bapo.dyktowanie"
ORGANIZATION = "BAPO"
__version__ = "2.0.0"

# Repozytorium GitHub, z którego program pobiera aktualizacje (wydania „Releases”).
GITHUB_REPO = "BartekPopinski82/BAPO-dyktowanie"

IS_WINDOWS = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"
FROZEN = bool(getattr(sys, "frozen", False))


def resource_dir() -> Path:
    """Katalog z plikami dołączonymi do programu (ikony, dostawcy.json)."""
    bundled = getattr(sys, "_MEIPASS", None)
    if bundled:
        return Path(bundled)
    return Path(__file__).resolve().parent


def data_dir() -> Path:
    """Katalog danych użytkownika: dziennik, pomiary, pobrane składniki."""
    override = os.environ.get("BAPO_DYKTOWANIE_DATA")
    if override:
        path = Path(override)
    elif IS_WINDOWS:
        path = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / APP_NAME
    elif IS_MAC:
        path = Path.home() / "Library" / "Application Support" / APP_NAME
    else:
        path = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def log_dir() -> Path:
    """Katalog dzienników (na macOS tam, gdzie szuka ich aplikacja Konsola)."""
    if IS_MAC and not os.environ.get("BAPO_DYKTOWANIE_DATA"):
        path = Path.home() / "Library" / "Logs" / APP_NAME
        path.mkdir(parents=True, exist_ok=True)
        return path
    return data_dir()


def config_file(name: str) -> Path:
    """Plik konfiguracyjny: wersja użytkownika (w katalogu danych) ma pierwszeństwo
    przed dołączoną do programu. Dzięki temu własne zmiany przetrwają aktualizację."""
    try:
        own = data_dir() / name
        if own.is_file():
            return own
    except OSError:
        pass
    return resource_dir() / name


def app_bundle() -> Path | None:
    """Ścieżka pakietu ``.app`` na macOS (tylko w zainstalowanej aplikacji)."""
    if not (FROZEN and IS_MAC):
        return None
    for parent in Path(sys.executable).resolve().parents:
        if parent.suffix == ".app":
            return parent
    return None
