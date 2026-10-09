"""Deezer: artist photos for suggested artists that Last.fm has no picture for."""

from datetime import timedelta

from taste import enrich, images, recommend
from tests.fake_recs import DEEZER_PHOTO, FakeDeezer
from tests.test_enrich import NOW_DT


def stored(conn):
    return dict(conn.execute("SELECT artist_key, picture_url FROM stg_deezer_artists"))


def test_only_an_exact_name_match_counts(conn, make_client):
    fake = FakeDeezer(
        {
            "Example New Y": [("Example New Y Tribute", "aaa"), ("example new y", "bbb")],
            "Example Nobody": [("Someone Else Entirely", "ccc")],
        }
    )
    client, _ = make_client(fake)
    assert enrich.fetch_deezer_pictures(conn, client, ["Example New Y", "Example Nobody"], NOW_DT)
    assert stored(conn) == {"example new y": DEEZER_PHOTO.format("bbb"), "example nobody": None}
    pages = conn.execute("SELECT endpoint FROM raw_api_pages WHERE source = 'deezer'").fetchall()
    assert [p[0] for p in pages] == ["search/artist", "search/artist"]  # every response kept


def test_answers_are_cached_for_30_days_even_no_match(conn, make_client):
    fake = FakeDeezer({"Example New Y": [("Example New Y", "bbb")]})
    client, _ = make_client(fake)
    names = ["Example New Y", "Example Nobody"]
    assert enrich.fetch_deezer_pictures(conn, client, names, NOW_DT) == 2
    fake.queries.clear()
    assert enrich.fetch_deezer_pictures(conn, client, names, NOW_DT + timedelta(days=29)) == 0
    assert fake.queries == []
    assert enrich.fetch_deezer_pictures(conn, client, names, NOW_DT + timedelta(days=31)) == 2


def test_deezers_blank_photo_counts_as_none(conn, make_client):
    client, _ = make_client(FakeDeezer({"Example Faceless": [("Example Faceless", None)]}))
    enrich.fetch_deezer_pictures(conn, client, ["Example Faceless"], NOW_DT)
    assert stored(conn) == {"example faceless": None}


def test_a_quota_error_stops_quietly_and_caches_nothing(conn, make_client, sleeper):
    fake = FakeDeezer(error={"type": "Exception", "message": "Quota limit exceeded", "code": 4})
    client, _ = make_client(fake)
    assert enrich.fetch_deezer_pictures(conn, client, ["Example New Y"], NOW_DT) == 0
    assert stored(conn) == {}
    assert sleeper.delays  # it backed off and retried before giving up
    run = conn.execute("SELECT status, error_message FROM sync_runs WHERE source = 'deezer'")
    status, message = run.fetchone()
    assert status == "failed" and "Quota limit exceeded" in message


def test_deezer_photos_are_an_allowed_image_host():
    assert images.allowed(DEEZER_PHOTO.format("0123abcd"))


def test_artist_pictures_fall_back_to_deezer_last(conn):
    conn.execute(
        "INSERT INTO stg_lastfm_artist_top_album (artist_key, album_name, image_url, fetched_at) "
        "VALUES ('example new y', '', NULL, '2026-10-01 00:00:00')"
    )
    conn.execute(
        "INSERT INTO stg_deezer_artists (artist_key, deezer_id, name, picture_url, fetched_at) "
        "VALUES ('example new y', 7, 'Example New Y', ?, '2026-10-01 00:00:00')",
        (DEEZER_PHOTO.format("bbb"),),
    )
    assert recommend._artist_cover(conn, "Example New Y") == DEEZER_PHOTO.format("bbb")
    # A Last.fm album cover, when there is one, still comes first.
    conn.execute(
        "UPDATE stg_lastfm_artist_top_album SET image_url = "
        "'https://lastfm-img.freetls.fastly.net/i/u/300x300/y.png'"
    )
    assert recommend._artist_cover(conn, "Example New Y").endswith("/y.png")
