"""Wybór warstwy systemowej: jedno miejsce, z którego interfejs bierze wpisywanie,
aktywne okno i globalny skrót — niezależnie od tego, czy to Windows, czy macOS."""

from __future__ import annotations

import sys

IS_WINDOWS = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"

if IS_MAC:
    from dyktowanie_macos import (
        DEFAULT_HOTKEY,
        HOTKEYS,
        HotkeyListener,
        TypingEngine,
        foreground_window,
        make_window_non_activating,
        own_process_elevated,
        request_typing_permission,
        typing_permission_granted,
    )

    # Nazwy do komunikatów w interfejsie.
    TARGET_NOUN = "aplikacja"
    TRAY_HINT = "na pasku menu u góry ekranu"
else:
    from dyktowanie_windows import (
        DEFAULT_HOTKEY,
        HOTKEYS,
        HotkeyListener,
        TypingEngine,
        foreground_window,
        make_window_non_activating,
        own_process_elevated,
    )

    TARGET_NOUN = "okno"
    TRAY_HINT = "w zasobniku obok zegara"

    def typing_permission_granted() -> bool:
        """Windows nie wymaga osobnej zgody na wpisywanie do innych programów."""
        return True

    def request_typing_permission():
        return None


# Czy program potrafi tu wpisywać tekst do innych aplikacji i ma globalny skrót.
TYPING_SUPPORTED = TypingEngine.available
HOTKEY_SUPPORTED = HotkeyListener.available

__all__ = [
    "DEFAULT_HOTKEY", "HOTKEYS", "HOTKEY_SUPPORTED", "IS_MAC", "IS_WINDOWS", "TARGET_NOUN",
    "TRAY_HINT", "TYPING_SUPPORTED", "HotkeyListener", "TypingEngine", "foreground_window",
    "make_window_non_activating", "own_process_elevated", "request_typing_permission",
    "typing_permission_granted",
]
