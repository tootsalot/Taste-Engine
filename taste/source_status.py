"""Where each source stands: connected or what it still needs, when it last synced,
and how much it holds. Read by the Settings page's Sources rows and the Dashboard's
status line. Qt-free.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

from taste import credentials, local_time
from taste.runner import SOURCE_LABELS, SOURCES
from taste.secrets_store import SecretStore, SecretStoreError

# Syncs that ended this close together count as one "Sync everything".
SAME_SYNC = timedelta(minutes=10)
KEY_WORDS = {"mal": "client ID", "lastfm": "API key"}
# source -> (its username setting, the environment variable it falls back to)
USERNAMES = {
    "mal": ("mal_username", "MAL_USERNAME"),
    "lastfm": ("lastfm_username", "LASTFM_USERNAME"),
}
KEYS = {"mal": "MAL_CLIENT_ID", "lastfm": "LASTFM_API_KEY", "tmdb": "TMDB_READ_TOKEN"}


@dataclass(frozen=True)
class SourceStatus:
    source: str
    label: str
    connected: bool  # ready to sync
    needs: str  # what's missing before it can sync; "" once connected
    last_sync: str | None  # UTC text of the last successful sync (not suggestion runs)
    items: str  # "400 anime on your list"


@dataclass(frozen=True)
class SyncedLine:
    text: str  # the Dashboard's status line
    tip: str  # each source's own time, one per line
    latest: str | None  # UTC text of the newest sync


def _stored(store: SecretStore, profile_id: str, name: str) -> bool:
    try:
        return bool(store.get(profile_id, name))
    except SecretStoreError:
        return False


def _count(conn: sqlite3.Connection, sql: str) -> int:
    return conn.execute(sql).fetchone()[0]


def _plural(n: int, word: str) -> str:
    return f"{n:,} {word}" + ("" if n == 1 else "s")


def _items(conn: sqlite3.Connection, source: str) -> str:
    if source == "mal":
        n = _count(conn, "SELECT COUNT(*) FROM stg_mal_list_entries WHERE removed_at IS NULL")
        return f"{n:,} anime on your list"
    if source == "lastfm":
        n = _count(
            conn,
            "SELECT COUNT(*) FROM core_behavior_events "
            "WHERE source = 'lastfm' AND event_type = 'play'",
        )
        return _plural(n, "play")
    if source == "tmdb":
        rated = dict(
            conn.execute(
                "SELECT media_type, COUNT(*) FROM stg_tmdb_account_ratings "
                "WHERE removed_at IS NULL AND season = 0 AND episode = 0 GROUP BY media_type"
            ).fetchall()
        )
        parts = [
            _plural(n, word)
            for n, word in ((rated.get("movie", 0), "film"), (rated.get("tv", 0), "show"))
            if n
        ]
        return f"{' and '.join(parts)} rated" if parts else "nothing rated yet"
    n = _count(conn, "SELECT COUNT(*) FROM stg_hardcover_user_books WHERE removed_at IS NULL")
    return f"{_plural(n, 'book')} in your library"


def _needs(conn: sqlite3.Connection, profile_id: str, store: SecretStore, source: str) -> str:
    if source in USERNAMES:
        missing = []
        if not credentials.username(conn, *USERNAMES[source]):
            missing.append("username")
        if not _stored(store, profile_id, KEYS[source]):
            missing.append(KEY_WORDS[source])
        return f"Add your {' and '.join(missing)}" if missing else ""
    if source == "tmdb":
        if not _stored(store, profile_id, KEYS["tmdb"]):
            return "Save a read access token, then connect your account"
        return "" if credentials.tmdb_connected(profile_id, store) else "Connect your account"
    return "" if credentials.hardcover_connected(profile_id, store) else "Not connected"


def last_sync(conn: sqlite3.Connection, source: str) -> str | None:
    """When the source last synced (UTC), leaving out runs that fetch for suggestions."""
    row = conn.execute(
        "SELECT MAX(ended_at) FROM sync_runs WHERE source = ? AND status = 'success' "
        "AND mode <> 'enrich'",
        (source,),
    ).fetchone()
    return row[0] if row else None


def load(conn: sqlite3.Connection, profile_id: str, store: SecretStore) -> list[SourceStatus]:
    """Every source, in the app's order."""
    found = []
    for source in SOURCES:
        needs = _needs(conn, profile_id, store, source)
        found.append(
            SourceStatus(
                source=source,
                label=SOURCE_LABELS[source],
                connected=not needs,
                needs=needs,
                last_sync=last_sync(conn, source),
                items=_items(conn, source),
            )
        )
    return found


def state_line(status: SourceStatus, tz_name: str, now: datetime | None = None) -> str:
    """Like "Connected · synced today at 11:22 PM · 400 anime on your list"."""
    first = "Connected" if status.connected else status.needs
    if not status.last_sync:
        return f"{first} · never synced" if status.connected else first
    when = local_time.describe(status.last_sync, tz_name, now)
    return f"{first} · {'' if status.connected else 'last '}synced {when} · {status.items}"


def _parse(utc_text: str) -> datetime:
    return datetime.strptime(utc_text, "%Y-%m-%d %H:%M:%S")


def _names(labels: list[str]) -> str:
    if len(labels) < 3:
        return " and ".join(labels)
    return f"{', '.join(labels[:-1])}, and {labels[-1]}"


def synced_line(
    statuses: list[SourceStatus], tz_name: str, now: datetime | None = None
) -> SyncedLine:
    """The Dashboard's line. Sources that synced together (SAME_SYNC) are named once,
    with the latest time: "Everything synced today at 11:22 PM." when that's all of them.
    Covers the connected sources and any that synced before."""
    listed = [s for s in statuses if s.connected or s.last_sync]
    if not listed:
        return SyncedLine("Nothing synced yet. Add your sources in Settings.", "", None)

    def when(utc_text: str) -> str:
        return local_time.describe(utc_text, tz_name, now)

    done = sorted((s for s in listed if s.last_sync), key=lambda s: s.last_sync, reverse=True)
    groups: list[list[SourceStatus]] = []
    for status in done:
        if groups and _parse(groups[-1][0].last_sync) - _parse(status.last_sync) <= SAME_SYNC:
            groups[-1].append(status)
        else:
            groups.append([status])
    never = [s for s in listed if not s.last_sync]
    order = {source: i for i, source in enumerate(SOURCES)}
    if len(groups) == 1 and len(done) > 1 and not never:
        text = f"Everything synced {when(done[0].last_sync)}."
    else:
        parts = [
            f"{_names([s.label for s in sorted(g, key=lambda s: order[s.source])])} "
            f"synced {when(g[0].last_sync)}."
            for g in groups
        ]
        parts += [f"{s.label}: never synced." for s in never]
        text = " ".join(parts)
    tip = "\n".join(
        f"{s.label} synced {when(s.last_sync)}" if s.last_sync else f"{s.label}: never synced"
        for s in listed
    )
    return SyncedLine(text, tip, done[0].last_sync if done else None)
