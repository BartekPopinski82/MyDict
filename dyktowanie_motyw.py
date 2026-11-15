"""Wygląd programu: motyw jasny i ciemny, arkusz stylów, typografia.

Jedno źródło kolorów. Kontrolki rysowane ręcznie pytają o kolor w chwili
rysowania (``color("accent")``), więc zmiana motywu działa od razu, bez
ponownego uruchamiania.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication

from dyktowanie_app import IS_MAC, IS_WINDOWS, data_dir


log = logging.getLogger("dyktowanie.motyw")

LIGHT = {
    "name": "light",
    "bg": "#F4F6FA",
    "surface": "#FFFFFF",
    "surface_alt": "#F7F9FC",
    "field": "#FFFFFF",
    "border": "#E1E7F0",
    "border_strong": "#CBD5E3",
    "hover": "#EEF3FA",
    "text": "#0F172A",
    "text_muted": "#55627A",
    "text_faint": "#8792A6",
    "accent": "#0A6CF0",
    "accent_hover": "#0959C7",
    "accent_soft": "#E8F1FE",
    "on_accent": "#FFFFFF",
    "cyan": "#17B8D6",
    "danger": "#E5484D",
    "danger_soft": "#FDECEE",
    "danger_text": "#B4232A",
    "success_soft": "#E6F6EE",
    "success_text": "#17764B",
    "warning_soft": "#FFF4DC",
    "warning_text": "#8A5A00",
    "neutral": "#8A97AD",
    "wave_idle": "#D5DEEA",
    "scroll": "#C5D0DD",
}

DARK = {
    "name": "dark",
    "bg": "#0F141C",
    "surface": "#171D28",
    "surface_alt": "#1C2330",
    "field": "#121821",
    "border": "#273142",
    "border_strong": "#364359",
    "hover": "#222B3A",
    "text": "#E8EDF5",
    "text_muted": "#A3AEC2",
    "text_faint": "#6F7C93",
    "accent": "#3D8BFF",
    "accent_hover": "#5B9DFF",
    "accent_soft": "#17304F",
    "on_accent": "#FFFFFF",
    "cyan": "#2CC7E3",
    "danger": "#F0555F",
    "danger_soft": "#3A1C22",
    "danger_text": "#FF8A92",
    "success_soft": "#133426",
    "success_text": "#5DD39E",
    "warning_soft": "#3A2E12",
    "warning_text": "#F2C265",
    "neutral": "#6F7C93",
    "wave_idle": "#334058",
    "scroll": "#3A475C",
}

# Pływający pasek jest ciemny w obu motywach — leży na cudzych oknach.
OVERLAY = {
    "bg": "#0C152E",
    "text": "#FFFFFF",
    "text_muted": "#9FB3D1",
    "tentative": "#8FA3C6",
    "placeholder": "#7F91B2",
    "icon": "#C9D5E8",
    "wave_idle": "#3B4A68",
    "hint": "#F5B14C",
}

MODES = {"auto": "Jak w systemie", "light": "Jasny", "dark": "Ciemny"}

_current = LIGHT


def theme() -> dict:
    return _current


def color(name: str) -> str:
    return _current[name]


def qcolor(name: str) -> QColor:
    return QColor(_current[name])


def is_dark() -> bool:
    return _current is DARK


# ---------------------------------------------------------------------------
# Obrazki używane przez arkusz stylów (strzałka listy, znaczek zaznaczenia)
# ---------------------------------------------------------------------------

_GLYPHS = {
    "chevron": '<path d="M6 9l6 6 6-6"/>',
    "check": '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
}


def _glyph_file(name: str, stroke: str, width: float = 2.2) -> str:
    """Zapisuje ikonę SVG w katalogu danych i zwraca ścieżkę do użycia w ``url()``."""
    directory = data_dir() / "motyw"
    path = directory / f"{name}-{stroke.lstrip('#')}.svg"
    try:
        if not path.is_file():
            directory.mkdir(parents=True, exist_ok=True)
            path.write_text(
                f'<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" '
                f'stroke="{stroke}" stroke-width="{width}" stroke-linecap="round" stroke-linejoin="round">'
                f"{_GLYPHS[name]}</svg>",
                encoding="utf-8",
            )
    except OSError:
        log.debug("Nie zapisano ikony motywu %s", path, exc_info=True)
    return Path(path).as_posix()


# ---------------------------------------------------------------------------
# Arkusz stylów
# ---------------------------------------------------------------------------


def stylesheet(t: dict | None = None) -> str:
    t = t or _current
    o = OVERLAY
    chevron = _glyph_file("chevron", t["text_muted"])
    check = _glyph_file("check", t["on_accent"], 2.8)
    return f"""
QWidget {{ color: {t['text']}; font-size: 13px; }}
QMainWindow, QDialog {{ background: {t['bg']}; }}
QScrollArea {{ background: transparent; border: 0; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}

QLabel {{ background: transparent; }}
QLabel[pageTitle="true"] {{ font-size: 21px; font-weight: 700; color: {t['text']}; }}
QLabel[pageSubtitle="true"] {{ font-size: 13px; color: {t['text_muted']}; }}
QLabel[cardTitle="true"] {{ font-size: 15px; font-weight: 700; color: {t['text']}; }}
QLabel[sectionTitle="true"] {{ font-size: 15px; font-weight: 700; color: {t['text']}; padding-bottom: 2px; }}
QLabel[groupTitle="true"] {{
    font-size: 11px; font-weight: 700; color: {t['text_faint']}; letter-spacing: 0.8px; padding-top: 10px;
}}
QLabel[controlTitle="true"] {{ font-size: 15px; font-weight: 700; color: {t['text']}; }}
QLabel[fieldLabel="true"] {{ font-size: 12px; font-weight: 600; color: {t['text_muted']}; padding-top: 6px; }}
QLabel[helper="true"] {{ font-size: 12px; color: {t['text_faint']}; }}
QLabel[link="true"] {{ font-size: 12px; color: {t['text_faint']}; }}
QLabel[targetLabel="true"] {{
    font-size: 12px; color: {t['text_muted']}; background: {t['surface_alt']};
    border: 1px solid {t['border']}; border-radius: 8px; padding: 6px 10px;
}}
QLabel[pill] {{ border-radius: 12px; padding: 4px 12px; font-size: 12px; font-weight: 600; }}
QLabel[pill="busy"] {{ background: {t['hover']}; color: {t['text_muted']}; }}
QLabel[pill="ready"] {{ background: {t['success_soft']}; color: {t['success_text']}; }}
QLabel[pill="live"] {{ background: {t['danger_soft']}; color: {t['danger_text']}; }}
QLabel[pill="error"] {{ background: {t['danger_soft']}; color: {t['danger_text']}; }}

QFrame[card="true"] {{ background: {t['surface']}; border: 1px solid {t['border']}; border-radius: 14px; }}
QFrame[divider="true"] {{ background: {t['border']}; border: 0; max-height: 1px; min-height: 1px; }}
QFrame[banner] {{ border-radius: 12px; border: 1px solid {t['border']}; background: {t['surface']}; }}
QFrame[banner="info"] {{ background: {t['accent_soft']}; border-color: {t['accent_soft']}; }}
QFrame[banner="warning"] {{ background: {t['warning_soft']}; border-color: {t['warning_soft']}; }}
QLabel[bannerTitle="true"] {{ font-weight: 700; color: {t['text']}; }}
QLabel[bannerText="true"] {{ font-size: 12px; color: {t['text_muted']}; }}

QTextEdit[transcript="true"] {{
    background: {t['surface_alt']}; border: 1px solid {t['border']}; border-radius: 10px; padding: 12px;
    font-size: 15px; color: {t['text']}; selection-background-color: {t['accent']};
    selection-color: {t['on_accent']};
}}
QTextEdit[notes="true"] {{
    background: {t['surface_alt']}; border: 1px solid {t['border']}; border-radius: 10px; padding: 10px;
    font-size: 13px; color: {t['text']};
}}

QLineEdit, QComboBox {{
    min-height: 32px; padding: 0 10px; background: {t['field']}; color: {t['text']};
    border: 1px solid {t['border_strong']}; border-radius: 8px;
    selection-background-color: {t['accent']}; selection-color: {t['on_accent']};
}}
QLineEdit:hover, QComboBox:hover {{ border-color: {t['text_faint']}; }}
QLineEdit:focus, QComboBox:focus {{ border: 1px solid {t['accent']}; }}
QLineEdit:disabled, QComboBox:disabled {{ color: {t['text_faint']}; background: {t['surface_alt']}; }}
QComboBox::drop-down {{ border: 0; width: 28px; }}
QComboBox::down-arrow {{ image: url("{chevron}"); width: 14px; height: 14px; }}
QComboBox QAbstractItemView {{
    background: {t['surface']}; color: {t['text']}; border: 1px solid {t['border_strong']};
    border-radius: 8px; padding: 4px; outline: 0;
    selection-background-color: {t['accent_soft']}; selection-color: {t['text']};
}}
QComboBox QAbstractItemView::item {{ min-height: 28px; padding: 0 8px; border-radius: 6px; }}

QPushButton {{
    min-height: 32px; padding: 0 14px; border: 1px solid {t['border_strong']};
    border-radius: 8px; background: {t['surface']}; color: {t['text']}; font-weight: 600;
}}
QPushButton:hover {{ background: {t['hover']}; }}
QPushButton:pressed {{ background: {t['border']}; }}
QPushButton:focus {{ border-color: {t['accent']}; }}
QPushButton:disabled {{ color: {t['text_faint']}; background: {t['surface_alt']}; border-color: {t['border']}; }}
QPushButton[primary="true"] {{ color: {t['on_accent']}; background: {t['accent']}; border-color: {t['accent']}; }}
QPushButton[primary="true"]:hover {{ background: {t['accent_hover']}; border-color: {t['accent_hover']}; }}
QPushButton[primary="true"]:disabled {{ color: {t['text_faint']}; background: {t['hover']}; border-color: {t['border']}; }}
QPushButton[secondary="true"] {{ color: {t['accent']}; border-color: {t['accent_soft']}; background: {t['accent_soft']}; }}
QPushButton[secondary="true"]:hover {{ border-color: {t['accent']}; }}
QPushButton[flat="true"] {{ border: 0; background: transparent; color: {t['text_muted']}; padding: 0 8px; }}
QPushButton[flat="true"]:hover {{ background: {t['hover']}; color: {t['text']}; }}
QPushButton[segment] {{
    border-radius: 0; background: {t['surface_alt']}; border-color: {t['border_strong']};
    padding: 0 8px; font-size: 12px; color: {t['text_muted']};
}}
QPushButton[segment="first"] {{ border-top-left-radius: 8px; border-bottom-left-radius: 8px; }}
QPushButton[segment="last"] {{ border-top-right-radius: 8px; border-bottom-right-radius: 8px; border-left: 0; }}
QPushButton[segment]:checked {{ background: {t['accent']}; color: {t['on_accent']}; border-color: {t['accent']}; }}

QCheckBox {{ spacing: 9px; padding: 3px 0; }}
QCheckBox:disabled {{ color: {t['text_faint']}; }}
QCheckBox::indicator {{
    width: 17px; height: 17px; border: 1px solid {t['border_strong']}; border-radius: 5px; background: {t['field']};
}}
QCheckBox::indicator:hover {{ border-color: {t['accent']}; }}
QCheckBox::indicator:checked {{ background: {t['accent']}; border-color: {t['accent']}; image: url("{check}"); }}
QCheckBox::indicator:disabled {{ background: {t['surface_alt']}; border-color: {t['border']}; }}
QCheckBox::indicator:checked:disabled {{ background: {t['text_faint']}; border-color: {t['text_faint']}; }}

QProgressBar {{
    min-height: 6px; max-height: 6px; border: 0; border-radius: 3px;
    background: {t['border']}; text-align: center; color: transparent;
}}
QProgressBar::chunk {{ border-radius: 3px; background: {t['accent']}; }}

QScrollBar:vertical {{ width: 10px; background: transparent; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {t['scroll']}; border-radius: 3px; min-height: 28px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
QScrollBar:horizontal {{ height: 0; }}

QMenu {{ background: {t['surface']}; border: 1px solid {t['border_strong']}; border-radius: 8px; padding: 5px; }}
QMenu::item {{ padding: 6px 22px 6px 12px; border-radius: 6px; color: {t['text']}; }}
QMenu::item:selected {{ background: {t['accent_soft']}; }}
QMenu::item:disabled {{ color: {t['text_faint']}; }}
QMenu::separator {{ height: 1px; background: {t['border']}; margin: 5px 8px; }}
QToolTip {{
    color: {t['text']}; background: {t['surface']}; border: 1px solid {t['border_strong']};
    border-radius: 6px; padding: 5px 7px; font-size: 12px;
}}
QMessageBox {{ background: {t['surface']}; }}

QLabel[overlayStatus="true"] {{ color: {o['text']}; font-size: 12px; font-weight: 700; }}
QLabel[overlayTarget="true"] {{ color: {o['text_muted']}; font-size: 11px; }}
QLabel[overlayText="true"] {{ color: {o['text']}; font-size: 15px; }}
QLabel[overlayHint="true"] {{ color: {o['hint']}; font-size: 10px; }}
QPushButton[overlayTool="true"] {{ min-height: 0; padding: 0; border: 0; border-radius: 15px; background: transparent; }}
QPushButton[overlayTool="true"]:hover {{ background: rgba(255, 255, 255, 28); }}
QPushButton[overlayTool="true"]:checked {{ background: transparent; }}
QPushButton[overlayTool="true"][paused="true"] {{ background: rgba(245, 177, 76, 60); }}
"""


# ---------------------------------------------------------------------------
# Zastosowanie
# ---------------------------------------------------------------------------


def app_font() -> QFont:
    if IS_WINDOWS:
        families = set(QFontDatabase.families())
        family = "Segoe UI Variable Text" if "Segoe UI Variable Text" in families else "Segoe UI"
        font = QFont(family)
    else:
        font = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont)
    font.setPixelSize(13)
    if not IS_MAC:
        font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    return font


def _palette(t: dict) -> QPalette:
    palette = QPalette()
    roles = QPalette.ColorRole
    for role, key in (
        (roles.Window, "bg"), (roles.WindowText, "text"), (roles.Base, "field"),
        (roles.AlternateBase, "surface_alt"), (roles.Text, "text"), (roles.Button, "surface"),
        (roles.ButtonText, "text"), (roles.Highlight, "accent"), (roles.HighlightedText, "on_accent"),
        (roles.ToolTipBase, "surface"), (roles.ToolTipText, "text"), (roles.PlaceholderText, "text_faint"),
        (roles.Link, "accent"),
    ):
        palette.setColor(role, QColor(t[key]))
    for role in (roles.Text, roles.WindowText, roles.ButtonText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(t["text_faint"]))
    return palette


def set_mode(app: QApplication, mode: str):
    """Wybór użytkownika: ``auto`` (jak system), ``light`` albo ``dark``."""
    scheme = {"light": Qt.ColorScheme.Light, "dark": Qt.ColorScheme.Dark}.get(mode, Qt.ColorScheme.Unknown)
    try:
        app.styleHints().setColorScheme(scheme)
    except AttributeError:   # starsze Qt: bez wymuszania motywu
        pass
    apply(app, mode)


def apply(app: QApplication, mode: str = "auto") -> dict:
    """Ustawia paletę i arkusz stylów zgodnie z motywem systemu albo wyborem użytkownika."""
    global _current
    if mode == "dark":
        dark = True
    elif mode == "light":
        dark = False
    else:
        dark = app.styleHints().colorScheme() == Qt.ColorScheme.Dark
    _current = DARK if dark else LIGHT
    app.setPalette(_palette(_current))
    app.setStyleSheet(stylesheet(_current))
    return _current
