# Changelog

Each release's notes on GitHub come from its section here.

## [0.2.0]

First packaged release (in progress): a desktop app with profiles.

- Profiles, each with its own API keys, settings, and data
- API keys entered in the app and kept in Windows Credential Manager
- Settings for time zone (with daylight saving), Last.fm capture scope, report thresholds, and top-N limits
- Sync from the app with live progress; interrupted Last.fm syncs resume
- Reports with CSV export
- Existing `data/taste.db` from 0.1 becomes the `default` profile automatically

## [0.1.0]

Command-line version: MyAnimeList and Last.fm sync into SQLite, with reports as CSV.
