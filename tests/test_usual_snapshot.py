"""Single lists retain the captures apart from 4.0's anime film routing.

The fixture's base_sha records the original capture. Only its film and everything
hashes first changed when Japanese animation stopped appearing in film suggestions.
The mixed hashes now capture 4.0's relative scores, balancing, and surprise card.
"""

import hashlib
import json
from dataclasses import asdict
from pathlib import Path

import pytest

from taste import recommend, settings
from tests.fill_world import NOW_DT
from tests.model_world import fill_model_library

MODEL_SETTINGS = ("rec_model_anime", "rec_model_film", "rec_model_tv", "rec_model_book")
GOLDEN = Path(__file__).parent / "fixtures" / "rec_usual_before31.json"


def _canonical_hash(run):
    payload = json.dumps(run, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _captured_run(conn, kind):
    recs, metrics, _ = recommend.latest(conn, kind)
    row = conn.execute(
        "SELECT params_json FROM rec_runs WHERE kind = ? ORDER BY rec_run_id DESC LIMIT 1",
        (kind,),
    ).fetchone()
    params = json.loads(row[0])
    assert {key: params.pop(key) for key in MODEL_SETTINGS} == dict.fromkeys(
        MODEL_SETTINGS, "usual"
    )
    return {"recs": [asdict(rec) for rec in recs], "metrics": metrics, "params": params}


def _verify_cases(conn, golden):
    assert golden["base_sha"] == "696c7d7dbbf4944c108c548badd2ea1e5b799550"
    assert golden["library_recipe"]["now"] == NOW_DT.isoformat()
    for name, case in golden["cases"].items():
        conn.execute("SAVEPOINT usual_before31")
        try:
            for key, value in case["settings"].items():
                settings.set_value(conn, key, value)
            for key in case["adult_anime_ids"]:
                conn.execute(
                    "UPDATE stg_mal_anime SET nsfw_rating = 'black' WHERE mal_anime_id = ?",
                    (key,),
                )
            for kind, key in case["dismissals"]:
                conn.execute(
                    "INSERT INTO rec_dismissed (kind, item_key, dismissed_at) VALUES (?, ?, ?) "
                    "ON CONFLICT (kind, item_key) DO NOTHING",
                    (kind, key, "2026-10-14 18:00:00"),
                )
            recommend.compute_all(
                conn, NOW_DT, ("anime", "music", "film", "tv", "book", "everything")
            )
            assert set(case["runs"]) == set(recommend.LIST_KINDS)
            for kind, expected in case["runs"].items():
                actual = _captured_run(conn, kind)
                assert _canonical_hash(actual) == expected, f"{name}/{kind}"
        finally:
            conn.execute("ROLLBACK TO usual_before31")
            conn.execute("RELEASE usual_before31")


@pytest.fixture
def full_usual_library(conn, tmp_path, monkeypatch):
    return fill_model_library(conn, tmp_path, monkeypatch)


def test_usual_saved_runs_match_before31_in_full(full_usual_library):
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    _verify_cases(full_usual_library, golden)
