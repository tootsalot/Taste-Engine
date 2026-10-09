# Data Dictionary

Every table and view in a profile database (`data/profiles/<profile_id>.db` from source, `%LOCALAPPDATA%\taste-engine\profiles\` in the packaged app). Each profile has its own file with the same schema. The DDL lives in `taste/sql/schema.sql` and `taste/sql/views.sql`.

Conventions used throughout:

- **Timestamps** are UTC text in the form `YYYY-MM-DD HH:MM:SS`.
- **Dates** are text `YYYY-MM-DD`. MAL sometimes gives partial dates (`YYYY` or `YYYY-MM`), and those are kept as sent.
- **Booleans** are INTEGER 0 or 1.
- **`source`** is a code from `core_sources` (`mal`, `lastfm`, `deezer`).
- Columns inside unique keys use `''` for "empty" instead of NULL, so the keys behave the same in SQLite and SQL Server.

Table prefixes: `sync_` bookkeeping, `raw_` API pages as fetched, `stg_` per-source staging, `core_` source-agnostic model, `rec_` saved recommendations, `rpt_` report views.

Posters and album covers aren't stored in the database. Only their URLs are (`core_item_images`); the pictures themselves are cached as files in `<data folder>/cache/images`, capped at 100 MB.

Window preferences shared by every profile (for now, whether the sidebar is collapsed) live in `<data folder>/ui_state.json`, not in any database. A missing or unreadable file means the defaults.

---

## Bookkeeping

### `sync_runs`
One row per sync attempt.

| Column | Type | Description |
|---|---|---|
| sync_run_id | INTEGER PK | Surrogate key. |
| source | TEXT | Which source was synced: `mal`, `lastfm`, or `deezer` (artist photo lookups, always mode `enrich`). |
| mode | TEXT | `full` (whole history or whole list), `incremental` (Last.fm, new window only), `resume` (Last.fm, finishing an interrupted window first), or `enrich` (fetching details for recommendations; not shown as a sync in the app). |
| started_at | TEXT | When the run started (UTC). |
| ended_at | TEXT | When it finished. NULL while running. |
| status | TEXT | `running`, `success`, or `failed`. A run left `running` by a killed process is marked `failed` with the message "interrupted" the next time that source syncs. |
| pages_fetched | INTEGER | API pages successfully fetched and stored. |
| rows_fetched | INTEGER | Items in those pages (MAL list entries or completed scrobbles; now-playing tracks aren't counted). |
| rows_inserted | INTEGER | New staging rows. |
| rows_updated | INTEGER | Staging rows that changed (MAL list edits). Always 0 for Last.fm, since a scrobble never changes. |
| error_message | TEXT | Why the run failed. API keys and client IDs are scrubbed out. |

### `sync_lastfm_windows`
The Last.fm resume ledger. Each sync fetches a fixed time window `[from_unix, to_unix)` newest first and records how far it got.

| Column | Type | Description |
|---|---|---|
| window_id | INTEGER PK | Surrogate key. |
| opened_by_run_id | INTEGER FK sync_runs | The run that created the window. |
| from_unix | INTEGER | Inclusive lower bound (Unix seconds). 0 means the start of history. Incremental windows start 14 days before the previous window's end, to catch late scrobbles. |
| to_unix | INTEGER | Exclusive upper bound, fixed when the window opens, so new scrobbles can't shift the pages mid-run. |
| cursor_unix | INTEGER | Oldest scrobble committed so far in this window. A resumed run requests `to = cursor_unix + 1`. |
| status | TEXT | `open` (unfinished, the next run resumes it) or `complete`. |
| completed_at | TEXT | When the last page was committed. |

---

## Raw

### `raw_api_pages`
Every successful API response page, unchanged. Identical repeats aren't stored twice, Last.fm pages with nothing new aren't kept, and pages older than `raw_retention_days` (default 180) are pruned after each sync unless staging points to them or they're the latest copy of their request. This makes re-processing possible without calling the APIs again.

| Column | Type | Description |
|---|---|---|
| raw_page_id | INTEGER PK | Surrogate key. |
| sync_run_id | INTEGER FK sync_runs | The run that fetched it. |
| source | TEXT | Source code. |
| endpoint | TEXT | `/users/{username}/animelist` or `/anime/{id}` (details) for MAL. `user.getrecenttracks`, `artist.getsimilar`, `artist.gettoptags`, or `artist.gettopalbums` for Last.fm. `search/artist` for Deezer. |
| request_params | TEXT | JSON of the query parameters, with `api_key` and other secrets removed. The MAL client ID is a header, so it's never here. |
| page_number | INTEGER | Page number within the run (MAL) or window fetch (Last.fm), starting at 1. |
| http_status | INTEGER | HTTP status of the response. |
| fetched_at | TEXT | When it was fetched (UTC). |
| payload | TEXT | The full JSON body. |

---

## Staging: MyAnimeList

### `stg_mal_anime`
One row per anime that has appeared on my list or was fetched as a recommendation candidate. These fields describe the show itself, not my opinion of it.

| Column | Type | Description |
|---|---|---|
| mal_anime_id | INTEGER PK | MAL's anime ID. |
| title | TEXT | Main title as MAL lists it (usually romaji). |
| title_en | TEXT | English title. NULL when MAL has none. |
| title_ja | TEXT | Japanese title. NULL when MAL has none. |
| synonyms_json | TEXT | JSON array of other titles. |
| media_type | TEXT | `tv`, `movie`, `ona`, `ova`, `special`, `tv_special`, `music`, `pv`. |
| num_episodes | INTEGER | Episode count. NULL when unknown (MAL sends 0, usually for shows still airing). |
| start_season_year | INTEGER | Year of the premiere season. |
| start_season | TEXT | `winter`, `spring`, `summer`, `fall`. |
| airing_status | TEXT | MAL `status`, for example `finished_airing` or `currently_airing`. |
| start_date | TEXT | Air start date, possibly partial. |
| end_date | TEXT | Air end date, possibly partial. |
| content_rating | TEXT | MAL `rating`, for example `pg_13`. |
| source_material | TEXT | MAL `source`, for example `manga` or `original`. |
| avg_episode_seconds | INTEGER | Average episode length in seconds. |
| community_mean | REAL | MAL community mean score (1 to 10). NULL when MAL has too few ratings. |
| num_scoring_users | INTEGER | How many MAL users scored it. |
| main_picture_url | TEXT | Poster URL on MAL's image server (`main_picture.medium`). Kept when a later response leaves it out. |
| nsfw_rating | TEXT | MAL's `nsfw` value: `white` (safe), `gray`, or `black` (adult). Recommendations leave out `black` shows unless the profile includes NSFW anime. |
| last_raw_page_id | INTEGER FK raw_api_pages | The raw page this row was last loaded from. |
| loaded_at | TEXT | When this row was last written. |

### `stg_mal_anime_genres`
Genres per anime. MAL mixes true genres, themes (Isekai), and demographics (Shounen) in one list and doesn't label which is which.

| Column | Type | Description |
|---|---|---|
| mal_anime_id | INTEGER PK, FK | The anime. |
| genre_id | INTEGER PK | MAL genre ID. |
| genre_name | TEXT | Genre name. |

### `stg_mal_anime_studios`
Studios per anime.

| Column | Type | Description |
|---|---|---|
| mal_anime_id | INTEGER PK, FK | The anime. |
| studio_id | INTEGER PK | MAL studio ID. |
| studio_name | TEXT | Studio name. |

### `stg_mal_list_entries`
My list status for each anime. This is my side: what I scored, how far I got, and so on.

| Column | Type | Description |
|---|---|---|
| mal_anime_id | INTEGER PK, FK | The anime. |
| status | TEXT | `watching`, `completed`, `on_hold`, `dropped`, `plan_to_watch`. |
| score | INTEGER | My score, 1 to 10. **NULL means not scored** (MAL sends 0). |
| num_episodes_watched | INTEGER | Episodes watched. |
| is_rewatching | INTEGER | 1 if I'm currently rewatching. |
| num_times_rewatched | INTEGER | Completed rewatches. |
| priority | INTEGER | MAL priority (0 low, 1 medium, 2 high). |
| rewatch_value | INTEGER | MAL rewatch value (0 to 5). |
| tags_json | TEXT | JSON array of my MAL tags. |
| comments | TEXT | My MAL comments. |
| start_date | TEXT | Date I started, as I entered it. NULL if I didn't. |
| finish_date | TEXT | Date I finished, as I entered it. NULL if I didn't. |
| mal_updated_at | TEXT | Last time I edited the entry on MAL (UTC). |
| first_seen_at | TEXT | First time a sync saw this entry. |
| last_seen_run_id | INTEGER FK sync_runs | The last sync that saw it. |
| removed_at | TEXT | Set when a complete sync no longer finds the entry (I removed it from my list). Cleared if it comes back. |
| last_raw_page_id | INTEGER FK raw_api_pages | The raw page this row was last loaded from. |

---

## Staging: Last.fm

### `stg_lastfm_scrobbles`
One row per completed scrobble. **Desktop listening only.**

| Column | Type | Description |
|---|---|---|
| scrobble_id | INTEGER PK | Surrogate key. |
| played_at_unix | INTEGER | When the track was played, Unix seconds (Last.fm `date.uts`). |
| played_at_utc | TEXT | Same moment as UTC text. |
| artist_name | TEXT | Artist credit as scrobbled. |
| artist_mbid | TEXT | MusicBrainz artist ID, `''` if empty (common). |
| track_name | TEXT | Track title as scrobbled. |
| track_mbid | TEXT | MusicBrainz recording ID, `''` if empty. |
| album_name | TEXT | Album title, `''` if none. |
| album_mbid | TEXT | MusicBrainz release ID, `''` if empty. |
| track_url | TEXT | Last.fm page for the track. |
| image_url | TEXT | Album cover URL from the scrobble (the 300 px `extralarge` size when Last.fm sent one). NULL when there's none or it's Last.fm's blank placeholder. Older rows were filled in from the raw pages, without new API calls. |
| raw_page_id | INTEGER FK raw_api_pages | The raw page it came from. |
| loaded_at | TEXT | When it was inserted. |

Unique on `(played_at_unix, artist_name, track_name)`. That's the dedupe key, since MBIDs are often empty.

---

## Staging: enrichment

Extra details fetched for recommendations and art, cached and refreshed about every 30 days. Last.fm artist keys are the lowercased artist name, the same as `lastfm_artist_key` in `core_creator_external_ids`.

### `stg_mal_anime_details`
Which anime have had their MAL details fetched (`/anime/{id}`), and when. The details themselves land in `stg_mal_anime` and the two tables below.

| Column | Type | Description |
|---|---|---|
| mal_anime_id | INTEGER PK | The anime. |
| fetched_at | TEXT | When the details were last fetched (UTC). |
| raw_page_id | INTEGER FK raw_api_pages | The raw response. |

### `stg_mal_anime_recommendations`
MAL's "users who liked this also recommend" list for a show.

| Column | Type | Description |
|---|---|---|
| mal_anime_id | INTEGER PK | The show the recommendation is from. |
| recommended_id | INTEGER PK | The recommended show. |
| recommended_title | TEXT | Its title as MAL sent it. |
| num_recommendations | INTEGER | How many MAL users made this recommendation. |

### `stg_mal_related_anime`
Sequels, prequels, side stories, and other relations.

| Column | Type | Description |
|---|---|---|
| mal_anime_id | INTEGER PK | The show. |
| related_id | INTEGER PK | The related show. |
| related_title | TEXT | Its title as MAL sent it. |
| relation_type | TEXT | MAL's relation, for example `sequel`, `prequel`, `side_story`, `summary`. `other` when MAL sends none. |

### `stg_lastfm_artist_fetches`
When each Last.fm artist method was last called for an artist, so refreshes only fetch what's stale.

| Column | Type | Description |
|---|---|---|
| artist_key | TEXT PK | The artist. |
| method | TEXT PK | `artist.getsimilar` or `artist.gettoptags`. |
| fetched_at | TEXT | When it was last fetched (UTC). |

### `stg_lastfm_similar_artists`
Last.fm's similar artists for each artist I play a lot.

| Column | Type | Description |
|---|---|---|
| artist_key | TEXT PK | The artist I play. |
| similar_key | TEXT PK | The similar artist. |
| similar_name | TEXT | Its name as Last.fm sent it. |
| similar_mbid | TEXT | MusicBrainz artist ID, `''` if empty. |
| match | REAL | Last.fm's similarity, 0 to 1. |

### `stg_lastfm_artist_tags`
Last.fm's top tags for an artist.

| Column | Type | Description |
|---|---|---|
| artist_key | TEXT PK | The artist. |
| tag | TEXT PK | Tag name, as Last.fm sent it. |
| tag_count | INTEGER | Last.fm's relative weight for the tag (0 to 100). |

### `stg_lastfm_artist_top_album`
The top album of a suggested artist I've never played, fetched only so the suggestion has a cover. Last.fm has no artist photos (it returns the same placeholder for every artist), so an artist's picture is an album cover, or a Deezer photo (below) when there's no cover.

| Column | Type | Description |
|---|---|---|
| artist_key | TEXT PK | The artist. |
| album_name | TEXT | The album's title. |
| image_url | TEXT | Cover URL. NULL when Last.fm has none. |
| fetched_at | TEXT | When it was fetched (UTC). |

### `stg_deezer_artists`
Deezer's artist search, used only for photos of suggested artists. Every suggested artist is looked up and cached for 30 days, misses included, so a miss isn't asked again on every refresh. The photo is the picture when Last.fm has no cover, and otherwise a backup the card switches to when the cover can't be downloaded.

| Column | Type | Description |
|---|---|---|
| artist_key | TEXT PK | The artist, keyed like the Last.fm tables. |
| deezer_id | INTEGER | Deezer's artist ID. NULL when there was no match. |
| name | TEXT | The name Deezer matched. Only an exact (case-insensitive) match counts, so a wrong photo is never used. `''` when there was no match. |
| picture_url | TEXT | Photo URL. NULL when there was no exact match, or Deezer only had its blank placeholder. |
| fetched_at | TEXT | When it was looked up (UTC). |

---

## Core

### `core_sources`
Reference list of data sources, seeded by `schema.sql`.

| Column | Type | Description |
|---|---|---|
| source | TEXT PK | Source code. |
| display_name | TEXT | Human-readable name. |
| default_capture_scope | TEXT | How complete this source's data is. MAL: `self_reported`. Last.fm: copied from the profile's `lastfm_capture_scope` setting (`desktop_only`, `mobile_only`, `all_devices`, `unknown`). Deezer: `not_applicable`, since it supplies photos, not taste data. |
| scope_note | TEXT | A plain-English caveat. The Last.fm note comes from the profile's `lastfm_scope_note` setting (or a standard note for the scope) and is copied into every Last.fm report as `data_scope`. |

### `core_items`
One row per piece of media, whatever the type. Anime that only appear as recommendation candidates get a row too, so they can carry a poster and similarity links.

| Column | Type | Description |
|---|---|---|
| item_id | INTEGER PK | Surrogate key. |
| media_type | TEXT | `anime`, `track`, `album`. Later `book`, `film`. |
| title | TEXT | Display title. For Last.fm items, it's the spelling from the oldest scrobble. |
| title_alt | TEXT | Alternate title, for example the English title of an anime. |
| release_year | INTEGER | MAL start season year (or air start year). NULL for Last.fm items. |
| source | TEXT | The source that created the item. |
| created_at | TEXT | When it was created. |
| updated_at | TEXT | When its descriptive fields last changed. |

### `core_item_external_ids`
Maps items to their IDs in each source. Entity resolution will plug in here later: matching means pointing another source's ID at an existing `item_id`.

| Column | Type | Description |
|---|---|---|
| source | TEXT PK | Source of the ID. |
| id_type | TEXT PK | `mal_anime_id`, `lastfm_track_key`, `lastfm_album_key`, `musicbrainz_recording`, `musicbrainz_release`. |
| external_id | TEXT PK | The ID value. Last.fm keys are lowercased `artist` + separator + `track` (or `album`), because MBIDs are often empty. |
| item_id | INTEGER FK core_items | The item. |
| match_method | TEXT | `source_native` in Phase 1. Later `exact`, `manual`, `fuzzy`. |
| match_confidence | REAL | NULL for `source_native`. |

### `core_item_links`
Relations between items.

| Column | Type | Description |
|---|---|---|
| parent_item_id | INTEGER PK, FK | For `appears_on`, the album. |
| child_item_id | INTEGER PK, FK | For `appears_on`, the track. |
| link_type | TEXT PK | `appears_on` in Phase 1. |
| source | TEXT | Source of the link. |

### `core_item_images`
Picture URLs for items. The pictures are downloaded when first shown and cached as files, only from MAL's and Last.fm's image servers.

| Column | Type | Description |
|---|---|---|
| item_id | INTEGER PK, FK | The item. |
| source | TEXT PK | Where the URL came from. |
| kind | TEXT PK | `poster` (MAL anime) or `cover` (Last.fm album, the newest cover seen on a scrobble). |
| url | TEXT | The image URL. |

### `core_item_similarity`
Links between items that a source considers alike. Only MAL fills it so far: Last.fm's similar artists are creators, not items, and stay in `stg_lastfm_similar_artists`.

| Column | Type | Description |
|---|---|---|
| item_id | INTEGER PK, FK | The item the link is from. |
| similar_item_id | INTEGER PK, FK | The item it points to. |
| source | TEXT PK | Who says they're alike. |
| kind | TEXT PK | `user_recommended` (MAL user recommendations) or `related_<relation>`, for example `related_sequel`. |
| score | REAL | For `user_recommended`, the number of users who recommended it. 1.0 for related shows. |

Rewritten for a show each time its details are fetched.

### `core_creators`
Artists, studios, and later authors and directors.

| Column | Type | Description |
|---|---|---|
| creator_id | INTEGER PK | Surrogate key. |
| name | TEXT | Display name (for Last.fm, the spelling from the oldest scrobble). |
| source | TEXT | Source that created it. |
| created_at | TEXT | When it was created. |

### `core_creator_external_ids`
Same idea as the item version.

| Column | Type | Description |
|---|---|---|
| source | TEXT PK | Source of the ID. |
| id_type | TEXT PK | `mal_studio_id`, `lastfm_artist_key` (lowercased name), `musicbrainz_artist`. |
| external_id | TEXT PK | The ID value. |
| creator_id | INTEGER FK core_creators | The creator. |
| match_method | TEXT | `source_native` in Phase 1. |
| match_confidence | REAL | NULL for `source_native`. |

### `core_item_creators`
Which creators made which items, and in what role. The role is on the link, because one person can be an author on one item and a director on another.

| Column | Type | Description |
|---|---|---|
| item_id | INTEGER PK, FK | The item. |
| creator_id | INTEGER PK, FK | The creator. |
| role | TEXT PK | `studio` (MAL) or `artist` (Last.fm). Later `author`, `director`. |
| source | TEXT | Source of the link. |

### `core_tags`
Tag vocabulary.

| Column | Type | Description |
|---|---|---|
| tag_id | INTEGER PK | Surrogate key. |
| name | TEXT | Tag name. |
| tag_kind | TEXT | `genre` in Phase 1. Unique together with `name`. |

### `core_item_tags`
Tags on items.

| Column | Type | Description |
|---|---|---|
| item_id | INTEGER PK, FK | The item. |
| tag_id | INTEGER PK, FK | The tag. |
| source | TEXT PK | Who applied the tag. |
| weight | REAL | NULL for MAL genres. Reserved for weighted tags like Last.fm tag counts. |

### `core_ratings`
**What I thought.** My current score for an item in each source. Unscored items have no row.

| Column | Type | Description |
|---|---|---|
| rating_id | INTEGER PK | Surrogate key. |
| item_id | INTEGER FK | The item. |
| source | TEXT | Where the rating came from. Unique together with `item_id`. |
| raw_score | REAL | The score as given (MAL 1 to 10). |
| scale_min | REAL | Bottom of the source's scale (MAL 1). |
| scale_max | REAL | Top of the source's scale (MAL 10). |
| normalized_score | REAL | `(raw_score - scale_min) / (scale_max - scale_min)`, so the bottom of any scale is 0.0 and the top is 1.0. |
| source_updated_at | TEXT | MAL `updated_at`. MAL doesn't record when the score itself changed. |

### `core_community_ratings`
**What everyone else thought.** Kept separate from my ratings so the two never mix.

| Column | Type | Description |
|---|---|---|
| item_id | INTEGER PK, FK | The item. |
| source | TEXT PK | Where the mean came from. |
| mean_score | REAL | Community mean on the source's scale. |
| scale_min | REAL | Bottom of the scale. |
| scale_max | REAL | Top of the scale. |
| normalized_mean | REAL | Same formula as `normalized_score`. |
| num_raters | INTEGER | Number of people who rated it. |
| observed_at | TEXT | When this value was fetched. The mean drifts over time, and only the latest value is kept. |

### `core_behavior_events`
**What I actually did.** Plays and watches with times.

| Column | Type | Description |
|---|---|---|
| event_id | INTEGER PK | Surrogate key. |
| item_id | INTEGER FK | The track or anime. |
| source | TEXT | Source of the event. |
| event_type | TEXT | `play` (Last.fm), `watch_started`, `watch_finished` (MAL). |
| occurred_at_utc | TEXT | When it happened. For day, month, or year precision, it's the calendar date I entered with time `00:00:00`, and that date has no time zone. |
| occurred_at_unix | INTEGER | Unix seconds when the exact moment is known. NULL for date-only events. |
| time_precision | TEXT | `second`, `day`, `month`, or `year`. |
| time_basis | TEXT | Where the time came from: `source_timestamp` (Last.fm scrobble time), `user_entered` (a MAL date I typed), or `fallback_list_updated_at` (see below). |
| capture_scope | TEXT | Last.fm: the profile's capture scope at the time the play was synced. Changing the setting later doesn't relabel old plays. MAL: `self_reported`. |
| source_event_key | TEXT | Dedupe key, unique per source. Last.fm: `uts\|artist\|track`. MAL: `{anime_id}:started` or `{anime_id}:finished`. |

**About `fallback_list_updated_at`:** when a completed MAL entry has no finish date, its `watch_finished` event uses the last time I edited the entry. That's an upper bound, not the real finish date. Most of these stand-in dates land on one bulk-edit day, so for timelines, filter to `user_entered` or treat fallbacks as "finished on or before." Fallbacks are never skipped. Start dates have no fallback.

### `core_curation`
**What I chose to save or list.**

| Column | Type | Description |
|---|---|---|
| curation_id | INTEGER PK | Surrogate key. |
| item_id | INTEGER FK | The item. |
| source | TEXT | Source. |
| curation_type | TEXT | `list_status` (MAL). Later `favorite`, `loved`, `list_member`, `shelf`. |
| list_name | TEXT | For named lists later. `''` when not applicable. |
| value | TEXT | For `list_status`: `watching`, `completed`, `on_hold`, `dropped`, `plan_to_watch`. |
| progress | INTEGER | Episodes watched. Later pages read, and so on. |
| progress_total | INTEGER | Total episodes. NULL if unknown. |
| is_repeat | INTEGER | 1 if currently rewatching. |
| repeat_count | INTEGER | Number of completed rewatches. |
| source_updated_at | TEXT | When the entry was last edited at the source. |

Unique on `(source, item_id, curation_type, list_name)`.

### `core_event_local_times`
Event times converted to the profile's time zone by Python (`taste/local_time.py`, using `zoneinfo`, so daylight saving is handled). Rebuilt when the time zone setting changes. Only events with an exact time (`occurred_at_unix` not NULL) get a row.

| Column | Type | Description |
|---|---|---|
| event_id | INTEGER PK, FK | The event. Deleted with it (`ON DELETE CASCADE`). |
| occurred_at_unix | INTEGER | Copy of the event's time, used to spot events whose time changed. |
| tz_name | TEXT | IANA time zone used, for example `America/Phoenix`. |
| local_ts | TEXT | Local timestamp `YYYY-MM-DD HH:MM:SS`. |
| local_date | TEXT | Local date. |
| local_year | INTEGER | Local year. |
| local_month | TEXT | Local month `YYYY-MM`. |
| local_hour | INTEGER | Local hour, 0 to 23. |
| local_weekday_num | INTEGER | 0 = Sunday through 6 = Saturday. |

---

## Profile settings

### `app_settings`
One row per setting. Types, defaults, and validation live in `taste/settings.py`. API keys are never stored here.

| Column | Type | Description |
|---|---|---|
| setting_key | TEXT PK | Profile: `display_name`, `mal_username`, `lastfm_username`, `timezone`, `include_nsfw`. Last.fm: `lastfm_capture_scope`, `lastfm_scope_note`. Reports: `genre_min_sample`, `in_line_threshold`, `top_n_all_time`, `top_n_per_year`, `top_n_per_month`. Recommendations: `rec_count`, `rec_min_raters`, `rec_media_types`, `rec_include_plan_to_watch`, `rec_seed_artists`. Syncing and storage: `lastfm_lookback_days`, `raw_retention_days`. |
| setting_value | TEXT | The value as text (booleans are `1` / `0`, `rec_media_types` is a comma-separated list such as `tv,movie,ona,ova`). Views `CAST` the numeric ones. |
| updated_at | TEXT | When it was last changed (UTC). |

---

## Recommendations

Every recommendation run is saved with its settings, scores, and reasons, so a list can be explained later and compared with older runs. The For You page shows the newest run of each kind.

### `rec_runs`
One row per computed list.

| Column | Type | Description |
|---|---|---|
| rec_run_id | INTEGER PK | Surrogate key. |
| kind | TEXT | `anime`, `music_discover` (artists I've never played), or `music_rediscover` (artists I played a lot who have gone quiet). |
| created_at | TEXT | When it was computed (UTC). |
| params_json | TEXT | The recommendation settings used, as JSON. |
| metrics_json | TEXT | For `anime`: `model_overall_bias` (my usual difference from the MAL mean), and the holdout check: `held_out` (scored shows hidden from the model), and the average error in MAL points of `model`, `community` (the MAL mean alone), and `overall` (the MAL mean plus my usual difference). `{}` for music. |

### `rec_items`
The suggestions in a run, best first.

| Column | Type | Description |
|---|---|---|
| rec_run_id | INTEGER PK, FK rec_runs | The run. Deleted with it. |
| rank | INTEGER PK | Position in the list, from 1. |
| item_key | TEXT | MAL anime ID, or the Last.fm artist key. |
| title | TEXT | Display title (the English title for anime when MAL has one). |
| subtitle | TEXT | Short facts line. `anime`: type, year, and episodes ("TV · 2019 · 24 eps"); the MAL mean is in `facts_json`. `music_discover`: "Sounds like 3 artists you play". `music_rediscover`: when I last played them ("Last played Mar 4, 2025"). |
| score | REAL | `anime`: predicted score for me (1 to 10). `music_discover`: match strength, the sum of weighted similarities, so it can pass 1. The card shows a match label instead (see `facts_json`). `music_rediscover`: all-time plays. |
| support | REAL | `anime`: how strongly shows I liked point at it, the tiebreaker. Each liked show adds (my score minus my average) times log(1 + users recommending), and a sequel or other related show adds (my score minus my average). 0 for Plan to Watch shows nothing points at, and for music. |
| badge | TEXT | A label such as "On your Plan to Watch". `''` when none. |
| image_url | TEXT | Poster or cover URL. NULL when there's none. |
| url | TEXT | Link to the show on MyAnimeList or the artist on Last.fm. |
| reasons_json | TEXT | JSON array of the plain-English reasons shown on the card. |
| facts_json | TEXT | JSON object of extra numbers for the card, `{}` for runs saved before it existed. `mal_mean`: the show's MAL mean, shown next to the prediction. `chance_8_plus`: the chance (0 to 1) that I'd give it an 8 or more, from how far my real scores landed from predictions made without them (5 folds); missing under 20 scored shows. `match_label`: "Strong match", "Good match", or "Worth a try", by thirds of the `music_discover` list. `fallback_image`: a Deezer photo the card switches to if the cover can't be downloaded. `details`: the numbers behind the reasons (vote counts, genre leans, match strength), shown as the card's tooltip. |

### `rec_dismissed`
"Not interested". These are never suggested again. App state, not a taste signal (yet).

| Column | Type | Description |
|---|---|---|
| kind | TEXT PK | `anime` or `artist`. |
| item_key | TEXT PK | MAL anime ID or Last.fm artist key. |
| dismissed_at | TEXT | When it was dismissed (UTC). |

---

## Report views

All views read from core, plus `app_settings` through `rpt_settings`. Every Last.fm view includes **`data_scope`** (the profile's scope note) and **`capture_scopes`** (the scope stored on the rows, or `mixed` if plays were captured under different settings).

| View | Columns |
|---|---|
| `rpt_settings` | Helper. One row: genre_min_sample, in_line_threshold, top_n_all_time, top_n_per_year, top_n_per_month, timezone. |
| `rpt_lastfm_scope` | Helper. One row: data_scope, capture_scopes. |
| `rpt_mal_score_vs_community` | item_id, title, title_english, release_year, list_status, my_score, community_mean, score_diff (mine minus community), normalized_diff, community_raters |
| `rpt_mal_critic_summary` | shows_scored, my_avg_score, community_avg_score, avg_diff, share_scored_below, share_scored_above |
| `rpt_mal_genre_vs_community` | genre, shows_scored, my_avg_score, community_avg_score, avg_diff, diff_vs_my_norm (genre avg_diff minus my overall avg_diff), vs_community (`above` if avg_diff >= the `in_line_threshold` setting, `below` if <= minus it, else `in_line`), min_sample_size (the `genre_min_sample` setting). Genres with fewer scored shows are left out. |
| `rpt_mal_dropped_on_hold` | item_id, title, title_english, list_status, episodes_watched, total_episodes, pct_complete (NULL when total unknown), my_score, last_updated_utc |
| `rpt_lastfm_plays_local` | Helper. event_id, track_item_id, track, artist_id, artist, occurred_at_utc, occurred_at_local, local_year, local_month, local_hour, local_weekday_num (0 = Sunday), tz_name, capture_scope, data_scope. Local means the profile's time zone setting, from `core_event_local_times`. |
| `rpt_lastfm_top_artists_all_time` | play_rank (`top_n_all_time`, default 50), artist, plays, distinct_tracks, first_play_local, last_play_local, data_scope, capture_scopes |
| `rpt_lastfm_top_artists_by_year` | local_year, play_rank (`top_n_per_year`, default 25), artist, plays, data_scope, capture_scopes |
| `rpt_lastfm_top_artists_by_month` | local_month, play_rank (`top_n_per_month`, default 10), artist, plays, data_scope, capture_scopes |
| `rpt_lastfm_top_tracks_all_time` | play_rank (`top_n_all_time`, default 50), track, artist, plays, first_play_local, last_play_local, data_scope, capture_scopes |
| `rpt_lastfm_top_tracks_by_year` | local_year, play_rank (`top_n_per_year`, default 25), track, artist, plays, data_scope, capture_scopes |
| `rpt_lastfm_top_tracks_by_month` | local_month, play_rank (`top_n_per_month`, default 10), track, artist, plays, data_scope, capture_scopes |
| `rpt_lastfm_by_hour` | local_hour (all 24), plays, pct_of_plays, data_scope, capture_scopes |
| `rpt_lastfm_by_weekday` | local_weekday_num, weekday (all 7), plays, pct_of_plays, data_scope, capture_scopes |

Ties in the ranked views are broken alphabetically, so the order is stable.
