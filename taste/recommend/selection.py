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

from taste import recommend, settings
from taste.db import utc_now
from taste.recommend import candidates
from taste.recommend.candidates import Facts, Rows
from taste.recommend.model import MIN_RESIDUALS, whole_score


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
    payload = [recommend.MODEL_SET_VERSION, medium, name, rows, metadata, links]
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _read_predictions(payload: str, rows: Rows) -> dict[str, list] | None:
    """A damaged cache is disposable; it must never supply invalid accuracy data."""
    try:
        result = json.loads(payload)
    except (TypeError, ValueError, RecursionError):
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
            if type(value[0]) is not int or isinstance(value[1], bool):
                return None
            guess = value[2]
            if isinstance(guess, bool) or not isinstance(guess, (float, int)):
                return None
            if not 1 <= guess <= 10:
                return None
    return result


def summary(rows: Rows, selected: list, usual: list) -> dict[str, Any]:
    """Describe the lab's predictions without changing the model's score scale."""
    from taste.recommend import lab

    result = dict.fromkeys(("within_one", "top_hits", "verdict", "interval"))
    result["count"] = len(rows)
    if not selected:
        return result
    errors = [abs(mine - guess) for _, mine, guess in selected]
    usual_errors = [abs(mine - guess) for _, mine, guess in usual]
    _, interval = lab.paired_bootstrap(errors, usual_errors)
    result.update(
        within_one=sum(error <= 1 for error in errors) / len(errors),
        top_hits=lab.top_hits(selected),
        verdict="better" if interval[1] < 0 else "worse" if interval[0] > 0 else "same",
        interval=list(interval),
    )
    return result


def _summary_fingerprint(value: dict, predictions: dict) -> str:
    encoded = json.dumps(
        [predictions["selected"], predictions["usual"], value],
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _valid_summary(value: Any, rows: Rows, predictions: dict) -> bool:
    from taste.recommend import lab

    if not isinstance(value, dict) or type(value.get("count")) is not int:
        return False
    if set(value) != {"count", "within_one", "top_hits", "verdict", "interval"}:
        return False
    if value["count"] != len(rows):
        return False
    if len(rows) < MIN_RESIDUALS:
        empty = all(
            value.get(key) is None for key in ("within_one", "top_hits", "verdict", "interval")
        )
        return empty and predictions.get("summary_fingerprint") == _summary_fingerprint(
            value, predictions
        )
    for key in ("within_one", "top_hits"):
        fraction = value.get(key)
        if isinstance(fraction, bool) or not isinstance(fraction, (float, int)):
            return False
        if not 0 <= fraction <= 1:
            return False
    selected = predictions["selected"]
    within = sum(abs(mine - guess) <= 1 for _, mine, guess in selected) / len(selected)
    if value["within_one"] != within or value["top_hits"] != lab.top_hits(selected):
        return False
    interval = value.get("interval")
    if not isinstance(interval, (tuple, list)) or len(interval) != 2:
        return False
    for endpoint in interval:
        if isinstance(endpoint, bool) or not isinstance(endpoint, (float, int)):
            return False
        if not -9 <= endpoint <= 9:
            return False
    low, high = interval
    if low > high:
        return False
    expected = "better" if high < 0 else "worse" if low > 0 else "same"
    return value.get("verdict") == expected and predictions.get(
        "summary_fingerprint"
    ) == _summary_fingerprint(value, predictions)


def saved_calibration(
    conn: sqlite3.Connection, medium: str, name: str, rows: Rows, facts: Facts
) -> dict | None:
    """Read matching predictions only. Invalid or old summaries need a worker to rebuild."""
    fingerprint = _fingerprint(conn, medium, name, rows, facts)
    cached = conn.execute(
        "SELECT predictions_json FROM rec_model_calibrations "
        "WHERE medium = ? AND model_name = ? AND fingerprint = ?",
        (medium, name, fingerprint),
    ).fetchone()
    predictions = _read_predictions(cached[0], rows) if cached is not None else None
    if predictions is not None and not _valid_summary(
        predictions.get("summary"), rows, predictions
    ):
        predictions["summary"] = None
    return predictions


def calibration(conn: sqlite3.Connection, medium: str, name: str, rows: Rows, facts: Facts) -> dict:
    """Reuse held-out predictions and their summary, without fitting candidate titles.

    Usual has its own cache entry. Other models reuse that baseline when available;
    older caches can gain plain accuracy words without refitting their predictions.
    """
    fit = {
        "usual": candidates.fit_usual,
        "details": candidates.fit_details,
        "similar": candidates.fit_similar,
        "blend": candidates.fit_blend,
    }[name]
    predictions = saved_calibration(conn, medium, name, rows, facts)
    if predictions is not None and predictions.get("summary") is not None:
        return predictions
    if predictions is None:
        context = candidates.Context(
            conn, medium, {key: crowd for key, _, crowd in rows}, date(1970, 1, 1)
        )
        if name == "usual":
            usual = candidates.holdout_predictions(rows, facts, context, fit)
            selected = usual
        else:
            selected = candidates.holdout_predictions(rows, facts, context, fit)
            baseline = saved_calibration(conn, medium, "usual", rows, facts)
            usual = (
                baseline["selected"]
                if baseline is not None
                else candidates.holdout_predictions(rows, facts, context, candidates.fit_usual)
            )
        predictions = {
            "selected": [list(row) for row in selected],
            "usual": [list(row) for row in usual],
        }
    predictions["summary"] = summary(rows, predictions["selected"], predictions["usual"])
    predictions["summary_fingerprint"] = _summary_fingerprint(predictions["summary"], predictions)
    conn.execute(
        "INSERT INTO rec_model_calibrations "
        "(medium, model_name, fingerprint, predictions_json, updated_at) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT (medium, model_name) DO UPDATE SET fingerprint = excluded.fingerprint, "
        "predictions_json = excluded.predictions_json, updated_at = excluded.updated_at",
        (
            medium,
            name,
            _fingerprint(conn, medium, name, rows, facts),
            json.dumps(predictions),
            utc_now(),
        ),
    )
    return predictions


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
    """Fit candidate predictions and reuse the separate five-fold calibration."""
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
    predictions = calibration(conn, medium, name, rows, facts)
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
