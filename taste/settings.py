"""Per-profile settings, stored in each profile's app_settings table.

One registry defines every setting's type, default, and validation, so the web
settings page, the CLI, and the report views all agree.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from taste.db import utc_now


class SettingError(ValueError):
    """A setting value failed validation."""


CAPTURE_SCOPES = {
    "desktop_only": "Desktop only",
    "mobile_only": "Phone only",
    "all_devices": "All devices",
    "unknown": "Not sure",
}

# Used when the scope note setting is blank.
STANDARD_SCOPE_NOTES = {
    "desktop_only": (
        "Desktop listening only. Only desktop plays reach Last.fm, so this is a sample "
        "of the listening, not all of it."
    ),
    "mobile_only": (
        "Phone listening only. Only phone plays reach Last.fm, so this is a sample "
        "of the listening, not all of it."
    ),
    "all_devices": "All devices. Every device used for listening scrobbles to Last.fm.",
    "unknown": (
        "Capture scope not set. It isn't known which devices scrobble to Last.fm, so "
        "treat this as a sample of the listening, not all of it."
    ),
}


@dataclass(frozen=True)
class Setting:
    key: str
    label: str
    kind: str  # text, int, float, bool, choice, timezone
    default: Any
    help: str = ""
    choices: dict[str, str] | None = None
    minimum: float | None = None
    maximum: float | None = None
    max_length: int = 200


SETTINGS: list[Setting] = [
    Setting("display_name", "Display name", "text", "", "Shown in the app. Blank uses the ID."),
    Setting("mal_username", "MyAnimeList username", "text", "", "The list has to be public."),
    Setting("lastfm_username", "Last.fm username", "text", ""),
    Setting(
        "timezone",
        "Time zone",
        "timezone",
        "America/Phoenix",
        "Used for hour of day, weekday, month, and year in the Last.fm reports.",
    ),
    Setting(
        "lastfm_capture_scope",
        "Which devices scrobble to Last.fm?",
        "choice",
        "unknown",
        "Applies to scrobbles synced from now on. Earlier plays keep their label.",
        choices=CAPTURE_SCOPES,
    ),
    Setting(
        "lastfm_scope_note",
        "Last.fm scope note",
        "text",
        "",
        "Printed on every Last.fm report. Blank uses a standard note for the scope above.",
        max_length=500,
    ),
    Setting("include_nsfw", "Include NSFW anime", "bool", True, "Sends nsfw=true to MAL."),
    Setting(
        "genre_min_sample",
        "Genre minimum sample",
        "int",
        5,
        "Genres with fewer scored shows are left out of the genre report.",
        minimum=1,
        maximum=1000,
    ),
    Setting(
        "in_line_threshold",
        "In-line threshold",
        "float",
        0.25,
        "How close to the community average (in MAL points) counts as in line.",
        minimum=0,
        maximum=9,
    ),
    Setting("top_n_all_time", "Top N, all time", "int", 50, minimum=1, maximum=1000),
    Setting("top_n_per_year", "Top N, per year", "int", 25, minimum=1, maximum=1000),
    Setting("top_n_per_month", "Top N, per month", "int", 10, minimum=1, maximum=1000),
    Setting(
        "lastfm_lookback_days",
        "Last.fm lookback days",
        "int",
        14,
        "How far back each incremental sync re-checks for late scrobbles.",
        minimum=0,
        maximum=365,
    ),
]
BY_KEY = {s.key: s for s in SETTINGS}


def _to_text(setting: Setting, value: Any) -> str:
    if setting.kind == "bool":
        return "1" if value else "0"
    return str(value)


def _from_text(setting: Setting, text: str) -> Any:
    if setting.kind == "int":
        return int(text)
    if setting.kind == "float":
        return float(text)
    if setting.kind == "bool":
        return text == "1"
    return text


def validate(key: str, raw: Any) -> Any:
    """Check a raw value (form text, CLI text, or Python value) and return it typed."""
    setting = BY_KEY.get(key)
    if setting is None:
        raise SettingError(f"Unknown setting: {key}")
    if setting.kind == "bool":
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() in {"1", "true", "on", "yes"}
    text = str(raw).strip()
    if setting.kind in ("int", "float"):
        try:
            value = int(text) if setting.kind == "int" else float(text)
        except ValueError:
            raise SettingError(f"{setting.label} must be a number.") from None
        if setting.minimum is not None and value < setting.minimum:
            raise SettingError(f"{setting.label} must be at least {setting.minimum:g}.")
        if setting.maximum is not None and value > setting.maximum:
            raise SettingError(f"{setting.label} must be at most {setting.maximum:g}.")
        return value
    if setting.kind == "choice":
        if text not in (setting.choices or {}):
            raise SettingError(f"{setting.label}: pick one of the listed options.")
        return text
    if setting.kind == "timezone":
        try:
            ZoneInfo(text)
        except (ZoneInfoNotFoundError, ValueError):
            raise SettingError(f"Unknown time zone: {text!r}.") from None
        return text
    if len(text) > setting.max_length:
        raise SettingError(f"{setting.label} is too long (max {setting.max_length}).")
    return text


def ensure_defaults(conn: sqlite3.Connection) -> None:
    """Insert any missing settings with their defaults. Report views rely on every row existing."""
    now = utc_now()
    for setting in SETTINGS:
        conn.execute(
            "INSERT INTO app_settings (setting_key, setting_value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT (setting_key) DO NOTHING",
            (setting.key, _to_text(setting, setting.default), now),
        )


def get(conn: sqlite3.Connection, key: str) -> Any:
    setting = BY_KEY[key]
    row = conn.execute(
        "SELECT setting_value FROM app_settings WHERE setting_key = ?", (key,)
    ).fetchone()
    return setting.default if row is None else _from_text(setting, row[0])


def get_all(conn: sqlite3.Connection) -> dict[str, Any]:
    return {s.key: get(conn, s.key) for s in SETTINGS}


def set_value(conn: sqlite3.Connection, key: str, raw: Any) -> Any:
    """Validate and store one setting. Returns the typed value."""
    value = validate(key, raw)
    conn.execute(
        "INSERT INTO app_settings (setting_key, setting_value, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT (setting_key) DO UPDATE SET setting_value = excluded.setting_value, "
        "updated_at = excluded.updated_at",
        (key, _to_text(BY_KEY[key], value), utc_now()),
    )
    if key in ("lastfm_capture_scope", "lastfm_scope_note"):
        apply_source_notes(conn)
    if key == "timezone":
        from taste import local_time  # local import: local_time imports this module

        local_time.refresh(conn)
    return value


def lastfm_scope_note(conn: sqlite3.Connection) -> str:
    note = get(conn, "lastfm_scope_note")
    return note or STANDARD_SCOPE_NOTES[get(conn, "lastfm_capture_scope")]


def apply_source_notes(conn: sqlite3.Connection) -> None:
    """Copy the profile's Last.fm scope settings into core_sources, where reports read them."""
    conn.execute(
        "UPDATE core_sources SET default_capture_scope = ?, scope_note = ? WHERE source = 'lastfm'",
        (get(conn, "lastfm_capture_scope"), lastfm_scope_note(conn)),
    )


def timezone_names() -> list[str]:
    return sorted(available_timezones())
