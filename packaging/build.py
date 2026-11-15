"""Buduje wydanie programu dla systemu, na którym jest uruchomiony.

    python packaging/build.py            # aplikacja + instalator
    python packaging/build.py --no-installer

Windows: ``dist/BAPO-dyktowanie/`` oraz ``dist/BAPO-dyktowanie-<wersja>-Windows-Setup.exe``
         (instalator wymaga Inno Setup 6 — polecenie ``iscc``).
macOS:   ``dist/BAPO-dyktowanie.app``, ``…-macOS-<arch>.zip`` (dla aktualizacji)
         i ``…-macOS-<arch>.dmg`` (do pierwszej instalacji).

Na końcu uruchamia zbudowany program z ``--selftest`` i zapisuje sumy SHA-256.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
APP_NAME = "BAPO-dyktowanie"
IS_WIN = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"


def version() -> str:
    text = (ROOT / "dyktowanie_app.py").read_text(encoding="utf-8")
    return re.search(r'__version__ = "([^"]+)"', text).group(1)


def run(command: list[str], **kwargs) -> None:
    sys.stdout.write("» " + " ".join(str(part) for part in command) + "\n")
    sys.stdout.flush()
    subprocess.run(command, check=True, cwd=ROOT, **kwargs)


def build_app() -> Path:
    run([sys.executable, "-m", "PyInstaller", str(ROOT / "packaging" / f"{APP_NAME}.spec"),
         "--noconfirm", "--distpath", str(DIST), "--workpath", str(ROOT / "build")])
    return DIST / (f"{APP_NAME}.app" if IS_MAC else APP_NAME)


def executable(app: Path) -> Path:
    if IS_MAC:
        return app / "Contents" / "MacOS" / APP_NAME
    return app / (f"{APP_NAME}.exe" if IS_WIN else APP_NAME)


def selftest(app: Path) -> None:
    """Zbudowany program musi sam potwierdzić, że ma komplet bibliotek i zasobów."""
    data = tempfile.mkdtemp(prefix="bapo-selftest-")
    env = dict(os.environ, BAPO_DYKTOWANIE_DATA=data, QT_QPA_PLATFORM="offscreen")
    result = subprocess.run([str(executable(app)), "--selftest"], env=env, timeout=300)
    report = Path(data) / "selftest.txt"
    text = report.read_text(encoding="utf-8").strip() if report.is_file() else "(brak raportu)"
    sys.stdout.write(f"Selftest: {text}\n")
    if result.returncode != 0 or not text.startswith("OK"):
        raise SystemExit(f"Selftest zbudowanej aplikacji nie przeszedł (kod {result.returncode}).")
    shutil.rmtree(data, ignore_errors=True)


def find_iscc() -> str | None:
    found = shutil.which("iscc") or shutil.which("ISCC")
    if found:
        return found
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"),
                 os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs")):
        candidate = Path(base or "") / "Inno Setup 6" / "ISCC.exe"
        if candidate.is_file():
            return str(candidate)
    return None


def windows_installer(ver: str) -> Path | None:
    iscc = find_iscc()
    if iscc is None:
        sys.stdout.write("Pominięto instalator: nie znaleziono Inno Setup 6 (iscc).\n")
        return None
    run([iscc, f"/DAppVersion={ver}", str(ROOT / "packaging" / "windows" / "installer.iss")])
    return DIST / f"{APP_NAME}-{ver}-Windows-Setup.exe"


def macos_packages(app: Path, ver: str) -> list[Path]:
    arch = "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "x64"
    identity = os.environ.get("MACOS_SIGN_IDENTITY")
    if not identity:
        # Bez certyfikatu Apple: podpis „ad hoc”, wymagany do uruchomienia na Apple Silicon.
        run(["codesign", "--force", "--deep", "--sign", "-", str(app)])
    archive = DIST / f"{APP_NAME}-{ver}-macOS-{arch}.zip"
    archive.unlink(missing_ok=True)
    run(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(app), str(archive)])
    image = DIST / f"{APP_NAME}-{ver}-macOS-{arch}.dmg"
    image.unlink(missing_ok=True)
    staging = Path(tempfile.mkdtemp(prefix="bapo-dmg-"))
    try:
        run(["ditto", str(app), str(staging / app.name)])
        os.symlink("/Applications", staging / "Applications")
        run(["hdiutil", "create", "-volname", APP_NAME, "-srcfolder", str(staging),
             "-ov", "-format", "UDZO", str(image)])
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return [archive, image]


def write_checksums(files: list[Path]) -> None:
    for path in files:
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        line = f"{digest.hexdigest()}  {path.name}\n"
        path.with_name(path.name + ".sha256").write_text(line, encoding="utf-8")
        sys.stdout.write(line)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--no-installer", action="store_true", help="tylko aplikacja, bez instalatora")
    args = parser.parse_args()
    ver = version()
    sys.stdout.write(f"Buduję {APP_NAME} {ver} ({sys.platform}, {platform.machine()})\n")
    app = build_app()
    selftest(app)
    packages: list[Path] = []
    if not args.no_installer:
        if IS_WIN:
            installer = windows_installer(ver)
            if installer is not None:
                packages.append(installer)
        elif IS_MAC:
            packages += macos_packages(app, ver)
    write_checksums(packages)
    sys.stdout.write(f"Gotowe: {app}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
