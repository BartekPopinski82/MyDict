"""Aktualizacje programu z wydań GitHub (Releases).

Przebieg: zapytanie o najnowsze wydanie → porównanie wersji → pobranie pliku
dla tego systemu → kontrola sumy SHA-256 → instalacja i ponowne uruchomienie.

* Windows: pobierany jest instalator; uruchamiamy go w trybie cichym, a sam
  program się zamyka. Instalator jest „na użytkownika”, więc nie pyta o uprawnienia.
* macOS: pobierany jest plik ZIP z aplikacją; po zamknięciu programu mały skrypt
  podmienia pakiet ``.app`` i uruchamia nową wersję.

Suma kontrolna pochodzi z pliku ``SHA256SUMS.txt`` podpisanego kluczem autora
(``SHA256SUMS.txt.sig``, klucz publiczny ``RELEASE_PUBLIC_KEY``). Wydanie bez
prawidłowego podpisu nie jest instalowane — także wtedy, gdy ktoś przejmie konto
GitHub i podmieni pliki razem z sumami. Bez zaszytego klucza (kopie robocze)
wystarcza suma z pola ``digest`` albo z ``SHA256SUMS.txt``.
Moduł nie zależy od Qt — interfejs jest w ``dyktowanie_ui``.
"""

from __future__ import annotations

import logging
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path

from dyktowanie_app import (
    APP_NAME,
    FROZEN,
    GITHUB_REPO,
    IS_MAC,
    IS_WINDOWS,
    RELEASE_PUBLIC_KEY,
    __version__,
    app_bundle,
    data_dir,
)
from dyktowanie_krypto import verify_hex
from dyktowanie_pobieranie import DownloadError, Progress, download, fetch_json, fetch_text


log = logging.getLogger("dyktowanie.aktualizacje")
API_URL = "https://api.github.com/repos/{repo}/releases/latest"
RELEASES_PAGE = "https://github.com/{repo}/releases/latest"
CHECKSUMS_NAME = "SHA256SUMS.txt"
SIGNATURE_NAME = CHECKSUMS_NAME + ".sig"
CHECK_INTERVAL_HOURS = 20
# Dozwolone adresy pobierania — pliki wydań GitHub.
TRUSTED_PREFIXES = ("https://github.com/", "https://objects.githubusercontent.com/")


class UpdateError(Exception):
    """Aktualizacja nie powiodła się; komunikat nadaje się do pokazania użytkownikowi."""


@dataclass(frozen=True)
class Asset:
    name: str
    url: str
    size: int = 0
    sha256: str = ""


@dataclass(frozen=True)
class Release:
    version: str
    notes: str
    page_url: str
    assets: tuple = field(default_factory=tuple)

    def asset(self, name: str) -> Asset | None:
        return next((item for item in self.assets if item.name == name), None)


# ---------------------------------------------------------------------------
# Wersje
# ---------------------------------------------------------------------------


def parse_version(text: str) -> tuple:
    """„v2.1.0” → (2, 1, 0). Dopiski (np. „-beta1”) są pomijane; śmieci dają ()."""
    match = re.match(r"\s*[vV]?(\d+(?:\.\d+)*)", text or "")
    if not match:
        return ()
    return tuple(int(part) for part in match.group(1).split("."))


def is_newer(candidate: str, current: str = __version__) -> bool:
    new, old = parse_version(candidate), parse_version(current)
    if not new or not old:
        return False
    width = max(len(new), len(old))
    return new + (0,) * (width - len(new)) > old + (0,) * (width - len(old))


# ---------------------------------------------------------------------------
# Wydania
# ---------------------------------------------------------------------------


def platform_asset_suffix(system: str | None = None, machine: str | None = None) -> str:
    """Końcówka nazwy pliku wydania przeznaczonego dla tego komputera."""
    system = system or sys.platform
    machine = (machine or platform.machine()).lower()
    if system.startswith("win"):
        return "-Windows-Setup.exe"
    if system == "darwin":
        return "-macOS-arm64.zip" if machine in ("arm64", "aarch64") else "-macOS-x64.zip"
    return ""


def release_from_json(data: dict) -> Release:
    assets = []
    for item in data.get("assets") or ():
        digest = str(item.get("digest") or "")
        assets.append(Asset(
            name=str(item.get("name") or ""),
            url=str(item.get("browser_download_url") or ""),
            size=int(item.get("size") or 0),
            sha256=digest.split(":", 1)[1].lower() if digest.startswith("sha256:") else "",
        ))
    return Release(
        version=str(data.get("tag_name") or "").lstrip("vV"),
        notes=str(data.get("body") or "").strip(),
        page_url=str(data.get("html_url") or RELEASES_PAGE.format(repo=GITHUB_REPO)),
        assets=tuple(assets),
    )


def fetch_latest(repo: str = GITHUB_REPO, fetch=fetch_json) -> Release:
    try:
        data = fetch(
            API_URL.format(repo=repo),
            {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"},
        )
    except DownloadError as exc:
        raise UpdateError(f"Nie udało się sprawdzić aktualizacji. {exc}") from exc
    return release_from_json(data)


def check(repo: str = GITHUB_REPO, current: str = __version__, fetch=fetch_json) -> Release | None:
    """Zwraca nowsze wydanie albo None, gdy program jest aktualny."""
    try:
        release = fetch_latest(repo, fetch)
    except UpdateError as exc:
        cause = exc.__cause__
        if isinstance(cause, DownloadError) and cause.status == 404:
            return None   # repozytorium nie ma jeszcze żadnego wydania
        raise
    return release if is_newer(release.version, current) else None


def pick_asset(release: Release, suffix: str | None = None) -> Asset | None:
    suffix = platform_asset_suffix() if suffix is None else suffix
    if not suffix:
        return None
    return next((item for item in release.assets if item.name.endswith(suffix)), None)


def parse_checksums(text: str) -> dict:
    """Plik w formacie ``sha256sum``: „<suma>  <nazwa pliku>” w każdej linii."""
    sums = {}
    for line in text.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2 and re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
            sums[parts[1].lstrip("*").strip()] = parts[0].lower()
    return sums


def expected_sha256(release: Release, asset: Asset, fetch_text_fn=fetch_text,
                    public_key: str | None = None) -> str:
    """Suma, którą musi mieć pobrany plik. Z kluczem publicznym — tylko z podpisanego
    ``SHA256SUMS.txt``; brak albo zły podpis to ``UpdateError``."""
    key = RELEASE_PUBLIC_KEY if public_key is None else public_key
    if key:
        return _signed_sha256(release, asset, fetch_text_fn, key)
    if asset.sha256:
        return asset.sha256
    sums = release.asset(CHECKSUMS_NAME)
    if sums is None:
        return ""
    try:
        return parse_checksums(fetch_text_fn(sums.url)).get(asset.name, "")
    except DownloadError as exc:
        raise UpdateError(f"Nie udało się pobrać sum kontrolnych. {exc}") from exc


def _signed_sha256(release: Release, asset: Asset, fetch_text_fn, key: str) -> str:
    sums, signature = release.asset(CHECKSUMS_NAME), release.asset(SIGNATURE_NAME)
    if sums is None or signature is None:
        raise UpdateError("Wydanie nie jest podpisane — dla bezpieczeństwa nie instaluję go automatycznie.")
    for item in (sums, signature):
        if not item.url.startswith(TRUSTED_PREFIXES):
            raise UpdateError("Wydanie wskazuje plik spoza GitHuba — pominięto.")
    try:
        text = fetch_text_fn(sums.url)
        signature_hex = fetch_text_fn(signature.url)
    except DownloadError as exc:
        raise UpdateError(f"Nie udało się pobrać sum kontrolnych. {exc}") from exc
    if not verify_hex(key, text.encode("utf-8"), signature_hex):
        log.warning("Nieprawidłowy podpis wydania %s", release.version)
        raise UpdateError("Podpis wydania jest nieprawidłowy — aktualizacja odrzucona. "
                          "Pobierz program ręcznie ze strony wydań.")
    value = parse_checksums(text).get(asset.name, "")
    if not value:
        raise UpdateError("Podpisane sumy kontrolne nie obejmują pliku dla Twojego systemu.")
    if asset.sha256 and asset.sha256 != value:
        raise UpdateError("Suma pliku na GitHubie nie zgadza się z podpisaną — aktualizacja odrzucona.")
    return value


# ---------------------------------------------------------------------------
# Pobieranie i instalacja
# ---------------------------------------------------------------------------


def updates_dir() -> Path:
    return data_dir() / "aktualizacje"


def can_self_install() -> tuple[bool, str]:
    """Czy ta kopia programu potrafi sama się zaktualizować (i dlaczego nie)."""
    if not FROZEN:
        return False, "Program działa ze źródeł — zaktualizuj go przez „git pull”."
    if IS_WINDOWS:
        return True, ""
    if IS_MAC:
        bundle = app_bundle()
        if bundle is None:
            return False, "Nie znaleziono pakietu aplikacji."
        if "/AppTranslocation/" in str(bundle):
            return False, "Przenieś aplikację do folderu Programy, aby włączyć aktualizacje."
        if not os.access(bundle.parent, os.W_OK):
            return False, "Brak uprawnień do zapisu w folderze aplikacji."
        return True, ""
    return False, "Automatyczna instalacja nie jest dostępna na tym systemie."


def fetch_update(
    release: Release,
    progress: Progress | None = None,
    cancel: threading.Event | None = None,
    *,
    fetch_file=download,
    fetch_text_fn=fetch_text,
) -> Path:
    """Pobiera plik aktualizacji dla tego systemu i sprawdza jego sumę kontrolną."""
    asset = pick_asset(release)
    if asset is None:
        raise UpdateError("To wydanie nie zawiera pliku dla Twojego systemu.")
    if not asset.url.startswith(TRUSTED_PREFIXES):
        raise UpdateError("Wydanie wskazuje plik spoza GitHuba — pominięto.")
    checksum = expected_sha256(release, asset, fetch_text_fn)
    if not checksum:
        raise UpdateError("Wydanie nie ma sumy kontrolnej — dla bezpieczeństwa nie instaluję go automatycznie.")
    directory = updates_dir()
    for stale in directory.glob("*") if directory.is_dir() else ():
        if stale.name not in (asset.name, asset.name + ".part"):
            if stale.is_dir():
                shutil.rmtree(stale, ignore_errors=True)
            else:
                stale.unlink(missing_ok=True)
    try:
        return fetch_file(asset.url, directory / asset.name, sha256=checksum, progress=progress, cancel=cancel)
    except DownloadError as exc:
        if exc.__class__.__name__ == "Cancelled":
            raise
        raise UpdateError(f"Nie udało się pobrać aktualizacji. {exc}") from exc


def _detached(command: list[str], **kwargs):
    if IS_WINDOWS:
        flags = 0x00000008 | 0x00000200 | 0x01000000   # DETACHED | NEW_PROCESS_GROUP | BREAKAWAY_FROM_JOB
        try:
            return subprocess.Popen(command, creationflags=flags, close_fds=True, **kwargs)
        except OSError:
            return subprocess.Popen(command, creationflags=0x00000008 | 0x00000200, close_fds=True, **kwargs)
    return subprocess.Popen(command, start_new_session=True, close_fds=True, **kwargs)


MAC_SWAP_SCRIPT = """#!/bin/sh
# Podmienia pakiet aplikacji po zamknięciu programu i uruchamia nową wersję.
PID="$1"; NEW="$2"; APP="$3"; WORK="$4"
i=0
while kill -0 "$PID" 2>/dev/null && [ $i -lt 300 ]; do sleep 0.2; i=$((i+1)); done
OLD="$APP.poprzednia"
rm -rf "$OLD"
if mv "$APP" "$OLD"; then
  if mv "$NEW" "$APP"; then
    rm -rf "$OLD"
    xattr -dr com.apple.quarantine "$APP" 2>/dev/null
  else
    mv "$OLD" "$APP"
  fi
fi
open "$APP"
rm -rf "$WORK"
"""


def _install_macos(archive: Path) -> None:
    bundle = app_bundle()
    if bundle is None:
        raise UpdateError("Nie znaleziono pakietu aplikacji.")
    work = updates_dir() / "nowa"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    result = subprocess.run(["/usr/bin/ditto", "-x", "-k", str(archive), str(work)], capture_output=True)
    if result.returncode != 0:
        raise UpdateError("Nie udało się rozpakować aktualizacji.")
    apps = sorted(work.glob("*.app"))
    if not apps:
        raise UpdateError("Archiwum aktualizacji nie zawiera aplikacji.")
    script = updates_dir() / "podmien.sh"
    script.write_text(MAC_SWAP_SCRIPT, encoding="utf-8")
    script.chmod(0o700)
    _detached(["/bin/sh", str(script), str(os.getpid()), str(apps[0]), str(bundle), str(work)])


def install(package: Path) -> None:
    """Uruchamia instalację pobranej aktualizacji. Po powrocie program powinien
    od razu się zamknąć — instalator/skrypt czeka na jego zakończenie."""
    ok, reason = can_self_install()
    if not ok:
        raise UpdateError(reason)
    log.info("Instalacja aktualizacji: %s", package.name)
    try:
        if IS_WINDOWS:
            _detached([
                str(package), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
                "/CLOSEAPPLICATIONS", "/NOCANCEL", "/AKTUALIZACJA",
            ])
        else:
            _install_macos(package)
    except OSError as exc:
        raise UpdateError(f"Nie udało się uruchomić instalacji: {exc}") from exc


def cleanup():
    """Po udanym starcie nowej wersji usuwa pobrane pliki aktualizacji."""
    shutil.rmtree(updates_dir(), ignore_errors=True)


def releases_page(repo: str = GITHUB_REPO) -> str:
    return RELEASES_PAGE.format(repo=repo)


__all__ = [
    "APP_NAME", "Asset", "Release", "UpdateError", "can_self_install", "check", "cleanup",
    "fetch_latest", "fetch_update", "install", "is_newer", "parse_checksums", "parse_version",
    "pick_asset", "platform_asset_suffix", "release_from_json", "releases_page",
]
