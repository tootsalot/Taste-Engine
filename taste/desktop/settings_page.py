"""Settings: API keys, profile settings, data folder, and deleting the profile."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QCompleter,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from taste import __version__, credentials, local_time, profiles, settings
from taste.config import data_dir
from taste.desktop import motion, theme, ui_state
from taste.desktop.context import AppContext
from taste.desktop.dialogs import ConfirmDeleteDialog
from taste.desktop.widgets import Card, ScrollArea, Segmented, heading, label, section
from taste.desktop.workers import CallWorker, ConnectionTestWorker
from taste.http_client import HttpClient
from taste.secrets_store import KEY_LABELS, KEY_NAMES, SecretStoreError
from taste.settings import SettingError
from taste.sources import hardcover, tmdb

KEY_SOURCES = {"MAL_CLIENT_ID": "mal", "LASTFM_API_KEY": "lastfm", "TMDB_READ_TOKEN": "tmdb"}
LABEL_WIDTH = 320
TMDB_LOGO_HEIGHT = 14  # the app's own logo is 30 px in the top bar
FIELD_WIDTH = 600  # inputs stop here, so nothing stretches across a wide screen
TWO_COLUMNS = 1500  # page width from which settings sit beside keys and data
THREE_COLUMNS = 2200  # and from which Advanced splits the settings in two
CREDITS = (
    "Built with Python and Qt for Python (PySide6, used under the LGPL 3.0), plus requests, "
    "keyring, python-dotenv, and tzdata. Fonts: Space Grotesk and Inter (SIL Open Font "
    "License). Anime data comes from MyAnimeList, music data from Last.fm, some artist "
    "photos from Deezer, books from Hardcover, and film and TV data "
    "from TMDB; Taste Engine isn't affiliated with any of them. This product uses the TMDB "
    "API but is not endorsed or certified by TMDB."
)
STATUS_TEXT = {
    "keyring": ("Saved in Windows Credential Manager", "ok"),
    "file": ("Saved in an unencrypted local file", "error"),
    "env": ("Using an environment variable", "ok"),
    None: ("Not set", "error"),
}


class SettingsPage(QWidget):
    profile_deleted = Signal(str)
    hardcover_connected = Signal()  # signed in: sync the books now
    settings_saved = Signal(list)  # keys that changed

    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        self.profile_id: str | None = None
        self.testers: list[ConnectionTestWorker] = []
        self.is_busy = lambda: False  # the main window points this at "is a sync running?"

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll = ScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        outer.addWidget(self.scroll)
        body = QWidget()
        self.scroll.setWidget(body)
        # The cards flow into columns by width (arrange): one, then two (keys and data
        # on the left, profile settings on the right), and in Advanced on a very wide
        # window the profile settings split into two columns of their own. Fields keep
        # a readable width either way (FIELD_WIDTH).
        layout = QVBoxLayout(body)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(16)
        top = QHBoxLayout()
        top.addWidget(heading("Settings", 28))
        top.addStretch()
        self.mode_switch = Segmented([("simple", "Simple"), ("advanced", "Advanced")])
        self.mode_switch.setToolTip("Advanced adds report limits, sync, and storage settings.")
        self.mode_switch.changed.connect(self.set_mode)
        self.mode_buttons = self.mode_switch.buttons
        top.addWidget(self.mode_switch)
        layout.addLayout(top)
        columns = QHBoxLayout()
        columns.setSpacing(16)
        self.left = QVBoxLayout()
        self.left.setSpacing(16)
        self.right = QVBoxLayout()
        self.right.setSpacing(16)
        columns.addLayout(self.left, 1)
        columns.addLayout(self.right, 1)
        layout.addLayout(columns, 1)

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
                "myanimelist.net/apiconfig, a Last.fm key at last.fm/api/account/create, and "
                "a TMDB read access token (optional, for films and TV) at "
                "themoviedb.org/settings/api.",
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
        self.keys_card = keys

        # Books, films, and TV: a Hardcover account (and TMDB below it)
        media = Card()
        media.body.addWidget(heading("Books, films, and TV", 18))
        media.body.addWidget(section("Hardcover"))
        media.body.addWidget(
            label(
                "Books come from your Hardcover library: your stars, what you've read and "
                "when, and the genres and moods readers gave each book. Connecting opens "
                "Hardcover in your browser to approve Taste Engine, which can only read. "
                "Books still on Goodreads? Hardcover can import a Goodreads export for you.",
                role="muted",
                wrap=True,
            )
        )
        hardcover_row = QHBoxLayout()
        self.hardcover_connect = QPushButton("Connect my Hardcover account…")
        self.hardcover_connect.clicked.connect(lambda: self.connect_hardcover())
        self.hardcover_stop = QPushButton("Stop")
        self.hardcover_stop.clicked.connect(lambda: self.stop_hardcover())
        self.hardcover_disconnect = QPushButton("Disconnect")
        self.hardcover_disconnect.clicked.connect(lambda: self.disconnect_hardcover())
        self.hardcover_copy = QPushButton("Copy the link")
        self.hardcover_copy.setProperty("role", "link")
        self.hardcover_copy.setToolTip("For when your browser didn't open by itself.")
        self.hardcover_copy.clicked.connect(lambda: self.copy_hardcover_link())
        for button in (
            self.hardcover_connect, self.hardcover_stop, self.hardcover_disconnect,
            self.hardcover_copy,
        ):  # fmt: skip
            hardcover_row.addWidget(button)
        hardcover_row.addStretch()
        media.body.addLayout(hardcover_row)
        self.hardcover_status = label("", wrap=True)
        media.body.addWidget(self.hardcover_status)
        self.hardcover_worker: CallWorker | None = None
        self.sign_in: hardcover.SignIn | None = None
        media.body.addWidget(section("TMDB"))
        media.body.addWidget(
            label(
                "Films and TV come from the ratings, watchlist, and favorites on your TMDB "
                "account. Save the TMDB read access token above, then connect your account: "
                "TMDB opens in your browser to ask you to approve Taste Engine, which can only "
                "read. TMDB keeps when you rated something, not when you watched it.",
                role="muted",
                wrap=True,
            )
        )
        tmdb_row = QHBoxLayout()
        self.tmdb_connect = QPushButton("Connect my TMDB account…")
        self.tmdb_connect.clicked.connect(lambda: self.connect_tmdb())
        self.tmdb_disconnect = QPushButton("Disconnect")
        self.tmdb_disconnect.clicked.connect(lambda: self.disconnect_tmdb())
        tmdb_row.addWidget(self.tmdb_connect)
        tmdb_row.addWidget(self.tmdb_disconnect)
        tmdb_row.addStretch()
        media.body.addLayout(tmdb_row)
        self.tmdb_status = label("", wrap=True)
        media.body.addWidget(self.tmdb_status)
        self.media_card = media
        self.tmdb_worker: CallWorker | None = None

        # Profile settings, generated from the registry, one heading per group
        form_card = Card()
        form_card.body.addWidget(heading("Profile settings", 18))
        self.inputs: dict[str, QWidget] = {}
        self.descriptions: dict[str, QLabel] = {}
        self.headings: list[str] = []
        self.heading_widgets: dict[str, QLabel] = {}
        self.group_of: dict[str, str] = {}
        self._form_of: dict[str, QFormLayout] = {}
        self.group_widgets: dict[str, QWidget] = {}
        for group in settings.GROUPS:
            fields = [f for f in settings.SETTINGS if f.group == group]
            if not fields:
                continue
            box = QWidget()
            box_layout = QVBoxLayout(box)
            box_layout.setContentsMargins(0, 0, 0, 0)
            box_layout.setSpacing(8)
            title = section(group)
            title.setContentsMargins(0, 10, 0, 0)
            box_layout.addWidget(title)
            self.headings.append(group)
            self.heading_widgets[group] = title
            form = QFormLayout()
            form.setVerticalSpacing(12)
            form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
            for field in fields:
                widget = self._input_for(field)
                if not isinstance(widget, (QCheckBox, ChoiceBoxes)):
                    widget.setMaximumWidth(FIELD_WIDTH)
                self.inputs[field.key] = widget
                self.group_of[field.key] = group
                # The name, with a plain sentence under it saying what it does.
                name = QWidget()
                name.setFixedWidth(LABEL_WIDTH)  # one label column across all groups
                stack = QVBoxLayout(name)
                stack.setContentsMargins(0, 0, 0, 0)
                stack.setSpacing(2)
                stack.addWidget(label(field.label, wrap=True))
                self.descriptions[field.key] = label(field.help, role="caption", wrap=True)
                stack.addWidget(self.descriptions[field.key])
                form.addRow(name, widget)
                self._form_of[field.key] = form
            box_layout.addLayout(form)
            self.group_widgets[group] = box
        group_row = QHBoxLayout()
        group_row.setSpacing(40)
        self.group_columns = [QVBoxLayout(), QVBoxLayout()]
        for column in self.group_columns:
            column.setSpacing(4)
            column.setAlignment(Qt.AlignmentFlag.AlignTop)
            group_row.addLayout(column, 1)
        form_card.body.addLayout(group_row)
        self.hidden_note = label("", role="muted")
        form_card.body.addWidget(self.hidden_note)
        save_row = QHBoxLayout()
        self.save_button = QPushButton("Save settings")
        self.save_button.setProperty("role", "primary")
        self.save_button.clicked.connect(self.save_settings)
        save_row.addWidget(self.save_button)
        save_row.addStretch()
        form_card.body.addLayout(save_row)
        self.settings_message = label("", wrap=True)
        form_card.body.addWidget(self.settings_message)
        form_card.body.addStretch(1)
        self.form_card = form_card

        # Appearance: how much the app moves (every profile, like the sidebar)
        appearance = Card()
        appearance.body.addWidget(heading("Appearance", 18))
        motion_row = QHBoxLayout()
        motion_row.addWidget(label("Motion"))
        motion_row.addStretch()
        self.motion_switch = Segmented([("full", "Full"), ("reduced", "Reduced"), ("off", "Off")])
        self.motion_switch.changed.connect(self.set_motion)
        motion_row.addWidget(self.motion_switch)
        appearance.body.addLayout(motion_row)
        self.motion_note = label("", role="caption", wrap=True)
        appearance.body.addWidget(self.motion_note)
        self.appearance_card = appearance
        self.set_motion(motion.chosen(), remember=False)

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
        self.data_card = data_card

        # About and credits
        about = Card()
        about.body.addWidget(heading("About", 18))
        about.body.addWidget(
            label(f"Taste Engine {__version__}. Free and open source under the MIT License.")
        )
        about.body.addWidget(label(CREDITS, role="muted", wrap=True))
        # TMDB asks apps using its data to credit it with its logo. Kept small, under the
        # app's own logo in size.
        self.tmdb_logo = QLabel()
        self.tmdb_logo.setPixmap(theme.tmdb_logo(TMDB_LOGO_HEIGHT))
        self.tmdb_logo.setToolTip("Film and TV data from TMDB (themoviedb.org)")
        about.body.addWidget(self.tmdb_logo)
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
        about.body.addStretch(1)
        self.about_card = about
        self.mode = ""
        self.layout_key: tuple[int, int] | None = None
        self.set_mode(ui_state.load().get("settings_mode", "simple"), remember=False)
        self.arrange(1000)
        self.scroll.viewport_resized.connect(self.arrange)

    # -- layout -------------------------------------------------------------

    def arrange(self, width: int) -> None:
        """Flow the cards into columns for a page this wide.

        Under TWO_COLUMNS: everything in one column. From there, keys, data, and About
        on the left and the profile settings on the right. In Advanced from
        THREE_COLUMNS, the profile settings' groups split into two columns too.
        """
        columns = 2 if width >= TWO_COLUMNS else 1
        split = columns == 2 and width >= THREE_COLUMNS and self.mode == "advanced"
        key = (columns, 2 if split else 1)
        if key == self.layout_key:
            return
        self.layout_key = key
        cards = (
            self.keys_card,
            self.media_card,
            self.appearance_card,
            self.form_card,
            self.data_card,
            self.about_card,
        )
        for layout in (self.left, self.right):
            for card in cards:
                layout.removeWidget(card)
        if columns == 1:
            for card in cards:
                self.left.addWidget(card)
        else:
            left = (
                self.keys_card,
                self.media_card,
                self.appearance_card,
                self.data_card,
                self.about_card,
            )
            for card in left:
                self.left.addWidget(card)
            self.right.addWidget(self.form_card)
            # The columns end together at the bottom of the window.
            self.left.setStretchFactor(self.about_card, 1)
            self.right.setStretchFactor(self.form_card, 1)
        self._place_groups(key[1])

    def _place_groups(self, count: int) -> None:
        """Groups in order, split where the two columns come out closest in length."""
        groups = list(self.group_widgets)
        for column in self.group_columns:
            for group in groups:
                column.removeWidget(self.group_widgets[group])
        sizes = [
            1
            + sum(
                1
                for f in settings.SETTINGS
                if f.group == g and (self.mode == "advanced" or not f.advanced)
            )
            for g in groups
        ]
        split = len(groups)
        if count == 2:
            total = sum(sizes)
            split = min(
                range(1, len(groups)),
                key=lambda i: abs(2 * sum(sizes[:i]) - total),
                default=len(groups),
            )
        for i, group in enumerate(groups):
            self.group_columns[0 if i < split else 1].addWidget(self.group_widgets[group])
        self.group_split = split if count == 2 else None

    def set_mode(self, mode: str, remember: bool = True) -> None:
        """Simple shows the everyday settings; Advanced shows every one. Hidden settings
        keep their values and are saved unchanged."""
        mode = "advanced" if mode == "advanced" else "simple"
        self.mode = mode
        self.mode_switch.set_value(mode)
        advanced = mode == "advanced"
        for field in settings.SETTINGS:
            self._form_of[field.key].setRowVisible(
                self.inputs[field.key], advanced or not field.advanced
            )
        for group, title in self.heading_widgets.items():
            title.setVisible(
                advanced or any(not f.advanced for f in settings.SETTINGS if f.group == group)
            )
        hidden = sum(f.advanced for f in settings.SETTINGS)
        self.hidden_note.setText(f"{hidden} more settings in Advanced.")
        self.hidden_note.setVisible(not advanced)
        if self.layout_key is not None:
            self.layout_key = None  # Advanced may split the groups; Simple never does
            self.arrange(self.scroll.viewport().width())
        if remember:
            ui_state.save(settings_mode=mode)

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
        if field.kind == "multichoice":
            return ChoiceBoxes(field.choices or {})
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
        self._refresh_hardcover()
        self._refresh_tmdb()
        self._refresh_keys()

    @staticmethod
    def _set_input(widget: QWidget, field: settings.Setting, value) -> None:
        if isinstance(widget, ChoiceBoxes):
            widget.set_value(value)
        elif isinstance(widget, QCheckBox):
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
        if isinstance(widget, ChoiceBoxes):
            return widget.value()
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
        self._refresh_tmdb()

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
                    # Compare in stored form: ticked boxes are a list, the stored value text.
                    if settings.validate(field.key, value) != settings.get(conn, field.key):
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

    # -- Motion ---------------------------------------------------------------

    def set_motion(self, choice: str, remember: bool = True) -> None:
        """Full, Reduced (quick fades, nothing slides), or Off. Kept for every profile."""
        choice = choice if choice in motion.LEVELS else "full"
        self.motion_switch.set_value(choice)
        if remember:
            ui_state.save(motion=choice)
        notes = {
            "full": "Pages fade and rise, panels slide, and pictures fade in.",
            "reduced": "Quick fades only. Nothing slides or moves across the screen.",
            "off": "Everything changes at once.",
        }
        note = notes[choice]
        if not motion.windows_allows():
            note += " Windows' animation effects are off, so the app doesn't animate either."
        self.motion_note.setText(note)

    # -- TMDB ---------------------------------------------------------------

    def _refresh_tmdb(self) -> None:
        connected = credentials.tmdb_connected(self.profile_id, self.ctx.store)
        has_token = self.ctx.store.get(self.profile_id, "TMDB_READ_TOKEN") is not None
        busy = self.tmdb_worker is not None and self.tmdb_worker.isRunning()
        self.tmdb_connect.setEnabled(has_token and not connected and not busy)
        self.tmdb_disconnect.setEnabled(connected and not busy)
        if connected:
            text = "Connected. Sync everything on the Dashboard now includes TMDB."
        elif has_token:
            text = "Not connected yet."
        else:
            text = "Save a TMDB read access token above first."
        self.tmdb_status.setText(text)
        self.tmdb_status.setProperty("role", "ok" if connected else "muted")
        self.tmdb_status.style().unpolish(self.tmdb_status)
        self.tmdb_status.style().polish(self.tmdb_status)

    def _tmdb_client(self, secrets: list[str]) -> HttpClient:
        factory = self.ctx.client_factory
        return factory(secrets) if factory is not None else HttpClient(secrets=secrets)

    def _run_tmdb(self, call, then) -> CallWorker:
        worker = CallWorker(call)
        worker.done.connect(then)
        self.tmdb_worker = self.ctx.track(worker)
        worker.start()
        self._refresh_tmdb()
        return worker

    def connect_tmdb(self, approve=None) -> CallWorker | None:
        """Ask TMDB for a request token, have it approved in the browser, then swap it for
        the account's access token. `approve(url)` stands in for the browser and the
        approval question (tests); it returns whether to go on."""
        read_token = self.ctx.store.get(self.profile_id, "TMDB_READ_TOKEN")
        if not read_token:
            self._refresh_tmdb()
            return None
        client = self._tmdb_client([read_token])

        def requested(token, error: str) -> None:
            self.tmdb_worker.wait()
            if token is None:
                self._tmdb_problem(f"TMDB said no: {error}")
                return
            url = tmdb.APPROVE_URL.format(token)
            if not (approve or self._approve_in_browser)(url):
                self._refresh_tmdb()
                return
            self._run_tmdb(lambda: tmdb.access_token(client, read_token, token), connected)

        def connected(result, error: str) -> None:
            self.tmdb_worker.wait()
            if result is None:
                self._tmdb_problem(f"Not connected: {error}")
                return
            user_token, account_id = result
            self.ctx.store.set(self.profile_id, "TMDB_USER_TOKEN", user_token)
            self.ctx.store.set(self.profile_id, "TMDB_ACCOUNT_ID", account_id)
            self._refresh_tmdb()

        worker = self._run_tmdb(lambda: tmdb.request_token(client, read_token), requested)
        self._message(self.tmdb_status, "Asking TMDB…", ok=True)
        return worker

    def _tmdb_problem(self, text: str) -> None:
        self._refresh_tmdb()
        self._message(self.tmdb_status, text, ok=False)

    def _approve_in_browser(self, url: str) -> bool:
        QDesktopServices.openUrl(QUrl(url))
        answer = QMessageBox.question(
            self,
            "Approve Taste Engine on TMDB",
            "TMDB opened in your browser. Approve Taste Engine there, then click Yes here. "
            "Click No to stop.",
        )
        return answer == QMessageBox.StandardButton.Yes

    def disconnect_tmdb(self) -> CallWorker | None:
        """Forget the account's token here, and ask TMDB to forget it too."""
        read_token = self.ctx.store.get(self.profile_id, "TMDB_READ_TOKEN")
        user_token = self.ctx.store.get(self.profile_id, "TMDB_USER_TOKEN")
        for name in ("TMDB_USER_TOKEN", "TMDB_ACCOUNT_ID"):
            self.ctx.store.delete(self.profile_id, name)
        self._refresh_tmdb()
        if not (read_token and user_token):
            return None
        client = self._tmdb_client([read_token, user_token])

        def revoked(_result, _error: str) -> None:
            # Forgotten here either way; TMDB forgetting it too is a courtesy.
            self.tmdb_worker.wait()
            self._refresh_tmdb()

        return self._run_tmdb(lambda: tmdb.revoke(client, read_token, user_token), revoked)

    # -- Hardcover ------------------------------------------------------------

    def _refresh_hardcover(self) -> None:
        if not self.profile_id:
            return
        connected = credentials.hardcover_connected(self.profile_id, self.ctx.store)
        waiting = self.sign_in is not None
        busy = self.hardcover_worker is not None and self.hardcover_worker.isRunning()
        self.hardcover_connect.setEnabled(not connected and not busy)
        self.hardcover_disconnect.setEnabled(connected and not busy)
        self.hardcover_stop.setVisible(waiting)
        self.hardcover_copy.setVisible(waiting)
        if waiting:
            return  # the waiting message stays until the browser comes back
        if connected:
            conn = self.ctx.connect(self.profile_id)
            try:
                last = hardcover.last_sync(conn)
                tz_name = settings.get(conn, "timezone")
            finally:
                conn.close()
            when = (
                f" Books last synced {local_time.describe(last, tz_name, self.ctx.now())}."
                if last
                else ""
            )
            text = f"Connected. Sync everything on the Dashboard now includes Hardcover.{when}"
        else:
            text = "Not connected yet."
        self._message(self.hardcover_status, text, ok=True)
        self.hardcover_status.setProperty("role", "ok" if connected else "muted")
        self.hardcover_status.style().unpolish(self.hardcover_status)
        self.hardcover_status.style().polish(self.hardcover_status)

    def _run_hardcover(self, call, then) -> CallWorker:
        worker = CallWorker(call)
        worker.done.connect(then)
        self.hardcover_worker = self.ctx.track(worker)
        worker.start()
        self._refresh_hardcover()
        return worker

    def connect_hardcover(self, browser=None) -> CallWorker | None:
        """Sign in to Hardcover: open its approval page in the browser, wait for it to send
        you back to the app, then swap the code for tokens. `browser(url)` stands in for
        opening the browser (tests)."""
        if not self.profile_id or self.sign_in is not None:
            return None
        try:
            sign_in = hardcover.SignIn()
        except OSError:
            self._message(
                self.hardcover_status, "Couldn't listen for Hardcover's reply here.", ok=False
            )
            return None
        self.sign_in = sign_in
        client = hardcover.client_for(self._tmdb_client([]))
        profile_id = self.profile_id
        now = self.ctx.now()

        def sign_in_and_swap():
            code = sign_in.wait()
            tokens = hardcover.exchange(client, sign_in, code, now)
            client.secrets += [tokens.access, tokens.refresh]
            return tokens, hardcover.me(client, tokens.access)[1]

        def done(result, error: str) -> None:
            self.hardcover_worker.wait()
            self.sign_in = None
            if result is None:
                self._refresh_hardcover()
                if error != "Stopped.":
                    self._message(self.hardcover_status, f"Not connected: {error}", ok=False)
                return
            tokens, username = result
            credentials.save_hardcover(profile_id, self.ctx.store, tokens)
            self._refresh_hardcover()
            if username:
                self._message(
                    self.hardcover_status,
                    f"Connected as {username}. Bringing in your books now.",
                    ok=True,
                )
            self.hardcover_connected.emit()

        worker = self._run_hardcover(sign_in_and_swap, done)
        (browser or (lambda url: QDesktopServices.openUrl(QUrl(url))))(sign_in.url)
        self._message(
            self.hardcover_status,
            "Approve Taste Engine on Hardcover in your browser. This page carries on by itself "
            "once you're sent back.",
            ok=True,
        )
        return worker

    def stop_hardcover(self) -> None:
        if self.sign_in is not None:
            self.sign_in.cancel()

    def copy_hardcover_link(self) -> None:
        if self.sign_in is not None:
            QApplication.clipboard().setText(self.sign_in.url)
            self._message(
                self.hardcover_status,
                "Link copied. Paste it into your browser, approve, and come back here.",
                ok=True,
            )

    def disconnect_hardcover(self) -> CallWorker | None:
        """Forget the tokens here, and sign out on Hardcover too."""
        if not self.profile_id:
            return None
        refresh = self.ctx.store.get(self.profile_id, "HARDCOVER_REFRESH_TOKEN")
        credentials.forget_hardcover(self.profile_id, self.ctx.store)
        self._refresh_hardcover()
        if not refresh:
            return None
        client = self._tmdb_client([refresh])

        def revoked(_result, _error: str) -> None:
            # Forgotten here either way; Hardcover forgetting it too is a courtesy.
            self.hardcover_worker.wait()
            self._refresh_hardcover()

        return self._run_hardcover(lambda: hardcover.revoke(client, refresh), revoked)

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


class ChoiceBoxes(QWidget):
    """A checkbox per choice, four to a row. The value is the ticked keys, in order."""

    def __init__(self, choices: dict[str, str]) -> None:
        super().__init__()
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 4, 0, 4)
        grid.setHorizontalSpacing(18)
        self.boxes: dict[str, QCheckBox] = {}
        for i, (key, text) in enumerate(choices.items()):
            box = QCheckBox(text)
            self.boxes[key] = box
            grid.addWidget(box, i // 4, i % 4)
        grid.setColumnStretch(4, 1)

    def set_value(self, stored: str) -> None:
        picked = {part.strip().lower() for part in stored.split(",")}
        for key, box in self.boxes.items():
            box.setChecked(key in picked)

    def value(self) -> list[str]:
        return [key for key, box in self.boxes.items() if box.isChecked()]


def notices_path() -> Path | None:
    """THIRD_PARTY_NOTICES.txt next to the packaged exe. Running from source there's none."""
    if not getattr(sys, "frozen", False):
        return None
    path = Path(sys.executable).resolve().parent / "THIRD_PARTY_NOTICES.txt"
    return path if path.is_file() else None
