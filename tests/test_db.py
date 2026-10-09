from taste import db
from taste.sync_log import INTERRUPTED_MESSAGE, SyncRun


def test_schema_is_idempotent(tmp_path):
    path = tmp_path / "taste.db"
    db.connect(path).close()
    conn = db.connect(path)  # second run must not fail or duplicate seed rows
    sources = [r["source"] for r in conn.execute("SELECT source FROM core_sources ORDER BY 1")]
    assert sources == ["lastfm", "mal"]


def test_lastfm_scope_note_says_desktop_only(conn):
    row = conn.execute(
        "SELECT default_capture_scope, scope_note FROM core_sources WHERE source = 'lastfm'"
    ).fetchone()
    assert row["default_capture_scope"] == "desktop_only"
    assert "Desktop listening only" in row["scope_note"]


def test_stale_running_row_is_marked_failed(conn):
    first = SyncRun(conn, "mal", "full")  # never finished, like a killed process
    SyncRun(conn, "mal", "full")
    row = conn.execute(
        "SELECT status, error_message FROM sync_runs WHERE sync_run_id = ?", (first.run_id,)
    ).fetchone()
    assert row["status"] == "failed"
    assert row["error_message"] == INTERRUPTED_MESSAGE


def test_transaction_rolls_back(conn):
    try:
        with db.transaction(conn):
            SyncRun(conn, "mal", "full")
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert conn.execute("SELECT COUNT(*) FROM sync_runs").fetchone()[0] == 0


def test_time_helpers():
    assert db.iso_to_utc("2025-03-14T08:54:27+00:00") == "2025-03-14 08:54:27"
    assert db.iso_to_utc("2025-03-14T01:54:27-07:00") == "2025-03-14 08:54:27"
    assert db.unix_to_utc(0) == "1970-01-01 00:00:00"
