from taste import __main__ as cli
from taste import profiles, settings
from taste.db import connect
from tests.test_reports import seed_lastfm, seed_mal


def test_status_and_report_end_to_end(tmp_path, make_client, capsys):
    db_path = tmp_path / "cli.db"
    conn = connect(db_path)
    seed_mal(conn, make_client)
    seed_lastfm(conn, make_client)
    conn.close()

    assert cli.main(["--db", str(db_path), "status"]) == 0
    out = capsys.readouterr().out
    assert "last success" in out
    assert "Desktop listening only" in out
    assert "stg_lastfm_scrobbles" in out

    out_dir = tmp_path / "reports"
    assert cli.main(["--db", str(db_path), "report", "--out", str(out_dir)]) == 0
    out = capsys.readouterr().out
    assert "how critical am I" in out
    assert "Wrote 12 CSV files" in out
    assert (out_dir / "lastfm_by_hour.csv").exists()


def test_status_on_new_database(tmp_path, capsys):
    assert cli.main(["--db", str(tmp_path / "new.db"), "status"]) == 0
    assert "never synced" in capsys.readouterr().out


def test_sync_reports_missing_settings(monkeypatch, capsys):
    monkeypatch.setattr(cli, "load_env", lambda: None)
    assert cli.main(["sync", "all"]) == 1
    out = capsys.readouterr().out
    assert "MyAnimeList needs: MyAnimeList client ID, username" in out
    assert "Last.fm needs: Last.fm API key, username" in out


def test_default_profile_created_on_first_use(capsys):
    assert cli.main(["status"]) == 0
    assert profiles.exists("default")


def test_unknown_profile_is_an_error(capsys):
    assert cli.main(["--profile", "nobody", "status"]) == 2
    assert "No profile named 'nobody'" in capsys.readouterr().err


def test_profiles_create_and_list(capsys):
    assert cli.main(["profiles", "create", "friend", "--name", "A Friend"]) == 0
    assert cli.main(["profiles"]) == 0
    assert "A Friend" in capsys.readouterr().out
    assert cli.main(["--profile", "Bad/Name", "status"]) == 2


def test_legacy_database_is_migrated(capsys):
    legacy = profiles.legacy_db()
    legacy.parent.mkdir(parents=True, exist_ok=True)
    connect(legacy).close()
    assert cli.main(["status"]) == 0
    assert "Moved data/taste.db" in capsys.readouterr().out
    assert not legacy.exists()
    conn = profiles.open_profile("default")
    assert settings.get(conn, "lastfm_capture_scope") == "desktop_only"
    conn.close()


def test_status_ignores_enrichment_runs(tmp_path):
    conn = connect(tmp_path / "enrich.db")
    from taste.sync_log import SyncRun

    SyncRun(conn, "mal", "full").finish("success")
    SyncRun(conn, "mal", "enrich").finish("failed", "MAL HTTP 503")
    lines = "\n".join(cli.status_lines(conn))
    conn.close()
    assert "last success" in lines and "(full," in lines
    assert "enrich" not in lines and "503" not in lines
