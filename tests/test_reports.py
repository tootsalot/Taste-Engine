import csv

import pytest

from taste import core, local_time, reports, settings
from taste.config import LastfmSettings, MalSettings
from taste.db import transaction, unix_to_utc
from taste.sources import lastfm, mal
from tests.conftest import FAKE_LASTFM_KEY, FAKE_MAL_CLIENT_ID, FakeResponse, load_fixture


def seed_mal(conn, make_client):
    def handler(url, params):
        name = "mal_animelist_page2.json" if "offset=3" in url else "mal_animelist_page1.json"
        return FakeResponse(200, load_fixture(name))

    client, _ = make_client(handler)
    mal.sync(conn, client, MalSettings(FAKE_MAL_CLIENT_ID, "example_user"), out=lambda m: None)


def seed_lastfm(conn, make_client):
    def handler(url, params):
        name = "lastfm_recent_page1.json" if params["page"] == 1 else "lastfm_recent_page2.json"
        return FakeResponse(200, load_fixture(name))

    client, _ = make_client(handler)
    settings.set_value(conn, "lastfm_capture_scope", "desktop_only")
    lastfm.sync(
        conn,
        client,
        LastfmSettings(FAKE_LASTFM_KEY, "example_user"),
        out=lambda m: None,
        now=lambda: 1791500000,
    )


def add_scored_anime(conn, n, genre, my_score, community_mean):
    """Insert anime straight into core for genre tests."""
    with transaction(conn):
        for i in range(n):
            item_id = core.upsert_item(
                conn,
                source="mal",
                id_type="mal_anime_id",
                external_id=f"{genre}-{i}",
                media_type="anime",
                title=f"{genre} show {i}",
            )
            core.set_item_tags(conn, item_id, "mal", [core.tag_id(conn, genre, "genre")])
            core.upsert_rating(
                conn,
                item_id=item_id,
                source="mal",
                raw_score=my_score,
                scale_min=1,
                scale_max=10,
                source_updated_at=None,
            )
            core.upsert_community_rating(
                conn,
                item_id=item_id,
                source="mal",
                mean_score=community_mean,
                scale_min=1,
                scale_max=10,
                num_raters=100,
            )


def add_play(conn, unix, artist="Artist", track="Track"):
    with transaction(conn):
        creator = core.upsert_creator(
            conn,
            source="lastfm",
            id_type="lastfm_artist_key",
            external_id=artist.lower(),
            name=artist,
        )
        item = core.upsert_item(
            conn,
            source="lastfm",
            id_type="lastfm_track_key",
            external_id=f"{artist.lower()}|{track.lower()}",
            media_type="track",
            title=track,
        )
        core.link_item_creator(conn, item, creator, "artist", "lastfm")
        core.upsert_event(
            conn,
            item_id=item,
            source="lastfm",
            event_type="play",
            occurred_at_utc=unix_to_utc(unix),
            occurred_at_unix=unix,
            time_precision="second",
            time_basis="source_timestamp",
            capture_scope="desktop_only",
            source_event_key=f"{unix}|{artist}|{track}",
        )
        local_time.refresh(conn)


def test_score_vs_community_and_summary(conn, make_client):
    seed_mal(conn, make_client)
    rows = conn.execute(
        "SELECT title, my_score, community_mean, score_diff FROM rpt_mal_score_vs_community "
        "ORDER BY my_score DESC"
    ).fetchall()
    # Scored shows with a community mean: 40000 (8 vs 8.0), 30000 (5 vs 7.4), 10001 (4 vs 6.52)
    assert [(r["my_score"], r["community_mean"], r["score_diff"]) for r in rows] == [
        (8.0, 8.0, 0.0),
        (5.0, 7.4, -2.4),
        (4.0, 6.52, -2.52),
    ]
    summary = conn.execute("SELECT * FROM rpt_mal_critic_summary").fetchone()
    assert summary["shows_scored"] == 3
    assert summary["avg_diff"] == pytest.approx(-1.64)
    assert summary["share_scored_below"] == pytest.approx(0.667)


def test_genre_view_respects_minimum_sample(conn):
    add_scored_anime(conn, 6, "Drama", my_score=9, community_mean=8)  # above, n=6
    add_scored_anime(conn, 5, "Mecha", my_score=6, community_mean=8)  # below, n=5
    add_scored_anime(conn, 4, "Romance", my_score=10, community_mean=5)  # too few, hidden
    rows = conn.execute(
        "SELECT genre, shows_scored, avg_diff, vs_community, diff_vs_my_norm "
        "FROM rpt_mal_genre_vs_community ORDER BY genre"
    ).fetchall()
    assert [(r["genre"], r["shows_scored"], r["vs_community"]) for r in rows] == [
        ("Drama", 6, "above"),
        ("Mecha", 5, "below"),
    ]
    # My overall avg diff is (6*1 + 5*-2 + 4*5) / 15 = 1.07, so Drama is below my norm.
    assert rows[0]["diff_vs_my_norm"] == pytest.approx(1 - 16 / 15, abs=0.01)


def test_dropped_on_hold_report(conn, make_client):
    seed_mal(conn, make_client)
    rows = conn.execute(
        "SELECT title, list_status, episodes_watched, total_episodes, pct_complete, my_score "
        "FROM rpt_mal_dropped_on_hold ORDER BY title"
    ).fetchall()
    assert [tuple(r) for r in rows] == [
        ("Example Dropped Show", "dropped", 6, 24, 25.0, 5.0),
        ("Example On Hold Movie", "on_hold", 0, 1, 0.0, None),
    ]


def test_phoenix_time_conversion(conn):
    # 2026-06-21 00:00 UTC is Saturday 17:00 in Phoenix (UTC-7 even in summer).
    add_play(conn, 1782000000)
    # 2026-01-01 00:00 UTC is still 2025 in Phoenix.
    add_play(conn, 1767225600, track="New Year")
    rows = conn.execute(
        "SELECT occurred_at_local, local_year, local_month, local_hour, local_weekday_num "
        "FROM rpt_lastfm_plays_local ORDER BY occurred_at_utc"
    ).fetchall()
    assert [tuple(r) for r in rows] == [
        ("2025-12-31 17:00:00", 2025, "2025-12", 17, 3),
        ("2026-06-20 17:00:00", 2026, "2026-06", 17, 6),
    ]
    hours = conn.execute("SELECT local_hour, plays FROM rpt_lastfm_by_hour").fetchall()
    assert len(hours) == 24
    assert {h["local_hour"]: h["plays"] for h in hours}[17] == 2
    days = conn.execute(
        "SELECT weekday, plays FROM rpt_lastfm_by_weekday ORDER BY local_weekday_num"
    ).fetchall()
    assert len(days) == 7
    assert {d["weekday"]: d["plays"] for d in days} == {
        "Sunday": 0,
        "Monday": 0,
        "Tuesday": 0,
        "Wednesday": 1,
        "Thursday": 0,
        "Friday": 0,
        "Saturday": 1,
    }


def test_top_artists_and_tracks(conn, make_client):
    seed_lastfm(conn, make_client)
    artists = conn.execute(
        "SELECT play_rank, artist, plays FROM rpt_lastfm_top_artists_all_time ORDER BY play_rank"
    ).fetchall()
    assert [tuple(r) for r in artists] == [
        (1, "the example band", 3),
        (2, "Night Driver", 1),
        (3, "Some Indie Artist", 1),
    ]
    tracks = conn.execute(
        "SELECT track, plays FROM rpt_lastfm_top_tracks_all_time WHERE play_rank = 1"
    ).fetchone()
    assert tuple(tracks) == ("signal", 2)
    by_month = conn.execute(
        "SELECT DISTINCT local_month FROM rpt_lastfm_top_artists_by_month"
    ).fetchall()
    assert [r[0] for r in by_month] == ["2026-10"]


def test_csvs_written_and_lastfm_ones_carry_the_desktop_note(conn, make_client, tmp_path):
    seed_mal(conn, make_client)
    seed_lastfm(conn, make_client)
    written = reports.write_csvs(conn, tmp_path)
    assert len(written) == len(reports.REPORTS)
    for report in reports.REPORTS:
        with (tmp_path / report.csv_name).open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        if report.uses_lastfm:
            assert rows, report.view
            assert all("Desktop listening only" in r["data_scope"] for r in rows), report.view


def test_summary_handles_empty_database(conn):
    lines = reports.summary_lines(conn)
    text = "\n".join(lines)
    assert "No scored anime yet" in text
    assert "Capture scope not set" in text
    assert "No scrobbles yet" in text


def test_summary_with_data(conn, make_client):
    seed_mal(conn, make_client)
    seed_lastfm(conn, make_client)
    text = "\n".join(reports.summary_lines(conn))
    assert "3 scored shows" in text
    assert "1.64 points below the community" in text
    assert "Desktop listening only" in text
    assert "Top artists: the example band (3)" in text


def test_genre_label_never_prints_negative_zero():
    row = {"genre": "Music", "avg_diff": -0.0, "shows_scored": 12}
    assert reports._genre_label(row) == "Music (+0.00, n=12)"
