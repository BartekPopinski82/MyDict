"""Pomoc dostępna w oknie programu; lista pochodzi z aktywnych komend."""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from dyktowanie_komendy import COMMANDS


class HelpDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Pomoc — komendy dyktowania")
        self.resize(800, 720)
        self.setMinimumSize(640, 500)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
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
        buttons = QHBoxLayout()
        buttons.addStretch()
        close = QPushButton("Zamknij")
        close.clicked.connect(self.close)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        self.search.textChanged.connect(self._filter)
        QTimer.singleShot(0, self.table.resizeRowsToContents)

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
