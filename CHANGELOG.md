# Changelog

Each release's notes on GitHub come from its section here.

## [2.3.0]

TV suggestions join For You, the Dashboard shows what's airing this week, and Rev offers to make the recaps for every month and year before I started using it.

Rev
- On the first launch with past history, or after a new profile's first sync, the Dashboard offers to make my missing monthly and yearly recaps
- Progress stays on the sync line, and an interrupted batch continues next time without repeating recaps already made
- Not now stops the offer; I can still open an older month or year on Rev

Dashboard
- Airing now sits beside This month for currently airing shows on my Watching list, with posters and the next weekly broadcast in my time zone
- Each row says which episode is next for me, marks the final known episode, and puts shows with no set time last
- The card follows my clock and time-zone settings without another sync, hides itself when empty, and leaves adult shows out while Include NSFW anime is off; like the other shelves, it never pictures an adult poster
- Dashboard cards can turn it off, and a long airing list scrolls inside the card so the full-screen Dashboard still fits
- Compact sync-line buttons and equal-height recap notices keep progress and banner changes from moving the page

For You
- TV has its own tab after Films, with picks from shows I rated above my own TV average and my watchlist
- TV picks use the film model with my TV ratings, genre and creator leans, and the same caution on averages from few votes
- Each TV pick gives its reason in one sentence, the way films do, and its subtitle says the episode length
- TV picks leave out anime (animation from Japan), since the Anime tab covers it; animation from elsewhere stays
- Shows not out yet stay out, watchlist picks are marked, and Not interested hides a TV pick without hiding a film with the same TMDB id
- The TMDB vote setting now says it covers both film and TV suggestions

Under the hood
- Raw-page pruning knows about the pages TV recommendations point to, with an index to find them
- Film and TV descriptions missing from older syncs are filled in from stored responses once per refresh
- Each show's original language and country of origin are kept, and filled in once from stored responses for shows synced before

## [2.2.0]

An audit of the whole app: Rev's Rebuild really rebuilds, adult anime is hidden without losing any history, and about thirty fixes for wrong numbers, crashes at exit, and wasted requests.

Rev
- Rebuild asks again for pictures that couldn't be downloaded before, instead of keeping the placeholder until the app restarts
- Rebuild also replaces the cards of that recap I'd already saved, found by which card they are and renamed if the deck grew; cards I never saved stay unsaved
- A recap made while a picture couldn't load is drawn again by itself once it can
- The year card never calls a later show the first or last finish of the year

Adult anime
- Every sync now brings in adult shows, so turning off "Include NSFW anime" never removes their scores or episode history
- With it off they still count in every number, but they're never named or pictured: not on the Dashboard, Rev, the Reports wall, CSV exports, or in a suggestion's reasons

Numbers that were wrong
- A rewatch years later no longer changes how many episodes count in the month I first finished the show
- Books vs Hardcover compares the same books on both sides
- Films and TV count by my local day, like everything else, so an evening rating on New Year's Eve lands in the right year
- A read-through deleted on Hardcover stops counting on the next sync, not a week later
- A MAL list longer than one page is read again before an entry is treated as removed, so an edit during a sync can't delete a show's history
- The Your taste headline names a genre only from its own medium

Crashes and quitting
- Closing the app during a sync, a Test connection, or a recap drawing waits for it in the background instead of crashing
- Ctrl+C in the terminal closes the app cleanly when it's run from source
- A locked or broken credential store says "Couldn't read the saved keys" instead of failing, and Sync everything goes on with the other sources
- Deleting a profile that's in use leaves its keys alone
- A sync running in another window is no longer marked interrupted

Fixes
- Not interested on a suggested book sticks
- Pictures that failed on a timeout or a server error get another try after ten minutes, and Clear in Settings gives every failed picture another try
- The artist picture is found whatever the capitals in the name
- A stray request to the sign-in page no longer ends a Hardcover sign-in, and saying no on Hardcover still ends it at once
- Titles in CSV exports that start like a spreadsheet formula are written as text
- Window preferences are saved so a crash mid-save can't wipe them
- The sidebar's arrow keys and the status dot's tooltip follow the app's own rules for safe widgets and plain text
- Music suggestions look up each artist's details once, a TMDB title that's gone isn't asked for on every sync, and a Hardcover book that's gone waits a month before it's asked for again

## [2.1.0]

Cleanup, consistency, and speed: one place for every source, steadier motion, fewer empty corners, and more settings.

Syncing and sources
- One Sync everything button on the Dashboard, with an arrow that lists each connected source to sync just one
- Syncing shows on the status line, with the progress bar beside the step, so the cards never move; Show log has the details, and a sync with problems turns the line red
- When every source synced together, the status line says so once instead of once per source
- Settings has one Sources card, a row per source in the same columns: whether it's connected, when it last synced and how much it holds, the username and key (or Connect), Test, Sync now, and Remove or Disconnect
- Hardcover gets Test connection, and asks only for the books that changed since the last sync, with a full read once a week
- Music hours keep improving: track lengths are looked up for the rest of the history too, a few hundred a sync, until most plays have one; until then a period shows the hours it knows of ("112+") instead of a dash

New settings
- Clock: 12-hour or 24-hour, everywhere in the app and on Rev's cards
- Weeks start on Sunday or Monday, for plays per week, plays by day, and Rev's calendar
- Sync when the app opens: off, every time, or once a day
- Advanced: which cards the Dashboard shows (the rest widen to fill the room), whether report pages open on all time or the latest year, how many votes a TMDB average needs before film picks trust it, and how big the picture cache can grow (100 MB, 300 MB, or 1 GB, with a Clear button)

Pages
- Your taste folds critic traits that agree into one, like "Tough critic" with a number for MAL, TMDB, and Hardcover
- You vs the crowd replaces How critical you are: my average against MAL's, TMDB's, and Hardcover's for the same titles, and how much of my listening goes beyond my ten most played artists
- This month hides zero counts
- Books: Your stars vs Hardcover replaces Length, and Kind, format, and stars adds how many books I gave stars
- Films and TV: Your rating vs TMDB replaces Rated each year, following the Films | TV switch
- Every Reports tile has a number and a second line, never a dash
- Studios, artists, authors, and directors cards grow their covers with the card
- Music suggestions show how closely they match as a number ("95%, Strong match"), and For You's notes all look the same
- Rev: thumbnails of every card beside the deck to jump to one, no zero tiles on the cover, and About these numbers folds away
- Settings balances its columns on wide windows, and Advanced never scrolls sideways

Film picks
- Films that aren't out yet are left out
- A TMDB average built on few votes counts for less, pulled toward TMDB's typical average, so a new film's early hype doesn't top the list; the card still shows TMDB's own average and the hover says how it was weighed

Motion and speed
- Page changes fade the new page in without grabbing the old one, so switching is quick and smooth; a second switch finishes the first
- For You fades in only the picks it hadn't shown, and Rev's deck continues a slide from where it is instead of jumping
- Pages off screen catch up when they're opened instead of all redrawing after every sync, and Reports redraws only the page in front
- Faster albums on repeat, and the Dashboard skips rebuilding shelves that didn't change

Fixes
- Settings' sources show the latest sync right after one ends
- "1 reread", not "1 rereads"
- An audiobook without a length on Hardcover no longer counts as 0 hours
- The "so far" label on a chart's last bar no longer covers a number
- The Dashboard no longer scrolls during a sync or when a recap is ready
- Hardcover's "not connected" message no longer says "Hardcover:" twice
- The picture cache holds 300 MB by default, up from 100 MB, so big walls don't download their covers again

Upkeep
- CI and releases run on Ubuntu 24.04 with the Node 24 versions of GitHub's actions
- README screenshots of every page, drawn from a made-up library

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

## [1.0.0]

First packaged release: a desktop app with profiles, recommendations, and artwork.

- A real Windows app with the After Hours look: dark, lilac and coral, Space Grotesk and Inter, and a two-disc logo
- A Windows installer: per user with no admin prompt, light or dark to match Windows with the app's logo, a Start menu shortcut and an optional desktop one; running it again offers Update, Repair, or Uninstall, and uninstalling keeps profiles and settings unless asked to delete them (saved API keys included, and nothing else in that folder)
- If Taste Engine is open, updating offers to close it and uninstalling asks you to close it first
- Profiles, each with its own API keys, settings, and data
- API keys entered in the app and kept in Windows Credential Manager
- Dashboard with recently finished anime, albums on repeat in the last 30 days, my score distribution, how often I score lower or higher than MAL (against its mean rounded to a whole score, since mine are whole numbers), and my most generous and harshest genres; the sync log opens only while a sync runs, and stays open if something went wrong
- For You: anime suggestions with a predicted score for me and the reasons behind each, new artists to try, and old favorites to rediscover, each with "Not interested" and a link to MyAnimeList or Last.fm
- Suggestions explained in plain words, with English titles when MAL has one ("Because you loved A and really liked B"); the numbers behind each reason are in the card's tooltip
- Each anime card shows the whole score I'd most likely give, with the exact prediction and the MAL mean beside it, and the chance I'd give the show an 8 or more (left out under 20 scored shows)
- Music suggestions labeled Strong match, Good match, or Worth a try, with similarity given as a percent
- With 30 or more scored shows, the anime model scales the MAL mean instead of only shifting it, which predicts harsh critics better
- An accuracy check on the anime predictions, compared with the MAL mean alone, both guessing whole scores like the ones I give
- Quick on a big library: recommendations recompute in a fraction of a second, and cleaning up old raw pages after a sync no longer scans every page
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
