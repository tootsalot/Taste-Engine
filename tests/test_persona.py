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
