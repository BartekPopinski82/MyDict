"""Biblioteki NVIDIA (cuBLAS, cuDNN) pobierane na żądanie.

Model Whisper działa na karcie graficznej NVIDIA tylko wtedy, gdy CTranslate2
znajdzie biblioteki cuBLAS i cuDNN. Ważą razem ok. 1 GB, więc nie są częścią
instalatora: program działa od razu na procesorze, a posiadacz karty NVIDIA
pobiera je jednym kliknięciem. Pliki pochodzą z oficjalnych paczek NVIDIA
w PyPI; każda jest sprawdzana sumą SHA-256 podaną przez PyPI.
"""

from __future__ import annotations

import json
import logging
import shutil
import threading
import zipfile
from pathlib import Path

from dyktowanie_app import IS_WINDOWS, data_dir
from dyktowanie_pobieranie import Cancelled, DownloadError, Progress, download, fetch_json


log = logging.getLogger("dyktowanie.gpu_pakiet")

# Wersje sprawdzone z CTranslate2 4.x (CUDA 12, cuDNN 9).
PACKAGES = (
    ("nvidia-cublas-cu12", "12.8.4.1"),
    ("nvidia-cudnn-cu12", "9.6.0.74"),
)
WHEEL_TAG = "win_amd64"
APPROX_SIZE = "ok. 1 GB"
MARKER = "pakiet.json"


def supported() -> bool:
    """Pakiet ma sens tylko na Windows (na macOS CTranslate2 nie używa CUDA)."""
    return IS_WINDOWS


def pack_dir() -> Path:
    return data_dir() / "gpu"


def _wanted() -> dict:
    return {name: version for name, version in PACKAGES}


def installed(directory: Path | None = None) -> bool:
    directory = directory or pack_dir()
    try:
        marker = json.loads((directory / MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return marker.get("packages") == _wanted() and any(directory.glob("cudnn*.dll"))


def library_dirs() -> list[Path]:
    """Katalogi z bibliotekami CUDA do wskazania systemowi (puste, gdy pakietu nie ma)."""
    directory = pack_dir()
    return [directory] if installed(directory) else []


def _wheel(name: str, version: str, fetch=fetch_json) -> dict:
    meta = fetch(f"https://pypi.org/pypi/{name}/{version}/json")
    for item in meta.get("urls") or ():
        if item.get("packagetype") == "bdist_wheel" and WHEEL_TAG in str(item.get("filename")):
            return item
    raise DownloadError(f"Brak paczki {name} {version} dla Windows")


def _extract_dlls(wheel: Path, target: Path) -> int:
    count = 0
    with zipfile.ZipFile(wheel) as archive:
        for member in archive.infolist():
            name = Path(member.filename)
            if member.is_dir() or name.suffix.lower() != ".dll" or name.parent.name != "bin":
                continue
            with archive.open(member) as source, open(target / name.name, "wb") as output:
                shutil.copyfileobj(source, output, 1024 * 1024)
            count += 1
    return count


def install(
    progress: Progress | None = None,
    cancel: threading.Event | None = None,
    *,
    directory: Path | None = None,
    fetch=fetch_json,
    fetch_file=download,
) -> Path:
    """Pobiera i rozpakowuje biblioteki. Przerwane pobieranie można wznowić."""
    directory = directory or pack_dir()
    work = directory / "pobieranie"
    work.mkdir(parents=True, exist_ok=True)
    wheels = [_wheel(name, version, fetch) for name, version in PACKAGES]
    total = sum(int(item.get("size") or 0) for item in wheels)
    # Paczki i rozpakowane biblioteki przez chwilę leżą obok siebie — potrzeba ok. 2,5× rozmiaru.
    free = shutil.disk_usage(directory).free
    if total and free < total * 2.5:
        raise DownloadError(
            f"Za mało miejsca na dysku: potrzeba ok. {total * 2.5 / 1024 ** 3:.1f} GB, "
            f"wolne {free / 1024 ** 3:.1f} GB."
        )
    finished = 0
    (directory / MARKER).unlink(missing_ok=True)
    for item in wheels:
        base = finished

        def report(done: int, _size: int, base=base):
            if progress is not None:
                progress(base + done, total)

        path = fetch_file(
            item["url"], work / item["filename"],
            sha256=str((item.get("digests") or {}).get("sha256") or ""),
            progress=report, cancel=cancel,
        )
        if cancel is not None and cancel.is_set():
            raise Cancelled("Pobieranie przerwane")
        extracted = _extract_dlls(path, directory)
        log.info("Pakiet GPU: %s — %d bibliotek", item["filename"], extracted)
        if not extracted:
            raise DownloadError(f"Paczka {item['filename']} nie zawiera bibliotek")
        finished += int(item.get("size") or 0)
    shutil.rmtree(work, ignore_errors=True)
    (directory / MARKER).write_text(json.dumps({"packages": _wanted()}), encoding="utf-8")
    return directory


def remove(directory: Path | None = None):
    shutil.rmtree(directory or pack_dir(), ignore_errors=True)
