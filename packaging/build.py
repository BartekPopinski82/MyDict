"""Buduje wydanie programu dla systemu, na którym jest uruchomiony.

    python packaging/build.py            # aplikacja + instalator
    python packaging/build.py --no-installer

Windows: ``dist/MyDict/`` oraz ``dist/MyDict-<wersja>-Windows-Setup.exe``
         (instalator wymaga Inno Setup 6 — polecenie ``iscc``).
macOS:   ``dist/MyDict.app``, ``…-macOS-<arch>.zip`` (dla aktualizacji)
         i ``…-macOS-<arch>.dmg`` (do pierwszej instalacji).

Na końcu uruchamia zbudowany program z ``--selftest`` i zapisuje sumy SHA-256.

Podpis kodu Windows: gdy ustawiona jest zmienna ``MYDICT_SIGN_CMD`` — polecenie
podpisu z ``{plik}`` w miejscu ścieżki (np. ``signtool sign /fd SHA256 … {plik}``) —
podpisywane są: MyDict.exe, drugi silnik, instalator i deinstalator. Po podpisaniu
budowanie sprawdza podpisy i przerywa, gdy któryś jest nieprawidłowy.
Dostawca certyfikatu jest dowolny; konfiguracja dla Azure — ``.github/workflows/wydanie.yml``.
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

from wykluczenia import GPL_BINARY_MARKERS

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
APP_NAME = "MyDict"
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
    # Licencje składników tego systemu (lista bibliotek różni się między Windows i macOS).
    run([sys.executable, str(ROOT / "packaging" / "licencje.py")])
    run([sys.executable, "-m", "PyInstaller", str(ROOT / "packaging" / f"{APP_NAME}.spec"),
         "--noconfirm", "--distpath", str(DIST), "--workpath", str(ROOT / "build")])
    app = DIST / (f"{APP_NAME}.app" if IS_MAC else APP_NAME)
    check_no_gpl(app)
    return app


def check_no_gpl(app: Path) -> None:
    """Program jest zamkniętym freeware — biblioteka GPL w pakiecie przerywa budowanie."""
    found = sorted(str(path.relative_to(app)) for path in app.rglob("*")
                   if path.is_file() and any(marker in path.name.lower() for marker in GPL_BINARY_MARKERS))
    if found:
        raise SystemExit("Biblioteki na licencji GPL w zbudowanym programie:\n  " + "\n  ".join(found)
                         + "\nUsuń pakiet, który je przynosi (packaging/wykluczenia.py).")


def executable(app: Path) -> Path:
    if IS_MAC:
        return app / "Contents" / "MacOS" / APP_NAME
    return app / (f"{APP_NAME}.exe" if IS_WIN else APP_NAME)


def selftest(app: Path) -> None:
    """Zbudowany program musi sam potwierdzić, że ma komplet bibliotek i zasobów."""
    data = tempfile.mkdtemp(prefix="mydict-selftest-")
    env = dict(os.environ, MYDICT_DATA=data, QT_QPA_PLATFORM="offscreen")
    if (ROOT / "whispercpp").is_dir():
        env["MYDICT_SELFTEST_SILNIK"] = "1"   # zbudowana aplikacja musi zawierać działający drugi silnik
    result = subprocess.run([str(executable(app)), "--selftest"], env=env, timeout=300)
    report = Path(data) / "selftest.txt"
    text = report.read_text(encoding="utf-8").strip() if report.is_file() else "(brak raportu)"
    sys.stdout.write(f"Selftest: {text}\n")
    if result.returncode != 0 or not text.startswith("OK"):
        raise SystemExit(f"Selftest zbudowanej aplikacji nie przeszedł (kod {result.returncode}).")
    shutil.rmtree(data, ignore_errors=True)


def sign_command() -> str:
    """Szablon polecenia podpisu kodu Windows (pusty = budowanie bez podpisu)."""
    template = os.environ.get("MYDICT_SIGN_CMD", "").strip()
    if template and "{plik}" not in template:
        raise SystemExit("MYDICT_SIGN_CMD musi zawierać {plik} w miejscu podpisywanego pliku.")
    return template if IS_WIN else ""


def files_to_sign(app: Path) -> list[Path]:
    """Własne pliki wykonywalne programu: MyDict.exe i drugi silnik (whisper.cpp)."""
    files = [executable(app)]
    for folder in app.rglob("whispercpp"):
        if folder.is_dir():
            files += sorted(p for p in folder.iterdir() if p.suffix.lower() in (".exe", ".dll"))
    return files


def sign_files(files: list[Path], template: str) -> None:
    for path in files:
        command = template.replace("{plik}", f'"{path}"')
        sys.stdout.write(f"» podpis: {path.name}\n")
        sys.stdout.flush()
        subprocess.run(command, check=True, cwd=ROOT, shell=True)


def verify_signatures(files: list[Path]) -> None:
    """Każdy plik musi mieć prawidłowy podpis Authenticode (sprawdza Windows)."""
    listing = ",".join("'" + str(p).replace("'", "''") + "'" for p in files)
    script = (f"$bad = @({listing}) | ForEach-Object {{ Get-AuthenticodeSignature -LiteralPath $_ }} "
              "| Where-Object Status -ne 'Valid'; "
              "$bad | ForEach-Object { Write-Output \"$($_.Status): $($_.Path)\" }; exit $bad.Count")
    result = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit("Nieprawidłowe podpisy:\n" + result.stdout)
    sys.stdout.write(f"Podpisy prawidłowe ({len(files)} plików).\n")


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


def windows_installer(ver: str, sign: str = "") -> Path | None:
    iscc = find_iscc()
    if iscc is None:
        sys.stdout.write("Pominięto instalator: nie znaleziono Inno Setup 6 (iscc).\n")
        return None
    command = [iscc, f"/DAppVersion={ver}"]
    if sign:
        # Inno podpisuje instalator i deinstalator; $f = ścieżka w cudzysłowie, $q = cudzysłów.
        command += ["/DPodpis", "/Smydict=" + sign.replace('"', "$q").replace("{plik}", "$f")]
    run(command + [str(ROOT / "packaging" / "windows" / "installer.iss")])
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
    staging = Path(tempfile.mkdtemp(prefix="mydict-dmg-"))
    try:
        run(["ditto", str(app), str(staging / app.name)])
        os.symlink("/Applications", staging / "Applications")
        for name in ("LICENSE.txt", "THIRD-PARTY-NOTICES.txt"):
            shutil.copy2(ROOT / name, staging / name)
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
    parser.add_argument("--require-whispercpp", action="store_true",
                        help="przerwij, gdy brakuje drugiego silnika (wydania publiczne)")
    parser.add_argument("--require-signature", action="store_true",
                        help="Windows: przerwij, gdy podpis kodu nie jest skonfigurowany")
    args = parser.parse_args()
    ver = version()
    sys.stdout.write(f"Buduję {APP_NAME} {ver} ({sys.platform}, {platform.machine()})\n")
    engine = ROOT / "whispercpp" / ("whisper-server.exe" if IS_WIN else "whisper-server")
    if not engine.is_file():
        message = ("Brak drugiego silnika (whispercpp/). Karty AMD/Intel i Mac będą liczyć na procesorze. "
                   "Zbuduj go: python packaging/build_whispercpp.py")
        if args.require_whispercpp:
            raise SystemExit(message)
        sys.stdout.write("UWAGA: " + message + "\n")
    sign = sign_command()
    if args.require_signature and IS_WIN and not sign:
        raise SystemExit("Brak MYDICT_SIGN_CMD — wydanie wymaga podpisu kodu.")
    app = build_app()
    if sign:
        signed = files_to_sign(app)
        sign_files(signed, sign)
        verify_signatures(signed)
    selftest(app)
    packages: list[Path] = []
    if not args.no_installer:
        if IS_WIN:
            installer = windows_installer(ver, sign)
            if installer is not None:
                if sign:
                    verify_signatures([installer])
                packages.append(installer)
        elif IS_MAC:
            packages += macos_packages(app, ver)
    write_checksums(packages)
    sys.stdout.write(f"Gotowe: {app}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
