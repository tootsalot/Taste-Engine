"""Cross-media title reasons require recorded item identity or a direct item link."""

import pytest

from taste import core, settings
from taste.recommend import links

SOURCE_IDS = {
    "anime": ("mal", "mal_anime_id", "anime", 10),
    "film": ("tmdb", "tmdb_movie_id", "film", 10),
    "tv": ("tmdb", "tmdb_tv_id", "tv_show", 10),
    "book": ("hardcover", "hardcover_book_id", "book", 5),
}


def item(conn, kind, key, title, rating=None):
    source, id_type, media, maximum = SOURCE_IDS[kind]
    result = core.upsert_item(
        conn,
        source=source,
        id_type=id_type,
        external_id=str(key),
        media_type=media,
        title=title,
    )
    if rating is not None:
        core.upsert_rating(
            conn,
            item_id=result,
            source=source,
            raw_score=rating,
            scale_min=1,
            scale_max=maximum,
            source_updated_at=None,
        )
    return result


def test_shared_title_people_and_source_material_never_create_a_link(conn):
    candidate = item(conn, "anime", 1, "Invented Paper River")
    book = item(conn, "book", 2, "Invented Paper River", 4.5)
    for item_id, source in ((candidate, "mal"), (book, "hardcover")):
        person = core.upsert_creator(
            conn, source=source, id_type="example_person_id", external_id="1", name="Jo Example"
        )
        core.link_item_creator(conn, item_id, person, "author", source)
    conn.execute(
        "INSERT INTO stg_mal_anime (mal_anime_id, title, source_material, loaded_at) "
        "VALUES (1, 'Invented Paper River', 'novel', '2026-10-14 18:00:00')"
    )
    assert links.load(conn).get(("anime", "1")) is None


@pytest.mark.parametrize("reverse", (False, True))
def test_direct_recorded_link_names_the_rated_other_medium_with_native_rating(conn, reverse):
    anime = item(conn, "anime", 1, "Invented Animated Harbor")
    book = item(conn, "book", 2, "Invented Paper River", 4.5)
    parent, child = (book, anime) if reverse else (anime, book)
    core.link_items(conn, parent, child, "adaptation", "mal")
    assert links.load(conn)["anime", "1"] == (
        'Linked to "Invented Paper River", which you rated 4.5/5 stars in books.'
    )


def test_recorded_cross_source_identity_needs_no_title_match(conn):
    book = item(conn, "book", 2, "Invented Paper River", 4)
    core.add_item_external_id(conn, book, "mal", "mal_anime_id", "1")
    assert links.load(conn)["anime", "1"] == (
        'Linked to "Invented Paper River", which you rated 4/5 stars in books.'
    )


@pytest.mark.parametrize(
    ("kind", "score", "expected"),
    (("anime", 9, "9/10 in anime"), ("film", 8.5, "8.5/10 in films"), ("tv", 8, "8/10 in TV")),
)
def test_rating_source_and_other_medium_are_named_without_watched_wording(
    conn, kind, score, expected
):
    book = item(conn, "book", 1, "Invented Book Candidate")
    other = item(conn, kind, 2, "Invented Rated Title", score)
    core.link_items(conn, book, other, "adaptation", "hardcover")
    reason = links.load(conn)["book", "1"]
    assert expected in reason
    assert "rated" in reason and "watched" not in reason


def test_same_medium_unrated_and_transitive_neighbours_never_supply_a_title_reason(conn):
    candidate = item(conn, "anime", 1, "Invented Candidate")
    same = item(conn, "anime", 2, "Invented Same Medium", 9)
    unrated = item(conn, "book", 3, "Invented Unrated Book")
    distant = item(conn, "film", 4, "Invented Distant Film", 10)
    core.link_items(conn, candidate, same, "sequel", "mal")
    core.link_items(conn, candidate, unrated, "adaptation", "mal")
    core.link_items(conn, unrated, distant, "adaptation", "hardcover")
    assert links.load(conn).get(("anime", "1")) is None


def test_hidden_adult_ratings_never_supply_a_named_reason(conn):
    book = item(conn, "book", 1, "Invented Book Candidate")
    adult = item(conn, "anime", 2, "Invented Hidden Adult Title", 10)
    film = item(conn, "film", 3, "Invented Visible Film", 8)
    conn.execute(
        "INSERT INTO stg_mal_anime (mal_anime_id, title, nsfw_rating, loaded_at) "
        "VALUES (2, 'Invented Hidden Adult Title', 'black', '2026-10-14 18:00:00')"
    )
    for other in (adult, film):
        core.link_items(conn, book, other, "adaptation", "hardcover")
    settings.set_value(conn, "include_nsfw", False)
    before = conn.execute("SELECT COUNT(*) FROM core_ratings").fetchone()[0]
    reasons = links.load(conn)
    assert "Invented Visible Film" in reasons["book", "1"]
    assert "Invented Hidden Adult Title" not in str(reasons)
    assert conn.execute("SELECT COUNT(*) FROM core_ratings").fetchone()[0] == before == 2
    settings.set_value(conn, "include_nsfw", True)
    assert "Invented Hidden Adult Title" in links.load(conn)["book", "1"]


def test_hidden_adult_candidates_have_no_reason_index_entry(conn):
    adult = item(conn, "anime", 1, "Invented Hidden Candidate")
    book = item(conn, "book", 2, "Invented Rated Book", 5)
    core.link_items(conn, adult, book, "adaptation", "mal")
    conn.execute(
        "INSERT INTO stg_mal_anime (mal_anime_id, title, nsfw_rating, loaded_at) "
        "VALUES (1, 'Invented Hidden Candidate', 'black', '2026-10-14 18:00:00')"
    )
    settings.set_value(conn, "include_nsfw", False)
    assert links.load(conn).get(("anime", "1")) is None


def test_index_is_read_only_and_lookups_do_not_query_sql(conn):
    candidate = item(conn, "anime", 1, "Invented Candidate")
    book = item(conn, "book", 2, "Invented Rated Book", 4)
    core.link_items(conn, candidate, book, "adaptation", "mal")
    before = conn.total_changes
    statements = []
    conn.set_trace_callback(statements.append)
    try:
        reasons = links.load(conn)
        queries = len(statements)
        for _ in range(100):
            assert reasons["anime", "1"]
        assert len(statements) == queries <= 8
        assert conn.total_changes == before
    finally:
        conn.set_trace_callback(None)
