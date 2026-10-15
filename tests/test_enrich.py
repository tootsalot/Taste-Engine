"""Enrichment: MAL details and Last.fm similar artists, cached and refreshed every 30 days."""

import math
from datetime import datetime, timedelta, timezone

import pytest

from taste import enrich
from taste.config import LastfmSettings, MalSettings
from taste.sources import lastfm, mal
from tests.conftest import FAKE_LASTFM_KEY, FAKE_MAL_CLIENT_ID, FakeResponse
from tests.fake_recs import (
    NOW,
    FakeLastfmApi,
    FakeMal,
    anime,
    list_entry,
    listening_history,
    scrobble,
)
from tests.test_lastfm_sync import FakeLastfm

MAL_SETTINGS = MalSettings(client_id=FAKE_MAL_CLIENT_ID, username="example_user")
LASTFM_SETTINGS = LastfmSettings(api_key=FAKE_LASTFM_KEY, username="example_user")
NOW_DT = datetime.fromtimestamp(NOW, timezone.utc)
quiet = lambda message: None  # noqa: E731


def sync_mal(conn, make_client, fake):
    client, _ = make_client(fake)
    assert mal.sync(conn, client, MAL_SETTINGS, out=quiet).status == "success"


def run_enrich_mal(conn, make_client, fake, now=NOW_DT, **kwargs):
    client, _ = make_client(fake)
    fake.detail_calls.clear()
    count = enrich.enrich_mal(conn, client, MAL_SETTINGS, out=quiet, now=now, **kwargs)
    return count, list(fake.detail_calls)


@pytest.fixture
def mal_world(conn, make_client):
    fake = FakeMal()
    sync_mal(conn, make_client, fake)
    return fake


# ---------------------------------------------------------------------------
# MAL
# ---------------------------------------------------------------------------


def test_seeds_are_shows_scored_above_my_average(conn, mal_world):
    assert enrich.my_mean_score(conn) == pytest.approx(7.25)
    assert enrich.seed_ids(conn) == [101, 106]


def test_first_run_fetches_seeds_then_candidates_by_support(conn, make_client, mal_world):
    count, calls = run_enrich_mal(conn, make_client, mal_world)
    # Seeds first, then candidates strongest first. 204 is a recap (not suggested),
    # and 102, 104, 105 are already on the list.
    assert calls == [101, 106, 201, 202, 203, 205, 206]
    assert count == 7

    recs = conn.execute(
        "SELECT recommended_id, num_recommendations FROM stg_mal_anime_recommendations "
        "WHERE mal_anime_id = 101 ORDER BY recommended_id"
    ).fetchall()
    assert [tuple(r) for r in recs] == [(104, 4), (105, 3), (201, 20), (202, 5)]
    related = conn.execute(
        "SELECT related_id, relation_type FROM stg_mal_related_anime WHERE mal_anime_id = 101 "
        "ORDER BY related_id"
    ).fetchall()
    assert [tuple(r) for r in related] == [(102, "prequel"), (203, "sequel"), (204, "summary")]

    # Candidates land in staging with their facts, and in core with a poster.
    row = conn.execute("SELECT * FROM stg_mal_anime WHERE mal_anime_id = 201").fetchone()
    assert row["community_mean"] == 8.2
    assert row["main_picture_url"] == "https://cdn.myanimelist.net/images/anime/1/201.jpg"
    poster = conn.execute(
        "SELECT i.url FROM core_item_images i JOIN core_item_external_ids x "
        "ON x.item_id = i.item_id WHERE x.id_type = 'mal_anime_id' AND x.external_id = '201'"
    ).fetchone()
    assert poster[0].endswith("/201.jpg")

    # Similarity links in core: 101 -> 201 with 20 votes, 101 -> 203 as a sequel.
    links = dict(
        conn.execute(
            "SELECT x2.external_id || ':' || s.kind, s.score FROM core_item_similarity s "
            "JOIN core_item_external_ids x1 ON x1.item_id = s.item_id "
            "JOIN core_item_external_ids x2 ON x2.item_id = s.similar_item_id "
            "WHERE x1.external_id = '101'"
        ).fetchall()
    )
    assert links["201:user_recommended"] == 20
    assert links["203:related_sequel"] == 1.0
    # A show that's only mentioned (never fetched) still gets a minimal core item.
    assert (
        conn.execute(
            "SELECT title FROM core_items i JOIN core_item_external_ids x ON x.item_id = i.item_id "
            "WHERE x.external_id = '204'"
        ).fetchone()[0]
        == "Example Drama A Recap"
    )

    # Every response is in the raw layer, and the run is logged as an enrich run.
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM raw_api_pages WHERE endpoint = '/anime/{id}'"
        ).fetchone()[0]
        == 7
    )
    run = conn.execute(
        "SELECT mode, status, pages_fetched FROM sync_runs ORDER BY sync_run_id DESC LIMIT 1"
    ).fetchone()
    assert tuple(run) == ("enrich", "success", 7)


def test_candidate_support_weights(conn, make_client, mal_world):
    run_enrich_mal(conn, make_client, mal_world)
    support = enrich.candidate_support(conn)
    # (my score - my average) * log(1 + users recommending), summed over shows I liked.
    assert support[201] == pytest.approx(1.75 * math.log(21) + 0.75 * math.log(8))
    assert support[202] == pytest.approx(1.75 * math.log(6))
    assert support[203] == pytest.approx(1.75)  # a sequel adds (my score - my average)
    assert 204 not in support  # recaps aren't suggested
    assert not {102, 104, 105} & set(support)  # already on my list


def test_second_run_is_cached(conn, make_client, mal_world):
    run_enrich_mal(conn, make_client, mal_world)
    count, calls = run_enrich_mal(conn, make_client, mal_world, now=NOW_DT + timedelta(days=29))
    assert (count, calls) == (0, [])


def test_refresh_after_30_days(conn, make_client, mal_world):
    run_enrich_mal(conn, make_client, mal_world)
    later = NOW_DT + timedelta(days=31)
    _, calls = run_enrich_mal(conn, make_client, mal_world, now=later)
    assert sorted(calls) == [101, 106, 201, 202, 203, 205, 206]
    fetched = {r[0] for r in conn.execute("SELECT fetched_at FROM stg_mal_anime_details")}
    assert fetched == {later.strftime("%Y-%m-%d %H:%M:%S")}


def test_refresh_slice_spreads_stale_entries_over_a_month():
    assert enrich.refresh_slice(5) == 10  # small lists refresh in one go
    assert enrich.refresh_slice(300) == 10
    assert enrich.refresh_slice(436) == 15


def test_only_a_slice_of_stale_entries_refreshes_per_run(conn, make_client):
    # 30 shows I liked and 10 I didn't, all fetched in August, so all stale by October.
    entries = [
        list_entry(
            anime(i, f"Example Show {i}", 7.0, ["Drama"], []), "completed", 9 if i <= 30 else 1
        )
        for i in range(1, 41)
    ]
    fake = FakeMal(entries=entries, details={})
    sync_mal(conn, make_client, fake)
    for i in range(1, 31):
        conn.execute(
            "INSERT INTO stg_mal_anime_details (mal_anime_id, fetched_at) VALUES (?, ?)",
            (31 - i, f"2026-08-{i:02d} 00:00:00"),  # show 30 is the stalest
        )
    _, calls = run_enrich_mal(conn, make_client, fake)
    # refresh_slice(30) = 10 per run, stalest first.
    assert calls == list(range(30, 20, -1))


def test_missing_show_is_noted_and_not_asked_again(conn, make_client):
    fake = FakeMal(missing={202})
    sync_mal(conn, make_client, fake)
    _, calls = run_enrich_mal(conn, make_client, fake)
    assert 202 in calls
    assert (
        conn.execute("SELECT COUNT(*) FROM stg_mal_anime WHERE mal_anime_id = 202").fetchone()[0]
        == 0
    )
    _, calls = run_enrich_mal(conn, make_client, fake, now=NOW_DT + timedelta(days=1))
    assert calls == []


def test_interrupted_run_resumes(conn, make_client):
    fake = FakeMal()
    sync_mal(conn, make_client, fake)
    outage = {"on": True}

    def flaky(url, params):
        if outage["on"] and url.endswith("/anime/202"):
            return FakeResponse(503, {})
        return fake(url, params)

    client, _ = make_client(flaky)
    messages = []
    assert enrich.enrich_mal(conn, client, MAL_SETTINGS, out=messages.append, now=NOW_DT) == 0
    assert "will continue next time" in messages[-1]
    done = {r[0] for r in conn.execute("SELECT mal_anime_id FROM stg_mal_anime_details")}
    assert done == {101, 106, 201}  # each item committed before the outage
    assert (
        conn.execute("SELECT status FROM sync_runs ORDER BY sync_run_id DESC LIMIT 1").fetchone()[0]
        == "failed"
    )

    outage["on"] = False
    _, calls = run_enrich_mal(conn, make_client, fake)
    assert calls == [202, 203, 205, 206]


def test_candidate_limit(conn, make_client, mal_world):
    _, calls = run_enrich_mal(conn, make_client, mal_world, candidate_limit=2)
    assert calls == [101, 106, 201, 202]


# ---------------------------------------------------------------------------
# Last.fm
# ---------------------------------------------------------------------------


@pytest.fixture
def music(conn, make_client):
    recent = FakeLastfm()
    recent.scrobbles = listening_history()
    api = FakeLastfmApi(recent)
    client, _ = make_client(api)
    result = lastfm.sync(conn, client, LASTFM_SETTINGS, out=quiet, now=lambda: NOW + 1)
    assert result.status == "success"
    return api


def run_enrich_lastfm(conn, make_client, api, now=NOW_DT):
    client, _ = make_client(api)
    api.calls.clear()
    count = enrich.enrich_lastfm(conn, client, LASTFM_SETTINGS, out=quiet, now=now)
    return count, list(api.calls)


def test_artist_weights_halve_every_90_days(conn, music):
    weights = enrich.artist_weights(conn, NOW)
    assert weights["example seed one"][1:3] == (10.0, 10)
    assert weights["example seed two"][1] == pytest.approx(2.5)
    assert "example old favorite" not in weights  # last played over a year ago
    all_time = enrich.artist_weights(conn, NOW, days=36500)
    assert all_time["example old favorite"][2] == 20


def test_similar_artists_and_tags_are_cached(conn, make_client, music):
    count, calls = run_enrich_lastfm(conn, make_client, music)
    assert count == 4  # four artists played in the last 12 months
    assert ("artist.getsimilar", "Example Seed One") in calls
    assert len(calls) == 8  # similar artists and tags for each
    rows = conn.execute(
        "SELECT similar_name, match FROM stg_lastfm_similar_artists "
        "WHERE artist_key = 'example seed one' ORDER BY match DESC"
    ).fetchall()
    assert [tuple(r) for r in rows][:2] == [("Example Seed Two", 0.9), ("Example New X", 0.8)]
    tags = conn.execute(
        "SELECT tag FROM stg_lastfm_artist_tags WHERE artist_key = 'example seed one' "
        "ORDER BY tag_count DESC"
    ).fetchall()
    assert [r[0] for r in tags] == ["example dream pop", "night"]

    assert run_enrich_lastfm(conn, make_client, music, now=NOW_DT + timedelta(days=29)) == (0, [])
    count, calls = run_enrich_lastfm(conn, make_client, music, now=NOW_DT + timedelta(days=31))
    assert count == 4 and len(calls) == 8


def test_unknown_artist_is_skipped_without_failing(conn, make_client, music):
    recent = music.recent
    recent.scrobbles.append(scrobble("Example Unknown", "Mystery", "", NOW - 5))
    client, _ = make_client(music)
    lastfm.sync(conn, client, LASTFM_SETTINGS, out=quiet, now=lambda: NOW + 2)
    count, _ = run_enrich_lastfm(conn, make_client, music)
    assert count == 5
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM stg_lastfm_similar_artists WHERE artist_key = 'example unknown'"
        ).fetchone()[0]
        == 0
    )
    assert (
        conn.execute("SELECT status FROM sync_runs ORDER BY sync_run_id DESC LIMIT 1").fetchone()[0]
        == "success"
    )


def test_bad_key_stops_the_run_and_caches_nothing(conn, make_client, music):
    music.bad_key = True
    client, _ = make_client(music)
    messages = []
    count = enrich.enrich_lastfm(conn, client, LASTFM_SETTINGS, out=messages.append, now=NOW_DT)
    assert count == 0
    assert "Invalid API key" in messages[-1]
    assert conn.execute("SELECT COUNT(*) FROM stg_lastfm_artist_fetches").fetchone()[0] == 0


def test_artist_covers_for_new_artists_are_cached(conn, make_client, music):
    client, _ = make_client(music)
    names = ["Example New X", "Example New Y", "example new x"]  # X once, whatever the case
    assert enrich.fetch_artist_covers(conn, client, LASTFM_SETTINGS, names, NOW_DT) == 2
    rows = dict(
        conn.execute("SELECT artist_key, image_url FROM stg_lastfm_artist_top_album").fetchall()
    )
    assert rows["example new x"] == "https://lastfm.freetls.fastly.net/i/u/300x300/newx.png"
    assert rows["example new y"] is None  # an album with only the placeholder image
    music.calls.clear()
    assert enrich.fetch_artist_covers(conn, client, LASTFM_SETTINGS, names, NOW_DT) == 0
    assert music.calls == []
