from taste import __main__ as cli
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


def test_sync_reports_missing_settings(tmp_path, monkeypatch, capsys):
    for name in ("MAL_CLIENT_ID", "MAL_USERNAME", "LASTFM_API_KEY", "LASTFM_USERNAME"):
        monkeypatch.setenv(name, "")
    monkeypatch.setattr(cli, "load_env", lambda: None)
    assert cli.main(["--db", str(tmp_path / "x.db"), "sync", "all"]) == 1
    err = capsys.readouterr().err
    assert "MAL_CLIENT_ID" in err
    assert "LASTFM_API_KEY" in err
