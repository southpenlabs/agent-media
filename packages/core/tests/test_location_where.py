"""`media location where-was-i` (location_where.py): days read from a question,
and the server's answer put into words, with only words coming back."""
from __future__ import annotations

import datetime as dt
import time

from agent_media_core import location_where as lw

TODAY = dt.date(2026, 11, 22)  # a Sunday


def _at(day: dt.date, h: int, m: int = 0) -> float:
    return time.mktime(dt.datetime(day.year, day.month, day.day, h, m).timetuple())


def test_reading_a_day():
    assert lw.read_day("today", TODAY) == TODAY
    assert lw.read_day("where was I yesterday?", TODAY) == dt.date(2026, 11, 21)
    assert lw.read_day("Tuesday", TODAY) == dt.date(2026, 11, 17)
    assert lw.read_day("on sunday", TODAY) == TODAY
    assert lw.read_day("last sunday", TODAY) == dt.date(2026, 11, 15)
    assert lw.read_day("18 Nov", TODAY) == dt.date(2026, 11, 18)
    assert lw.read_day("december 3rd", TODAY) is None or lw.read_day("dec 3", TODAY) == dt.date(2025, 12, 3)
    assert lw.read_day("2026-11-01", TODAY) == dt.date(2026, 11, 1)
    assert lw.read_day("18/11", TODAY) == dt.date(2026, 11, 18)
    assert lw.read_day("Southland", TODAY) is None
    assert lw.place_words("When was I last at Southland?") == "southland"


def test_spans():
    d = TODAY
    assert lw.span(_at(d, 13, 40), _at(d, 15, 15)) == "1:40 to 3:15 pm"
    assert lw.span(_at(d, 11, 30), _at(d, 13)) == "11:30 am to 1 pm"
    assert lw.span(_at(d, 13, 40), 0, ongoing=True) == "since 1:40 pm"


def test_a_place_answer():
    tue = dt.date(2026, 11, 17)
    asked = []

    def call(method, path):
        asked.append(path)
        return 200, {"visits": [{"place": "Southland", "from": _at(tue, 13, 40), "to": _at(tue, 15, 15)},
                                {"place": "Southland", "from": _at(dt.date(2026, 11, 2), 10), "to": _at(dt.date(2026, 11, 2), 11)}]}

    got = lw.answer("when was I last at Southland", call, TODAY)
    assert asked == ["/map/history/visits?q=southland&limit=4"]
    assert got.startswith("You were last at Southland on Tuesday 17 Nov, 1:40 to 3:15 pm.")
    assert "Monday 2 Nov, 10 to 11 am" in got
    assert lw.answer("Bunnings", lambda m, p: (200, {"visits": []}), TODAY) == \
        'No visit to a place called "bunnings" is in the history kept.'


def test_a_day_answer_and_refusals():
    y = dt.date(2026, 11, 21)

    def call(method, path):
        assert path == "/map/history?day=2026-11-21"
        return 200, {"points": [{}], "distance_m": 18400,
                     "visits": [{"place": "Home", "from": _at(y, 8), "to": _at(y, 9, 30)},
                                {"place": None, "from": _at(y, 12), "to": _at(y, 12, 20)}]}

    got = lw.answer("yesterday", call, TODAY)
    assert got == ("Yesterday you were at Home 8 to 9:30 am, then a place with no name yet 12 to 12:20 pm; "
                   "about 18.4 km on the move.")
    assert "Nothing is kept for today" in lw.answer("", lambda m, p: (200, {"points": [], "visits": []}), TODAY)
    assert "refused" in lw.answer("today", lambda m, p: (403, {}), TODAY)

    def down(m, p):
        raise OSError("connection refused")

    assert "could not be reached" in lw.answer("Southland", down, TODAY)
