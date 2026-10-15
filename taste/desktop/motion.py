"""Transitions: pages, the sidebar, the sync log, banners, pictures, and cards.

One place decides how much the app moves:
- "full": fades with a little travel, slides, and staggers (DURATION ms).
- "reduced": quick fades only, nothing that travels or bounces.
- "off": everything changes at once.

The choice is a window preference (`motion` in ui_state.json, set on the Settings
page), and Windows' own "Animation effects" setting (Accessibility, Visual
effects) turns it off whatever the choice. Tests turn it off too (`override`), so
they never wait on an animation.

Every helper finishes in the same state the instant version would reach, so code
that reads a widget's size or visibility right after calling one still works.
"""

from __future__ import annotations

import contextlib
import sys
from collections.abc import Callable

from PySide6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QParallelAnimationGroup,
    QPoint,
    QSequentialAnimationGroup,
    Qt,
    QVariantAnimation,
)
from PySide6.QtWidgets import QGraphicsOpacityEffect, QLabel, QWidget

from taste.desktop import ui_state

LEVELS = ("full", "reduced", "off")
DURATION = 320  # ms, "relaxed": chosen by the user from a live demo (2026-10-15)
REDUCED = 150  # ms, fades only
TRAVEL = 10  # px a page or card rises while fading in, in "full"
STAGGER = 40  # ms between cards fading in one after another
STAGGER_MAX = 12  # cards past this many appear with the last group, not later

override: str | None = None  # tests set "off"; None means the saved choice
_keep: set[QAbstractAnimation] = set()  # running animations, so they aren't collected
_revealing: dict = {}  # widget -> (its open/close animation, its own min and max height)
_targets: dict = {}  # widget -> where its running open/close animation ends


def windows_allows() -> bool:
    """False when Windows' "Animation effects" is off. True anywhere else."""
    if sys.platform != "win32":
        return True
    try:
        import ctypes

        value = ctypes.c_int(1)
        spi_getclientareaanimation = 0x1042
        ok = ctypes.windll.user32.SystemParametersInfoW(
            spi_getclientareaanimation, 0, ctypes.byref(value), 0
        )
        return bool(value.value) if ok else True
    except Exception:  # an unusual Windows build: animate rather than fail
        return True


def chosen() -> str:
    """The saved choice, whatever Windows says ("full" when never set)."""
    value = ui_state.load().get("motion", "full")
    return value if value in LEVELS else "full"


def level() -> str:
    """How much the app moves right now."""
    if override is not None:
        return override
    return chosen() if windows_allows() else "off"


def duration(full: int = DURATION) -> int:
    """Milliseconds for an animation at the current level (0 means no animation)."""
    return {"full": full, "reduced": min(full, REDUCED), "off": 0}[level()]


def travels() -> bool:
    """Whether things may slide (only in "full")."""
    return level() == "full"


def _run(animation: QAbstractAnimation, done: Callable[[], None] | None = None) -> None:
    """Start an animation. One with a parent lives and dies with it; one without is
    kept here until it finishes."""
    owned = animation.parent() is not None
    if not owned:
        _keep.add(animation)

    def finished() -> None:
        _keep.discard(animation)
        if done is not None:
            done()

    animation.finished.connect(finished)
    animation.start()


def _set_opacity(effect: QGraphicsOpacityEffect, value) -> None:
    with contextlib.suppress(RuntimeError):  # deleted between two frames
        effect.setOpacity(float(value))


def _opacity(
    effect: QGraphicsOpacityEffect, start: float, end: float, ms: int, curve: QEasingCurve
) -> QVariantAnimation:
    """A fade with no target object: it sets the effect's opacity itself. An animation
    with a target warned ("Changing state of an animation without target") whenever
    the widget it faded was deleted mid-fade, which For You's grid does on every update.
    Callers give it (or its group) the faded widget as parent, so it goes with it."""
    animation = QVariantAnimation()
    animation.setDuration(ms)
    animation.setStartValue(float(start))
    animation.setEndValue(float(end))
    animation.setEasingCurve(curve)
    animation.valueChanged.connect(lambda value: _set_opacity(effect, value))
    return animation


def _curve() -> QEasingCurve:
    return QEasingCurve(QEasingCurve.Type.OutCubic)


# ---------------------------------------------------------------------------
# Page changes: a snapshot of the old page fades away over the new one
# ---------------------------------------------------------------------------


def crossfade(container: QWidget, switch: Callable[[], None]) -> None:
    """Run `switch` (which changes what `container` shows) behind a fading snapshot
    of how it looked, so the new content seems to fade and rise into place.

    Only a picture of the old page moves, never the live widgets, so layouts and
    scroll positions are never disturbed.
    """
    ms = duration()
    if not ms or not container.isVisible() or container.width() <= 0:
        switch()
        return
    snapshot = QLabel(container)
    snapshot.setPixmap(container.grab())
    snapshot.setGeometry(container.rect())
    snapshot.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
    switch()
    snapshot.show()
    snapshot.raise_()
    effect = QGraphicsOpacityEffect(snapshot)
    snapshot.setGraphicsEffect(effect)
    group = QParallelAnimationGroup(snapshot)
    group.addAnimation(_opacity(effect, 1.0, 0.0, ms, QEasingCurve(QEasingCurve.Type.OutQuad)))
    if travels():
        # The old picture drifts up a little as it fades, so the page reads as arriving.
        drift = QVariantAnimation()
        drift.setDuration(ms)
        drift.setStartValue(0.0)
        drift.setEndValue(float(-TRAVEL))
        drift.setEasingCurve(_curve())
        drift.valueChanged.connect(lambda value: _move(snapshot, QPoint(0, round(float(value)))))
        group.addAnimation(drift)
    _run(group, snapshot.deleteLater)


def _move(widget: QWidget, to: QPoint) -> None:
    with contextlib.suppress(RuntimeError):
        widget.move(to)


# ---------------------------------------------------------------------------
# Sizes: the sidebar's width, a log or banner opening
# ---------------------------------------------------------------------------


def resize_width(widget: QWidget, width: int) -> None:
    """Animate a fixed width (the sidebar collapsing). Ends fixed at `width`."""
    ms = duration()
    start = widget.width() if widget.isVisible() else width
    if not ms or start == width:
        widget.setFixedWidth(width)
        return
    animation = QVariantAnimation()
    animation.setDuration(ms)
    animation.setStartValue(start)
    animation.setEndValue(width)
    animation.setEasingCurve(_curve())
    animation.valueChanged.connect(lambda value: widget.setFixedWidth(int(value)))
    _run(animation, lambda: widget.setFixedWidth(width))


def reveal(widget: QWidget, shown: bool, done: Callable[[], None] | None = None) -> None:
    """Open or close a widget by animating its height (the sync log, a banner).

    Visibility is set at once when opening (so callers and tests see it), and the
    widget hides once it has closed. Its own height limits (a fixed-height log) are
    put back at the end. The widget must already have a parent.
    """
    ms = duration()
    running = _revealing.pop(widget, None)
    if running is not None:  # changed its mind halfway: start from where it is
        animation, low, high = running
        animation.stop()
        _keep.discard(animation)
    else:
        low, high = widget.minimumHeight(), widget.maximumHeight()
    if not ms or not widget.parentWidget() or not widget.parentWidget().isVisible():
        widget.setMinimumHeight(low)
        widget.setMaximumHeight(high)
        widget.setVisible(shown)
        if done:
            done()
        return
    target = max(widget.sizeHint().height(), low, 1)
    start = 0 if shown else max(widget.height(), 1)
    end = target if shown else 0
    widget.setFixedHeight(start)
    widget.setVisible(True)
    animation = QVariantAnimation()
    animation.setDuration(ms)
    animation.setEasingCurve(_curve())
    animation.setStartValue(start)
    animation.setEndValue(end)
    animation.valueChanged.connect(lambda value: widget.setFixedHeight(int(value)))

    def finish() -> None:
        _revealing.pop(widget, None)
        widget.setMinimumHeight(low)
        widget.setMaximumHeight(high)
        if not shown:
            widget.setVisible(False)
        if done:
            done()

    _revealing[widget] = (animation, low, high)
    _targets[widget] = shown
    _run(animation, finish)


def is_open(widget: QWidget) -> bool:
    """Whether a widget is open or opening (a closing one counts as closed)."""
    if widget in _revealing:
        return _targets.get(widget, not widget.isHidden())
    return not widget.isHidden()


# ---------------------------------------------------------------------------
# Fades: cards appearing and leaving
# ---------------------------------------------------------------------------


def fade_in(widgets: list[QWidget], stagger: bool = True) -> None:
    """Fade widgets in, one after another (For You's grid, Rev's sections)."""
    ms = duration(240)
    if not ms:
        return
    for i, widget in enumerate(widgets):
        effect = QGraphicsOpacityEffect(widget)
        effect.setOpacity(0.0)
        widget.setGraphicsEffect(effect)
        delay = min(i, STAGGER_MAX) * STAGGER if stagger and travels() else 0
        sequence = QSequentialAnimationGroup(widget)
        if delay:
            sequence.addPause(delay)
        sequence.addAnimation(
            _opacity(effect, 0.0, 1.0, ms, QEasingCurve(QEasingCurve.Type.OutQuad))
        )
        # Effects slow painting, so each one goes once its fade is done.
        _run(sequence, lambda w=widget: _drop_effect(w))


def fade_out(widget: QWidget, done: Callable[[], None]) -> None:
    """Fade a widget away, then call `done` (which removes it)."""
    ms = duration(200)
    if not ms:
        done()
        return
    effect = QGraphicsOpacityEffect(widget)
    widget.setGraphicsEffect(effect)
    fade = _opacity(effect, 1.0, 0.0, ms, QEasingCurve(QEasingCurve.Type.Linear))
    fade.setParent(widget)
    _run(fade, done)


def _drop_effect(widget: QWidget) -> None:
    with contextlib.suppress(RuntimeError):  # the widget was deleted while it faded
        widget.setGraphicsEffect(None)


def tween(
    start: float,
    end: float,
    step: Callable[[float], None],
    ms: int | None = None,
    owner: QWidget | None = None,
) -> QVariantAnimation | None:
    """Call `step` with values from start to end over time (a sliding highlight, a
    picture's opacity). Calls it once with `end` when motion is off. `owner` is the
    widget being moved: the animation goes when it does, mid-way or not."""
    ms = duration() if ms is None else ms
    if not ms or level() == "off":
        step(end)
        return None
    animation = QVariantAnimation(owner)
    animation.setDuration(ms)
    animation.setStartValue(float(start))
    animation.setEndValue(float(end))
    animation.setEasingCurve(_curve())
    animation.valueChanged.connect(lambda value: step(float(value)))
    _run(animation)
    return animation
