# Changelog

Each release's notes on GitHub come from its section here.

## [0.2.0]

First packaged release: a local web app with profiles.

- Web app that runs on your own computer (`taste-engine.exe`, or `python -m taste app`)
- Profiles, each with its own API keys, settings, and data
- API keys entered in the app and kept in Windows Credential Manager
- Settings for time zone (with daylight saving), Last.fm capture scope, report thresholds, and top-N limits
- Sync from the app with live progress; interrupted Last.fm syncs resume
- Reports in the browser with CSV downloads
- Existing `data/taste.db` from 0.1 becomes the `default` profile automatically

## [0.1.0]

Command-line version: MyAnimeList and Last.fm sync into SQLite, with reports as CSV.
