"""Fit a selected model and reuse unchanged training-only calibration."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from statistics import mean
from typing import Any

from taste import settings
from taste.db import utc_now
from taste.recommend import candidates
from taste.recommend.candidates import Facts, Rows
from taste.recommend.model import MIN_RESIDUALS, whole_score

# Bump when a fitting rule changes so older calibration cannot survive new code.
CALIBRATION_VERSION = 1


@dataclass(frozen=True)
class SelectedModel:
    predict: Callable[[int], float]
    residuals: list[float]
    metrics: dict[str, Any]
    detail: str


def _fingerprint(conn: sqlite3.Connection, medium: str, name: str, rows: Rows, facts: Facts) -> str:
    rated = {key for key, _, _ in rows}
    metadata = {}
    for key in sorted(rated):
        fact = facts.get(key, {})
        values = {field: fact.get(field, []) for field in ("genres", "studios")}
        if name in ("details", "blend"):
            categories, numeric = candidates.detail_features(fact, medium)
            values["details"] = [sorted(categories), numeric]
        if medium == "book" and name in ("similar", "blend"):
            values["links"] = sorted(
                target
                for target in json.loads(fact.get("similar_json") or "[]")
                if isinstance(target, int) and target in rated
            )
        metadata[key] = values
    links = []
    if medium == "anime" and name in ("similar", "blend"):
        links = sorted(
            tuple(row)
            for row in conn.execute(
                "SELECT mal_anime_id, recommended_id, num_recommendations "
                "FROM stg_mal_anime_recommendations WHERE num_recommendations > 0"
            )
            if row[0] in rated and row[1] in rated
        )
    payload = [CALIBRATION_VERSION, medium, name, rows, metadata, links]
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _read_predictions(payload: str, rows: Rows) -> dict[str, list] | None:
    """A damaged cache is disposable; it must never supply invalid accuracy data."""
    try:
        result = json.loads(payload)
    except (TypeError, ValueError):
        return None
    expected = [row for fold in range(5) for row in rows if row[0] % 5 == fold]
    if len(rows) < MIN_RESIDUALS:
        expected = []
    if not isinstance(result, dict):
        return None
    for name in ("selected", "usual"):
        predictions = result.get(name)
        if not isinstance(predictions, list) or len(predictions) != len(expected):
            return None
        for value, (key, mine, _) in zip(predictions, expected, strict=True):
            if not isinstance(value, list) or len(value) != 3 or value[:2] != [key, mine]:
                return None
            guess = value[2]
            if isinstance(guess, bool) or not isinstance(guess, (float, int)):
                return None
            if not 1 <= guess <= 10:
                return None
    return result


def fit_selected(
    conn: sqlite3.Connection,
    medium: str,
    name: str,
    rows: Rows,
    facts: Facts,
    crowd: dict[int, float],
    *,
    prediction_ids: Sequence[int] | None = None,
) -> SelectedModel:
    """Fit candidate predictions and cache the separate five-fold calibration.

    Only rated metadata and links between rated titles affect calibration. New
    suggestions still get a fresh full-data fit, without repeating held-out fits.
    The lab remains read-only and uses the same uncached fold implementation.
    """
    fit = {
        "details": candidates.fit_details,
        "similar": candidates.fit_similar,
        "blend": candidates.fit_blend,
    }[name]
    context = candidates.Context(
        conn,
        medium,
        crowd,
        date(1970, 1, 1),
        prediction_ids=None if prediction_ids is None else tuple(prediction_ids),
    )
    predict = fit(rows, facts, context)
    fingerprint = _fingerprint(conn, medium, name, rows, facts)
    cached = conn.execute(
        "SELECT predictions_json FROM rec_model_calibrations "
        "WHERE medium = ? AND model_name = ? AND fingerprint = ?",
        (medium, name, fingerprint),
    ).fetchone()
    predictions = _read_predictions(cached[0], rows) if cached is not None else None
    if predictions is None:
        predictions = {
            "selected": candidates.holdout_predictions(rows, facts, context, fit),
            "usual": candidates.holdout_predictions(rows, facts, context, candidates.fit_usual),
        }
        conn.execute(
            "INSERT INTO rec_model_calibrations "
            "(medium, model_name, fingerprint, predictions_json, updated_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT (medium, model_name) DO UPDATE SET fingerprint = excluded.fingerprint, "
            "predictions_json = excluded.predictions_json, updated_at = excluded.updated_at",
            (medium, name, fingerprint, json.dumps(predictions), utc_now()),
        )
    held_out, usual = predictions["selected"], predictions["usual"]
    residuals = sorted(mine - guess for _, mine, guess in held_out)
    metrics = {
        "name": name,
        "count": len(rows),
        "mae": round(mean(abs(value) for value in residuals), 3) if residuals else None,
        "usual_mae": round(mean(abs(mine - guess) for _, mine, guess in usual), 3)
        if usual
        else None,
    }
    if medium == "anime":
        for key, values in (("whole_mae", held_out), ("usual_whole_mae", usual)):
            metrics[key] = (
                round(mean(abs(mine - whole_score(guess)) for _, mine, guess in values), 3)
                if values
                else None
            )
    return SelectedModel(
        predict, residuals, metrics, f"Predicted by {settings.REC_MODELS[name]}, experimental."
    )
