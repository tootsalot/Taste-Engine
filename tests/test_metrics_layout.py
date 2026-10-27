"""Saved metrics keep a number, never a model block, under "model".

3.0.0 crashed at launch when a chosen model's block was saved under "model", where a
Usual anime list keeps its numeric error.
"""

import json

from taste import recommend, settings
from taste.recommend import lab
from tests.fill_world import NOW_DT
from tests.model_world import fill_model_library


def compute_with_every_model(conn):
    for name in settings.REC_MODELS:
        for medium in lab.MEDIA:
            settings.set_value(conn, f"rec_model_{medium}", name)
        recommend.compute_all(conn, NOW_DT, ("anime", "music", "film", "tv", "book", "everything"))
    saved = conn.execute("SELECT kind, metrics_json FROM rec_runs").fetchall()
    assert {kind for kind, _ in saved} == set(recommend.LIST_KINDS)
    for kind, text in saved:
        model = json.loads(text).get("model")
        assert model is None or type(model) in (int, float), (kind, model)


def test_every_list_with_every_model_keeps_a_number_under_model(conn, tmp_path, monkeypatch):
    fill_model_library(conn, tmp_path, monkeypatch)
    compute_with_every_model(conn)


def test_every_list_with_every_model_computes_on_an_empty_profile(conn):
    compute_with_every_model(conn)
