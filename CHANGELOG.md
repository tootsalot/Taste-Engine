# Changelog

Each release's notes on GitHub come from its section here.

## [0.2.0]

First packaged release: a desktop app with profiles, recommendations, and artwork.

- A real Windows app (`taste-engine.exe`) with the After Hours look: dark, lilac and coral, Space Grotesk and Inter, and a two-disc logo
- Profiles, each with its own API keys, settings, and data
- API keys entered in the app and kept in Windows Credential Manager
- Dashboard with recently finished anime, albums on repeat in the last 30 days, my score distribution, and my most generous and harshest genres; the sync log opens only while a sync runs, and stays open if something went wrong
- For You: anime suggestions with a predicted score for me and the reasons behind each, new artists to try, and old favorites to rediscover, each with "Not interested" and a link to MyAnimeList or Last.fm
- Suggestions explained in plain words, with English titles when MAL has one ("Because you loved A and really liked B"); the numbers behind each reason are in the card's tooltip
- Each anime card shows the MAL mean next to my predicted score, and the chance I'd give the show an 8 or more (left out under 20 scored shows)
- Music suggestions labeled Strong match, Good match, or Worth a try, with similarity given as a percent
- With 30 or more scored shows, the anime model scales the MAL mean instead of only shifting it, which predicts harsh critics better
- An accuracy check on the anime predictions, compared with the MAL mean alone
- Posters and album covers, downloaded only from MAL's and Last.fm's image servers and cached on disk (capped at 100 MB); covers that moved to another address on the same server still load
- Artist photos from Deezer when Last.fm has no cover, and as a backup when a cover can't be downloaded (exact name matches only)
- Sync everything also refreshes recommendations; the first refresh takes a few minutes, later ones seconds
- Settings grouped by topic: time zone (with daylight saving), Last.fm capture scope, report thresholds, top-N limits, and recommendation options (anime types are checkboxes)
- Settings opens in Simple mode with the everyday settings, each explained in a sentence under its name; Advanced shows every setting, and the app remembers which was picked
- Sync from the app with live progress; interrupted Last.fm syncs resume
- Raw API pages no longer pile up: identical pages are stored once, and old ones are pruned after 180 days (a setting)
- Reports as charts: my score against MAL's, where I drop shows, genre leans, plays per week, month, or year, top artists and tracks, and plays by hour and day; every report's numbers export as CSV, one at a time or all at once
- Long charts show as many of the latest weeks or months as fit the window, so a wider window shows more history
- A sidebar with icons that collapses to a narrow rail (Ctrl+B) and remembers how it was left, with Settings at the bottom and the last sync just above it
- Thin scroll bars that match the dark theme
- Times shown in the profile's time zone, in plain words ("today at 10:52 PM")
- Visible keyboard focus on buttons, links, checkboxes, and fields
- Existing `data/taste.db` from 0.1 becomes the `default` profile automatically
- The download includes LICENSE.txt and THIRD_PARTY_NOTICES.txt

## [0.1.0]

Command-line version: MyAnimeList and Last.fm sync into SQLite, with reports as CSV.
