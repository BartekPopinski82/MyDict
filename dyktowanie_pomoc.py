"""Pomoc dostępna w oknie programu: komendy dyktowania (z aktywnych komend)
oraz informacje o programie z podglądem plików licencji."""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMessageBox, QPlainTextEdit, QPushButton, QTableWidget, QTableWidgetItem, QTabWidget,
    QVBoxLayout, QWidget,
)

import dyktowanie_deinstalacja as deinstalacja
import dyktowanie_motyw as motyw
from dyktowanie_app import APP_NAME, ORGANIZATION, __version__, resource_dir
from dyktowanie_komendy import COMMANDS

# Pliki licencji dołączane do programu (packaging/MyDict.spec, instalator).
LICENSE_FILES = (
    ("Licencja programu MyDict", "LICENSE.txt"),
    ("Licencje składników (Qt, PySide6, Python i inne)", "THIRD-PARTY-NOTICES.txt"),
)


class LicenseViewer(QDialog):
    """Podgląd pliku licencji w oknie programu (tylko do odczytu)."""

    def __init__(self, title: str, file_name: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(820, 700)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 14)
        text = QPlainTextEdit()
        text.setReadOnly(True)
        text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        text.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        text.setAccessibleName(title)
        path = resource_dir() / file_name
        try:
            text.setPlainText(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            text.setPlainText(f"Nie znaleziono pliku {file_name} w folderze programu.")
        layout.addWidget(text, 1)
        layout.addLayout(motyw.close_row(self.close))


class HelpDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Pomoc")
        self.resize(800, 720)
        self.setMinimumSize(640, 500)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(12)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._commands_page(), "Komendy")
        self.tabs.addTab(self._about_page(), "O programie")
        layout.addWidget(self.tabs, 1)

        layout.addLayout(motyw.close_row(self.close))
        self.search.textChanged.connect(self._filter)
        QTimer.singleShot(0, self.table.resizeRowsToContents)

    def _commands_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(4, 12, 4, 4)
        layout.setSpacing(12)

        title = QLabel("Znaki i komendy głosowe")
        title.setProperty("sectionTitle", True)
        layout.addWidget(title)
        intro = QLabel(
            "Podczas dyktowania powiedz nazwę komendy, a program wpisze znak. "
            "Komendy z kilku słów wypowiadaj bez długiej pauzy w środku."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Szukaj komendy lub znaku, np. nawias, myślnik, @…")
        self.search.setClearButtonEnabled(True)
        self.search.setAccessibleName("Szukaj komendy lub znaku")
        layout.addWidget(self.search)

        grouped = {}
        for phrase, result in COMMANDS.items():
            grouped.setdefault(result, []).append(phrase)
        self.table = QTableWidget(len(grouped), 3)
        self.table.setHorizontalHeaderLabels(["Powiedz", "Program wpisze", "Odstępy"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(False)
        self.table.setWordWrap(True)
        self.table.verticalHeader().hide()
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        spacing = {
            "close": "Bez spacji przed znakiem",
            "open": "Bez spacji po znaku",
            "join": "Bez spacji po obu stronach",
            "space": "Spacje między słowami",
            "break": "Przejście do nowego wiersza",
        }
        for row, ((symbol, kind), phrases) in enumerate(grouped.items()):
            result = {"\n": "Nowy wiersz (Enter)",
                      "\n\n": "Nowy akapit (2 × Enter)"}.get(symbol, symbol)
            for col, value in enumerate((" / ".join(phrases), result, spacing[kind])):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                if col == 1:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row, col, item)
        layout.addWidget(self.table, 1)

        tips = QLabel(
            '<b>Nazwa jako słowo:</b> powiedz „dosłownie dwukropek”, aby zapisać '
            'słowo „dwukropek”.<br>'
            '<b>Przykład:</b> „Uwaga dwukropek to ważne wykrzyknik” → „Uwaga: to ważne!”<br>'
            '<b>Lista:</b> „Zakupy dwukropek nowa linia myślnik mleko” → lista z pozycją „- mleko”.<br>'
            '<b>Uwaga:</b> nowa linia i nowy akapit wysyłają Enter. W komunikatorze '
            'może to wysłać wiadomość.'
        )
        tips.setWordWrap(True)
        layout.addWidget(tips)
        return page

    def _about_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(4, 12, 4, 4)
        layout.setSpacing(12)

        title = QLabel(f"{APP_NAME} {__version__}")
        title.setProperty("sectionTitle", True)
        layout.addWidget(title)
        # Informacja o prawach autorskich bibliotek LGPL i odesłanie do tekstów GPL/LGPL
        # są wymagane, gdy program pokazuje własne informacje o prawach autorskich (LGPL-3.0, pkt 4c).
        info = QLabel(
            f"Dyktowanie: zamiana mowy na tekst wpisywany do dowolnego programu.<br><br>"
            f"© 2025-2026 {ORGANIZATION}. Program bezpłatny (freeware), nie jest oprogramowaniem "
            f"otwartym. Zasady korzystania określa licencja programu.<br><br>"
            f"Program korzysta ze składników osób trzecich na ich własnych licencjach, m.in. "
            f"Qt i PySide6 (© The Qt Company Ltd. i inni, GNU LGPL 3.0), Python (PSF), "
            f"faster-whisper, CTranslate2, whisper.cpp i modeli Whisper (MIT). Pełne teksty "
            f"licencji, w tym GNU LGPL i GNU GPL, oraz informacje o kodzie źródłowym tych "
            f"składników zawiera lista licencji składników.<br><br>"
            f'Kontakt i zgłoszenia: <a href="https://github.com/BartekPopinski82/MyDict/issues">'
            f"github.com/BartekPopinski82/MyDict</a>"
        )
        info.setWordWrap(True)
        info.setOpenExternalLinks(True)
        info.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        layout.addWidget(info)

        for label, file_name in LICENSE_FILES:
            button = QPushButton(label)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _=False, t=label, f=file_name: self._show_license(t, f))
            row = QHBoxLayout()
            row.addWidget(button)
            row.addStretch()
            layout.addLayout(row)
        if deinstalacja.can_uninstall():
            uninstall = QPushButton(f"Odinstaluj {APP_NAME}…")
            uninstall.setCursor(Qt.CursorShape.PointingHandCursor)
            uninstall.setToolTip("Usuń program, pobrane składniki i modele, ustawienia oraz skrót z pulpitu")
            uninstall.clicked.connect(self._uninstall)
            row = QHBoxLayout()
            row.addWidget(uninstall)
            row.addStretch()
            layout.addSpacing(8)
            layout.addLayout(row)
        layout.addStretch(1)
        return page

    def _uninstall(self) -> None:
        answer = QMessageBox.question(
            self,
            f"Odinstaluj {APP_NAME}",
            f"Odinstalować {APP_NAME}?\n\n"
            "Zostaną usunięte: program, skróty na pulpicie i w menu, uruchamianie z systemem, "
            "ustawienia i zapisane klucze API, dzienniki, pobrane składniki (obsługa karty "
            "graficznej, whisper.cpp) oraz pobrane modele rozpoznawania mowy.\n\n"
            "Program zamknie się teraz.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            deinstalacja.start_uninstall()
        except OSError as error:
            QMessageBox.critical(self, f"Odinstaluj {APP_NAME}", f"Nie udało się uruchomić deinstalacji: {error}")
            return
        window = self.parent()
        self.close()
        if window is not None:
            window.close()   # zamyka silnik i serwer whisper.cpp, potem kończy program
        else:
            QApplication.quit()

    def _show_license(self, title: str, file_name: str) -> None:
        LicenseViewer(title, file_name, self).exec()

    def _filter(self, text):
        fold = str.maketrans("ąćęłńóśźż", "acelnoszz")
        query = text.casefold().translate(fold).strip()
        for row in range(self.table.rowCount()):
            values = " ".join(self.table.item(row, col).text() for col in range(3))
            self.table.setRowHidden(row, query not in values.casefold().translate(fold))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "table"):
            self.table.resizeRowsToContents()
