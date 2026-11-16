"""Zgłoszenia problemów i awarii — zapisywane jako pliki w prywatnym repozytorium GitHub.

Przebieg: użytkownik (sam albo po awarii) zgadza się wysłać zgłoszenie → program
składa raport tekstowy (wersja, system, opis, koniec dziennika) → szyfruje go kluczem
publicznym autora (``REPORTS_PUBLIC_KEY``) → zapisuje jako plik w repozytorium
``REPORTS_REPO`` przez GitHub API, w folderze tej instalacji:

    zgloszenia/<identyfikator instalacji>/<czas UTC>-<rodzaj>.mdz

Autor odczytuje je u siebie: ``python packaging/zgloszenia.py <klon repozytorium>``
(powstają zwykłe pliki .txt). Dziennik może zawierać fragmenty dyktowanego tekstu,
dlatego raport jest szyfrowany — token zapisu jest dołączony do programu i da się go
wydobyć, ale bez klucza prywatnego autora nie da się nic odczytać.

Awarie: nieobsłużony błąd Pythona zapisuje ``record_crash``; awarię natywną (DLL, CUDA,
Qt) rozpoznaje następne uruchomienie po zapisie ``faulthandler`` w ``konsola.log``.
Nic nie jest wysyłane bez zgody użytkownika. Moduł nie zależy od Qt.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import platform
import re
import sys
import urllib.error
import urllib.request
import uuid
import zlib
from datetime import datetime, timezone
from pathlib import Path

from dyktowanie_app import (
    APP_NAME,
    FROZEN,
    REPORTS_PUBLIC_KEY,
    REPORTS_REPO,
    __version__,
    data_dir,
    log_dir,
    resource_dir,
)
from dyktowanie_krypto import seal


log = logging.getLogger("dyktowanie.zgloszenia")
TOKEN_FILE = "zgloszenia.token"          # dołączany przy budowaniu (packaging/MyDict.spec)
TOKEN_ENV = "MYDICT_ZGLOSZENIA_TOKEN"
API_URL = "https://api.github.com/repos/{repo}/contents/{path}"
TIMEOUT = 30.0
LOG_TAIL = 200_000          # bajtów końca dziennika w raporcie
CONSOLE_TAIL = 30_000
CRASH_LIMIT = 50_000
FATAL_MARKERS = ("Fatal Python error", "Windows fatal exception", "Segmentation fault")


class ReportError(Exception):
    """Wysłanie zgłoszenia nie powiodło się; komunikat nadaje się dla użytkownika."""


# ---------------------------------------------------------------------------
# Konfiguracja
# ---------------------------------------------------------------------------


def token() -> str:
    value = os.environ.get(TOKEN_ENV, "").strip()
    if value:
        return value
    try:
        return (resource_dir() / TOKEN_FILE).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def can_send() -> bool:
    """Czy ta kopia programu potrafi wysłać zgłoszenie (bez tokenu: tylko zapis do pliku)."""
    return bool(token() and REPORTS_REPO and REPORTS_PUBLIC_KEY)


def _state_dir() -> Path:
    path = data_dir() / "zgloszenia"
    path.mkdir(parents=True, exist_ok=True)
    return path


def install_id() -> str:
    """Losowy, stały identyfikator tej instalacji — nazwa folderu zgłoszeń.
    Nie zawiera nazwy użytkownika ani komputera."""
    path = data_dir() / "identyfikator.txt"
    try:
        value = path.read_text(encoding="utf-8").strip()
        if re.fullmatch(r"[0-9a-f]{12}", value):
            return value
    except OSError:
        pass
    value = uuid.uuid4().hex[:12]
    try:
        path.write_text(value + "\n", encoding="utf-8")
    except OSError:
        pass
    return value


# ---------------------------------------------------------------------------
# Awarie
# ---------------------------------------------------------------------------


def _crash_file() -> Path:
    return _state_dir() / "awaria.txt"


def _session_file() -> Path:
    return _state_dir() / "sesja.txt"


def record_crash(text: str) -> None:
    """Zapisuje opis nieobsłużonego błędu do zgłoszenia przy następnej okazji."""
    try:
        path = _crash_file()
        previous = path.read_text(encoding="utf-8") if path.is_file() else ""
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        path.write_text((previous + f"\n[{stamp}]\n{text}\n")[-CRASH_LIMIT:], encoding="utf-8")
    except OSError:
        pass


def session_started() -> None:
    """Na starcie programu: wykrywa awarię natywną poprzedniej sesji (zapis
    ``faulthandler`` w ``konsola.log`` po niedokończonej sesji) i zaczyna nową."""
    console = log_dir() / "konsola.log"
    size = console.stat().st_size if console.is_file() else 0
    marker = _session_file()
    try:
        if marker.is_file():
            offset = int(marker.read_text(encoding="utf-8").strip() or 0)
            if 0 <= offset <= size:
                with open(console, "rb") as handle:
                    handle.seek(offset)
                    fresh = handle.read().decode("utf-8", errors="replace")
                if any(item in fresh for item in FATAL_MARKERS):
                    record_crash("Awaria natywna (zapis z konsola.log):\n" + fresh[-CRASH_LIMIT // 2:])
        marker.write_text(str(size), encoding="utf-8")
    except (OSError, ValueError):
        log.debug("Znacznik sesji niedostępny", exc_info=True)


def session_ended() -> None:
    try:
        _session_file().unlink(missing_ok=True)
    except OSError:
        pass


def pending_crash() -> str:
    try:
        return _crash_file().read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def clear_crash() -> None:
    try:
        _crash_file().unlink(missing_ok=True)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Raport
# ---------------------------------------------------------------------------


def _tail(path: Path, limit: int) -> str:
    try:
        with open(path, "rb") as handle:
            handle.seek(max(0, path.stat().st_size - limit))
            return handle.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def build_report(description: str, contact: str = "", crash: str = "", include_log: bool = True) -> str:
    """Treść zgłoszenia jako zwykły tekst (to, co autor przeczyta po odszyfrowaniu)."""
    lines = [
        f"Program: {APP_NAME} {__version__} ({'aplikacja' if FROZEN else 'źródła'})",
        f"System: {platform.platform()} | {platform.machine()} | Python {platform.python_version()}",
        f"Instalacja: {install_id()}",
        f"Czas: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        f"Kontakt: {contact.strip() or '(nie podano)'}",
        "",
        "== Opis użytkownika ==",
        description.strip() or "(brak)",
    ]
    if crash:
        lines += ["", "== Awaria ==", crash]
    if include_log:
        directory = log_dir()
        lines += ["", "== Dziennik (koniec) ==", _tail(directory / "dyktowanie.log", LOG_TAIL),
                  "", "== Konsola (koniec) ==", _tail(directory / "konsola.log", CONSOLE_TAIL)]
    return "\n".join(lines) + "\n"


def encrypt(report: str, public_key_hex: str = "") -> bytes:
    key = bytes.fromhex(public_key_hex or REPORTS_PUBLIC_KEY)
    return seal(key, zlib.compress(report.encode("utf-8"), 9))


def report_path(kind: str, when: datetime | None = None) -> str:
    when = when or datetime.now(timezone.utc)
    safe_kind = re.sub(r"[^a-z0-9-]", "", kind.lower()) or "zgloszenie"
    return f"zgloszenia/{install_id()}/{when.strftime('%Y-%m-%d_%H%M%S')}-{safe_kind}.mdz"


def _put(url: str, body: bytes, headers: dict) -> int:
    request = urllib.request.Request(url, data=body, headers=headers, method="PUT")
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return response.status


def send(report: str, kind: str = "zgloszenie", *, put=_put) -> str:
    """Szyfruje i zapisuje zgłoszenie w repozytorium; zwraca ścieżkę pliku."""
    secret = token()
    if not can_send():
        raise ReportError("Ta kopia programu nie ma skonfigurowanego wysyłania zgłoszeń.")
    path = report_path(kind)
    body = json.dumps({
        "message": f"Zgłoszenie {kind} ({__version__})",
        "content": base64.b64encode(encrypt(report)).decode("ascii"),
    }).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {secret}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": f"{APP_NAME}/{__version__}",
        "Content-Type": "application/json",
    }
    try:
        status = put(API_URL.format(repo=REPORTS_REPO, path=path), body, headers)
    except urllib.error.HTTPError as exc:
        log.warning("Zgłoszenie odrzucone: HTTP %s", exc.code)
        raise ReportError(f"Serwer odrzucił zgłoszenie (błąd {exc.code}). Zapisz je do pliku i wyślij inaczej.") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise ReportError(f"Brak połączenia z serwerem: {exc}") from exc
    if status not in (200, 201):
        raise ReportError(f"Serwer odpowiedział nieoczekiwanie (kod {status}).")
    log.info("Wysłano zgłoszenie %s", path)
    return path


def save_to_file(report: str, directory: Path | None = None) -> Path:
    """Zapis zgłoszenia jako zwykły plik tekstowy — gdy wysyłanie jest niedostępne."""
    directory = directory or _state_dir()
    target = directory / f"zgloszenie-{datetime.now().strftime('%Y-%m-%d_%H%M%S')}.txt"
    target.write_text(report, encoding="utf-8")
    return target


__all__ = [
    "ReportError", "build_report", "can_send", "clear_crash", "encrypt", "install_id",
    "pending_crash", "record_crash", "report_path", "save_to_file", "send", "session_ended",
    "session_started",
]
