"""Expanded mixed themes stay separate from the existing per-list experiment."""

from taste import core, themes
from tests.fill_world import GENRES, World


def test_expanded_map_covers_invented_taxonomy_without_changing_legacy_map():
    assert not themes.mapped("mal", ["Detective", "Time Travel"])
    assert themes.mapped("mal", ["Detective", "Time Travel"], mixed=True) == [
        "mystery",
        "time travel",
    ]
    assert not themes.mapped("mal", ["Award Winning"], mixed=True)
    assert themes.mapped("mal", ["Seinen"], mixed=True) == ["adult audience"]
    assert themes.mapped("mal", ["Ecchi", "Hentai"], mixed=True) == ["sexual content"]
    assert themes.mapped("mal", ["Hentai"]) == ["romance"]
    assert all(themes.mapped("mal", [term], mixed=True) for term in GENRES)
    world = World()
    for entry in world.hardcover.library:
        for kind in ("Genre", "Mood"):
            assert all(
                themes.mapped("hardcover", [tag["tag"]], mixed=True)
                for tag in entry["book"]["cached_tags"][kind]
            )
    for entry in world.tmdb.catalog.values():
        assert all(
            themes.mapped("tmdb", [tag["name"]], mixed=True) for tag in entry.get("genres", [])
        )


def test_coverage_counts_hidden_and_missing_crowd_ratings_by_recorded_source(conn):
    item = core.upsert_item(
        conn,
        source="mal",
        id_type="mal_anime_id",
        external_id="1",
        media_type="anime",
        title="Invented private adult title",
    )
    core.add_item_external_id(conn, item, "tmdb", "tmdb_movie_id", "2")
    for source in ("mal", "tmdb"):
        core.upsert_rating(
            conn,
            item_id=item,
            source=source,
            raw_score=8,
            scale_min=1,
            scale_max=10,
            source_updated_at=None,
        )
        core.set_item_tags(conn, item, source, [core.tag_id(conn, "Detective", "genre")])
    before = conn.total_changes
    result = themes.coverage(conn)
    assert result["media"]["anime"] == {
        "titles": 1,
        "mapped_titles": 1,
        "tags": 1,
        "mapped_tags": 1,
    }
    assert result["media"]["film"] == result["media"]["anime"]
    assert result["media"]["book"]["titles"] == 0
    assert "Invented private adult title" not in str(result)
    assert conn.total_changes == before


def test_provider_taxonomy_inventory_has_explicit_mapping_or_reason_to_omit():
    mal = """Action|Adventure|Avant Garde|Award Winning|Boys Love|Comedy|Drama|Fantasy|
    Girls Love|Gourmet|Horror|Mystery|Romance|Sci-Fi|Slice of Life|Sports|Supernatural|
    Suspense|Ecchi|Erotica|Hentai|Adult Cast|Anthropomorphic|CGDCT|Childcare|Combat Sports|
    Crossdressing|Delinquents|Detective|Educational|Gag Humor|Gore|Harem|High Stakes Game|
    Historical|Idols (Female)|Idols (Male)|Isekai|Iyashikei|Love Polygon|Love Status Quo|
    Magical Sex Shift|Mahou Shoujo|Martial Arts|Mecha|Medical|Military|Music|Mythology|
    Organized Crime|Otaku Culture|Parody|Performing Arts|Pets|Psychological|Racing|
    Reincarnation|Reverse Harem|Romantic Subtext|Samurai|School|Showbiz|Space|
    Strategy Game|Super Power|Survival|Team Sports|Time Travel|Vampire|Video Game|
    Villainess|Visual Arts|Workplace|Josei|Kids|Seinen|Shoujo|Shounen"""
    tmdb = """Action|Adventure|Animation|Comedy|Crime|Documentary|Drama|Family|Fantasy|
    History|Horror|Music|Mystery|Romance|Science Fiction|TV Movie|Thriller|War|Western|
    Action & Adventure|Kids|News|Reality|Sci-Fi & Fantasy|Soap|Talk|War & Politics"""
    for source, inventory in (("mal", mal), ("tmdb", tmdb)):
        for term in inventory.split("|"):
            assert (
                themes.mapped(source, [term], mixed=True)
                or term.strip().casefold() in themes.UNMAPPED
            ), term


def test_unmapped_terms_are_counted_and_limited_to_twenty(conn):
    item = core.upsert_item(
        conn,
        source="hardcover",
        id_type="hardcover_book_id",
        external_id="1",
        media_type="book",
        title="Invented private book",
    )
    core.upsert_rating(
        conn,
        item_id=item,
        source="hardcover",
        raw_score=4,
        scale_min=0.5,
        scale_max=5,
        source_updated_at=None,
    )
    core.set_item_tags(
        conn,
        item,
        "hardcover",
        [core.tag_id(conn, f"Invented genre {index:02}", "genre") for index in range(25)],
    )
    result = themes.coverage(conn)
    assert len(result["unmapped"]) == 20
    assert result["unmapped"][0] == ("invented genre 00", 1)
    assert result["media"]["book"]["mapped_titles"] == 0
    assert result["media"]["book"]["tags"] == 25
