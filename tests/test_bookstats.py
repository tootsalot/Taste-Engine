"""The Books page's numbers (taste.bookstats), on the made-up Hardcover library."""

from taste import bookstats
from taste.sources import hardcover
from tests.fake_hardcover import FakeHardcover


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
    assert s.genres[0].name == "Fantasy" and s.genres[0].books == 4
    assert s.per_year == [
        (str(y), {2019: 1, 2025: 1, 2026: 3}.get(y, 0)) for y in range(2019, 2027)
    ]
    assert dict(s.lengths)["301-400"] == 3 and dict(s.lengths)["201-300"] == 1
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
    assert s.my_average is None and s.top_authors == [] and s.average_pages is None
