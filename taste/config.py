"""Paths and environment loading.

Where data lives (profiles/ and reports/):
- TASTE_DATA_DIR set: that folder.
- Packaged app (PyInstaller): the user's data folder, so updating or deleting the
  app never touches the data. On Windows that's %LOCALAPPDATA%\\taste-engine.
- Running from source: data/ and reports/ in the project folder.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_NAME = "taste-engine"


def is_frozen() -> bool:
    """True when running as a PyInstaller build."""
    return bool(getattr(sys, "frozen", False))


def user_data_dir() -> Path:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = str(Path.home() / "Library" / "Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / APP_NAME


def standalone_dir() -> Path | None:
    """The single folder holding everything, or None when running from source.

    It belongs to the app alone (the packaged app's folder, or TASTE_DATA_DIR), so the
    uninstaller may delete it. From source, data/ and reports/ sit in the project.
    """
    override = os.environ.get("TASTE_DATA_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    if is_frozen():
        return user_data_dir()
    return None


def data_dir() -> Path:
    base = standalone_dir()
    return base if base is not None else PROJECT_ROOT / "data"


def reports_dir() -> Path:
    base = standalone_dir()
    return base / "reports" if base is not None else PROJECT_ROOT / "reports"


class ConfigError(RuntimeError):
    """A required credential or username is missing."""


def load_env() -> None:
    """Load .env from the project root if it exists.

    Variables that are already set win over the file, and a missing .env is fine.
    """
    load_dotenv(PROJECT_ROOT / ".env", override=False)


@dataclass(frozen=True)
class MalSettings:
    client_id: str
    username: str


@dataclass(frozen=True)
class LastfmSettings:
    api_key: str
    username: str
