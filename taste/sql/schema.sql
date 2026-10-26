-- taste-engine schema.
-- Layers: sync_* (bookkeeping), raw_* (API pages as fetched), stg_* (per-source cleaned
-- fields), core_* (source-agnostic). Report views live in views.sql.
-- Written to port to SQL Server with few changes: INTEGER, REAL, and TEXT only.
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
    ('lastfm', 'Last.fm', 'unknown', NULL),
    ('deezer', 'Deezer', 'not_applicable',
     'Artist photos only, for suggested artists Last.fm has no picture for.'),
    ('hardcover', 'Hardcover', 'self_reported',
     'My Hardcover library. Ratings, statuses, and read dates were entered by hand.'),
    ('tmdb', 'TMDB', 'self_reported',
     'Ratings from my TMDB account. TMDB keeps the date of each rating, not when I watched.')
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
    loaded_at           TEXT NOT NULL,
    num_list_users      INTEGER,
    popularity_rank     INTEGER,
    score_rank          INTEGER,
    synopsis            TEXT
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
-- Staging: Hardcover (my library through its GraphQL API, signed in with OAuth)
-- ---------------------------------------------------------------------------

-- One row per book in my library. status_id: 1 want to read, 2 reading, 3 read,
-- 4 paused, 5 did not finish, 6 ignored. rating is 0.5 to 5 in halves, NULL when
-- not rated. The edition is the one I picked, when I picked one. A full read that
-- no longer has the book sets removed_at.
CREATE TABLE IF NOT EXISTS stg_hardcover_user_books (
    user_book_id       INTEGER NOT NULL PRIMARY KEY,
    book_id            INTEGER NOT NULL,
    status_id          INTEGER NOT NULL,
    rating             REAL,
    date_added         TEXT,
    first_started_date TEXT,
    first_read_date    TEXT,
    last_read_date     TEXT,
    read_count         INTEGER NOT NULL DEFAULT 0,
    owned              INTEGER NOT NULL DEFAULT 0,
    edition_id         INTEGER,
    reading_format_id  INTEGER,
    edition_pages      INTEGER,
    edition_seconds    INTEGER,
    first_seen_at      TEXT NOT NULL,
    last_seen_run_id   INTEGER NOT NULL REFERENCES sync_runs (sync_run_id),
    removed_at         TEXT,
    last_raw_page_id   INTEGER REFERENCES raw_api_pages (raw_page_id)
);

-- One row per read-through (Hardcover's user_book_reads): the days I started and
-- finished. A book read twice has two rows.
CREATE TABLE IF NOT EXISTS stg_hardcover_reads (
    read_id           INTEGER NOT NULL PRIMARY KEY,
    user_book_id      INTEGER NOT NULL,
    started_at        TEXT,
    finished_at       TEXT,
    edition_id        INTEGER,
    first_seen_at     TEXT NOT NULL,
    last_seen_run_id  INTEGER NOT NULL REFERENCES sync_runs (sync_run_id),
    removed_at        TEXT,
    last_raw_page_id  INTEGER REFERENCES raw_api_pages (raw_page_id)
);

-- The book itself, as Hardcover describes it. literary_type_id: 1 fiction,
-- 2 nonfiction. rating is the Hardcover average (0 to 5). authors_json holds the
-- contributions credited as author, in order; genres_json and moods_json the top
-- tags readers gave the book.
CREATE TABLE IF NOT EXISTS stg_hardcover_books (
    book_id          INTEGER NOT NULL PRIMARY KEY,
    title            TEXT NOT NULL,
    subtitle         TEXT,
    slug             TEXT,
    pages            INTEGER,
    audio_seconds    INTEGER,
    release_year     INTEGER,
    rating           REAL,
    ratings_count    INTEGER,
    literary_type_id INTEGER,
    image_url        TEXT,
    authors_json     TEXT NOT NULL DEFAULT '[]',
    genres_json      TEXT NOT NULL DEFAULT '[]',
    moods_json       TEXT NOT NULL DEFAULT '[]',
    last_raw_page_id INTEGER REFERENCES raw_api_pages (raw_page_id),
    updated_at       TEXT NOT NULL,
    similar_json     TEXT NOT NULL DEFAULT '[]',
    description      TEXT,
    users_count      INTEGER,
    users_read_count INTEGER,
    details_at       TEXT
);

-- ---------------------------------------------------------------------------
-- Staging: TMDB (my account's ratings and lists, and details per title)
-- ---------------------------------------------------------------------------

-- media_type: movie or tv. season and episode are 0 for a film or a show (no NULLs in
-- the key). A full read that no longer has a rating sets removed_at.
CREATE TABLE IF NOT EXISTS stg_tmdb_account_ratings (
    media_type       TEXT NOT NULL,
    tmdb_id          INTEGER NOT NULL,
    season           INTEGER NOT NULL DEFAULT 0,
    episode          INTEGER NOT NULL DEFAULT 0,
    title            TEXT NOT NULL,
    rating           REAL NOT NULL,
    rated_at         TEXT,
    first_seen_at    TEXT NOT NULL,
    last_seen_run_id INTEGER NOT NULL REFERENCES sync_runs (sync_run_id),
    removed_at       TEXT,
    last_raw_page_id INTEGER REFERENCES raw_api_pages (raw_page_id),
    PRIMARY KEY (media_type, tmdb_id, season, episode)
);

-- Watchlist and favorites membership.
CREATE TABLE IF NOT EXISTS stg_tmdb_account_lists (
    media_type       TEXT NOT NULL,
    tmdb_id          INTEGER NOT NULL,
    list_name        TEXT NOT NULL,
    title            TEXT NOT NULL,
    last_seen_run_id INTEGER NOT NULL REFERENCES sync_runs (sync_run_id),
    removed_at       TEXT,
    PRIMARY KEY (media_type, tmdb_id, list_name)
);

CREATE TABLE IF NOT EXISTS stg_tmdb_movies (
    tmdb_id           INTEGER NOT NULL PRIMARY KEY,
    title             TEXT NOT NULL,
    original_title    TEXT,
    release_date      TEXT,
    runtime           INTEGER,
    vote_average      REAL,
    vote_count        INTEGER,
    poster_path       TEXT,
    original_language TEXT,
    imdb_id           TEXT,
    genres_json       TEXT NOT NULL DEFAULT '[]',
    directors_json    TEXT NOT NULL DEFAULT '[]',
    raw_page_id       INTEGER REFERENCES raw_api_pages (raw_page_id),
    fetched_at        TEXT NOT NULL,
    overview          TEXT,
    popularity        REAL
);

CREATE TABLE IF NOT EXISTS stg_tmdb_tv (
    tmdb_id          INTEGER NOT NULL PRIMARY KEY,
    name             TEXT NOT NULL,
    original_name    TEXT,
    first_air_date   TEXT,
    last_air_date    TEXT,
    status           TEXT,
    seasons          INTEGER,
    episodes         INTEGER,
    episode_run_time INTEGER,
    vote_average     REAL,
    vote_count       INTEGER,
    poster_path      TEXT,
    networks_json    TEXT NOT NULL DEFAULT '[]',
    genres_json      TEXT NOT NULL DEFAULT '[]',
    creators_json    TEXT NOT NULL DEFAULT '[]',
    raw_page_id      INTEGER REFERENCES raw_api_pages (raw_page_id),
    fetched_at       TEXT NOT NULL
);

-- TMDB's recommendations for films I rated highly: the film suggestion candidates.
CREATE TABLE IF NOT EXISTS stg_tmdb_movie_recommendations (
    tmdb_id           INTEGER NOT NULL,
    recommended_id    INTEGER NOT NULL,
    recommended_title TEXT NOT NULL,
    position          INTEGER NOT NULL,
    PRIMARY KEY (tmdb_id, recommended_id)
);

-- TV candidates use their own ids, separate from movie ids.
CREATE TABLE IF NOT EXISTS stg_tmdb_tv_recommendations (
    tmdb_id           INTEGER NOT NULL,
    recommended_id    INTEGER NOT NULL,
    recommended_title TEXT NOT NULL,
    position          INTEGER NOT NULL,
    source            TEXT NOT NULL DEFAULT 'tmdb' REFERENCES core_sources (source),
    raw_page_id       INTEGER REFERENCES raw_api_pages (raw_page_id),
    PRIMARY KEY (tmdb_id, recommended_id)
);

CREATE TABLE IF NOT EXISTS stg_tmdb_fetches (
    tmdb_id    INTEGER NOT NULL,
    method     TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (tmdb_id, method)
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

-- Track to album lookups (the Dashboard's "On repeat" goes from plays to albums).
CREATE INDEX IF NOT EXISTS ix_core_item_links_child ON core_item_links (child_item_id);

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

-- Period lookups (this month, a recap's month) filter on the local day.
CREATE INDEX IF NOT EXISTS ix_core_event_local_times_date
    ON core_event_local_times (local_date);

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
-- Enrichment: details fetched for recommendations and art
-- ---------------------------------------------------------------------------

-- fields_version: which set of detail fields the fetch asked for (2 adds the synopsis
-- and popularity), so details from before can be fetched again once, when needed.
CREATE TABLE IF NOT EXISTS stg_mal_anime_details (
    mal_anime_id   INTEGER NOT NULL PRIMARY KEY,
    fetched_at     TEXT NOT NULL,
    raw_page_id    INTEGER REFERENCES raw_api_pages (raw_page_id),
    fields_version INTEGER NOT NULL DEFAULT 0
);

-- Saving, deduplicating, and pruning raw pages look them up by request and check
-- whether staging still points at them (taste/raw.py; deletes also run the foreign
-- key checks). Without these, every old page scanned each staging table: seconds
-- per sync on a big library.
CREATE INDEX IF NOT EXISTS ix_raw_api_pages_request
    ON raw_api_pages (source, endpoint, request_params);
CREATE INDEX IF NOT EXISTS ix_stg_lastfm_scrobbles_raw ON stg_lastfm_scrobbles (raw_page_id);
CREATE INDEX IF NOT EXISTS ix_stg_mal_anime_raw ON stg_mal_anime (last_raw_page_id);
CREATE INDEX IF NOT EXISTS ix_stg_mal_list_entries_raw ON stg_mal_list_entries (last_raw_page_id);
CREATE INDEX IF NOT EXISTS ix_stg_mal_anime_details_raw ON stg_mal_anime_details (raw_page_id);

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

-- Who a suggested artist is (artist.getinfo), for the For You panel: the bio's summary
-- as plain text (Last.fm sends a little HTML) and how many people listen. Cached 30
-- days; a miss is a row with every field NULL.
CREATE TABLE IF NOT EXISTS stg_lastfm_artist_info (
    artist_key  TEXT NOT NULL PRIMARY KEY,
    bio         TEXT,
    listeners   INTEGER,
    playcount   INTEGER,
    raw_page_id INTEGER REFERENCES raw_api_pages (raw_page_id),
    fetched_at  TEXT NOT NULL
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

-- Track lengths from Last.fm track.getinfo, for "about how many hours". Scrobbles
-- carry no length. duration_ms NULL means Last.fm has none (it sends 0) or doesn't
-- know the track; a length doesn't change, so rows are kept for good.
CREATE TABLE IF NOT EXISTS stg_lastfm_track_info (
    track_key   TEXT NOT NULL PRIMARY KEY,
    artist_name TEXT NOT NULL,
    track_name  TEXT NOT NULL,
    duration_ms INTEGER,
    fetched_at  TEXT NOT NULL
);

-- Deezer artist search, only for photos. picture_url NULL means no exact match (or no
-- photo), cached like a hit so it isn't asked again for 30 days.
CREATE TABLE IF NOT EXISTS stg_deezer_artists (
    artist_key  TEXT NOT NULL PRIMARY KEY,
    deezer_id   INTEGER,
    name        TEXT NOT NULL DEFAULT '',
    picture_url TEXT,
    fetched_at  TEXT NOT NULL
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

-- One reusable five-fold check per medium and selected model, replaced when its
-- training inputs change. Recommendation runs remain the saved display history.
CREATE TABLE IF NOT EXISTS rec_model_calibrations (
    medium           TEXT NOT NULL,
    model_name       TEXT NOT NULL,
    fingerprint      TEXT NOT NULL,
    predictions_json TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    PRIMARY KEY (medium, model_name)
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
    facts_json   TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (rec_run_id, rank)
);

-- ---------------------------------------------------------------------------
-- Recaps: the monthly and yearly cards, made by the app on its own
-- ---------------------------------------------------------------------------

-- One row per recap made automatically. fingerprint is a hash of the numbers behind
-- the cards: during the late-data window a recap is rebuilt only when it changes.
-- final: past that window, no more automatic rebuilds. announced: the Dashboard
-- banner has shown it.
CREATE TABLE IF NOT EXISTS recap_runs (
    period_kind TEXT NOT NULL,
    period_key  TEXT NOT NULL,
    built_at    TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    cards       INTEGER NOT NULL,
    final       INTEGER NOT NULL DEFAULT 0,
    announced   INTEGER NOT NULL DEFAULT 0,
    pictures_missing INTEGER NOT NULL DEFAULT 0,  -- drawn with placeholders: draw again
    PRIMARY KEY (period_kind, period_key)
);

-- Small per-profile app state that isn't a setting (never shown on the Settings page),
-- like whether past recaps were offered (taste/recap.py).
CREATE TABLE IF NOT EXISTS app_state (
    state_key   TEXT NOT NULL PRIMARY KEY,
    state_value TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

-- "Not interested". App state, not a taste signal yet.
CREATE TABLE IF NOT EXISTS rec_dismissed (
    kind         TEXT NOT NULL,
    item_key     TEXT NOT NULL,
    dismissed_at TEXT NOT NULL,
    PRIMARY KEY (kind, item_key)
);

-- Staging tables added in 2.0.0 point at raw pages too: indexed like the ones above,
-- so pruning old raw pages looks each reference up instead of scanning the table.
CREATE INDEX IF NOT EXISTS ix_stg_lastfm_artist_info_raw ON stg_lastfm_artist_info (raw_page_id);
CREATE INDEX IF NOT EXISTS ix_stg_tmdb_account_ratings_raw
    ON stg_tmdb_account_ratings (last_raw_page_id);
CREATE INDEX IF NOT EXISTS ix_stg_tmdb_movies_raw ON stg_tmdb_movies (raw_page_id);
CREATE INDEX IF NOT EXISTS ix_stg_tmdb_tv_raw ON stg_tmdb_tv (raw_page_id);
CREATE INDEX IF NOT EXISTS ix_stg_tmdb_tv_recommendations_raw
    ON stg_tmdb_tv_recommendations (raw_page_id);
CREATE INDEX IF NOT EXISTS ix_stg_hardcover_user_books_raw
    ON stg_hardcover_user_books (last_raw_page_id);
CREATE INDEX IF NOT EXISTS ix_stg_hardcover_reads_raw ON stg_hardcover_reads (last_raw_page_id);
CREATE INDEX IF NOT EXISTS ix_stg_hardcover_books_raw ON stg_hardcover_books (last_raw_page_id);
