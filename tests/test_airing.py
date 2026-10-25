"""Weekly broadcast times from an invented MAL list, with no Qt dependency."""

from datetime import datetime, timedelta, timezone

import pytest

from taste import db, settings
from taste.sources import mal
from tests.fake_recs import FakeMal, anime, list_entry
from tests.test_mal_sync import SETTINGS

NOW = datetime(2026, 10, 24, tzinfo=timezone.utc)


def airing_entries():
    entries = []
    for i, title, day, clock, watched in (
        (501, "Example Midnight Crossing", "sunday", "00:30", 6),
        (502, "Example Final Show", "saturday", "17:30", 11),
        (503, "Example No Schedule", None, None, 2),
        (504, "Example Adult Airing", "saturday", "10:00", 3),
    ):
        node = anime(i, title, 7.0, ["Drama"], [], episodes=12)
        node["status"] = "currently_airing"
        node["broadcast"] = {"day_of_the_week": day, "start_time": clock}
        if i == 504:
            node["nsfw"] = "black"
        entry = list_entry(node, "watching")
        entry["list_status"]["num_episodes_watched"] = watched
        entries.append(entry)
    return entries


@pytest.fixture
def world(conn, make_client):
    fake = FakeMal(entries=airing_entries(), details={})
    client, transport = make_client(fake)
    assert mal.sync(conn, client, SETTINGS, out=lambda m: None).status == "success"
    settings.set_value(conn, "timezone", "America/Phoenix")
    settings.set_value(conn, "include_nsfw", False)
    return fake, client, transport


def test_mal_requests_and_upserts_broadcast_without_losing_raw(conn, world):
    fake, client, transport = world
    assert "broadcast" in transport.calls[0]["params"]["fields"].split(",")
    assert transport.calls[0]["params"]["nsfw"] == "true"
    row = conn.execute("SELECT * FROM stg_mal_anime WHERE mal_anime_id = 501").fetchone()
    assert (row["broadcast_day"], row["broadcast_time"]) == ("sunday", "00:30")
    raw = conn.execute(
        "SELECT payload FROM raw_api_pages WHERE raw_page_id = ?", (row["last_raw_page_id"],)
    ).fetchone()[0]
    assert '"start_time": "00:30"' in raw
    fake.entries[0]["node"]["broadcast"] = {"day_of_the_week": "monday", "start_time": "01:00"}
    mal.sync(conn, client, SETTINGS, out=lambda m: None)
    row = conn.execute(
        "SELECT broadcast_day, broadcast_time FROM stg_mal_anime WHERE mal_anime_id = 501"
    ).fetchone()
    assert tuple(row) == ("monday", "01:00")
    fake.entries[0]["node"].pop("broadcast")
    mal.sync(conn, client, SETTINGS, out=lambda m: None)
    assert tuple(
        conn.execute(
            "SELECT broadcast_day, broadcast_time FROM stg_mal_anime WHERE mal_anime_id = 501"
        ).fetchone()
    ) == (None, None)


def test_schedule_crosses_midnight_sorts_and_marks_the_final_episode(conn, world):
    from taste import airing

    shows = airing.load(conn, NOW)
    assert [s.mal_anime_id for s in shows] == [502, 501, 503]
    assert shows[1].next_at.isoformat() == "2026-10-24T08:30:00-07:00"
    assert shows[1].next_episode == 7
    assert shows[1].caption == "Sat 8:30 AM · next for you: ep 7"
    assert shows[0].final_episode and "next for you: ep 12 · final episode" in shows[0].caption
    assert shows[-1].next_at is None
    assert shows[-1].caption == "no set time · next for you: ep 3"
    settings.set_value(conn, "clock_format", "24h")
    assert airing.load(conn, NOW)[1].caption == "Sat 08:30 · next for you: ep 7"
    settings.set_value(conn, "timezone", "Europe/London")
    assert airing.load(conn, NOW)[1].caption == "Sat 16:30 · next for you: ep 7"


def test_hidden_adult_titles_and_posters_never_leave_the_loader(conn, world):
    from taste import airing

    assert all(s.mal_anime_id != 504 for s in airing.load(conn, NOW))
    settings.set_value(conn, "include_nsfw", True)
    shows = airing.load(conn, NOW)
    assert shows[0].title == "Example Adult Airing"
    assert len(shows) == 4


def test_adult_posters_stay_off_the_dashboard_even_when_shown(conn, world):
    """The Dashboard never pictures a poster MAL rates "black", like Recently finished."""
    from taste import airing

    settings.set_value(conn, "include_nsfw", True)
    posters = {s.mal_anime_id: s.poster_url for s in airing.load(conn, NOW)}
    assert posters[504] is None
    assert all(posters[i] for i in (501, 502, 503))


def test_only_currently_airing_watching_entries_are_included(conn, world):
    from taste import airing

    conn.execute("UPDATE stg_mal_list_entries SET status = 'completed' WHERE mal_anime_id = 501")
    conn.execute(
        "UPDATE stg_mal_list_entries SET removed_at = '2026-10-24 00:00:00' "
        "WHERE mal_anime_id = 502"
    )
    conn.execute(
        "UPDATE stg_mal_anime SET airing_status = 'finished_airing' WHERE mal_anime_id = 503"
    )
    assert airing.load(conn, NOW) == []


def test_next_week_and_exact_broadcast_boundary():
    from taste import airing

    exact = datetime(2026, 10, 24, 15, 30, tzinfo=timezone.utc)
    assert airing.next_broadcast("sunday", "00:30", exact, "UTC") == exact
    assert airing.next_broadcast("sunday", "00:30", exact + timedelta(seconds=1), "UTC") == (
        exact + timedelta(days=7)
    )


@pytest.mark.parametrize(
    ("day", "clock"),
    [
        (None, None),
        ("sunday", None),
        ("invalid", "12:00"),
        ("monday", "24:30"),
        ("monday", "12:99"),
        ("monday", "bad"),
    ],
)
def test_missing_or_invalid_broadcast_has_no_set_time(day, clock):
    from taste import airing

    assert airing.next_broadcast(day, clock, NOW, "America/Phoenix") is None


def test_daylight_saving_converts_each_occurrence_and_sorts_by_instant(conn, world):
    from taste import airing

    # Both land at 1:30 AM in New York, on opposite sides of the repeated hour.
    conn.execute(
        "UPDATE stg_mal_anime SET broadcast_day = 'sunday', broadcast_time = '15:30' "
        "WHERE mal_anime_id = 501"
    )
    conn.execute(
        "UPDATE stg_mal_anime SET broadcast_day = 'sunday', broadcast_time = '14:30' "
        "WHERE mal_anime_id = 502"
    )
    settings.set_value(conn, "timezone", "America/New_York")
    shows = airing.load(conn, datetime(2026, 11, 1, tzinfo=timezone.utc))
    assert [s.mal_anime_id for s in shows] == [502, 501, 503]
    assert shows[0].next_at.fold == 0 and shows[1].next_at.fold == 1
    assert shows[0].next_at.timestamp() < shows[1].next_at.timestamp()


def test_profile_upgrade_adds_broadcast_and_enables_new_card_only_once(conn):
    assert "airing" in settings.DASHBOARD_CARDS
    conn.execute("ALTER TABLE stg_mal_anime DROP COLUMN broadcast_day")
    conn.execute("ALTER TABLE stg_mal_anime DROP COLUMN broadcast_time")
    settings.set_value(conn, "dashboard_cards", "month,recent")
    db.apply_schema(conn)
    assert set(settings.get(conn, "dashboard_cards").split(",")) == {"month", "recent", "airing"}
    settings.set_value(conn, "dashboard_cards", "month,recent")
    db.apply_schema(conn)
    assert settings.get(conn, "dashboard_cards") == "month,recent"


def test_all_known_episodes_recorded_does_not_invent_an_extra_episode(conn, world):
    from taste import airing

    conn.execute(
        "UPDATE stg_mal_list_entries SET num_episodes_watched = 12 WHERE mal_anime_id = 502"
    )
    show = next(s for s in airing.load(conn, NOW) if s.mal_anime_id == 502)
    assert "all 12 episodes recorded" in show.caption
    assert "ep 13" not in show.caption
    conn.execute("UPDATE stg_mal_anime SET num_episodes = NULL WHERE mal_anime_id = 502")
    show = next(s for s in airing.load(conn, NOW) if s.mal_anime_id == 502)
    assert "next for you: ep 13" in show.caption and not show.final_episode


def test_adult_only_list_is_kept_in_storage_but_has_no_visible_schedule(conn, world):
    from taste import airing

    fake, client, _ = world
    fake.entries = [e for e in fake.entries if e["node"]["id"] == 504]
    mal.sync(conn, client, SETTINGS, out=lambda m: None)
    assert airing.load(conn, NOW) == []
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM stg_mal_list_entries WHERE removed_at IS NULL"
        ).fetchone()[0]
        == 1
    )
