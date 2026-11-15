"""Interfejs Qt dyktowania na żywo: okno główne i pływający pasek."""

from __future__ import annotations

import html
import logging
import math
import os
import sys
import time
from collections import deque
from pathlib import Path

from PySide6.QtCore import QByteArray, QObject, QPoint, QRectF, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import (
    QAction,
    QColor,
    QFont,
    QFontDatabase,
    QGuiApplication,
    QIcon,
    QPainter,
    QPainterPath,
    QPalette,
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
    QGridLayout,
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

from dyktowanie_asr import (
    DEFAULT_MODEL,
    LANGUAGES,
    MODELS,
    DictationEngine,
    EngineCallbacks,
    Microphone,
    ModelInfo,
    batch_transcription_running,
)
from dyktowanie_api import CLOUD_MODELS, PROVIDERS, is_cloud_key
from dyktowanie_api_ui import ApiKeysDialog, ApiStore, CustomModelDialog
from dyktowanie_core import SessionText, display_tail, level_to_unit
from dyktowanie_latencja import tracker as latency
from dyktowanie_windows import (
    DEFAULT_HOTKEY,
    HOTKEYS,
    IS_WINDOWS,
    HotkeyListener,
    TypingEngine,
    foreground_window,
    make_window_non_activating,
    own_process_elevated,
)


log = logging.getLogger("dyktowanie.ui")
APP_TITLE = "BAPO-dyktowanie"
HERE = Path(__file__).resolve().parent
ICON_CANDIDATES = [
    HERE / "files" / ("ikonka.icns" if sys.platform == "darwin" else "ikonka.ico"),
    HERE.parent / "files" / ("ikonka.icns" if sys.platform == "darwin" else "ikonka.ico"),
]

PAUSE_OPTIONS = [
    (0.5, "Krótka — 0,5 s (szybciej, może dzielić zdania)"),
    (0.7, "Normalna — 0,7 s (zalecana)"),
    (1.0, "Dłuższa — 1 s (dla spokojnego dyktowania)"),
    (1.5, "Długa — 1,5 s"),
]

BLUE = "#087BF0"
CYAN = "#17B8D6"
RED = "#E5484D"
NAVY = "#0A1540"
MUTED = "#65738A"

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
}


# False = okno główne pokazuje wyłącznie tekst ostateczny (bez szarego tekstu wstępnego).
# Pływający pasek pokazuje tekst wstępny zawsze — jako znak, że program pracuje.
SHOW_TENTATIVE = False


def svg_icon(name: str, color: str = NAVY, size: int = 24) -> QIcon:
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" '
        f'fill="none" stroke="{color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
        f"{SVG_PATHS[name]}</svg>"
    )
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    return QIcon(pixmap)


def app_icon() -> QIcon:
    for path in ICON_CANDIDATES:
        if path.is_file():
            return QIcon(str(path))
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    LogoMark.paint_mark(painter, 64)
    painter.end()
    return QIcon(pixmap)


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
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(BLUE))
        painter.drawRoundedRect(QRectF(1, 1, size - 2, size - 2), size * 0.25, size * 0.25)
        painter.setBrush(QColor(CYAN))
        bar = max(2.0, size / 15)
        for index, ratio in enumerate((.30, .52, .30)):
            height = size * ratio
            x = size * 0.12 + index * (bar + size * 0.05)
            painter.drawRoundedRect(QRectF(x, (size - height) / 2, bar, height), bar / 2, bar / 2)
        _draw_mic(painter, size * 0.64, size * 0.52, size * 0.36, QColor("white"))

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
        colors = {"idle": BLUE, "listening": RED, "loading": "#8A97AD", "error": "#8A97AD"}
        color = QColor(colors.get(self.state, BLUE))
        if self.state == "listening":
            halo = QColor(RED)
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

    def __init__(self, bars=28, active_color=CYAN, idle_color="#C5D0DD", parent=None):
        super().__init__(parent)
        self.values = deque([0.0] * bars, maxlen=bars)
        self.active_color = QColor(active_color)
        self.idle_color = QColor(idle_color)
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
        painter.setBrush(self.active_color if self.active else self.idle_color)
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
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFixedSize(620, 96)
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

        self.wave = Waveform(18, CYAN, "#3B4A68")
        self.wave.setFixedSize(64, 30)
        layout.addWidget(self.wave)

        tools = QVBoxLayout()
        tools.setSpacing(2)
        row = QHBoxLayout()
        row.setSpacing(2)
        self.typing_button = ToolButton("keyboard", "Wpisywanie do aktywnego okna: włączone", "#C9D5E8")
        self.typing_button.setCheckable(True)
        self.typing_button.setChecked(True)
        self.typing_button.toggled.connect(self._typing_toggled)
        self.settings_button = ToolButton("gear", "Pokaż okno BAPO-dyktowanie", "#C9D5E8")
        self.settings_button.clicked.connect(self.settings_requested)
        self.close_button = ToolButton("close", "Ukryj pasek", "#C9D5E8")
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
        if not stable and not tentative:
            if self.listening:
                body = '<span style="color:#7F91B2">Mów — tekst pojawi się tutaj i w aktywnym oknie…</span>'
            else:
                body = '<span style="color:#7F91B2">Naciśnij mikrofon albo skrót, aby dyktować.</span>'
            self.text.setText(body)
            return
        full = stable + tentative
        tail, cut = display_tail(full, 150)
        split = max(0, len(tail) - len(tentative)) if tentative else len(tail)
        shown_stable, shown_tentative = tail[:split], tail[split:]
        caret = ""
        if self.listening:
            caret = f'<span style="color:{CYAN if self.caret_visible else "transparent"}">▍</span>'
        body = (
            ("… " if cut else "")
            + html.escape(shown_stable)
            + f'<span style="color:#8FA3C6">{html.escape(shown_tentative)}</span>'
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
        painter.setBrush(QColor(12, 21, 46, 242))
        painter.drawRoundedRect(rect, 22, 22)
        if self.listening:
            accent = QColor(RED)
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


def field_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setProperty("fieldLabel", True)
    return label


def helper_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setProperty("helper", True)
    label.setWordWrap(True)
    return label


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.setWindowIcon(app_icon())
        self.resize(1000, 680)
        self.setMinimumSize(880, 600)
        self.settings = QSettings("BAPO", APP_TITLE)
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
        self.started_from_window = False
        self.hide_overlay_timer = QTimer(self)
        self.hide_overlay_timer.setSingleShot(True)
        self.hide_overlay_timer.timeout.connect(self._auto_hide_overlay)

        self.overlay = Overlay()
        self._build()
        self._load_settings()
        self._connect()
        self._build_tray()

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

    # -- budowa ----------------------------------------------------------------

    def _build(self):
        root = QWidget()
        outer = QVBoxLayout(root)
        outer.setContentsMargins(22, 18, 22, 18)
        outer.setSpacing(14)

        header = QHBoxLayout()
        header.setSpacing(12)
        header.addWidget(LogoMark(44))
        titles = QVBoxLayout()
        titles.setSpacing(0)
        title = QLabel("Dyktowanie na żywo")
        title.setProperty("pageTitle", True)
        subtitle = QLabel("Mów — tekst pojawia się od razu w aktywnym oknie: Word, przeglądarka, poczta, komunikatory.")
        subtitle.setProperty("pageSubtitle", True)
        titles.addWidget(title)
        titles.addWidget(subtitle)
        header.addLayout(titles, 1)
        self.status_pill = QLabel("Przygotowanie…")
        self.status_pill.setProperty("pill", "busy")
        header.addWidget(self.status_pill, 0, Qt.AlignmentFlag.AlignTop)
        outer.addLayout(header)

        body = QHBoxLayout()
        body.setSpacing(16)

        # Lewa część: transkrypcja na żywo
        live = QFrame()
        live.setProperty("card", True)
        live_layout = QVBoxLayout(live)
        live_layout.setContentsMargins(18, 16, 18, 14)
        live_layout.setSpacing(10)
        live_top = QHBoxLayout()
        live_title = QLabel("Transkrypcja")
        live_title.setProperty("cardTitle", True)
        live_top.addWidget(live_title)
        live_top.addStretch(1)
        self.main_wave = Waveform(36, CYAN, "#D5DEEA")
        self.main_wave.setFixedSize(150, 26)
        live_top.addWidget(self.main_wave)
        live_layout.addLayout(live_top)
        self.target_label = QLabel("Okno docelowe: —")
        self.target_label.setProperty("targetLabel", True)
        live_layout.addWidget(self.target_label)
        self.transcript = QTextEdit()
        self.transcript.setReadOnly(True)
        self.transcript.setProperty("transcript", True)
        live_layout.addWidget(self.transcript, 1)
        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.copy_button = QPushButton("Kopiuj wszystko")
        self.copy_button.setIcon(svg_icon("copy", BLUE, 16))
        self.copy_button.setProperty("secondary", True)
        self.save_button = QPushButton("Zapisz jako .txt")
        self.save_button.setIcon(svg_icon("save", BLUE, 16))
        self.save_button.setProperty("secondary", True)
        self.clear_button = QPushButton("Wyczyść")
        self.clear_button.setIcon(svg_icon("trash", "#5E6C84", 16))
        actions.addWidget(self.copy_button)
        actions.addWidget(self.save_button)
        actions.addWidget(self.clear_button)
        actions.addStretch(1)
        self.counter = QLabel("")
        self.counter.setProperty("helper", True)
        actions.addWidget(self.counter)
        live_layout.addLayout(actions)
        body.addWidget(live, 1)

        # Prawa część: sterowanie i ustawienia
        side = QWidget()
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(0, 0, 0, 0)
        side_layout.setSpacing(12)

        control = QFrame()
        control.setProperty("card", True)
        control_layout = QVBoxLayout(control)
        control_layout.setContentsMargins(16, 10, 16, 14)
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
        control_layout.addSpacing(4)
        control_layout.addWidget(self.progress)
        self.model_label = QLabel("")
        self.model_label.setProperty("helper", True)
        self.model_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.model_label.setWordWrap(True)
        control_layout.addWidget(self.model_label)
        side_layout.addWidget(control)

        settings_card = QFrame()
        settings_card.setProperty("card", True)
        form = QGridLayout(settings_card)
        form.setContentsMargins(16, 14, 16, 14)
        form.setHorizontalSpacing(8)
        form.setVerticalSpacing(5)
        row = 0
        heading = QLabel("Ustawienia")
        heading.setProperty("sectionTitle", True)
        form.addWidget(heading, row, 0, 1, 2)
        row += 1

        form.addWidget(field_label("Model rozpoznawania"), row, 0, 1, 2)
        row += 1
        self.model_combo = QComboBox()
        self.model_combo.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.model_combo.setMaxVisibleItems(24)
        self._fill_models()
        form.addWidget(self.model_combo, row, 0, 1, 2)
        row += 1
        self.device_combo = QComboBox()
        self.device_combo.addItem("Karta graficzna, jeśli dostępna", "auto")
        self.device_combo.addItem("Tylko procesor (CPU)", "cpu")
        form.addWidget(self.device_combo, row, 0, 1, 2)
        row += 1
        self.cloud_hint = helper_label("")
        self.cloud_hint.setVisible(False)
        form.addWidget(self.cloud_hint, row, 0, 1, 2)
        row += 1
        api_row = QHBoxLayout()
        api_row.setSpacing(6)
        self.keys_button = QPushButton("Klucze API…")
        self.keys_button.setToolTip("Klucze OpenAI, Groq, ElevenLabs, Deepgram, AssemblyAI")
        self.custom_button = QPushButton("Własny model API…")
        self.custom_button.setToolTip("Wpisz ręcznie adres API, nazwę modelu i klucz")
        api_row.addWidget(self.keys_button)
        api_row.addWidget(self.custom_button)
        form.addLayout(api_row, row, 0, 1, 2)
        row += 1

        form.addWidget(field_label("Mikrofon"), row, 0, 1, 2)
        row += 1
        self.mic_combo = QComboBox()
        self.mic_combo.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.mic_refresh = QPushButton()
        self.mic_refresh.setIcon(svg_icon("refresh", "#5E6C84", 16))
        self.mic_refresh.setToolTip("Odśwież listę mikrofonów")
        self.mic_refresh.setFixedWidth(34)
        form.addWidget(self.mic_combo, row, 0)
        form.addWidget(self.mic_refresh, row, 1)
        row += 1

        form.addWidget(field_label("Język"), row, 0, 1, 2)
        row += 1
        self.language_combo = QComboBox()
        for code, label in LANGUAGES.items():
            self.language_combo.addItem(label, code)
        form.addWidget(self.language_combo, row, 0, 1, 2)
        row += 1

        form.addWidget(field_label("Skrót klawiszowy (działa w każdym oknie)"), row, 0, 1, 2)
        row += 1
        self.hotkey_combo = QComboBox()
        for combo in HOTKEYS:
            self.hotkey_combo.addItem(combo, combo)
        form.addWidget(self.hotkey_combo, row, 0, 1, 2)
        row += 1
        mode_row = QHBoxLayout()
        mode_row.setSpacing(0)
        self.mode_toggle = QPushButton("Naciśnij: start / stop")
        self.mode_hold = QPushButton("Przytrzymaj i mów")
        self.mode_group = QButtonGroup(self)
        for button in (self.mode_toggle, self.mode_hold):
            button.setCheckable(True)
            button.setProperty("segment", True)
            self.mode_group.addButton(button)
            mode_row.addWidget(button)
        form.addLayout(mode_row, row, 0, 1, 2)
        row += 1

        form.addWidget(field_label("Wpisywanie"), row, 0, 1, 2)
        row += 1
        self.typing_check = QCheckBox("Wpisuj tekst do aktywnego okna")
        form.addWidget(self.typing_check, row, 0, 1, 2)
        row += 1
        self.smooth_check = QCheckBox("Płynne pisanie (efekt pisania na żywo)")
        form.addWidget(self.smooth_check, row, 0, 1, 2)
        row += 1
        self.space_check = QCheckBox("Spacja przed pierwszym słowem sesji")
        form.addWidget(self.space_check, row, 0, 1, 2)
        row += 1

        form.addWidget(field_label("Pauza kończąca wypowiedź"), row, 0, 1, 2)
        row += 1
        self.pause_combo = QComboBox()
        for seconds, label in PAUSE_OPTIONS:
            self.pause_combo.addItem(label, seconds)
        form.addWidget(self.pause_combo, row, 0, 1, 2)
        row += 1

        form.addWidget(field_label("Słownik — nazwy własne i terminy"), row, 0, 1, 2)
        row += 1
        self.hotwords_edit = QLineEdit()
        self.hotwords_edit.setPlaceholderText("np. BAPO, WhisperX, Kraków")
        form.addWidget(self.hotwords_edit, row, 0, 1, 2)
        row += 1

        form.addWidget(field_label("Okna"), row, 0, 1, 2)
        row += 1
        self.overlay_check = QCheckBox("Pływający pasek zawsze widoczny")
        form.addWidget(self.overlay_check, row, 0, 1, 2)
        row += 1
        self.minimize_check = QCheckBox("Chowaj to okno po starcie przyciskiem")
        form.addWidget(self.minimize_check, row, 0, 1, 2)
        row += 1
        if not IS_WINDOWS:
            note = helper_label("Wpisywanie do innych aplikacji i globalny skrót działają na Windows.")
            form.addWidget(note, row, 0, 1, 2)
            row += 1

        side_layout.addWidget(settings_card)
        side_layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(side)
        scroll.setFixedWidth(328)
        body.addWidget(scroll)
        outer.addLayout(body, 1)
        self.setCentralWidget(root)

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
        menu.addAction(self.tray_toggle)
        menu.addAction(show)
        menu.addSeparator()
        menu.addAction(quit_action)
        self.tray.setContextMenu(menu)
        self.tray_menu = menu
        self.tray.activated.connect(self._tray_activated)
        self.tray.show()

    def _tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
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
        header("— W chmurze przez API (wymaga klucza) —")
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
        self._select(self.model_combo, s.value("model", DEFAULT_MODEL))
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
        for widget_signal in (
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
        self.custom_button.setText("Edytuj własny model…" if key.startswith("custom:") else "Własny model API…")
        if cloud:
            resolved = self.api.resolve(key)
            service = resolved.service if resolved else "usługi w chmurze"
            self.cloud_hint.setText(
                f"Tekst pojawia się po pauzie — całą wypowiedzią naraz. "
                f"Nagranie każdej wypowiedzi jest wysyłane do: {service} (płatne wg cennika dostawcy)."
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
        self.smooth_check.setEnabled(enabled and IS_WINDOWS)
        self.space_check.setEnabled(enabled and IS_WINDOWS)
        self.overlay.set_typing(enabled)

    def _overlay_typing(self, enabled: bool):
        self.typing_check.setChecked(enabled)

    def _restart_hotkey(self):
        if self.hotkey is not None:
            self.hotkey.stop()
            self.hotkey = None
        combo = self.hotkey_combo.currentData() or DEFAULT_HOTKEY
        hold = self.mode_hold.isChecked()
        self.settings.setValue("hold_mode", hold)
        if not HotkeyListener.available:
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
                "Uruchom program plikiem „Uruchom BAPO-dyktowanie.bat” — zainstaluje ją automatycznie.",
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
        if self.started_from_window and self.minimize_check.isChecked() and IS_WINDOWS:
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
        if not IS_WINDOWS:
            self.target_label.setText("Okno docelowe: tylko podgląd (wpisywanie działa na Windows)")
            return
        info = foreground_window()
        if info is None or info.own:
            if self.engine.listening and self.typer.enabled:
                self.overlay.target.setText("→ kliknij w okno, do którego mam pisać")
            return
        warning = ""
        if info.elevated and not self.own_elevated:
            warning = "  ⚠ program jako administrator — Windows zablokuje wpisywanie"
        prefix = "→ " if self.typer.enabled else "⏸ "
        self.overlay.target.setText(prefix + info.label + warning)
        self.overlay.target.setToolTip(info.title)
        self.target_label.setText(f"Okno docelowe: {info.label}{warning}")

    def _render_transcript(self):
        stable, tentative = self.session.split()
        if not SHOW_TENTATIVE:
            tentative = ""
        parts = [f'<p style="margin:0 0 10px 0">{html.escape(text)}</p>' for text in self.archive]
        current = html.escape(stable) + f'<span style="color:#8D9AB0">{html.escape(tentative)}</span>'
        if stable or tentative:
            parts.append(f'<p style="margin:0">{current}</p>')
        if not parts:
            if is_cloud_key(self._current_model):
                parts.append(
                    '<p style="color:#8D9AB0">Tu zobaczysz tekst. Model w chmurze rozpoznaje całą '
                    'wypowiedź naraz — tekst pojawia się po krótkiej pauzie w mówieniu.</p>'
                )
            else:
                parts.append(
                    '<p style="color:#8D9AB0">Tu zobaczysz tekst w trakcie mówienia. '
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
        if self.hotkey is not None:
            self.hotkey.stop()
        self.typer.stop()
        self.engine.shutdown()
        self.overlay.close()
        if self.tray is not None:
            self.tray.hide()
        event.accept()
        QApplication.quit()


STYLE = """
QWidget { color: #101936; font-size: 11px; }
QMainWindow, QScrollArea, QScrollArea > QWidget > QWidget { background: #F6F9FD; }
QLabel[pageTitle="true"] { font-size: 20px; font-weight: 700; color: #0A1540; }
QLabel[pageSubtitle="true"] { font-size: 12px; color: #65738A; }
QLabel[cardTitle="true"] { font-size: 15px; font-weight: 700; color: #0A1540; }
QLabel[sectionTitle="true"] { font-size: 13px; font-weight: 700; color: #0A1540; padding-bottom: 2px; }
QLabel[controlTitle="true"] { font-size: 13px; font-weight: 700; color: #0A1540; }
QLabel[fieldLabel="true"] { font-size: 11px; font-weight: 600; color: #14203B; padding-top: 6px; }
QLabel[helper="true"] { font-size: 10px; color: #6D7A91; }
QLabel[targetLabel="true"] { font-size: 11px; color: #4E5E78; background: #F2F6FB; border-radius: 6px; padding: 5px 8px; }
QLabel[pill] { border-radius: 11px; padding: 4px 11px; font-weight: 600; }
QLabel[pill="busy"] { background: #EEF2F7; color: #4E5E78; }
QLabel[pill="ready"] { background: #E7F5EE; color: #19744A; }
QLabel[pill="live"] { background: #FDECEC; color: #B4232A; }
QLabel[pill="error"] { background: #FDECEC; color: #B4232A; }
QFrame[card="true"] { background: #FFFFFF; border: 1px solid #DCE5F0; border-radius: 11px; }
QTextEdit[transcript="true"] {
    background: #FBFCFE; border: 1px solid #E3EAF3; border-radius: 8px; padding: 10px;
    font-size: 14px; color: #101936; selection-background-color: #087BF0;
}
QLineEdit, QComboBox {
    min-height: 28px; padding: 0 8px; background: #FFFFFF;
    border: 1px solid #CCD8E7; border-radius: 6px; selection-background-color: #087BF0;
}
QLineEdit:focus, QComboBox:focus { border: 1px solid #087BF0; }
QComboBox::drop-down { border: 0; width: 22px; }
QComboBox QAbstractItemView { background: #FFFFFF; border: 1px solid #CCD8E7; selection-background-color: #EAF4FF; selection-color: #0A1540; }
QPushButton {
    min-height: 28px; padding: 0 12px; border: 1px solid #CFDAE7;
    border-radius: 6px; background: #FFFFFF; font-weight: 600;
}
QPushButton:hover { background: #F0F6FD; border-color: #9FBDD9; }
QPushButton:disabled { color: #9DA8B7; background: #EFF2F6; border-color: #E0E5EC; }
QPushButton[primary="true"] { color: white; background: #087BF0; border-color: #087BF0; }
QPushButton[primary="true"]:hover { background: #0868CC; }
QDialog { background: #F6F9FD; }
QPushButton[secondary="true"] { color: #087BF0; border-color: #91C6FA; background: #F8FBFF; }
QPushButton[segment="true"] { border-radius: 0; background: #F8FAFD; border-color: #D2DCE8; padding: 0 6px; font-size: 10px; }
QPushButton[segment="true"]:checked { background: #087BF0; color: white; border-color: #087BF0; }
QCheckBox { spacing: 6px; padding: 2px 0; }
QCheckBox::indicator { width: 15px; height: 15px; }
QProgressBar { min-height: 5px; max-height: 5px; border: 0; border-radius: 3px; background: #E4EAF2; text-align: center; color: transparent; }
QProgressBar::chunk { border-radius: 3px; background: #087BF0; }
QScrollBar:vertical { width: 8px; background: transparent; margin: 1px; }
QScrollBar::handle:vertical { background: #C5D0DD; border-radius: 4px; min-height: 21px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QLabel[overlayStatus="true"] { color: #FFFFFF; font-size: 11px; font-weight: 700; background: transparent; }
QLabel[overlayTarget="true"] { color: #9FB3D1; font-size: 10px; background: transparent; }
QLabel[overlayText="true"] { color: #FFFFFF; font-size: 14px; background: transparent; }
QLabel[overlayHint="true"] { color: #F5B14C; font-size: 9px; background: transparent; }
QPushButton[overlayTool="true"] { min-height: 0; padding: 0; border: 0; border-radius: 15px; background: transparent; }
QPushButton[overlayTool="true"]:hover { background: rgba(255, 255, 255, 28); }
QPushButton[overlayTool="true"]:checked { background: transparent; }
QPushButton[overlayTool="true"][paused="true"] { background: rgba(245, 177, 76, 60); }
QToolTip { color: #101936; background: #FFFFFF; border: 1px solid #CCD8E7; padding: 4px; }
"""


def run(argv=None) -> int:
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    if IS_WINDOWS:
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("pl.bapo.dyktowanie")
        except (AttributeError, OSError):
            pass
    app = QApplication(list(argv or sys.argv))
    app.setApplicationName(APP_TITLE)
    app.setOrganizationName("BAPO")
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(app_icon())
    app.setStyle("Fusion")
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#F6F9FD"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#101936"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#FFFFFF"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#101936"))
    palette.setColor(QPalette.ColorRole.Button, QColor("#FFFFFF"))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#101936"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(BLUE))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#FFFFFF"))
    app.setPalette(palette)
    if IS_WINDOWS:
        font = QFont("Segoe UI", 8)
    else:
        font = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont)
        font.setPointSize(8)
    app.setFont(font)
    app.setStyleSheet(STYLE)

    from PySide6.QtCore import QLockFile, QStandardPaths

    lock_dir = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.TempLocation)
    lock = QLockFile(str(Path(lock_dir) / "bapo-dyktowanie.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        QMessageBox.information(None, APP_TITLE, "BAPO-dyktowanie już działa — znajdziesz je w zasobniku obok zegara.")
        return 0

    window = MainWindow()
    window.show()
    code = app.exec()
    lock.unlock()
    return code
