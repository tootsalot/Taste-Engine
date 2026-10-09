"""Recommendations: predicted anime scores, candidate filtering, music, and saved runs.

The anime numbers below are worked out by hand from tests/fake_recs.py:

  my scores vs MAL:  101 +1.0, 102 -2.0, 103 -0.5, 106 +0.4
  overall bias:      (1.0 - 2.0 - 0.5 + 0.4) / 4 = -0.275
  extra per show:    101 +1.275, 102 -1.725, 103 -0.225, 106 +0.675
  Drama  (101, 103): (1.275 - 0.225) / (2 + 5)  = +0.15
  Comedy (102, 103): (-1.725 - 0.225) / (2 + 5) = -0.2786
  Action (106):      0.675 / (1 + 5)            = +0.1125
  Studio Alpha (101, 103): 1.05 / (2 + 3)  = +0.21
  Studio Beta  (102, 106): -1.05 / (2 + 3) = -0.21

  201 (MAL 8.2, Drama, Alpha): 8.2 - 0.275 + 0.15 + 0.5 * 0.21 = 8.18
  203 (MAL 8.0, Drama, Beta):  8.0 - 0.275 + 0.15 - 0.5 * 0.21 = 7.77
  104 (MAL 7.8, Drama, Gamma): 7.8 - 0.275 + 0.15             = 7.675
  206 (MAL 7.5, Hentai):       7.5 - 0.275                    = 7.225
"""

from datetime import datetime, timedelta, timezone

import pytest

from taste import enrich, recommend, runner, settings
from taste.db import transaction
from taste.http_client import HttpClient
from taste.recommend import GENRE_SHRINK, TasteModel, build_model
from taste.secrets_store import SecretStore
from taste.sources import lastfm
from tests.conftest import FakeKeyring, FakeTransport
from tests.fake_recs import (
    DAY,
    NOW,
    FakeLastfmApi,
    FakeMal,
    anime,
    list_entry,
    listening_history,
)
from tests.test_enrich import (
    LASTFM_SETTINGS,
    MAL_SETTINGS,
    NOW_DT,
    quiet,
    run_enrich_mal,
    sync_mal,
)
from tests.test_lastfm_sync import FakeLastfm


@pytest.fixture
def anime_world(conn, make_client):
    fake = FakeMal()
    sync_mal(conn, make_client, fake)
    run_enrich_mal(conn, make_client, fake)
    return fake


def anime_keys(conn):
    recs, _ = recommend.anime_recommendations(conn)
    return [int(r.item_key) for r in recs], {int(r.item_key): r for r in recs}


# ---------------------------------------------------------------------------
# The taste model
# ---------------------------------------------------------------------------


def test_predicted_scores_match_the_hand_worked_example(conn, anime_world):
    order, recs = anime_keys(conn)
    assert recs[201].score == pytest.approx(8.18)
    assert recs[203].score == pytest.approx(7.77)
    assert recs[104].score == pytest.approx(7.675, abs=0.005)
    assert recs[206].score == pytest.approx(7.225, abs=0.005)
    # Ranked by predicted score; support only nudges near-ties.
    assert order == [201, 203, 104, 206]


def test_model_biases(conn, anime_world):
    facts = recommend._anime_facts(conn)
    model = build_model(recommend._scored(conn), facts)
    assert model.overall == pytest.approx(-0.275)
    assert model.genre["Drama"] == pytest.approx(0.15)
    assert model.genre["Comedy"] == pytest.approx(-1.95 / 7)
    assert model.genre["Action"] == pytest.approx(0.1125)
    assert model.studio["Studio Alpha"] == pytest.approx(0.21)
    assert model.studio["Studio Beta"] == pytest.approx(-0.21)
    assert model.genre_counts == {"Drama": 2, "Comedy": 2, "Action": 1}


def test_genre_bias_is_shrunk_when_it_rests_on_few_shows():
    def model_for(n):
        # n Thrillers I rate 3 above MAL and n Slice of Life shows I rate 3 below.
        scored, facts = [], {}
        for i in range(n):
            scored += [(2 * i, 10.0, 7.0), (2 * i + 1, 4.0, 7.0)]
            facts[2 * i] = {"genres": ["Thriller"], "studios": []}
            facts[2 * i + 1] = {"genres": ["Slice of Life"], "studios": []}
        return build_model(scored, facts)

    one = model_for(1)
    assert one.overall == 0
    assert one.genre["Thriller"] == pytest.approx(3 / (1 + GENRE_SHRINK))  # 0.5, not 3
    assert one.genre["Slice of Life"] == pytest.approx(-3 / (1 + GENRE_SHRINK))
    ten = model_for(10)
    assert ten.genre["Thriller"] == pytest.approx(30 / (10 + GENRE_SHRINK))  # 2.0
    assert one.genre["Thriller"] < ten.genre["Thriller"] < 3


def test_predictions_stay_on_the_mal_scale():
    generous = TasteModel(overall=2.0, genre={}, genre_counts={}, studio={})
    harsh = TasteModel(overall=-2.0, genre={}, genre_counts={}, studio={})
    assert generous.predict(9.5, [], []) == 10.0
    assert harsh.predict(2.5, [], []) == 1.0


def test_no_scores_means_no_bias():
    model = build_model([], {})
    assert model.predict(7.0, ["Drama"], ["Studio Alpha"]) == 7.0


# ---------------------------------------------------------------------------
# Candidate filtering
# ---------------------------------------------------------------------------


def test_shows_on_my_list_are_left_out_except_plan_to_watch(conn, anime_world):
    order, recs = anime_keys(conn)
    assert not {101, 102, 103, 105, 106} & set(order)  # watched or watching
    assert recs[104].badge == "On your Plan to Watch"
    assert recs[201].badge == ""

    settings.set_value(conn, "rec_include_plan_to_watch", False)
    order, _ = anime_keys(conn)
    assert order == [201, 203, 206]


def test_dismissed_shows_stay_hidden(conn, anime_world):
    recommend.dismiss(conn, "anime", "203")
    recommend.dismiss(conn, "anime", "203")  # twice is fine
    order, _ = anime_keys(conn)
    assert 203 not in order


def test_filters_on_raters_media_type_and_nsfw(conn, anime_world):
    order, _ = anime_keys(conn)
    assert 202 not in order  # 3,000 raters, under the 5,000 minimum
    assert 205 not in order  # a music video
    assert 204 not in order  # a recap, never a candidate

    settings.set_value(conn, "rec_min_raters", 0)
    order, _ = anime_keys(conn)
    assert 202 in order
    settings.set_value(conn, "rec_media_types", "tv")
    order, _ = anime_keys(conn)
    assert 202 not in order  # a movie

    assert 206 in order
    settings.set_value(conn, "include_nsfw", False)
    order, _ = anime_keys(conn)
    assert 206 not in order


def test_candidates_without_details_wait_for_a_later_refresh(conn, make_client):
    fake = FakeMal()
    sync_mal(conn, make_client, fake)
    run_enrich_mal(conn, make_client, fake, candidate_limit=1)  # only 201 fetched
    order, _ = anime_keys(conn)
    assert order == [201, 104]


def test_rec_count_limits_the_list(conn, anime_world):
    # The setting's minimum is 5, so write a smaller value directly to keep the example small.
    conn.execute("UPDATE app_settings SET setting_value = '2' WHERE setting_key = 'rec_count'")
    order, _ = anime_keys(conn)
    assert order == [201, 203]


def test_cards_explain_themselves(conn, anime_world):
    _, recs = anime_keys(conn)
    one = recs[201]
    assert one.title == "Example Candidate One"
    assert one.subtitle == "TV · 2020 · 12 eps"  # the MAL mean sits next to the prediction
    assert one.facts["mal_mean"] == 8.2
    assert one.url == "https://myanimelist.net/anime/201"
    assert one.image_url == "https://cdn.myanimelist.net/images/anime/1/201.jpg"
    # Plain words, the show I liked most first. The numbers go to the tooltip.
    # Drama's lean is +0.15 (worked out above), right at the cutoff for a mention.
    assert one.reasons == [
        "Because you loved Example Drama A and really liked Example Action F, and you tend "
        "to enjoy drama anime."
    ]
    assert one.facts["details"] == [
        "20 MAL users who liked Example Drama A recommend this.",
        "7 MAL users who liked Example Action F recommend this.",
        "You rate Drama shows +0.15 vs your usual.",
    ]
    assert recs[203].reasons == [
        "It's the sequel to Example Drama A, which you gave a 9.",
        "You tend to enjoy drama anime.",
    ]
    assert "chance_8_plus" not in one.facts  # four scored shows are too few to say


def test_reasons_use_english_titles(conn, anime_world):
    conn.execute(
        "UPDATE stg_mal_anime SET title_en = 'Drama A in English' WHERE mal_anime_id = 101"
    )
    _, recs = anime_keys(conn)
    assert recs[201].reasons[0].startswith("Because you loved Drama A in English and")
    assert recs[203].reasons[0] == "It's the sequel to Drama A in English, which you gave a 9."


def test_model_scales_the_mal_mean_when_there_are_enough_shows():
    # 40 made-up shows where I score 2 x MAL - 8: generous on the best, harsh on the rest.
    means = [6.0 + 0.1 * i for i in range(40)]
    scored = [(i, 2 * m - 8, m) for i, m in enumerate(means)]
    facts = {i: {"genres": [], "studios": []} for i in range(40)}
    model = build_model(scored, facts)
    assert model.slope == pytest.approx(2.0)
    assert model.intercept == pytest.approx(-8.0)
    assert model.predict(8.5, [], []) == pytest.approx(9.0)

    # With fewer shows a slope would swing wildly, so it stays a plain shift.
    small = build_model(scored[:10], facts)
    assert small.slope == 1.0
    assert small.predict(8.5, [], []) == pytest.approx(8.5 + small.overall)


def test_genre_leans():
    model = TasteModel(0.0, {"Drama": 0.4, "Comedy": -0.5, "Action": 0.05}, {}, {})
    assert recommend._genre_leans(model, ["Drama", "Comedy"]) == ("Drama", "Comedy")
    assert recommend._genre_leans(model, ["Action"]) == (None, None)  # too small to mention


def test_because_sentences():
    because = recommend._because
    assert because([("Show A", 10), ("Show B", 8)], "Award Winning", None) == [
        "Because you loved Show A and really liked Show B, and you tend to enjoy "
        "award winning anime."
    ]
    assert because([("Show A", 7)], None, None) == ["Because you liked Show A."]
    assert because([], "Samurai", "Mystery") == [
        "You tend to enjoy samurai anime.",
        "Heads up: you're usually tougher on mystery anime.",
    ]
    assert because([], None, None) == []


def test_chance_of_an_8_or_more():
    # Predicted 7.6, and real scores have landed -1, 0, +0.5, and +1 from predictions:
    # 6.6, 7.6, 8.1, 8.6. Three of four round to 8 or more.
    assert recommend.chance_at_least(7.6, [-1.0, 0.0, 0.5, 1.0] * 5, 8) == 0.75
    assert recommend.chance_at_least(7.6, [0.0] * 3, 8) is None  # too few to say


# ---------------------------------------------------------------------------
# Holdout evaluation
# ---------------------------------------------------------------------------


def holdout_world(conn, make_client, n=30):
    """n shows, all MAL 7.5. Odd ids are Drama, which I score 8; even ids are Comedy, 6."""
    entries = []
    for i in range(1, n + 1):
        genre, score = ("Drama", 8) if i % 2 else ("Comedy", 6)
        entries.append(
            list_entry(anime(i, f"Example Show {i}", 7.5, [genre], []), "completed", score)
        )
    sync_mal(conn, make_client, FakeMal(entries=entries, details={}))


def test_holdout_evaluation(conn, make_client):
    holdout_world(conn, make_client)
    result = recommend.evaluate(conn)
    # Held out: ids 5, 10, ..., 30 (3 Drama, 3 Comedy). Trained on 12 of each.
    assert result["held_out"] == 6
    # The community mean is off by 0.5 on Drama and 1.5 on Comedy.
    assert result["community"] == 1.0
    # My overall bias (-0.5) alone makes both off by 1.0.
    assert result["overall"] == 1.0
    # Genre bias: +1.0 extra on 12 Drama shows, shrunk to 12 / 17. Each prediction is
    # then off by 1 - 12/17 = 5/17.
    assert result["model"] == round(5 / 17, 3)


def test_holdout_needs_enough_shows(conn, make_client):
    holdout_world(conn, make_client, n=12)
    assert recommend.evaluate(conn) == {"held_out": 2}


def test_metrics_travel_with_the_anime_list(conn, anime_world):
    _, metrics = recommend.anime_recommendations(conn)
    assert metrics["model_overall_bias"] == pytest.approx(-0.275)
    assert metrics["held_out"] == 0  # too few scored shows to evaluate


# ---------------------------------------------------------------------------
# Music
# ---------------------------------------------------------------------------


@pytest.fixture
def music_world(conn, make_client):
    recent = FakeLastfm()
    recent.scrobbles = listening_history()
    api = FakeLastfmApi(recent)
    client, _ = make_client(api)
    lastfm.sync(conn, client, LASTFM_SETTINGS, out=quiet, now=lambda: NOW + 1)
    enrich.enrich_lastfm(conn, client, LASTFM_SETTINGS, out=quiet, now=NOW_DT)
    return api


def test_discover_scores_by_similarity_and_recent_plays(conn, music_world):
    recommend.dismiss(conn, "artist", "example dismissed z")
    discover, _ = recommend.music_recommendations(conn, NOW_DT)
    # Seed One has weight 10 (the top, so 1.0); Seed Two 2.5 (so 0.25).
    # X: 1.0 * 0.8 + 0.25 * 0.6 = 0.95.  Y: 1.0 * 0.5.  W: 0.25 * 1.0.
    assert [(r.title, r.score) for r in discover] == [
        ("Example New X", 0.95),
        ("Example New Y", 0.5),
        ("Example New W", 0.25),
    ]
    x = discover[0]
    assert x.subtitle == "Sounds like 2 artists you play"
    assert x.reasons == ["Close to Example Seed One (80% similar) and Example Seed Two (60%)."]
    # A label by place in the list instead of an unexplained number.
    assert [r.facts["match_label"] for r in discover] == [
        "Strong match",
        "Good match",
        "Worth a try",
    ]
    assert x.facts["details"] == [
        "Match strength 0.95: how similar it is to artists you play, weighted by how "
        "much you play them."
    ]
    assert discover[1].reasons == ["Close to Example Seed One (50% similar), an artist you play."]
    assert x.url == "https://www.last.fm/music/Example+New+X"
    assert x.image_url is None  # no cover until fetch_artist_covers runs


def test_artists_i_already_play_are_never_discovered(conn, music_world):
    discover, _ = recommend.music_recommendations(conn, NOW_DT)
    titles = {r.title for r in discover}
    assert "Example Seed Two" not in titles
    assert "Example Dismissed Z" in titles  # only hidden once dismissed


def test_rediscover_window(conn, music_world):
    _, rediscover = recommend.music_recommendations(conn, NOW_DT)
    # Old Favorite: 20 plays, last 400 days ago. Faded has only 14 plays; Still Around
    # was played 10 days ago; Seed One and Seed Two are current.
    assert [r.title for r in rediscover] == ["Example Old Favorite"]
    old = rediscover[0]
    # The play count is already the card's big number and its reason; the subtitle says when.
    last = datetime.fromtimestamp(NOW - 400 * DAY, timezone.utc)
    assert old.subtitle == f"Last played {last:%b} {last.day}, {last.year}"
    # Artist art: the cover of their most-played album.
    assert old.image_url == "https://lastfm.freetls.fastly.net/i/u/300x300/olddays.png"

    # 181 days later, Still Around has been quiet long enough too.
    _, later = recommend.music_recommendations(conn, NOW_DT + timedelta(days=171))
    assert {r.title for r in later} == {"Example Still Around", "Example Old Favorite"}


# ---------------------------------------------------------------------------
# Saved runs and the runner
# ---------------------------------------------------------------------------


def test_runs_are_saved_and_read_back(conn, anime_world):
    with transaction(conn):
        counts = recommend.compute_all(conn, NOW_DT)
    assert counts["anime"] == 4
    recs, metrics, created = recommend.latest(conn, "anime")
    assert [r.item_key for r in recs] == ["201", "203", "104", "206"]
    assert recs[0].reasons[0].startswith("Because you loved Example Drama A")
    assert recs[0].facts["mal_mean"] == 8.2  # facts survive the round trip
    assert metrics["model_overall_bias"] == pytest.approx(-0.275)
    assert created is not None
    params = conn.execute("SELECT params_json FROM rec_runs WHERE kind = 'anime'").fetchone()[0]
    assert '"rec_count": 30' in params

    recommend.dismiss(conn, "anime", "203")
    recs, _, _ = recommend.latest(conn, "anime")
    assert [r.item_key for r in recs] == ["201", "104", "206"]
    assert recommend.latest(conn, "nothing") == ([], {}, None)


def test_refresh_recommendations_end_to_end(conn):
    mal_fake = FakeMal()
    recent = FakeLastfm()
    recent.scrobbles = listening_history()
    music = FakeLastfmApi(recent)

    def handler(url, params):
        return mal_fake(url, params) if "myanimelist" in url else music(url, params)

    def client_factory(secrets):
        return HttpClient(
            FakeTransport(handler), secrets=secrets, sleep=lambda s: None, jitter=lambda: 0.0
        )

    store = SecretStore(backend=FakeKeyring())
    store.set("me", "MAL_CLIENT_ID", MAL_SETTINGS.client_id)
    store.set("me", "LASTFM_API_KEY", LASTFM_SETTINGS.api_key)
    settings.set_value(conn, "mal_username", "example_user")
    settings.set_value(conn, "lastfm_username", "example_user")
    runner.sync_sources(
        conn, "me", "all", store=store, out=quiet, client_factory=client_factory,
        now=lambda: NOW + 1,
    )  # fmt: skip

    messages = []
    counts = runner.refresh_recommendations(
        conn, "me", store=store, out=messages.append, client_factory=client_factory, now=NOW_DT
    )
    assert counts == {"anime": 4, "music_discover": 4, "music_rediscover": 1}
    assert messages[-1] == ("Recommendations ready: 4 anime, 4 new artists, 1 to rediscover.")
    # Suggested artists without a played album get their top album's cover.
    discover, _, _ = recommend.latest(conn, "music_discover")
    covers = {r.title: r.image_url for r in discover}
    assert covers["Example New X"].endswith("/newx.png")

    # Without keys it recomputes from the cache and says what it skipped.
    messages.clear()
    counts = runner.refresh_recommendations(
        conn, "me", store=SecretStore(backend=FakeKeyring()), out=messages.append, now=NOW_DT
    )
    assert counts["anime"] == 4
    assert "skipping MyAnimeList" in messages[0]


def test_refresh_without_data_is_empty_not_an_error(conn):
    counts = runner.refresh_recommendations(
        conn, "me", store=SecretStore(backend=FakeKeyring()), out=quiet,
        now=datetime(2026, 10, 1, tzinfo=timezone.utc),
    )  # fmt: skip
    assert counts == {"anime": 0, "music_discover": 0, "music_rediscover": 0}


def test_reasons_read_like_sentences(conn, anime_world):
    _, recs = anime_keys(conn)
    assert recs[206].reasons == ["Because you really liked Example Action F."]
    assert recs[206].facts["details"] == ["1 MAL user who liked Example Action F recommends this."]
