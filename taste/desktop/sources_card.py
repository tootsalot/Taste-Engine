"""Settings' Sources card: every source in one card, one row each.

Every row has the same columns, so the fields and buttons line up down the card
(approved from a mockup, 2026-10-16): the source and its state, the username or
account, the key, Save, Sync now, and a menu. TMDB and Hardcover have no username;
their Connect button sits in that column. Since 2.4 (approved from a mockup,
2026-10-24) Sync now is the one button a connected row shows: Test connection and
Remove or Disconnect are in the row's menu, and Save shows only while a field holds
something not saved yet. A connected source never shows a Connect button.
The state lines come from taste.source_status, shared with the Dashboard.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QSize, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from taste import credentials, settings, source_status
from taste.desktop import motion, theme
from taste.desktop.context import AppContext
from taste.desktop.widgets import Card, heading, label, plain_tip, set_role
from taste.desktop.workers import CallWorker, ConnectionTestWorker
from taste.http_client import HttpClient
from taste.runner import SOURCE_LABELS, SOURCES
from taste.secrets_store import SecretStoreError
from taste.settings import SettingError
from taste.sources import hardcover, tmdb

KEYS = source_status.KEYS  # source -> the key it's typed into
USERNAMES = {source: names[0] for source, names in source_status.USERNAMES.items()}
KEY_WORDS = {"mal": "client ID", "lastfm": "API key", "tmdb": "read access token"}
KEY_STATES = {
    "keyring": ("Saved in Credential Manager", "Saved in Windows Credential Manager."),
    "file": ("Saved in an unencrypted file", "Saved in an unencrypted file in the data folder."),
    "env": ("Using an environment variable", "Read from an environment variable."),
}
HELP = {
    "mal": "Get a client ID at myanimelist.net/apiconfig. Your anime list has to be public.",
    "lastfm": "Get an API key at last.fm/api/account/create.",
    "tmdb": "Films and TV from the ratings, watchlist, and favorites on your TMDB account. "
    "Get a read access token at themoviedb.org/settings/api and save it, then connect your "
    "account. TMDB keeps when you rated something, not when you watched it.",
    "hardcover": "Books from your Hardcover library: your stars, what you've read and when, "
    "and the genres and moods readers gave each book. Connecting opens Hardcover in your "
    "browser to approve Taste Engine, which can only read. Books still on Goodreads? "
    "Hardcover can import a Goodreads export for you.",
}
COLUMNS = ("source", "account", "key", "save", "sync", "more")
DOT = 10
USERNAME_WIDTH = 150
KEY_WIDTH = 220


@dataclass
class SourceRow:
    """One source's widgets. Columns that don't apply to it hold None."""

    source: str
    state: QLabel
    sync: QPushButton
    more: QToolButton  # the row's menu: Test connection, Remove or Disconnect
    test: QAction
    remove: QAction
    username: QLineEdit | None = None
    key: QLineEdit | None = None
    save: QPushButton | None = None
    connect: QPushButton | None = None  # TMDB and Hardcover
    account: QLabel | None = None  # "Account connected"
    stop: QPushButton | None = None  # Hardcover, waiting for the browser
    copy: QPushButton | None = None
    no_key: QLabel | None = None


class SourcesCard(Card):
    changed = Signal()  # a key, username, or account changed: the Dashboard follows
    sync_requested = Signal(str)
    hardcover_connected = Signal()  # signed in: sync the books now

    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        self.profile_id: str | None = None
        self.busy = False  # a sync or a recommendations refresh is running
        self._appeared: list[QWidget] = []
        self.testers: list[ConnectionTestWorker] = []
        self.tmdb_worker: CallWorker | None = None
        self.hardcover_worker: CallWorker | None = None
        self.sign_in: hardcover.SignIn | None = None
        self.usernames: dict[str, QLineEdit] = {}
        self.saved_usernames: dict[str, str] = {}  # to tell when Save is needed
        self.key_inputs: dict[str, QLineEdit] = {}  # by key name, like MAL_CLIENT_ID

        self.body.addWidget(heading("Sources", 18))
        self.insecure_note = label(
            "This computer has no system credential store, so saved keys go in an "
            "unencrypted file in the data folder. On Windows this never happens.",
            role="error",
            wrap=True,
        )
        self.body.addWidget(self.insecure_note)
        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(8)
        self.grid.setVerticalSpacing(6)
        for column, stretch in enumerate((3, 2, 3, 0, 0, 0)):
            self.grid.setColumnStretch(column, stretch)
        for column, text in ((1, "Username or account"), (2, "Key")):
            self.grid.addWidget(label(text, role="caption"), 0, column)
        self.rows = {
            source: self._add_row(source, 1 + i * 3, last=i == len(SOURCES) - 1)
            for i, source in enumerate(SOURCES)
        }
        self.body.addLayout(self.grid)
        self.message = label("", wrap=True)
        self.body.addWidget(self.message)
        self.body.addWidget(label("Saved keys are never shown again.", role="caption"))

    # -- building -------------------------------------------------------------

    def _button(self, text: str, then, role: str | None = None) -> QPushButton:
        button = QPushButton(text)
        if role:
            button.setProperty("role", role)
        button.clicked.connect(lambda _=False: then())
        return button

    def _add_row(self, source: str, at: int, last: bool) -> SourceRow:
        name = QWidget()
        stack = QVBoxLayout(name)
        stack.setContentsMargins(0, 0, 0, 0)
        stack.setSpacing(2)
        title = QHBoxLayout()
        title.setSpacing(8)
        dot = QLabel()
        dot.setPixmap(theme.source_dot(source, DOT).pixmap(QSize(DOT * 2, DOT * 2)))
        dot.setFixedSize(DOT * 2, DOT * 2)
        title.addWidget(dot)
        title.addWidget(label(SOURCE_LABELS[source], role="strong"))
        title.addStretch()
        stack.addLayout(title)
        # The state goes on the full-width line under the row, beside nothing: in the
        # name column it wrapped onto four or five lines at 1920 px.
        state = label("", role="muted", wrap=True)
        more = QToolButton()
        more.setObjectName("RowMenu")
        more.setIcon(QIcon(str(theme.ASSETS / "more.svg")))  # three dots, drawn the same
        more.setIconSize(QSize(18, 18))  # everywhere (the font's "⋯" wasn't)
        more.setFixedSize(34, 34)
        more.setToolTip(f"More for {SOURCE_LABELS[source]}")
        more.setAccessibleName(f"More for {SOURCE_LABELS[source]}")
        more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(more)
        more.setMenu(menu)
        test = QAction("Test connection", menu)
        test.triggered.connect(lambda _=False: self.test_connection(source))
        remove = QAction("Remove", menu)
        remove.triggered.connect(lambda _=False: self.remove_source(source))
        menu.addAction(test)
        menu.addSeparator()
        menu.addAction(remove)
        row = SourceRow(
            source=source,
            state=state,
            sync=self._button("Sync now", lambda: self.sync_now(source)),
            more=more,
            test=test,
            remove=remove,
        )
        self.grid.addWidget(name, at, 0)

        account = QHBoxLayout()
        account.setContentsMargins(0, 0, 0, 0)
        if source in USERNAMES:
            row.username = QLineEdit()
            row.username.setPlaceholderText("your username")
            row.username.setMaxLength(200)
            row.username.setMinimumWidth(USERNAME_WIDTH)
            row.username.returnPressed.connect(lambda: self.save_source(source))
            row.username.textChanged.connect(lambda _text: self._update_save(source))
            self.usernames[source] = row.username
            account.addWidget(row.username)
        else:
            connect = self.connect_tmdb if source == "tmdb" else self.connect_hardcover
            row.connect = self._button("Connect…", lambda: connect(), "primary")
            row.account = label("Account connected", role="ok")
            account.addWidget(row.connect)
            account.addWidget(row.account)
            if source == "hardcover":
                row.stop = self._button("Stop", self.stop_hardcover)
                account.addWidget(row.stop)
        self.grid.addLayout(account, at, 1)

        if source in KEYS:
            row.key = QLineEdit()
            row.key.setEchoMode(QLineEdit.EchoMode.Password)
            row.key.setMinimumWidth(KEY_WIDTH)  # room for "Saved in Credential Manager"
            row.key.returnPressed.connect(lambda: self.save_source(source))
            row.key.textChanged.connect(lambda _text: self._update_save(source))
            self.key_inputs[KEYS[source]] = row.key
            self.grid.addWidget(row.key, at, 2)
            row.save = self._button("Save", lambda: self.save_source(source), "primary")
            self.grid.addWidget(row.save, at, 3)
            row.save.setVisible(False)  # until something is typed (in its layout first)
            # The column keeps the button's width, so Sync now and the menu never shift.
            self.grid.setColumnMinimumWidth(3, row.save.sizeHint().width())
        else:
            box = QHBoxLayout()
            box.setContentsMargins(0, 0, 0, 0)
            row.no_key = label("No key needed", role="muted")
            row.copy = self._button("Copy the link", self.copy_hardcover_link, "link")
            row.copy.setToolTip("For when your browser didn't open by itself.")
            box.addWidget(row.no_key)
            box.addWidget(row.copy)
            box.addStretch()
            self.grid.addLayout(box, at, 2)
        self.grid.addWidget(row.sync, at, 4)
        self.grid.addWidget(row.more, at, 5)
        under = QVBoxLayout()
        under.setContentsMargins(DOT * 2 + 8, 0, 0, 0)  # lined up with the source's name
        under.setSpacing(2)
        under.addWidget(state)
        under.addWidget(label(HELP[source], role="caption", wrap=True))
        self.grid.addLayout(under, at + 1, 0, 1, len(COLUMNS))
        if not last:
            line = QFrame()
            line.setObjectName("SourceLine")
            line.setFixedHeight(1)
            self.grid.addWidget(line, at + 2, 0, 1, len(COLUMNS))
        return row

    # -- state ----------------------------------------------------------------

    def set_profile(self, profile_id: str | None) -> None:
        self.profile_id = profile_id
        self.message.setText("")
        for field in self.key_inputs.values():
            field.clear()
        if not profile_id:
            return
        conn = self.ctx.connect(profile_id)
        try:
            for source, field in self.usernames.items():
                self.saved_usernames[source] = settings.get(conn, USERNAMES[source])
                field.setText(self.saved_usernames[source])
        finally:
            conn.close()
        self.refresh_status()

    def set_busy(self, busy: bool) -> None:
        """While a sync or a recommendations refresh runs, Sync now waits."""
        self.busy = busy
        for row in self.rows.values():
            row.sync.setEnabled(not busy)
            row.sync.setToolTip("A sync or a suggestions refresh is running." if busy else "")

    def refresh_status(self) -> None:
        """Each row's state, and which of its buttons show. Typed text stays."""
        if not self.profile_id:
            return
        conn = self.ctx.connect(self.profile_id)
        try:
            found = {s.source: s for s in source_status.load(conn, self.profile_id, self.ctx.store)}
            tz_name = settings.get(conn, "timezone")
        finally:
            conn.close()
        self.insecure_note.setVisible(not self.ctx.store.secure)
        now = self.ctx.now()
        self._appeared: list[QWidget] = []
        for source, row in self.rows.items():
            status = found[source]
            row.state.setText(source_status.state_line(status, tz_name, now))
            set_role(row.state, "ok" if status.connected else "muted")
            stored = self._key_state(row) if row.key is not None else None
            row.test.setVisible(status.connected)
            self._show(row.sync, status.connected)
            row.sync.setEnabled(not self.busy)
            if source in USERNAMES:
                row.remove.setText("Remove")
                row.remove.setToolTip(f"Forget the {KEY_WORDS[source]} saved here.")
                self._show(row.remove, stored in ("keyring", "file"))
            elif source == "tmdb":
                self._tmdb_buttons(row, status.connected, stored)
            else:
                self._hardcover_buttons(row, status.connected)
            self._show(row.more, any(a.isVisible() for a in (row.test, row.remove)))
            self._update_save(source)
        if self.isVisible():
            motion.fade_in(self._appeared, stagger=False)  # Test and Sync now, once connected

    def _update_save(self, source: str) -> None:
        """Save shows only while the row holds something not saved yet (2.4)."""
        row = getattr(self, "rows", {}).get(source)  # (fields fill in while rows are built)
        if row is None or row.save is None:
            return
        typed = row.key is not None and bool(row.key.text().strip())
        if row.username is not None:
            saved = self.saved_usernames.get(source, "")
            typed = typed or row.username.text().strip() != saved
        row.save.setVisible(typed)

    def _show(self, widget: QWidget | QAction, shown: bool) -> None:
        """Show or hide a row's button; one that appears fades in (see refresh_status).
        Menu actions just show or hide."""
        if isinstance(widget, QAction):
            widget.setVisible(shown)
            return
        if shown and widget.isHidden():
            self._appeared.append(widget)
        widget.setVisible(shown)

    def _key_state(self, row: SourceRow) -> str | None:
        """Show where the key is kept (never the key). Returns 'keyring', 'file', 'env', None."""
        try:
            _, where = self.ctx.store.get_with_source(self.profile_id, KEYS[row.source])
        except SecretStoreError as exc:
            row.key.setPlaceholderText("Can't read the saved key")
            row.key.setToolTip(plain_tip(str(exc)))
            return None
        short, full = KEY_STATES.get(where, (f"Paste your {KEY_WORDS[row.source]}", ""))
        row.key.setPlaceholderText(short)
        row.key.setToolTip(plain_tip(full))
        return where

    def _tmdb_buttons(self, row: SourceRow, connected: bool, stored: str | None) -> None:
        busy = self.tmdb_worker is not None and self.tmdb_worker.isRunning()
        self._show(row.connect, not connected and stored is not None)
        row.connect.setEnabled(not busy)
        self._show(row.account, connected)
        row.remove.setText("Disconnect" if connected else "Remove")
        row.remove.setToolTip(
            "Forget your TMDB account here, and ask TMDB to forget it too."
            if connected
            else "Forget the read access token saved here."
        )
        self._show(row.remove, connected or stored in ("keyring", "file"))
        row.remove.setEnabled(not busy)

    def _hardcover_buttons(self, row: SourceRow, connected: bool) -> None:
        waiting = self.sign_in is not None
        busy = self.hardcover_worker is not None and self.hardcover_worker.isRunning()
        self._show(row.connect, not connected and not waiting)
        row.connect.setEnabled(not busy)
        self._show(row.stop, waiting)
        self._show(row.copy, waiting)
        self._show(row.no_key, not waiting)
        self._show(row.account, connected)
        row.remove.setText("Disconnect")
        row.remove.setToolTip("Forget your Hardcover sign-in here, and sign out on Hardcover too.")
        self._show(row.remove, connected)
        row.remove.setEnabled(not busy)

    def say(self, text: str, ok: bool = True) -> None:
        self.message.setText(text)
        set_role(self.message, "ok" if ok else "error")

    # -- keys and usernames -----------------------------------------------------

    def save_source(self, source: str) -> None:
        """Save what's typed in the row: the username, the key, or both."""
        saved, problems = [], []
        if source in USERNAMES:
            key = USERNAMES[source]
            value = self.usernames[source].text()
            conn = self.ctx.connect(self.profile_id)
            try:
                if settings.validate(key, value) != settings.get(conn, key):
                    settings.set_value(conn, key, value)
                    saved.append("username")
                self.saved_usernames[source] = settings.get(conn, key)
            except SettingError as exc:
                problems.append(str(exc))
            finally:
                conn.close()
        field = self.key_inputs.get(KEYS.get(source, ""))
        if field is not None and field.text().strip():
            try:
                self.ctx.store.set(self.profile_id, KEYS[source], field.text())
                saved.append(KEY_WORDS[source])
            except SecretStoreError as exc:
                problems.append(str(exc))
            field.clear()  # never leave a key sitting in the window
        if problems:
            self.say(" ".join(problems), ok=False)
        elif saved:
            self.say(f"Saved your {SOURCE_LABELS[source]} {' and '.join(saved)}.")
        else:
            self.say("Nothing new to save.")
        self.refresh_status()
        self.changed.emit()

    def remove_source(self, source: str) -> None:
        """Remove the saved key, or disconnect the account."""
        if source == "hardcover":
            self.disconnect_hardcover()
            return
        if source == "tmdb" and credentials.tmdb_connected(self.profile_id, self.ctx.store):
            self.disconnect_tmdb()
            return
        try:
            self.ctx.store.delete(self.profile_id, KEYS[source])
            self.say(f"Removed the {SOURCE_LABELS[source]} {KEY_WORDS[source]}.")
        except SecretStoreError as exc:
            self.say(str(exc), ok=False)
        self.refresh_status()
        self.changed.emit()

    def test_connection(self, source: str) -> ConnectionTestWorker:
        self.say(f"Testing {SOURCE_LABELS[source]}…")
        worker = ConnectionTestWorker(
            self.profile_id, source, self.ctx.store, self.ctx.client_factory
        )
        worker.done.connect(self._tested)
        worker.finished.connect(self._forget_finished_testers)
        self.testers.append(worker)
        self.ctx.track(worker)  # closing the app mid-test waits for it
        worker.start()
        return worker

    def _forget_finished_testers(self) -> None:
        self.testers = [w for w in self.testers if w.isRunning()]

    def _tested(self, ok: bool, text: str) -> None:
        self.say(text, ok)

    def sync_now(self, source: str) -> None:
        if not self.busy:
            self.sync_requested.emit(source)

    # -- TMDB -----------------------------------------------------------------

    def _client(self, secrets: list[str]) -> HttpClient:
        factory = self.ctx.client_factory
        return factory(secrets) if factory is not None else HttpClient(secrets=secrets)

    def _run_tmdb(self, call, then) -> CallWorker:
        worker = CallWorker(call)
        worker.done.connect(then)
        self.tmdb_worker = self.ctx.track(worker)
        worker.start()
        self.refresh_status()
        return worker

    def connect_tmdb(self, approve=None) -> CallWorker | None:
        """Ask TMDB for a request token, have it approved in the browser, then swap it for
        the account's access token. `approve(url)` stands in for the browser and the
        approval question (tests); it returns whether to go on."""
        read_token = self.ctx.store.get(self.profile_id, KEYS["tmdb"])
        if not read_token:
            self.refresh_status()
            return None
        client = self._client([read_token])

        def requested(token, error: str) -> None:
            self.tmdb_worker.wait()
            if token is None:
                self._tmdb_problem(f"TMDB said no: {error}")
                return
            url = tmdb.approve_url(token)
            if not (approve or self._approve_in_browser)(url):
                self.say("")
                self.refresh_status()
                return
            self._run_tmdb(lambda: tmdb.access_token(client, read_token, token), connected)

        def connected(result, error: str) -> None:
            self.tmdb_worker.wait()
            if result is None:
                self._tmdb_problem(f"Not connected: {error}")
                return
            user_token, account_id = result
            self.ctx.store.set(self.profile_id, "TMDB_USER_TOKEN", user_token)
            self.ctx.store.set(self.profile_id, "TMDB_ACCOUNT_ID", account_id)
            self.say("TMDB connected. Sync everything now includes it.")
            self.refresh_status()
            self.changed.emit()

        worker = self._run_tmdb(lambda: tmdb.request_token(client, read_token), requested)
        self.say("Asking TMDB…")
        return worker

    def _tmdb_problem(self, text: str) -> None:
        self.refresh_status()
        self.say(text, ok=False)

    def _approve_in_browser(self, url: str) -> bool:
        QDesktopServices.openUrl(QUrl(url))
        answer = QMessageBox.question(
            self,
            "Approve Taste Engine on TMDB",
            "TMDB opened in your browser. Approve Taste Engine there, then click Yes here. "
            "Click No to stop.",
        )
        return answer == QMessageBox.StandardButton.Yes

    def disconnect_tmdb(self) -> CallWorker | None:
        """Forget the account's token here, and ask TMDB to forget it too."""
        read_token = self.ctx.store.get(self.profile_id, KEYS["tmdb"])
        user_token = self.ctx.store.get(self.profile_id, "TMDB_USER_TOKEN")
        for name in ("TMDB_USER_TOKEN", "TMDB_ACCOUNT_ID"):
            self.ctx.store.delete(self.profile_id, name)
        self.say("TMDB disconnected.")
        self.refresh_status()
        self.changed.emit()
        if not (read_token and user_token):
            return None
        client = self._client([read_token, user_token])

        def revoked(_result, _error: str) -> None:
            # Forgotten here either way; TMDB forgetting it too is a courtesy.
            self.tmdb_worker.wait()
            self.refresh_status()

        return self._run_tmdb(lambda: tmdb.revoke(client, read_token, user_token), revoked)

    # -- Hardcover --------------------------------------------------------------

    def _run_hardcover(self, call, then) -> CallWorker:
        worker = CallWorker(call)
        worker.done.connect(then)
        self.hardcover_worker = self.ctx.track(worker)
        worker.start()
        self.refresh_status()
        return worker

    def connect_hardcover(self, browser=None) -> CallWorker | None:
        """Sign in to Hardcover: open its approval page in the browser, wait for it to send
        you back to the app, then swap the code for tokens. `browser(url)` stands in for
        opening the browser (tests)."""
        if not self.profile_id or self.sign_in is not None:
            return None
        try:
            sign_in = hardcover.SignIn()
        except OSError:
            self.say("Couldn't listen for Hardcover's reply here.", ok=False)
            return None
        self.sign_in = sign_in
        client = hardcover.client_for(self._client([]))
        profile_id = self.profile_id
        now = self.ctx.now()

        def sign_in_and_swap():
            code = sign_in.wait()
            tokens = hardcover.exchange(client, sign_in, code, now)
            client.secrets += [tokens.access, tokens.refresh]
            return tokens, hardcover.me(client, tokens.access)[1]

        def done(result, error: str) -> None:
            self.hardcover_worker.wait()
            self.sign_in = None
            if result is None:
                self.refresh_status()
                if error == "Stopped.":
                    self.say("")
                else:
                    self.say(f"Not connected: {error}", ok=False)
                return
            tokens, username = result
            credentials.save_hardcover(profile_id, self.ctx.store, tokens)
            self.refresh_status()
            who = f" as {username}" if username else ""
            self.say(f"Hardcover connected{who}. Bringing in your books now.")
            self.changed.emit()
            self.hardcover_connected.emit()

        worker = self._run_hardcover(sign_in_and_swap, done)
        (browser or (lambda url: QDesktopServices.openUrl(QUrl(url))))(sign_in.url)
        self.say(
            "Approve Taste Engine on Hardcover in your browser. This page carries on by itself "
            "once you're sent back."
        )
        return worker

    def stop_hardcover(self) -> None:
        if self.sign_in is not None:
            self.sign_in.cancel()

    def copy_hardcover_link(self) -> None:
        if self.sign_in is not None:
            QApplication.clipboard().setText(self.sign_in.url)
            self.say("Link copied. Paste it into your browser, approve, and come back here.")

    def disconnect_hardcover(self) -> CallWorker | None:
        """Forget the tokens here, and sign out on Hardcover too."""
        if not self.profile_id:
            return None
        refresh = self.ctx.store.get(self.profile_id, "HARDCOVER_REFRESH_TOKEN")
        credentials.forget_hardcover(self.profile_id, self.ctx.store)
        self.say("Hardcover disconnected.")
        self.refresh_status()
        self.changed.emit()
        if not refresh:
            return None
        client = self._client([refresh])

        def revoked(_result, _error: str) -> None:
            # Forgotten here either way; Hardcover forgetting it too is a courtesy.
            self.hardcover_worker.wait()
            self.refresh_status()

        return self._run_hardcover(lambda: hardcover.revoke(client, refresh), revoked)
