"""New profile and delete confirmation dialogs."""

from __future__ import annotations

from PySide6.QtCore import QRegularExpression
from PySide6.QtGui import QRegularExpressionValidator
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QVBoxLayout,
)

from taste.desktop.widgets import heading, label


class NewProfileDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("New profile")
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)
        layout.addWidget(heading("New profile", 22))
        layout.addWidget(
            label("Each profile has its own API keys, settings, and data.", role="muted", wrap=True)
        )
        form = QFormLayout()
        self.profile_id = QLineEdit()
        self.profile_id.setPlaceholderText("for example: me")
        self.profile_id.setMaxLength(40)
        self.profile_id.setValidator(
            QRegularExpressionValidator(QRegularExpression(r"[a-z0-9][a-z0-9_-]{0,39}"))
        )
        self.display_name = QLineEdit()
        self.display_name.setPlaceholderText("optional")
        self.display_name.setMaxLength(200)
        form.addRow("Profile ID", self.profile_id)
        form.addRow("Display name", self.display_name)
        layout.addLayout(form)
        layout.addWidget(
            label("Lowercase letters, numbers, - and _. Can't be changed later.", role="muted")
        )
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Create")
        buttons.button(QDialogButtonBox.StandardButton.Ok).setProperty("role", "primary")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self) -> tuple[str, str]:
        return self.profile_id.text().strip(), self.display_name.text().strip()


class ConfirmDeleteDialog(QDialog):
    """Deleting a profile is permanent, so it takes typing the profile ID."""

    def __init__(self, profile_id: str, parent=None) -> None:
        super().__init__(parent)
        self.expected = profile_id
        self.setWindowTitle("Delete profile")
        self.setMinimumWidth(440)
        layout = QVBoxLayout(self)
        layout.addWidget(heading("Delete this profile?", 22))
        layout.addWidget(
            label(
                f"This permanently deletes the profile's data and its saved API keys. "
                f"Type {profile_id} to confirm.",
                wrap=True,
            )
        )
        self.confirm = QLineEdit()
        self.confirm.setPlaceholderText(profile_id)
        layout.addWidget(self.confirm)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        ok = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        ok.setText("Delete profile")
        ok.setProperty("role", "danger")
        ok.setEnabled(False)
        self.confirm.textChanged.connect(lambda text: ok.setEnabled(text.strip() == self.expected))
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
