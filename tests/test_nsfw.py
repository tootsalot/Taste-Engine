"""Adult shows (MAL's nsfw "black"): always synced and counted, never shown or suggested
while "Include NSFW anime" is off (the user's call, 2026-10-19). Made-up data only."""

import csv

import pytest

from taste import activity, animestats, overview, recap, recommend, reports, settings
from taste.activity import Period
from taste.config import MalSettings
from taste.sources import mal
from tests.conftest import FAKE_MAL_CLIENT_ID
from tests.fake_recs import FakeMal
from tests.test_recap import OCT_14, entry, show

ADULT = 302


def adult_list():
    adult = show(ADULT, "Example Adult Drama", 7.0, ["Drama"])
    adult["nsfw"] = "black"
    plain = show(301, "Example Plain Drama", 7.4, ["Drama"])
    return [
        entry(plain, "completed", 7, "2026-09-10"),
        entry(adult, "completed", 10, "2026-09-20"),  # my favorite of the month
        entry(show(303, "Example Dropped", 7.0, ["Action"]), "dropped", 4, episodes=3),
    ]


@pytest.fixture
def world(conn, make_client):
    fake = FakeMal(entries=adult_list(), details={})
    client, _ = make_client(fake)

    def sync():
        return mal.sync(conn, client, MalSettings(FAKE_MAL_CLIENT_ID, "example_user"),
                        out=lambda m: None)  # fmt: skip

    sync()
    return {"conn": conn, "fake": fake, "sync": sync}


def hide(conn):
    settings.set_value(conn, "include_nsfw", False)


def test_the_sync_always_asks_for_adult_shows_and_never_drops_them(world):
    conn = world["conn"]
    hide(conn)
    world["sync"]()
    assert all(p.get("nsfw") == "true" for p in world["fake"].list_params)
    assert (
        conn.execute(
            "SELECT removed_at FROM stg_mal_list_entries WHERE mal_anime_id = ?", (ADULT,)
        ).fetchone()[0]
        is None
    )
    assert conn.execute("SELECT COUNT(*) FROM core_ratings WHERE source = 'mal'").fetchone()[0] == 3


def test_hidden_shows_count_but_never_show(world, tmp_path):
    conn = world["conn"]
    sept = Period("month", "2026-09")
    hide(conn)
    finished = activity.finished_shows(conn, sept.start, sept.end)
    assert len(finished) == 2 and [f.hidden for f in finished] == [False, True]

    data = recap.build(conn, sept, OCT_14)
    assert data.anime.finished == 2 and data.anime.scored == 2  # counted
    assert [p.title for p in data.anime.posters] == ["Example Plain Drama"]  # not shown
    assert data.anime.best.title == "Example Plain Drama"
    assert "Adult" not in data.closing and "Adult" not in repr(data.year)
    year = recap.build(conn, Period("year", "2026"), OCT_14).year
    assert [s.title for s in year.top_shows] == ["Example Plain Drama"]
    assert year.first_finish.title == "Example Plain Drama"
    assert year.last_finish is None  # the real last finish is hidden: not a wrong one

    glance = activity.glance(conn, sept)
    assert glance.shows_finished == 2 and glance.top_show.title == "Example Plain Drama"
    assert [s.title for s in overview.recently_finished(conn)] == ["Example Plain Drama"]

    stats = animestats.load(conn)
    assert len(stats.finished) == 2  # the tiles count it
    assert [f.title for f in stats.shown] == ["Example Plain Drama"]  # the wall doesn't show it

    reports.write_csvs(conn, tmp_path)
    for name in ("mal_score_vs_community.csv", "mal_dropped_on_hold.csv"):
        text = (tmp_path / name).read_text(encoding="utf-8-sig")
        assert "Adult" not in text
    rows = list(csv.reader((tmp_path / "mal_score_vs_community.csv").open(encoding="utf-8-sig")))
    assert len(rows) == 1 + 2  # header, and the two shows that aren't hidden

    # Switched back on, everything shows again.
    settings.set_value(conn, "include_nsfw", True)
    data = recap.build(conn, sept, OCT_14)
    assert data.anime.best.title == "Example Adult Drama"
    assert len(overview.recently_finished(conn)) == 2


def test_suggestions_never_name_a_hidden_show(conn, make_client):
    from tests.test_recommend import run_enrich_mal, sync_mal

    fake = FakeMal()
    sync_mal(conn, make_client, fake)
    run_enrich_mal(conn, make_client, fake)
    conn.execute("UPDATE stg_mal_anime SET nsfw_rating = 'black' WHERE mal_anime_id = 101")
    hide(conn)
    recs = {r.item_key: r for r in recommend.anime_recommendations(conn)[0]}
    for rec in recs.values():
        text = " ".join(rec.reasons + rec.facts.get("details", []))
        assert "Example Drama A" not in text, rec.title
    assert recs["201"].reasons[0].startswith("Because you really liked Example Action F")
