"""Malformed stored metadata never crashes a plain accuracy note."""

from datetime import date

import pytest

from taste import core
from taste.recommend import comparison, lab
from taste.sync_log import SyncRun


@pytest.mark.parametrize("valid", (False, True))
def test_lab_skips_bad_rating_dates_and_keeps_the_latest_valid_reference(conn, valid):
    stamps = ["not-a-date", "2026-10-20 12:00:00"] if valid else ["not-a-date"]
    for key, stamp in enumerate(stamps, 1):
        item = core.upsert_item(
            conn,
            source="mal",
            id_type="mal_anime_id",
            external_id=str(key),
            media_type="anime",
            title=f"Invented show {key}",
        )
        core.upsert_rating(
            conn,
            item_id=item,
            source="mal",
            raw_score=7,
            scale_min=1,
            scale_max=10,
            source_updated_at=stamp,
        )
    _, _, context = lab.load_data(conn, "anime")
    assert context.now == (date(2026, 10, 20) if valid else date(1970, 1, 1))
    assert comparison.saved_accuracy(conn, "anime", "usual")["count"] == 0


@pytest.mark.parametrize(("genres", "people"), (("{broken", "[]"), ("[]", '["Invented person"]')))
def test_saved_accuracy_safely_withholds_malformed_facts_without_writes(conn, genres, people):
    conn.execute(
        "INSERT INTO stg_tmdb_movies "
        "(tmdb_id, title, fetched_at, genres_json, directors_json) VALUES (?, ?, ?, ?, ?)",
        (1, "Invented film", "2026-10-14 18:00:00", genres, people),
    )
    statements = []
    conn.set_trace_callback(statements.append)
    assert comparison.saved_accuracy(conn, "film", "usual") is None
    conn.set_trace_callback(None)
    assert not any(statement.startswith(("INSERT", "UPDATE", "DELETE")) for statement in statements)


@pytest.mark.parametrize(
    ("model", "similar", "moods", "pages"),
    (
        ("similar", "{broken", "[]", 200),
        ("details", "[]", "{broken", 200),
        ("details", "[]", "[]", "not-a-number"),
    ),
)
def test_saved_accuracy_checks_malformed_fingerprint_metadata_safely(
    conn, model, similar, moods, pages
):
    run = SyncRun(conn, "hardcover", "full")
    for key in range(1, 21):
        conn.execute(
            "INSERT INTO stg_hardcover_books "
            "(book_id, title, rating, updated_at, similar_json, moods_json, pages) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (key, f"Invented book {key}", 3.5, "2026-10-14 18:00:00", similar, moods, pages),
        )
        conn.execute(
            "INSERT INTO stg_hardcover_user_books "
            "(user_book_id, book_id, status_id, rating, first_seen_at, last_seen_run_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (key, key, 3, 4.0, "2026-10-14 18:00:00", run.run_id),
        )
    assert comparison.saved_accuracy(conn, "book", model) is None
