"""Recaps: the numbers per period, the late-data rule, and the cards. Made-up data only.

A small world goes in through the real MAL and Last.fm sync code: four shows (one
finished in August, two in September, one with no finish date) and plays in
August, September, and October 2026. Times are in the default zone, Phoenix.
"""

import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from taste import activity, enrich, recap, settings
from taste.activity import Period
from taste.config import LastfmSettings, MalSettings
from taste.sources import lastfm, mal
from tests.conftest import FAKE_LASTFM_KEY, FAKE_MAL_CLIENT_ID
from tests.fake_recs import FakeLastfmApi, FakeMal, anime, scrobble
from tests.test_lastfm_sync import FakeLastfm

PHOENIX = ZoneInfo("America/Phoenix")
OCT_14 = datetime(2026, 10, 14, 18, 0, tzinfo=timezone.utc)


def at(day: str, hour: int = 12) -> int:
    """Unix seconds for a local Phoenix time."""
    return int(datetime.fromisoformat(f"{day}T{hour:02d}:00").replace(tzinfo=PHOENIX).timestamp())


def entry(node, status, score=0, finish=None, episodes=None, updated="2026-01-01T00:00:00+00:00"):
    fields = {
        "status": status,
        "score": score,
        "num_episodes_watched": node["num_episodes"] if episodes is None else episodes,
        "is_rewatching": False,
        "updated_at": updated,
    }
    if finish:
        fields["finish_date"] = finish
    return {"node": node, "list_status": fields}


def show(anime_id, title, mean, genres, episodes=12):
    node = anime(anime_id, title, mean, genres, ["Studio Alpha"], episodes=episodes)
    node["average_episode_duration"] = 1440  # 24 minutes
    return node


SHOWS = {
    301: show(301, "Example Autumn Drama", 7.4, ["Drama"]),
    302: show(302, "Example Autumn Comedy", 7.0, ["Comedy", "Drama"]),
    303: show(303, "Example Undated Show", 8.0, ["Action"]),
    304: show(304, "Example Summer Show", 8.0, ["Drama"], episodes=24),
}


def list_now():
    return [
        entry(SHOWS[301], "completed", 8, "2026-09-10"),
        entry(SHOWS[302], "completed", 7, "2026-09-20"),
        entry(SHOWS[303], "completed", 9),  # no finish date, edited before the first sync
        entry(SHOWS[304], "completed", 9, "2026-08-05"),
    ]


PLAYS = (
    [scrobble("Example Band", "Song One", "First LP", at("2026-08-03"), "lp1") for _ in range(1)]
    + [
        scrobble("Example Band", f"Song {i}", "First LP", at("2026-09-05", 8 + i), "lp1")
        for i in range(6)
    ]  # fmt: skip
    + [scrobble("Example Newcomer", "Hello", "", at("2026-09-06", 22))]
    + [scrobble("Example Band", "Song Late", "First LP", at("2026-10-02"), "lp1")]
)


@pytest.fixture
def world(conn, make_client):
    mal_api = FakeMal(entries=list_now(), details={})
    client, _ = make_client(mal_api)
    run = lambda: mal.sync(  # noqa: E731
        conn, client, MalSettings(FAKE_MAL_CLIENT_ID, "example_user"), out=lambda m: None
    )
    run()
    recent = FakeLastfm()
    recent.scrobbles = list(PLAYS)
    music = FakeLastfmApi(recent)
    music.lengths = {"Hello": 0}  # Last.fm has no length for this one
    lf_client, _ = make_client(music)
    cfg = LastfmSettings(FAKE_LASTFM_KEY, "example_user")
    lastfm.sync(conn, lf_client, cfg, out=lambda m: None, now=lambda: int(OCT_14.timestamp()))
    return {"conn": conn, "mal": mal_api, "sync_mal": run, "music": music, "lf": lf_client}


def test_periods_step_and_name_themselves():
    october = Period("month", "2026-10")
    assert (october.start, october.end, october.label, october.short) == (
        "2026-10-01",
        "2026-11-01",
        "October 2026",
        "October",
    )
    assert Period("month", "2026-12").next() == Period("month", "2027-01")
    assert Period("month", "2026-01").previous() == Period("month", "2025-12")
    assert Period("year", "2026").end == "2027-01-01"
    assert len(Period("month", "2026-02").days()) == 28


def test_finished_shows_use_typed_dates_and_leave_old_guesses_out(world):
    conn = world["conn"]
    sept = activity.finished_shows(conn, "2026-09-01", "2026-10-01")
    assert [(f.title, f.day, f.my_score) for f in sept] == [
        ("Example Autumn Drama", "2026-09-10", 8),
        ("Example Autumn Comedy", "2026-09-20", 7),
    ]
    assert sept[1].genres == ["Comedy", "Drama"]
    # The undated show's guess (its last edit, before the first sync) counts nowhere.
    everything = activity.finished_shows(conn, "2000-01-01", "2100-01-01")
    assert "Example Undated Show" not in [f.title for f in everything]


def test_a_guess_seen_live_counts(world):
    conn = world["conn"]
    later = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    world["mal"].entries = [*list_now()[:2], entry(SHOWS[303], "completed", 9, updated=later)]
    world["mal"].entries.append(list_now()[3])
    world["sync_mal"]()
    titles = [f.title for f in activity.finished_shows(conn, "2000-01-01", "2100-01-01")]
    assert "Example Undated Show" in titles  # edited after the first sync: seen happening


def test_episodes_are_recorded_when_progress_goes_up(world):
    conn = world["conn"]
    watching = show(305, "Example Weekly Show", 7.5, ["Drama"], episodes=12)
    world["mal"].entries = [*list_now(), entry(watching, "watching", episodes=2)]
    world["sync_mal"]()
    # Seen for the first time: nothing is known about when those two were watched.
    assert episode_keys(conn) == []
    world["mal"].entries[-1] = entry(
        watching, "watching", episodes=5, updated="2026-10-03T03:00:00+00:00"
    )
    world["sync_mal"]()
    assert episode_keys(conn) == ["305:ep:w:3", "305:ep:w:4", "305:ep:w:5"]
    world["sync_mal"]()  # nothing changed: nothing new
    assert len(episode_keys(conn)) == 3
    # October 2 in Phoenix (03:00 UTC on the 3rd), 24 minutes each.
    eps = activity.episodes_watched(conn, "2026-10-01", "2026-11-01")
    assert (eps.count, eps.seconds) == (3, 3 * 1440)
    # September: no tracked episodes, so the two finished shows count all of theirs.
    assert activity.episodes_watched(conn, "2026-09-01", "2026-10-01").count == 24
    world["mal"].entries.pop()  # removed from the list: its episodes go too
    world["sync_mal"]()
    assert episode_keys(conn) == []


def episode_keys(conn):
    return [
        r[0]
        for r in conn.execute(
            "SELECT source_event_key FROM core_behavior_events "
            "WHERE event_type = 'episode_watched' ORDER BY source_event_key"
        )
    ]


def test_track_lengths_are_looked_up_once_and_hours_are_about(world):
    conn = world["conn"]
    sept = ("2026-09-01", "2026-10-01")
    assert activity.music_hours(conn, *sept).hours is None  # no lengths yet
    cfg = LastfmSettings(FAKE_LASTFM_KEY, "example_user")
    looked_up = enrich.fetch_track_lengths(conn, world["lf"], cfg, [sept], out=lambda m: None)
    assert looked_up == 7  # six songs and Hello, each once
    assert enrich.fetch_track_lengths(conn, world["lf"], cfg, [sept], out=lambda m: None) == 0
    hours = activity.music_hours(conn, *sept)
    # Six four-minute plays known; Hello has none and counts as the typical length.
    assert hours.coverage == pytest.approx(6 / 7)
    assert hours.hours == pytest.approx(7 * 4 / 60)
    assert activity.about_hours(41.4) == "about 41 hours"
    assert activity.about_hours(0.3) == "under an hour"
    assert not any(r[0].find("api_key") >= 0 for r in conn.execute(
        "SELECT request_params FROM raw_api_pages WHERE endpoint = 'track.getinfo'"))  # fmt: skip


def test_open_spans_cover_the_recaps_that_can_still_change(world):
    conn = world["conn"]
    assert activity.open_spans(conn, OCT_14) == [
        ("2026-10-01", "2026-11-01"),
        ("2026-09-01", "2026-10-01"),
    ]
    january = datetime(2027, 1, 5, 18, tzinfo=timezone.utc)
    assert ("2026-01-01", "2027-01-01") in activity.open_spans(conn, january)


def test_whole_score_rule():
    # 7 against 7.4 is level (7.4 rounds to 7); 7 against 7.5 is lower (it rounds to 8).
    assert activity.whole_score_split([(7, 7.4), (7, 7.5), (9, 8.2)]) == (1, 1, 1)


def test_a_month_recap(world):
    conn = world["conn"]
    settings.set_value(conn, "display_name", "Example Person")
    data = recap.build(conn, Period("month", "2026-09"), OCT_14)
    assert not data.so_far
    assert data.cards == ["cover", "music", "clock", "anime", "compare", "closing"]
    assert [name for name, _ in data.slides] == ["cover", "music", "when", "anime", "wrap"]
    assert data.slides[2] == ("when", ["clock"])  # a month has no months-of-the-year chart
    assert data.slides[-1] == ("wrap", ["compare", "closing"])
    assert data.file_names()[:2] == ["01-cover.png", "02-music.png"]
    assert data.save_name(1) == "Taste Engine recap September 2026 (2 of 5).png"
    m = data.music
    assert m.plays == 7
    assert m.top_artists == [("Example Band", 6), ("Example Newcomer", 1)]
    assert m.new_artists == 1 and m.top_new_artist == ("Example Newcomer", 1)
    assert m.busiest_day == ("2026-09-05", 6)
    assert sum(m.hours_by_clock) == 7 and m.hours_by_clock[22] == 1
    assert len(m.days) == 30 and dict(m.days)["2026-09-06"] == 1
    a = data.anime
    assert (a.finished, a.scored, a.episodes) == (2, 2, 24)
    assert a.hours == pytest.approx(24 * 1440 / 3600)
    assert a.best.title == "Example Autumn Drama"
    # 8 vs 7.4 is higher (7.4 rounds to 7); 7 vs 7.0 is level. Average +0.30.
    assert (a.above, a.level, a.below, a.avg_diff) == (1, 1, 0, 0.3)
    assert a.top_genre == ("Drama", 2)
    assert data.compare == [
        "7 times as much listening as August.",
        "1 more show finished than August (2 against 1).",
    ]
    assert data.closing == "An Example Autumn Drama month, with a lot of Example Band."
    assert data.profile_name == "Example Person"
    assert data.fingerprint == recap.build(conn, Period("month", "2026-09"), OCT_14).fingerprint


def test_the_current_month_is_so_far_and_a_year_has_its_own_card(world):
    conn = world["conn"]
    october = recap.build(conn, Period("month", "2026-10"), OCT_14)
    assert october.so_far and october.anime is None and october.music.plays == 1
    year = recap.build(conn, Period("year", "2026"), OCT_14)
    assert "year" in year.cards
    assert year.year.plays_by_month[7:10] == [1, 7, 1]
    assert year.year.shows_by_month[7:9] == [1, 2]
    assert year.year.busiest_month == ("September", 7)
    assert year.year.first_finish.title == "Example Summer Show"
    assert year.year.last_finish.title == "Example Autumn Comedy"
    empty = recap.build(conn, Period("month", "2025-03"), OCT_14)
    assert not recap.has_data(empty) and empty.cards == ["cover", "closing"]


def test_change_sentences():
    assert recap.change_sentence(118, 100, "listening", "September") == (
        "18% more listening than September."
    )
    assert recap.change_sentence(80, 100, "listening", "May") == "20% less listening than May."
    assert recap.change_sentence(102, 100, "listening", "May") == "About as much listening as May."
    assert recap.change_sentence(0, 0, "listening", "May") is None


def test_recaps_make_themselves_and_stay_up_to_date_for_two_weeks(world, tmp_path):
    conn = world["conn"]
    sept = Period("month", "2026-09")
    # October 14: September ended under two weeks ago, so it's due. Last year's recap
    # was never made, but it's far too late for it to make itself now.
    assert recap.due(conn, OCT_14) == [sept]
    data = recap.build(conn, sept, OCT_14)
    assert recap.record(conn, data, OCT_14) is True  # the first time: worth a banner
    assert recap.unannounced(conn) == [sept]
    recap.mark_announced(conn, sept)
    assert recap.unannounced(conn) == []
    assert recap.record(conn, data, OCT_14) is False
    assert recap.stored_fingerprint(conn, sept) == data.fingerprint
    # Still within the window on the 14th: checked again on each launch or sync.
    assert sept in recap.due(conn, OCT_14)
    # The 15th is past it: the recap turns final and isn't checked again.
    later = datetime(2026, 10, 15, 18, tzinfo=timezone.utc)
    assert sept not in recap.due(conn, later)
    assert conn.execute("SELECT final FROM recap_runs WHERE period_key = '2026-09'").fetchone()[0]
    january = datetime(2027, 1, 5, 18, tzinfo=timezone.utc)
    assert Period("year", "2026") in recap.due(conn, january)
    settings.set_value(conn, "recap_yearly", False)
    assert Period("year", "2026") not in recap.due(conn, january)
    path = recap.write_json(data, tmp_path / "out")
    assert json.loads(path.read_text(encoding="utf-8"))["period"] == {
        "kind": "month",
        "key": "2026-09",
    }
    assert recap.banner_text(sept) == "Your September recap is ready."


def test_a_late_first_launch_still_gets_last_months_recap(world):
    conn = world["conn"]
    # Opened on November 20: October's window has passed, but it was never made.
    late = datetime(2026, 11, 20, 18, tzinfo=timezone.utc)
    assert Period("month", "2026-10") in recap.due(conn, late)
    # Opened in January 2027 for the first time since: November isn't made by itself.
    assert Period("month", "2026-11") not in recap.due(
        conn, datetime(2027, 1, 20, tzinfo=timezone.utc)
    )


def test_cards_draw_at_phone_story_size(world, qapp):
    from taste.desktop import recap_cards

    data = recap.build(world["conn"], Period("year", "2026"), OCT_14)
    cards = recap_cards.render(data)
    assert [name for name, _ in cards] == [name for name, _ in data.slides]
    assert 5 <= len(cards) <= 7
    assert all((img.width(), img.height()) == (1080, 1920) for _, img in cards)
    assert ("when", ["clock", "year"]) in data.slides  # a year adds its months


def test_a_picture_that_fails_gets_a_placeholder_not_a_crash(world, qapp):
    from taste.desktop import recap_cards

    def broken(url):
        raise OSError("no picture")

    data = recap.build(world["conn"], Period("month", "2026-09"), OCT_14)
    cards = recap_cards.render(data, broken)
    assert len(cards) == len(data.slides) and not cards[0][1].isNull()


def test_a_recap_made_by_hand_is_never_announced(world):
    conn = world["conn"]
    data = recap.build(conn, Period("month", "2026-03"), OCT_14)
    assert recap.record(conn, data, OCT_14, announced=True) is True
    assert recap.unannounced(conn) == []
