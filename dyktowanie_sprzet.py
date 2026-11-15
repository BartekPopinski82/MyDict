"""Rozpoznanie sprzętu: jaka karta graficzna jest w komputerze i co z tego wynika.

Program ma dwa silniki rozpoznawania mowy:

* faster-whisper (CTranslate2) — karty NVIDIA przez CUDA, a bez karty: procesor;
* whisper.cpp — karty AMD i Intel przez Vulkan (Windows) oraz Mac przez Metal.

Ten moduł ustala, który silnik i który model pasują do danego komputera.
"""

from __future__ import annotations

import logging
import platform
import sys
from dataclasses import dataclass


log = logging.getLogger("dyktowanie.sprzet")
IS_WINDOWS = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"

# Poniżej tej ilości pamięci karty model Turbo może się nie zmieścić obok innych programów.
MIN_VRAM_MB_FOR_TURBO = 2500
MODEL_FAST_GPU = "turbo"
MODEL_CPU = "small"

VENDOR_NAMES = {
    "nvidia": "NVIDIA",
    "amd": "AMD",
    "intel": "Intel",
    "apple": "Apple",
    "other": "inna",
}


def vendor_of(name: str) -> str:
    text = name.casefold()
    if "nvidia" in text or "geforce" in text or "quadro" in text or "rtx" in text or "gtx" in text:
        return "nvidia"
    if "amd" in text or "radeon" in text or "ati " in text:
        return "amd"
    if "intel" in text or "iris" in text or "uhd graphics" in text or "arc(" in text:
        return "intel"
    if "apple" in text:
        return "apple"
    return "other"


def _windows_cards() -> list[str]:
    import ctypes
    from ctypes import wintypes

    class DISPLAY_DEVICEW(ctypes.Structure):
        _fields_ = (
            ("cb", wintypes.DWORD), ("DeviceName", wintypes.WCHAR * 32),
            ("DeviceString", wintypes.WCHAR * 128), ("StateFlags", wintypes.DWORD),
            ("DeviceID", wintypes.WCHAR * 128), ("DeviceKey", wintypes.WCHAR * 128),
        )

    user32 = ctypes.WinDLL("user32")
    user32.EnumDisplayDevicesW.argtypes = (
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(DISPLAY_DEVICEW), wintypes.DWORD
    )
    user32.EnumDisplayDevicesW.restype = wintypes.BOOL
    names: list[str] = []
    index = 0
    while index < 64:
        device = DISPLAY_DEVICEW()
        device.cb = ctypes.sizeof(device)
        if not user32.EnumDisplayDevicesW(None, index, ctypes.byref(device), 0):
            break
        index += 1
        name = device.DeviceString.strip()
        mirroring = device.StateFlags & 0x00000008   # sterowniki zdalnego pulpitu itp.
        if name and not mirroring and name not in names:
            names.append(name)
    return names


def _windows_cards_registry() -> list[str]:
    """Karty bez podłączonego ekranu (np. NVIDIA w laptopie) nie zawsze widać przez
    EnumDisplayDevices — klasa sterowników „Display” w rejestrze ma je wszystkie."""
    import winreg

    names: list[str] = []
    path = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as root:
            for index in range(32):
                try:
                    sub = winreg.EnumKey(root, index)
                except OSError:
                    break
                try:
                    with winreg.OpenKey(root, sub) as key:
                        name = str(winreg.QueryValueEx(key, "DriverDesc")[0]).strip()
                except OSError:
                    continue
                if name and name not in names:
                    names.append(name)
    except OSError:
        pass
    return names


def graphics_cards() -> list[str]:
    """Nazwy kart graficznych w komputerze (bez wirtualnych sterowników ekranu)."""
    names: list[str] = []
    try:
        if IS_WINDOWS:
            for name in _windows_cards() + _windows_cards_registry():
                if name not in names:
                    names.append(name)
        elif IS_MAC:
            if platform.machine().lower() in ("arm64", "aarch64"):
                names.append("Apple Silicon")
    except Exception:
        log.debug("Nie odczytano listy kart graficznych", exc_info=True)
    virtual = ("basic render", "basic display", "remote display", "virtual", "parsec", "citrix", "meta ")
    return [name for name in names if not any(mark in name.casefold() for mark in virtual)]


def nvidia_vram_mb() -> int | None:
    """Pamięć pierwszej karty NVIDIA w MB (None, gdy nie da się odczytać)."""
    try:
        import pynvml

        pynvml.nvmlInit()
        try:
            handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            return int(pynvml.nvmlDeviceGetMemoryInfo(handle).total / (1024 * 1024))
        finally:
            pynvml.nvmlShutdown()
    except Exception:
        return None


@dataclass(frozen=True)
class Hardware:
    cards: tuple          # nazwy wszystkich kart
    vendor: str           # najlepsza do rozpoznawania: nvidia / amd / intel / apple / other / none
    accelerated: bool     # czy działa NVIDIA CUDA (pierwszy silnik)
    vram_mb: int | None = None
    second_engine: bool = False   # czy do programu dołączony jest whisper.cpp (Vulkan / Metal)

    @property
    def backend(self) -> str:
        """Czym będzie liczone rozpoznawanie: cuda / vulkan / metal / cpu."""
        if self.accelerated:
            return "cuda"
        if self.second_engine and self.vendor == "apple":
            return "metal"
        if self.second_engine and self.vendor in ("amd", "intel", "nvidia"):
            return "vulkan"
        return "cpu"

    @property
    def card(self) -> str:
        """Nazwa karty, o której mówimy użytkownikowi."""
        for name in self.cards:
            if vendor_of(name) == self.vendor:
                return name
        return self.cards[0] if self.cards else ""

    @property
    def recommended_model(self) -> str:
        if self.accelerated and (self.vram_mb is None or self.vram_mb >= MIN_VRAM_MB_FOR_TURBO):
            return MODEL_FAST_GPU
        if self.backend == "metal":
            return MODEL_FAST_GPU      # układy Apple mają szybką kartę i wspólną, dużą pamięć
        return MODEL_CPU               # Vulkan na kartach zintegrowanych i procesor: model lekki

    @property
    def summary(self) -> str:
        """Jedno zdanie dla użytkownika: co wykryto i jak będzie działać rozpoznawanie."""
        if self.accelerated:
            memory = f", {self.vram_mb / 1024:.0f} GB" if self.vram_mb else ""
            return f"Wykryto kartę {self.card}{memory} — rozpoznawanie mowy liczy karta graficzna."
        if self.backend == "metal":
            return "Wykryto Apple Silicon — rozpoznawanie mowy liczy karta graficzna (Metal)."
        if self.backend == "vulkan" and self.vendor == "nvidia":
            return (f"Wykryto kartę {self.card}, ale jej sterownik nie udostępnia CUDA — "
                    "rozpoznawanie liczy karta przez Vulkan. Aktualizacja sterownika NVIDIA przyspieszy pracę.")
        if self.backend == "vulkan":
            return f"Wykryto kartę {self.card} — rozpoznawanie mowy liczy karta graficzna (Vulkan)."
        if self.vendor == "nvidia":
            return (f"Wykryto kartę {self.card}, ale jej sterownik nie udostępnia CUDA — "
                    "rozpoznawanie liczy procesor. Zaktualizuj sterownik NVIDIA.")
        if self.vendor in ("amd", "intel"):
            return (f"Wykryto kartę {self.card}. Karty {VENDOR_NAMES[self.vendor]} nie przyspieszają "
                    "rozpoznawania mowy — liczy je procesor, dlatego wybrano lżejszy model.")
        if self.vendor == "apple":
            return "Wykryto Apple Silicon — rozpoznawanie mowy liczy procesor, dlatego wybrano lżejszy model."
        return "Nie wykryto karty graficznej przyspieszającej rozpoznawanie — liczy je procesor."


def detect(cuda_devices: int | None = None, cards: list[str] | None = None,
           vram_mb: int | None = None, second_engine: bool | None = None) -> Hardware:
    """Rozpoznaje sprzęt. ``cuda_devices`` — liczba kart widzianych przez CTranslate2
    (None = sprawdź teraz); parametry pozwalają też podać dane w testach."""
    cards = graphics_cards() if cards is None else cards
    if cuda_devices is None:
        try:
            import ctranslate2

            cuda_devices = 0 if IS_MAC else int(ctranslate2.get_cuda_device_count())
        except Exception:
            cuda_devices = 0
    vendors = [vendor_of(name) for name in cards]
    accelerated = cuda_devices > 0 and not IS_MAC
    if accelerated and "nvidia" not in vendors:
        cards = ["NVIDIA (CUDA)"] + list(cards)
        vendors = ["nvidia"] + vendors
    vendor = next((v for v in ("nvidia", "amd", "intel", "apple", "other") if v in vendors), "none")
    if accelerated and vram_mb is None:
        vram_mb = nvidia_vram_mb()
    if second_engine is None:
        try:
            import dyktowanie_whispercpp

            second_engine = dyktowanie_whispercpp.available()
        except Exception:
            second_engine = False
    hardware = Hardware(tuple(cards), vendor, accelerated, vram_mb if accelerated else None, bool(second_engine))
    log.info("Sprzęt: karty=%s, wybór=%s, liczy=%s, VRAM=%s MB",
             list(cards), vendor, hardware.backend, hardware.vram_mb)
    return hardware
