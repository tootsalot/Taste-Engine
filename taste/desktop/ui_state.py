"""Small window preferences shared by every profile, like whether the sidebar is collapsed.

Kept in <data folder>/ui_state.json, never in the Windows registry, so tests (which
point the data folder at a temp directory) can't touch the real ones. A missing or
broken file just means the defaults.
"""

from __future__ import annotations

import json
from typing import Any

from taste.config import data_dir

FILE_NAME = "ui_state.json"


def load() -> dict[str, Any]:
    try:
        value = json.loads((data_dir() / FILE_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def save(**changes: Any) -> None:
    state = load()
    state.update(changes)
    path = data_dir() / FILE_NAME
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except OSError:
        pass  # a preference that can't be saved isn't worth an error dialog
