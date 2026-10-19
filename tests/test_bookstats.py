"""The Books page's numbers (taste.bookstats), on the made-up Hardcover library."""

from dataclasses import replace

from taste import bookstats
from taste.desktop.books_view import book_tiles
from taste.sources import hardcover
from tests.fake_hardcover import ANA, FakeHardcover, book, entry


def synced(conn, make_client):
    fake = FakeHardcover()
    client, _ = make_client(fake)
    hardcover.sync(conn, client, fake.access, lambda m: None)


def test_all_time_counts_every_read(conn, make_client):
    synced(conn, make_client)
    s = bookstats.load(conn)
    assert s.years == ["2026", "2025", "2019"]
    # Lantern Road twice, Quiet Harbor, Field Guide, Old Favorite, and one undated.
    assert (s.reads, s.unique, s.undated) == (6, 5, 1)
    assert s.rereads == 1 and s.most_reread == ("Example Lantern Road", 2)
    assert s.covers[0].title == "Example Undated Read"  # undated sorts as newest
    assert next(c for c in s.covers if c.title == "Example Lantern Road").times == 2
    assert (s.audiobooks, s.audio_hours) == (1, 10.0)
    assert s.pages == 320 * 3 + 210 + 320 and s.pages_known == 5
    assert s.authors == 3 and s.new_authors is None
    assert s.my_average == round((4.5 + 3.0 + 4.0 + 5.0 + 2.5) / 5, 2)
    assert s.stars[8] == 1 and s.stars[4] == 1  # one 4.5 and one 2.5
    assert {k.name: k.books for k in s.kinds} == {"Fiction": 4, "Nonfiction": 1}
    assert {f.name: f.books for f in s.formats} == {"Not set": 4, "Audio": 1}
    assert {f.name: f.books for f in s.starred} == {"Starred": 5}  # every read one has stars
    assert s.genres[0].name == "Fantasy" and s.genres[0].books == 4
    assert s.per_year == [
        (str(y), {2019: 1, 2025: 1, 2026: 3}.get(y, 0)) for y in range(2019, 2027)
    ]
    lead = s.top_authors[0]
    assert (lead.name, lead.books) == ("Ana Example", 3)
    assert lead.ratings == [4.5, 3.0, 4.5]  # in the order read
    assert [c.title for c in lead.covers] == ["Example Lantern Road", "Example Quiet Harbor"]


def test_one_year(conn, make_client):
    synced(conn, make_client)
    s = bookstats.load(conn, "2026")
    assert (s.reads, s.unique, s.undated) == (3, 2, 0)
    assert s.new_authors == 1  # Ana, first read this year
    assert s.rereads == 1
    earlier = bookstats.load(conn, "2025")
    assert earlier.reads == 1 and earlier.covers[0].title == "Example Field Guide"
    assert earlier.new_authors == 1


def test_an_empty_library(conn):
    s = bookstats.load(conn)
    assert (s.reads, s.unique, s.years, s.per_year) == (0, 0, [], [])
    assert s.my_average is None and s.top_authors == [] and s.bands == []


AUDIO = {"id": 78, "pages": None, "audio_seconds": None, "reading_format_id": 2}


def listened(user_book_id: int, title: str, edition: dict) -> dict:
    return entry(
        user_book_id,
        book(user_book_id, title, ANA, pages=None),
        rating=4.0,
        reads=[(user_book_id * 10, None, "2026-02-01")],
        edition=edition,
    )


def test_an_audiobook_with_no_length_is_unknown_not_zero(conn, make_client):
    fake = FakeHardcover([listened(301, "Example Long Listen", AUDIO)])
    client, _ = make_client(fake)
    hardcover.sync(conn, client, fake.access, lambda m: None)
    s = bookstats.load(conn)
    assert (s.audiobooks, s.audio_hours, s.audio_unknown) == (1, None, 1)
    hours = book_tiles(s)[2]
    assert (hours.value, hours.caption) == ("1", "audiobook")
    assert hours.detail == "length not on Hardcover"


def test_hours_count_the_known_lengths_and_say_how_many_are_missing(conn, make_client):
    known = dict(AUDIO, audio_seconds=7200)
    fake = FakeHardcover(
        [listened(301, "Example Long Listen", AUDIO), listened(302, "Example Two Hours", known)]
    )
    client, _ = make_client(fake)
    hardcover.sync(conn, client, fake.access, lambda m: None)
    s = bookstats.load(conn)
    assert (s.audiobooks, s.audio_hours, s.audio_unknown) == (2, 2.0, 1)
    hours = book_tiles(s)[2]
    assert (hours.value, hours.caption) == ("2", "hours listened")
    assert hours.detail == "2 audiobooks, 1 length unknown"


def test_one_reread_is_singular(conn, make_client):
    synced(conn, make_client)
    s = bookstats.load(conn)
    rereads = book_tiles(s)[5]
    assert (rereads.value, rereads.caption) == ("1", "reread")
    assert book_tiles(replace(s, rereads=2))[5].caption == "rereads"


def test_your_stars_against_hardcover_by_band(conn, make_client):
    synced(conn, make_client)
    s = bookstats.load(conn)
    # Five rated books, each with a Hardcover average: grouped by quarter star.
    assert sum(b.shows for b in s.bands) == 5
    assert all(b.mal_to - b.mal_from == 0.25 for b in s.bands)


def test_you_vs_hardcover_compares_the_same_books(conn, make_client):
    """A rated book Hardcover has no average for used to count in my side of the
    difference only, so "vs Hardcover" mixed two sets of books."""
    from statistics import mean

    from taste import overview

    synced(conn, make_client)
    conn.execute(
        "UPDATE stg_hardcover_books SET rating = NULL WHERE title = 'Example Quiet Harbor'"
    )
    s = bookstats.load(conn)
    pairs = [
        (r[0], r[1])
        for r in conn.execute(
            "SELECT DISTINCT ub.user_book_id, ub.rating, b.rating FROM stg_hardcover_user_books ub "
            "JOIN stg_hardcover_books b ON b.book_id = ub.book_id "
            "WHERE ub.rating IS NOT NULL AND b.rating IS NOT NULL AND ub.removed_at IS NULL "
            "AND ub.status_id = 3"
        ).fetchall()
        for r in [r[1:]]
    ]
    assert s.vs_hardcover == round(mean(mine - crowd for mine, crowd in pairs), 2)
    books = next(n for n in overview.vs_crowd(conn) if n.kind == "books")
    assert books.value == f"{s.vs_hardcover:+.1f}★"
