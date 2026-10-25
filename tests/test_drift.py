"""Taste drift uses known years and never names hidden anime."""

from datetime import datetime, timezone

from taste import drift, recap, settings
from taste.activity import Period
from taste.config import MalSettings
from taste.sources import mal
from tests.conftest import FAKE_MAL_CLIENT_ID
from tests.fake_recs import FakeMal
from tests.test_recap import entry, show


def library(conn, make_client):
    entries = []
    for year in (2024, 2025):
        for i in range(20):
            genre = "Drama" if (year == 2025 and i < 15) or (year == 2024 and i < 5) else "Comedy"
            node = show(year * 100 + i, f"Example {year} show {i}", 7, [genre])
            if i == 0:
                node["nsfw"] = "black"
                node["title"] = "Example Hidden Anime"
            entries.append(
                entry(node, "completed", 9 if year == 2025 else 7, f"{year}-05-{i + 1:02d}")
            )
    client, _ = make_client(FakeMal(entries, {}))
    mal.sync(
        conn, client, MalSettings(FAKE_MAL_CLIENT_ID, "example_user"), out=lambda message: None
    )


def test_genres_grow_and_fade_and_small_media_are_absent(conn, make_client):
    library(conn, make_client)
    result = drift.compare(conn, 2025)
    anime = result["anime"]
    assert anime["growing"] == ("Drama", 0.75, 0.25)
    assert anime["fading"] == ("Comedy", 0.25, 0.75)
    assert anime["difference"] == 2
    assert anime["previous_difference"] == 0
    assert anime["movement"] == 2
    assert "book" not in result and "music" not in result
    assert "Hidden Anime" not in repr(result)
    settings.set_value(conn, "drift_min_titles", 21)
    assert drift.compare(conn, 2025) == {}


def test_year_deck_contains_drift_month_does_not_and_card_draws(conn, make_client):
    from taste.desktop import recap_cards

    library(conn, make_client)
    now = datetime(2026, 1, 2, tzinfo=timezone.utc)
    year = recap.build(conn, Period("year", "2025"), now)
    month = recap.build(conn, Period("month", "2025-05"), now)
    names = [n for n, _ in year.slides]
    assert "drift" in names and names.index("drift") == names.index("when") + 1
    assert "drift" not in [n for n, _ in month.slides]
    assert len(year.slides) <= 8 and len(month.slides) <= 7
    images = dict(recap_cards.render(year))
    assert not images["drift"].isNull()
    fingerprint = year.fingerprint
    year.drift["anime"]["movement"] = 5
    assert year.fingerprint != fingerprint


def test_music_counts_new_artist_play_share_and_top_changes(conn, make_client):
    from tests.fake_recs import scrobble
    from tests.test_lastfm_sync import FakeLastfm, run_sync
    from tests.test_recap import at

    api = FakeLastfm()
    for year, artist, count in (
        (2023, "Example Earlier Artist", 10),
        (2024, "Example Earlier Artist", 120),
        (2024, "Example Second Artist", 80),
        (2025, "Example Second Artist", 50),
        (2025, "Example New Artist", 150),
    ):
        for i in range(count):
            api.scrobbles.append(
                scrobble(artist, f"Example track {i}", "", at(f"{year}-06-01") + i)
            )
    client, _ = make_client(api)
    run_sync(conn, client)
    result = drift.compare(conn, 2025)["music"]
    assert result["new_artist_plays"] == [150, 80]
    assert result["new_share"] == [0.75, 0.4]
    assert result["retained_top_artists"] == 1
    assert "150/200" in result["lines"][0]
