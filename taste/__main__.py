"""Command line entry point: `python -m taste <command>`."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections.abc import Callable
from pathlib import Path

from taste import db, reports
from taste.config import (
    DEFAULT_DB_PATH,
    REPORTS_DIR,
    ConfigError,
    lastfm_settings,
    load_env,
    mal_settings,
)
from taste.http_client import HttpClient
from taste.sources import lastfm, mal


def _sync_mal(conn: sqlite3.Connection) -> bool:
    settings = mal_settings()
    client = HttpClient(secrets=[settings.client_id])
    return mal.sync(conn, client, settings).status == "success"


def _sync_lastfm(conn: sqlite3.Connection) -> bool:
    settings = lastfm_settings()
    client = HttpClient(secrets=[settings.api_key])
    return lastfm.sync(conn, client, settings).status == "success"


SYNCS: dict[str, Callable[[sqlite3.Connection], bool]] = {
    "mal": _sync_mal,
    "lastfm": _sync_lastfm,
}


def cmd_sync(conn: sqlite3.Connection, source: str) -> int:
    targets = list(SYNCS) if source == "all" else [source]
    ok = True
    for name in targets:
        try:
            ok = SYNCS[name](conn) and ok
        except ConfigError as exc:
            # In `sync all`, a missing setting for one source shouldn't stop the other.
            print(f"{name}: {exc}", file=sys.stderr)
            ok = False
    return 0 if ok else 1


def cmd_report(conn: sqlite3.Connection, out_dir: Path) -> int:
    reports.run(conn, out_dir)
    return 0


def status_lines(conn: sqlite3.Connection) -> list[str]:
    lines = ["Last sync per source (times are UTC):"]
    for source in conn.execute("SELECT source, scope_note FROM core_sources ORDER BY source"):
        name = source["source"]
        last = conn.execute(
            "SELECT * FROM sync_runs WHERE source = ? ORDER BY sync_run_id DESC LIMIT 1", (name,)
        ).fetchone()
        last_ok = conn.execute(
            "SELECT * FROM sync_runs WHERE source = ? AND status = 'success' "
            "ORDER BY sync_run_id DESC LIMIT 1",
            (name,),
        ).fetchone()
        lines.append(f"  {name}")
        if last is None:
            lines.append("    never synced")
            continue
        if last_ok is not None:
            lines.append(
                f"    last success: {last_ok['ended_at']} ({last_ok['mode']}, "
                f"{last_ok['rows_fetched']} fetched, {last_ok['rows_inserted']} new, "
                f"{last_ok['rows_updated']} changed)"
            )
        else:
            lines.append("    last success: none")
        if last_ok is None or last["sync_run_id"] != last_ok["sync_run_id"]:
            lines.append(
                f"    last attempt: {last['started_at']} {last['status']}"
                + (f": {last['error_message']}" if last["error_message"] else "")
            )
        if name == "lastfm":
            open_windows = conn.execute(
                "SELECT COUNT(*) FROM sync_lastfm_windows WHERE status = 'open'"
            ).fetchone()[0]
            if open_windows:
                lines.append(f"    {open_windows} interrupted window(s), next sync resumes them")
            lines.append(f"    note: {source['scope_note']}")

    lines.append("")
    lines.append("Row counts:")
    tables = [
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name"
        )
    ]
    width = max(len(t) for t in tables)
    for table in tables:
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        lines.append(f"  {table:<{width}}  {count:>8,}")
    return lines


def cmd_status(conn: sqlite3.Connection, db_path: Path) -> int:
    print(f"Database: {db_path}")
    for line in status_lines(conn):
        print(line)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m taste", description="Personal taste engine: sync, report, status."
    )
    parser.add_argument(
        "--db", type=Path, default=DEFAULT_DB_PATH, help=f"SQLite file (default {DEFAULT_DB_PATH})"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sync = sub.add_parser("sync", help="pull data from a source")
    sync.add_argument("source", choices=["mal", "lastfm", "all"])
    rep = sub.add_parser("report", help="print a summary and write reports/*.csv")
    rep.add_argument("--out", type=Path, default=REPORTS_DIR, help="folder for CSV files")
    sub.add_parser("status", help="row counts and last sync per source")
    return parser


def main(argv: list[str] | None = None) -> int:
    # Windows consoles can choke on Japanese titles; replace what they can't show.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")

    args = build_parser().parse_args(argv)
    load_env()
    conn = db.connect(args.db)
    try:
        if args.command == "sync":
            return cmd_sync(conn, args.source)
        if args.command == "report":
            return cmd_report(conn, args.out)
        return cmd_status(conn, args.db)
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
