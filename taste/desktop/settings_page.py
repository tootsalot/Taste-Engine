"""Settings: API keys, profile settings, data folder, and deleting the profile."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QCompleter,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from taste import __version__, profiles, settings
from taste.config import data_dir
from taste.desktop.context import AppContext
from taste.desktop.dialogs import ConfirmDeleteDialog
from taste.desktop.widgets import Card, heading, label
from taste.desktop.workers import ConnectionTestWorker
from taste.secrets_store import KEY_LABELS, KEY_NAMES, SecretStoreError
from taste.settings import SettingError

KEY_SOURCES = {"MAL_CLIENT_ID": "mal", "LASTFM_API_KEY": "lastfm"}
CREDITS = (
    "Built with Python and Qt for Python (PySide6, used under the LGPL 3.0), plus requests, "
    "keyring, python-dotenv, and tzdata. Fonts: Space Grotesk and Inter (SIL Open Font "
    "License). Anime data comes from MyAnimeList and music data from Last.fm; Taste Engine "
    "isn't affiliated with either."
)
STATUS_TEXT = {
    "keyring": ("Saved in Windows Credential Manager", "ok"),
    "file": ("Saved in an unencrypted local file", "error"),
    "env": ("Using an environment variable", "ok"),
    None: ("Not set", "error"),
}


class SettingsPage(QWidget):
    profile_deleted = Signal(str)
    settings_saved = Signal(list)  # keys that changed

    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        self.profile_id: str | None = None
        self.testers: list[ConnectionTestWorker] = []
        self.is_busy = lambda: False  # the main window points this at "is a sync running?"

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        outer.addWidget(scroll)
        body = QWidget()
        scroll.setWidget(body)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(16)
        layout.addWidget(heading("Settings", 28))

        # API keys
        keys = Card()
        keys.body.addWidget(heading("API keys", 18))
        self.insecure_note = label(
            "This computer has no system credential store, so saved keys go in an "
            "unencrypted file in the data folder. On Windows this never happens.",
            role="error",
            wrap=True,
        )
        keys.body.addWidget(self.insecure_note)
        keys.body.addWidget(
            label(
                "Saved keys are never shown again. Get a MAL client ID at "
                "myanimelist.net/apiconfig and a Last.fm key at last.fm/api/account/create.",
                role="muted",
                wrap=True,
            )
        )
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        self.key_inputs: dict[str, QLineEdit] = {}
        self.key_status: dict[str, object] = {}
        self.key_remove: dict[str, QPushButton] = {}
        for row, name in enumerate(KEY_NAMES):
            field = QLineEdit()
            field.setEchoMode(QLineEdit.EchoMode.Password)
            field.setPlaceholderText("paste a new key")
            status = label("", role="muted")
            save = QPushButton("Save")
            save.clicked.connect(lambda _=False, n=name: self.save_key(n))
            remove = QPushButton("Remove")
            remove.clicked.connect(lambda _=False, n=name: self.remove_key(n))
            test = QPushButton("Test connection")
            test.clicked.connect(lambda _=False, n=name: self.test_connection(KEY_SOURCES[n]))
            grid.addWidget(label(KEY_LABELS[name]), row * 2, 0)
            grid.addWidget(status, row * 2, 1, 1, 4)
            grid.addWidget(field, row * 2 + 1, 0, 1, 2)
            grid.addWidget(save, row * 2 + 1, 2)
            grid.addWidget(remove, row * 2 + 1, 3)
            grid.addWidget(test, row * 2 + 1, 4)
            self.key_inputs[name] = field
            self.key_status[name] = status
            self.key_remove[name] = remove
        keys.body.addLayout(grid)
        self.key_message = label("", wrap=True)
        keys.body.addWidget(self.key_message)
        layout.addWidget(keys)

        # Profile settings, generated from the registry
        form_card = Card()
        form_card.body.addWidget(heading("Profile settings", 18))
        form = QFormLayout()
        form.setVerticalSpacing(10)
        self.inputs: dict[str, QWidget] = {}
        for field in settings.SETTINGS:
            widget = self._input_for(field)
            if field.help:
                widget.setToolTip(field.help)
            self.inputs[field.key] = widget
            form.addRow(field.label, widget)
        form_card.body.addLayout(form)
        save_row = QHBoxLayout()
        self.save_button = QPushButton("Save settings")
        self.save_button.setProperty("role", "primary")
        self.save_button.clicked.connect(self.save_settings)
        save_row.addWidget(self.save_button)
        save_row.addStretch()
        form_card.body.addLayout(save_row)
        self.settings_message = label("", wrap=True)
        form_card.body.addWidget(self.settings_message)
        layout.addWidget(form_card)

        # Data folder and delete
        data_card = Card()
        data_card.body.addWidget(heading("Data", 18))
        self.data_path = label("", role="muted", wrap=True)
        data_card.body.addWidget(self.data_path)
        row = QHBoxLayout()
        open_folder = QPushButton("Open data folder")
        open_folder.clicked.connect(self.open_data_folder)
        self.delete_button = QPushButton("Delete this profile")
        self.delete_button.setProperty("role", "danger")
        self.delete_button.clicked.connect(lambda: self.delete_profile())
        row.addWidget(open_folder)
        row.addStretch()
        row.addWidget(self.delete_button)
        data_card.body.addLayout(row)
        layout.addWidget(data_card)

        # About and credits
        about = Card()
        about.body.addWidget(heading("About", 18))
        about.body.addWidget(
            label(f"Taste Engine {__version__}. Free and open source under the MIT License.")
        )
        about.body.addWidget(label(CREDITS, role="muted", wrap=True))
        self.notices_button = QPushButton("Third-party notices")
        self.notices_button.clicked.connect(self.open_notices)
        found = notices_path()
        self.notices_button.setEnabled(found is not None)
        if found is None:
            self.notices_button.setToolTip("Included with the downloadable app.")
        about_row = QHBoxLayout()
        about_row.addWidget(self.notices_button)
        about_row.addStretch()
        about.body.addLayout(about_row)
        layout.addWidget(about)
        layout.addStretch()

    @staticmethod
    def _input_for(field: settings.Setting) -> QWidget:
        if field.kind == "bool":
            return QCheckBox()
        if field.kind == "int":
            box = QSpinBox()
            box.setRange(int(field.minimum or 0), int(field.maximum or 1_000_000))
            return box
        if field.kind == "float":
            box = QDoubleSpinBox()
            box.setDecimals(2)
            box.setSingleStep(0.05)
            box.setRange(field.minimum or 0.0, field.maximum or 1_000_000.0)
            return box
        if field.kind == "choice":
            combo = QComboBox()
            for value, text in (field.choices or {}).items():
                combo.addItem(text, value)
            return combo
        if field.kind == "timezone":
            combo = QComboBox()
            combo.setEditable(True)
            combo.addItems(settings.timezone_names())
            combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
            combo.completer().setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
            combo.completer().setFilterMode(Qt.MatchFlag.MatchContains)  # 'Phoenix' finds it
            return combo
        line = QLineEdit()
        line.setMaxLength(field.max_length)
        return line

    # -- loading ------------------------------------------------------------

    def set_profile(self, profile_id: str | None) -> None:
        self.profile_id = profile_id
        self.key_message.setText("")
        self.settings_message.setText("")
        for field in self.key_inputs.values():
            field.clear()
        self.refresh()

    def refresh(self) -> None:
        if not self.profile_id:
            return
        conn = self.ctx.connect(self.profile_id)
        try:
            values = settings.get_all(conn)
        finally:
            conn.close()
        for field in settings.SETTINGS:
            self._set_input(self.inputs[field.key], field, values[field.key])
        self.insecure_note.setVisible(not self.ctx.store.secure)
        self.data_path.setText(f"Every profile is stored in {data_dir().resolve()}")
        self._refresh_keys()

    @staticmethod
    def _set_input(widget: QWidget, field: settings.Setting, value) -> None:
        if isinstance(widget, QCheckBox):
            widget.setChecked(bool(value))
        elif isinstance(widget, (QSpinBox, QDoubleSpinBox)):
            widget.setValue(value)
        elif isinstance(widget, QComboBox) and field.kind == "choice":
            widget.setCurrentIndex(max(widget.findData(value), 0))
        elif isinstance(widget, QComboBox):
            widget.setCurrentText(value)
        else:
            widget.setText(str(value))

    @staticmethod
    def _read_input(widget: QWidget):
        if isinstance(widget, QCheckBox):
            return widget.isChecked()
        if isinstance(widget, (QSpinBox, QDoubleSpinBox)):
            return widget.value()
        if isinstance(widget, QComboBox) and widget.isEditable():
            return widget.currentText()
        if isinstance(widget, QComboBox):
            return widget.currentData()
        return widget.text()

    def _refresh_keys(self) -> None:
        for name in KEY_NAMES:
            try:
                _, source = self.ctx.store.get_with_source(self.profile_id, name)
                text, role = STATUS_TEXT[source]
            except SecretStoreError as exc:
                source, text, role = None, str(exc), "error"
            status = self.key_status[name]
            status.setText(text)
            status.setProperty("role", role)
            status.style().unpolish(status)
            status.style().polish(status)
            self.key_remove[name].setEnabled(source in ("keyring", "file"))

    def _message(self, widget, text: str, ok: bool) -> None:
        widget.setText(text)
        widget.setProperty("role", "ok" if ok else "error")
        widget.style().unpolish(widget)
        widget.style().polish(widget)

    # -- actions ------------------------------------------------------------

    def save_settings(self) -> bool:
        errors = []
        changed = []
        conn = self.ctx.connect(self.profile_id)
        try:
            for field in settings.SETTINGS:
                value = self._read_input(self.inputs[field.key])
                try:
                    if value != settings.get(conn, field.key):
                        settings.set_value(conn, field.key, value)
                        changed.append(field.key)
                except SettingError as exc:
                    errors.append(str(exc))
        finally:
            conn.close()
        if errors:
            self._message(self.settings_message, " ".join(errors), ok=False)
        else:
            self._message(self.settings_message, "Settings saved.", ok=True)
        self.refresh()
        self.settings_saved.emit(changed)
        return not errors

    def save_key(self, name: str) -> None:
        field = self.key_inputs[name]
        try:
            self.ctx.store.set(self.profile_id, name, field.text())
            self._message(self.key_message, f"Saved the {KEY_LABELS[name]}.", ok=True)
        except SecretStoreError as exc:
            self._message(self.key_message, str(exc), ok=False)
        field.clear()  # never leave a key sitting in the window
        self._refresh_keys()

    def remove_key(self, name: str) -> None:
        try:
            self.ctx.store.delete(self.profile_id, name)
            self._message(self.key_message, f"Removed the {KEY_LABELS[name]}.", ok=True)
        except SecretStoreError as exc:
            self._message(self.key_message, str(exc), ok=False)
        self._refresh_keys()

    def test_connection(self, source: str) -> ConnectionTestWorker:
        self._message(self.key_message, "Testing...", ok=True)
        worker = ConnectionTestWorker(
            self.profile_id, source, self.ctx.store, self.ctx.client_factory
        )
        worker.done.connect(lambda ok, msg: self._message(self.key_message, msg, ok))
        worker.finished.connect(lambda: self.testers.remove(worker))
        self.testers.append(worker)
        worker.start()
        return worker

    def open_data_folder(self) -> None:
        folder = data_dir()
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder.resolve())))

    def open_notices(self) -> bool:
        path = notices_path()
        if path is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        return path is not None

    def _confirm_delete(self) -> bool:
        return ConfirmDeleteDialog(self.profile_id, self).exec() == QDialog.DialogCode.Accepted

    def delete_profile(self, confirmed: bool | None = None) -> bool:
        if not self.profile_id:
            return False
        if self.is_busy():
            self._message(
                self.settings_message, "Wait for the sync to finish before deleting.", ok=False
            )
            return False
        if confirmed is None:
            confirmed = self._confirm_delete()
        if not confirmed:
            return False
        deleted = self.profile_id
        self.ctx.forget(deleted)
        profiles.delete(deleted, store=self.ctx.store)
        self.profile_deleted.emit(deleted)
        return True


def notices_path() -> Path | None:
    """THIRD_PARTY_NOTICES.txt next to the packaged exe. Running from source there's none."""
    if not getattr(sys, "frozen", False):
        return None
    path = Path(sys.executable).resolve().parent / "THIRD_PARTY_NOTICES.txt"
    return path if path.is_file() else None
