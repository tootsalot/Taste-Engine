"""Rev: my month or year as a deck of phone-sized cards, swiped one at a time.

The cards are drawn in a background thread (workers.RecapWorker). The deck shows
the current card large with its neighbors dimmed on either side; the arrows (or
the arrow keys, or a click on a neighbor) slide to the next one. The panel beside
it saves the card on screen, or all of them, and explains how the numbers were
worked out. The first save asks for a folder and later saves go straight there
(`recap_folder` in ui_state.json, for every profile). Recaps for finished months and
years also make themselves (see taste.recap).
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QIcon, QImage, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from taste import activity, recap, settings
from taste.activity import Period
from taste.desktop import motion, ui_state
from taste.desktop.context import AppContext
from taste.desktop.widgets import Card, LazyPage, Segmented, heading, label, section
from taste.desktop.workers import RecapWorker

TITLE = "Rev"
SIDE_WIDTH = 380
THUMB_GAP = 8
CARD_RATIO = 1920 / 1080
NEIGHBOR_SCALE = 0.78  # the cards either side of the current one
NEIGHBOR_ALPHA = 0.35
NOTES = (
    "Plays are the ones that reached Last.fm. {scope}",
    "Hours of music are estimates from Last.fm track lengths. A track with no known length "
    'counts as a typical one, so they\'re rounded and marked "about".',
    "A show counts in the month of the finish date you typed on MyAnimeList. A completed show "
    "without one counts only if you finished it after your first sync here, when the app saw "
    "it happen; older ones like that are left out of monthly recaps.",
    "Episodes come from your progress between syncs. Before the app tracked that, a show "
    "finished in the month counts all of its episodes.",
    "Your score against MAL compares with the MAL mean rounded to a whole point, since your "
    "scores are whole points too.",
    "Books count by the day you finished them on Hardcover; a book read twice counts twice. "
    "Your stars compare with the Hardcover average rounded to the nearest half star.",
    "Films and TV count by the day you rated them on TMDB, which keeps no watch dates.",
)


class Deck(QWidget):
    """The cards as a carousel: the current one at full height, its neighbors smaller and
    dimmed beside it. Changing card slides them across (or just swaps, with motion off)."""

    picked = Signal(int)  # a neighbor was clicked

    def __init__(self) -> None:
        super().__init__()
        self.cards: list[QImage] = []
        self.index = 0
        self.offset = 0.0  # while sliding: how far the cards still have to go, in cards
        self.cam_offset = 0.0  # and how far the view still has to go
        self._slide = None
        self.setMinimumSize(320, 420)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setCursor(Qt.CursorShape.ArrowCursor)

    def set_cards(self, cards: list[QImage], index: int = 0) -> None:
        self.cards = list(cards)
        self.index = min(max(index, 0), max(len(self.cards) - 1, 0))
        self.offset = self.cam_offset = 0.0
        self.update()

    def go(self, index: int) -> bool:
        if not self.cards or not 0 <= index < len(self.cards) or index == self.index:
            return False
        # From where the cards are now: a click mid-slide used to start a second slide
        # from the card's resting place while the first kept running (2.0's jumpy deck).
        if self._slide is not None:
            self._slide.stop()
            self._slide = None
        start = float(index - self.index) + self.offset
        view = self.camera_for(self.index) + self.cam_offset  # where the view is now
        self.index = index
        cam_start = view - self.camera_for(index)
        ms = motion.duration(320) if motion.travels() else 0
        if not ms:
            self.offset = self.cam_offset = 0.0
            self.update()
            return True

        def step(t: float) -> None:
            self.offset = start * (1 - t)
            self.cam_offset = cam_start * (1 - t)
            self.update()

        self.offset, self.cam_offset = start, cam_start
        self._slide = motion.tween(0.0, 1.0, step, ms, owner=self)
        return True

    def card_size(self) -> tuple[float, float]:
        height = self.height() - 8
        width = height / CARD_RATIO
        if width > self.width() * 0.6:  # a narrow window: let the card set the size
            width = self.width() * 0.6
            height = width * CARD_RATIO
        return width, height

    def _spacing(self) -> float:
        """From one card's middle to the next. Neighbors sit beside the current card, or
        tuck behind it when the panel is too narrow, but never run off its edge."""
        width, _ = self.card_size()
        side = width * NEIGHBOR_SCALE
        return max(min(width * 0.5 + side * 0.5 + 24, self.width() / 2 - side / 2 - 4), 1.0)

    def camera_for(self, index: int) -> float:
        """Which card the view centers on. In the middle, the current one; at either end
        the view moves inward (as far as the current card still fits) so neighbors fill
        both sides instead of half the deck being empty (2.1)."""
        count = len(self.cards)
        if count < 2:
            return float(index)
        width, _ = self.card_size()
        spacing = self._spacing()
        side = width * NEIGHBOR_SCALE
        reach = max(int((self.width() / 2 - side / 2) // spacing), 1)  # neighbors per side
        target = (
            (count - 1) / 2 if count <= 2 * reach + 1 else min(max(index, reach), count - 1 - reach)
        )
        room = max((self.width() / 2 - width / 2 - 4) / spacing, 0.0)  # how far it may move
        return index + min(max(target - index, -room), room)

    def _rect(self, place: float, distance: float) -> QRectF:
        """Where a card is drawn: `place` cards from the view's middle, at the size for
        being `distance` cards from the current one."""
        width, height = self.card_size()
        scale = 1 - (1 - NEIGHBOR_SCALE) * min(abs(distance), 1.0)
        w, h = width * scale, height * scale
        center = self.width() / 2 + place * self._spacing()
        return QRectF(center - w / 2, (self.height() - h) / 2, w, h)

    def rect_of(self, i: int) -> QRectF:
        view = self.camera_for(self.index) + self.cam_offset
        return self._rect(i - view, i - self.index + self.offset)

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        if not self.cards:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        # Far ones first, so the current card is drawn on top.
        order = sorted(range(len(self.cards)), key=lambda i: -abs(i - self.index + self.offset))
        for i in order:
            position = i - self.index + self.offset
            rect = self.rect_of(i)
            if rect.right() < 0 or rect.left() > self.width():
                continue
            painter.setOpacity(1 - (1 - NEIGHBOR_ALPHA) * min(abs(position), 1.0))
            clip = QPainterPath()
            clip.addRoundedRect(rect, 18, 18)
            painter.save()
            painter.setClipPath(clip)
            painter.drawImage(rect, self.cards[i])
            painter.restore()
        painter.setOpacity(1.0)
        painter.end()

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        point = QPointF(event.position().x(), self.height() / 2)
        if not self.rect_of(self.index).contains(point):
            for i in range(len(self.cards)):
                if i != self.index and self.rect_of(i).contains(point):
                    self.picked.emit(i)  # any card in sight, not only the next one
                    return
        super().mousePressEvent(event)


class RecapPage(LazyPage, QWidget):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        self.profile_id: str | None = None
        self.period = Period("month", "2000-01")
        self.data: recap.RecapData | None = None
        self.cards: list[tuple[str, QImage]] = []
        self.worker: RecapWorker | None = None
        self.workers: list[RecapWorker] = []  # kept alive until they finish
        self.saved_path: Path | None = None
        self.waiting = False  # the latest drawing hasn't arrived yet
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 20, 28, 20)
        layout.setSpacing(12)
        top = QHBoxLayout()
        top.setSpacing(10)
        top.addWidget(heading(TITLE, 28))
        top.addSpacing(16)
        self.kind_switch = Segmented([("month", "Month"), ("year", "Year")])
        self.kind_switch.changed.connect(self.set_kind)
        top.addWidget(self.kind_switch)
        self.back = QPushButton("‹")
        self.back.setToolTip("Earlier")
        self.back.clicked.connect(lambda: self.show_period(self.period.previous()))
        top.addWidget(self.back)
        self.period_label = heading("", 20)
        top.addWidget(self.period_label)
        self.forward = QPushButton("›")
        self.forward.setToolTip("Later")
        self.forward.clicked.connect(lambda: self.show_period(self.period.next()))
        top.addWidget(self.forward)
        top.addStretch()
        self.status = label("", role="muted")
        top.addWidget(self.status)
        layout.addLayout(top)

        body = QHBoxLayout()
        body.setSpacing(16)
        # The deck, on its own panel: arrows either side, which card under it.
        self.deck_card = Card()
        self.deck_card.body.setContentsMargins(16, 20, 16, 16)
        row = QHBoxLayout()
        row.setSpacing(12)
        self.prev_card = QPushButton("‹")
        self.prev_card.setProperty("role", "deck-arrow")
        self.prev_card.setToolTip("Previous card (Left arrow)")
        self.prev_card.clicked.connect(lambda: self.show_card(self.deck.index - 1))
        self.next_card = QPushButton("›")
        self.next_card.setProperty("role", "deck-arrow")
        self.next_card.setToolTip("Next card (Right arrow)")
        self.next_card.clicked.connect(lambda: self.show_card(self.deck.index + 1))
        self.deck = Deck()
        self.deck.picked.connect(self.show_card)
        row.addWidget(self.prev_card, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.deck, 1)
        row.addWidget(self.next_card, 0, Qt.AlignmentFlag.AlignVCenter)
        self.deck_card.body.addLayout(row, 1)
        self.position = label("", role="muted")
        self.position.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.deck_card.body.addWidget(self.position)
        self.empty = label("", role="muted", wrap=True)
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.deck_card.body.addWidget(self.empty, 1)
        self.empty.setVisible(False)
        body.addWidget(self.deck_card, 1)

        # Saving, and how the numbers were worked out.
        side = Card()
        side.setFixedWidth(SIDE_WIDTH)
        side.body.setContentsMargins(22, 22, 22, 22)
        side.body.setSpacing(10)
        side.body.addWidget(section("Save"))
        self.save_button = QPushButton("Save this card")
        self.save_button.setProperty("role", "primary")
        self.save_button.clicked.connect(lambda: self.save_card())
        side.body.addWidget(self.save_button)
        self.save_all_button = QPushButton("Save all cards")
        self.save_all_button.clicked.connect(lambda: self.save_all())
        side.body.addWidget(self.save_all_button)
        self.folder_line = label("", role="caption", wrap=True)
        side.body.addWidget(self.folder_line)
        links = QHBoxLayout()
        self.change_button = QPushButton("Change folder…")
        self.change_button.setProperty("role", "link")
        self.change_button.clicked.connect(lambda: self.change_folder())
        self.show_button = QPushButton("Show in folder")
        self.show_button.setProperty("role", "link")
        self.show_button.clicked.connect(lambda: self.show_saved())
        links.addWidget(self.change_button)
        links.addStretch()
        links.addWidget(self.show_button)
        side.body.addLayout(links)
        self.rebuild_button = QPushButton("Rebuild")
        self.rebuild_button.setToolTip("Make this recap again from the data as it is now.")
        self.rebuild_button.clicked.connect(lambda: self.rebuild())
        side.body.addWidget(self.rebuild_button)
        side.body.addSpacing(10)
        # Folded to one line: seven paragraphs of fine print read as a wall of text.
        self.about_toggle = QPushButton("About these numbers")
        self.about_toggle.setProperty("role", "link")
        self.about_toggle.setCheckable(True)
        self.about_toggle.toggled.connect(self.show_notes)
        side.body.addWidget(self.about_toggle, 0, Qt.AlignmentFlag.AlignLeft)
        self.notes = [label("", role="caption", wrap=True) for _ in NOTES]
        for note in self.notes:
            side.body.addWidget(note)
            note.setVisible(False)
        # Every card small, three across: where you are, and a click to jump there. The
        # panel was mostly bare (2.1's fill test counts empty space inside cards).
        side.body.addSpacing(10)
        side.body.addWidget(section("Cards"))
        self.thumb_grid = QGridLayout()
        self.thumb_grid.setSpacing(THUMB_GAP)
        side.body.addLayout(self.thumb_grid)
        self.thumbs: list[QToolButton] = []
        side.body.addStretch(1)
        self.so_far_note = label("", role="muted", wrap=True)
        side.body.addWidget(self.so_far_note)
        body.addWidget(side)
        layout.addLayout(body, 1)
        self._refresh_folder_line()
        self._refresh_buttons()

    def show_notes(self, shown: bool) -> None:
        for note in self.notes:
            motion.reveal(note, shown)
        self.about_toggle.setText("Hide the notes" if shown else "About these numbers")

    # -- periods ------------------------------------------------------------

    def set_profile(self, profile_id: str | None) -> None:
        self.profile_id = profile_id
        self.status.setText("")
        if not profile_id:
            return
        conn = self.ctx.connect(profile_id)
        try:
            latest = activity.current(conn, "month", self.ctx.now()).previous()
            scope = settings.lastfm_scope_note(conn)
        finally:
            conn.close()
        for note, text in zip(self.notes, NOTES, strict=True):
            note.setText(text.format(scope=scope))
        self.show_period(latest)

    def set_kind(self, kind: str) -> None:
        if kind == self.period.kind:
            return
        if kind == "year":
            self.show_period(Period("year", self.period.key[:4]))
        else:
            year = self.period.key
            this = self._current("month")
            self.show_period(this if this.key.startswith(year) else Period("month", f"{year}-12"))

    def _current(self, kind: str) -> Period:
        conn = self.ctx.connect(self.profile_id)
        try:
            return activity.current(conn, kind, self.ctx.now())
        finally:
            conn.close()

    def show_period(self, period: Period) -> None:
        """Draw a period's recap. Periods after the current one aren't offered."""
        if not self.profile_id:
            return
        current = self._current(period.kind)
        if period.key > current.key:
            period = current
        self.period = period
        self.kind_switch.set_value(period.kind)
        self.period_label.setText(period.label)
        self.forward.setEnabled(period.key < current.key)
        self.so_far_note.setText(
            f"Not over yet: this is {period.short} so far. It's made again once it ends."
            if period == current
            else ""
        )
        self.status.setText("Drawing…")
        self._start(RecapWorker(self.profile_id, self.ctx.art.cache, self.ctx.now(), period))

    def lazy_refresh(self) -> None:  # LazyPage: after a sync, when shown
        self.refresh()

    def refresh(self) -> None:
        if self.profile_id:
            self.show_period(self.period)

    def _start(self, worker: RecapWorker) -> RecapWorker:
        self.worker = worker
        self.waiting = True
        # Bound methods, not lambdas: Qt drops them when the page is deleted, so a result
        # that arrives after that never reaches a widget that's gone.
        worker.ready.connect(self._worker_ready)
        worker.failed.connect(self._worker_failed)
        worker.finished.connect(self._worker_finished)
        self.workers.append(worker)
        self.ctx.track(worker)
        worker.start()
        return worker

    def _worker_ready(self, kind: str, key: str, data, cards: list) -> None:
        worker = self.sender()
        if worker is not self.worker:
            return  # only the latest request is shown; an older one finishing late is ignored
        self._on_ready(kind, key, data, cards)
        if getattr(worker, "rebuild", False):
            self.status.setText(f"Rebuilt {self.period.label}.")

    def _worker_failed(self, text: str) -> None:
        if self.sender() is self.worker:
            self.waiting = False
        self.status.setText(text)

    def _worker_finished(self) -> None:
        worker = self.sender()
        if worker in self.workers:
            self.workers.remove(worker)

    def is_busy(self) -> bool:
        """Until the latest cards have arrived (a thread can finish before its result does)."""
        return self.waiting or any(w.isRunning() for w in self.workers)

    def _on_ready(self, kind: str, key: str, data, cards: list) -> None:
        self.waiting = False
        self.data = data
        has = recap.has_data(data)
        same = self.cards and [n for n, _ in self.cards] == [n for n, _ in cards]
        keep = self.deck.index if same else 0
        self.cards = list(cards) if has else []
        self.deck.set_cards([image for _, image in self.cards], keep)
        self._fill_thumbs()
        self.empty.setText(
            ""
            if has
            else f"Nothing to recap for {self.period.label}: no plays, shows, books, or films."
        )
        self.empty.setVisible(not has)
        for widget in (self.deck, self.prev_card, self.next_card, self.position):
            widget.setVisible(has)
        self._refresh_buttons()
        if self.status.text() == "Drawing…":
            self.status.setText("")

    # -- the deck -----------------------------------------------------------

    def _fill_thumbs(self) -> None:
        for thumb in self.thumbs:
            self.thumb_grid.removeWidget(thumb)
            thumb.deleteLater()
        self.thumbs = []
        width = (SIDE_WIDTH - 44 - 2 * THUMB_GAP) // 3
        size = QSize(width, round(width * CARD_RATIO))
        for i, (name, image) in enumerate(self.cards):
            thumb = QToolButton()
            thumb.setObjectName("CardThumb")
            thumb.setCheckable(True)
            thumb.setIcon(QIcon(QPixmap.fromImage(image.scaled(
                size * 2, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))))  # fmt: skip
            thumb.setIconSize(size)
            thumb.setToolTip(f"Card {i + 1}: {name}")
            thumb.clicked.connect(lambda _=False, n=i: self.show_card(n))
            self.thumb_grid.addWidget(thumb, i // 3, i % 3)  # in its layout before shown
            self.thumbs.append(thumb)
        self._refresh_buttons()

    def show_card(self, index: int) -> None:
        if self.deck.go(index):
            self._refresh_buttons()

    def keyPressEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        if event.key() == Qt.Key.Key_Left:
            self.show_card(self.deck.index - 1)
        elif event.key() == Qt.Key.Key_Right:
            self.show_card(self.deck.index + 1)
        else:
            super().keyPressEvent(event)

    def _refresh_buttons(self) -> None:
        count = len(self.cards)
        index = self.deck.index
        self.prev_card.setEnabled(index > 0)
        self.next_card.setEnabled(index < count - 1)
        for button in (self.save_button, self.save_all_button, self.rebuild_button):
            button.setEnabled(count > 0)
        self.save_all_button.setText(f"Save all {count} cards" if count else "Save all cards")
        self.position.setText(f"Card {index + 1} of {count}" if count else "")
        for i, thumb in enumerate(self.thumbs):
            thumb.setChecked(i == index)

    # -- saving -------------------------------------------------------------

    def folder(self) -> Path | None:
        """Where Save puts cards, once chosen. None until then."""
        saved = ui_state.load().get("recap_folder")
        return Path(saved) if saved else None

    def _ask_folder(self) -> str:
        start = str(self.folder() or Path.home() / "Pictures")
        return QFileDialog.getExistingDirectory(self, "Save recaps in", start)

    def change_folder(self, folder: str | None = None) -> Path | None:
        """Pick the folder recaps save to. `folder` skips the dialog (tests)."""
        chosen = folder or self._ask_folder()
        if not chosen:
            return None
        ui_state.save(recap_folder=str(Path(chosen)))
        self._refresh_folder_line()
        return Path(chosen)

    def _target(self, folder: str | None) -> Path | None:
        """The save folder: the remembered one, or (the first time) asked for."""
        target = self.folder()
        if target is None or not target.is_dir():
            target = self.change_folder(folder)
        if target is not None:
            target.mkdir(parents=True, exist_ok=True)
        return target

    def _save(self, target: Path, index: int) -> Path | None:
        path = target / self.data.save_name(index)
        if not self.cards[index][1].save(str(path)):
            self.status.setText("Couldn't save there. Try another folder.")
            return None
        return path

    def save_card(self, folder: str | None = None) -> Path | None:
        """Save the card on screen. The first time it asks where; after that it goes
        straight there. `folder` stands in for the first-time question (tests)."""
        if not self.cards or self.data is None:
            return None
        target = self._target(folder)
        if target is None:
            return None
        path = self._save(target, self.deck.index)
        if path is not None:
            self.saved_path = path
            self.status.setText(f"Saved {path.name}")
        return path

    def save_all(self, folder: str | None = None) -> list[Path]:
        """Every card of this recap, to the same folder."""
        if not self.cards or self.data is None:
            return []
        target = self._target(folder)
        if target is None:
            return []
        saved = [p for i in range(len(self.cards)) if (p := self._save(target, i)) is not None]
        if saved:
            self.saved_path = saved[-1]
            self.status.setText(f"Saved {len(saved)} cards to {target.name}")
        return saved

    def show_saved(self) -> None:
        target = self.saved_path.parent if self.saved_path else self.folder()
        if target is not None and target.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))

    def _refresh_folder_line(self) -> None:
        target = self.folder()
        self.folder_line.setText(
            f"Saves to {target}" if target else "The first save asks where to keep recaps."
        )
        self.show_button.setEnabled(target is not None)

    def rebuild(self) -> RecapWorker | None:
        """Make the recap again now and keep it in its own folder."""
        if not self.profile_id:
            return None
        self.status.setText("Rebuilding…")
        worker = RecapWorker(
            self.profile_id,
            self.ctx.art.cache,
            self.ctx.now(),
            self.period,
            save_to=recap.folder(self.profile_id, self.period),
            record=self.period != self._current(self.period.kind),
        )
        worker.rebuild = True
        return self._start(worker)
