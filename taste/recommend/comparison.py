"""Plain model comparisons from cached, training-only held-out predictions."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from contextlib import nullcontext
from typing import Any

from taste import db, recommend, settings
from taste.recommend import lab, selection
from taste.recommend.model import MIN_RESIDUALS

ASKED_KEY = "rec_model_asked"
VERDICT_TEXT = {
    "better": "a little better than Usual",
    "same": "about the same as Usual",
    "worse": "a little worse than Usual",
}


def asked_version(conn: sqlite3.Connection) -> int:
    """Read the answered model set for this profile; damaged state asks again."""
    row = conn.execute(
        "SELECT state_value FROM app_state WHERE state_key = ?", (ASKED_KEY,)
    ).fetchone()
    try:
        value = int(row[0]) if row else 0
    except (TypeError, ValueError, OverflowError):
        return 0
    return max(value, 0)


def mark_asked(conn: sqlite3.Connection, version: int | None = None) -> None:
    """Remember the version that was answered, including a small profile's silent skip."""
    version = recommend.MODEL_SET_VERSION if version is None else version
    if type(version) is not int or version < 0:
        raise ValueError("The model set version must be a non-negative whole number.")
    conn.execute(
        "INSERT INTO app_state (state_key, state_value, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT (state_key) DO UPDATE SET state_value = excluded.state_value, "
        "updated_at = excluded.updated_at",
        (ASKED_KEY, str(version), db.utc_now()),
    )


def apply_choice(conn: sqlite3.Connection, name: str, version: int | None = None) -> set[str]:
    """Set all rated lists together and return the settings that need recomputing."""
    version = recommend.MODEL_SET_VERSION if version is None else version
    if type(version) is not int or version < 0:
        raise ValueError("The model set version must be a non-negative whole number.")
    keys = [f"rec_model_{medium}" for medium in lab.MEDIA]
    for key in keys:
        settings.validate(key, name)
    changed = {key for key in keys if settings.get(conn, key) != name}
    with nullcontext(conn) if conn.in_transaction else db.transaction(conn):
        for key in keys:
            if key in changed:
                settings.set_value(conn, key, name)
        mark_asked(conn, version)
    return changed


def saved_accuracy(conn: sqlite3.Connection, medium: str, model: str) -> dict | None:
    """Read fresh accuracy without fitting, bootstrapping, or writing on the UI thread."""
    if model not in settings.REC_MODELS:
        return None
    try:
        rows, facts, _ = lab.load_data(conn, medium)
        if len(rows) < MIN_RESIDUALS:
            return selection.summary(rows, [], [])
        saved = selection.saved_calibration(conn, medium, model, rows, facts)
    except (ValueError, TypeError, AttributeError, RecursionError):
        return None
    return saved.get("summary") if saved is not None else None


def check_models(
    conn: sqlite3.Connection,
    cancelled: Callable[[], bool] | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any] | None:
    """Compare eligible media, with weighted summaries and a paired pooled verdict.

    A medium needs twenty ratings. Its count remains visible below that threshold,
    while aggregate accuracy excludes it. No candidate title is fitted here.
    """
    data = {}
    for medium in lab.MEDIA:
        if cancelled is not None and cancelled():
            return None
        rows, facts, _ = lab.load_data(conn, medium)
        data[medium] = (rows, facts)
    count = sum(len(rows) for rows, _ in data.values() if len(rows) >= MIN_RESIDUALS)
    if count and progress is not None:
        progress("Checking each model against your ratings")
    models = {}
    for name in settings.REC_MODELS:
        media = {}
        errors, baseline_errors = [], []
        for medium, (rows, facts) in data.items():
            if cancelled is not None and cancelled():
                return None
            if len(rows) < MIN_RESIDUALS:
                media[medium] = selection.summary(rows, [], [])
                continue
            saved = selection.calibration(conn, medium, name, rows, facts)
            media[medium] = saved["summary"]
            errors.extend(abs(mine - guess) for _, mine, guess in saved["selected"])
            baseline_errors.extend(abs(mine - guess) for _, mine, guess in saved["usual"])
        combined = selection.summary([], [], [])
        combined["count"] = count
        if count:
            for key in ("within_one", "top_hits"):
                combined[key] = (
                    sum(
                        block["count"] * block[key]
                        for block in media.values()
                        if block[key] is not None
                    )
                    / count
                )
            _, interval = lab.paired_bootstrap(errors, baseline_errors)
            combined["interval"] = list(interval)
            combined["verdict"] = (
                "better" if interval[1] < 0 else "worse" if interval[0] > 0 else "same"
            )
        models[name] = {**combined, "media": media}
    if cancelled is not None and cancelled():
        return None
    return {"count": count, "models": models}


def accuracy_text(kind: str, block: dict) -> str:
    """Use the same plain accuracy words in the question and on a saved list."""
    within, top = block.get("within_one"), block.get("top_hits")
    if block.get("count", 0) < MIN_RESIDUALS or within is None or top is None:
        return "Not enough ratings to check yet"
    scale = "half a star" if kind == "book" else "1 point"
    extra = " (half a star for books)" if kind == "all" else ""
    return (
        f"Guessed within {scale} of your score{extra} {round(100 * within)}% of the time. "
        f"Of the titles you rated highest, it ranked {round(10 * top)} in 10 near the top."
    )
