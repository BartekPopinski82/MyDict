"""Okno „Zgłoś problem”: opis, kontakt, zgoda na dołączenie dziennika, wysłanie w tle."""

from __future__ import annotations

import threading

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

import dyktowanie_zgloszenia as reports


class _Worker(QObject):
    done = Signal(str)
    failed = Signal(str)

    def run(self, report: str, kind: str):
        def work():
            try:
                self.done.emit(reports.send(report, kind))
            except reports.ReportError as exc:
                self.failed.emit(str(exc))
            except Exception as exc:  # nieprzewidziany błąd nie może zamknąć programu
                self.failed.emit(f"Nie udało się wysłać zgłoszenia: {exc}")

        threading.Thread(target=work, name="dyktowanie-zgloszenie", daemon=True).start()


def _helper(text: str) -> QLabel:
    label = QLabel(text)
    label.setProperty("helper", True)
    label.setWordWrap(True)
    return label


class ReportDialog(QDialog):
    """``crash`` — opis awarii z poprzedniej sesji; pusty = zgłoszenie z menu."""

    def __init__(self, parent=None, crash: str = ""):
        super().__init__(parent)
        self.crash = crash
        self.kind = "awaria" if crash else "zgloszenie"
        self.can_send = reports.can_send()
        self.setWindowTitle("Zgłoś problem")
        self.setMinimumSize(520, 440)
        self.worker = _Worker(self)
        self.worker.done.connect(self._on_sent)
        self.worker.failed.connect(self._on_failed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(10)
        title = QLabel("Poprzednio program zakończył się błędem" if crash else "Zgłoś problem")
        title.setProperty("sectionTitle", True)
        layout.addWidget(title)
        layout.addWidget(_helper(
            "Zgłoszenie trafia tylko do autora programu, zaszyfrowane. Zawiera wersję programu, "
            "system i — jeśli zostawisz zaznaczone — końcówkę dziennika programu, w której mogą "
            "być fragmenty podyktowanego tekstu."
            if self.can_send else
            "Ta kopia programu nie wysyła zgłoszeń sama. Zapisz zgłoszenie do pliku i dołącz je "
            "do wiadomości do autora."
        ))
        layout.addWidget(QLabel("Co się stało? (opcjonalnie)"))
        self.description = QPlainTextEdit()
        self.description.setPlaceholderText("Np. co robiłeś, gdy pojawił się problem, i czego się spodziewałeś.")
        layout.addWidget(self.description, 1)
        layout.addWidget(QLabel("Kontakt do odpowiedzi (opcjonalnie)"))
        self.contact = QLineEdit()
        self.contact.setPlaceholderText("imię lub e-mail")
        layout.addWidget(self.contact)
        self.include_log = QCheckBox("Dołącz dziennik programu")
        self.include_log.setChecked(True)
        layout.addWidget(self.include_log)
        self.status = _helper("")
        layout.addWidget(self.status)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.save_button = QPushButton("Zapisz do pliku")
        self.save_button.setProperty("flat", True)
        self.save_button.clicked.connect(self._save)
        buttons.addWidget(self.save_button)
        buttons.addStretch(1)
        self.cancel_button = QPushButton("Nie wysyłaj" if crash else "Anuluj")
        self.cancel_button.clicked.connect(self._dismiss)
        buttons.addWidget(self.cancel_button)
        self.send_button = QPushButton("Wyślij")
        self.send_button.setProperty("primary", True)
        self.send_button.setDefault(True)
        self.send_button.setVisible(self.can_send)
        self.send_button.clicked.connect(self._send)
        buttons.addWidget(self.send_button)
        layout.addLayout(buttons)

    def _report(self) -> str:
        return reports.build_report(self.description.toPlainText(), self.contact.text(),
                                    self.crash, self.include_log.isChecked())

    def _send(self):
        self.send_button.setEnabled(False)
        self.save_button.setEnabled(False)
        self.status.setText("Wysyłanie…")
        self.worker.run(self._report(), self.kind)

    def _on_sent(self, _path: str):
        reports.clear_crash()
        self.status.setText("Wysłano. Dziękuję!")
        self.cancel_button.setText("Zamknij")
        self.send_button.setVisible(False)

    def _on_failed(self, message: str):
        self.status.setText(message)
        self.send_button.setEnabled(True)
        self.save_button.setEnabled(True)

    def _save(self):
        try:
            path = reports.save_to_file(self._report())
        except OSError as exc:
            self.status.setText(f"Nie udało się zapisać: {exc}")
            return
        reports.clear_crash()
        self.status.setText(f"Zapisano: {path}")
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))

    def _dismiss(self):
        if self.crash:
            reports.clear_crash()   # nie pytamy drugi raz o tę samą awarię
        self.reject()
