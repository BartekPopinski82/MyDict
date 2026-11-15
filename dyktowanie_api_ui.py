"""Okna ustawień modeli w chmurze: klucze API i formularz własnego modelu."""

from __future__ import annotations

import json
import threading

from PySide6.QtCore import QObject, QSettings, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from dyktowanie_api import (
    API_KINDS,
    CLOUD_MODELS,
    DEFAULT_BASE_URLS,
    IS_WINDOWS,
    KEY_SOURCES,
    PROVIDERS,
    CustomModel,
    ResolvedModel,
    check_key,
    find_key,
    normalize_base_url,
    protect_secret,
    provider_endpoint,
    provider_for_url,
    resolve,
    unprotect_secret,
)


BLUE = "#087BF0"


class ApiStore:
    """Klucze i własne modele zapisane w QSettings (klucze szyfrowane DPAPI)."""

    def __init__(self, settings: QSettings):
        self.settings = settings

    # -- klucze ---------------------------------------------------------------

    def stored_keys(self) -> dict:
        return {
            provider_id: str(self.settings.value(f"api_keys/{provider_id}", "") or "")
            for provider_id in PROVIDERS
        }

    def saved_key(self, provider_id: str) -> str:
        return unprotect_secret(str(self.settings.value(f"api_keys/{provider_id}", "") or ""))

    def set_key(self, provider_id: str, key: str):
        key = (key or "").strip()
        if key:
            self.settings.setValue(f"api_keys/{provider_id}", protect_secret(key))
        else:
            self.settings.remove(f"api_keys/{provider_id}")

    def key_for(self, provider_id: str) -> tuple[str, str]:
        return find_key(provider_id, self.stored_keys().get(provider_id, ""))

    # -- adresy API (endpointy) ----------------------------------------------------

    def stored_endpoints(self) -> dict:
        return {
            provider_id: str(self.settings.value(f"api_endpoints/{provider_id}", "") or "")
            for provider_id in PROVIDERS
        }

    def saved_endpoint(self, provider_id: str) -> str:
        return str(self.settings.value(f"api_endpoints/{provider_id}", "") or "")

    def endpoint_for(self, provider_id: str) -> str:
        return provider_endpoint(provider_id, self.stored_endpoints())

    def set_endpoint(self, provider_id: str, url: str):
        """Pusty adres albo równy domyślnemu = wraca adres z dostawcy.json."""
        url = normalize_base_url(url)
        if url and url != PROVIDERS[provider_id].base_url:
            self.settings.setValue(f"api_endpoints/{provider_id}", url)
        else:
            self.settings.remove(f"api_endpoints/{provider_id}")

    # -- własne modele -----------------------------------------------------------

    def custom_models(self) -> list[CustomModel]:
        raw = self.settings.value("custom_models", "[]")
        try:
            items = json.loads(raw) if isinstance(raw, str) else []
        except ValueError:
            items = []
        models = [CustomModel.from_dict(item) for item in items if isinstance(item, dict)]
        return [model for model in models if model is not None]

    def save_custom_models(self, models: list[CustomModel]):
        self.settings.setValue("custom_models", json.dumps([m.to_dict() for m in models], ensure_ascii=False))

    def upsert_custom(self, model: CustomModel):
        models = [m for m in self.custom_models() if m.id != model.id]
        existing = [m.id for m in self.custom_models()]
        if model.id in existing:
            index = existing.index(model.id)
            models.insert(index, model)
        else:
            models.append(model)
        self.save_custom_models(models)

    def delete_custom(self, model_id: str):
        self.save_custom_models([m for m in self.custom_models() if m.id != model_id])

    def find_custom(self, model_id: str) -> CustomModel | None:
        for model in self.custom_models():
            if model.id == model_id:
                return model
        return None

    def resolve(self, key: str) -> ResolvedModel | None:
        return resolve(key, self.stored_keys(), self.custom_models(), self.stored_endpoints())


class _CheckBridge(QObject):
    done = Signal(str, bool, str)


def _run_check(bridge: _CheckBridge, tag: str, kind: str, base_url: str, key: str):
    def work():
        ok, message = check_key(kind, base_url, key)
        bridge.done.emit(tag, ok, message)

    threading.Thread(target=work, name="sprawdz-klucz", daemon=True).start()


def _password_field(text: str = "") -> tuple[QLineEdit, QPushButton]:
    edit = QLineEdit(text)
    edit.setEchoMode(QLineEdit.EchoMode.Password)
    show = QPushButton("Pokaż")
    show.setCheckable(True)
    show.setFixedWidth(64)
    show.toggled.connect(
        lambda checked: (
            edit.setEchoMode(QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password),
            show.setText("Ukryj" if checked else "Pokaż"),
        )
    )
    return edit, show


def _helper(text: str) -> QLabel:
    label = QLabel(text)
    label.setProperty("helper", True)
    label.setWordWrap(True)
    return label


# ---------------------------------------------------------------------------
# Klucze API
# ---------------------------------------------------------------------------


class ApiKeysDialog(QDialog):
    def __init__(self, store: ApiStore, focus_provider: str | None = None, parent=None):
        super().__init__(parent)
        self.store = store
        self.setWindowTitle("Klucze API — modele w chmurze")
        self.setMinimumWidth(720)
        self.rows: dict[str, tuple[QLineEdit, QLabel, QPushButton]] = {}
        self.endpoints: dict[str, QLineEdit] = {}
        self.bridge = _CheckBridge(self)
        self.bridge.done.connect(self._checked)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(10)
        title = QLabel("Klucze API")
        title.setProperty("sectionTitle", True)
        layout.addWidget(title)
        layout.addWidget(_helper(
            "Wystarczy klucz usługi, z której chcesz korzystać. Link „Utwórz klucz” otwiera stronę dostawcy. "
            "Klucze z BAPO-transcript Lite, zmiennych środowiskowych lub pliku secrets.env są używane automatycznie. "
            "Pole „Endpoint” pozwala wskazać inny serwer usługi (np. region EU); puste = adres z pliku dostawcy.json."
        ))

        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)
        for index, provider in enumerate(PROVIDERS.values()):
            name = QLabel(
                f'<b>{provider.name}</b><br><a href="{provider.key_url}" style="color:{BLUE}">Utwórz klucz ↗</a>'
            )
            name.setTextFormat(Qt.TextFormat.RichText)
            name.setOpenExternalLinks(True)
            name.setMinimumWidth(92)
            edit, show = _password_field(store.saved_key(provider.id))
            key, source = store.key_for(provider.id)
            if source and source != "saved":
                edit.setPlaceholderText(f"Używany klucz {KEY_SOURCES[source]}")
            else:
                edit.setPlaceholderText(f"Wklej klucz {provider.name}")
            check = QPushButton("Sprawdź")
            check.setProperty("secondary", True)
            check.clicked.connect(lambda _checked=False, pid=provider.id: self._check(pid))
            status = QLabel("")
            status.setProperty("helper", True)
            status.setWordWrap(True)
            status.setMinimumWidth(170)
            endpoint_label = QLabel("Endpoint")
            endpoint_label.setProperty("helper", True)
            endpoint_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            endpoint = QLineEdit(store.saved_endpoint(provider.id))
            endpoint.setPlaceholderText(provider.base_url)
            endpoint.setToolTip(provider.endpoint_note or f"Domyślnie: {provider.base_url}")
            reset = QPushButton("Domyślny")
            reset.setProperty("secondary", True)
            reset.setToolTip(f"Przywraca {provider.base_url}")
            reset.clicked.connect(lambda _checked=False, field=endpoint: field.clear())
            top, bottom = index * 2, index * 2 + 1
            grid.addWidget(name, top, 0, 2, 1, Qt.AlignmentFlag.AlignTop)
            grid.addWidget(edit, top, 1)
            grid.addWidget(show, top, 2)
            grid.addWidget(check, top, 3)
            grid.addWidget(status, top, 4)
            grid.addWidget(endpoint, bottom, 1)
            grid.addWidget(reset, bottom, 2, 1, 2)
            if provider.endpoint_note:
                note = QLabel(provider.endpoint_note)
                note.setProperty("helper", True)
                note.setWordWrap(True)
                grid.addWidget(note, bottom, 4)
            self.rows[provider.id] = (edit, status, check)
            self.endpoints[provider.id] = endpoint
        grid.setColumnStretch(1, 3)
        grid.setColumnStretch(4, 2)
        layout.addLayout(grid)

        storage = (
            "Klucze są szyfrowane Twoim kontem Windows (DPAPI) — inne konto ani komputer ich nie odczyta."
            if IS_WINDOWS else "Klucze są zapisane w ustawieniach programu tylko dla Twojego konta."
        )
        layout.addWidget(_helper(storage))

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Anuluj")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Zapisz")
        save.setProperty("primary", True)
        save.setDefault(True)
        save.clicked.connect(self._save)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        layout.addLayout(buttons)

        if focus_provider in self.rows:
            edit, status, _check = self.rows[focus_provider]
            status.setText(f"<span style='color:#B4232A'>Wybrany model wymaga klucza {PROVIDERS[focus_provider].name}.</span>")
            edit.setFocus()

    def _check(self, provider_id: str):
        edit, status, button = self.rows[provider_id]
        key = edit.text().strip() or self.store.key_for(provider_id)[0]
        if not key:
            status.setText("Wpisz klucz.")
            return
        provider = PROVIDERS[provider_id]
        button.setEnabled(False)
        status.setText("Sprawdzam…")
        url = normalize_base_url(self.endpoints[provider_id].text()) or provider.base_url
        _run_check(self.bridge, provider_id, provider.kind, url, key)

    def _checked(self, provider_id: str, ok: bool, message: str):
        if provider_id not in self.rows:
            return
        _edit, status, button = self.rows[provider_id]
        button.setEnabled(True)
        color = "#18794E" if ok else "#B4232A"
        status.setText(f"<span style='color:{color}'>{message}</span>")

    def _save(self):
        for provider_id, (edit, _status, _button) in self.rows.items():
            self.store.set_key(provider_id, edit.text())
            self.store.set_endpoint(provider_id, self.endpoints[provider_id].text())
        self.accept()


# ---------------------------------------------------------------------------
# Własny model
# ---------------------------------------------------------------------------


class CustomModelDialog(QDialog):
    """Popup „Własny model API”: rodzaj API, adres, nazwa modelu i klucz."""

    def __init__(self, store: ApiStore, model: CustomModel | None = None, parent=None):
        super().__init__(parent)
        self.store = store
        self.original = model
        self.result_model: CustomModel | None = None
        self.deleted = False
        self.setWindowTitle("Edytuj własny model API" if model else "Własny model API")
        self.setMinimumWidth(560)
        self.bridge = _CheckBridge(self)
        self.bridge.done.connect(self._checked)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(10)
        title = QLabel("Własny model API")
        title.setProperty("sectionTitle", True)
        layout.addWidget(title)
        layout.addWidget(_helper(
            "Podaj dowolny model rozpoznawania mowy dostępny przez API — np. nowszy model OpenAI, "
            "model z innego serwera zgodnego z OpenAI (Groq, Together, Fireworks, lokalny serwer whisper) "
            "albo inny model ElevenLabs / Deepgram / AssemblyAI."
        ))

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(8)

        self.kind_combo = QComboBox()
        for kind, label in API_KINDS.items():
            self.kind_combo.addItem(label, kind)
        form.addRow("Rodzaj API", self.kind_combo)

        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("https://api.openai.com/v1")
        form.addRow("Adres API", self.url_edit)
        self.url_hint = _helper("")
        form.addRow("", self.url_hint)

        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        self.model_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.model_combo.lineEdit().setPlaceholderText("np. gpt-4o-transcribe")
        form.addRow("Model", self.model_combo)

        key_row = QWidget()
        key_layout = QHBoxLayout(key_row)
        key_layout.setContentsMargins(0, 0, 0, 0)
        key_layout.setSpacing(6)
        self.key_edit, show = _password_field()
        key_layout.addWidget(self.key_edit, 1)
        key_layout.addWidget(show)
        form.addRow("Klucz API", key_row)
        self.key_hint = _helper("")
        form.addRow("", self.key_hint)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("nazwa na liście modeli (opcjonalnie)")
        form.addRow("Nazwa", self.name_edit)
        layout.addLayout(form)

        self.status = QLabel("")
        self.status.setProperty("helper", True)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        buttons = QHBoxLayout()
        self.check_button = QPushButton("Sprawdź połączenie")
        self.check_button.setProperty("secondary", True)
        self.check_button.clicked.connect(self._check)
        buttons.addWidget(self.check_button)
        if model is not None:
            delete = QPushButton("Usuń model")
            delete.clicked.connect(self._delete)
            buttons.addWidget(delete)
        buttons.addStretch(1)
        cancel = QPushButton("Anuluj")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Zapisz i użyj")
        save.setProperty("primary", True)
        save.setDefault(True)
        save.clicked.connect(self._save)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        layout.addLayout(buttons)

        # wypełnienie
        kind = model.kind if model else "openai"
        self.kind_combo.setCurrentIndex(max(0, self.kind_combo.findData(kind)))
        self._fill_suggestions(kind)
        self.url_edit.setText(model.base_url if model else DEFAULT_BASE_URLS[kind])
        if model:
            self.model_combo.setEditText(model.model)
            self.name_edit.setText(model.name if model.name != model.model else "")
            self.key_edit.setText(unprotect_secret(model.key) if model.key else "")
        else:
            self.model_combo.setEditText("")
        self.kind_combo.currentIndexChanged.connect(self._kind_changed)
        self.url_edit.textChanged.connect(self._update_hints)
        self._update_hints()

    # -- pomocnicze ------------------------------------------------------------------

    def _kind(self) -> str:
        return self.kind_combo.currentData() or "openai"

    def _fill_suggestions(self, kind: str):
        current = self.model_combo.currentText()
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        seen = []
        for cloud in CLOUD_MODELS.values():
            if PROVIDERS[cloud.provider].kind == kind and cloud.model not in seen:
                seen.append(cloud.model)
        for name in seen:
            self.model_combo.addItem(name)
        self.model_combo.setEditText(current)
        self.model_combo.blockSignals(False)

    def _kind_changed(self):
        kind = self._kind()
        url = normalize_base_url(self.url_edit.text())
        if not url or url in [normalize_base_url(u) for u in DEFAULT_BASE_URLS.values()] or \
                url == normalize_base_url(PROVIDERS["groq"].base_url):
            self.url_edit.setText(DEFAULT_BASE_URLS[kind])
        self._fill_suggestions(kind)
        self._update_hints()

    def _provider(self) -> str | None:
        return provider_for_url(self._kind(), self.url_edit.text())

    def _update_hints(self):
        kind = self._kind()
        if kind == "openai":
            self.url_hint.setText(
                "Adres bazowy, do którego program dopisze /audio/transcriptions. "
                f"Groq: {PROVIDERS['groq'].base_url}"
            )
        else:
            self.url_hint.setText("Zwykle zostaw domyślny adres.")
        provider_id = self._provider()
        if provider_id:
            key, source = self.store.key_for(provider_id)
            name = PROVIDERS[provider_id].name
            if key:
                self.key_edit.setPlaceholderText(f"puste = klucz {name} ({KEY_SOURCES[source]})")
            else:
                self.key_edit.setPlaceholderText(f"wklej klucz {name}")
            self.key_hint.setText("")
        else:
            self.key_edit.setPlaceholderText("klucz do tego serwera")
            self.key_hint.setText(
                "Dla własnego serwera bez uwierzytelniania pole może zostać puste." if kind == "openai" else ""
            )

    def _effective_key(self) -> str:
        key = self.key_edit.text().strip()
        if key:
            return key
        provider_id = self._provider()
        return self.store.key_for(provider_id)[0] if provider_id else ""

    # -- akcje -------------------------------------------------------------------------

    def _check(self):
        url = normalize_base_url(self.url_edit.text())
        if not url:
            self.status.setText("Wpisz adres API.")
            return
        self.check_button.setEnabled(False)
        self.status.setText("Sprawdzam…")
        _run_check(self.bridge, "custom", self._kind(), url, self._effective_key())

    def _checked(self, _tag: str, ok: bool, message: str):
        self.check_button.setEnabled(True)
        color = "#18794E" if ok else "#B4232A"
        extra = "" if ok else "<br>Model możesz zapisać mimo to — błąd zobaczysz przy pierwszej wypowiedzi."
        self.status.setText(f"<span style='color:{color}'>{message}</span>{extra}")

    def _save(self):
        model_name = self.model_combo.currentText().strip()
        url = normalize_base_url(self.url_edit.text())
        if not url:
            self.status.setText("<span style='color:#B4232A'>Wpisz adres API.</span>")
            self.url_edit.setFocus()
            return
        if not model_name:
            self.status.setText("<span style='color:#B4232A'>Wpisz nazwę modelu.</span>")
            self.model_combo.setFocus()
            return
        key = self.key_edit.text().strip()
        provider_id = self._provider()
        if not key and not self._effective_key() and (provider_id or self._kind() != "openai"):
            service = PROVIDERS[provider_id].name if provider_id else "Ten rodzaj API"
            self.status.setText(f"<span style='color:#B4232A'>{service} wymaga klucza API — wklej go powyżej.</span>")
            self.key_edit.setFocus()
            return
        self.result_model = CustomModel(
            id=self.original.id if self.original else CustomModel.new_id(),
            name=self.name_edit.text().strip() or model_name,
            kind=self._kind(),
            base_url=url,
            model=model_name,
            key=protect_secret(key) if key else "",
        )
        self.store.upsert_custom(self.result_model)
        self.accept()

    def _delete(self):
        if self.original is None:
            return
        answer = QMessageBox.question(self, self.windowTitle(), f"Usunąć model „{self.original.name}”?")
        if answer == QMessageBox.StandardButton.Yes:
            self.store.delete_custom(self.original.id)
            self.deleted = True
            self.accept()
