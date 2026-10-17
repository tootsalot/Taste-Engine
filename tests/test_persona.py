"""The Your taste card's words (taste.persona), from made-up data only."""

from taste import persona
from taste.sources import hardcover
from tests.fake_hardcover import FakeHardcover


def test_too_little_data_says_nothing(conn):
    assert persona.load(conn) is None


def test_traits_need_enough_data_and_carry_their_numbers(conn, make_client):
    fake = FakeHardcover()
    client, _ = make_client(fake)
    hardcover.sync(conn, client, fake.access, lambda m: None)
    traits, books, genre = persona._books(conn)
    assert books == 5 and genre == "Fantasy"
    assert traits[0].title == "Fantasy reader" and traits[0].evidence == "80% of your books"
    assert persona.load(conn) is None  # one medium, one trait: not enough for a card


def lean(title, part, kind, diff):
    return persona.Trait(title, f"{part} on average", kind, abs(diff), "", diff, part)


def test_critics_across_media_fold_into_one_trait():
    traits = [
        lean("Tough grader", "0.9 below MAL", "anime", -0.9),
        lean("Tough on films", "2.0 below TMDB", "screen", -2.0),
        lean("Hard to impress on the page", "1.1★ below Hardcover", "books", -1.1),
        persona.Trait("Night owl", "41% of plays between 10 PM and 4 AM", "music", 0.41),
    ]
    merged = persona.merge_critics(traits)
    critic = next(t for t in merged if t.title == "Tough critic")
    assert critic.evidence == "0.9 below MAL · 2.0 below TMDB · 1.1★ below Hardcover"
    assert critic.kind == "critic" and critic.adjective == "tough-grading"
    assert [t.title for t in merged].count("Tough critic") == 1 and len(merged) == 2


def test_one_critical_medium_keeps_its_own_trait():
    traits = [
        lean("Tough grader", "0.9 below MAL", "anime", -0.9),
        lean("Easy to please at the movies", "0.6 above TMDB", "screen", 0.6),
    ]
    merged = persona.merge_critics(traits)
    assert [t.evidence for t in merged] == [
        "0.9 below MAL on average",
        "0.6 above TMDB on average",
    ]
