from taste import settings
from tests.test_reports import add_play

# 2026-03-08 09:30 UTC is just after the US DST switch (2 AM local in Denver).
AFTER_DST = 1772962200
# 2026-03-08 08:30 UTC is just before it.
BEFORE_DST = 1772958600


def local_rows(conn):
    return [
        tuple(r)
        for r in conn.execute(
            "SELECT l.local_ts, l.local_hour, l.tz_name FROM core_event_local_times l "
            "JOIN core_behavior_events e ON e.event_id = l.event_id ORDER BY e.occurred_at_unix"
        )
    ]


def test_phoenix_has_no_dst(conn):
    add_play(conn, BEFORE_DST)
    add_play(conn, AFTER_DST, track="Later")
    assert local_rows(conn) == [
        ("2026-03-08 01:30:00", 1, "America/Phoenix"),
        ("2026-03-08 02:30:00", 2, "America/Phoenix"),
    ]


def test_denver_handles_dst_and_setting_change_rebuilds(conn):
    add_play(conn, BEFORE_DST)
    add_play(conn, AFTER_DST, track="Later")
    settings.set_value(conn, "timezone", "America/Denver")
    # Clocks jump from 02:00 to 03:00, so one real hour apart shows as two local hours.
    assert local_rows(conn) == [
        ("2026-03-08 01:30:00", 1, "America/Denver"),
        ("2026-03-08 03:30:00", 3, "America/Denver"),
    ]
    hours = dict(conn.execute("SELECT local_hour, plays FROM rpt_lastfm_by_hour").fetchall())
    assert hours[1] == 1 and hours[3] == 1 and hours[2] == 0


def test_deleted_events_drop_their_local_rows(conn):
    add_play(conn, AFTER_DST)
    conn.execute("DELETE FROM core_behavior_events")
    assert conn.execute("SELECT COUNT(*) FROM core_event_local_times").fetchone()[0] == 0
