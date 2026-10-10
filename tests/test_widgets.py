"""The painted charts and small helpers in taste.desktop.widgets, offscreen."""

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontMetrics

from taste.charts import Band
from taste.desktop.widgets import (
    BandChart,
    BarChart,
    GenreCard,
    LeanAxis,
    elided,
    elided_lines,
    nice_ticks,
    small_font,
)
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
