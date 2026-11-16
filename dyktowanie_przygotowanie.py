"""Kończenie instalacji: pobranie składników, bez których program działałby „w połowie”.

Instalator uruchamia program z ``--przygotuj`` i czeka na zakończenie. W tym czasie
okno poniżej rozpoznaje sprzęt i pobiera:

* biblioteki NVIDIA — tylko gdy w komputerze jest karta NVIDIA,
* model rozpoznawania mowy dobrany do sprzętu.

Dzięki temu po instalacji program jest od razu gotowy. Na macOS (instalacja przez
przeciągnięcie aplikacji) to samo okno pojawia się przy pierwszym uruchomieniu.
Gdy użytkownik pominie pobieranie albo nie ma internetu, program dokończy je sam
przy starcie.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

import dyktowanie_gpu_pakiet as gpu_pack
import dyktowanie_motyw as motyw
import dyktowanie_sprzet as sprzet
import dyktowanie_whispercpp as whispercpp
from dyktowanie_app import APP_NAME
from dyktowanie_asr import MODELS, gpu_acceleration_state, reset_gpu_libraries
from dyktowanie_pobieranie import Cancelled


log = logging.getLogger("dyktowanie.przygotowanie")
MODEL_FILES = ("config.json", "preprocessor_config.json", "model.bin", "tokenizer.json", "vocabulary.*")


# ---------------------------------------------------------------------------
# Model rozpoznawania mowy
# ---------------------------------------------------------------------------


def model_name(key: str) -> str:
    return MODELS.get(key, MODELS[sprzet.MODEL_CPU])[0]


def model_cached(key: str) -> bool:
    try:
        from faster_whisper import download_model

        download_model(model_name(key), local_files_only=True)
        return True
    except Exception:
        return False


def _model_repo(key: str) -> str:
    from faster_whisper.utils import _MODELS

    return _MODELS[model_name(key)]


def _model_size(repo: str) -> int:
    """Łączny rozmiar plików modelu (0, gdy serwer go nie podał)."""
    import fnmatch

    from huggingface_hub import HfApi

    info = HfApi().model_info(repo, files_metadata=True)
    return sum(
        int(item.size or 0) for item in info.siblings or ()
        if any(fnmatch.fnmatch(item.rfilename, pattern) for pattern in MODEL_FILES)
    )


def _directory_size(path: Path) -> int:
    total = 0
    try:
        for item in path.rglob("*"):
            try:
                if item.is_file() and not item.is_symlink():
                    total += item.stat().st_size
            except OSError:
                continue
    except OSError:
        pass
    return total


def download_model(key: str, progress=None, cancel: threading.Event | None = None) -> None:
    """Pobiera model do pamięci podręcznej Hugging Face. Postęp liczymy z przyrostu
    plików na dysku — działa niezależnie od wersji biblioteki pobierającej."""
    from faster_whisper import download_model as fetch
    from huggingface_hub.constants import HF_HUB_CACHE

    repo = _model_repo(key)
    try:
        total = _model_size(repo)
    except Exception:
        log.info("Nie odczytano rozmiaru modelu %s", repo, exc_info=True)
        total = 0
    folder = Path(HF_HUB_CACHE) / ("models--" + repo.replace("/", "--"))
    start = _directory_size(folder)
    result: dict = {}

    def work():
        try:
            fetch(model_name(key))
        except Exception as exc:
            result["error"] = exc

    thread = threading.Thread(target=work, name="pobieranie-modelu", daemon=True)
    thread.start()
    while thread.is_alive():
        if cancel is not None and cancel.is_set():
            raise Cancelled("Pobieranie przerwane")   # wątek pobierania kończy się razem z programem
        if progress is not None:
            done = max(0, _directory_size(folder) - start)
            progress(min(done, total) if total else done, total)
        time.sleep(0.4)
    if "error" in result:
        raise RuntimeError(f"Nie udało się pobrać modelu: {result['error']}")
    if progress is not None and total:
        progress(total, total)


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------


def chosen_model(settings, hardware: sprzet.Hardware) -> str:
    """Model, którego program użyje po starcie: zapisany wybór albo dobrany do sprzętu."""
    saved = str(settings.value("model", "") or "") if settings is not None else ""
    return saved if saved in MODELS else hardware.recommended_model


def pending_steps(settings, hardware: sprzet.Hardware | None = None) -> list[tuple[str, str]]:
    """Lista brakujących składników: (rodzaj, opis)."""
    hardware = hardware or sprzet.detect()
    steps = []
    if gpu_acceleration_state() == "missing":
        steps.append(("gpu", f"Obsługa karty graficznej ({gpu_pack.APPROX_SIZE})"))
    key = chosen_model(settings, hardware)
    saved = str(settings.value("model", "") or "") if settings is not None else ""
    cloud = saved.startswith(("api:", "custom:"))
    if cloud:
        return steps
    if hardware.backend in ("vulkan", "metal"):
        # Drugi silnik (whisper.cpp) ma własny format modeli.
        if not whispercpp.model_cached(key):
            steps.append((f"ggml:{key}", f"Model rozpoznawania mowy ({whispercpp.MODELS[key][1]})"))
    elif not model_cached(key):
        steps.append((f"model:{key}", f"Model rozpoznawania mowy ({MODELS[key][2]})"))
    return steps


# ---------------------------------------------------------------------------
# Okno
# ---------------------------------------------------------------------------


class SetupWindow(QDialog):
    """Okno „Kończenie instalacji” z postępem pobierania składników."""

    def __init__(self, steps: list[tuple[str, str]], hardware: sprzet.Hardware, settings=None, parent=None):
        super().__init__(parent)
        from dyktowanie_ui import LogoMark, Task, app_icon, helper_label

        self.steps = list(steps)
        self.settings = settings
        self.index = -1
        self.failed = False
        self.setWindowTitle(f"{APP_NAME} — kończenie instalacji")
        self.setWindowIcon(app_icon())
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.setFixedWidth(520)
        self.task = Task(self)
        self.task.progress.connect(self._on_progress)
        self.task.done.connect(self._on_done)
        self.task.failed.connect(self._on_failed)
        self.task.cancelled.connect(self.accept)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 24, 26, 20)
        layout.setSpacing(12)
        top = QHBoxLayout()
        top.setSpacing(14)
        top.addWidget(LogoMark(48))
        titles = QVBoxLayout()
        titles.setSpacing(2)
        title = QLabel("Kończenie instalacji")
        title.setProperty("pageTitle", True)
        subtitle = QLabel(f"{APP_NAME} pobiera składniki potrzebne do pracy. To jednorazowe.")
        subtitle.setProperty("pageSubtitle", True)
        subtitle.setWordWrap(True)
        titles.addWidget(title)
        titles.addWidget(subtitle)
        top.addLayout(titles, 1)
        layout.addLayout(top)
        layout.addWidget(helper_label(hardware.summary))

        self.rows: list[QLabel] = []
        for _kind, text in self.steps:
            row = QLabel("")
            row.setTextFormat(Qt.TextFormat.RichText)
            self.rows.append(row)
            layout.addWidget(row)
        self.bar = QProgressBar()
        self.bar.setRange(0, 0)
        layout.addWidget(self.bar)
        self.status = helper_label("")
        layout.addWidget(self.status)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.skip = QPushButton("Pomiń — dokończ później")
        self.skip.setProperty("flat", True)
        self.skip.clicked.connect(self._skip)
        buttons.addWidget(self.skip)
        layout.addLayout(buttons)
        self._render_rows()
        QTimer.singleShot(200, self._next)

    def _render_rows(self):
        for position, (row, (_kind, text)) in enumerate(zip(self.rows, self.steps)):
            if position < self.index:
                mark, tone = "✓", motyw.color("success_text")
            elif position == self.index:
                mark, tone = "●", motyw.color("danger_text" if self.failed else "accent")
            else:
                mark, tone = "○", motyw.color("text_faint")
            weight = "600" if position == self.index else "400"
            row.setText(f'<span style="color:{tone}">{mark}</span>&nbsp;&nbsp;<span style="font-weight:{weight}">{text}</span>')

    def _next(self):
        self.index += 1
        self._render_rows()
        if self.index >= len(self.steps):
            self.bar.setRange(0, 1)
            self.bar.setValue(1)
            self.status.setText("Gotowe.")
            self.skip.setVisible(False)
            QTimer.singleShot(700, self.accept)
            return
        kind = self.steps[self.index][0]
        self.bar.setRange(0, 0)
        self.status.setText("Łączenie z serwerem…")
        if kind == "gpu":
            self.task.start(lambda progress, cancel: gpu_pack.install(progress, cancel))
        elif kind.startswith("ggml:"):
            key = kind.split(":", 1)[1]
            self.task.start(lambda progress, cancel: whispercpp.download_model(key, progress, cancel))
        else:
            key = kind.split(":", 1)[1]
            self.task.start(lambda progress, cancel: download_model(key, progress, cancel))

    def _on_progress(self, done: float, total: float):
        from dyktowanie_ui import format_size

        if total > 0:
            self.bar.setRange(0, 1000)
            self.bar.setValue(int(1000 * min(1.0, done / total)))
            self.status.setText(f"Pobrano {format_size(done)} z {format_size(total)}")
        else:
            self.status.setText(f"Pobrano {format_size(done)}")

    def _on_done(self, _result):
        if self.steps[self.index][0] == "gpu":
            reset_gpu_libraries()
        self._next()

    def _on_failed(self, message: str):
        self.failed = True
        self._render_rows()
        self.bar.setRange(0, 1)
        self.bar.setValue(0)
        self.status.setText(
            f'<span style="color:{motyw.color("danger_text")}">{message}</span><br>'
            f"{APP_NAME} dokończy pobieranie sam przy uruchomieniu, gdy będzie internet."
        )
        self.skip.setText("Zamknij")

    def _skip(self):
        if self.task.running:
            self.task.cancel()
            self.skip.setEnabled(False)
            self.status.setText("Przerywanie…")
        else:
            self.accept()

    def reject(self):
        self._skip()


def show_if_needed(settings=None, parent=None) -> bool:
    """Pokazuje okno, gdy czegoś brakuje. Zwraca True, jeśli okno było potrzebne."""
    hardware = sprzet.detect()
    steps = pending_steps(settings, hardware)
    if not steps:
        return False
    log.info("Kończenie instalacji: %s", [kind for kind, _text in steps])
    SetupWindow(steps, hardware, settings, parent).exec()
    return True


def run_setup(argv: list[str]) -> int:
    """Tryb ``--przygotuj``: wywoływany przez instalator. Zawsze kończy się kodem 0 —
    brak internetu nie może zepsuć instalacji; program dokończy pobieranie sam."""
    if os.environ.get("MYDICT_TEST_INSTALACJI"):
        log.info("Test instalatora — pomijam pobieranie składników")
        return 0
    try:
        from dyktowanie_ui import app_icon, app_settings

        app = QApplication.instance() or QApplication(argv[:1])
        app.setApplicationName(APP_NAME)
        app.setWindowIcon(app_icon())
        app.setStyle("Fusion")
        app.setFont(motyw.app_font())
        settings = app_settings()
        mode = str(settings.value("theme", "auto"))
        motyw.set_mode(app, mode if mode in motyw.MODES else "auto")
        show_if_needed(settings)
    except Exception:
        log.exception("Kończenie instalacji nie powiodło się")
    return 0
