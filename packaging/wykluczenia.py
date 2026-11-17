"""Co nie może trafić do zbudowanego programu — jedno źródło dla budowania i listy licencji.

``EXCLUDED_PACKAGES``: pakiety Pythona, które zależności importują, ale program ich nie używa.
PyInstaller ich nie dołącza, a w zbudowanym programie zastępuje je zaślepka (import się udaje,
dopiero użycie zgłasza ImportError, więc biblioteki traktują pakiet jak niezainstalowany). Lista licencji ich nie wymienia, bo nie są rozpowszechniane.

``GPL_BINARY_MARKERS``: fragmenty nazw bibliotek na licencji GPL. Program jest freeware
z zamkniętym kodem, więc żadna z nich nie może znaleźć się w pakiecie — budowanie przerywa się,
gdy któraś się pojawi (np. jako zależność nowej wersji pakietu).
"""

EXCLUDED_PACKAGES = {
    # PyAV (FFmpeg): faster-whisper importuje go tylko do odczytu plików audio, a MyDict podaje
    # mu dźwięk z mikrofonu jako tablicę próbek. Pakiet z PyPI zawiera kodery wideo x264/x265 (GPL).
    "av": "odczyt plików audio (PyAV/FFmpeg) nie jest częścią tej wersji programu",
}

# Moduły, których program nie używa, a które PyInstaller dołącza „na wszelki wypadek”
# (bez zaślepki — nic ich nie importuje w trakcie działania).
EXCLUDED_MODULES = (
    "PySide6.QtNetwork",   # program nie korzysta z sieci Qt (sieć: urllib/websockets)
    "setuptools", "pkg_resources", "distutils",   # narzędzia instalacji pakietów
)

# Pliki dołączane przez hooki Qt, zbędne w programie na samych widżetach (ścieżki w pakiecie,
# porównanie bez wielkości liter, separator „/”).
EXCLUDED_FILES = (
    "pyside6/opengl32sw.dll",           # programowy OpenGL (20 MB) — program nie używa OpenGL
    "pyside6/translations/",            # tłumaczenia Qt — program ich nie wczytuje (własne napisy PL)
    "pyside6/plugins/networkinformation/",
    "pyside6/plugins/tls/",
    "pyside6/plugins/generic/",         # dotyk przez sieć (TUIO) — wczytywany tylko na żądanie
    "pyside6/qt6network.dll",           # potrzebny tylko wtyczkom wyżej
)

GPL_BINARY_MARKERS =("x264", "x265", "xvid", "postproc", "fdk-aac", "rubberband", "vidstab", "frei0r")


def gpl_license(expression: str) -> bool:
    """Licencja wyłącznie GPL (bez wariantu LGPL ani wyjątku pozwalającego na zamknięty program)."""
    text = expression.upper()
    if "GPL" not in text:
        return False
    return not any(word in text for word in ("LGPL", "LESSER", "EXCEPTION", "WYJĄTK"))
