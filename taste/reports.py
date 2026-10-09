"""Phase 1 reports: query the rpt_* views, print a short summary, write CSVs."""

from __future__ import annotations

import csv
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Report:
    view: str
    order_by: str
    title: str
    uses_lastfm: bool = False

    @property
    def csv_name(self) -> str:
        return self.view.removeprefix("rpt_") + ".csv"


SCOPE_PHRASES = {
    "desktop_only": "desktop only",
    "mobile_only": "phone only",
    "all_devices": "all devices",
    "unknown": "devices unknown",
    "mixed": "mixed capture scopes",
}

REPORTS = [
    Report("rpt_mal_critic_summary", "shows_scored", "How critical am I?"),
    Report("rpt_mal_score_vs_community", "score_diff, title", "My score vs the community"),
    Report("rpt_mal_genre_vs_community", "avg_diff DESC, genre", "Genres vs the community"),
    Report(
        "rpt_mal_dropped_on_hold", "list_status, pct_complete DESC, title", "Dropped and on hold"
    ),
    Report("rpt_lastfm_top_artists_all_time", "play_rank", "Top artists, all time", True),
    Report(
        "rpt_lastfm_top_artists_by_year", "local_year DESC, play_rank", "Top artists by year", True
    ),
    Report(
        "rpt_lastfm_top_artists_by_month",
        "local_month DESC, play_rank",
        "Top artists by month",
        True,
    ),
    Report("rpt_lastfm_top_tracks_all_time", "play_rank", "Top tracks, all time", True),
    Report(
        "rpt_lastfm_top_tracks_by_year", "local_year DESC, play_rank", "Top tracks by year", True
    ),
    Report(
        "rpt_lastfm_top_tracks_by_month", "local_month DESC, play_rank", "Top tracks by month", True
    ),
    Report("rpt_lastfm_by_hour", "local_hour", "Plays by hour of day", True),
    Report("rpt_lastfm_by_weekday", "local_weekday_num", "Plays by day of week", True),
]


def write_csv(conn: sqlite3.Connection, report: Report, path: Path) -> int:
    """Write one report view to `path`. Returns the row count."""
    cursor = conn.execute(f"SELECT * FROM {report.view} ORDER BY {report.order_by}")
    columns = [d[0] for d in cursor.description]
    rows = cursor.fetchall()
    # utf-8-sig so Excel on Windows shows Japanese titles correctly.
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        writer.writerows(tuple(row) for row in rows)
    return len(rows)


def write_csvs(conn: sqlite3.Connection, out_dir: Path) -> list[tuple[Path, int]]:
    """Write one CSV per report view. Returns (path, row count) for each."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for report in REPORTS:
        path = out_dir / report.csv_name
        written.append((path, write_csv(conn, report, path)))
    return written


def summary_lines(conn: sqlite3.Connection) -> list[str]:
    lines = ["MyAnimeList: how critical am I?"]
    s = conn.execute("SELECT * FROM rpt_mal_critic_summary").fetchone()
    if not s or not s["shows_scored"]:
        lines.append("  No scored anime yet. Run `python -m taste sync mal` first.")
    else:
        direction = "below" if s["avg_diff"] < 0 else "above"
        lines.append(
            f"  {s['shows_scored']} scored shows. My average is {s['my_avg_score']:.2f}, "
            f"the community's is {s['community_avg_score']:.2f}."
        )
        lines.append(
            f"  On average I score {abs(s['avg_diff']):.2f} points {direction} the community "
            f"(below on {s['share_scored_below']:.0%} of shows, "
            f"above on {s['share_scored_above']:.0%})."
        )
        genres = conn.execute(
            "SELECT genre, shows_scored, avg_diff FROM rpt_mal_genre_vs_community "
            "ORDER BY avg_diff DESC, genre"
        ).fetchall()
        if genres:
            top = ", ".join(_genre_label(g) for g in genres[:5])
            bottom = ", ".join(_genre_label(g) for g in reversed(genres[-5:]))
            lines.append(f"  Genres where I'm most generous (min 5 shows): {top}")
            lines.append(f"  Genres where I'm harshest (min 5 shows): {bottom}")

    dropped = conn.execute(
        "SELECT list_status, COUNT(*) AS n, AVG(pct_complete) AS avg_pct "
        "FROM rpt_mal_dropped_on_hold GROUP BY list_status ORDER BY list_status"
    ).fetchall()
    if dropped:
        parts = []
        for row in dropped:
            pct = f", {row['avg_pct']:.0f}% of the way through on average" if row["avg_pct"] else ""
            parts.append(f"{row['n']} {row['list_status'].replace('_', ' ')}{pct}")
        lines.append(f"  Shows I stopped: {'; '.join(parts)}.")

    lines.append("")
    note = conn.execute("SELECT scope_note FROM core_sources WHERE source = 'lastfm'").fetchone()
    lines.append("Last.fm listening")
    lines.append(f"  NOTE: {note[0]}")
    total = conn.execute("SELECT COUNT(*) FROM rpt_lastfm_plays_local").fetchone()[0]
    if not total:
        lines.append("  No scrobbles yet. Run `python -m taste sync lastfm` first.")
        return lines
    span = conn.execute(
        "SELECT MIN(occurred_at_local), MAX(occurred_at_local) FROM rpt_lastfm_plays_local"
    ).fetchone()
    scope = conn.execute("SELECT capture_scopes FROM rpt_lastfm_scope").fetchone()[0]
    tz_name = conn.execute("SELECT timezone FROM rpt_settings").fetchone()[0]
    lines.append(
        f"  {total:,} plays ({SCOPE_PHRASES.get(scope, scope)}) from {span[0][:10]} "
        f"to {span[1][:10]}, times in {tz_name}."
    )
    artists = conn.execute(
        "SELECT artist, plays FROM rpt_lastfm_top_artists_all_time ORDER BY play_rank LIMIT 5"
    ).fetchall()
    lines.append("  Top artists: " + ", ".join(f"{a['artist']} ({a['plays']})" for a in artists))
    tracks = conn.execute(
        "SELECT track, artist, plays FROM rpt_lastfm_top_tracks_all_time ORDER BY play_rank LIMIT 5"
    ).fetchall()
    lines.append(
        "  Top tracks: "
        + ", ".join(f"{t['track']} by {t['artist']} ({t['plays']})" for t in tracks)
    )
    hour = conn.execute(
        "SELECT local_hour, pct_of_plays FROM rpt_lastfm_by_hour "
        "ORDER BY plays DESC, local_hour LIMIT 1"
    ).fetchone()
    day = conn.execute(
        "SELECT weekday, pct_of_plays FROM rpt_lastfm_by_weekday "
        "ORDER BY plays DESC, local_weekday_num LIMIT 1"
    ).fetchone()
    lines.append(
        f"  Busiest hour: {hour['local_hour']:02d}:00 ({hour['pct_of_plays']}% of plays). "
        f"Busiest day: {day['weekday']} ({day['pct_of_plays']}% of plays)."
    )
    return lines


def run(conn: sqlite3.Connection, out_dir: Path, out: Callable[[str], None] = print) -> None:
    for line in summary_lines(conn):
        out(line)
    written = write_csvs(conn, out_dir)
    out("")
    out(f"Wrote {len(written)} CSV files to {out_dir}:")
    for path, count in written:
        out(f"  {path.name} ({count} {'row' if count == 1 else 'rows'})")


def _genre_label(genre: sqlite3.Row) -> str:
    # Adding 0.0 turns SQLite's -0.0 into 0.0, so a tiny negative prints as +0.00.
    return f"{genre['genre']} ({genre['avg_diff'] + 0.0:+.2f}, n={genre['shows_scored']})"
