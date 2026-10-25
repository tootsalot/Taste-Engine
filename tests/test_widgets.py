"""The painted charts and small helpers in taste.desktop.widgets, offscreen."""

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtWidgets import QLabel

from taste.charts import Band
from taste.desktop.cards import GenreCard
from taste.desktop.plots import BandChart, BarChart, LeanAxis, nice_ticks
from taste.desktop.widgets import elided, elided_lines, small_font
from taste.overview import GenreLean


def lean(genre, diff):
    return GenreLean(genre, 10, 7.0 + diff, 7.0, diff)


@pytest.fixture
def font(qapp):
    f = QFont()
    f.setPixelSize(14)
    return f


def test_scale_lines_land_on_round_numbers():
    assert nice_ticks(550) == [250, 500]
    assert nice_ticks(120) == [50, 100]
    assert nice_ticks(7) == [2, 4, 6]
    assert nice_ticks(1) == [1]
    assert nice_ticks(0) == []


def test_bars_leave_room_for_their_numbers_and_the_scale(qtbot):
    chart = BarChart(value_labels=True)
    qtbot.addWidget(chart)
    chart.resize(300, 160)
    chart.set_data([5, 10], ["a", "b"])
    tallest = chart.bar_rects()[1]
    assert tallest.top() >= BarChart.VALUE_ROOM  # the count sits above it

    scaled = BarChart(scale=True)
    qtbot.addWidget(scaled)
    scaled.resize(300, 160)
    scaled.set_data([300, 550], ["a", "b"])
    assert scaled.bar_rects()[0].left() >= BarChart.SCALE_ROOM  # numbers for the lines
    scaled.set_data([12500, 30], ["a", "b"])
    room = QFontMetrics(small_font(scaled)).horizontalAdvance("10,000")
    assert scaled.bar_rects()[0].left() - 6 >= room  # a five-digit scale number isn't cut off


def test_hovering_a_bar_shows_only_that_bar(qtbot):
    chart = BarChart(scale=True)
    qtbot.addWidget(chart)
    chart.resize(300, 160)
    chart.set_data([400, 120], ["Sep 26", "Oct 26"], unit="plays", partial_last=True)
    first, last = chart.bar_rects()
    assert chart.tip_at(first.center().x()) == "Sep 26: 400 plays"
    assert chart.tip_at(last.center().x()) == "Oct 26: 120 plays so far"
    assert chart.tip_at(1) == ""  # over the scale numbers, not a bar
    assert chart.toolTip() == ""  # never one list of every bar for the whole chart


@pytest.mark.parametrize("width", [420, 120])
def test_so_far_never_overlaps_the_numbers_above_the_bars(qtbot, width):
    chart = BarChart(value_labels=True)
    qtbot.addWidget(chart)
    chart.resize(width, 160)
    chart.set_data([3, 12, 9, 4], ["2023", "2024", "2025", "2026"], partial_last=True)
    metrics = QFontMetrics(small_font(chart))
    drawn = [
        metrics.boundingRect(box.toRect(), int(align), text)
        for box, align, text in chart.top_texts()
    ]
    for i, a in enumerate(drawn):
        assert a.left() >= 0 and a.right() <= width, chart.top_texts()[i][2]
        for b in drawn[i + 1 :]:
            assert not a.intersects(b), chart.top_texts()
    last = chart.top_texts()[-1][2]
    # "so far" joins the number when the bar has room for both; the dashed bar and its
    # hover text say it otherwise.
    assert last == ("4 so far" if width == 420 else "4")


def test_so_far_sits_above_the_taller_bars_beside_it(qtbot):
    chart = BarChart(scale=True, min_bar=18)  # the Dashboard's plays per week
    qtbot.addWidget(chart)
    chart.resize(300, 160)
    weeks = [150, 140, 160, 120, 150, 140, 160, 120, 150, 140, 160, 40]
    chart.set_data(weeks, [str(i) for i in range(len(weeks))], partial_last=True)
    assert chart.first_shown() == 0 and chart.bar_rects()[0].width() < 24  # narrow bars
    (box, _, text), *_ = chart.top_texts()
    assert text == "so far"
    spanned = [r for r in chart.bar_rects() if r.right() >= box.left() and r.left() <= box.right()]
    assert len(spanned) > 1  # wider than its own bar
    assert box.bottom() <= min(r.top() for r in spanned)  # over the bars, not on them


def test_a_long_series_shows_as_many_recent_bars_as_fit(qtbot):
    chart = BarChart(min_bar=20)
    qtbot.addWidget(chart)
    values = list(range(1, 101))
    chart.set_data(values, [str(v) for v in values])
    chart.resize(300, 160)
    narrow = len(chart.bar_rects())
    chart.resize(900, 160)
    wide = len(chart.bar_rects())
    assert 10 <= narrow < wide < 100  # a wider window loads more of the history
    assert all(rect.width() >= 20 for rect in chart.bar_rects())
    last = chart.bar_rects()[-1]
    assert chart.tip_at(last.center().x()) == "100: 100"  # the newest bar is always shown


def test_axis_labels_never_overlap_or_run_off_the_edge(qtbot):
    chart = BarChart(min_bar=18)
    qtbot.addWidget(chart)
    values = list(range(1, 121))
    chart.set_data(values, [f"Week of {v}" for v in values])
    metrics = chart.fontMetrics()
    for width in (300, 640, 1500):
        chart.resize(width, 160)
        boxes = chart.axis_labels()
        assert len(boxes) >= 2, width
        for (box, _, text), (after, _, _) in zip(boxes, boxes[1:], strict=False):
            end = box.left() + metrics.horizontalAdvance(text)
            assert end + BarChart.LABEL_GAP <= after.left() + 0.5, (width, text)
        last, _, text = boxes[-1]
        assert last.left() + metrics.horizontalAdvance(text) <= width

    short = BarChart()
    qtbot.addWidget(short)
    short.resize(600, 160)
    short.set_data([1, 2, 3], ["Sun", "Mon", "Tue"])
    assert [text for _, _, text in short.axis_labels()] == ["Sun", "Mon", "Tue"]  # all fit


def test_bars_can_carry_their_own_hover_text(qtbot):
    chart = BarChart()
    qtbot.addWidget(chart)
    chart.resize(200, 120)
    chart.set_data([1, 15], ["1", "2"], tips=["1: 1 show", "2: 15 shows"])
    assert chart.tip_at(chart.bar_rects()[1].center().x()) == "2: 15 shows"


def test_band_chart_hover_explains_the_band_under_the_mouse(qtbot):
    chart = BandChart()
    qtbot.addWidget(chart)
    chart.resize(320, 260)
    chart.set_bands(
        [Band(7.0, 7.5, 112, 6.42, (6.0, 7.0)), Band(8.5, 9.0, 2, 8.5, None)],
    )
    x, _ = chart.point(7.25, 6.42)
    assert chart.tip_at(x) == (
        "MAL 7.0 to 7.5: 112 shows. You average 6.42, and the middle half of your scores is 6 to 7."
    )
    x, _ = chart.point(8.75, 8.5)
    assert chart.tip_at(x) == "MAL 8.5 to 9.0: 2 shows. You average 8.50."


def test_genre_headings_say_what_the_numbers_show(qtbot):
    card = GenreCard()
    qtbot.addWidget(card)
    # Harsher than MAL everywhere: the "generous" end is only the closest to MAL.
    card.set_genres([lean("Music", 0.0), lean("Space", -0.1)], [lean("Sports", -2.6)], "")
    assert card.headings() == ["Closest to MAL", "Furthest below MAL"]
    card.set_genres([lean("Drama", 0.4)], [lean("Mecha", 0.1)], "")
    assert card.headings() == ["Furthest above MAL", "Lowest against MAL"]


def test_genre_scale_always_shows_mal_and_your_usual(qtbot):
    card = GenreCard()
    qtbot.addWidget(card)
    card.set_genres([lean("Drama", 0.4)], [lean("Mecha", 0.1)], "", usual=-0.8)
    assert card.scale == (-0.8, 0.4)  # the usual marker is in range, and so is zero
    assert card.usual == -0.8


def test_scale_labels_stay_inside_even_when_mal_is_at_the_edge(qtbot):
    # Every genre below MAL puts zero at the right edge; "MAL" must not be cut off.
    axis = LeanAxis(-2.6, 0.0, usual=-0.8)
    qtbot.addWidget(axis)
    axis.resize(300, 16)
    boxes = axis.label_boxes()
    assert [text for _, _, text in boxes] == ["MAL", "usual"]
    assert all(box.left() >= 0 and box.right() <= 300 for box, _, _ in boxes)
    mal_box, mal_align, _ = boxes[0]
    assert mal_align == Qt.AlignmentFlag.AlignRight  # so the text ends at the line


def test_two_columns_put_the_lists_side_by_side(qtbot):
    card = GenreCard(columns=2)
    qtbot.addWidget(card)
    card.set_genres([lean("Drama", 0.4)], [lean("Mecha", -1.0)], "", usual=-0.5)
    card.resize(800, 300)
    card.show()
    left, right = card.grids
    assert left.count() and right.count()
    assert left.geometry().right() < right.geometry().left()


def test_long_names_end_in_an_ellipsis(font):
    metrics = QFontMetrics(font)
    name = "A Boogie wit da Hoodie and Some Friends"
    short = elided(name, font, 120)
    assert short.endswith("…") and metrics.horizontalAdvance(short) <= 120
    assert elided("Sky", font, 120) == "Sky"

    two = elided_lines("That Time I Got Reincarnated as a Slime Season 3", font, 104, 2)
    lines = two.split("\n")
    assert len(lines) == 2 and lines[1].endswith("…")
    assert all(metrics.horizontalAdvance(line) <= 104 for line in lines)
    assert elided_lines("Monster", font, 104, 2) == "Monster"


def test_tooltips_show_api_text_as_written():
    from taste.desktop.widgets import plain_tip

    assert plain_tip("") == ""
    assert plain_tip("<b>Example</b> & co\nline two") == (
        "<qt>&lt;b&gt;Example&lt;/b&gt; &amp; co<br>line two</qt>"
    )


def test_sparse_labels_land_on_their_own_bars(qtbot):
    from taste.desktop.plots import BarChart

    chart = BarChart()
    qtbot.addWidget(chart)
    chart.resize(300, 200)
    labels = [f"{(i + 1) // 2}" if i % 2 == 1 else "" for i in range(20)]
    chart.set_data(list(range(1, 21)), labels)
    shown = [text for _, _, text in chart.axis_labels()]
    assert shown and all(shown) and shown[0] == "1"


def test_deleting_a_widget_mid_fade_warns_about_nothing(qtbot, monkeypatch):
    from PySide6.QtCore import QEventLoop, QTimer, qInstallMessageHandler
    from PySide6.QtWidgets import QWidget

    from taste.desktop import motion

    monkeypatch.setattr(motion, "override", "full")
    messages = []
    previous = qInstallMessageHandler(lambda mode, context, text: messages.append(text))
    try:
        parent = QWidget()
        qtbot.addWidget(parent)
        parent.show()
        children = [QWidget(parent) for _ in range(5)]
        for child in children:
            child.show()
        motion.fade_in(children)
        motion.fade_out(children[0], lambda: None)
        for child in children:
            child.deleteLater()
        loop = QEventLoop()
        QTimer.singleShot(500, loop.quit)
        loop.exec()
    finally:
        qInstallMessageHandler(previous)
    assert not [m for m in messages if "animation" in m]


def test_band_chart_speaks_any_crowd_and_scale(qtbot):
    chart = BandChart(crowd="Hardcover", top=5, noun="book", scores="stars")
    qtbot.addWidget(chart)
    chart.resize(320, 260)
    chart.set_bands([Band(3.75, 4.0, 9, 3.61, (3.0, 4.0)), Band(4.25, 4.5, 1, 4.5, None)])
    x, _ = chart.point(3.875, 3.61)
    assert chart.tip_at(x) == (
        "Hardcover 3.75 to 4.00: 9 books. You average 3.61, and the middle half of your "
        "stars is 3 to 4."
    )
    assert chart.y_ticks() == [1, 2, 3, 4, 5]
    chart.setMinimumHeight(140)  # as on the Books page
    chart.resize(320, 140)  # short: every other number, so they never touch
    assert chart.y_ticks() == [1, 3, 5]
    chart.resize(320, 260)
    assert chart.point(4.0, 5)[1] > chart.TOP  # 5 stars sits inside the plot, not cut off
    assert BandChart().y_ticks() == [2, 4, 6, 8, 10]


def test_checkboxes_fit_as_many_to_a_row_as_their_width_holds(qtbot):
    from taste.desktop.settings_page import ChoiceBoxes

    boxes = ChoiceBoxes(
        {"a": "TV", "b": "Movie", "c": "ONA", "d": "OVA", "e": "A much longer name"}
    )
    qtbot.addWidget(boxes)
    widths = [b.sizeHint().width() for b in boxes.boxes.values()]
    spacing = ChoiceBoxes.SPACING
    # Four across: the long name shares column 0 with TV, so column 0 is the long one.
    four = max(widths[0], widths[4]) + sum(widths[1:4]) + 3 * spacing
    assert boxes.fits(four) == 4
    assert boxes.fits(four - 1) < 4
    assert boxes.fits(1) == 1
    assert boxes.minimumSizeHint().width() == max(widths)  # one box wide, never a full row


def test_one_bar_leads_at_full_color(qtbot):
    """2.4: the highest finished bar (newest on a tie) is solid; the rest are soft."""
    chart = BarChart()
    qtbot.addWidget(chart)
    chart.resize(400, 200)
    chart.set_data([1, 5, 3, 5, 2], list("abcde"))
    assert chart.lead_bar() == 3
    chart.set_data([1, 5, 3, 9], list("abcd"), partial_last=True)
    assert chart.lead_bar() == 1  # a month still filling up never leads
    chart.set_data([0, 0], ["a", "b"])
    assert chart.lead_bar() == -1


def test_an_elided_label_shortens_and_keeps_the_whole_text(qtbot):
    """2.5: one line that gives up width; the whole text stays readable and returnable."""
    from taste.desktop.widgets import ElidedLabel

    text = "Make recaps for your past months? 49 months and 4 years, about a minute."
    tag = ElidedLabel(text, min_width=40)
    qtbot.addWidget(tag)
    tag.show()
    tag.resize(120, 20)
    assert tag.text() == text and tag.minimumSizeHint().width() == 40
    shown = QLabel.text(tag)
    assert shown != text and shown.endswith("…")
    assert text in tag.toolTip()  # the whole sentence on hover
    tag.setToolTip("MyAnimeList synced today")
    assert tag.toolTip() == "MyAnimeList synced today"  # a tooltip from outside wins
    tag.resize(900, 20)
    assert QLabel.text(tag) == text
