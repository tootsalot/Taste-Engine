"""Paths and environment loading.

Where data lives:
- Running from source: `data/` and `reports/` in the project folder.
- TASTE_DATA_DIR set: that folder holds `profiles/` and `reports/` instead.
  Packaged releases use this (see docs/PLAN_APP.md, Releases).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    override = os.environ.get("TASTE_DATA_DIR", "").strip()
    return Path(override).expanduser() if override else PROJECT_ROOT / "data"


def reports_dir() -> Path:
    override = os.environ.get("TASTE_DATA_DIR", "").strip()
    return Path(override).expanduser() / "reports" if override else PROJECT_ROOT / "reports"


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
