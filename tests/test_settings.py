import pytest

from taste import settings
from taste.settings import SettingError
from tests.test_reports import add_scored_anime


def test_defaults_exist_for_every_setting(conn):
    values = settings.get_all(conn)
    assert values["timezone"] == "America/Phoenix"
    assert values["genre_min_sample"] == 5
    assert values["include_nsfw"] is True
    assert values["lastfm_capture_scope"] == "unknown"
    stored = conn.execute("SELECT COUNT(*) FROM app_settings").fetchone()[0]
    assert stored == len(settings.SETTINGS)


@pytest.mark.parametrize(
    ("key", "raw", "message"),
    [
        ("genre_min_sample", "0", "at least 1"),
        ("genre_min_sample", "abc", "must be a number"),
        ("in_line_threshold", "-1", "at least 0"),
        ("timezone", "Mars/Olympus_Mons", "Unknown time zone"),
        ("lastfm_capture_scope", "tablet", "pick one"),
        ("lastfm_lookback_days", "400", "at most 365"),
        ("display_name", "x" * 201, "too long"),
        ("not_a_setting", "1", "Unknown setting"),
    ],
)
def test_validation_errors(conn, key, raw, message):
    with pytest.raises(SettingError, match=message):
        settings.set_value(conn, key, raw)


def test_values_round_trip_with_types(conn):
    settings.set_value(conn, "genre_min_sample", " 7 ")
    settings.set_value(conn, "in_line_threshold", "0.5")
    settings.set_value(conn, "include_nsfw", "off")
    assert settings.get(conn, "genre_min_sample") == 7
    assert settings.get(conn, "in_line_threshold") == 0.5
    assert settings.get(conn, "include_nsfw") is False


def test_views_use_genre_min_and_threshold(conn):
    add_scored_anime(conn, 6, "Drama", my_score=8.2, community_mean=8)  # +0.2
    add_scored_anime(conn, 3, "Mecha", my_score=6, community_mean=8)
    rows = conn.execute("SELECT genre, vs_community FROM rpt_mal_genre_vs_community").fetchall()
    assert [tuple(r) for r in rows] == [("Drama", "in_line")]

    settings.set_value(conn, "genre_min_sample", 3)
    settings.set_value(conn, "in_line_threshold", 0.1)
    rows = conn.execute(
        "SELECT genre, vs_community, min_sample_size FROM rpt_mal_genre_vs_community ORDER BY genre"
    ).fetchall()
    assert [tuple(r) for r in rows] == [("Drama", "above", 3), ("Mecha", "below", 3)]


def test_top_n_setting_limits_rows(conn, make_client):
    from tests.test_reports import seed_lastfm

    seed_lastfm(conn, make_client)
    assert conn.execute("SELECT COUNT(*) FROM rpt_lastfm_top_artists_all_time").fetchone()[0] == 3
    settings.set_value(conn, "top_n_all_time", 1)
    assert conn.execute("SELECT COUNT(*) FROM rpt_lastfm_top_artists_all_time").fetchone()[0] == 1
