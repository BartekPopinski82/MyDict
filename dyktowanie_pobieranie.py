"""Pobieranie plików przez HTTPS: z postępem, wznawianiem i kontrolą sumy SHA-256.

Wspólne dla aktualizacji programu i dla pobieranych na żądanie bibliotek GPU.
Tylko biblioteka standardowa.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

from dyktowanie_app import APP_NAME, __version__


log = logging.getLogger("dyktowanie.pobieranie")
USER_AGENT = f"{APP_NAME}/{__version__}"
TIMEOUT = 30.0
CHUNK = 256 * 1024

Progress = Callable[[int, int], None]   # (pobrane bajty, rozmiar całkowity albo 0)


class DownloadError(Exception):
    """Pobieranie nie powiodło się (sieć, serwer, zła suma kontrolna)."""

    def __init__(self, message: str, *, status: int | None = None):
        super().__init__(message)
        self.status = status   # kod HTTP, jeśli błąd zgłosił serwer


class Cancelled(DownloadError):
    """Pobieranie przerwane przez użytkownika."""


def _request(url: str, headers: dict | None = None) -> urllib.request.Request:
    if not url.lower().startswith("https://"):
        raise DownloadError(f"Odrzucono adres bez HTTPS: {url}")
    merged = {"User-Agent": USER_AGENT}
    merged.update(headers or {})
    return urllib.request.Request(url, headers=merged)


def fetch_json(url: str, headers: dict | None = None, opener=urllib.request.urlopen) -> dict:
    """Pobiera i dekoduje dokument JSON."""
    try:
        with opener(_request(url, headers), timeout=TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise DownloadError(f"Serwer odpowiedział błędem {exc.code} ({url})", status=exc.code) from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise DownloadError(f"Brak połączenia z serwerem: {exc}") from exc


def fetch_text(url: str, opener=urllib.request.urlopen, limit: int = 1_000_000) -> str:
    try:
        with opener(_request(url), timeout=TIMEOUT) as response:
            return response.read(limit).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raise DownloadError(f"Serwer odpowiedział błędem {exc.code} ({url})") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise DownloadError(f"Brak połączenia z serwerem: {exc}") from exc


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def download(
    url: str,
    destination: Path,
    *,
    sha256: str = "",
    progress: Progress | None = None,
    cancel: threading.Event | None = None,
    opener=urllib.request.urlopen,
) -> Path:
    """Pobiera ``url`` do ``destination``.

    Dane trafiają najpierw do pliku ``.part``; przerwane pobieranie jest wznawiane
    od miejsca przerwania (nagłówek ``Range``). Plik docelowy powstaje dopiero po
    sprawdzeniu sumy SHA-256 (jeśli podana), więc nigdy nie jest niekompletny.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    wanted = sha256.strip().lower()
    if destination.is_file() and wanted and file_sha256(destination) == wanted:
        return destination
    part = destination.with_name(destination.name + ".part")
    offset = part.stat().st_size if part.is_file() else 0
    headers = {"Range": f"bytes={offset}-"} if offset else {}
    try:
        response = opener(_request(url, headers), timeout=TIMEOUT)
    except urllib.error.HTTPError as exc:
        if exc.code == 416 and offset:      # plik .part jest już kompletny albo nieaktualny
            part.unlink(missing_ok=True)
            return download(url, destination, sha256=sha256, progress=progress, cancel=cancel, opener=opener)
        raise DownloadError(f"Serwer odpowiedział błędem {exc.code}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise DownloadError(f"Brak połączenia z serwerem: {exc}") from exc

    with response:
        resumed = offset > 0 and getattr(response, "status", 200) == 206
        if not resumed:
            offset = 0
        length = int(response.headers.get("Content-Length") or 0)
        total = offset + length if length else 0
        done = offset
        try:
            with open(part, "ab" if resumed else "wb") as handle:
                while True:
                    if cancel is not None and cancel.is_set():
                        raise Cancelled("Pobieranie przerwane")
                    block = response.read(CHUNK)
                    if not block:
                        break
                    handle.write(block)
                    done += len(block)
                    if progress is not None:
                        progress(done, total)
        except Cancelled:
            raise
        except OSError as exc:
            raise DownloadError(f"Pobieranie przerwane: {exc}") from exc
    if total and done < total:
        raise DownloadError("Połączenie zostało zerwane przed końcem pobierania")
    if wanted:
        actual = file_sha256(part)
        if actual != wanted:
            part.unlink(missing_ok=True)
            raise DownloadError("Pobrany plik jest uszkodzony (niezgodna suma kontrolna)")
    part.replace(destination)
    return destination
