"""Co nie może trafić do zbudowanego programu — jedno źródło dla budowania i listy licencji.

``EXCLUDED_PACKAGES``: pakiety Pythona, które zależności importują, ale program ich nie używa.
PyInstaller ich nie dołącza, a w zbudowanym programie zastępuje je zaślepka (import się udaje,
dopiero użycie zgłasza błąd). Lista licencji ich nie wymienia, bo nie są rozpowszechniane.

``GPL_BINARY_MARKERS``: fragmenty nazw bibliotek na licencji GPL. Program jest freeware
z zamkniętym kodem, więc żadna z nich nie może znaleźć się w pakiecie — budowanie przerywa się,
gdy któraś się pojawi (np. jako zależność nowej wersji pakietu).
"""

EXCLUDED_PACKAGES = {
    # PyAV (FFmpeg): faster-whisper importuje go tylko do odczytu plików audio, a MyDict podaje
    # mu dźwięk z mikrofonu jako tablicę próbek. Pakiet z PyPI zawiera kodery wideo x264/x265 (GPL).
    "av": "odczyt plików audio (PyAV/FFmpeg) nie jest częścią tej wersji programu",
}

GPL_BINARY_MARKERS = ("x264", "x265", "xvid", "postproc", "fdk-aac", "rubberband", "vidstab", "frei0r")


def gpl_license(expression: str) -> bool:
    """Licencja wyłącznie GPL (bez wariantu LGPL ani wyjątku pozwalającego na zamknięty program)."""
    text = expression.upper()
    if "GPL" not in text:
        return False
    return not any(word in text for word in ("LGPL", "LESSER", "EXCEPTION", "WYJĄTK"))
