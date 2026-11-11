"""Small window preferences shared by every profile, like whether the sidebar is collapsed.

Kept in <data folder>/ui_state.json, never in the Windows registry, so tests (which
point the data folder at a temp directory) can't touch the real ones. A missing or
broken file just means the defaults.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from taste.config import data_dir

FILE_NAME = "ui_state.json"
MIX_FILTERS = ("all", "anime", "film", "tv", "book")
CACHE_SIZES_MB = (100, 300, 1000)  # Settings' picture cache choices


def mix_filter(profile_id: str | None) -> str:
    values = load().get("for_you_mix_filter", {})
    value = values.get(profile_id) if isinstance(values, dict) else None
    return value if isinstance(value, str) and value in MIX_FILTERS else "all"


def set_mix_filter(profile_id: str | None, choice: str) -> None:
    if profile_id is None or choice not in MIX_FILTERS:
        return
    values = load().get("for_you_mix_filter", {})
    values = dict(values) if isinstance(values, dict) else {}
    values[profile_id] = choice
    save(for_you_mix_filter=values)


# The file as last read or written, and where it was. Every animation asks for the
# motion level, so reading the file each time cost two disk reads per picture that
# arrived and one per tile that faded in. Only this app writes the file.
_cached: tuple[Path, dict[str, Any]] | None = None


def load() -> dict[str, Any]:
    global _cached
    path = data_dir() / FILE_NAME
    if _cached is None or _cached[0] != path:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            value = {}
        _cached = (path, value if isinstance(value, dict) else {})
    return dict(_cached[1])


def save(**changes: Any) -> None:
    global _cached
    state = load()
    state.update(changes)
    path = data_dir() / FILE_NAME
    partial = path.with_name(FILE_NAME + ".part")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Written aside and swapped in, so a save cut short never leaves a broken file
        # (which reads as empty, losing every preference).
        partial.write_text(json.dumps(state, indent=2), encoding="utf-8")
        os.replace(partial, path)
    except OSError:
        return  # a preference that can't be saved isn't worth an error dialog
    _cached = (path, state)
