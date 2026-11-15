"""Buduje drugi silnik rozpoznawania (whisper.cpp) z obsługą karty graficznej.

    python packaging/build_whispercpp.py

Windows: Vulkan (karty AMD, Intel, NVIDIA). macOS: Metal. Wynik trafia do folderu
``whispercpp/`` w katalogu projektu, skąd zabiera go ``packaging/build.py``.

Wymagania:
  * git, CMake 3.20+
  * Windows: Visual Studio 2022 (C++), Vulkan SDK (zmienna VULKAN_SDK)
  * macOS: Xcode Command Line Tools

Wersja whisper.cpp jest przypięta (``TAG``) — zmiana wymaga ponownego sprawdzenia
dyktowania, bo program polega na formacie odpowiedzi serwera i czasach słów (DTW).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TAG = "b5454"   # wydanie z 2026-10-06 — z tą wersją sprawdzono dyktowanie
REPO = "https://github.com/ggml-org/whisper.cpp"
SOURCE = ROOT / "build" / "whisper.cpp"
OUTPUT = ROOT / "whispercpp"
IS_WIN = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"


def run(command: list[str], **kwargs) -> None:
    sys.stdout.write("» " + " ".join(str(part) for part in command) + "\n")
    sys.stdout.flush()
    subprocess.run(command, check=True, **kwargs)


def fetch_source() -> None:
    if (SOURCE / ".git").is_dir():
        run(["git", "-C", str(SOURCE), "fetch", "--depth", "1", "origin", "tag", TAG])
        run(["git", "-C", str(SOURCE), "checkout", "--force", TAG])
        return
    SOURCE.parent.mkdir(parents=True, exist_ok=True)
    run(["git", "clone", "--depth", "1", "--branch", TAG, REPO, str(SOURCE)])


def configure_and_build() -> Path:
    build = SOURCE / "build-mydict"
    options = [
        "-DCMAKE_BUILD_TYPE=Release",
        "-DBUILD_SHARED_LIBS=OFF",          # jeden plik wykonywalny, bez kompletu bibliotek obok
        "-DWHISPER_BUILD_SERVER=ON",
        "-DWHISPER_BUILD_EXAMPLES=ON",
        "-DWHISPER_BUILD_TESTS=OFF",
        "-DWHISPER_SDL2=OFF",
        "-DGGML_NATIVE=OFF",                # program ma działać na każdym procesorze, nie tylko na maszynie budującej
    ]
    if IS_WIN:
        if not os.environ.get("VULKAN_SDK"):
            raise SystemExit("Brak Vulkan SDK (zmienna VULKAN_SDK). Zainstaluj: https://vulkan.lunarg.com/")
        options += ["-DGGML_VULKAN=ON", "-A", "x64"]
    elif IS_MAC:
        options += ["-DGGML_METAL=ON", "-DGGML_METAL_EMBED_LIBRARY=ON", "-DCMAKE_OSX_DEPLOYMENT_TARGET=12.0"]
    run(["cmake", "-S", str(SOURCE), "-B", str(build), *options])
    run(["cmake", "--build", str(build), "--config", "Release", "--target", "whisper-server", "-j"])
    return build


def collect(build: Path) -> None:
    name = "whisper-server.exe" if IS_WIN else "whisper-server"
    found = [path for path in build.rglob(name) if path.is_file()]
    if not found:
        raise SystemExit(f"Nie znaleziono {name} po kompilacji.")
    binary = max(found, key=lambda path: path.stat().st_mtime)
    shutil.rmtree(OUTPUT, ignore_errors=True)
    OUTPUT.mkdir(parents=True)
    shutil.copy2(binary, OUTPUT / name)
    for library in binary.parent.glob("*.dll"):     # na wypadek bibliotek dołączanych dynamicznie
        shutil.copy2(library, OUTPUT / library.name)
    shutil.copy2(SOURCE / "LICENSE", OUTPUT / "LICENSE-whisper.cpp.txt")
    (OUTPUT / "WERSJA.txt").write_text(
        f"whisper.cpp {TAG} ({'Vulkan' if IS_WIN else 'Metal' if IS_MAC else 'CPU'})\n{REPO}\n", encoding="utf-8"
    )
    sys.stdout.write(f"Gotowe: {OUTPUT}\n")
    for path in sorted(OUTPUT.iterdir()):
        sys.stdout.write(f"  {path.name}  {path.stat().st_size / 1e6:.1f} MB\n")


def main() -> int:
    fetch_source()
    collect(configure_and_build())
    return 0


if __name__ == "__main__":
    sys.exit(main())
