"""Profile dialogs and the question about choosing a rating model."""

from __future__ import annotations

from PySide6.QtCore import QRegularExpression, Qt
from PySide6.QtGui import QFont, QRegularExpressionValidator
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from taste import settings
from taste.desktop import theme
from taste.desktop.widgets import Card, card_title, heading, label
from taste.recommend import comparison


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


class ModelChoiceDialog(QDialog):
    """One choice for the four rated lists, with each held-out check in plain words."""

    def __init__(self, checks: dict, selected: str = "usual", parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Choose a rating model")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.resize(900, 740)
        self.setMinimumSize(640, 460)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(theme.GAP)
        title = heading("How should For You guess your ratings?", 24)
        title.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(
            label(
                "Sets Anime, Films, TV, and Books together. "
                "Advanced settings can change each list.",
                role="muted",
                wrap=True,
            )
        )
        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        rows = QVBoxLayout(content)
        rows.setContentsMargins(0, 0, 0, 0)
        rows.setSpacing(theme.GAP // 2)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.choices = {}
        self.expanders = {}
        self.details = {}
        models = checks.get("models", {})
        for name, title in settings.REC_MODELS.items():
            card = Card(content)
            card.body.setContentsMargins(14, 10, 14, 10)
            card.body.setSpacing(4)
            row = QHBoxLayout()
            radio = QRadioButton(title, card)
            radio.setFont(theme.heading_font(theme.CARD_TITLE_PX, QFont.Weight.DemiBold))
            radio.setProperty("model_name", name)
            self.group.addButton(radio)
            self.choices[name] = radio
            row.addWidget(radio, 1)
            summary = models.get(name, {})
            verdict = comparison.VERDICT_TEXT.get(summary.get("verdict"), "")
            if name != "usual" and verdict:
                row.addWidget(label(verdict, role="muted", wrap=True))
            expander = QPushButton("See each list", card)
            expander.setCheckable(True)
            expander.setProperty("model_name", name)
            expander.toggled.connect(self._toggle_lists)
            self.expanders[name] = expander
            row.addWidget(expander)
            card.body.addLayout(row)
            card.body.addWidget(label(settings.REC_MODEL_DESCRIPTIONS[name], wrap=True))
            card.body.addWidget(
                label(comparison.accuracy_text("all", summary), role="muted", wrap=True)
            )
            details = QWidget(card)
            detail_layout = QVBoxLayout(details)
            detail_layout.setContentsMargins(0, 6, 0, 0)
            detail_layout.setSpacing(10)
            for medium, medium_title in (
                ("anime", "Anime"),
                ("film", "Films"),
                ("tv", "TV"),
                ("book", "Books"),
            ):
                medium_summary = summary.get("media", {}).get(medium, {})
                medium_row = QHBoxLayout()
                medium_row.addWidget(card_title(medium_title))
                verdict = comparison.VERDICT_TEXT.get(medium_summary.get("verdict"), "")
                if name != "usual" and medium_summary.get("within_one") is not None and verdict:
                    medium_row.addWidget(label(verdict, role="muted", wrap=True), 1)
                detail_layout.addLayout(medium_row)
                detail_layout.addWidget(
                    label(
                        comparison.accuracy_text(medium, medium_summary),
                        role="muted",
                        wrap=True,
                    )
                )
            self.details[name] = details
            card.body.addWidget(details)
            details.hide()
            rows.addWidget(card)
        rows.addStretch(1)
        self.choices.get(selected, self.choices["usual"]).setChecked(True)
        self.scroll.setWidget(content)
        layout.addWidget(self.scroll, 1)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.later_button = QPushButton("Decide later", self)
        self.later_button.clicked.connect(self.reject)
        self.use_button = QPushButton("Use this", self)
        self.use_button.setProperty("role", "primary")
        self.use_button.clicked.connect(self.accept)
        buttons.addWidget(self.later_button)
        buttons.addWidget(self.use_button)
        layout.addLayout(buttons)

    def _toggle_lists(self, checked: bool) -> None:
        button = self.sender()
        if button is not None:
            self.details[button.property("model_name")].setVisible(checked)

    def choice(self) -> str | None:
        if self.result() != QDialog.DialogCode.Accepted:
            return None
        selected = self.group.checkedButton()
        return selected.property("model_name") if selected is not None else None
