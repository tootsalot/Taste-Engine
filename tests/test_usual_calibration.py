"""Ordinary list refreshes keep the chooser's Usual accuracy ready to read."""

import json

from taste import recommend
from taste.recommend import candidates, lab
from tests.fill_world import NOW_DT
from tests.model_world import fill_model_library


def test_usual_refreshes_cache_the_same_folds_without_changing_saved_runs(
    conn, tmp_path, monkeypatch
):
    fill_model_library(conn, tmp_path, monkeypatch)
    conn.execute("DELETE FROM rec_model_calibrations")
    usual = lab.Candidate("usual", candidates.fit_usual)
    expected = {}
    for medium in lab.MEDIA:
        rows, facts, context = lab.load_data(conn, medium)
        expected[medium] = lab.holdout_predictions(rows, facts, context, usual)
    recommend.compute_all(conn, NOW_DT, lab.MEDIA)
    before = {}
    for medium in lab.MEDIA:
        row = conn.execute(
            "SELECT predictions_json FROM rec_model_calibrations "
            "WHERE medium = ? AND model_name = 'usual'",
            (medium,),
        ).fetchone()
        assert row is not None
        saved = json.loads(row[0])
        assert saved["selected"] == [list(value) for value in expected[medium]]
        assert saved["usual"] == saved["selected"]
        assert saved["summary"]["count"] == len(expected[medium])
        before[medium] = recommend.latest(conn, medium)[:2]

    def refuse(*args, **kwargs):
        raise AssertionError("An unchanged Usual refresh must reuse its held-out checks")

    monkeypatch.setattr(candidates, "holdout_predictions", refuse)
    recommend.compute_all(conn, NOW_DT, lab.MEDIA)
    for medium in lab.MEDIA:
        assert recommend.latest(conn, medium)[:2] == before[medium]
