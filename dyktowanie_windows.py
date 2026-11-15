"""Windows: aktywne okno, wpisywanie podyktowanego tekstu i globalny skrót.

Tekst trafia do aktywnego okna przez ``SendInput`` z flagą
``KEYEVENTF_UNICODE`` — system dostarcza gotowe znaki (także polskie litery)
do dowolnej aplikacji: Worda, przeglądarki, Teams, Notatnika itd.,
niezależnie od układu klawiatury. Tak samo działa wbudowane dyktowanie
Windows (Win+H).

Wpisywany jest wyłącznie tekst zatwierdzony przez rozpoznawanie mowy — raz
wpisanego tekstu program nigdy nie kasuje ani nie poprawia, więc nie ma
ryzyka, że usunie coś, co użytkownik napisał sam.

Na innych systemach moduł udostępnia te same nazwy, ale wpisywanie jest
wyłączone — interfejs pokazuje wtedy tylko transkrypcję.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from dataclasses import dataclass


log = logging.getLogger("dyktowanie.windows")
IS_WINDOWS = sys.platform.startswith("win")
OWN_PID = os.getpid()

# Okna, do których nigdy nie piszemy (pulpit, pasek zadań).
IGNORED_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}

FRIENDLY_PROCESS_NAMES = {
    "winword.exe": "Word",
    "excel.exe": "Excel",
    "powerpnt.exe": "PowerPoint",
    "outlook.exe": "Outlook",
    "olk.exe": "Outlook",
    "onenote.exe": "OneNote",
    "chrome.exe": "Chrome",
    "msedge.exe": "Edge",
    "firefox.exe": "Firefox",
    "opera.exe": "Opera",
    "brave.exe": "Brave",
    "notepad.exe": "Notatnik",
    "wordpad.exe": "WordPad",
    "ms-teams.exe": "Teams",
    "teams.exe": "Teams",
    "slack.exe": "Slack",
    "discord.exe": "Discord",
    "code.exe": "VS Code",
    "windowsterminal.exe": "Terminal",
    "explorer.exe": "Eksplorator",
    "thunderbird.exe": "Thunderbird",
    "acrobat.exe": "Acrobat",
    "soffice.bin": "LibreOffice",
    "obsidian.exe": "Obsidian",
    "notion.exe": "Notion",
    "whatsapp.exe": "WhatsApp",
    "telegram.exe": "Telegram",
}


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    pid: int
    title: str
    process: str
    class_name: str
    elevated: bool | None = None

    @property
    def app(self) -> str:
        friendly = FRIENDLY_PROCESS_NAMES.get(self.process.lower())
        if friendly:
            return friendly
        stem = self.process.rsplit(".", 1)[0]
        return stem[:1].upper() + stem[1:] if stem else "Aplikacja"

    @property
    def label(self) -> str:
        title = self.title.strip()
        if not title or title.lower() == self.app.lower():
            return self.app
        if len(title) > 48:
            title = title[:47] + "…"
        return f"{self.app} — {title}"

    @property
    def own(self) -> bool:
        return self.pid == OWN_PID


# ---------------------------------------------------------------------------
# Win32
# ---------------------------------------------------------------------------

if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

    ULONG_PTR = ctypes.c_size_t
    LONG_PTR = ctypes.c_ssize_t

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = (
            ("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR),
        )

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = (
            ("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR),
        )

    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = (("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD))

    class _INPUTUNION(ctypes.Union):
        _fields_ = (("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT))

    class INPUT(ctypes.Structure):
        _fields_ = (("type", wintypes.DWORD), ("union", _INPUTUNION))

    user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
    user32.SendInput.restype = wintypes.UINT
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowTextLengthW.argtypes = (wintypes.HWND,)
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetClassNameW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    user32.GetClassNameW.restype = ctypes.c_int
    user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetAsyncKeyState.argtypes = (ctypes.c_int,)
    user32.GetAsyncKeyState.restype = ctypes.c_short
    user32.RegisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT)
    user32.RegisterHotKey.restype = wintypes.BOOL
    user32.UnregisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int)
    user32.GetMessageW.argtypes = (ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT)
    user32.GetMessageW.restype = ctypes.c_int
    user32.PeekMessageW.argtypes = (
        ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT
    )
    user32.PostThreadMessageW.argtypes = (wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    user32.PostThreadMessageW.restype = wintypes.BOOL
    LRESULT = LONG_PTR

    class KBDLLHOOKSTRUCT(ctypes.Structure):
        _fields_ = (
            ("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD), ("flags", wintypes.DWORD),
            ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR),
        )

    HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
    user32.SetWindowsHookExW.argtypes = (ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD)
    user32.SetWindowsHookExW.restype = wintypes.HHOOK
    user32.CallNextHookEx.argtypes = (wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
    user32.CallNextHookEx.restype = LRESULT
    user32.UnhookWindowsHookEx.argtypes = (wintypes.HHOOK,)
    user32.UnhookWindowsHookEx.restype = wintypes.BOOL
    user32.MsgWaitForMultipleObjects.argtypes = (
        wintypes.DWORD, ctypes.c_void_p, wintypes.BOOL, wintypes.DWORD, wintypes.DWORD
    )
    user32.MsgWaitForMultipleObjects.restype = wintypes.DWORD
    kernel32.GetModuleHandleW.argtypes = (wintypes.LPCWSTR,)
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE
    if hasattr(user32, "GetWindowLongPtrW"):
        _get_window_long = user32.GetWindowLongPtrW
        _set_window_long = user32.SetWindowLongPtrW
    else:  # 32-bitowy Python
        _get_window_long = user32.GetWindowLongW
        _set_window_long = user32.SetWindowLongW
    _get_window_long.argtypes = (wintypes.HWND, ctypes.c_int)
    _get_window_long.restype = LONG_PTR
    _set_window_long.argtypes = (wintypes.HWND, ctypes.c_int, LONG_PTR)
    _set_window_long.restype = LONG_PTR

    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.QueryFullProcessImageNameW.argtypes = (
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)
    )
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.GetCurrentThreadId.restype = wintypes.DWORD
    advapi32.OpenProcessToken.argtypes = (wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE))
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = (
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)
    )
    advapi32.GetTokenInformation.restype = wintypes.BOOL

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
VK_TAB = 0x09
VK_RETURN = 0x0D
VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12
VK_LWIN = 0x5B
VK_RWIN = 0x5C
MODIFIER_KEYS = (VK_SHIFT, VK_CONTROL, VK_MENU, VK_LWIN, VK_RWIN)
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_NOREPEAT = 0x4000
KEYEVENTF_EXTENDEDKEY = 0x0001
WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
WM_SYSKEYDOWN = 0x0104
LLKHF_INJECTED = 0x10
PM_REMOVE = 0x0001
QS_ALLINPUT = 0x04FF
VK_DUMMY = 0xE8  # nieprzypisany klawisz: blokuje otwarcie menu (Alt) / Startu (Win)
# (klawisz, czy rozszerzony) — konkretne lewe/prawe modyfikatory
SIDE_MODIFIERS = (
    (0xA2, False), (0xA3, True),   # Ctrl
    (0xA4, False), (0xA5, True),   # Alt / AltGr
    (0xA0, False), (0xA1, False),  # Shift
    (VK_LWIN, True), (VK_RWIN, True),
)


def _process_details(pid: int) -> tuple[str, bool | None]:
    """Nazwa programu oraz to, czy działa z prawami administratora."""
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return "", None
    try:
        size = wintypes.DWORD(1024)
        buffer = ctypes.create_unicode_buffer(1024)
        name = ""
        if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            name = os.path.basename(buffer.value)
        elevated = None
        token = wintypes.HANDLE()
        if advapi32.OpenProcessToken(handle, 0x0008, ctypes.byref(token)):  # TOKEN_QUERY
            try:
                value = wintypes.DWORD()
                returned = wintypes.DWORD()
                if advapi32.GetTokenInformation(token, 20, ctypes.byref(value), 4, ctypes.byref(returned)):
                    elevated = bool(value.value)
            finally:
                kernel32.CloseHandle(token)
        elif ctypes.get_last_error() == 5:  # ERROR_ACCESS_DENIED → zwykle program uruchomiony jako administrator
            elevated = True
        return name, elevated
    finally:
        kernel32.CloseHandle(handle)


_DETAILS_CACHE: dict[int, tuple[str, bool | None]] = {}


def own_process_elevated() -> bool:
    if not IS_WINDOWS:
        return False
    return bool(_process_details(OWN_PID)[1])


def foreground_window() -> WindowInfo | None:
    """Zwraca informacje o aktywnym oknie albo None (pulpit, brak okna)."""
    if not IS_WINDOWS:
        return None
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    class_buffer = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, class_buffer, 256)
    class_name = class_buffer.value
    if class_name in IGNORED_CLASSES:
        return None
    length = user32.GetWindowTextLengthW(hwnd)
    title_buffer = ctypes.create_unicode_buffer(max(1, length + 1))
    user32.GetWindowTextW(hwnd, title_buffer, length + 1)
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    details = _DETAILS_CACHE.get(pid.value)
    if details is None:
        details = _process_details(pid.value)
        if len(_DETAILS_CACHE) > 256:
            _DETAILS_CACHE.clear()
        _DETAILS_CACHE[pid.value] = details
    return WindowInfo(int(hwnd), int(pid.value), title_buffer.value, details[0], class_name, details[1])


def modifiers_down() -> bool:
    """Czy Ctrl/Alt/Shift/Win są wciśnięte? Wtedy wstrzymujemy wpisywanie,
    bo znak razem z modyfikatorem zadziałałby jak skrót klawiszowy."""
    if not IS_WINDOWS:
        return False
    return any(user32.GetAsyncKeyState(vk) & 0x8000 for vk in MODIFIER_KEYS)


def make_window_non_activating(hwnd: int):
    """Okno nie przejmie fokusu po kliknięciu — tekst nadal trafia do aplikacji docelowej."""
    if not IS_WINDOWS or not hwnd:
        return
    GWL_EXSTYLE = -20
    WS_EX_TOOLWINDOW = 0x00000080
    WS_EX_NOACTIVATE = 0x08000000
    style = _get_window_long(hwnd, GWL_EXSTYLE)
    _set_window_long(hwnd, GWL_EXSTYLE, style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)


# ---------------------------------------------------------------------------
# Wpisywanie tekstu
# ---------------------------------------------------------------------------


def _key_input(vk: int, up: bool = False):
    item = INPUT()
    item.type = INPUT_KEYBOARD
    item.union.ki = KEYBDINPUT(vk, 0, KEYEVENTF_KEYUP if up else 0, 0, 0)
    return item


def _unicode_input(unit: int, up: bool = False):
    item = INPUT()
    item.type = INPUT_KEYBOARD
    flags = KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if up else 0)
    item.union.ki = KEYBDINPUT(0, unit, flags, 0, 0)
    return item


def type_unicode(text: str) -> bool:
    """Wpisuje tekst do aktywnego okna jako znaki Unicode."""
    inputs = []
    for char in text:
        if char == "\r":
            continue
        if char == "\n":
            inputs += [_key_input(VK_RETURN), _key_input(VK_RETURN, True)]
            continue
        if char == "\t":
            inputs += [_key_input(VK_TAB), _key_input(VK_TAB, True)]
            continue
        encoded = char.encode("utf-16-le")
        for index in range(0, len(encoded), 2):
            unit = int.from_bytes(encoded[index:index + 2], "little")
            inputs += [_unicode_input(unit), _unicode_input(unit, True)]
    if not inputs:
        return True
    array = (INPUT * len(inputs))(*inputs)
    sent = user32.SendInput(len(inputs), array, ctypes.sizeof(INPUT))
    if sent != len(inputs):
        log.warning("SendInput wysłał %s z %s zdarzeń (błąd %s)", sent, len(inputs), ctypes.get_last_error())
    return sent == len(inputs)


class TypingEngine:
    """Wpisuje do aktywnego okna kolejne zatwierdzone fragmenty tekstu.

    Interfejs przekazuje pełny zatwierdzony tekst bieżącej sesji
    (``set_text``); wątek wpisuje tylko jego nową końcówkę. Gdy włączone jest
    płynne pisanie, znaki idą małymi porcjami, co daje efekt pisania na żywo,
    a przy zaległościach porcje automatycznie rosną.

    Program czeka, gdy aktywne jest jego własne okno, pulpit albo gdy
    wciśnięty jest klawisz Ctrl/Alt/Win. Po przełączeniu okna dalszy tekst
    trafia już do nowego okna.
    """

    available = IS_WINDOWS

    def __init__(self, inject=None, window_provider=None, modifiers=None):
        self.enabled = True
        self.smooth = True
        self._inject = inject or type_unicode
        self._window = window_provider or foreground_window
        self._modifiers = modifiers or modifiers_down
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._running = True
        self._text = ""
        self._done = 0
        self._target = None
        self._generation = 0
        # Pomiar latencji: on_typed(od_znaku, do_znaku, czas, czas_sendinput) po każdym
        # SendInput; on_blocked(powód, sekundy) po wznowieniu wpisywania, które czekało
        # na okno docelowe albo na puszczenie klawiszy modyfikujących.
        self.on_typed = None
        self.on_blocked = None
        self._blocked_since = None
        self._thread = threading.Thread(target=self._run, name="dyktowanie-wpisywanie", daemon=True)

    def start(self):
        if self.available:
            self._thread.start()

    def stop(self):
        self._running = False
        self._wake.set()

    def reset(self):
        """Nowa sesja dyktowania."""
        with self._lock:
            self._text = ""
            self._done = 0
            self._target = None
            self._blocked_since = None
            self._generation += 1

    def set_text(self, text: str):
        with self._lock:
            if not text.startswith(self._text[: self._done]):
                # Zatwierdzony tekst tylko rośnie; gdyby jednak się zmienił,
                # nie kasujemy niczego — dopisujemy od miejsca rozbieżności.
                self._done = min(self._done, len(text))
            self._text = text
        self._wake.set()

    @property
    def pending_chars(self) -> int:
        with self._lock:
            return max(0, len(self._text) - self._done)

    def _mark_blocked(self, reason: str):
        if self._blocked_since is None:
            self._blocked_since = (time.perf_counter(), reason)

    def _report_unblocked(self, now: float):
        blocked, self._blocked_since = self._blocked_since, None
        if blocked is not None and self.on_blocked is not None:
            try:
                self.on_blocked(blocked[1], now - blocked[0])
            except Exception:
                pass

    def _chunk(self, addition: str) -> str:
        if not self.smooth:
            return addition
        backlog = len(addition)
        if backlog <= 12:
            size = 1
        elif backlog <= 40:
            size = 2
        else:
            size = max(3, backlog // 12)
        return addition[:size]

    def _run(self):
        while self._running:
            self._wake.wait(0.05)
            self._wake.clear()
            try:
                self.step()
            except Exception:
                log.exception("Błąd wpisywania")
                time.sleep(0.2)

    def step(self) -> bool:
        """Jedna porcja pracy; zwraca True, jeśli coś wpisano."""
        with self._lock:
            text, done, generation = self._text, self._done, self._generation
        if not self.enabled:
            with self._lock:
                if generation == self._generation:
                    self._done = len(self._text)  # w pauzie tekst nie trafia do okna
            return False
        addition = text[done:]
        if not addition:
            return False
        window = self._window()
        if window is None or window.own:
            self._mark_blocked("brak okna docelowego (pulpit / okno programu)")
            return False  # czekamy, aż użytkownik wróci do okna docelowego
        if window.hwnd != self._target:
            self._target = window.hwnd
            log.info("Okno docelowe: %s [%s]", window.label, window.process)
        if self._modifiers():
            self._mark_blocked("wciśnięty Ctrl/Alt/Shift/Win")
            return False
        chunk = self._chunk(addition)
        inject_started = time.perf_counter()
        if not self._inject(chunk):
            time.sleep(0.1)
            return False
        injected = time.perf_counter()
        self._report_unblocked(injected)
        current = False
        with self._lock:
            if generation == self._generation:
                self._done = done + len(chunk)
                current = True
        if current and self.on_typed is not None:
            try:
                self.on_typed(done, done + len(chunk), injected, injected - inject_started)
            except Exception:
                pass
        if len(chunk) < len(addition):
            if self.smooth:
                time.sleep(0.012)
            self._wake.set()
        return True


# ---------------------------------------------------------------------------
# Globalny skrót klawiszowy
# ---------------------------------------------------------------------------

HOTKEYS = {
    "Ctrl+Alt+Spacja": (MOD_CONTROL | MOD_ALT, 0x20),
    "Ctrl+Alt+D": (MOD_CONTROL | MOD_ALT, 0x44),
    "Ctrl+Shift+Spacja": (MOD_CONTROL | MOD_SHIFT, 0x20),
    "F8": (0, 0x77),
    "F9": (0, 0x78),
    "Pause": (0, 0x13),
    "Scroll Lock": (0, 0x91),
}
DEFAULT_HOTKEY = "Ctrl+Alt+Spacja"


class HotkeyListener:
    """Globalny skrót (RegisterHotKey): działa także wtedy, gdy aktywne jest inne okno.

    W trybie „przytrzymaj” po naciśnięciu skrótu czekamy na puszczenie tego
    samego klawisza i zgłaszamy to osobno — a w trakcie tekst wpisuje się na
    bieżąco, tak samo jak w trybie start/stop.
    """

    available = IS_WINDOWS

    def __init__(self, combo: str, on_press, on_release, hold_mode: bool = False):
        self.combo = combo if combo in HOTKEYS else DEFAULT_HOTKEY
        self.on_press = on_press
        self.on_release = on_release
        self.hold_mode = hold_mode
        self.ok = False
        self._thread_id = None
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, name="dyktowanie-skrot", daemon=True)

    def start(self) -> bool:
        if not self.available:
            return False
        self._thread.start()
        self._ready.wait(2.0)
        return self.ok

    def stop(self):
        if self._thread_id and self._thread.is_alive():
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            self._thread.join(timeout=1.0)

    def _run(self):
        self._thread_id = kernel32.GetCurrentThreadId()
        message = wintypes.MSG()
        # Tworzy kolejkę komunikatów wątku, zanim ktokolwiek wyśle WM_QUIT.
        user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 0)
        modifiers, vk = HOTKEYS[self.combo]
        self.ok = bool(user32.RegisterHotKey(None, 1, modifiers | MOD_NOREPEAT, vk))
        if not self.ok:
            log.warning("Nie udało się zarejestrować skrótu %s (błąd %s)", self.combo, ctypes.get_last_error())
        self._ready.set()
        if not self.ok:
            return
        try:
            while True:
                result = user32.GetMessageW(ctypes.byref(message), None, 0, 0)
                if result in (0, -1):
                    break
                if message.message != WM_HOTKEY:
                    continue
                self.on_press()
                if self.hold_mode:
                    quit_requested = self._hold(vk)
                    self.on_release()
                    if quit_requested:
                        break
        finally:
            user32.UnregisterHotKey(None, 1)

    def _hold(self, vk: int) -> bool:
        """Czeka na puszczenie klawisza skrótu, nie blokując wpisywania na żywo.

        Przytrzymane Ctrl/Alt/Shift/Win są dla systemu „puszczane” (sztuczne
        zdarzenie), więc tekst może trafiać do okna od razu, a nie dopiero po
        puszczeniu skrótu. Żeby autopowtarzanie przytrzymanego klawisza (np.
        spacji) nie wpisywało się do okna, na czas przytrzymania działa hak
        klawiatury, który pomija jego kolejne naciśnięcia. Zwraca True, gdy
        w międzyczasie przyszło polecenie zakończenia wątku.
        """
        def hook(n_code, w_param, l_param):
            if n_code == 0 and w_param in (WM_KEYDOWN, WM_SYSKEYDOWN):
                info = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                if info.vkCode == vk and not info.flags & LLKHF_INJECTED:
                    return 1
            return user32.CallNextHookEx(None, n_code, w_param, l_param)

        proc = HOOKPROC(hook)  # referencja musi żyć do końca przytrzymania
        handle = user32.SetWindowsHookExW(WH_KEYBOARD_LL, proc, kernel32.GetModuleHandleW(None), 0)
        if not handle:
            log.warning("Nie udało się założyć haka klawiatury (błąd %s)", ctypes.get_last_error())
        try:
            self._release_modifiers()
            message = wintypes.MSG()
            while user32.GetAsyncKeyState(vk) & 0x8000:
                user32.MsgWaitForMultipleObjects(0, None, False, 20, QS_ALLINPUT)
                while user32.PeekMessageW(ctypes.byref(message), None, 0, 0, PM_REMOVE):
                    if message.message == WM_QUIT:
                        return True
        finally:
            if handle:
                user32.UnhookWindowsHookEx(handle)
        return False

    @staticmethod
    def _release_modifiers():
        held = [(key, ext) for key, ext in SIDE_MODIFIERS if user32.GetAsyncKeyState(key) & 0x8000]
        if not held:
            return
        inputs = [_key_input(VK_DUMMY), _key_input(VK_DUMMY, True)]
        for key, ext in held:
            item = _key_input(key, True)
            if ext:
                item.union.ki.dwFlags |= KEYEVENTF_EXTENDEDKEY
            inputs.append(item)
        array = (INPUT * len(inputs))(*inputs)
        user32.SendInput(len(inputs), array, ctypes.sizeof(INPUT))
