"""Pomocnik wydań: zgodność znacznika z wersją i opis zmian z CHANGELOG.md.

    python packaging/wydanie.py sprawdz v2.0.0   # kod 1, gdy znacznik ≠ wersja w kodzie
    python packaging/wydanie.py notatki v2.0.0   # wypisuje sekcję „## 2.0.0” z CHANGELOG.md

Opis trafia do wydania GitHub i jest pokazywany użytkownikowi w oknie aktualizacji.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def code_version() -> str:
    text = (ROOT / "dyktowanie_app.py").read_text(encoding="utf-8")
    return re.search(r'__version__ = "([^"]+)"', text).group(1)


def notes(version: str, changelog: str) -> str:
    """Treść sekcji ``## <wersja>`` (do następnego nagłówka drugiego poziomu)."""
    pattern = rf"^##\s*\[?v?{re.escape(version)}\]?[^\n]*\n(.*?)(?=^##\s|\Z)"
    match = re.search(pattern, changelog, flags=re.MULTILINE | re.DOTALL)
    return match.group(1).strip() if match else ""


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[1] not in ("sprawdz", "notatki"):
        sys.stderr.write(__doc__)
        return 2
    version = argv[2].lstrip("vV")
    if argv[1] == "sprawdz":
        if version != code_version():
            sys.stderr.write(
                f"Znacznik v{version} nie zgadza się z wersją w dyktowanie_app.py ({code_version()}).\n"
            )
            return 1
        sys.stdout.write(f"Wersja {version} — zgodna.\n")
        return 0
    text = notes(version, (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"))
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stdout.write((text or f"Wersja {version}.") + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
