"""Tworzy THIRD-PARTY-NOTICES.txt — licencje składników dołączanych do programu.

    python packaging/licencje.py            # zapis do THIRD-PARTY-NOTICES.txt
    python packaging/licencje.py --sprawdz  # kod 1, gdy plik w repozytorium jest nieaktualny

Uruchamiać w środowisku budowania (``requirements-build.txt``). Lista bibliotek
Pythona powstaje z ``requirements.txt`` i ich zależności (bez narzędzi budowania),
z tekstami licencji z metadanych pakietów. Składniki spoza PyPI (Python, PyInstaller,
whisper.cpp, modele, biblioteki NVIDIA) są opisane w ``OTHER_COMPONENTS``.
Teksty licencji, których pakiety nie dołączają (LGPL-3.0), leżą w ``packaging/licencje/``.
"""

from __future__ import annotations

import argparse
import importlib.metadata as metadata
import sys
from pathlib import Path

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "THIRD-PARTY-NOTICES.txt"
EXTRA_TEXTS = ROOT / "packaging" / "licencje"
LICENSE_HINTS = ("LICENSE", "LICENCE", "COPYING", "NOTICE", "AUTHORS")
SEPARATOR = "=" * 78

HEADER = """MyDict — informacje o składnikach osób trzecich / Third-party notices

MyDict zawiera lub pobiera poniższe składniki. Każdy z nich podlega własnej licencji,
przytoczonej niżej; licencja MyDict (LICENSE.txt) ich nie zmienia ani nie ogranicza.

Biblioteki Qt i PySide6 (LGPL-3.0) oraz FFmpeg w PyAV (LGPL) są dołączone jako osobne,
dynamicznie ładowane pliki w folderze programu (_internal). Można je zastąpić własną,
zgodną wersją. Kod źródłowy: https://download.qt.io/official_releases/QtForPython/ ,
https://code.qt.io/ , https://github.com/PyAV-Org/PyAV , https://ffmpeg.org/download.html .
Na prośbę przekażemy kopię kodu źródłowego tych bibliotek w wersjach użytych w programie
(kontakt: https://github.com/BartekPopinski82/MyDict/issues).
"""

# Składniki spoza bibliotek Pythona: (nazwa, licencja, opis, plik z tekstem licencji lub "").
OTHER_COMPONENTS = [
    ("Python", "PSF-2.0", "Środowisko uruchomieniowe dołączone przez PyInstaller. https://www.python.org/",
     ""),
    ("PyInstaller (bootloader)", "GPL-2.0-or-later z wyjątkiem dla bootloadera",
     "Program uruchamiający; wyjątek pozwala rozpowszechniać aplikację na dowolnej licencji. "
     "https://pyinstaller.org/", ""),
    ("whisper.cpp / ggml", "MIT", "Drugi silnik rozpoznawania mowy (Vulkan/Metal). "
     "https://github.com/ggml-org/whisper.cpp", "whispercpp/LICENSE-whisper.cpp.txt"),
    ("Modele Whisper (OpenAI)", "MIT",
     "Modele rozpoznawania mowy, pobierane przez program z Hugging Face: Systran/faster-whisper-*, "
     "mobiuslabsgmbh/faster-whisper-large-v3-turbo, ggerganov/whisper.cpp (ggml-*). "
     "https://github.com/openai/whisper", ""),
    ("Silero VAD", "MIT", "Model wykrywania mowy (w pakiecie faster-whisper). "
     "https://github.com/snakers4/silero-vad", ""),
    ("Biblioteki NVIDIA CUDA (cuBLAS, cuDNN)", "NVIDIA Software License Agreement",
     "NIE są rozpowszechniane z programem. Na komputerach z kartą NVIDIA program pobiera je na żądanie "
     "użytkownika bezpośrednio z PyPI (pakiety nvidia-*); ich użycie podlega licencji NVIDIA: "
     "https://docs.nvidia.com/cuda/eula/ i https://docs.nvidia.com/deeplearning/cudnn/latest/reference/eula.html",
     ""),
    ("Inno Setup", "Inno Setup License", "Instalator Windows. Copyright (C) 1997-2026 Jordan Russell, "
     "Martijn Laan. https://jrsoftware.org/", ""),
]


def _environment() -> dict:
    env = default_environment()
    env["extra"] = ""
    return env


def runtime_distributions(requirements: Path = ROOT / "requirements.txt") -> list:
    """Pakiety programu (z zależnościami) zainstalowane w tym środowisku."""
    env = _environment()
    queue = []
    for line in requirements.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line and not line.startswith("-"):
            queue.append(Requirement(line))
    seen: dict = {}
    while queue:
        requirement = queue.pop()
        if requirement.marker is not None and not requirement.marker.evaluate(env):
            continue
        key = canonicalize_name(requirement.name)
        if key in seen:
            continue
        try:
            dist = metadata.distribution(requirement.name)
        except metadata.PackageNotFoundError:
            sys.stderr.write(f"UWAGA: brak pakietu {requirement.name} w tym środowisku\n")
            continue
        seen[key] = dist
        queue += [Requirement(item) for item in dist.requires or ()]
    return sorted(seen.values(), key=lambda d: canonicalize_name(d.metadata["Name"]))


def license_name(dist) -> str:
    meta = dist.metadata
    expression = meta.get("License-Expression")
    if expression:
        return expression
    text = (meta.get("License") or "").strip()
    if text and "\n" not in text and len(text) < 80:
        return text
    classifiers = [c.split("::")[-1].strip() for c in meta.get_all("Classifier") or () if c.startswith("License ::")]
    return "; ".join(classifiers) or "zob. tekst licencji"


def license_texts(dist) -> list:
    texts = []
    for item in dist.files or ():
        name = item.name.upper()
        if any(hint in name for hint in LICENSE_HINTS) and not name.endswith((".PY", ".PYC")):
            try:
                texts.append((str(item), Path(dist.locate_file(item)).read_text(encoding="utf-8", errors="replace")))
            except OSError:
                pass
    if not texts:
        body = (dist.metadata.get("License") or "").strip()
        if "\n" in body:
            texts.append(("metadane pakietu", body))
    return texts


def render() -> str:
    parts = [HEADER, SEPARATOR, "SPIS", SEPARATOR]
    dists = runtime_distributions()
    for dist in dists:
        parts.append(f"- {dist.metadata['Name']} {dist.version} — {license_name(dist)}")
    for name, lic, _desc, _file in OTHER_COMPONENTS:
        parts.append(f"- {name} — {lic}")
    for name, lic, desc, file in OTHER_COMPONENTS:
        parts += ["", SEPARATOR, f"{name} — {lic}", SEPARATOR, desc]
        if file and (ROOT / file).is_file():
            parts += ["", (ROOT / file).read_text(encoding="utf-8", errors="replace").strip()]
    for dist in dists:
        meta = dist.metadata
        parts += ["", SEPARATOR, f"{meta['Name']} {dist.version} — {license_name(dist)}",
                  meta.get("Home-page") or next((u.split(",", 1)[1].strip() for u in meta.get_all("Project-URL") or ()), ""),
                  SEPARATOR]
        texts = license_texts(dist)
        for source, text in texts:
            parts += [f"--- {source} ---", text.strip(), ""]
        if not texts:
            parts.append("(pakiet nie dołącza tekstu licencji; obowiązuje licencja wskazana wyżej)")
    extra = sorted(EXTRA_TEXTS.glob("*.txt")) if EXTRA_TEXTS.is_dir() else []
    for path in extra:
        parts += ["", SEPARATOR, f"Pełny tekst licencji: {path.stem}", SEPARATOR,
                  path.read_text(encoding="utf-8", errors="replace").strip()]
    if not any(p.stem.upper().startswith("LGPL-3") for p in extra):
        sys.stderr.write("UWAGA: brak packaging/licencje/LGPL-3.0.txt (pełny tekst LGPL dla Qt/PySide6).\n")
        parts += ["", SEPARATOR, "GNU LGPL 3.0", SEPARATOR, "https://www.gnu.org/licenses/lgpl-3.0.txt"]
    return "\n".join(parts).replace("\r\n", "\n") + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sprawdz", action="store_true")
    args = parser.parse_args()
    text = render()
    if args.sprawdz:
        current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.is_file() else ""
        if current != text:
            sys.stderr.write("THIRD-PARTY-NOTICES.txt jest nieaktualny — uruchom python packaging/licencje.py\n")
            return 1
        return 0
    OUTPUT.write_text(text, encoding="utf-8", newline="\n")
    sys.stdout.write(f"Zapisano {OUTPUT.name} ({len(text) // 1024} KB)\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
