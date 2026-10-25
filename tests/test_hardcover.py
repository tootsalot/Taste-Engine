"""Hardcover: signing in, syncing my library, and token refresh. Made-up books only."""

import json
import threading
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import pytest

from taste import activity, credentials, db, runner
from taste.secrets_store import SecretStore
from taste.sources import hardcover, hardcover_auth
from tests import fake_hardcover
from tests.conftest import FakeKeyring
from tests.fake_hardcover import FakeHardcover, book, entry

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)


def browser_returns(sign_in, **query):
    """Stand in for the browser coming back to the app's loopback address."""
    url = f"{sign_in.redirect_uri}?{urllib.parse.urlencode(query)}"

    def visit():
        with urllib.request.urlopen(url, timeout=5) as page:  # 127.0.0.1 only
            visit.page = page.read().decode()

    thread = threading.Thread(target=visit)
    thread.start()
    return thread, visit


def test_the_sign_in_url_asks_for_read_only_access_with_pkce():
    sign_in = hardcover_auth.SignIn()
    try:
        query = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(sign_in.url).query))
        assert sign_in.url.startswith(hardcover_auth.AUTHORIZE_URL + "?")
        assert query["client_id"] == hardcover_auth.CLIENT_ID
        assert query["code_challenge"] == hardcover_auth.challenge(sign_in.verifier)
        assert query["code_challenge_method"] == "S256"
        assert query["redirect_uri"].startswith("http://127.0.0.1:")
        assert query["redirect_uri"].endswith("/callback")
        assert all(scope.startswith("read:") for scope in query["scope"].split())
        assert 43 <= len(sign_in.verifier) <= 128
    finally:
        sign_in.server.server_close()


def test_the_browser_coming_back_gives_the_code(make_client):
    sign_in = hardcover_auth.SignIn()
    fake = FakeHardcover()
    code = fake.issue(hardcover_auth.challenge(sign_in.verifier))
    thread, visit = browser_returns(
        sign_in, code=code, state=sign_in.state, iss=hardcover_auth.ISSUER
    )
    assert sign_in.wait(timeout=10) == code
    thread.join()
    assert "close this tab" in visit.page
    client, transport = make_client(fake)
    tokens = hardcover_auth.exchange(client, sign_in, code, NOW)
    assert (tokens.access, tokens.refresh) == (fake_hardcover.ACCESS, fake_hardcover.REFRESH)
    assert tokens.expires_at == "2026-10-21 12:00:00"
    sent = transport.calls[0]["params"]
    assert (
        sent["code_verifier"] == sign_in.verifier and sent["redirect_uri"] == sign_in.redirect_uri
    )


@pytest.mark.parametrize(
    "change, message",
    [
        ({"iss": "https://evil.example"}, "didn't come from Hardcover"),
        ({"error": "access_denied", "code": ""}, "didn't approve"),
    ],
)
def test_a_wrong_or_refused_callback_is_rejected(change, message):
    sign_in = hardcover_auth.SignIn()
    query = {"code": "code-1", "state": sign_in.state, "iss": hardcover_auth.ISSUER, **change}
    thread, visit = browser_returns(sign_in, **query)
    with pytest.raises(hardcover_auth.SignInError, match=message):
        sign_in.wait(timeout=10)
    thread.join()
    assert "didn't get access" in visit.page  # the browser never says "Signed in"


def test_a_stray_callback_is_turned_away_and_the_real_one_still_works():
    """A request without this sign-in's state (an old tab, a prefetch) used to end the
    sign-in, so the real redirect a moment later found the listener closed."""
    import time

    sign_in = hardcover_auth.SignIn()
    stray, stray_visit = browser_returns(sign_in, code="old", state="someone-else")
    time.sleep(0.3)  # queued first: the listener answers in order once wait() runs
    thread, _ = browser_returns(
        sign_in, code="code-1", state=sign_in.state, iss=hardcover_auth.ISSUER
    )
    assert sign_in.wait(timeout=10) == "code-1"
    stray.join(timeout=5)
    thread.join()
    assert "didn't get access" in stray_visit.page


def test_an_idle_connection_never_holds_up_the_real_callback():
    """2.5: the listener answers one connection at a time, and a connection that opened and
    sent nothing (a browser's spare preconnect socket, or any local program) blocked it
    for good, so the real redirect was never answered and Stop couldn't end the wait."""
    import socket

    sign_in = hardcover_auth.SignIn()
    idle = socket.create_connection(sign_in.server.server_address)  # opens, says nothing
    found = []
    try:
        waiter = threading.Thread(target=lambda: found.append(sign_in.wait(timeout=20)))
        waiter.start()
        thread, _ = browser_returns(
            sign_in, code="code-1", state=sign_in.state, iss=hardcover_auth.ISSUER
        )
        waiter.join(timeout=10)
        assert not waiter.is_alive() and found == ["code-1"]
        thread.join(timeout=5)
    finally:
        idle.close()
        sign_in.cancel()


def test_a_refusal_without_state_still_ends_the_sign_in():
    """Some OAuth servers send a refusal without state; it carries no code, so it can end
    the wait (it used to be ignored after the stray-callback fix, hanging 5 minutes)."""
    sign_in = hardcover_auth.SignIn()
    thread, _ = browser_returns(sign_in, error="access_denied")
    with pytest.raises(hardcover_auth.SignInError, match="didn't approve"):
        sign_in.wait(timeout=10)
    thread.join()


def test_a_sign_in_can_be_stopped():
    sign_in = hardcover_auth.SignIn()
    sign_in.cancel()
    with pytest.raises(hardcover_auth.SignInError, match="Stopped"):
        sign_in.wait(timeout=10)


def synced(conn, make_client, fake=None, **kwargs):
    fake = fake or FakeHardcover()
    client, transport = make_client(fake)
    result = hardcover.sync(conn, client, fake.access, lambda m: None, **kwargs)
    return result, fake, transport


def test_a_sync_fills_staging_and_core(conn, make_client):
    result, fake, _ = synced(conn, make_client)
    assert (result.status, result.books, result.new) == ("success", 8, 8)
    rows = {r["user_book_id"]: r for r in conn.execute("SELECT * FROM stg_hardcover_user_books")}
    assert rows[101]["rating"] == 4.5 and rows[101]["owned"] == 1
    assert rows[106]["rating"] is None  # want to read, not rated
    assert rows[103]["reading_format_id"] == 2 and rows[103]["edition_seconds"] == 36000
    first = conn.execute("SELECT * FROM stg_hardcover_books WHERE book_id = 1").fetchone()
    assert json.loads(first["genres_json"]) == ["Fantasy", "Young Adult"]
    assert json.loads(first["moods_json"]) == ["adventurous"]
    assert [a["name"] for a in json.loads(first["authors_json"])] == ["Ana Example"]

    view = {r["title"]: r for r in conn.execute("SELECT * FROM rpt_hardcover_books")}
    lantern = view["Example Lantern Road"]
    assert (lantern["my_stars"], lantern["hardcover_average"]) == (4.5, 4.1)
    assert lantern["times_read"] == 2 and lantern["status"] == "read"
    assert lantern["vs_average"] == "higher"  # 4.1 rounds to 4.0 in halves
    assert view["Example Quiet Harbor"]["vs_average"] == "lower"
    assert view["Example Old Favorite"]["times_read"] == 1  # from the last read date
    assert view["Example Undated Read"]["times_read"] == 0
    assert view["Example Gave Up"]["status"] == "did_not_finish"
    keys = {r[0] for r in conn.execute("SELECT source_event_key FROM core_behavior_events")}
    assert keys == {"101:read:1001", "101:read:1002", "102:read:1003", "103:read:1004", "104:read"}
    tags = conn.execute(
        "SELECT t.tag_kind, t.name FROM core_item_tags it JOIN core_tags t ON t.tag_id = it.tag_id "
        "JOIN core_items i ON i.item_id = it.item_id WHERE i.title = 'Example Lantern Road' "
        "ORDER BY t.tag_kind, t.name"
    ).fetchall()
    assert [tuple(t) for t in tags] == [
        ("genre", "Fantasy"), ("genre", "Young Adult"), ("mood", "adventurous"),
    ]  # fmt: skip
    authors = conn.execute(
        "SELECT DISTINCT c.name FROM core_item_creators ic JOIN core_creators c "
        "ON c.creator_id = ic.creator_id WHERE ic.source = 'hardcover'"
    ).fetchall()
    assert "Example Illustrator" not in {a[0] for a in authors}


def test_raw_pages_never_hold_a_token(conn, make_client):
    synced(conn, make_client)
    for params, payload in conn.execute("SELECT request_params, payload FROM raw_api_pages"):
        assert "hc_at_" not in params + payload and "hc_rt_" not in params + payload


def test_syncing_twice_changes_nothing_and_removals_are_kept(conn, make_client):
    synced(conn, make_client)
    result, _, _ = synced(conn, make_client)
    assert (result.new, result.removed) == (0, 0)
    assert conn.execute("SELECT COUNT(*) FROM core_items").fetchone()[0] == 8

    smaller = [e for e in fake_hardcover.LIBRARY if e["id"] != 102]
    # A read-through deleted on Hardcover goes too.
    smaller[0] = entry(101, smaller[0]["book"], rating=4.5, reads=[(1001, None, "2026-09-04")])
    result, _, _ = synced(conn, make_client, FakeHardcover(smaller), full=True)
    assert result.removed == 1  # a full read sees what's gone
    gone = conn.execute("SELECT removed_at FROM stg_hardcover_user_books WHERE user_book_id = 102")
    assert gone.fetchone()[0] is not None
    keys = {r[0] for r in conn.execute("SELECT source_event_key FROM core_behavior_events")}
    assert "102:read:1003" not in keys and "101:read:1002" not in keys
    assert "101:read:1001" in keys


def test_a_sync_reads_only_what_changed_and_everything_weekly(conn, make_client):
    synced(conn, make_client)  # the first one reads everything
    library = [dict(e) for e in fake_hardcover.LIBRARY]
    library[1] = dict(library[1], rating=5.0, updated_at="2099-01-01T00:00:00+00:00")
    del library[0]  # gone, but a changes-only read can't tell
    result, fake, _ = synced(conn, make_client, FakeHardcover(library))
    assert fake.calls == ["me", "library-since:0"]
    assert result.books == 1 and result.removed == 0
    rating = conn.execute("SELECT rating FROM stg_hardcover_user_books WHERE user_book_id = 102")
    assert rating.fetchone()[0] == 5.0
    mode = conn.execute("SELECT mode FROM sync_runs ORDER BY sync_run_id DESC LIMIT 1")
    assert mode.fetchone()[0] == "incremental"
    # A week after the last full read, it reads everything again and sees the removal.
    later = datetime.now(timezone.utc) + hardcover.FULL_EVERY
    result, fake, _ = synced(conn, make_client, FakeHardcover(library), now=later)
    assert fake.calls[1] == "library:0" and result.removed == 1


def test_a_read_through_deleted_on_hardcover_goes_on_a_changes_only_read(conn, make_client):
    synced(conn, make_client)
    library = [dict(e) for e in fake_hardcover.LIBRARY]
    lantern = library[0]  # read twice: read-throughs 1001 and 1002
    library[0] = dict(
        lantern,
        user_book_reads=[r for r in lantern["user_book_reads"] if r["id"] != 1002],
        read_count=1,
        updated_at="2099-01-01T00:00:00+00:00",
    )
    result, fake, _ = synced(conn, make_client, FakeHardcover(library))
    assert fake.calls == ["me", "library-since:0"]  # a changes-only read
    keys = [
        r[0]
        for r in conn.execute(
            "SELECT source_event_key FROM core_behavior_events WHERE source = 'hardcover' "
            "AND source_event_key LIKE '101:%'"
        )
    ]
    assert keys == ["101:read:1001"]  # the deleted second read doesn't count for a week
    gone = conn.execute("SELECT removed_at FROM stg_hardcover_reads WHERE read_id = 1002")
    assert gone.fetchone()[0] is not None


def test_a_book_sent_without_its_reads_list_keeps_its_reads(conn, make_client):
    """Reads are settled only against a reads list that came back; a row without one
    (a field error, an API change) used to mark every read of the book removed."""
    synced(conn, make_client)
    library = [dict(e) for e in fake_hardcover.LIBRARY]
    library[0] = dict(library[0], updated_at="2099-01-01T00:00:00+00:00")
    del library[0]["user_book_reads"]
    synced(conn, make_client, FakeHardcover(library))
    keys = conn.execute(
        "SELECT COUNT(*) FROM core_behavior_events WHERE source = 'hardcover' "
        "AND source_event_key LIKE '101:read:%'"
    ).fetchone()[0]
    assert keys == 2


def test_without_a_changes_filter_it_reads_everything(conn, make_client):
    synced(conn, make_client)
    fake = FakeHardcover()
    fake.knows_updated_at = False
    lines = []
    client, _ = make_client(fake)
    result = hardcover.sync(conn, client, fake.access, lines.append)
    assert result.status == "success" and result.books == len(fake_hardcover.LIBRARY)
    assert fake.calls == ["me", "library:0"]
    assert any("reading the whole library" in line for line in lines)


def test_a_big_library_comes_in_pages(conn, make_client):
    shelf = [
        entry(1000 + i, book(500 + i, f"Example Book {i}", fake_hardcover.BEN), status=1)
        for i in range(hardcover.PAGE_SIZE + 5)
    ]
    result, fake, _ = synced(conn, make_client, FakeHardcover(shelf))
    assert result.books == hardcover.PAGE_SIZE + 5
    assert fake.calls == ["me", "library:0", f"library:{hardcover.PAGE_SIZE}"]


def test_an_expired_sign_in_fails_with_a_plain_message(conn, make_client):
    def expired(url, params):
        from tests.conftest import FakeResponse

        return FakeResponse(401, {"error": "invalid_token"})

    client, _ = make_client(expired)
    result = hardcover.sync(conn, client, "hc_at_old", lambda m: None)
    assert result.status == "failed"
    assert "Connect your account again" in result.error


def test_cached_tags_in_other_shapes_read_as_no_tags():
    assert hardcover.tag_names(None, "Genre") == []
    assert hardcover.tag_names("not json", "Genre") == []
    assert hardcover.tag_names({"genre": ["Mystery", "Mystery", ""]}, "Genre") == ["Mystery"]
    assert hardcover.tag_names('{"Mood": [{"tag": "dark"}]}', "Mood") == ["dark"]


def test_tokens_refresh_before_they_run_out_and_the_new_pair_is_kept(make_client, tmp_path):
    store = SecretStore(tmp_path, backend=FakeKeyring())
    fake = FakeHardcover()
    credentials.save_hardcover(
        "me", store, hardcover_auth.Tokens(fake.access, fake.refresh, "2026-10-14 18:00:00")
    )
    factory = lambda secrets: make_client(fake)[0]  # noqa: E731
    clock = NOW.timestamp
    tokens = runner.hardcover_tokens("me", store, factory, clock)
    assert fake.refreshes == 1 and tokens.refresh.endswith("-1")
    assert credentials.hardcover("me", store) == tokens  # saved before use
    # A week away from running out: used as it is.
    assert runner.hardcover_tokens("me", store, factory, clock) == tokens
    assert fake.refreshes == 1


def test_sync_all_includes_hardcover_once_connected(conn, make_client, tmp_path):
    store = SecretStore(tmp_path, backend=FakeKeyring())
    fake = FakeHardcover()
    factory = lambda secrets: make_client(fake)[0]  # noqa: E731
    results = runner.sync_sources(conn, "me", "hardcover", store=store, out=lambda m: None)
    assert results == {"hardcover": False}  # not connected yet
    credentials.save_hardcover(
        "me", store, hardcover_auth.Tokens(fake.access, fake.refresh, "2026-12-01 00:00:00")
    )
    results = runner.sync_sources(
        conn, "me", "hardcover", store=store, out=lambda m: None, client_factory=factory,
        now=NOW.timestamp,
    )  # fmt: skip
    assert results == {"hardcover": True}


def test_test_connection_asks_hardcover_whose_account_it_is(conn, make_client, tmp_path):
    store = SecretStore(tmp_path, backend=FakeKeyring())
    fake = FakeHardcover()
    factory = lambda secrets: make_client(fake)[0]  # noqa: E731
    ok, message = runner.check_connection(conn, "me", "hardcover", store=store)
    assert not ok
    assert message == "Hardcover: connect your Hardcover account on the Settings page first."
    credentials.save_hardcover(
        "me", store, hardcover_auth.Tokens(fake.access, fake.refresh, "2026-12-01 00:00:00")
    )
    ok, message = runner.check_connection(
        conn, "me", "hardcover", store=store, client_factory=factory
    )
    assert ok and message == "Hardcover: connected as example_user."
    assert fake.access not in message


def test_books_read_in_a_month(conn, make_client):
    synced(conn, make_client)
    september = activity.books_read(conn, "2026-09-01", "2026-10-01")
    assert [b.title for b in september] == [
        "Example Lantern Road", "Example Quiet Harbor", "Example Lantern Road",
    ]  # fmt: skip
    first = september[0]
    assert (first.author, first.my_rating, first.average, first.pages) == (
        "Ana Example", 4.5, 4.1, 320,
    )  # fmt: skip
    assert first.cover_url.endswith("/1/cover.jpg")


def test_old_goodreads_books_are_removed_once(tmp_path):
    path = tmp_path / "old.db"
    conn = db.connect(path)
    # What a profile from before Hardcover held: the old sources, a book, and its rows.
    conn.executescript(
        """
        INSERT INTO core_sources (source, display_name, default_capture_scope)
            VALUES ('goodreads', 'Goodreads', 'self_reported');
        INSERT INTO core_sources (source, display_name, default_capture_scope)
            VALUES ('openlibrary', 'Open Library', 'not_applicable');
        CREATE TABLE stg_goodreads_books (goodreads_book_id INTEGER PRIMARY KEY);
        CREATE TABLE stg_openlibrary_books (isbn13 TEXT PRIMARY KEY);
        INSERT INTO core_items (item_id, media_type, title, source, created_at, updated_at)
            VALUES (900, 'book', 'Example Old Book', 'goodreads', '2026-01-01', '2026-01-01');
        INSERT INTO core_item_external_ids (source, id_type, external_id, item_id, match_method)
            VALUES ('goodreads', 'goodreads_book_id', '1', 900,
            'source_native');
        INSERT INTO core_item_images (item_id, source, kind, url)
            VALUES (900, 'openlibrary', 'cover', 'https://example.org/c');
        INSERT INTO sync_runs (source, mode, started_at, status)
            VALUES ('goodreads', 'import', '2026-01-01 00:00:00', 'success');
        """
    )
    conn.close()
    conn = db.connect(path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM core_items").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM sync_runs").fetchone()[0] == 0
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "stg_goodreads_books" not in tables and "stg_openlibrary_books" not in tables
        sources = {r[0] for r in conn.execute("SELECT source FROM core_sources")}
        assert "goodreads" not in sources and "hardcover" in sources
    finally:
        conn.close()


def test_a_refresh_token_that_ran_out_asks_to_connect_again(conn, make_client, tmp_path):
    store = SecretStore(tmp_path, backend=FakeKeyring())
    fake = FakeHardcover()
    credentials.save_hardcover(
        "me", store, hardcover_auth.Tokens(fake.access, "hc_rt_long-gone", "2026-10-01 00:00:00")
    )
    lines = []
    results = runner.sync_sources(
        conn, "me", "hardcover", store=store, out=lines.append,
        client_factory=lambda secrets: make_client(fake)[0], now=NOW.timestamp,
    )  # fmt: skip
    assert results == {"hardcover": False}
    assert any("Connect your account again" in line for line in lines)


def test_a_suggested_book_hardcover_doesnt_send_back_waits_a_month(conn, make_client):
    """A similar book that's gone (deleted or merged) was asked for on every refresh,
    since the note that it was asked for went to a row that didn't exist."""
    library = [dict(e) for e in fake_hardcover.LIBRARY]
    old = library[3]  # Example Old Favorite, 5 stars: its similar books are candidates
    library[3] = dict(old, book=dict(old["book"], cached_similar_book_ids=[202, 203, 999]))
    fake = FakeHardcover(library)
    client, _ = make_client(fake)
    hardcover.sync(conn, client, fake.access, lambda m: None)
    hardcover.enrich_recommendations(conn, client, fake.access, lambda m: None, now=NOW)
    assert hardcover.enrich_recommendations(conn, client, fake.access, lambda m: None, now=NOW) == 0
    from taste import recommend

    assert "999" not in [r.item_key for r in recommend.book_recommendations(conn)[0]]


def test_book_suggestions_from_similar_books(conn, make_client):
    from taste import recommend

    fake = FakeHardcover()
    client, _ = make_client(fake)
    hardcover.sync(conn, client, fake.access, lambda m: None)
    looked_up = hardcover.enrich_recommendations(conn, client, fake.access, lambda m: None, now=NOW)
    assert looked_up == 5  # 4 candidates and the Want to Read book
    assert hardcover.enrich_recommendations(conn, client, fake.access, lambda m: None, now=NOW) == 0

    recs, metrics = recommend.book_recommendations(conn)
    titles = [r.title for r in recs]
    assert "Example Few Ratings" not in titles  # 3 ratings: too unsettled
    assert "Example Lantern Road" not in titles  # already read
    assert {"Example Tide Keeper", "Example Salt Atlas", "Example Paper Fox"} <= set(titles)
    want = next(r for r in recs if r.title == "Example Want List")
    assert want.badge == "On your Want to Read"
    salt = next(r for r in recs if r.title == "Example Salt Atlas")
    assert 0.5 <= salt.score <= 5 and salt.facts["hardcover_mean"] == 3.9
    assert salt.facts["about"] == "A made-up book called Example Salt Atlas."
    assert salt.facts["popularity"][0] == "1.1k readers on Hardcover"
    assert salt.url == "https://hardcover.app/books/example-salt-atlas"
    assert any("Example Old Favorite" in reason for reason in salt.reasons)
    assert metrics["rated"] == 6

    recommend.save_run(conn, "book", recs, metrics)
    recommend.dismiss(conn, "book", salt.item_key)
    assert "Example Salt Atlas" not in [r.title for r in recommend.book_recommendations(conn)[0]]
    # The saved list For You reloads after Not interested leaves it out too (it used to
    # read the hidden artists instead, so the book came straight back).
    shown, _, _ = recommend.latest(conn, "book")
    assert shown and {r.kind for r in shown} == {"book"}
    assert "Example Salt Atlas" not in [r.title for r in shown]
