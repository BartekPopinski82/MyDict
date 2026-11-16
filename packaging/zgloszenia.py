"""Odczyt zgłoszeń z repozytorium zgłoszeń (tylko dla autora).

    git clone https://github.com/<konto>/MyDict-zgloszenia   # raz; potem: git pull
    python packaging/zgloszenia.py <folder klonu> [--klucz <plik klucza>] [--wynik <folder>]

Każdy plik ``zgloszenia/<instalacja>/<czas>-<rodzaj>.mdz`` jest odszyfrowywany do
``<wynik>/<instalacja>[-<kontakt>]/<czas>-<rodzaj>.txt`` (domyślnie ``<klon>/odczytane``;
dopisz ``odczytane/`` do .gitignore klonu). Już odczytane pliki są pomijane.
Klucz prywatny domyślnie: ``~/.mydict-klucze/klucz-zgloszen`` (z ``wydanie.py nowe-klucze``).
"""

from __future__ import annotations

import argparse
import re
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import dyktowanie_krypto as krypto  # noqa: E402

DEFAULT_KEY = Path.home() / ".mydict-klucze" / "klucz-zgloszen"


def decrypt(data: bytes, private: bytes) -> str:
    return zlib.decompress(krypto.unseal(private, data)).decode("utf-8", errors="replace")


def contact_slug(text: str) -> str:
    match = re.search(r"^Kontakt: (.+)$", text, flags=re.MULTILINE)
    value = match.group(1).strip() if match else ""
    if not value or value.startswith("("):
        return ""
    return re.sub(r"[^\w.@-]+", "_", value)[:40]


def read_all(clone: Path, private: bytes, output: Path) -> tuple[int, int]:
    """Zwraca (nowo odczytane, błędne)."""
    new = bad = 0
    for source in sorted((clone / "zgloszenia").glob("*/*.mdz")):
        install = source.parent.name
        try:
            text = decrypt(source.read_bytes(), private)
        except (ValueError, zlib.error) as exc:
            sys.stderr.write(f"Pominięto {source.name}: {exc}\n")
            bad += 1
            continue
        slug = contact_slug(text)
        folder = output / (f"{install}-{slug}" if slug else install)
        target = folder / (source.stem + ".txt")
        if any(output.glob(f"{install}*/{source.stem}.txt")):
            continue
        folder.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        sys.stdout.write(f"{target.relative_to(output)}\n")
        new += 1
    return new, bad


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("klon", type=Path)
    parser.add_argument("--klucz", type=Path, default=DEFAULT_KEY)
    parser.add_argument("--wynik", type=Path)
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    private = bytes.fromhex(args.klucz.read_text(encoding="ascii").strip())
    new, bad = read_all(args.klon, private, args.wynik or args.klon / "odczytane")
    sys.stdout.write(f"Nowych zgłoszeń: {new}" + (f", nieczytelnych: {bad}" if bad else "") + "\n")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
