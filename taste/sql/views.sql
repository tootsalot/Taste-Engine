-- Report views. All read from the core layer, plus app_settings for thresholds and limits.
-- Views are dropped and recreated on every start so definition changes take effect.
-- No ORDER BY inside views (SQL Server doesn't allow it). Sorting happens when querying.
-- No SQLite date functions: local times come from core_event_local_times.

-- ---------------------------------------------------------------------------
-- Settings helper
-- ---------------------------------------------------------------------------

DROP VIEW IF EXISTS rpt_mal_critic_summary;
DROP VIEW IF EXISTS rpt_mal_genre_vs_community;
DROP VIEW IF EXISTS rpt_mal_score_vs_community;
DROP VIEW IF EXISTS rpt_mal_dropped_on_hold;
DROP VIEW IF EXISTS rpt_lastfm_top_artists_all_time;
DROP VIEW IF EXISTS rpt_lastfm_top_artists_by_year;
DROP VIEW IF EXISTS rpt_lastfm_top_artists_by_month;
DROP VIEW IF EXISTS rpt_lastfm_top_tracks_all_time;
DROP VIEW IF EXISTS rpt_lastfm_top_tracks_by_year;
DROP VIEW IF EXISTS rpt_lastfm_top_tracks_by_month;
DROP VIEW IF EXISTS rpt_lastfm_by_hour;
DROP VIEW IF EXISTS rpt_lastfm_by_weekday;
DROP VIEW IF EXISTS rpt_lastfm_scope;
DROP VIEW IF EXISTS rpt_lastfm_plays_local;
DROP VIEW IF EXISTS rpt_settings;

-- One row with every report setting as a typed column.
CREATE VIEW rpt_settings AS
SELECT
    MAX(CASE WHEN setting_key = 'genre_min_sample'
        THEN CAST(setting_value AS INTEGER) END) AS genre_min_sample,
    MAX(CASE WHEN setting_key = 'in_line_threshold'
        THEN CAST(setting_value AS REAL) END) AS in_line_threshold,
    MAX(CASE WHEN setting_key = 'top_n_all_time'
        THEN CAST(setting_value AS INTEGER) END) AS top_n_all_time,
    MAX(CASE WHEN setting_key = 'top_n_per_year'
        THEN CAST(setting_value AS INTEGER) END) AS top_n_per_year,
    MAX(CASE WHEN setting_key = 'top_n_per_month'
        THEN CAST(setting_value AS INTEGER) END) AS top_n_per_month,
    MAX(CASE WHEN setting_key = 'timezone' THEN setting_value END) AS timezone
FROM app_settings;

-- ---------------------------------------------------------------------------
-- MyAnimeList
-- ---------------------------------------------------------------------------

-- Every anime I scored, next to the MAL community mean.
CREATE VIEW rpt_mal_score_vs_community AS
SELECT
    i.item_id,
    i.title,
    i.title_alt AS title_english,
    i.release_year,
    cu.value AS list_status,
    r.raw_score AS my_score,
    cr.mean_score AS community_mean,
    ROUND(r.raw_score - cr.mean_score, 2) AS score_diff,
    ROUND(r.normalized_score - cr.normalized_mean, 4) AS normalized_diff,
    cr.num_raters AS community_raters
FROM core_ratings r
JOIN core_items i
    ON i.item_id = r.item_id
JOIN core_community_ratings cr
    ON cr.item_id = r.item_id AND cr.source = r.source
LEFT JOIN core_curation cu
    ON cu.item_id = r.item_id
    AND cu.source = r.source
    AND cu.curation_type = 'list_status'
    AND cu.list_name = ''
WHERE r.source = 'mal';

-- One row: how critical am I overall?
CREATE VIEW rpt_mal_critic_summary AS
SELECT
    COUNT(*) AS shows_scored,
    ROUND(AVG(my_score), 2) AS my_avg_score,
    ROUND(AVG(community_mean), 2) AS community_avg_score,
    ROUND(AVG(score_diff), 2) AS avg_diff,
    ROUND(AVG(CASE WHEN score_diff < 0 THEN 1.0 ELSE 0.0 END), 3) AS share_scored_below,
    ROUND(AVG(CASE WHEN score_diff > 0 THEN 1.0 ELSE 0.0 END), 3) AS share_scored_above
FROM rpt_mal_score_vs_community;

-- Per genre, only genres with at least genre_min_sample scored shows, so one show
-- can't swing a genre. diff_vs_my_norm compares each genre to my overall average
-- difference: where I'm unusually harsh or generous for me, not just vs everyone.
CREATE VIEW rpt_mal_genre_vs_community AS
SELECT
    g.genre,
    g.shows_scored,
    g.my_avg_score,
    g.community_avg_score,
    g.avg_diff,
    ROUND(g.avg_diff - overall.avg_diff, 2) AS diff_vs_my_norm,
    CASE
        WHEN g.avg_diff >= s.in_line_threshold THEN 'above'
        WHEN g.avg_diff <= -s.in_line_threshold THEN 'below'
        ELSE 'in_line'
    END AS vs_community,
    s.genre_min_sample AS min_sample_size
FROM (
    SELECT
        t.name AS genre,
        COUNT(*) AS shows_scored,
        ROUND(AVG(sc.my_score), 2) AS my_avg_score,
        ROUND(AVG(sc.community_mean), 2) AS community_avg_score,
        ROUND(AVG(sc.score_diff), 2) AS avg_diff
    FROM rpt_mal_score_vs_community sc
    JOIN core_item_tags it
        ON it.item_id = sc.item_id AND it.source = 'mal'
    JOIN core_tags t
        ON t.tag_id = it.tag_id AND t.tag_kind = 'genre'
    GROUP BY t.name
) g
CROSS JOIN rpt_settings s
CROSS JOIN (
    SELECT AVG(score_diff) AS avg_diff FROM rpt_mal_score_vs_community
) overall
WHERE g.shows_scored >= s.genre_min_sample;

-- Shows I dropped or put on hold, and how far I got.
CREATE VIEW rpt_mal_dropped_on_hold AS
SELECT
    i.item_id,
    i.title,
    i.title_alt AS title_english,
    cu.value AS list_status,
    cu.progress AS episodes_watched,
    cu.progress_total AS total_episodes,
    CASE
        WHEN cu.progress_total > 0 THEN ROUND(100.0 * cu.progress / cu.progress_total, 1)
    END AS pct_complete,
    r.raw_score AS my_score,
    cu.source_updated_at AS last_updated_utc
FROM core_curation cu
JOIN core_items i
    ON i.item_id = cu.item_id
LEFT JOIN core_ratings r
    ON r.item_id = cu.item_id AND r.source = cu.source
WHERE cu.source = 'mal'
    AND cu.curation_type = 'list_status'
    AND cu.value IN ('dropped', 'on_hold');

-- ---------------------------------------------------------------------------
-- Last.fm. Every view carries data_scope (the profile's scope note) and
-- capture_scopes (the scope stored on the rows, or 'mixed' if they differ).
-- ---------------------------------------------------------------------------

-- Helper: every play with its time in the profile's time zone.
CREATE VIEW rpt_lastfm_plays_local AS
SELECT
    e.event_id,
    e.item_id AS track_item_id,
    i.title AS track,
    c.creator_id AS artist_id,
    c.name AS artist,
    e.occurred_at_utc,
    l.local_ts AS occurred_at_local,
    l.local_year,
    l.local_month,
    l.local_hour,
    l.local_weekday_num,
    l.tz_name,
    e.capture_scope,
    s.scope_note AS data_scope
FROM core_behavior_events e
JOIN core_event_local_times l
    ON l.event_id = e.event_id
JOIN core_items i
    ON i.item_id = e.item_id
JOIN core_item_creators ic
    ON ic.item_id = e.item_id AND ic.role = 'artist' AND ic.source = 'lastfm'
JOIN core_creators c
    ON c.creator_id = ic.creator_id
JOIN core_sources s
    ON s.source = e.source
WHERE e.source = 'lastfm'
    AND e.event_type = 'play';

-- One row: the scope note and which capture scopes are in the data.
CREATE VIEW rpt_lastfm_scope AS
SELECT
    src.scope_note AS data_scope,
    CASE
        WHEN MIN(p.capture_scope) IS NULL THEN src.default_capture_scope
        WHEN MIN(p.capture_scope) = MAX(p.capture_scope) THEN MIN(p.capture_scope)
        ELSE 'mixed'
    END AS capture_scopes
FROM core_sources src
LEFT JOIN core_behavior_events p
    ON p.source = src.source AND p.event_type = 'play'
WHERE src.source = 'lastfm'
GROUP BY src.scope_note, src.default_capture_scope;

CREATE VIEW rpt_lastfm_top_artists_all_time AS
SELECT play_rank, artist, plays, distinct_tracks, first_play_local, last_play_local,
    data_scope, capture_scopes
FROM (
    SELECT
        ROW_NUMBER() OVER (ORDER BY COUNT(*) DESC, artist) AS play_rank,
        artist,
        COUNT(*) AS plays,
        COUNT(DISTINCT track_item_id) AS distinct_tracks,
        MIN(occurred_at_local) AS first_play_local,
        MAX(occurred_at_local) AS last_play_local,
        data_scope,
        CASE WHEN MIN(capture_scope) = MAX(capture_scope) THEN MIN(capture_scope)
            ELSE 'mixed' END AS capture_scopes
    FROM rpt_lastfm_plays_local
    GROUP BY artist_id, artist, data_scope
) ranked
CROSS JOIN rpt_settings s
WHERE play_rank <= s.top_n_all_time;

CREATE VIEW rpt_lastfm_top_artists_by_year AS
SELECT local_year, play_rank, artist, plays, data_scope, capture_scopes
FROM (
    SELECT
        local_year,
        ROW_NUMBER() OVER (PARTITION BY local_year ORDER BY COUNT(*) DESC, artist) AS play_rank,
        artist,
        COUNT(*) AS plays,
        data_scope,
        CASE WHEN MIN(capture_scope) = MAX(capture_scope) THEN MIN(capture_scope)
            ELSE 'mixed' END AS capture_scopes
    FROM rpt_lastfm_plays_local
    GROUP BY local_year, artist_id, artist, data_scope
) ranked
CROSS JOIN rpt_settings s
WHERE play_rank <= s.top_n_per_year;

CREATE VIEW rpt_lastfm_top_artists_by_month AS
SELECT local_month, play_rank, artist, plays, data_scope, capture_scopes
FROM (
    SELECT
        local_month,
        ROW_NUMBER() OVER (PARTITION BY local_month ORDER BY COUNT(*) DESC, artist) AS play_rank,
        artist,
        COUNT(*) AS plays,
        data_scope,
        CASE WHEN MIN(capture_scope) = MAX(capture_scope) THEN MIN(capture_scope)
            ELSE 'mixed' END AS capture_scopes
    FROM rpt_lastfm_plays_local
    GROUP BY local_month, artist_id, artist, data_scope
) ranked
CROSS JOIN rpt_settings s
WHERE play_rank <= s.top_n_per_month;

CREATE VIEW rpt_lastfm_top_tracks_all_time AS
SELECT play_rank, track, artist, plays, first_play_local, last_play_local,
    data_scope, capture_scopes
FROM (
    SELECT
        ROW_NUMBER() OVER (ORDER BY COUNT(*) DESC, artist, track) AS play_rank,
        track,
        artist,
        COUNT(*) AS plays,
        MIN(occurred_at_local) AS first_play_local,
        MAX(occurred_at_local) AS last_play_local,
        data_scope,
        CASE WHEN MIN(capture_scope) = MAX(capture_scope) THEN MIN(capture_scope)
            ELSE 'mixed' END AS capture_scopes
    FROM rpt_lastfm_plays_local
    GROUP BY track_item_id, track, artist, data_scope
) ranked
CROSS JOIN rpt_settings s
WHERE play_rank <= s.top_n_all_time;

CREATE VIEW rpt_lastfm_top_tracks_by_year AS
SELECT local_year, play_rank, track, artist, plays, data_scope, capture_scopes
FROM (
    SELECT
        local_year,
        ROW_NUMBER() OVER (
            PARTITION BY local_year ORDER BY COUNT(*) DESC, artist, track
        ) AS play_rank,
        track,
        artist,
        COUNT(*) AS plays,
        data_scope,
        CASE WHEN MIN(capture_scope) = MAX(capture_scope) THEN MIN(capture_scope)
            ELSE 'mixed' END AS capture_scopes
    FROM rpt_lastfm_plays_local
    GROUP BY local_year, track_item_id, track, artist, data_scope
) ranked
CROSS JOIN rpt_settings s
WHERE play_rank <= s.top_n_per_year;

CREATE VIEW rpt_lastfm_top_tracks_by_month AS
SELECT local_month, play_rank, track, artist, plays, data_scope, capture_scopes
FROM (
    SELECT
        local_month,
        ROW_NUMBER() OVER (
            PARTITION BY local_month ORDER BY COUNT(*) DESC, artist, track
        ) AS play_rank,
        track,
        artist,
        COUNT(*) AS plays,
        data_scope,
        CASE WHEN MIN(capture_scope) = MAX(capture_scope) THEN MIN(capture_scope)
            ELSE 'mixed' END AS capture_scopes
    FROM rpt_lastfm_plays_local
    GROUP BY local_month, track_item_id, track, artist, data_scope
) ranked
CROSS JOIN rpt_settings s
WHERE play_rank <= s.top_n_per_month;

-- All 24 hours are listed, including ones with no plays.
CREATE VIEW rpt_lastfm_by_hour AS
SELECT
    h.local_hour,
    COALESCE(p.plays, 0) AS plays,
    ROUND(100.0 * COALESCE(p.plays, 0) / NULLIF(t.total_plays, 0), 1) AS pct_of_plays,
    sc.data_scope,
    sc.capture_scopes
FROM (
    SELECT 0 AS local_hour UNION ALL SELECT 1 UNION ALL SELECT 2 UNION ALL SELECT 3
    UNION ALL SELECT 4 UNION ALL SELECT 5 UNION ALL SELECT 6 UNION ALL SELECT 7
    UNION ALL SELECT 8 UNION ALL SELECT 9 UNION ALL SELECT 10 UNION ALL SELECT 11
    UNION ALL SELECT 12 UNION ALL SELECT 13 UNION ALL SELECT 14 UNION ALL SELECT 15
    UNION ALL SELECT 16 UNION ALL SELECT 17 UNION ALL SELECT 18 UNION ALL SELECT 19
    UNION ALL SELECT 20 UNION ALL SELECT 21 UNION ALL SELECT 22 UNION ALL SELECT 23
) h
LEFT JOIN (
    SELECT local_hour, COUNT(*) AS plays FROM rpt_lastfm_plays_local GROUP BY local_hour
) p
    ON p.local_hour = h.local_hour
CROSS JOIN (SELECT COUNT(*) AS total_plays FROM rpt_lastfm_plays_local) t
CROSS JOIN rpt_lastfm_scope sc;

-- All 7 days are listed. local_weekday_num: 0 = Sunday.
CREATE VIEW rpt_lastfm_by_weekday AS
SELECT
    d.local_weekday_num,
    d.weekday,
    COALESCE(p.plays, 0) AS plays,
    ROUND(100.0 * COALESCE(p.plays, 0) / NULLIF(t.total_plays, 0), 1) AS pct_of_plays,
    sc.data_scope,
    sc.capture_scopes
FROM (
    SELECT 0 AS local_weekday_num, 'Sunday' AS weekday
    UNION ALL SELECT 1, 'Monday'
    UNION ALL SELECT 2, 'Tuesday'
    UNION ALL SELECT 3, 'Wednesday'
    UNION ALL SELECT 4, 'Thursday'
    UNION ALL SELECT 5, 'Friday'
    UNION ALL SELECT 6, 'Saturday'
) d
LEFT JOIN (
    SELECT local_weekday_num, COUNT(*) AS plays
    FROM rpt_lastfm_plays_local
    GROUP BY local_weekday_num
) p
    ON p.local_weekday_num = d.local_weekday_num
CROSS JOIN (SELECT COUNT(*) AS total_plays FROM rpt_lastfm_plays_local) t
CROSS JOIN rpt_lastfm_scope sc;
