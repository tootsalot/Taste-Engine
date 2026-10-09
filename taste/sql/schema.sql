-- taste-engine schema.
-- Layers: sync_* (bookkeeping), raw_* (API pages as fetched), stg_* (per-source cleaned
-- fields), core_* (source-agnostic). Report views live in views.sql.
-- Written to port to SQL Server with few changes. See docs/PORTABILITY.md.
-- Every statement is idempotent so this file runs on every start.

-- ---------------------------------------------------------------------------
-- Reference
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS core_sources (
    source                TEXT NOT NULL PRIMARY KEY,
    display_name          TEXT NOT NULL,
    default_capture_scope TEXT NOT NULL,
    scope_note            TEXT
);

-- The Last.fm scope and note come from the profile's settings (taste/settings.py
-- copies them in on every connect), so re-running this file never overwrites them.
INSERT INTO core_sources (source, display_name, default_capture_scope, scope_note)
VALUES
    ('mal', 'MyAnimeList', 'self_reported',
     'Self-reported list. Scores, statuses, and dates were entered by hand.'),
    ('lastfm', 'Last.fm', 'unknown', NULL)
ON CONFLICT (source) DO UPDATE SET
    display_name = excluded.display_name;

-- ---------------------------------------------------------------------------
-- Profile settings
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS app_settings (
    setting_key   TEXT NOT NULL PRIMARY KEY,
    setting_value TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

-- ---------------------------------------------------------------------------
-- Bookkeeping
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS sync_runs (
    sync_run_id   INTEGER PRIMARY KEY,
    source        TEXT NOT NULL REFERENCES core_sources (source),
    mode          TEXT NOT NULL,
    started_at    TEXT NOT NULL,
    ended_at      TEXT,
    status        TEXT NOT NULL,
    pages_fetched INTEGER NOT NULL DEFAULT 0,
    rows_fetched  INTEGER NOT NULL DEFAULT 0,
    rows_inserted INTEGER NOT NULL DEFAULT 0,
    rows_updated  INTEGER NOT NULL DEFAULT 0,
    error_message TEXT
);

CREATE INDEX IF NOT EXISTS ix_sync_runs_source_started ON sync_runs (source, started_at);

CREATE TABLE IF NOT EXISTS sync_lastfm_windows (
    window_id        INTEGER PRIMARY KEY,
    opened_by_run_id INTEGER NOT NULL REFERENCES sync_runs (sync_run_id),
    from_unix        INTEGER NOT NULL,
    to_unix          INTEGER NOT NULL,
    cursor_unix      INTEGER,
    status           TEXT NOT NULL,
    completed_at     TEXT
);

-- ---------------------------------------------------------------------------
-- Raw
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS raw_api_pages (
    raw_page_id    INTEGER PRIMARY KEY,
    sync_run_id    INTEGER NOT NULL REFERENCES sync_runs (sync_run_id),
    source         TEXT NOT NULL REFERENCES core_sources (source),
    endpoint       TEXT NOT NULL,
    request_params TEXT NOT NULL,
    page_number    INTEGER,
    http_status    INTEGER NOT NULL,
    fetched_at     TEXT NOT NULL,
    payload        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_raw_api_pages_source_fetched ON raw_api_pages (source, fetched_at);

-- ---------------------------------------------------------------------------
-- Staging: MyAnimeList
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS stg_mal_anime (
    mal_anime_id        INTEGER NOT NULL PRIMARY KEY,
    title               TEXT NOT NULL,
    title_en            TEXT,
    title_ja            TEXT,
    synonyms_json       TEXT,
    media_type          TEXT,
    num_episodes        INTEGER,
    start_season_year   INTEGER,
    start_season        TEXT,
    airing_status       TEXT,
    start_date          TEXT,
    end_date            TEXT,
    content_rating      TEXT,
    source_material     TEXT,
    avg_episode_seconds INTEGER,
    community_mean      REAL,
    num_scoring_users   INTEGER,
    main_picture_url    TEXT,
    nsfw_rating         TEXT,
    last_raw_page_id    INTEGER REFERENCES raw_api_pages (raw_page_id),
    loaded_at           TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS stg_mal_anime_genres (
    mal_anime_id INTEGER NOT NULL REFERENCES stg_mal_anime (mal_anime_id),
    genre_id     INTEGER NOT NULL,
    genre_name   TEXT NOT NULL,
    PRIMARY KEY (mal_anime_id, genre_id)
);

CREATE TABLE IF NOT EXISTS stg_mal_anime_studios (
    mal_anime_id INTEGER NOT NULL REFERENCES stg_mal_anime (mal_anime_id),
    studio_id    INTEGER NOT NULL,
    studio_name  TEXT NOT NULL,
    PRIMARY KEY (mal_anime_id, studio_id)
);

CREATE TABLE IF NOT EXISTS stg_mal_list_entries (
    mal_anime_id         INTEGER NOT NULL PRIMARY KEY REFERENCES stg_mal_anime (mal_anime_id),
    status               TEXT NOT NULL,
    score                INTEGER,
    num_episodes_watched INTEGER NOT NULL,
    is_rewatching        INTEGER NOT NULL,
    num_times_rewatched  INTEGER,
    priority             INTEGER,
    rewatch_value        INTEGER,
    tags_json            TEXT,
    comments             TEXT,
    start_date           TEXT,
    finish_date          TEXT,
    mal_updated_at       TEXT NOT NULL,
    first_seen_at        TEXT NOT NULL,
    last_seen_run_id     INTEGER NOT NULL REFERENCES sync_runs (sync_run_id),
    removed_at           TEXT,
    last_raw_page_id     INTEGER REFERENCES raw_api_pages (raw_page_id)
);

-- ---------------------------------------------------------------------------
-- Staging: Last.fm
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS stg_lastfm_scrobbles (
    scrobble_id    INTEGER PRIMARY KEY,
    played_at_unix INTEGER NOT NULL,
    played_at_utc  TEXT NOT NULL,
    artist_name    TEXT NOT NULL,
    artist_mbid    TEXT NOT NULL DEFAULT '',
    track_name     TEXT NOT NULL,
    track_mbid     TEXT NOT NULL DEFAULT '',
    album_name     TEXT NOT NULL DEFAULT '',
    album_mbid     TEXT NOT NULL DEFAULT '',
    track_url      TEXT,
    image_url      TEXT,
    raw_page_id    INTEGER NOT NULL REFERENCES raw_api_pages (raw_page_id),
    loaded_at      TEXT NOT NULL,
    CONSTRAINT uq_stg_lastfm_scrobbles UNIQUE (played_at_unix, artist_name, track_name)
);

-- ---------------------------------------------------------------------------
-- Core: items and creators
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS core_items (
    item_id      INTEGER PRIMARY KEY,
    media_type   TEXT NOT NULL,
    title        TEXT NOT NULL,
    title_alt    TEXT,
    release_year INTEGER,
    source       TEXT NOT NULL REFERENCES core_sources (source),
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS core_item_external_ids (
    source           TEXT NOT NULL REFERENCES core_sources (source),
    id_type          TEXT NOT NULL,
    external_id      TEXT NOT NULL,
    item_id          INTEGER NOT NULL REFERENCES core_items (item_id),
    match_method     TEXT NOT NULL,
    match_confidence REAL,
    PRIMARY KEY (source, id_type, external_id)
);

CREATE INDEX IF NOT EXISTS ix_core_item_external_ids_item ON core_item_external_ids (item_id);

CREATE TABLE IF NOT EXISTS core_item_links (
    parent_item_id INTEGER NOT NULL REFERENCES core_items (item_id),
    child_item_id  INTEGER NOT NULL REFERENCES core_items (item_id),
    link_type      TEXT NOT NULL,
    source         TEXT NOT NULL REFERENCES core_sources (source),
    PRIMARY KEY (parent_item_id, child_item_id, link_type)
);

CREATE TABLE IF NOT EXISTS core_creators (
    creator_id INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    source     TEXT NOT NULL REFERENCES core_sources (source),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS core_creator_external_ids (
    source           TEXT NOT NULL REFERENCES core_sources (source),
    id_type          TEXT NOT NULL,
    external_id      TEXT NOT NULL,
    creator_id       INTEGER NOT NULL REFERENCES core_creators (creator_id),
    match_method     TEXT NOT NULL,
    match_confidence REAL,
    PRIMARY KEY (source, id_type, external_id)
);

CREATE TABLE IF NOT EXISTS core_item_creators (
    item_id    INTEGER NOT NULL REFERENCES core_items (item_id),
    creator_id INTEGER NOT NULL REFERENCES core_creators (creator_id),
    role       TEXT NOT NULL,
    source     TEXT NOT NULL REFERENCES core_sources (source),
    PRIMARY KEY (item_id, creator_id, role)
);

CREATE INDEX IF NOT EXISTS ix_core_item_creators_creator ON core_item_creators (creator_id);

CREATE TABLE IF NOT EXISTS core_tags (
    tag_id   INTEGER PRIMARY KEY,
    name     TEXT NOT NULL,
    tag_kind TEXT NOT NULL,
    CONSTRAINT uq_core_tags UNIQUE (tag_kind, name)
);

CREATE TABLE IF NOT EXISTS core_item_tags (
    item_id INTEGER NOT NULL REFERENCES core_items (item_id),
    tag_id  INTEGER NOT NULL REFERENCES core_tags (tag_id),
    source  TEXT NOT NULL REFERENCES core_sources (source),
    weight  REAL,
    PRIMARY KEY (item_id, tag_id, source)
);

-- ---------------------------------------------------------------------------
-- Core: signals
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS core_ratings (
    rating_id         INTEGER PRIMARY KEY,
    item_id           INTEGER NOT NULL REFERENCES core_items (item_id),
    source            TEXT NOT NULL REFERENCES core_sources (source),
    raw_score         REAL NOT NULL,
    scale_min         REAL NOT NULL,
    scale_max         REAL NOT NULL,
    normalized_score  REAL NOT NULL,
    source_updated_at TEXT,
    CONSTRAINT uq_core_ratings UNIQUE (source, item_id)
);

CREATE TABLE IF NOT EXISTS core_community_ratings (
    item_id         INTEGER NOT NULL REFERENCES core_items (item_id),
    source          TEXT NOT NULL REFERENCES core_sources (source),
    mean_score      REAL NOT NULL,
    scale_min       REAL NOT NULL,
    scale_max       REAL NOT NULL,
    normalized_mean REAL NOT NULL,
    num_raters      INTEGER,
    observed_at     TEXT NOT NULL,
    PRIMARY KEY (source, item_id)
);

CREATE TABLE IF NOT EXISTS core_behavior_events (
    event_id         INTEGER PRIMARY KEY,
    item_id          INTEGER NOT NULL REFERENCES core_items (item_id),
    source           TEXT NOT NULL REFERENCES core_sources (source),
    event_type       TEXT NOT NULL,
    occurred_at_utc  TEXT NOT NULL,
    occurred_at_unix INTEGER,
    time_precision   TEXT NOT NULL,
    time_basis       TEXT NOT NULL,
    capture_scope    TEXT NOT NULL,
    source_event_key TEXT NOT NULL,
    CONSTRAINT uq_core_behavior_events UNIQUE (source, source_event_key)
);

CREATE INDEX IF NOT EXISTS ix_core_behavior_events_time ON core_behavior_events (occurred_at_unix);
CREATE INDEX IF NOT EXISTS ix_core_behavior_events_item ON core_behavior_events (item_id);

-- Event times converted to the profile's time zone (taste/local_time.py). Rebuilt
-- when the time zone setting changes. Only events with an exact time get a row.
CREATE TABLE IF NOT EXISTS core_event_local_times (
    event_id          INTEGER NOT NULL PRIMARY KEY
                      REFERENCES core_behavior_events (event_id) ON DELETE CASCADE,
    occurred_at_unix  INTEGER NOT NULL,
    tz_name           TEXT NOT NULL,
    local_ts          TEXT NOT NULL,
    local_date        TEXT NOT NULL,
    local_year        INTEGER NOT NULL,
    local_month       TEXT NOT NULL,
    local_hour        INTEGER NOT NULL,
    local_weekday_num INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS core_curation (
    curation_id       INTEGER PRIMARY KEY,
    item_id           INTEGER NOT NULL REFERENCES core_items (item_id),
    source            TEXT NOT NULL REFERENCES core_sources (source),
    curation_type     TEXT NOT NULL,
    list_name         TEXT NOT NULL DEFAULT '',
    value             TEXT,
    progress          INTEGER,
    progress_total    INTEGER,
    is_repeat         INTEGER NOT NULL DEFAULT 0,
    repeat_count      INTEGER,
    source_updated_at TEXT,
    CONSTRAINT uq_core_curation UNIQUE (source, item_id, curation_type, list_name)
);

-- ---------------------------------------------------------------------------
-- Enrichment (docs/PLAN_RECS.md): details fetched for recommendations and art
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS stg_mal_anime_details (
    mal_anime_id INTEGER NOT NULL PRIMARY KEY,
    fetched_at   TEXT NOT NULL,
    raw_page_id  INTEGER REFERENCES raw_api_pages (raw_page_id)
);

CREATE TABLE IF NOT EXISTS stg_mal_anime_recommendations (
    mal_anime_id        INTEGER NOT NULL,
    recommended_id      INTEGER NOT NULL,
    recommended_title   TEXT NOT NULL,
    num_recommendations INTEGER NOT NULL,
    PRIMARY KEY (mal_anime_id, recommended_id)
);

CREATE TABLE IF NOT EXISTS stg_mal_related_anime (
    mal_anime_id  INTEGER NOT NULL,
    related_id    INTEGER NOT NULL,
    related_title TEXT NOT NULL,
    relation_type TEXT NOT NULL,
    PRIMARY KEY (mal_anime_id, related_id)
);

CREATE TABLE IF NOT EXISTS stg_lastfm_artist_fetches (
    artist_key TEXT NOT NULL,
    method     TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (artist_key, method)
);

CREATE TABLE IF NOT EXISTS stg_lastfm_similar_artists (
    artist_key   TEXT NOT NULL,
    similar_key  TEXT NOT NULL,
    similar_name TEXT NOT NULL,
    similar_mbid TEXT NOT NULL DEFAULT '',
    match        REAL NOT NULL,
    PRIMARY KEY (artist_key, similar_key)
);

CREATE TABLE IF NOT EXISTS stg_lastfm_artist_tags (
    artist_key TEXT NOT NULL,
    tag        TEXT NOT NULL,
    tag_count  INTEGER NOT NULL,
    PRIMARY KEY (artist_key, tag)
);

CREATE TABLE IF NOT EXISTS stg_lastfm_artist_top_album (
    artist_key TEXT NOT NULL PRIMARY KEY,
    album_name TEXT NOT NULL,
    image_url  TEXT,
    fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS core_item_images (
    item_id INTEGER NOT NULL REFERENCES core_items (item_id),
    source  TEXT NOT NULL REFERENCES core_sources (source),
    kind    TEXT NOT NULL,
    url     TEXT NOT NULL,
    PRIMARY KEY (item_id, source, kind)
);

CREATE TABLE IF NOT EXISTS core_item_similarity (
    item_id         INTEGER NOT NULL REFERENCES core_items (item_id),
    similar_item_id INTEGER NOT NULL REFERENCES core_items (item_id),
    source          TEXT NOT NULL REFERENCES core_sources (source),
    kind            TEXT NOT NULL,
    score           REAL NOT NULL,
    PRIMARY KEY (item_id, similar_item_id, source, kind)
);

-- ---------------------------------------------------------------------------
-- Recommendations: every run is saved with its scores and reasons
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS rec_runs (
    rec_run_id   INTEGER PRIMARY KEY,
    kind         TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    params_json  TEXT NOT NULL,
    metrics_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS rec_items (
    rec_run_id   INTEGER NOT NULL REFERENCES rec_runs (rec_run_id) ON DELETE CASCADE,
    rank         INTEGER NOT NULL,
    item_key     TEXT NOT NULL,
    title        TEXT NOT NULL,
    subtitle     TEXT NOT NULL DEFAULT '',
    score        REAL,
    support      REAL,
    badge        TEXT NOT NULL DEFAULT '',
    image_url    TEXT,
    url          TEXT,
    reasons_json TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY (rec_run_id, rank)
);

-- "Not interested". App state, not a taste signal yet.
CREATE TABLE IF NOT EXISTS rec_dismissed (
    kind         TEXT NOT NULL,
    item_key     TEXT NOT NULL,
    dismissed_at TEXT NOT NULL,
    PRIMARY KEY (kind, item_key)
);
