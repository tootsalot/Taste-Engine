"""Per-profile settings, stored in each profile's app_settings table.

One registry defines every setting's type, default, validation, and group, so
the Settings page, the CLI, and the report views all agree.
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


ANIME_TYPES = {
    "tv": "TV",
    "movie": "Movie",
    "ona": "ONA",
    "ova": "OVA",
    "special": "Special",
    "tv_special": "TV special",
    "music": "Music",
}

# Headings on the Settings page, in order. Every setting names one.
GROUPS = ["Profile", "Last.fm", "Reports", "Recommendations", "Syncing and storage"]


@dataclass(frozen=True)
class Setting:
    key: str
    label: str
    kind: str  # text, int, float, bool, choice, multichoice, timezone
    default: Any
    help: str = ""
    choices: dict[str, str] | None = None
    minimum: float | None = None
    maximum: float | None = None
    max_length: int = 200
    group: str = "Profile"
    advanced: bool = False  # shown only in the Settings page's Advanced mode


SETTINGS: list[Setting] = [
    Setting(
        "display_name",
        "Display name",
        "text",
        "",
        "The name at the top of the Dashboard. Leave it blank to use the profile ID.",
    ),
    Setting(
        "mal_username",
        "MyAnimeList username",
        "text",
        "",
        "Your MyAnimeList username. Your anime list has to be public.",
    ),
    Setting("lastfm_username", "Last.fm username", "text", "", "Your Last.fm username."),
    Setting(
        "timezone",
        "Time zone",
        "timezone",
        "America/Phoenix",
        "Used for the times shown in the app, and to put your plays in the right hour, "
        "day, and month.",
    ),
    Setting(
        "include_nsfw",
        "Include NSFW anime",
        "bool",
        True,
        "Include shows MyAnimeList marks as adult, in your synced list and in suggestions. "
        "Their posters stay hidden on the Dashboard either way.",
    ),
    Setting(
        "lastfm_capture_scope",
        "Which devices scrobble to Last.fm?",
        "choice",
        "unknown",
        "Tells the reports how much of your listening reaches Last.fm. Plays synced from "
        "now on get this label; older plays keep theirs.",
        choices=CAPTURE_SCOPES,
        group="Last.fm",
    ),
    Setting(
        "lastfm_scope_note",
        "Last.fm scope note",
        "text",
        "",
        "Your own wording for the note shown with Last.fm reports. Leave it blank for the "
        "standard note.",
        max_length=500,
        group="Last.fm",
        advanced=True,
    ),
    Setting(
        "genre_min_sample",
        "Genre minimum sample",
        "int",
        5,
        "Genres with fewer of your scored shows than this are left out of the genre charts.",
        minimum=1,
        maximum=1000,
        group="Reports",
        advanced=True,
    ),
    Setting(
        "in_line_threshold",
        "In-line threshold",
        "float",
        0.25,
        "How close to the MAL average, in points, still counts as in line. Used in the "
        "genre report export.",
        minimum=0,
        maximum=9,
        group="Reports",
        advanced=True,
    ),
    Setting(
        "top_n_all_time",
        "Top N, all time",
        "int",
        50,
        "How many artists and tracks the all-time reports keep. The Reports page shows the top 10.",
        minimum=1,
        maximum=1000,
        group="Reports",
        advanced=True,
    ),
    Setting(
        "top_n_per_year",
        "Top N, per year",
        "int",
        25,
        "How many artists and tracks each year keeps in the yearly report exports.",
        minimum=1,
        maximum=1000,
        group="Reports",
        advanced=True,
    ),
    Setting(
        "top_n_per_month",
        "Top N, per month",
        "int",
        10,
        "How many artists and tracks each month keeps in the monthly report exports.",
        minimum=1,
        maximum=1000,
        group="Reports",
        advanced=True,
    ),
    Setting(
        "rec_count",
        "Recommendations per list",
        "int",
        30,
        "How many cards each For You tab shows.",
        minimum=5,
        maximum=200,
        group="Recommendations",
    ),
    Setting(
        "rec_min_raters",
        "Minimum MAL raters for anime suggestions",
        "int",
        5000,
        "Skips shows rated by fewer MyAnimeList users than this, since their scores are "
        "less settled.",
        minimum=0,
        maximum=5_000_000,
        group="Recommendations",
        advanced=True,
    ),
    Setting(
        "rec_media_types",
        "Anime types to suggest",
        "multichoice",
        "tv,movie,ona,ova",
        "Only these kinds of anime are suggested.",
        choices=ANIME_TYPES,
        group="Recommendations",
    ),
    Setting(
        "rec_include_plan_to_watch",
        "Include my Plan to Watch in suggestions",
        "bool",
        True,
        "Also suggest shows already on your Plan to Watch, with a label.",
        group="Recommendations",
    ),
    Setting(
        "rec_seed_artists",
        "Artists to base music suggestions on",
        "int",
        50,
        "How many of your most played artists from the past year music suggestions start "
        "from. Recent plays count more.",
        minimum=5,
        maximum=200,
        group="Recommendations",
        advanced=True,
    ),
    Setting(
        "lastfm_lookback_days",
        "Last.fm lookback days",
        "int",
        14,
        "Each sync looks this many days back for plays that reached Last.fm late.",
        minimum=0,
        maximum=365,
        group="Syncing and storage",
        advanced=True,
    ),
    Setting(
        "raw_retention_days",
        "Keep raw API pages for (days)",
        "int",
        180,
        "How many days to keep copies of the raw API responses. Older copies are cleared "
        "after each sync, except the newest of each and any still in use. 0 keeps them all.",
        minimum=0,
        maximum=3650,
        group="Syncing and storage",
        advanced=True,
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
    if setting.kind == "multichoice":
        return _validate_multichoice(setting, raw)
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


def _validate_multichoice(setting: Setting, raw: Any) -> str:
    """A list or comma-separated text. Stored comma-separated, in the choices' order."""
    parts = raw if isinstance(raw, (list, tuple)) else str(raw).split(",")
    picked = {str(p).strip().lower() for p in parts if str(p).strip()}
    choices = setting.choices or {}
    unknown = sorted(picked - set(choices))
    if unknown:
        raise SettingError(f"Unknown anime type: {', '.join(unknown)}.")
    if not picked:
        raise SettingError(f"{setting.label}: pick at least one.")
    return ",".join(key for key in choices if key in picked)


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
