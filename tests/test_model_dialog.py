"""The rating-model question uses plain words and fits a small desktop window."""

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QLabel

from taste import settings
from taste.desktop.dialogs import ModelChoiceDialog


def checks():
    summary = {
        "count": 100,
        "within_one": 0.61,
        "top_hits": 0.5,
        "verdict": "same",
        "interval": [-0.1, 0.1],
    }
    return {
        "count": 300,
        "models": {
            name: {
                **summary,
                "verdict": None if name == "usual" else "same",
                "media": {
                    medium: {
                        **summary,
                        "count": 19 if medium == "tv" else 100,
                        "within_one": None if medium == "tv" else 0.61,
                        "top_hits": None if medium == "tv" else 0.5,
                    }
                    for medium in ("anime", "film", "tv", "book")
                },
            }
            for name in settings.REC_MODELS
        },
    }


def test_dialog_uses_shared_descriptions_plain_accuracy_and_one_choice(qtbot):
    dialog = ModelChoiceDialog(checks())
    qtbot.addWidget(dialog)
    text = "\n".join(widget.text() for widget in dialog.findChildren(QLabel))
    assert "How should For You guess your ratings?" in text
    assert all(description in text for description in settings.REC_MODEL_DESCRIPTIONS.values())
    assert "61% of the time" in text
    assert "it ranked 5 in 10 near the top" in text
    assert "about the same as Usual" in text
    assert "MAE" not in text and "off by" not in text
    assert all(
        widget.textFormat() == Qt.TextFormat.PlainText for widget in dialog.findChildren(QLabel)
    )
    assert dialog.choices["usual"].isChecked()
    dialog.choices["blend"].setChecked(True)
    assert [name for name, radio in dialog.choices.items() if radio.isChecked()] == ["blend"]
    assert dialog.choice() is None
    dialog.use_button.click()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.choice() == "blend"


@pytest.mark.parametrize("selected", ("usual", "details", "similar", "blend"))
def test_dialog_keeps_the_current_shared_choice_and_decide_later_returns_none(qtbot, selected):
    dialog = ModelChoiceDialog(checks(), selected=selected)
    qtbot.addWidget(dialog)
    assert dialog.choices[selected].isChecked()
    dialog.later_button.click()
    assert dialog.result() == QDialog.DialogCode.Rejected
    assert dialog.choice() is None


def test_each_list_expander_names_media_and_explains_small_samples(qtbot):
    dialog = ModelChoiceDialog(checks())
    qtbot.addWidget(dialog)
    dialog.show()
    assert dialog.details["usual"].isHidden()
    dialog.expanders["usual"].click()
    assert dialog.details["usual"].isVisible()
    text = "\n".join(widget.text() for widget in dialog.details["usual"].findChildren(QLabel))
    assert all(medium in text for medium in ("Anime", "Films", "TV", "Books"))
    assert "Not enough ratings to check yet" in text
    assert "within half a star" in text


def test_dialog_can_scroll_vertically_without_sideways_scrolling(qtbot):
    dialog = ModelChoiceDialog(checks())
    qtbot.addWidget(dialog)
    dialog.resize(900, 700)
    dialog.show()
    for expander in dialog.expanders.values():
        expander.click()
    qtbot.waitUntil(lambda: dialog.scroll.verticalScrollBar().maximum() > 0)
    assert dialog.width() <= 1100 and dialog.height() <= 780
    assert dialog.scroll.horizontalScrollBar().maximum() == 0
