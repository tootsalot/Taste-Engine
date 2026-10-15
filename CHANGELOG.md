# Changelog

Each release's notes on GitHub come from its section here.

## [2.0.0]

Books and films join anime and music, every month and year gets a recap, and the whole app was reworked to fill the window and move smoothly.

New sources
- Books from a Hardcover account, connected in the browser with read-only access (OAuth with PKCE, through a one-time listener on 127.0.0.1): my stars in half steps, statuses, every read-through, genres, moods, formats, and covers
- Films and TV from a TMDB account, connected with read-only access: ratings, watchlist, and favorites, counted by the day I rated them
- Hours of music, from Last.fm track lengths looked up after each sync and kept for good, always shown as "about"
- Episodes per month, from the episode counts each MAL sync sees go up

New and redesigned pages
- Rev: a recap of a month or a year as a deck of up to seven phone-sized cards (1080 x 1920) in the After Hours look, swiped through with arrows or the arrow keys; Save this card asks for a folder once and remembers it, and Save all saves the deck; recaps make themselves after each month and year and stay up to date for two weeks while late data comes in, with a Dashboard banner when one is ready
- Reports is a page per medium (Anime, Music, Books, Films and TV), grouped under a Reports heading in the sidebar, all on one shape: All time or a year, six tiles, a picture wall beside the main chart, three charts, and the studios, artists, authors, or directors I keep coming back to
- A Books page modeled on Hardcover's stats, for all time or any year: books, pages, and hours listened, the covers read with rereads marked, books per year, genres, moods, fiction and format, stars, lengths, and most read authors
- For You is a grid of picks with a panel for the selected one, which says what the pick is (synopsis, bio, overview, or description) and how popular it is
- Film suggestions from TMDB's recommendations for the films I rated highest, and book suggestions from Hardcover's similar books for the ones I rated highest, both predicted the same way as anime
- Your taste, across the top of the Dashboard: a one-line description from all my data and the traits behind it, each with its number
- Films and TV rebuilt like Books, for all time or a year: posters with my ratings, ratings for Films or TV, genres, decades, and repeat directors
- Dashboard: a "This month so far" card, a plays-per-week strip, shelves for books, films, and TV, and a getting-started panel for a new profile
- Reports tiles get a second line, and points against MAL a small line by year

Everywhere
- Every page uses the whole window: the Dashboard's cards share its height, Settings flows into columns, and a test keeps bare background under 15% at 2560 x 1380 and 1920 x 1040
- Smooth motion: pages fade and rise, the sidebar and panels slide, pictures fade in, and suggestions arrive and leave gently; Settings has Full, Reduced, or Off, and the app follows Windows when its animations are off
- Books, films, and TV in the CSV export

Fixes
- Tooltips show titles from the services exactly as written, never as markup
- Closing the app no longer risks a crash while windows are torn down, and a recap finishing after its page closed no longer touches it
- Status messages no longer draw over the version line
- Most rated directors no longer lists the same person once per film
- Charts whose labels skip a bar (whole stars over half stars) no longer lose every label
- Pictures fetched twice at once no longer fail on Windows ("Access is denied" in the console)
- Fades on widgets that are rebuilt mid-fade no longer flood the console with Qt warnings
- A full-screen Dashboard fits without scrolling again, Your taste and all four media included

Upgrading
- Books imported from a Goodreads export (with Open Library covers) are removed the first time 2.0.0 opens a profile, since Hardcover holds the same history; connect Hardcover in Settings to bring them back. The export file itself isn't touched.

## [0.2.0]

First packaged release: a desktop app with profiles, recommendations, and artwork.

- A real Windows app with the After Hours look: dark, lilac and coral, Space Grotesk and Inter, and a two-disc logo
- A Windows installer: per user with no admin prompt, a Start menu shortcut and an optional desktop one; running it again offers Update, Repair, or Uninstall, and uninstalling keeps profiles and settings unless asked to delete them (saved API keys included)
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
- Pages fill a wide window: long charts show as many of the latest weeks or months as fit, the Dashboard's shelves as many posters and covers (only those on screen are downloaded), and For You puts up to four cards side by side
- A sidebar with icons that collapses to a narrow rail (Ctrl+B) and remembers how it was left, with Settings at the bottom and the last sync just above it
- Thin scroll bars that match the dark theme
- Times shown in the profile's time zone, in plain words ("today at 10:52 PM")
- Visible keyboard focus on buttons, links, checkboxes, and fields
- Existing `data/taste.db` from 0.1 becomes the `default` profile automatically
- The installed app includes LICENSE.txt and THIRD_PARTY_NOTICES.txt (Settings, About, Third-party notices)

## [0.1.0]

Command-line version: MyAnimeList and Last.fm sync into SQLite, with reports as CSV.
