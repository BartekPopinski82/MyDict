"""Polskie komendy znaków; bez zmiany już wpisanego tekstu i bez usług AI."""

from __future__ import annotations

import re


# Tryb odstępów: close/join przykleja znak z lewej, open/join także z prawej.
COMMANDS = {
    "kropka": (".", "close"),
    "przecinek": (",", "close"),
    "dwukropek": (":", "close"),
    "średnik": (";", "close"),
    "pytajnik": ("?", "close"),
    "znak zapytania": ("?", "close"),
    "wykrzyknik": ("!", "close"),
    "wielokropek": ("…", "close"),
    "trzy kropki": ("…", "close"),
    "myślnik": ("-", "space"),
    "pauza": ("—", "space"),
    "łącznik": ("-", "join"),
    "nowa linia": ("\n", "break"),
    "nowy wiersz": ("\n", "break"),
    "nowy akapit": ("\n\n", "break"),
    "otwórz nawias": ("(", "open"),
    "nawias otwierający": ("(", "open"),
    "zamknij nawias": (")", "close"),
    "nawias zamykający": (")", "close"),
    "otwórz nawias kwadratowy": ("[", "open"),
    "zamknij nawias kwadratowy": ("]", "close"),
    "otwórz nawias klamrowy": ("{", "open"),
    "zamknij nawias klamrowy": ("}", "close"),
    "otwórz cudzysłów": ("„", "open"),
    "zamknij cudzysłów": ("”", "close"),
    "apostrof": ("'", "join"),
    "ukośnik": ("/", "join"),
    "ukośnik wsteczny": ("\\", "join"),
    "podkreślnik": ("_", "join"),
    "małpa": ("@", "join"),
    "znak małpy": ("@", "join"),
    "procent": ("%", "close"),
    "promil": ("‰", "close"),
    "plus": ("+", "space"),
    "minus": ("-", "space"),
    "znak równości": ("=", "space"),
    "równa się": ("=", "space"),
    "gwiazdka": ("*", "space"),
    "hasztag": ("#", "open"),
    "kratka": ("#", "open"),
    "ampersand": ("&", "space"),
}

_FOLD = str.maketrans("ąćęłńóśźż", "acelnoszz")


def _key(text: str) -> str:
    return text.lower().translate(_FOLD)


_COMMANDS = {_key(name): value for name, value in COMMANDS.items()}
_PHRASES = sorted(_COMMANDS, key=len, reverse=True)
_PATTERNS = [(phrase, re.compile(re.escape(phrase).replace(r"\ ", r"\s+") + r"(?!\w)"))
             for phrase in _PHRASES]
_ESCAPE = "doslownie"
_PREFIXES = _PHRASES + [_ESCAPE + " " + name for name in _PHRASES]
_OPEN_OR_JOIN = "([{„#@/_\\'"


def render_dictation(raw: str, previous: str = "", *, final: bool = True,
                     leading_space: bool = False, right_glue: bool = False) -> tuple[str, bool]:
    """Zwraca dopisek. Niepełne komendy czekają na resztę w trybie live.

    Komenda musi mieścić się w jednej wypowiedzi, lecz może przyjść w wielu
    fragmentach ASR. Zwykła interpunkcja pozostaje bez zmian. Bezpośrednio po
    komendzie pomijamy interpunkcję dopisaną automatycznie przez model.
    """
    normalized = _key(raw)
    out = ""
    pos = 0
    pending_space = bool(previous) or leading_space
    glue_right = right_glue or bool(previous and previous[-1] in _OPEN_OR_JOIN)

    def emit(value: str, kind: str = "text"):
        nonlocal out, pending_space, glue_right
        before = previous + out
        if (pending_space and kind not in ("close", "join", "break")
                and not glue_right and (before or leading_space)
                and not (before and before[-1].isspace())):
            out += " "
        out += value
        pending_space = kind in ("space", "close")
        glue_right = kind in ("open", "join", "break")

    while pos < len(raw):
        if raw[pos].isspace():
            pending_space = True
            pos += 1
            continue
        boundary = pos == 0 or not (raw[pos - 1].isalnum() or raw[pos - 1] == "_")
        if boundary and not final:
            tail = re.sub(r"\s+", " ", normalized[pos:])
            if any(phrase.startswith(tail) for phrase in _PREFIXES):
                break
        escaped = False
        start = pos
        escape = re.match(r"doslownie\s+", normalized[pos:]) if boundary else None
        if escape:
            escaped = True
            start += escape.end()
        matched = False
        if boundary:
            for phrase, pattern in _PATTERNS:
                match = pattern.match(normalized, start)
                if not match:
                    continue
                end = match.end()
                if escaped:
                    emit(raw[start:end])
                else:
                    value, kind = _COMMANDS[phrase]
                    emit(value, kind)
                    # Whisper potrafi zwrócić np. „dwukropek.” lub „kropka,”.
                    while end < len(raw) and raw[end] in ".,;:!?…":
                        end += 1
                pos = end
                matched = True
                break
        if matched:
            continue
        token = re.match(r"\w+|[^\w\s]", raw[pos:])
        emit(token.group())
        pos += token.end()
    return out, glue_right


def format_dictation(raw: str, previous: str = "", *, final: bool = True,
                     leading_space: bool = False) -> str:
    """Wygodny wariant bez stanu pomiędzy wypowiedziami."""
    return render_dictation(raw, previous, final=final, leading_space=leading_space)[0]
