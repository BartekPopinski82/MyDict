"""Test gotowego pakietu tak, jak dostanie go użytkownik: instalacja → autotest → odinstalowanie.

    python packaging/test_instalatora.py dist/MyDict-2.1.0-Windows-Setup.exe
    python packaging/test_instalatora.py dist/MyDict-2.1.0-macOS-arm64.dmg

Windows: cicha instalacja do katalogu tymczasowego (bez pobierania modelu — zmienna
``MYDICT_TEST_INSTALACJI``), ``--selftest`` zainstalowanego programu, sprawdzenie
plików licencji, ciche odinstalowanie i kontrola, że nic nie zostało.
macOS: zamontowanie DMG, skopiowanie aplikacji, ``--selftest``, kontrola podpisu.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

APP_NAME = "MyDict"


def _selftest(executable: Path) -> None:
    data = tempfile.mkdtemp(prefix="mydict-test-dane-")
    env = dict(os.environ, MYDICT_DATA=data, QT_QPA_PLATFORM="offscreen")
    result = subprocess.run([str(executable), "--selftest"], env=env, timeout=300)
    report = Path(data) / "selftest.txt"
    text = report.read_text(encoding="utf-8").strip() if report.is_file() else "(brak raportu)"
    print(f"Selftest zainstalowanego programu: {text}")
    shutil.rmtree(data, ignore_errors=True)
    if result.returncode != 0 or not text.startswith("OK"):
        raise SystemExit("Zainstalowany program nie przeszedł autotestu.")


def test_windows(installer: Path) -> None:
    target = Path(tempfile.mkdtemp(prefix="mydict-test-instalacja-")) / APP_NAME
    env = dict(os.environ, MYDICT_TEST_INSTALACJI="1")
    subprocess.run([str(installer), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
                    f"/DIR={target}", "/TASKS="], env=env, check=True, timeout=600)
    for name in (f"{APP_NAME}.exe", "LICENSE.txt", "THIRD-PARTY-NOTICES.txt", "unins000.exe"):
        if not (target / name).is_file():
            raise SystemExit(f"Po instalacji brakuje pliku: {name}")
    _selftest(target / f"{APP_NAME}.exe")
    subprocess.run([str(target / "unins000.exe"), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
                   check=True, timeout=300)
    # Deinstalator działa z kopii w katalogu tymczasowym i usuwa unins000.exe dopiero po swoim
    # zakończeniu — czekamy, aż folder programu opustoszeje.
    left: list = []
    for _ in range(60):
        left = [p.relative_to(target) for p in target.rglob("*") if p.is_file()] if target.exists() else []
        if not left:
            break
        time.sleep(1)
    if left:
        raise SystemExit(f"Po odinstalowaniu zostały pliki: {left[:10]}")
    print("Instalacja i odinstalowanie: OK")


def test_macos(image: Path) -> None:
    mount = Path(tempfile.mkdtemp(prefix="mydict-test-dmg-"))
    copy = Path(tempfile.mkdtemp(prefix="mydict-test-app-"))
    subprocess.run(["hdiutil", "attach", "-nobrowse", "-readonly", "-mountpoint", str(mount), str(image)],
                   check=True)
    try:
        for name in (f"{APP_NAME}.app", "LICENSE.txt", "THIRD-PARTY-NOTICES.txt", "Applications"):
            if not (mount / name).exists():
                raise SystemExit(f"W obrazie DMG brakuje: {name}")
        subprocess.run(["ditto", str(mount / f"{APP_NAME}.app"), str(copy / f"{APP_NAME}.app")], check=True)
    finally:
        subprocess.run(["hdiutil", "detach", str(mount)], check=False)
    app = copy / f"{APP_NAME}.app"
    subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], check=True)
    _selftest(app / "Contents" / "MacOS" / APP_NAME)
    shutil.rmtree(copy, ignore_errors=True)
    print("Obraz DMG: OK")


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        sys.stderr.write(__doc__)
        return 2
    package = Path(argv[1]).resolve()
    if package.suffix.lower() == ".exe":
        test_windows(package)
    elif package.suffix.lower() == ".dmg":
        test_macos(package)
    else:
        sys.stderr.write("Obsługiwane pakiety: .exe (Windows), .dmg (macOS)\n")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
