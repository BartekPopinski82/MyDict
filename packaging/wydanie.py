"""Pomocnik wydań: wersja, opis zmian, podpis wydania i klucze.

    python packaging/wydanie.py sprawdz v2.0.0          # kod 1, gdy znacznik ≠ wersja w kodzie
    python packaging/wydanie.py notatki v2.0.0          # sekcja „## 2.0.0” z CHANGELOG.md
    python packaging/wydanie.py podpisz dist/SHA256SUMS.txt
        # tworzy SHA256SUMS.txt.sig; klucz prywatny (hex) w zmiennej MYDICT_KLUCZ_WYDAN
    python packaging/wydanie.py sprawdz-podpis dist/SHA256SUMS.txt
        # sprawdza podpis kluczem publicznym zaszytym w programie
    python packaging/wydanie.py nowe-klucze <katalog>
        # nowa para kluczy wydań i zgłoszeń; prywatne trafiają do <katalog>, publiczne na ekran

Opis zmian trafia do wydania GitHub i jest pokazywany użytkownikowi w oknie aktualizacji.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import dyktowanie_krypto as krypto  # noqa: E402

SIGNATURE_SUFFIX = ".sig"


def code_version() -> str:
    text = (ROOT / "dyktowanie_app.py").read_text(encoding="utf-8")
    return re.search(r'__version__ = "([^"]+)"', text).group(1)


def code_constant(name: str) -> str:
    text = (ROOT / "dyktowanie_app.py").read_text(encoding="utf-8")
    match = re.search(rf'^{name} = "([^"]*)"', text, flags=re.MULTILINE)
    return match.group(1) if match else ""


def notes(version: str, changelog: str) -> str:
    """Treść sekcji ``## <wersja>`` (do następnego nagłówka drugiego poziomu)."""
    pattern = rf"^##\s*\[?v?{re.escape(version)}\]?[^\n]*\n(.*?)(?=^##\s|\Z)"
    match = re.search(pattern, changelog, flags=re.MULTILINE | re.DOTALL)
    return match.group(1).strip() if match else ""


def sign_file(path: Path, private_hex: str) -> Path:
    """Podpisuje plik; podpis (hex) trafia do ``<plik>.sig``. Sprawdza go od razu
    kluczem publicznym z programu, żeby nie wydać wersji, której program nie przyjmie."""
    seed = bytes.fromhex(private_hex.strip())
    data = path.read_bytes()
    signature = krypto.sign(seed, data)
    expected = code_constant("RELEASE_PUBLIC_KEY")
    if expected and krypto.public_key(seed).hex() != expected:
        raise SystemExit("Klucz MYDICT_KLUCZ_WYDAN nie pasuje do RELEASE_PUBLIC_KEY w dyktowanie_app.py.")
    target = path.with_name(path.name + SIGNATURE_SUFFIX)
    target.write_text(signature.hex() + "\n", encoding="ascii")
    return target


def new_keys(directory: Path) -> str:
    """Tworzy klucze prywatne (pliki bez rozszerzenia, tylko dla autora) i zwraca
    linie z kluczami publicznymi do wpisania w ``dyktowanie_app.py``."""
    directory.mkdir(parents=True, exist_ok=True)
    release, reports = os.urandom(32), os.urandom(32)
    for name, value in (("klucz-wydan", release), ("klucz-zgloszen", reports)):
        target = directory / name
        if target.exists():
            raise SystemExit(f"{target} już istnieje — nie nadpisuję klucza.")
        target.write_text(value.hex() + "\n", encoding="ascii")
    return (f'RELEASE_PUBLIC_KEY = "{krypto.public_key(release).hex()}"\n'
            f'REPORTS_PUBLIC_KEY = "{krypto.x25519_public(reports).hex()}"\n')


def main(argv: list[str]) -> int:
    commands = ("sprawdz", "notatki", "podpisz", "sprawdz-podpis", "nowe-klucze")
    if len(argv) != 3 or argv[1] not in commands:
        sys.stderr.write(__doc__)
        return 2
    command, argument = argv[1], argv[2]
    sys.stdout.reconfigure(encoding="utf-8")
    if command == "podpisz":
        key = os.environ.get("MYDICT_KLUCZ_WYDAN", "")
        if not key.strip():
            sys.stderr.write("Brak klucza podpisu: ustaw zmienną MYDICT_KLUCZ_WYDAN (sekret repozytorium).\n")
            return 1
        sys.stdout.write(f"Podpisano: {sign_file(Path(argument), key)}\n")
        return 0
    if command == "sprawdz-podpis":
        path = Path(argument)
        signature = path.with_name(path.name + SIGNATURE_SUFFIX).read_text(encoding="ascii")
        if not krypto.verify_hex(code_constant("RELEASE_PUBLIC_KEY"), path.read_bytes(), signature):
            sys.stderr.write("Podpis NIEPRAWIDŁOWY.\n")
            return 1
        sys.stdout.write("Podpis prawidłowy.\n")
        return 0
    if command == "nowe-klucze":
        sys.stdout.write(new_keys(Path(argument)))
        return 0
    version = argument.lstrip("vV")
    if command == "sprawdz":
        if version != code_version():
            sys.stderr.write(
                f"Znacznik v{version} nie zgadza się z wersją w dyktowanie_app.py ({code_version()}).\n"
            )
            return 1
        sys.stdout.write(f"Wersja {version} — zgodna.\n")
        return 0
    text = notes(version, (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"))
    sys.stdout.write((text or f"Wersja {version}.") + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
