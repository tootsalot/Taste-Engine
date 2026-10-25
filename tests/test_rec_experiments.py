"""Experimental model changes are opt-in, with paired holdout metrics."""

import json
from datetime import datetime, timezone

import pytest

from taste import recommend, settings
from taste.recommend import experiments
from tests.test_recommend import anime_world  # noqa: F401

NOW = datetime(2026, 10, 14, tzinfo=timezone.utc)


@pytest.mark.usefixtures("anime_world")
def test_off_returns_the_original_model_and_exact_picks(conn):
    assert not settings.get(conn, "rec_dismissal_signal")
    assert not settings.get(conn, "rec_recent_ratings")
    scored, facts = recommend._scored(conn), recommend._anime_facts(conn)
    original = recommend.build_model(scored, facts)
    assert experiments.apply(conn, "anime", original, scored, facts, NOW) is original
    picks, _ = recommend.anime_recommendations(conn)
    assert [r.item_key for r in picks] == ["201", "203", "104", "206"]
    assert picks[0].score == pytest.approx(8.18)
    recommend.dismiss(conn, "anime", "203")
    ordinary = recommend.anime_recommendations(conn)[0]
    assert next(r for r in ordinary if r.item_key == "201").score == pytest.approx(8.18)
    settings.set_value(conn, "rec_dismissal_signal", True)
    changed = recommend.anime_recommendations(conn)[0]
    assert next(r for r in changed if r.item_key == "201").score < 8.18
    assert not any(r.item_key == "203" for r in changed)


def test_recency_weights_recent_scores_and_records_off_on_metrics(conn):
    scored = [(i, 9.0 if i < 21 else 5.0, 7.0) for i in range(1, 41)]
    facts = {i: {"genres": ["Drama"], "studios": ["Studio Alpha"]} for i, _, _ in scored}
    # The learned preference shifted upward in the recent half, with a recent date
    # attached only to those rated items, not to the held-out score itself.
    from taste import core

    for key, score, _ in scored:
        item = core.upsert_item(
            conn,
            source="mal",
            id_type="mal_anime_id",
            external_id=str(key),
            media_type="anime",
            title=f"Example {key}",
        )
        core.upsert_rating(
            conn,
            item_id=item,
            source="mal",
            raw_score=score,
            scale_min=1,
            scale_max=10,
            source_updated_at="2026-01-01 00:00:00" if key < 21 else "2020-01-01 00:00:00",
        )
    base = recommend.build_model(scored, facts)
    on = experiments.apply(conn, "anime", base, scored, facts, NOW, recency=True, dismissal=False)
    assert on.predict(7, ["Drama"], ["Studio Alpha"]) > base.predict(7, ["Drama"], ["Studio Alpha"])
    metrics = experiments.metrics(conn, "anime", scored, facts, NOW)
    for name in ("recency", "dismissal"):
        assert metrics["experiments"][name]["off"]["count"] == 40
        assert metrics["experiments"][name]["on"]["mae"] is not None
    recommend.save_run(conn, "anime", [], metrics)
    saved = json.loads(
        conn.execute(
            "SELECT metrics_json FROM rec_runs ORDER BY rec_run_id DESC LIMIT 1"
        ).fetchone()[0]
    )
    assert saved == metrics
    print(f"Invented experiment holdouts: {saved}")


def test_every_rated_medium_saves_the_paired_checks(conn):
    recommend.compute_all(conn, NOW, ("anime", "film", "tv", "book"))
    for kind in ("anime", "film", "tv", "book"):
        metrics = recommend.latest(conn, kind)[1]
        assert set(metrics["experiments"]) == {"dismissal", "recency"}
        assert metrics["experiments"]["recency"]["on"] == {"count": 0, "mae": None}
