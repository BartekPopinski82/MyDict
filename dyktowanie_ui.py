"""Interfejs Qt dyktowania na żywo: okno główne i pływający pasek."""

from __future__ import annotations

import html
import logging
import math
import os
import sys
import threading
import time
from collections import deque
from pathlib import Path

from PySide6.QtCore import QByteArray, QObject, QPoint, QRectF, QSettings, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QAction,
    QColor,
    QDesktopServices,
    QGuiApplication,
    QIcon,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSystemTrayIcon,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

import dyktowanie_aktualizacje as updates
import dyktowanie_gpu_pakiet as gpu_pack
import dyktowanie_motyw as motyw
import dyktowanie_sprzet as sprzet
from dyktowanie_app import (
    APP_ID,
    APP_NAME,
    LEGACY_NAME,
    ORGANIZATION,
    __version__,
    data_dir,
    log_dir,
    resource_dir,
)
from dyktowanie_asr import (
    DEFAULT_MODEL,
    LANGUAGES,
    MODELS,
    DictationEngine,
    EngineCallbacks,
    Microphone,
    ModelInfo,
    batch_transcription_running,
    gpu_acceleration_state,
    reset_gpu_libraries,
)
from dyktowanie_api import CLOUD_MODELS, is_cloud_key
from dyktowanie_api_ui import ApiKeysDialog, ApiStore, CustomModelDialog
from dyktowanie_core import SessionText, display_tail, level_to_unit
from dyktowanie_latencja import tracker as latency
from dyktowanie_platforma import (
    DEFAULT_HOTKEY,
    HOTKEYS,
    HOTKEY_SUPPORTED,
    IS_MAC,
    IS_WINDOWS,
    TRAY_HINT,
    TYPING_SUPPORTED,
    HotkeyListener,
    TypingEngine,
    foreground_window,
    make_window_non_activating,
    own_process_elevated,
    request_typing_permission,
    typing_permission_granted,
)
from dyktowanie_pobieranie import Cancelled, DownloadError


log = logging.getLogger("dyktowanie.ui")
APP_TITLE = APP_NAME
LOGO_FILE = resource_dir() / "zasoby" / "logo.svg"

PAUSE_OPTIONS = [
    (0.5, "Krótka — 0,5 s (szybciej, może dzielić zdania)"),
    (0.7, "Normalna — 0,7 s (zalecana)"),
    (1.0, "Dłuższa — 1 s (dla spokojnego dyktowania)"),
    (1.5, "Długa — 1,5 s"),
]

SVG_PATHS = {
    "mic": '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0"/><path d="M12 17.5V21M8.5 21h7"/>',
    "keyboard": '<rect x="2.5" y="6" width="19" height="12" rx="2"/><path d="M6 10h.01M9 10h.01M12 10h.01M15 10h.01M18 10h.01M7 14h10"/>',
    "keyboard-off": '<rect x="2.5" y="6" width="19" height="12" rx="2"/><path d="M7 14h10"/><path d="M3 3l18 18"/>',
    "gear": '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1A1.7 1.7 0 0 0 4.6 9a1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
    "close": '<path d="M6 6l12 12M18 6 6 18"/>',
    "copy": '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3"/>',
    "save": '<path d="M5 3h11l3 3v15H5z"/><path d="M8 3v5h7V3M8 21v-7h8v7"/>',
    "trash": '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
    "refresh": '<path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 4v7h-7"/>',
    "download": '<path d="M12 4v11M7.5 10.5 12 15l4.5-4.5M5 19h14"/>',
    "bolt": '<path d="M13 3 5 13.5h6L10 21l8-10.5h-6z"/>',
    "shield": '<path d="M12 3l7 3v5.5c0 4.2-2.9 7.6-7 9.5-4.1-1.9-7-5.3-7-9.5V6z"/><path d="M9 12l2.2 2.2L15 10.5"/>',
    "key": '<circle cx="8" cy="15" r="4"/><path d="M11 12l8.5-8.5M16 6.5l2.5 2.5"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
}


# False = okno główne pokazuje wyłącznie tekst ostateczny (bez szarego tekstu wstępnego).
# Pływający pasek pokazuje tekst wstępny zawsze — jako znak, że program pracuje.
SHOW_TENTATIVE = False


def svg_icon(name: str, color: str | None = None, size: int = 24) -> QIcon:
    """Ikona liniowa w podanym kolorze (domyślnie kolor tekstu motywu), ostra na ekranach HiDPI."""
    color = color or motyw.color("text")
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" '
        f'fill="none" stroke="{color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
        f"{SVG_PATHS[name]}</svg>"
    )
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    scale = 3   # zapas rozdzielczości na skalowanie 150–300%
    pixmap = QPixmap(size * scale, size * scale)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    pixmap.setDevicePixelRatio(scale)
    return QIcon(pixmap)


_LOGO_RENDERER: QSvgRenderer | None = None


def _logo_renderer() -> QSvgRenderer | None:
    global _LOGO_RENDERER
    if _LOGO_RENDERER is None and LOGO_FILE.is_file():
        renderer = QSvgRenderer(str(LOGO_FILE))
        if renderer.isValid():
            _LOGO_RENDERER = renderer
    return _LOGO_RENDERER


def app_icon() -> QIcon:
    """Ikona programu w kilku rozmiarach — z logo SVG, a bez pliku rysowana w kodzie."""
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        LogoMark.paint_mark(painter, size)
        painter.end()
        icon.addPixmap(pixmap)
    return icon


# ---------------------------------------------------------------------------
# Kontrolki rysowane
# ---------------------------------------------------------------------------


def _mic_path(cx: float, cy: float, r: float) -> QPainterPath:
    path = QPainterPath()
    w, h = r * 0.46, r * 0.86
    path.addRoundedRect(QRectF(cx - w / 2, cy - r * 0.62, w, h), w / 2, w / 2)
    return path


def _draw_mic(painter: QPainter, cx: float, cy: float, r: float, color: QColor):
    painter.setBrush(color)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawPath(_mic_path(cx, cy, r))
    pen = QPen(color, max(1.6, r * 0.11))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    arc = QRectF(cx - r * 0.42, cy - r * 0.45, r * 0.84, r * 0.84)
    painter.drawArc(arc, 200 * 16, 140 * 16)
    painter.drawLine(QPoint(int(cx), int(cy + r * 0.40)), QPoint(int(cx), int(cy + r * 0.62)))


class LogoMark(QWidget):
    def __init__(self, size=44, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)

    @staticmethod
    def paint_mark(painter: QPainter, size: int):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        renderer = _logo_renderer()
        if renderer is not None:
            renderer.render(painter, QRectF(0, 0, size, size))
            return
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#0A6CF0"))
        painter.drawRoundedRect(QRectF(1, 1, size - 2, size - 2), size * 0.25, size * 0.25)
        _draw_mic(painter, size * 0.5, size * 0.52, size * 0.42, QColor("white"))

    def paintEvent(self, _event):
        painter = QPainter(self)
        self.paint_mark(painter, self.width())


class MicButton(QAbstractButton):
    """Okrągły przycisk mikrofonu z pulsującą obwódką zależną od głośności."""

    def __init__(self, diameter=64, parent=None):
        super().__init__(parent)
        self.diameter = diameter
        margin = int(diameter * 0.28)
        self.setFixedSize(diameter + margin * 2, diameter + margin * 2)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.state = "loading"
        self.level = 0.0
        self.phase = 0.0

    def set_state(self, state: str):
        self.state = state
        self.update()

    def set_level(self, value: float):
        self.level = self.level * 0.6 + value * 0.4
        self.phase = (self.phase + 0.06) % (2 * math.pi)
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        cx, cy = self.width() / 2, self.height() / 2
        r = self.diameter / 2
        colors = {"idle": "accent", "listening": "danger", "loading": "neutral", "error": "neutral"}
        color = motyw.qcolor(colors.get(self.state, "accent"))
        if self.state == "listening":
            halo = motyw.qcolor("danger")
            for scale, alpha in ((0.22 + 0.55 * self.level, 55), (0.10 + 0.25 * self.level, 90)):
                halo.setAlpha(alpha)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(halo)
                painter.drawEllipse(QRectF(cx - r * (1 + scale), cy - r * (1 + scale),
                                           2 * r * (1 + scale), 2 * r * (1 + scale)))
        if self.isDown():
            color = color.darker(115)
        elif self.underMouse():
            color = color.lighter(108)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawEllipse(QRectF(cx - r, cy - r, 2 * r, 2 * r))
        white = QColor("white")
        if self.state == "listening":
            side = r * 0.62
            painter.setBrush(white)
            painter.drawRoundedRect(QRectF(cx - side / 2, cy - side / 2, side, side), side * 0.18, side * 0.18)
        elif self.state == "loading":
            pen = QPen(white, max(2.0, r * 0.10))
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            start = int((time.monotonic() * 360) % 360)
            painter.drawArc(QRectF(cx - r * 0.5, cy - r * 0.5, r, r), -start * 16, 270 * 16)
        else:
            _draw_mic(painter, cx, cy, r * 0.62, white)


class Waveform(QWidget):
    """Przewijany wykres głośności — od razu widać, że mikrofon „słyszy”."""

    def __init__(self, bars=28, idle_color: str | None = None, parent=None):
        super().__init__(parent)
        self.values = deque([0.0] * bars, maxlen=bars)
        self._idle_override = idle_color   # pasek pływający ma własne, stałe kolory
        self.active = False
        self.setMinimumSize(bars * 4, 26)

    def push(self, value: float, active: bool):
        self.values.append(value if active else 0.0)
        self.active = active
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        idle = QColor(self._idle_override) if self._idle_override else motyw.qcolor("wave_idle")
        painter.setBrush(motyw.qcolor("cyan") if self.active else idle)
        count = len(self.values)
        step = self.width() / count
        bar = max(2.0, step * 0.55)
        height = self.height()
        for index, value in enumerate(self.values):
            h = max(2.5, value * height * 0.95)
            x = index * step + (step - bar) / 2
            painter.drawRoundedRect(QRectF(x, (height - h) / 2, bar, h), bar / 2, bar / 2)


class ToolButton(QPushButton):
    def __init__(self, icon_name: str, tooltip: str, color: str, parent=None):
        super().__init__(parent)
        self.icon_name = icon_name
        self.color = color
        self.setIcon(svg_icon(icon_name, color, 18))
        self.setToolTip(tooltip)
        self.setFixedSize(30, 30)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setProperty("overlayTool", True)

    def set_icon_name(self, name: str):
        self.icon_name = name
        self.setIcon(svg_icon(name, self.color, 18))


# ---------------------------------------------------------------------------
# Pływający pasek
# ---------------------------------------------------------------------------


class Overlay(QWidget):
    """Ciemny pasek „na wierzchu”, który nigdy nie zabiera fokusu oknu docelowemu."""

    toggle_requested = Signal()
    settings_requested = Signal()
    typing_toggled = Signal(bool)
    close_requested = Signal()
    moved = Signal(QPoint)

    def __init__(self):
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        if IS_MAC:
            # Bez tego macOS chowa okna narzędziowe, gdy aktywna jest inna aplikacja.
            self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFixedSize(640, 104)
        self._drag_offset = None
        self.listening = False
        self.caret_visible = True

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(10)
        self.mic = MicButton(44)
        self.mic.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.mic.clicked.connect(self.toggle_requested)
        layout.addWidget(self.mic)

        center = QVBoxLayout()
        center.setSpacing(3)
        top = QHBoxLayout()
        top.setSpacing(8)
        self.status = QLabel("Gotowy")
        self.status.setProperty("overlayStatus", True)
        self.target = QLabel("")
        self.target.setProperty("overlayTarget", True)
        self.target.setMinimumWidth(80)
        self.target.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        top.addWidget(self.status)
        top.addWidget(self.target, 1)
        center.addLayout(top)
        self.text = QLabel("")
        self.text.setProperty("overlayText", True)
        self.text.setWordWrap(True)
        self.text.setTextFormat(Qt.TextFormat.RichText)
        self.text.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.text.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        center.addWidget(self.text, 1)
        layout.addLayout(center, 1)

        self.wave = Waveform(18, motyw.OVERLAY["wave_idle"])
        self.wave.setFixedSize(64, 30)
        layout.addWidget(self.wave)

        tools = QVBoxLayout()
        tools.setSpacing(2)
        row = QHBoxLayout()
        row.setSpacing(2)
        icon_color = motyw.OVERLAY["icon"]
        self.typing_button = ToolButton("keyboard", "Wpisywanie do aktywnego okna: włączone", icon_color)
        self.typing_button.setCheckable(True)
        self.typing_button.setChecked(True)
        self.typing_button.toggled.connect(self._typing_toggled)
        self.settings_button = ToolButton("gear", f"Pokaż okno {APP_NAME}", icon_color)
        self.settings_button.clicked.connect(self.settings_requested)
        self.close_button = ToolButton("close", "Ukryj pasek", icon_color)
        self.close_button.clicked.connect(self.close_requested)
        row.addWidget(self.typing_button)
        row.addWidget(self.settings_button)
        row.addWidget(self.close_button)
        tools.addLayout(row)
        self.hint = QLabel("")
        self.hint.setProperty("overlayHint", True)
        self.hint.setAlignment(Qt.AlignmentFlag.AlignRight)
        tools.addWidget(self.hint)
        tools.addStretch(1)
        layout.addLayout(tools)

        self.caret_timer = QTimer(self)
        self.caret_timer.timeout.connect(self._blink)
        self.caret_timer.start(530)
        self._stable = ""
        self._tentative = ""

    # -- zawartość -----------------------------------------------------------

    def _typing_toggled(self, enabled: bool):
        self._mark_paused(enabled)
        self.typing_button.setToolTip(
            "Wpisywanie do aktywnego okna: włączone" if enabled
            else "Wpisywanie wstrzymane — tekst tylko w podglądzie"
        )
        self.typing_toggled.emit(enabled)

    def _mark_paused(self, enabled: bool):
        button = self.typing_button
        button.set_icon_name("keyboard" if enabled else "keyboard-off")
        button.setProperty("paused", not enabled)
        button.style().unpolish(button)
        button.style().polish(button)

    def set_typing(self, enabled: bool):
        self.typing_button.blockSignals(True)
        self.typing_button.setChecked(enabled)
        self._mark_paused(enabled)
        self.typing_button.blockSignals(False)

    def set_text(self, stable: str, tentative: str):
        self._stable, self._tentative = stable, tentative
        self._render()

    def set_listening(self, listening: bool):
        self.listening = listening
        self.mic.set_state("listening" if listening else self.mic.state if self.mic.state in ("loading", "error") else "idle")
        self._render()
        self.update()

    def _blink(self):
        self.caret_visible = not self.caret_visible
        if self.listening:
            self._render()

    def _render(self):
        stable, tentative = self._stable, self._tentative
        look = motyw.OVERLAY
        if not stable and not tentative:
            if self.listening:
                hint = "Mów — tekst pojawi się tutaj i w aktywnym oknie…"
            elif HOTKEY_SUPPORTED:
                hint = "Naciśnij mikrofon albo skrót, aby dyktować."
            else:
                hint = "Naciśnij mikrofon, aby dyktować."
            self.text.setText(f'<span style="color:{look["placeholder"]}">{hint}</span>')
            return
        full = stable + tentative
        tail, cut = display_tail(full, 150)
        split = max(0, len(tail) - len(tentative)) if tentative else len(tail)
        shown_stable, shown_tentative = tail[:split], tail[split:]
        caret = ""
        if self.listening:
            caret_color = motyw.DARK["cyan"] if self.caret_visible else "transparent"
            caret = f'<span style="color:{caret_color}">▍</span>'
        body = (
            ("… " if cut else "")
            + html.escape(shown_stable)
            + f'<span style="color:{look["tentative"]}">{html.escape(shown_tentative)}</span>'
            + caret
        )
        self.text.setText(body)

    # -- okno ------------------------------------------------------------------

    def showEvent(self, event):
        super().showEvent(event)
        make_window_non_activating(int(self.winId()))

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        painter.setPen(QPen(QColor(255, 255, 255, 30), 1))
        background = QColor(motyw.OVERLAY["bg"])
        background.setAlpha(242)
        painter.setBrush(background)
        painter.drawRoundedRect(rect, 22, 22)
        if self.listening:
            accent = QColor(motyw.DARK["danger"])
            accent.setAlpha(160)
            painter.setPen(QPen(accent, 1.4))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 21, 21)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event):
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)

    def mouseReleaseEvent(self, _event):
        if self._drag_offset is not None:
            self._drag_offset = None
            self.moved.emit(self.pos())

    def place_default(self):
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        self.move(area.center().x() - self.width() // 2, area.bottom() - self.height() - 36)

    def place(self, point: QPoint | None):
        if point is None:
            self.place_default()
            return
        for screen in QGuiApplication.screens():
            if screen.availableGeometry().contains(point + QPoint(20, 20)):
                self.move(point)
                return
        self.place_default()


# ---------------------------------------------------------------------------
# Most między wątkami a Qt
# ---------------------------------------------------------------------------


class EngineBridge(QObject, EngineCallbacks):
    status_changed = Signal(str, bool)
    model_ready = Signal(object)
    model_failed = Signal(str)
    partial_text = Signal(str, str)
    final_text = Signal(str)
    speech_changed = Signal(bool)
    listening_changed = Signal(bool)
    hotkey_pressed = Signal()
    hotkey_released = Signal()

    def status(self, text, busy):
        self.status_changed.emit(text, busy)

    def ready(self, info):
        self.model_ready.emit(info)

    def failed(self, text):
        self.model_failed.emit(text)

    def partial(self, delta, tentative):
        self.partial_text.emit(delta, tentative)

    def final(self, delta):
        self.final_text.emit(delta)

    def speech(self, active):
        self.speech_changed.emit(active)

    def listening(self, active):
        self.listening_changed.emit(active)


# ---------------------------------------------------------------------------
# Okno główne
# ---------------------------------------------------------------------------


def app_settings() -> QSettings:
    """Ustawienia programu. Gdy katalog danych jest wskazany zmienną
    MYDICT_DATA (testy, zrzuty ekranu, kopia przenośna), ustawienia też
    trafiają tam — a nie do rejestru systemu."""
    if os.environ.get("MYDICT_DATA"):
        return QSettings(str(data_dir() / "ustawienia.ini"), QSettings.Format.IniFormat)
    settings = QSettings(ORGANIZATION, APP_TITLE)
    if not settings.allKeys():
        # Program nazywał się wcześniej inaczej — przenosimy ustawienia i klucze API.
        legacy = QSettings(ORGANIZATION, LEGACY_NAME)
        for key in legacy.allKeys():
            settings.setValue(key, legacy.value(key))
        if legacy.allKeys():
            log.info("Przeniesiono ustawienia z %s", LEGACY_NAME)
    return settings


def field_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setProperty("fieldLabel", True)
    return label


def helper_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setProperty("helper", True)
    label.setWordWrap(True)
    return label


def group_label(text: str) -> QLabel:
    label = QLabel(text.upper())
    label.setProperty("groupTitle", True)
    return label


def repolish(widget: QWidget):
    """Po zmianie właściwości używanej w arkuszu stylów trzeba przeliczyć styl."""
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def format_size(size: float) -> str:
    if size >= 1024 ** 3:
        return f"{size / 1024 ** 3:.1f} GB".replace(".", ",")
    return f"{size / 1024 ** 2:.0f} MB"


class Task(QObject):
    """Praca w tle (sieć, pobieranie) z wynikiem dostarczanym do wątku interfejsu."""

    progress = Signal(float, float)
    done = Signal(object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.cancel_event = threading.Event()
        self.running = False
        self._last_report = 0.0

    def start(self, work):
        """``work(progress, cancel_event)`` działa w osobnym wątku."""
        if self.running:
            return
        self.running = True
        self.cancel_event.clear()

        def report(done: float, total: float):
            now = time.monotonic()
            if now - self._last_report >= 0.1 or (total and done >= total):
                self._last_report = now
                self.progress.emit(float(done), float(total))

        def run():
            try:
                result = work(report, self.cancel_event)
            except Cancelled:
                self.running = False
                self.cancelled.emit()
            except Exception as exc:
                log.warning("Zadanie w tle nie powiodło się: %s", exc, exc_info=not isinstance(
                    exc, (updates.UpdateError, DownloadError)))
                self.running = False
                self.failed.emit(str(exc))
            else:
                self.running = False
                self.done.emit(result)

        threading.Thread(target=run, name="dyktowanie-zadanie", daemon=True).start()

    def cancel(self):
        self.cancel_event.set()


class Banner(QFrame):
    """Pasek informacji nad treścią okna: ikona, tytuł, opis, przyciski, postęp."""

    def __init__(self, kind: str, icon: str, parent=None):
        super().__init__(parent)
        self.kind = kind
        self.icon_name = icon
        self.setProperty("banner", kind)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 10, 10)
        layout.setSpacing(12)
        self.icon = QLabel()
        self.icon.setFixedSize(22, 22)
        layout.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignTop)
        texts = QVBoxLayout()
        texts.setSpacing(2)
        self.title = QLabel("")
        self.title.setProperty("bannerTitle", True)
        self.text = QLabel("")
        self.text.setProperty("bannerText", True)
        self.text.setWordWrap(True)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setVisible(False)
        texts.addWidget(self.title)
        texts.addWidget(self.text)
        texts.addWidget(self.bar)
        layout.addLayout(texts, 1)
        self.buttons = QHBoxLayout()
        self.buttons.setSpacing(6)
        layout.addLayout(self.buttons)
        self.refresh_theme()
        self.setVisible(False)

    def add_button(self, text: str, handler, primary: bool = False) -> QPushButton:
        button = QPushButton(text)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setProperty("primary" if primary else "flat", True)
        button.clicked.connect(handler)
        self.buttons.addWidget(button)
        return button

    def set_message(self, title: str, text: str = ""):
        self.title.setText(title)
        self.text.setText(text)
        self.text.setVisible(bool(text))

    def set_progress(self, done: float, total: float):
        self.bar.setVisible(True)
        if total > 0:
            self.bar.setRange(0, 1000)
            self.bar.setValue(int(1000 * min(1.0, done / total)))
        else:
            self.bar.setRange(0, 0)

    def clear_progress(self):
        self.bar.setVisible(False)

    def refresh_theme(self):
        tone = "warning_text" if self.kind == "warning" else "accent"
        self.icon.setPixmap(svg_icon(self.icon_name, motyw.color(tone), 22).pixmap(22, 22))


class UpdateDialog(QDialog):
    """Okno aktualizacji: co nowego, pobieranie z postępem, instalacja."""

    def __init__(self, release, parent=None):
        super().__init__(parent)
        self.release = release
        self.installing = False
        self.setWindowTitle("Aktualizacja programu")
        self.setMinimumSize(520, 420)
        self.task = Task(self)
        self.task.progress.connect(self._on_progress)
        self.task.done.connect(self._on_downloaded)
        self.task.failed.connect(self._on_failed)
        self.task.cancelled.connect(self._on_cancelled)
        self.can_install, self.reason = updates.can_self_install()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(10)
        title = QLabel(f"Dostępna jest wersja {release.version}")
        title.setProperty("sectionTitle", True)
        layout.addWidget(title)
        layout.addWidget(helper_label(f"Masz wersję {__version__}. Ustawienia, klucze i pobrane modele zostają bez zmian."))
        self.notes = QTextEdit()
        self.notes.setReadOnly(True)
        self.notes.setProperty("notes", True)
        self.notes.setMarkdown(release.notes or "Brak opisu zmian.")
        layout.addWidget(self.notes, 1)
        self.status = helper_label("" if self.can_install else self.reason)
        layout.addWidget(self.status)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setVisible(False)
        layout.addWidget(self.bar)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        page = QPushButton("Strona wydania")
        page.setProperty("flat", True)
        page.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(release.page_url)))
        buttons.addWidget(page)
        buttons.addStretch(1)
        self.later = QPushButton("Później")
        self.later.clicked.connect(self.reject)
        buttons.addWidget(self.later)
        self.install_button = QPushButton(
            "Zainstaluj i uruchom ponownie" if self.can_install else "Pobierz ze strony"
        )
        self.install_button.setProperty("primary", True)
        self.install_button.setDefault(True)
        self.install_button.clicked.connect(self._start)
        buttons.addWidget(self.install_button)
        layout.addLayout(buttons)

    def _start(self):
        if not self.can_install:
            QDesktopServices.openUrl(QUrl(self.release.page_url))
            self.reject()
            return
        self.install_button.setEnabled(False)
        self.later.setText("Anuluj")
        self.bar.setVisible(True)
        self.bar.setRange(0, 0)
        self.status.setText("Pobieranie aktualizacji…")
        self.task.start(lambda progress, cancel: updates.fetch_update(self.release, progress, cancel))

    def _on_progress(self, done: float, total: float):
        if total > 0:
            self.bar.setRange(0, 1000)
            self.bar.setValue(int(1000 * done / total))
            self.status.setText(f"Pobieranie aktualizacji… {format_size(done)} z {format_size(total)}")

    def _on_downloaded(self, package):
        self.status.setText("Instalowanie — program uruchomi się ponownie…")
        self.bar.setRange(0, 0)
        try:
            updates.install(package)
        except updates.UpdateError as exc:
            self._on_failed(str(exc))
            return
        self.installing = True
        self.accept()

    def _on_failed(self, message: str):
        self.bar.setVisible(False)
        self.install_button.setEnabled(True)
        self.install_button.setText("Spróbuj ponownie")
        self.later.setText("Później")
        self.status.setText(f'<span style="color:{motyw.color("danger_text")}">{html.escape(message)}</span>')

    def _on_cancelled(self):
        self.bar.setVisible(False)
        self.install_button.setEnabled(True)
        self.later.setText("Później")
        self.status.setText("Pobieranie przerwane — można je wznowić.")

    def reject(self):
        if self.task.running:
            self.task.cancel()
            return
        super().reject()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.setWindowIcon(app_icon())
        self.resize(1080, 740)
        self.setMinimumSize(940, 620)
        self.settings = app_settings()
        self.api = ApiStore(self.settings)
        self._current_model = ""

        self.bridge = EngineBridge()
        self.engine = DictationEngine(self.bridge)
        self.typer = TypingEngine()
        self.typer.on_typed = latency.typed
        self.typer.on_blocked = latency.blocked
        self.hotkey: HotkeyListener | None = None
        self.session = SessionText()
        self.archive: list[str] = []
        self.model_state = "loading"
        self.model_info: ModelInfo | None = None
        self.own_elevated = own_process_elevated()
        self.hardware = sprzet.detect()
        self.started_from_window = False
        self.pending_release = None
        self._gpu_reload_pending = False
        self._gpu_auto_tried = False
        self.quitting_for_update = False
        self.update_task = Task(self)
        self.update_task.done.connect(self._on_update_checked)
        self.update_task.failed.connect(self._on_update_check_failed)
        self._manual_update_check = False
        self.gpu_task = Task(self)
        self.gpu_task.progress.connect(self._on_gpu_progress)
        self.gpu_task.done.connect(self._on_gpu_installed)
        self.gpu_task.failed.connect(self._on_gpu_failed)
        self.gpu_task.cancelled.connect(lambda: self._on_gpu_failed("Pobieranie przerwane — można je wznowić."))
        self.hide_overlay_timer = QTimer(self)
        self.hide_overlay_timer.setSingleShot(True)
        self.hide_overlay_timer.timeout.connect(self._auto_hide_overlay)

        self.overlay = Overlay()
        self._build()
        self._load_settings()
        self._connect()
        self._build_tray()
        self._refresh_icons()

        self.level_timer = QTimer(self)
        self.level_timer.timeout.connect(self._tick_level)
        self.level_timer.start(33)
        self.window_timer = QTimer(self)
        self.window_timer.timeout.connect(self._tick_window)
        self.window_timer.start(300)

        self.engine.start()
        self.typer.start()
        self._apply_typing()
        self._apply_stream_settings()
        self._restart_hotkey()
        self._load_model()
        self._render_transcript()
        if self.overlay_check.isChecked():
            self._show_overlay()
        # Od razu, nie czekając na model: karta NVIDIA bez bibliotek → pobieramy je w tle.
        QTimer.singleShot(1500, self._update_gpu_banner)
        updates.cleanup()
        if self.update_check.isChecked():
            QTimer.singleShot(4000, self._auto_check_updates)

    # -- budowa ----------------------------------------------------------------

    def _build(self):
        root = QWidget()
        outer = QVBoxLayout(root)
        outer.setContentsMargins(24, 20, 24, 14)
        outer.setSpacing(14)

        header = QHBoxLayout()
        header.setSpacing(14)
        header.addWidget(LogoMark(44))
        titles = QVBoxLayout()
        titles.setSpacing(1)
        title = QLabel("Dyktowanie na żywo")
        title.setProperty("pageTitle", True)
        subtitle = QLabel("Mów — tekst pojawia się od razu tam, gdzie piszesz: w dokumencie, przeglądarce, poczcie, komunikatorze.")
        subtitle.setProperty("pageSubtitle", True)
        titles.addWidget(title)
        titles.addWidget(subtitle)
        header.addLayout(titles, 1)
        self.status_pill = QLabel("Przygotowanie…")
        self.status_pill.setProperty("pill", "busy")
        header.addWidget(self.status_pill, 0, Qt.AlignmentFlag.AlignVCenter)
        outer.addLayout(header)

        # Banery: aktualizacja, przyspieszenie GPU, zgoda systemu na wpisywanie.
        self.update_banner = Banner("info", "download")
        self.update_banner.add_button("Później", self._dismiss_update_banner)
        self.update_banner.add_button("Zobacz i zainstaluj", self._open_update_dialog, primary=True)
        outer.addWidget(self.update_banner)
        self.gpu_banner = Banner("info", "bolt")
        self.gpu_later = self.gpu_banner.add_button("Nie teraz", self._dismiss_gpu_banner)
        self.gpu_button = self.gpu_banner.add_button("Pobierz teraz", self._install_gpu_pack, primary=True)
        outer.addWidget(self.gpu_banner)
        self.cpu_banner = Banner("info", "bolt")
        self.cpu_banner.set_message("Rozpoznawanie mowy liczy procesor", self.hardware.summary)
        self.cpu_banner.add_button("Rozumiem", self._dismiss_cpu_banner, primary=True)
        outer.addWidget(self.cpu_banner)
        self.permission_banner = Banner("warning", "shield")
        self.permission_banner.set_message(
            "Zezwól programowi na wpisywanie tekstu",
            "macOS wymaga zgody „Dostępność”, aby tekst trafiał do innych aplikacji. "
            f"Włącz {APP_NAME} na liście w Ustawieniach systemowych.",
        )
        self.permission_banner.add_button("Otwórz ustawienia", request_typing_permission, primary=True)
        outer.addWidget(self.permission_banner)

        body = QHBoxLayout()
        body.setSpacing(16)

        # Lewa część: transkrypcja na żywo
        live = QFrame()
        live.setProperty("card", True)
        live_layout = QVBoxLayout(live)
        live_layout.setContentsMargins(20, 18, 20, 16)
        live_layout.setSpacing(12)
        live_top = QHBoxLayout()
        live_title = QLabel("Transkrypcja")
        live_title.setProperty("cardTitle", True)
        live_top.addWidget(live_title)
        live_top.addStretch(1)
        self.main_wave = Waveform(36)
        self.main_wave.setFixedSize(160, 26)
        live_top.addWidget(self.main_wave)
        live_layout.addLayout(live_top)
        self.target_label = QLabel("Pisanie do: —")
        self.target_label.setProperty("targetLabel", True)
        live_layout.addWidget(self.target_label)
        self.transcript = QTextEdit()
        self.transcript.setReadOnly(True)
        self.transcript.setProperty("transcript", True)
        live_layout.addWidget(self.transcript, 1)
        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.copy_button = QPushButton("Kopiuj wszystko")
        self.copy_button.setProperty("secondary", True)
        self.save_button = QPushButton("Zapisz jako .txt")
        self.save_button.setProperty("secondary", True)
        self.clear_button = QPushButton("Wyczyść")
        self.clear_button.setProperty("flat", True)
        for button in (self.copy_button, self.save_button, self.clear_button):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            actions.addWidget(button)
        actions.addStretch(1)
        self.counter = QLabel("")
        self.counter.setProperty("helper", True)
        actions.addWidget(self.counter)
        live_layout.addLayout(actions)
        body.addWidget(live, 1)

        # Prawa część: sterowanie i ustawienia
        side = QWidget()
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(0, 0, 6, 0)
        side_layout.setSpacing(14)

        control = QFrame()
        control.setProperty("card", True)
        control_layout = QVBoxLayout(control)
        control_layout.setContentsMargins(18, 12, 18, 16)
        control_layout.setSpacing(4)
        self.big_mic = MicButton(76)
        control_layout.addWidget(self.big_mic, 0, Qt.AlignmentFlag.AlignHCenter)
        self.control_label = QLabel("Rozpocznij dyktowanie")
        self.control_label.setProperty("controlTitle", True)
        self.control_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        control_layout.addWidget(self.control_label)
        self.hotkey_hint = QLabel("")
        self.hotkey_hint.setProperty("helper", True)
        self.hotkey_hint.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.hotkey_hint.setWordWrap(True)
        control_layout.addWidget(self.hotkey_hint)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)
        control_layout.addSpacing(6)
        control_layout.addWidget(self.progress)
        self.model_label = QLabel("")
        self.model_label.setProperty("helper", True)
        self.model_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.model_label.setWordWrap(True)
        control_layout.addWidget(self.model_label)
        side_layout.addWidget(control)

        settings_card = QFrame()
        settings_card.setProperty("card", True)
        form = QVBoxLayout(settings_card)
        form.setContentsMargins(18, 14, 18, 18)
        form.setSpacing(6)
        heading = QLabel("Ustawienia")
        heading.setProperty("sectionTitle", True)
        form.addWidget(heading)

        def divider():
            line = QFrame()
            line.setProperty("divider", True)
            form.addSpacing(8)
            form.addWidget(line)

        # --- Rozpoznawanie mowy
        form.addWidget(group_label("Rozpoznawanie mowy"))
        form.addWidget(field_label("Model"))
        self.model_combo = QComboBox()
        self.model_combo.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.model_combo.setMaxVisibleItems(24)
        self._fill_models()
        form.addWidget(self.model_combo)
        self.device_combo = QComboBox()
        self.device_combo.addItem("Karta graficzna, jeśli dostępna", "auto")
        self.device_combo.addItem("Tylko procesor (CPU)", "cpu")
        form.addWidget(self.device_combo)
        self.hardware_hint = helper_label(self.hardware.summary)
        form.addWidget(self.hardware_hint)
        self.cloud_hint = helper_label("")
        self.cloud_hint.setVisible(False)
        form.addWidget(self.cloud_hint)
        api_row = QHBoxLayout()
        api_row.setSpacing(8)
        self.keys_button = QPushButton("Klucze API…")
        self.keys_button.setToolTip("Klucze OpenAI, Groq, ElevenLabs, Deepgram, AssemblyAI")
        self.custom_button = QPushButton("Własny model…")
        self.custom_button.setToolTip("Wpisz ręcznie adres API, nazwę modelu i klucz")
        api_row.addWidget(self.keys_button)
        api_row.addWidget(self.custom_button)
        form.addLayout(api_row)

        form.addWidget(field_label("Język"))
        self.language_combo = QComboBox()
        for code, label in LANGUAGES.items():
            self.language_combo.addItem(label, code)
        form.addWidget(self.language_combo)

        form.addWidget(field_label("Pauza kończąca wypowiedź"))
        self.pause_combo = QComboBox()
        self.pause_combo.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        for seconds, label in PAUSE_OPTIONS:
            self.pause_combo.addItem(label, seconds)
        form.addWidget(self.pause_combo)

        form.addWidget(field_label("Słownik — nazwy własne i terminy"))
        self.hotwords_edit = QLineEdit()
        self.hotwords_edit.setPlaceholderText("np. BAPO, WhisperX, Kraków")
        form.addWidget(self.hotwords_edit)
        divider()

        # --- Dźwięk
        form.addWidget(group_label("Źródło dźwięku"))
        mic_row = QHBoxLayout()
        mic_row.setSpacing(8)
        self.mic_combo = QComboBox()
        self.mic_combo.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.mic_refresh = QPushButton()
        self.mic_refresh.setToolTip("Odśwież listę mikrofonów")
        self.mic_refresh.setFixedWidth(36)
        mic_row.addWidget(self.mic_combo, 1)
        mic_row.addWidget(self.mic_refresh)
        form.addSpacing(4)
        form.addLayout(mic_row)
        divider()

        # --- Skrót
        form.addWidget(group_label("Skrót klawiszowy"))
        self.hotkey_combo = QComboBox()
        for combo in HOTKEYS:
            self.hotkey_combo.addItem(combo, combo)
        form.addSpacing(4)
        form.addWidget(self.hotkey_combo)
        mode_row = QHBoxLayout()
        mode_row.setSpacing(0)
        self.mode_toggle = QPushButton("Naciśnij: start / stop")
        self.mode_hold = QPushButton("Przytrzymaj i mów")
        self.mode_group = QButtonGroup(self)
        for button, position in ((self.mode_toggle, "first"), (self.mode_hold, "last")):
            button.setCheckable(True)
            button.setProperty("segment", position)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            self.mode_group.addButton(button)
            mode_row.addWidget(button)
        form.addLayout(mode_row)
        self.hotkey_note = helper_label("Działa w każdym oknie, także gdy program jest zminimalizowany.")
        form.addWidget(self.hotkey_note)
        for widget in (self.hotkey_combo, self.mode_toggle, self.mode_hold):
            widget.setEnabled(HOTKEY_SUPPORTED)
        divider()

        # --- Wpisywanie
        form.addWidget(group_label("Wpisywanie tekstu"))
        self.typing_check = QCheckBox("Wpisuj tekst do aktywnego okna")
        form.addWidget(self.typing_check)
        self.smooth_check = QCheckBox("Płynne pisanie (efekt pisania na żywo)")
        form.addWidget(self.smooth_check)
        self.space_check = QCheckBox("Spacja przed pierwszym słowem sesji")
        form.addWidget(self.space_check)
        if not TYPING_SUPPORTED:
            self.typing_check.setEnabled(False)
            form.addWidget(helper_label("Na tym systemie program pokazuje tekst tylko w oknie podglądu."))
        divider()

        # --- Program
        form.addWidget(group_label("Program"))
        self.overlay_check = QCheckBox("Pływający pasek zawsze widoczny")
        form.addWidget(self.overlay_check)
        self.minimize_check = QCheckBox("Chowaj to okno po starcie przyciskiem")
        form.addWidget(self.minimize_check)
        self.update_check = QCheckBox("Sprawdzaj aktualizacje automatycznie")
        form.addWidget(self.update_check)
        form.addWidget(field_label("Motyw"))
        self.theme_combo = QComboBox()
        for mode, label in motyw.MODES.items():
            self.theme_combo.addItem(label, mode)
        form.addWidget(self.theme_combo)

        side_layout.addWidget(settings_card)
        side_layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(side)
        scroll.setFixedWidth(372)
        body.addWidget(scroll)
        outer.addLayout(body, 1)

        footer = QHBoxLayout()
        footer.setSpacing(4)
        self.version_label = QLabel(f"{APP_TITLE} {__version__}")
        self.version_label.setProperty("link", True)
        footer.addWidget(self.version_label)
        footer.addStretch(1)
        self.update_button = QPushButton("Sprawdź aktualizacje")
        self.log_button = QPushButton("Otwórz folder dziennika")
        for button in (self.update_button, self.log_button):
            button.setProperty("flat", True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            footer.addWidget(button)
        outer.addLayout(footer)
        self.setCentralWidget(root)

    def _refresh_icons(self):
        """Ikony mają kolor wpisany w obrazek — po zmianie motywu rysujemy je od nowa."""
        accent = motyw.color("accent")
        muted = motyw.color("text_muted")
        self.copy_button.setIcon(svg_icon("copy", accent, 16))
        self.save_button.setIcon(svg_icon("save", accent, 16))
        self.clear_button.setIcon(svg_icon("trash", muted, 16))
        self.mic_refresh.setIcon(svg_icon("refresh", muted, 16))
        self.keys_button.setIcon(svg_icon("key", muted, 16))
        self.custom_button.setIcon(svg_icon("plus", muted, 16))
        for banner in (self.update_banner, self.gpu_banner, self.cpu_banner, self.permission_banner):
            banner.refresh_theme()

    def _build_tray(self):
        self.tray = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(app_icon(), self)
        self.tray.setToolTip(APP_TITLE)
        menu = QMenu()
        self.tray_toggle = QAction("Rozpocznij dyktowanie", self)
        self.tray_toggle.triggered.connect(self.toggle_listening)
        show = QAction("Pokaż okno", self)
        show.triggered.connect(self.show_window)
        quit_action = QAction("Zakończ", self)
        quit_action.triggered.connect(self.close)
        check = QAction("Sprawdź aktualizacje…", self)
        check.triggered.connect(lambda: (self.show_window(), self.check_updates(manual=True)))
        menu.addAction(self.tray_toggle)
        menu.addAction(show)
        menu.addAction(check)
        menu.addSeparator()
        menu.addAction(quit_action)
        self.tray.setContextMenu(menu)
        self.tray_menu = menu
        self.tray.activated.connect(self._tray_activated)
        self.tray.show()

    def _tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            if not IS_MAC:   # na macOS kliknięcie ikony otwiera menu — nie może przy okazji włączać nagrywania
                self.toggle_listening()
        elif reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.show_window()

    # -- ustawienia ------------------------------------------------------------

    def _select(self, combo: QComboBox, value):
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    ADD_CUSTOM = "__add_custom__"

    def _fill_models(self, selected: str | None = None):
        """Lista modeli: lokalne, w chmurze, własne i pozycja „dodaj własny”."""
        combo = self.model_combo
        combo.blockSignals(True)
        combo.clear()

        def header(text: str):
            combo.addItem(text, None)
            item = combo.model().item(combo.count() - 1)
            item.setEnabled(False)
            font = item.font()
            font.setBold(True)
            item.setFont(font)

        header("— Na tym komputerze (bez internetu) —")
        for key, (_name, label, _size) in MODELS.items():
            combo.addItem(label, key)
        combo.insertSeparator(combo.count())
        header("— W chmurze: własny klucz API, w trakcie testów —")
        for key, model in CLOUD_MODELS.items():
            combo.addItem(model.label, key)
            combo.setItemData(combo.count() - 1, model.note, Qt.ItemDataRole.ToolTipRole)
        for custom in self.api.custom_models():
            combo.addItem(f"★ {custom.name}", custom.combo_key)
            combo.setItemData(combo.count() - 1, f"{custom.model} · {custom.base_url}", Qt.ItemDataRole.ToolTipRole)
        combo.addItem("＋ Dodaj własny model API…", self.ADD_CUSTOM)
        index = combo.findData(selected) if selected else -1
        if index < 0:
            index = combo.findData(self._current_model or DEFAULT_MODEL)
        combo.setCurrentIndex(index if index >= 0 else combo.findData(DEFAULT_MODEL))
        combo.blockSignals(False)

    def _load_settings(self):
        s = self.settings
        # Pierwsze uruchomienie: bez karty NVIDIA model Turbo nie nadąża za mową — zaczynamy od lżejszego.
        self._select(self.model_combo, s.value("model", self.hardware.recommended_model))
        if not self.model_combo.currentData() or self.model_combo.currentData() == self.ADD_CUSTOM:
            self._select(self.model_combo, DEFAULT_MODEL)
        self._select(self.device_combo, s.value("device", "auto"))
        self._select(self.language_combo, s.value("language", "pl"))
        self._select(self.hotkey_combo, s.value("hotkey", DEFAULT_HOTKEY))
        hold = s.value("hold_mode", False, type=bool)
        (self.mode_hold if hold else self.mode_toggle).setChecked(True)
        self.typing_check.setChecked(s.value("typing", True, type=bool))
        self.smooth_check.setChecked(s.value("smooth", True, type=bool))
        self.space_check.setChecked(s.value("leading_space", False, type=bool))
        wanted = float(s.value("end_silence", 0.7))
        nearest = min(range(len(PAUSE_OPTIONS)), key=lambda i: abs(PAUSE_OPTIONS[i][0] - wanted))
        self.pause_combo.setCurrentIndex(nearest)
        self.hotwords_edit.setText(s.value("hotwords", ""))
        self.overlay_check.setChecked(s.value("overlay_always", False, type=bool))
        self.minimize_check.setChecked(s.value("minimize_on_start", True, type=bool))
        self.update_check.setChecked(s.value("auto_update", True, type=bool))
        self._select(self.theme_combo, s.value("theme", "auto"))
        self._fill_microphones(s.value("microphone", ""))
        position = s.value("overlay_pos")
        self.overlay.place(position if isinstance(position, QPoint) else None)
        geometry = s.value("geometry")
        if isinstance(geometry, QByteArray):
            self.restoreGeometry(geometry)

    def _save_settings(self):
        s = self.settings
        if self._current_model:
            s.setValue("model", self._current_model)
        s.setValue("device", self.device_combo.currentData())
        s.setValue("language", self.language_combo.currentData())
        s.setValue("hotkey", self.hotkey_combo.currentData())
        s.setValue("hold_mode", self.mode_hold.isChecked())
        s.setValue("typing", self.typing_check.isChecked())
        s.setValue("smooth", self.smooth_check.isChecked())
        s.setValue("leading_space", self.space_check.isChecked())
        s.setValue("end_silence", self.pause_combo.currentData())
        s.setValue("hotwords", self.hotwords_edit.text())
        s.setValue("overlay_always", self.overlay_check.isChecked())
        s.setValue("minimize_on_start", self.minimize_check.isChecked())
        s.setValue("auto_update", self.update_check.isChecked())
        s.setValue("theme", self.theme_combo.currentData() or "auto")
        s.setValue("microphone", self.mic_combo.currentData() or "")
        s.setValue("overlay_pos", self.overlay.pos())
        s.setValue("geometry", self.saveGeometry())

    def _fill_microphones(self, selected: str = ""):
        self.mic_combo.blockSignals(True)
        self.mic_combo.clear()
        self.mic_combo.addItem("Domyślny mikrofon systemu", "")
        try:
            for name in Microphone.devices():
                self.mic_combo.addItem(name, name)
        except Exception as exc:
            log.warning("Lista mikrofonów: %s", exc)
            self.mic_combo.setItemText(0, "Domyślny mikrofon (brak biblioteki sounddevice)")
        self._select(self.mic_combo, selected)
        self.mic_combo.blockSignals(False)

    def _connect(self):
        b = self.bridge
        b.status_changed.connect(self._on_status)
        b.model_ready.connect(self._on_ready)
        b.model_failed.connect(self._on_failed)
        b.partial_text.connect(self._on_partial)
        b.final_text.connect(self._on_final)
        b.speech_changed.connect(self._on_speech)
        b.listening_changed.connect(self._on_listening)
        b.hotkey_pressed.connect(self._on_hotkey_pressed)
        b.hotkey_released.connect(self._on_hotkey_released)

        self.big_mic.clicked.connect(self._toggle_from_window)
        self.overlay.toggle_requested.connect(self.toggle_listening)
        self.overlay.settings_requested.connect(self.show_window)
        self.overlay.close_requested.connect(self._overlay_close)
        self.overlay.typing_toggled.connect(self._overlay_typing)
        self.overlay.moved.connect(lambda point: self.settings.setValue("overlay_pos", point))
        self.copy_button.clicked.connect(self._copy_all)
        self.save_button.clicked.connect(self._save_txt)
        self.clear_button.clicked.connect(self._clear)
        self.mic_refresh.clicked.connect(lambda: self._fill_microphones(self.mic_combo.currentData() or ""))
        self.mic_combo.currentIndexChanged.connect(self._mic_changed)

        self.model_combo.currentIndexChanged.connect(self._model_selected)
        self.device_combo.currentIndexChanged.connect(self._load_model)
        self.keys_button.clicked.connect(lambda: self._open_keys())
        self.custom_button.clicked.connect(self._custom_button_clicked)
        self.language_combo.currentIndexChanged.connect(self._apply_stream_settings)
        self.pause_combo.currentIndexChanged.connect(self._apply_stream_settings)
        self.hotwords_edit.editingFinished.connect(self._apply_stream_settings)
        self.hotkey_combo.currentIndexChanged.connect(self._restart_hotkey)
        self.mode_group.buttonClicked.connect(lambda _button: self._restart_hotkey())
        self.typing_check.toggled.connect(self._apply_typing)
        self.smooth_check.toggled.connect(self._apply_typing)
        self.space_check.toggled.connect(lambda value: setattr(self.session, "leading_space", value))
        self.overlay_check.toggled.connect(self._overlay_setting_changed)
        self.theme_combo.currentIndexChanged.connect(self._theme_selected)
        self.update_button.clicked.connect(lambda: self.check_updates(manual=True))
        self.log_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(log_dir()))))
        for widget_signal in (
            self.update_check.toggled, self.theme_combo.currentIndexChanged,
            self.model_combo.currentIndexChanged, self.device_combo.currentIndexChanged,
            self.language_combo.currentIndexChanged, self.hotkey_combo.currentIndexChanged,
            self.typing_check.toggled, self.smooth_check.toggled, self.space_check.toggled,
            self.pause_combo.currentIndexChanged, self.hotwords_edit.editingFinished,
            self.overlay_check.toggled, self.minimize_check.toggled, self.mic_combo.currentIndexChanged,
        ):
            widget_signal.connect(self._save_settings)

    # -- zastosowanie ustawień ------------------------------------------------------

    def _model_selected(self):
        key = self.model_combo.currentData()
        if key == self.ADD_CUSTOM:
            # Okno otwieramy po obsłużeniu zmiany w liście, żeby nie zagnieżdżać zdarzeń.
            QTimer.singleShot(0, self._add_custom_model)
            return
        if key and key != self._current_model:
            self._load_model()

    def _restore_selection(self):
        self.model_combo.blockSignals(True)
        self._select(self.model_combo, self._current_model or DEFAULT_MODEL)
        self.model_combo.blockSignals(False)

    def _add_custom_model(self):
        dialog = CustomModelDialog(self.api, None, self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.result_model is not None:
            self._current_model = ""
            self._fill_models(dialog.result_model.combo_key)
            self._load_model()
        else:
            self._restore_selection()

    def _custom_button_clicked(self):
        key = self._current_model
        custom = self.api.find_custom(key.split(":", 1)[1]) if key.startswith("custom:") else None
        dialog = CustomModelDialog(self.api, custom, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if dialog.deleted:
            self._current_model = ""
            self._fill_models(DEFAULT_MODEL)
        else:
            self._current_model = ""
            self._fill_models(dialog.result_model.combo_key)
        self._load_model()

    def _open_keys(self, focus_provider: str | None = None) -> bool:
        dialog = ApiKeysDialog(self.api, focus_provider, self)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        if accepted and is_cloud_key(self._current_model):
            # Klucz mógł się zmienić — przeładuj bieżący model w chmurze.
            self._load_model()
        return accepted

    def _update_model_extras(self, key: str):
        cloud = is_cloud_key(key)
        self.device_combo.setVisible(not cloud)
        self.hardware_hint.setVisible(not cloud)
        self.custom_button.setText("Edytuj model…" if key.startswith("custom:") else "Własny model…")
        if cloud:
            resolved = self.api.resolve(key)
            service = resolved.service if resolved else "usługi w chmurze"
            self.cloud_hint.setText(
                f"Opcja dla osób z własnym kontem u dostawcy: klucz API tworzysz samodzielnie, a nagranie "
                f"każdej wypowiedzi jest wysyłane do: {service}. Opłaty i limity zapytań wg planu dostawcy "
                f"(darmowe plany mają niskie limity). Tekst pojawia się po pauzie. "
                f"Modele w chmurze są w trakcie testów — szybkość zależy od usługi i łącza."
            )
        self.cloud_hint.setVisible(cloud)
        if hasattr(self, "transcript"):
            self._render_transcript()

    def _load_model(self):
        if self.engine.listening:
            self.stop_listening()
        key = self.model_combo.currentData()
        if not key or key == self.ADD_CUSTOM:
            key = DEFAULT_MODEL
            self._restore_selection()
        self._current_model = key
        self._update_model_extras(key)
        self.model_state = "loading"
        self._update_controls()
        if not is_cloud_key(key):
            self.engine.load(key, self.device_combo.currentData())
            return
        resolved = self.api.resolve(key)
        if resolved is None:
            self._current_model = DEFAULT_MODEL
            self._restore_selection()
            self._update_model_extras(DEFAULT_MODEL)
            self.engine.load(DEFAULT_MODEL, self.device_combo.currentData())
            return
        if resolved.needs_key and self.isVisible():
            # Brak klucza — od razu pokaż okno, w którym można go wkleić.
            if key.startswith("custom:"):
                QTimer.singleShot(0, self._custom_button_clicked)
            else:
                QTimer.singleShot(0, lambda: self._open_keys(CLOUD_MODELS[key].provider))
        self.engine.load(key, self.device_combo.currentData(), resolved)

    def _apply_stream_settings(self):
        settings = self.engine.settings
        settings.language = self.language_combo.currentData() or "pl"
        settings.hotwords = self.hotwords_edit.text().strip()
        settings.stream.end_silence = float(self.pause_combo.currentData())

    def _apply_typing(self):
        enabled = self.typing_check.isChecked()
        self.typer.enabled = enabled
        self.typer.smooth = self.smooth_check.isChecked()
        self.smooth_check.setEnabled(enabled and TYPING_SUPPORTED)
        self.space_check.setEnabled(enabled and TYPING_SUPPORTED)
        self.overlay.set_typing(enabled)
        self._update_permission_banner()

    def _update_permission_banner(self):
        """macOS: bez zgody „Dostępność” system po cichu odrzuca wpisywany tekst."""
        needed = IS_MAC and self.typer.enabled and not typing_permission_granted()
        if needed != self.permission_banner.isVisible():
            self.permission_banner.setVisible(needed)

    # -- motyw ---------------------------------------------------------------------

    def _theme_selected(self):
        motyw.set_mode(QApplication.instance(), self.theme_combo.currentData() or "auto")
        self.apply_theme()

    def apply_theme(self):
        """Wywoływane po zmianie motywu w programie albo w systemie."""
        motyw.apply(QApplication.instance(), self.theme_combo.currentData() or "auto")
        self._refresh_icons()
        self._render_transcript()
        self.update()
        self.overlay.update()

    # -- aktualizacje ----------------------------------------------------------------

    def _auto_check_updates(self):
        last = float(self.settings.value("last_update_check", 0) or 0)
        if time.time() - last >= updates.CHECK_INTERVAL_HOURS * 3600:
            self.check_updates(manual=False)

    def check_updates(self, manual: bool = False):
        if self.update_task.running:
            return
        self._manual_update_check = manual
        if manual:
            self.update_button.setEnabled(False)
            self.update_button.setText("Sprawdzam…")
        self.update_task.start(lambda _progress, _cancel: updates.check())

    def _reset_update_button(self):
        self.update_button.setEnabled(True)
        self.update_button.setText("Sprawdź aktualizacje")

    def _on_update_checked(self, release):
        self.settings.setValue("last_update_check", time.time())
        self._reset_update_button()
        manual, self._manual_update_check = self._manual_update_check, False
        if release is None:
            self.update_banner.setVisible(False)
            if manual:
                QMessageBox.information(self, APP_TITLE, f"Masz najnowszą wersję ({__version__}).")
            return
        self.pending_release = release
        skipped = str(self.settings.value("update_skipped", ""))
        self.update_banner.set_message(
            f"Dostępna jest wersja {release.version}",
            f"Masz wersję {__version__}. Aktualizacja zajmie chwilę, ustawienia zostają bez zmian.",
        )
        if manual:
            self.update_banner.setVisible(True)
            self._open_update_dialog()
        elif skipped != release.version:
            self.update_banner.setVisible(True)

    def _on_update_check_failed(self, message: str):
        self._reset_update_button()
        manual, self._manual_update_check = self._manual_update_check, False
        if manual:
            QMessageBox.warning(self, APP_TITLE, message)

    def _dismiss_update_banner(self):
        if self.pending_release is not None:
            self.settings.setValue("update_skipped", self.pending_release.version)
        self.update_banner.setVisible(False)

    def _open_update_dialog(self):
        if self.pending_release is None:
            return
        if self.engine.listening:
            self.stop_listening()
        dialog = UpdateDialog(self.pending_release, self)
        dialog.exec()
        if dialog.installing:
            self.quitting_for_update = True
            self.close()

    # -- przyspieszenie kartą graficzną ----------------------------------------------

    def _update_gpu_banner(self):
        """Karta NVIDIA bez bibliotek CUDA: proponujemy ich pobranie (jednorazowo)."""
        if self.gpu_task.running:
            return
        state = gpu_acceleration_state()
        local = not is_cloud_key(self._current_model)
        wanted = (
            local and state == "missing"
            and self.device_combo.currentData() == "auto"
            and not self.settings.value("gpu_banner_dismissed", False, type=bool)
        )
        if wanted and not self._gpu_auto_tried:
            # Zwykły użytkownik nie musi wiedzieć, czym są biblioteki CUDA: program sam
            # rozpoznaje kartę i przygotowuje przyspieszenie przy pierwszym uruchomieniu.
            self._gpu_auto_tried = True
            self.gpu_banner.setVisible(True)
            self._install_gpu_pack()
            return
        if wanted:
            self.gpu_banner.clear_progress()
            self.gpu_banner.set_message(
                "Przyspieszenie kartą graficzną nie jest jeszcze gotowe",
                f"Program musi jednorazowo pobrać biblioteki NVIDIA ({gpu_pack.APPROX_SIZE}). "
                "Do tego czasu rozpoznawanie działa wolniej, na procesorze.",
            )
            self.gpu_button.setVisible(True)
            self.gpu_button.setText("Pobierz teraz")
            self.gpu_later.setText("Nie teraz")
        self.gpu_banner.setVisible(wanted)
        # Komputer bez karty NVIDIA: podpowiadamy, jak uzyskać dobrą szybkość.
        self.cpu_banner.setVisible(
            local and state == "none" and not self.settings.value("cpu_banner_dismissed", False, type=bool)
        )

    def _dismiss_cpu_banner(self):
        self.settings.setValue("cpu_banner_dismissed", True)
        self.cpu_banner.setVisible(False)

    def _dismiss_gpu_banner(self):
        if self.gpu_task.running:
            self.gpu_task.cancel()
            return
        self.settings.setValue("gpu_banner_dismissed", True)
        self.gpu_banner.setVisible(False)

    def _install_gpu_pack(self):
        self.gpu_button.setVisible(False)
        self.gpu_later.setText("Anuluj")
        self.gpu_banner.set_message(
            "Przygotowuję przyspieszenie kartą graficzną (jednorazowo)",
            "Wykryto kartę NVIDIA. Możesz już dyktować — do końca pobierania nieco wolniej.",
        )
        self.gpu_banner.set_progress(0, 0)
        self.gpu_task.start(lambda progress, cancel: gpu_pack.install(progress, cancel))

    def _on_gpu_progress(self, done: float, total: float):
        self.gpu_banner.set_progress(done, total)
        if total > 0:
            self.gpu_banner.set_message(
                "Przygotowuję przyspieszenie kartą graficzną (jednorazowo)",
                f"Pobrano {format_size(done)} z {format_size(total)}. Możesz już dyktować — na razie nieco wolniej.",
            )

    def _on_gpu_installed(self, _directory):
        self.gpu_banner.setVisible(False)
        reset_gpu_libraries()
        if self.engine.listening:
            # Nie przerywamy dyktowania — karta zostanie użyta przy następnym ładowaniu modelu.
            self.model_label.setText("Przyspieszenie GPU gotowe — zadziała po zakończeniu dyktowania.")
            self._gpu_reload_pending = True
            return
        if not is_cloud_key(self._current_model):
            self._load_model()

    def _on_gpu_failed(self, message: str):
        self.gpu_banner.clear_progress()
        self.gpu_banner.set_message("Przyspieszenie kartą graficzną nie jest jeszcze gotowe", message)
        self.gpu_button.setVisible(True)
        self.gpu_button.setText("Spróbuj ponownie")
        self.gpu_later.setText("Nie teraz")

    def _overlay_typing(self, enabled: bool):
        self.typing_check.setChecked(enabled)

    def _restart_hotkey(self):
        if self.hotkey is not None:
            self.hotkey.stop()
            self.hotkey = None
        combo = self.hotkey_combo.currentData() or DEFAULT_HOTKEY
        hold = self.mode_hold.isChecked()
        self.settings.setValue("hold_mode", hold)
        if not HOTKEY_SUPPORTED:
            self.hotkey_hint.setText("Kliknij mikrofon, aby zacząć.")
            return
        listener = HotkeyListener(combo, self.bridge.hotkey_pressed.emit, self.bridge.hotkey_released.emit, hold)
        if listener.start():
            self.hotkey = listener
            if hold:
                self.hotkey_hint.setText(f"albo przytrzymaj {combo} i mów — w dowolnym oknie")
            else:
                self.hotkey_hint.setText(f"albo naciśnij {combo} — w dowolnym oknie")
        else:
            self.hotkey_hint.setText(f"Skrót {combo} jest zajęty przez inny program — wybierz inny.")

    def _overlay_setting_changed(self, always: bool):
        if always:
            self._show_overlay()
        elif not self.engine.listening:
            self.overlay.hide()

    # -- sterowanie --------------------------------------------------------------------

    def _toggle_from_window(self):
        self.started_from_window = not self.engine.listening
        self.toggle_listening()

    def _mic_changed(self):
        """Zmiana źródła na liście działa od razu, także w trakcie dyktowania."""
        if not self.engine.listening:
            return
        try:
            self.engine.switch_microphone(self.mic_combo.currentData() or None)
        except Exception as exc:
            log.exception("Zmiana źródła dźwięku")
            self.stop_listening()
            QMessageBox.critical(self, APP_TITLE, f"Nie udało się przełączyć na to źródło.\n\n{exc}")

    def toggle_listening(self):
        if self.engine.listening:
            self.stop_listening()
        else:
            self.start_listening()

    def _on_hotkey_pressed(self):
        if self.mode_hold.isChecked():
            if not self.engine.listening:
                self.start_listening()
        else:
            self.toggle_listening()

    def _on_hotkey_released(self):
        if self.mode_hold.isChecked() and self.engine.listening:
            self.stop_listening()

    def start_listening(self):
        if self.model_state == "error":
            if is_cloud_key(self._current_model):
                resolved = self.api.resolve(self._current_model)
                if resolved is not None and resolved.needs_key:
                    if self._current_model.startswith("custom:"):
                        self._custom_button_clicked()
                    else:
                        self._open_keys(CLOUD_MODELS[self._current_model].provider)
                    return
            QMessageBox.warning(self, APP_TITLE, "Model rozpoznawania mowy nie jest gotowy:\n\n" + self.model_label.text())
            return
        if self.session.full():
            self.archive.append(self.session.full())
        self.session = SessionText(leading_space=self.space_check.isChecked())
        self.typer.reset()
        latency.reset_session()
        try:
            self.engine.start_listening(self.mic_combo.currentData() or None)
        except ModuleNotFoundError:
            QMessageBox.critical(
                self, APP_TITLE,
                "Brakuje biblioteki mikrofonu „sounddevice”.\n\n"
                "Zainstaluj program ponownie albo, przy uruchamianiu ze źródeł, wykonaj:\n"
                "pip install -r requirements.txt",
            )
            return
        except Exception as exc:
            log.exception("Mikrofon")
            QMessageBox.critical(self, APP_TITLE, f"Nie udało się włączyć mikrofonu.\n\n{exc}")
            return
        self.hide_overlay_timer.stop()
        self._show_overlay()
        if batch_transcription_running():
            self.overlay.hint.setText("BAPO-transcript pracuje")
            self.overlay.hint.setToolTip("Trwa transkrypcja plików — dyktowanie może działać wolniej.")
        else:
            self.overlay.hint.setText("")
        if self.started_from_window and self.minimize_check.isChecked() and TYPING_SUPPORTED:
            self.showMinimized()
        self.started_from_window = False
        self._render_transcript()

    def stop_listening(self):
        self.engine.stop_listening()

    def show_window(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _show_overlay(self):
        if not self.overlay.isVisible():
            self.overlay.show()

    def _overlay_close(self):
        if self.engine.listening:
            self.stop_listening()
        self.overlay_check.setChecked(False)
        self.overlay.hide()

    def _auto_hide_overlay(self):
        if not self.engine.listening and not self.overlay_check.isChecked() and not self.overlay.underMouse():
            self.overlay.hide()

    # -- zdarzenia silnika ------------------------------------------------------------

    def _on_status(self, text: str, busy: bool):
        self.model_label.setText(text)
        self.progress.setVisible(busy)
        self._set_pill(text if len(text) < 34 else "Przygotowanie modelu…", "busy")
        if not self.engine.listening:
            self.overlay.status.setText("Przygotowanie…")

    def _on_ready(self, info: ModelInfo):
        self.model_state = "ready"
        self.model_info = info
        self.progress.setVisible(False)
        self.model_label.setText(f"{info.label} · wykrywanie mowy: {info.vad}")
        self._update_controls()
        self._update_gpu_banner()

    def _on_failed(self, text: str):
        if self.model_state == "loading":
            self.model_state = "error"
        self.progress.setVisible(False)
        self.model_label.setText(text)
        self._update_controls()
        if self.engine.listening and self.model_state == "error":
            self.stop_listening()
        self._set_pill("Błąd", "error")
        if self.model_state == "ready":
            # Pojedynczy błąd przebiegu — model nadal działa.
            self.overlay.hint.setText("Błąd — szczegóły w oknie")
            QTimer.singleShot(5000, lambda: self.overlay.hint.setText(""))
            QTimer.singleShot(5000, self._update_controls)

    def _on_partial(self, delta: str, tentative: str):
        self.session.partial(delta, tentative)
        self._text_changed()

    def _on_final(self, delta: str):
        self.session.final(delta)
        self._text_changed(final=True)

    def _text_changed(self, final: bool = False):
        handler_start = time.perf_counter()
        stable, tentative = self.session.split()
        self.overlay.set_text(stable, tentative)
        latency.ui_text(len(stable), final, len(tentative))
        self.typer.set_text(stable)
        render_start = time.perf_counter()
        self._render_transcript()
        latency.ui_cost(render_start - handler_start, time.perf_counter() - render_start,
                        len(self.archive), len(stable))

    def _on_speech(self, active: bool):
        if self.engine.listening:
            self.overlay.status.setText("Rozpoznaję…" if active else "Słucham")

    def _on_listening(self, active: bool):
        self.overlay.set_listening(active)
        self._update_controls()
        if not active:
            self.hide_overlay_timer.start(2500)
            if self._gpu_reload_pending:
                self._gpu_reload_pending = False
                if not is_cloud_key(self._current_model):
                    QTimer.singleShot(0, self._load_model)

    def _update_controls(self):
        listening = self.engine.listening
        if listening:
            state = "listening"
        elif self.model_state == "loading":
            state = "loading"
        elif self.model_state == "error":
            state = "error"
        else:
            state = "idle"
        self.big_mic.set_state(state)
        self.overlay.mic.set_state(state)
        self.overlay.set_listening(listening)
        if listening:
            self.control_label.setText("Słucham — kliknij, aby zakończyć")
            self.overlay.status.setText("Słucham" if self.model_state == "ready" else "Nagrywam, model się ładuje…")
            self._set_pill("Słucham", "live")
        elif self.model_state == "loading":
            self.control_label.setText("Model się ładuje…")
            self.overlay.status.setText("Przygotowanie…")
        elif self.model_state == "error":
            self.control_label.setText("Model niedostępny")
            self.overlay.status.setText("Błąd modelu")
            self._set_pill("Błąd", "error")
        else:
            self.control_label.setText("Rozpocznij dyktowanie")
            self.overlay.status.setText("Gotowy")
            cloud = self.model_info is not None and self.model_info.device == "api"
            self._set_pill("Gotowy · chmura" if cloud else "Gotowy", "ready")
        if self.tray is not None:
            self.tray_toggle.setText("Zatrzymaj dyktowanie" if listening else "Rozpocznij dyktowanie")
            self.tray.setToolTip(f"{APP_TITLE} — {'słucham' if listening else 'gotowy'}")

    def _set_pill(self, text: str, kind: str):
        self.status_pill.setText(text)
        self.status_pill.setProperty("pill", kind)
        self.status_pill.style().unpolish(self.status_pill)
        self.status_pill.style().polish(self.status_pill)

    # -- odświeżanie -------------------------------------------------------------------

    def _tick_level(self):
        latency.ui_tick()  # timer 33 ms: spóźnienie = wątek interfejsu był zajęty
        listening = self.engine.listening
        level = level_to_unit(self.engine.level) if listening else 0.0
        if listening or self.big_mic.state == "loading":
            self.big_mic.set_level(level)
            self.overlay.mic.set_level(level)
        if listening or any(self.main_wave.values):
            self.main_wave.push(level, listening)
            self.overlay.wave.push(level, listening)

    def _tick_window(self):
        if not TYPING_SUPPORTED:
            self.target_label.setText("Pisanie do: tylko podgląd w tym oknie")
            return
        self._update_permission_banner()
        info = foreground_window()
        if info is None or info.own:
            if self.engine.listening and self.typer.enabled:
                self.overlay.target.setText("→ kliknij tam, gdzie mam pisać")
            return
        warning = ""
        if info.elevated and not self.own_elevated:
            warning = "  ⚠ program jako administrator — Windows zablokuje wpisywanie"
        prefix = "→ " if self.typer.enabled else "⏸ "
        self.overlay.target.setText(prefix + info.label + warning)
        self.overlay.target.setToolTip(info.title)
        self.target_label.setText(f"Pisanie do: {info.label}{warning}")

    def _render_transcript(self):
        stable, tentative = self.session.split()
        if not SHOW_TENTATIVE:
            tentative = ""
        faint = motyw.color("text_faint")
        parts = [f'<p style="margin:0 0 10px 0">{html.escape(text)}</p>' for text in self.archive]
        current = html.escape(stable) + f'<span style="color:{faint}">{html.escape(tentative)}</span>'
        if stable or tentative:
            parts.append(f'<p style="margin:0">{current}</p>')
        if not parts:
            if is_cloud_key(self._current_model):
                parts.append(
                    f'<p style="color:{faint}">Tu zobaczysz tekst. Model w chmurze rozpoznaje całą '
                    'wypowiedź naraz — tekst pojawia się po krótkiej pauzie w mówieniu.</p>'
                )
            else:
                parts.append(
                    f'<p style="color:{faint}">Tu zobaczysz tekst w trakcie mówienia. '
                    'Pokazywany jest tylko tekst ostateczny.</p>'
                )
        bar = self.transcript.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 4
        self.transcript.setHtml("".join(parts))
        if at_bottom:
            bar.setValue(bar.maximum())
        words = len(self._all_text().split())
        self.counter.setText(f"{words} słów" if words else "")

    def _all_text(self) -> str:
        texts = list(self.archive)
        if self.session.full():
            texts.append(self.session.full())
        return "\n\n".join(texts)

    def _copy_all(self):
        QGuiApplication.clipboard().setText(self._all_text())
        self._set_pill("Skopiowano", "ready")
        QTimer.singleShot(1500, self._update_controls)

    def _save_txt(self):
        default = Path.home() / "Documents" / f"dyktowanie_{time.strftime('%Y-%m-%d_%H-%M')}.txt"
        path, _ = QFileDialog.getSaveFileName(self, "Zapisz transkrypcję", str(default), "Tekst (*.txt)")
        if path:
            Path(path).write_text(self._all_text() + "\n", encoding="utf-8")

    def _clear(self):
        self.archive.clear()
        if not self.engine.listening:
            self.session = SessionText(leading_space=self.space_check.isChecked())
        self.overlay.set_text(*self.session.split())
        self._render_transcript()

    # -- zamykanie -------------------------------------------------------------------------

    def closeEvent(self, event):
        self._save_settings()
        self.gpu_task.cancel()
        self.update_task.cancel()
        if self.hotkey is not None:
            self.hotkey.stop()
        self.typer.stop()
        self.engine.shutdown()
        self.overlay.close()
        if self.tray is not None:
            self.tray.hide()
        event.accept()
        QApplication.quit()


def run(argv=None) -> int:
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    if IS_WINDOWS:
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
        except (AttributeError, OSError):
            pass
    app = QApplication(list(argv or sys.argv))
    app.setApplicationName(APP_TITLE)
    app.setApplicationDisplayName(APP_TITLE)
    app.setApplicationVersion(__version__)
    app.setOrganizationName(ORGANIZATION)
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(app_icon())
    app.setStyle("Fusion")
    app.setFont(motyw.app_font())
    mode = str(app_settings().value("theme", "auto"))
    motyw.set_mode(app, mode if mode in motyw.MODES else "auto")

    from PySide6.QtCore import QLockFile, QStandardPaths

    lock_dir = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.TempLocation)
    lock = QLockFile(str(Path(lock_dir) / "mydict.lock"))
    lock.setStaleLockTime(0)
    # Po aktualizacji nowa wersja startuje, zanim stara zdąży się zamknąć — chwilę czekamy.
    if not lock.tryLock(8000 if "--po-aktualizacji" in sys.argv else 100):
        QMessageBox.information(None, APP_TITLE, f"{APP_TITLE} już działa — znajdziesz go {TRAY_HINT}.")
        return 0

    # Brakujące składniki (zwykle pobrane już przez instalator; na macOS — pierwszy start).
    try:
        import dyktowanie_przygotowanie as przygotowanie

        przygotowanie.show_if_needed(app_settings())
    except Exception:
        log.exception("Kończenie instalacji nie powiodło się — program startuje mimo to")

    window = MainWindow()
    app.styleHints().colorSchemeChanged.connect(lambda _scheme: window.apply_theme())
    window.show()
    code = app.exec()
    lock.unlock()
    return code
