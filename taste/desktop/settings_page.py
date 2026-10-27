"""Settings: sources (taste.desktop.sources_card), profile settings, appearance, the data
folder, deleting the profile, and About."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QCompleter,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLayout,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from taste import __version__, backup, profiles, settings
from taste.config import data_dir
from taste.desktop import motion, theme, ui_state
from taste.desktop.context import AppContext
from taste.desktop.dialogs import ConfirmDeleteDialog
from taste.desktop.sources_card import SourcesCard
from taste.desktop.widgets import Card, ScrollArea, Segmented, heading, label, section
from taste.desktop.workers import BackupWorker
from taste.settings import SettingError

CACHE_SIZES = ((100, "100 MB"), (300, "300 MB"), (1000, "1 GB"))
FORM_SETTINGS = [f for f in settings.SETTINGS if f.group in settings.FORM_GROUPS]
LABEL_WIDTH = 320
TMDB_LOGO_HEIGHT = 14  # the app's own logo is 30 px in the top bar
FIELD_WIDTH = 600  # inputs stop here, so nothing stretches across a wide screen
TWO_COLUMNS = 1500  # page width from which settings sit beside keys and data
THREE_COLUMNS = 2200  # and from which Advanced splits the settings in two
DELETE_WAIT_MS = 5000  # a cancelled check stops between media, a recap between periods
CREDITS = (
    "Built with Python and Qt for Python (PySide6, used under the LGPL 3.0), plus requests, "
    "keyring, python-dotenv, and tzdata. Fonts: Space Grotesk and Inter (SIL Open Font "
    "License). Anime data comes from MyAnimeList, music data from Last.fm, some artist "
    "photos from Deezer, books from Hardcover, and film and TV data "
    "from TMDB; Taste Engine isn't affiliated with any of them. This product uses the TMDB "
    "API but is not endorsed or certified by TMDB."
)


class SettingsPage(QWidget):
    profile_restored = Signal(str)
    transfer_busy = Signal()
    message = Signal(str)
    profile_deleted = Signal(str)
    settings_saved = Signal(list)  # keys that changed

    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        self.profile_id: str | None = None
        self.backup_worker: BackupWorker | None = None
        self._transfer_active = False
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
        self.columns = QHBoxLayout()
        self.columns.setSpacing(16)
        self.left = QVBoxLayout()
        self.left.setSpacing(16)
        self.right = QVBoxLayout()
        self.right.setSpacing(16)
        self.columns.addLayout(self.left, 1)
        self.columns.addLayout(self.right, 1)
        layout.addLayout(self.columns, 1)

        # Every source in one card: keys, usernames, connecting, syncing
        self.sources = SourcesCard(ctx)
        self.sources_card = self.sources

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
        for group in settings.FORM_GROUPS:
            fields = [f for f in FORM_SETTINGS if f.group == group]
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
        archive_row = QHBoxLayout()
        self.backup_button = QPushButton("Back up this profile…")
        self.restore_button = QPushButton("Restore a profile…")
        self.backup_button.clicked.connect(self.backup_profile)
        self.restore_button.clicked.connect(self.restore_profile)
        archive_row.addWidget(self.backup_button)
        archive_row.addWidget(self.restore_button)
        archive_row.addStretch()
        data_card.body.addLayout(archive_row)
        data_card.body.addWidget(
            label(
                "Backups include history, recaps, and the profile picture. "
                "Enter source keys again after restoring.",
                role="caption",
                wrap=True,
            )
        )
        # Posters and covers are kept on disk for every profile (Advanced).
        self.picture_row = QWidget()
        pictures = QHBoxLayout(self.picture_row)
        pictures.setContentsMargins(0, 6, 0, 0)
        pictures.addWidget(label("Pictures kept on disk"))
        self.cache_switch = Segmented([(str(mb), name) for mb, name in CACHE_SIZES])
        self.cache_switch.changed.connect(self.set_cache_size)
        pictures.addWidget(self.cache_switch)
        self.picture_use = label("", role="muted")
        pictures.addWidget(self.picture_use)
        pictures.addStretch()
        clear = QPushButton("Clear pictures")
        clear.setToolTip("Delete the saved posters and covers. They download again when shown.")
        clear.clicked.connect(self.clear_pictures)
        pictures.addWidget(clear)
        data_card.body.addWidget(self.picture_row)
        self.cache_switch.set_value(str(ui_state.load().get("image_cache_mb", 300)))
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
            self.sources_card,
            self.appearance_card,
            self.form_card,
            self.data_card,
            self.about_card,
        )
        for layout in (self.left, self.right):
            for card in cards:
                layout.removeWidget(card)
        # One column takes the whole width; the empty right one had been taking a share.
        self.columns.setStretch(1, 0 if columns == 1 else 1)
        if columns == 1:
            for card in cards:
                self.left.addWidget(card)
        else:
            for card in (self.sources_card, self.about_card):
                self.left.addWidget(card)
            # Appearance and Data under the profile settings, so the two columns come out
            # about the same height (as little empty card as possible, 2026-10-16).
            for card in (self.form_card, self.appearance_card, self.data_card):
                self.right.addWidget(card)
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
                for f in FORM_SETTINGS
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
        if remember and mode != self.mode and self.isVisible():
            # Rows come and go and the columns reflow: fade it in like a page change.
            def switch() -> None:
                self.set_mode(mode, remember=False)
                ui_state.save(settings_mode=mode)

            motion.crossfade(self.scroll.viewport(), switch)
            return
        self.mode = mode
        self.mode_switch.set_value(mode)
        advanced = mode == "advanced"
        for field in FORM_SETTINGS:
            self._form_of[field.key].setRowVisible(
                self.inputs[field.key], advanced or not field.advanced
            )
        for group, title in self.heading_widgets.items():
            title.setVisible(
                advanced or any(not f.advanced for f in FORM_SETTINGS if f.group == group)
            )
        self.picture_row.setVisible(advanced)
        if advanced:
            self.refresh_picture_use()
        hidden = sum(f.advanced for f in FORM_SETTINGS)
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
        self.settings_message.setText("")
        self.sources.set_profile(profile_id)
        self.refresh()

    def refresh(self) -> None:
        if not self.profile_id:
            return
        conn = self.ctx.connect(self.profile_id)
        try:
            values = settings.get_all(conn)
        finally:
            conn.close()
        for field in FORM_SETTINGS:
            self._set_input(self.inputs[field.key], field, values[field.key])
        self.data_path.setText(f"Every profile is stored in {data_dir().resolve()}")
        self.refresh_status()

    def refresh_status(self) -> None:
        """The sources' state lines (connected, last synced), without touching the form,
        so a sync ending never throws away settings being edited."""
        self.sources.refresh_status()

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
            for field in FORM_SETTINGS:
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

    # -- Pictures -------------------------------------------------------------

    def set_cache_size(self, megabytes: str) -> None:
        """100 MB, 300 MB, or 1 GB of posters and covers, for every profile."""
        value = int(megabytes) if megabytes.isdigit() else 300
        self.cache_switch.set_value(str(value))
        self.ctx.art.cache.max_bytes = value << 20
        ui_state.save(image_cache_mb=value)
        self.refresh_picture_use()

    def refresh_picture_use(self) -> None:
        used = self.ctx.art.cache.size() / (1 << 20)
        self.picture_use.setText("Using under 1 MB" if used < 1 else f"Using {used:,.0f} MB")

    def clear_pictures(self) -> None:
        self.ctx.art.clear()
        self.refresh_picture_use()

    # -- Motion ---------------------------------------------------------------

    def set_motion(self, choice: str, remember: bool = True) -> None:
        """Full, Reduced (quick fades, nothing slides), or Off. Kept for every profile."""
        choice = choice if choice in motion.LEVELS else "full"
        self.motion_switch.set_value(choice)
        if remember:
            ui_state.save(motion=choice)
        notes = {
            "full": "Pages and new cards fade in, panels slide, and pictures fade in.",
            "reduced": "Quick fades only. Nothing slides or moves across the screen.",
            "off": "Everything changes at once.",
        }
        note = notes[choice]
        if not motion.windows_allows():
            note += " Windows' animation effects are off, so the app doesn't animate either."
        self.motion_note.setText(note)

    def is_transferring(self) -> bool:
        return self._transfer_active

    def backup_profile(self, path: str | Path | None = None) -> bool:
        if not self.profile_id or self.is_busy():
            return False
        if isinstance(path, bool):  # clicked(bool)
            path = None
        if path is None:
            path, _ = QFileDialog.getSaveFileName(
                self,
                "Back up this profile",
                "profile.tasteprofile",
                "Taste Engine profile (*.tasteprofile)",
            )
        return self._start_transfer("backup", path, self.profile_id) if path else False

    def restore_profile(
        self, path: str | Path | None = None, profile_id: str | None = None
    ) -> bool:
        if self.is_busy():
            return False
        if isinstance(path, bool):
            path = None
        if path is None:
            path, _ = QFileDialog.getOpenFileName(
                self, "Restore a profile", "", "Taste Engine profile (*.tasteprofile)"
            )
            if not path:
                return False
            # Offer the backup's own id, which suits a new computer. One already used here
            # (a profile or its retained recaps) needs a new one: restore never replaces.
            try:
                offered = profiles.validate_id(backup.read_manifest(path).get("profile_id"))
            except backup.BackupError as exc:
                self.message.emit(str(exc))
                return False
            except profiles.ProfileError:
                offered = ""
            prompt = (
                "That id is already used here. Type a new one."
                if offered and backup.id_in_use(offered)
                else "Profile id"
            )
            profile_id, ok = QInputDialog.getText(
                self, "Restore a profile", prompt, QLineEdit.EchoMode.Normal, offered
            )
            if not ok:
                return False
            profile_id = profile_id.strip() or None  # blank keeps the backup's own id
        return self._start_transfer("restore", path, profile_id) if path else False

    def _start_transfer(self, operation: str, path, profile_id: str | None) -> bool:
        worker = BackupWorker(operation, Path(path), profile_id)
        self.backup_worker = worker
        worker.done.connect(self._transfer_done)
        worker.finished.connect(self._transfer_finished)
        self.ctx.track(worker)
        self.backup_button.setEnabled(False)
        self.restore_button.setEnabled(False)
        self._transfer_active = True
        worker.start()
        self.transfer_busy.emit()
        return True

    def _transfer_done(self, profile_id: str, error: str) -> None:
        if error:
            self.message.emit(error)
        elif self.backup_worker.operation == "restore":
            self.profile_restored.emit(profile_id)
            self.message.emit("Profile restored. Enter its source keys in Settings.")
        else:
            self.message.emit("Profile backup saved.")

    def _transfer_finished(self) -> None:
        self._transfer_active = False
        self.backup_button.setEnabled(True)
        self.restore_button.setEnabled(True)
        self.transfer_busy.emit()

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
        # A cancelled model check or a recap drawing can still hold the database open, and
        # Windows can't delete an open file: stop them first, as closing the window does.
        jobs = [t for t in self.ctx.threads if getattr(t, "profile_id", None) == deleted]
        for job in jobs:
            if hasattr(job, "cancel"):
                job.cancel()
        if not all(job.wait(DELETE_WAIT_MS) for job in jobs):
            self._message(
                self.settings_message,
                "Wait for the background work to finish before deleting.",
                ok=False,
            )
            return False
        self.ctx.forget(deleted)
        profiles.delete(deleted, store=self.ctx.store)
        self.profile_deleted.emit(deleted)
        return True


class ChoiceBoxes(QWidget):
    """A checkbox per choice, up to four to a row: as many as fit the width it gets, so
    long names (the Dashboard's cards) wrap instead of widening the page. Its minimum
    is one box wide. The value is the ticked keys, in order."""

    MOST = 4
    SPACING = 18

    def __init__(self, choices: dict[str, str]) -> None:
        super().__init__()
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 4, 0, 4)
        self.grid.setHorizontalSpacing(self.SPACING)
        # The widget's own minimum (one box), not the grid's (a full row).
        self.grid.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self.boxes = {key: QCheckBox(text) for key, text in choices.items()}
        self.per_row = 0
        self._place(self.MOST)

    def _widest(self) -> int:
        return max((box.sizeHint().width() for box in self.boxes.values()), default=0)

    def fits(self, width: int) -> int:
        """The most boxes to a row that fit in `width`, each column as wide as its own
        widest box (short names sit closer than long ones)."""
        widths = [box.sizeHint().width() for box in self.boxes.values()]
        for per_row in range(self.MOST, 1, -1):
            columns = [max(widths[j::per_row], default=0) for j in range(per_row)]
            if sum(columns) + self.SPACING * (per_row - 1) <= width:
                return per_row
        return 1

    def _place(self, per_row: int) -> None:
        if per_row == self.per_row:
            return
        self.grid.setColumnStretch(self.per_row, 0)
        self.per_row = per_row
        for box in self.boxes.values():
            self.grid.removeWidget(box)
        for i, box in enumerate(self.boxes.values()):
            self.grid.addWidget(box, i // per_row, i % per_row)
        self.grid.setColumnStretch(per_row, 1)
        self.updateGeometry()

    def minimumSizeHint(self) -> QSize:
        return QSize(self._widest(), self.grid.minimumSize().height())

    def resizeEvent(self, event) -> None:
        self._place(self.fits(event.size().width()))
        super().resizeEvent(event)

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
