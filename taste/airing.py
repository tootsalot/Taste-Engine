"""Weekly MAL broadcasts in the profile's time zone, without Qt or network calls.

MAL gives a weekly broadcast slot, not episode-by-episode air dates. Episode numbers
here come only from my list progress, never from an estimate of what has aired.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from taste import activity, local_time, settings

DAYS = {
    name: i
    for i, name in enumerate(
        ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
    )
}
TOKYO = ZoneInfo("Asia/Tokyo")


@dataclass(frozen=True)
class AiringShow:
    mal_anime_id: int
    title: str
    poster_url: str | None
    next_at: datetime | None
    next_episode: int
    final_episode: bool
    caption: str  # when and progress together, for tooltips
    when: str = ""  # "Sat 10:30 AM" or "no set time"
    progress: str = ""  # "next for you: ep 7"


def next_broadcast(
    day: str | None, clock: str | None, now: datetime, tz_name: str
) -> datetime | None:
    """The next weekly slot at or after now, converted from Japan to the profile zone."""
    weekday = DAYS.get(day)
    if weekday is None or not clock:
        return None
    try:
        time = datetime.strptime(clock, "%H:%M").time()
    except (ValueError, TypeError):
        return None
    japan = now.astimezone(TOKYO)
    date = japan.date() + timedelta(days=(weekday - japan.weekday()) % 7)
    broadcast = datetime.combine(date, time, TOKYO)
    if broadcast < japan:
        broadcast += timedelta(days=7)
    return broadcast.astimezone(ZoneInfo(tz_name))


def load(conn: sqlite3.Connection, now: datetime) -> list[AiringShow]:
    """Visible, currently airing shows on my watching list, in next-broadcast order.

    Unknown slots sort last. Adult shows are filtered before titles or posters leave
    this loader. These are local staging lookups; sync and art requests run elsewhere.
    """
    hidden = activity.hidden_items(conn)
    cfg = settings.get_all(conn)
    rows = conn.execute(
        "SELECT a.mal_anime_id, a.title, a.title_en, a.main_picture_url, a.nsfw_rating, "
        "a.num_episodes, a.broadcast_day, a.broadcast_time, l.num_episodes_watched, x.item_id "
        "FROM stg_mal_list_entries l JOIN stg_mal_anime a ON a.mal_anime_id = l.mal_anime_id "
        "JOIN core_item_external_ids x ON x.external_id = CAST(a.mal_anime_id AS TEXT) "
        "AND x.source = 'mal' AND x.id_type = 'mal_anime_id' "
        "WHERE l.status = 'watching' AND l.removed_at IS NULL "
        "AND a.airing_status = 'currently_airing'"
    )
    shows = []
    for row in rows:
        if row["item_id"] in hidden:
            continue
        moment = next_broadcast(row["broadcast_day"], row["broadcast_time"], now, cfg["timezone"])
        episode = row["num_episodes_watched"] + 1
        total = row["num_episodes"]
        final = bool(total and episode == total)
        when = "no set time"
        if moment is not None:
            clock = local_time.time_text(moment.hour, moment.minute, cfg["clock_format"])
            when = f"{moment:%a} {clock}"
        # A still-watching entry can already have all known episodes recorded. Don't
        # label a nonexistent episode as upcoming when that happens.
        progress = (
            f"all {total} episodes recorded"
            if total and episode > total
            else f"next for you: ep {episode}"
        )
        if final:
            progress += " · final episode"
        shows.append(
            AiringShow(
                row["mal_anime_id"],
                row["title_en"] or row["title"],
                # Like Recently finished: a poster MAL rates adult is never pictured on
                # the Dashboard, even when the show itself is shown.
                None if row["nsfw_rating"] == "black" else row["main_picture_url"],
                moment,
                episode,
                final,
                f"{when} · {progress}",
                when,
                progress,
            )
        )
    shows.sort(
        key=lambda s: (
            s.next_at is None,
            s.next_at.timestamp() if s.next_at else 0,
            s.title,
            s.mal_anime_id,
        )
    )
    return shows
