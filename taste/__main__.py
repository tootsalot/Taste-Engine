"""Command line entry point: `python -m taste <command>`."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from taste import __version__, db, profiles, reports, runner
from taste.config import load_env, reports_dir
from taste.profiles import DEFAULT_PROFILE, ProfileError


def cmd_sync(conn: sqlite3.Connection, profile_id: str, source: str) -> int:
    results = runner.sync_sources(conn, profile_id, source)
    return 0 if all(results.values()) else 1


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


def cmd_status(conn: sqlite3.Connection, label: str) -> int:
    print(f"Profile database: {label}")
    for line in status_lines(conn):
        print(line)
    return 0


def cmd_profiles(args: argparse.Namespace) -> int:
    if args.action == "create":
        profile = profiles.create(args.profile_id, args.name or "")
        print(f"Created profile {profile.profile_id!r} at {profile.path}")
        return 0
    found = profiles.list_profiles()
    if not found:
        print("No profiles yet. Create one with `python -m taste profiles create <id>`.")
    for p in found:
        print(f"  {p.profile_id:<20} {p.display_name}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m taste", description="Personal taste engine: sync, report, status."
    )
    parser.add_argument("--version", action="version", version=f"taste-engine {__version__}")
    parser.add_argument(
        "--profile",
        default=DEFAULT_PROFILE,
        help=f"which profile to use (default {DEFAULT_PROFILE!r})",
    )
    parser.add_argument(
        "--db", type=Path, help="use this SQLite file instead of the profile's database"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sync = sub.add_parser("sync", help="pull data from a source")
    sync.add_argument("source", choices=["mal", "lastfm", "all"])
    rep = sub.add_parser("report", help="print a summary and write CSV files")
    rep.add_argument("--out", type=Path, help="folder for CSV files (default reports/<profile>)")
    sub.add_parser("status", help="row counts and last sync per source")
    prof = sub.add_parser("profiles", help="list or create profiles")
    prof_sub = prof.add_subparsers(dest="action")
    create = prof_sub.add_parser("create", help="create a profile")
    create.add_argument("profile_id")
    create.add_argument("--name", help="display name")
    return parser


def _open(args: argparse.Namespace) -> tuple[sqlite3.Connection, str]:
    if args.db:
        return db.connect(args.db), str(args.db)
    profiles.validate_id(args.profile)
    if not profiles.exists(args.profile):
        if args.profile != DEFAULT_PROFILE:
            raise ProfileError(
                f"No profile named {args.profile!r}. Create it with "
                f"`python -m taste profiles create {args.profile}` or in the app."
            )
        profiles.create(DEFAULT_PROFILE)
    return profiles.open_profile(args.profile), str(profiles.db_path(args.profile))


def main(argv: list[str] | None = None) -> int:
    # Windows consoles can choke on Japanese titles; replace what they can't show.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")

    args = build_parser().parse_args(argv)
    load_env()
    if profiles.migrate_legacy():
        print("Moved data/taste.db to the 'default' profile.")
    try:
        if args.command == "profiles":
            return cmd_profiles(args)
        conn, label = _open(args)
    except ProfileError as exc:
        print(exc, file=sys.stderr)
        return 2
    try:
        if args.command == "sync":
            return cmd_sync(conn, args.profile, args.source)
        if args.command == "report":
            return cmd_report(conn, args.out or reports_dir() / args.profile)
        return cmd_status(conn, label)
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
