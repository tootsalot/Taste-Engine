"""Fit a selected model and calibrate it on the lab's shared held-out folds."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from statistics import mean
from typing import Any

from taste import settings
from taste.recommend import candidates
from taste.recommend.candidates import Facts, Rows
from taste.recommend.model import holdout_residuals


@dataclass(frozen=True)
class SelectedModel:
    predict: Callable[[int], float]
    residuals: list[float]
    metrics: dict[str, Any]
    detail: str


def fit_selected(
    conn: sqlite3.Connection,
    medium: str,
    name: str,
    rows: Rows,
    facts: Facts,
    crowd: dict[int, float],
) -> SelectedModel:
    """Fit once for candidate titles, with separate training-only fits for calibration.

    Usual keeps the existing list path. These three models do not use dates, so
    their context has a fixed reference. Errors stay on the 1 to 10 model scale,
    including doubled book stars, and compare with plain Usual on the same folds.
    """
    fit = {
        "details": candidates.fit_details,
        "similar": candidates.fit_similar,
        "blend": candidates.fit_blend,
    }[name]
    context = candidates.Context(conn, medium, crowd, date(1970, 1, 1))
    predict = fit(rows, facts, context)
    held_out = candidates.holdout_predictions(rows, facts, context, fit)
    residuals = sorted(mine - guess for _, mine, guess in held_out)
    usual = holdout_residuals(rows, facts)
    metrics = {
        "name": name,
        "count": len(rows),
        "mae": round(mean(abs(value) for value in residuals), 3) if residuals else None,
        "usual_mae": round(mean(abs(value) for value in usual), 3) if usual else None,
    }
    return SelectedModel(
        predict, residuals, metrics, f"Predicted by {settings.REC_MODELS[name]}, experimental."
    )
