"""macOS: aktywna aplikacja, wpisywanie podyktowanego tekstu i globalny skrót.

Odpowiednik ``dyktowanie_windows`` o tych samych nazwach. Wszystko przez ctypes
i biblioteki systemowe (bez PyObjC):

* tekst trafia do aktywnej aplikacji jako zdarzenia klawiatury Quartz z gotowym
  znakiem Unicode (``CGEventKeyboardSetUnicodeString``) — polskie litery działają
  przy każdym układzie klawiatury. Wymaga zgody „Dostępność” w Ustawieniach
  systemowych; bez niej system po cichu odrzuca zdarzenia;
* aktywną aplikację podaje ``NSWorkspace.frontmostApplication``;
* globalny skrót rejestruje Carbon (``RegisterEventHotKey``) — nie wymaga zgody
  i zgłasza osobno naciśnięcie i puszczenie, więc tryb „przytrzymaj” działa wprost.
"""

from __future__ import annotations

import ctypes
import logging
import subprocess
import sys
import time

from dyktowanie_windows import OWN_PID, TypingEngine as _TypingEngine, WindowInfo


log = logging.getLogger("dyktowanie.macos")
IS_MAC = sys.platform == "darwin"

# Modyfikatory Carbon i kody klawiszy (układ ANSI — pozycje fizyczne).
CMD, SHIFT, OPTION, CONTROL = 0x0100, 0x0200, 0x0800, 0x1000
KEY_SPACE, KEY_D, KEY_F8, KEY_F9, KEY_RETURN, KEY_TAB = 49, 2, 100, 101, 36, 48

HOTKEYS = {
    "Ctrl+Option+Spacja": (CONTROL | OPTION, KEY_SPACE),
    "Ctrl+Option+D": (CONTROL | OPTION, KEY_D),
    "Ctrl+Shift+Spacja": (CONTROL | SHIFT, KEY_SPACE),
    "F8": (0, KEY_F8),
    "F9": (0, KEY_F9),
}
DEFAULT_HOTKEY = "Ctrl+Option+Spacja"

# Maski flag zdarzeń Quartz: Shift, Control, Option, Command.
_MODIFIER_MASK = 0x00020000 | 0x00040000 | 0x00080000 | 0x00100000

if IS_MAC:
    # Ścieżki podane wprost: działają także w aplikacji spakowanej (biblioteki są w pamięci systemu).
    _objc = ctypes.cdll.LoadLibrary("/usr/lib/libobjc.A.dylib")
    _cf = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    _app_services = ctypes.cdll.LoadLibrary(
        "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
    )
    _carbon = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/Carbon.framework/Carbon")
    ctypes.cdll.LoadLibrary("/System/Library/Frameworks/AppKit.framework/AppKit")

    _objc.objc_getClass.restype = ctypes.c_void_p
    _objc.objc_getClass.argtypes = (ctypes.c_char_p,)
    _objc.sel_registerName.restype = ctypes.c_void_p
    _objc.sel_registerName.argtypes = (ctypes.c_char_p,)

    def _send(restype, *argtypes):
        """``objc_msgSend`` o podanej sygnaturze (każde wywołanie ma własną)."""
        prototype = ctypes.CFUNCTYPE(restype, ctypes.c_void_p, ctypes.c_void_p, *argtypes)
        return prototype(("objc_msgSend", _objc))

    _send_id = _send(ctypes.c_void_p)
    _send_int = _send(ctypes.c_int)
    _send_ulong = _send(ctypes.c_ulong)
    _send_str = _send(ctypes.c_char_p)
    _send_set_ulong = _send(None, ctypes.c_ulong)
    _send_set_bool = _send(None, ctypes.c_bool)

    def _sel(name: str):
        return _objc.sel_registerName(name.encode("ascii"))

    def _cls(name: str):
        return _objc.objc_getClass(name.encode("ascii"))

    _cf.CFRelease.argtypes = (ctypes.c_void_p,)
    _cf.CFRelease.restype = None
    _cf.CFDictionaryCreate.restype = ctypes.c_void_p
    _cf.CFDictionaryCreate.argtypes = (
        ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p,
    )

    _app_services.CGEventCreateKeyboardEvent.restype = ctypes.c_void_p
    _app_services.CGEventCreateKeyboardEvent.argtypes = (ctypes.c_void_p, ctypes.c_uint16, ctypes.c_bool)
    _app_services.CGEventKeyboardSetUnicodeString.restype = None
    _app_services.CGEventKeyboardSetUnicodeString.argtypes = (
        ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_uint16)
    )
    _app_services.CGEventSetFlags.restype = None
    _app_services.CGEventSetFlags.argtypes = (ctypes.c_void_p, ctypes.c_uint64)
    _app_services.CGEventPost.restype = None
    _app_services.CGEventPost.argtypes = (ctypes.c_uint32, ctypes.c_void_p)
    _app_services.CGEventSourceFlagsState.restype = ctypes.c_uint64
    _app_services.CGEventSourceFlagsState.argtypes = (ctypes.c_int32,)
    _app_services.AXIsProcessTrusted.restype = ctypes.c_bool
    _app_services.AXIsProcessTrusted.argtypes = ()
    _app_services.AXIsProcessTrustedWithOptions.restype = ctypes.c_bool
    _app_services.AXIsProcessTrustedWithOptions.argtypes = (ctypes.c_void_p,)

    class _EventTypeSpec(ctypes.Structure):
        _fields_ = (("eventClass", ctypes.c_uint32), ("eventKind", ctypes.c_uint32))

    class _EventHotKeyID(ctypes.Structure):
        _fields_ = (("signature", ctypes.c_uint32), ("id", ctypes.c_uint32))

    _HANDLER = ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
    _carbon.GetApplicationEventTarget.restype = ctypes.c_void_p
    _carbon.GetApplicationEventTarget.argtypes = ()
    _carbon.InstallEventHandler.restype = ctypes.c_int32
    _carbon.InstallEventHandler.argtypes = (
        ctypes.c_void_p, _HANDLER, ctypes.c_ulong, ctypes.POINTER(_EventTypeSpec),
        ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
    )
    _carbon.RemoveEventHandler.restype = ctypes.c_int32
    _carbon.RemoveEventHandler.argtypes = (ctypes.c_void_p,)
    _carbon.RegisterEventHotKey.restype = ctypes.c_int32
    _carbon.RegisterEventHotKey.argtypes = (
        ctypes.c_uint32, ctypes.c_uint32, _EventHotKeyID, ctypes.c_void_p,
        ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p),
    )
    _carbon.UnregisterEventHotKey.restype = ctypes.c_int32
    _carbon.UnregisterEventHotKey.argtypes = (ctypes.c_void_p,)
    _carbon.GetEventKind.restype = ctypes.c_uint32
    _carbon.GetEventKind.argtypes = (ctypes.c_void_p,)


# ---------------------------------------------------------------------------
# Aktywna aplikacja
# ---------------------------------------------------------------------------


def foreground_window() -> WindowInfo | None:
    """Aktywna aplikacja (macOS nie udostępnia tytułu okna bez dodatkowych zgód)."""
    if not IS_MAC:
        return None
    pool = _send_id(_send_id(_cls("NSAutoreleasePool"), _sel("alloc")), _sel("init"))
    try:
        workspace = _send_id(_cls("NSWorkspace"), _sel("sharedWorkspace"))
        application = _send_id(workspace, _sel("frontmostApplication")) if workspace else None
        if not application:
            return None
        pid = int(_send_int(application, _sel("processIdentifier")))
        name_object = _send_id(application, _sel("localizedName"))
        raw = _send_str(name_object, _sel("UTF8String")) if name_object else None
        name = raw.decode("utf-8", errors="replace") if raw else ""
        return WindowInfo(pid, pid, "", name, "", None)
    except Exception:
        log.debug("Nie odczytano aktywnej aplikacji", exc_info=True)
        return None
    finally:
        _send_id(pool, _sel("drain"))


def own_process_elevated() -> bool:
    return False


def modifiers_down() -> bool:
    """Czy Shift/Control/Option/Command są wciśnięte? Wtedy wstrzymujemy wpisywanie."""
    if not IS_MAC:
        return False
    return bool(_app_services.CGEventSourceFlagsState(0) & _MODIFIER_MASK)


def make_window_non_activating(view: int):
    """Pasek nie aktywuje programu po kliknięciu i jest widoczny na każdym biurku."""
    if not IS_MAC or not view:
        return
    try:
        window = _send_id(view, _sel("window"))
        if not window:
            return
        mask = _send_ulong(window, _sel("styleMask"))
        _send_set_ulong(window, _sel("setStyleMask:"), mask | (1 << 7))      # NSWindowStyleMaskNonactivatingPanel
        _send_set_bool(window, _sel("setHidesOnDeactivate:"), False)
        # canJoinAllSpaces | fullScreenAuxiliary
        _send_set_ulong(window, _sel("setCollectionBehavior:"), (1 << 0) | (1 << 8))
    except Exception:
        log.debug("Nie ustawiono paska jako panelu nieaktywującego", exc_info=True)


# ---------------------------------------------------------------------------
# Zgoda „Dostępność”
# ---------------------------------------------------------------------------


def typing_permission_granted() -> bool:
    if not IS_MAC:
        return False
    try:
        return bool(_app_services.AXIsProcessTrusted())
    except Exception:
        return False


def request_typing_permission():
    """Pokazuje systemowe pytanie o zgodę i otwiera właściwy panel Ustawień."""
    if not IS_MAC:
        return
    try:
        key = ctypes.c_void_p.in_dll(_app_services, "kAXTrustedCheckOptionPrompt")
        value = ctypes.c_void_p.in_dll(_cf, "kCFBooleanTrue")
        keys = (ctypes.c_void_p * 1)(key)
        values = (ctypes.c_void_p * 1)(value)
        options = _cf.CFDictionaryCreate(
            None, keys, values, 1,
            ctypes.addressof(ctypes.c_void_p.in_dll(_cf, "kCFTypeDictionaryKeyCallBacks")),
            ctypes.addressof(ctypes.c_void_p.in_dll(_cf, "kCFTypeDictionaryValueCallBacks")),
        )
        try:
            _app_services.AXIsProcessTrustedWithOptions(options)
        finally:
            if options:
                _cf.CFRelease(options)
    except Exception:
        log.warning("Nie udało się wywołać pytania o zgodę Dostępność", exc_info=True)
    try:
        subprocess.Popen(
            ["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"]
        )
    except OSError:
        log.warning("Nie udało się otworzyć Ustawień systemowych", exc_info=True)


# ---------------------------------------------------------------------------
# Wpisywanie tekstu
# ---------------------------------------------------------------------------


def _post_key(keycode: int, units: list[int] | None = None):
    for down in (True, False):
        event = _app_services.CGEventCreateKeyboardEvent(None, keycode, down)
        if not event:
            raise OSError("CGEventCreateKeyboardEvent zwrócił NULL")
        try:
            if units:
                buffer = (ctypes.c_uint16 * len(units))(*units)
                _app_services.CGEventKeyboardSetUnicodeString(event, len(units), buffer)
            _app_services.CGEventSetFlags(event, 0)   # bez przypadkowo trzymanych modyfikatorów
            _app_services.CGEventPost(0, event)       # kCGHIDEventTap
        finally:
            _cf.CFRelease(event)


def type_unicode(text: str) -> bool:
    """Wpisuje tekst do aktywnej aplikacji — jeden znak na parę zdarzeń klawisza."""
    if not IS_MAC:
        return False
    if not typing_permission_granted():
        return False
    try:
        for index, char in enumerate(text):
            if char == "\r":
                continue
            if char == "\n":
                _post_key(KEY_RETURN)
            elif char == "\t":
                _post_key(KEY_TAB)
            else:
                encoded = char.encode("utf-16-le")
                units = [int.from_bytes(encoded[i:i + 2], "little") for i in range(0, len(encoded), 2)]
                _post_key(0, units)
            if index % 8 == 7:
                time.sleep(0.002)   # długie porcje: aplikacja musi nadążyć z kolejką zdarzeń
        return True
    except Exception:
        log.warning("Błąd wysyłania zdarzeń klawiatury", exc_info=True)
        return False


class TypingEngine(_TypingEngine):
    """Ten sam mechanizm co na Windows, z funkcjami systemowymi macOS."""

    available = IS_MAC

    def __init__(self, inject=None, window_provider=None, modifiers=None):
        super().__init__(
            inject or type_unicode,
            window_provider or foreground_window,
            modifiers or modifiers_down,
        )


# ---------------------------------------------------------------------------
# Globalny skrót klawiszowy
# ---------------------------------------------------------------------------

_EVENT_CLASS_KEYBOARD = int.from_bytes(b"keyb", "big")
_HOTKEY_PRESSED, _HOTKEY_RELEASED = 5, 6
_SIGNATURE = int.from_bytes(b"BAPO", "big")


class HotkeyListener:
    """Globalny skrót (Carbon). Tworzyć i zatrzymywać w głównym wątku programu —
    zdarzenia przychodzą przez pętlę zdarzeń aplikacji."""

    available = IS_MAC

    def __init__(self, combo: str, on_press, on_release, hold_mode: bool = False):
        self.combo = combo if combo in HOTKEYS else DEFAULT_HOTKEY
        self.on_press = on_press
        self.on_release = on_release
        self.hold_mode = hold_mode
        self.ok = False
        self._hotkey = ctypes.c_void_p()
        self._handler_ref = ctypes.c_void_p()
        self._callback = None

    def start(self) -> bool:
        if not self.available:
            return False

        def handle(_call_ref, event, _user_data):
            try:
                kind = _carbon.GetEventKind(event)
                if kind == _HOTKEY_PRESSED:
                    self.on_press()
                elif kind == _HOTKEY_RELEASED and self.hold_mode:
                    self.on_release()
            except Exception:
                log.exception("Błąd obsługi skrótu")
            return 0

        self._callback = _HANDLER(handle)   # referencja musi żyć tak długo jak rejestracja
        types = (_EventTypeSpec * 2)(
            _EventTypeSpec(_EVENT_CLASS_KEYBOARD, _HOTKEY_PRESSED),
            _EventTypeSpec(_EVENT_CLASS_KEYBOARD, _HOTKEY_RELEASED),
        )
        target = _carbon.GetApplicationEventTarget()
        status = _carbon.InstallEventHandler(
            target, self._callback, 2, types, None, ctypes.byref(self._handler_ref)
        )
        if status != 0:
            log.warning("Nie udało się zainstalować obsługi skrótu (błąd %s)", status)
            return False
        modifiers, keycode = HOTKEYS[self.combo]
        status = _carbon.RegisterEventHotKey(
            keycode, modifiers, _EventHotKeyID(_SIGNATURE, 1), target, 0, ctypes.byref(self._hotkey)
        )
        self.ok = status == 0
        if not self.ok:
            log.warning("Nie udało się zarejestrować skrótu %s (błąd %s)", self.combo, status)
            self.stop()
        return self.ok

    def stop(self):
        if self._hotkey:
            _carbon.UnregisterEventHotKey(self._hotkey)
            self._hotkey = ctypes.c_void_p()
        if self._handler_ref:
            _carbon.RemoveEventHandler(self._handler_ref)
            self._handler_ref = ctypes.c_void_p()
        self.ok = False


__all__ = [
    "DEFAULT_HOTKEY", "HOTKEYS", "OWN_PID", "HotkeyListener", "TypingEngine", "WindowInfo",
    "foreground_window", "make_window_non_activating", "modifiers_down", "own_process_elevated",
    "request_typing_permission", "type_unicode", "typing_permission_granted",
]
