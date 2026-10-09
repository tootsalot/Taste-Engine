"""Settings from environment variables, optionally loaded from a local .env file."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "taste.db"
REPORTS_DIR = PROJECT_ROOT / "reports"


class ConfigError(RuntimeError):
    """A required environment variable is missing."""


def load_env() -> None:
    """Load .env from the project root if it exists.

    Variables that are already set win over the file, and a missing .env is fine
    (that's the normal case in a cloud session).
    """
    load_dotenv(PROJECT_ROOT / ".env", override=False)


def _require(*names: str) -> dict[str, str]:
    values = {name: os.environ.get(name, "").strip() for name in names}
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise ConfigError(
            f"Missing environment variable(s): {', '.join(missing)}. "
            "Set them in your shell or in a .env file (see .env.example)."
        )
    return values


@dataclass(frozen=True)
class MalSettings:
    client_id: str
    username: str


@dataclass(frozen=True)
class LastfmSettings:
    api_key: str
    username: str


def mal_settings() -> MalSettings:
    values = _require("MAL_CLIENT_ID", "MAL_USERNAME")
    return MalSettings(values["MAL_CLIENT_ID"], values["MAL_USERNAME"])


def lastfm_settings() -> LastfmSettings:
    values = _require("LASTFM_API_KEY", "LASTFM_USERNAME")
    return LastfmSettings(values["LASTFM_API_KEY"], values["LASTFM_USERNAME"])
