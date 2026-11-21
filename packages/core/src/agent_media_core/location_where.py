"""`media location where-was-i "<place or day>"` and the `location_history`
MCP tool: where the user has been, answered in words (maps proposal step 5,
docs/proposals/2026-11-06-maps.md).

The history itself never leaves the server (location_history.py, its own
`location.db`). This asks the server with the host's own token and turns its
answer into a sentence or two, so only the answer enters the chat, never the
points: "You were last at Southland on Tuesday 18 Nov, 1:40 to 3:15 pm."

A question naming a day ("today", "yesterday", "Tuesday", "18 Nov",
"2026-11-18") is that day's visits and distance; anything else is taken as
place words, and the newest visits to a place of that name come back.
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Callable

#: Words dropped from a question before it is read as a place.
_FILLER = {"when", "was", "i", "last", "at", "where", "the", "did", "go", "to", "were", "we", "my",
           "on", "in", "been", "have", "a", "an", "time", "visit", "visited", "?"}
_DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]

Call = Callable[[str, str], tuple[int, dict]]


def _call(method: str, path: str) -> tuple[int, dict]:
    from . import phone_ask

    return phone_ask._call(method, path)


def read_day(q: str, today: dt.date | None = None) -> dt.date | None:
    """The day a question names, or None when it names none."""
    today = today or dt.date.today()
    s = q.lower().strip().rstrip("?").strip()
    words = re.findall(r"[a-z0-9]+|\d{4}-\d{2}-\d{2}", s)
    if "today" in words:
        return today
    if "yesterday" in words:
        return today - dt.timedelta(days=1)
    m = re.search(r"\d{4}-\d{2}-\d{2}", s)
    if m:
        try:
            return dt.date.fromisoformat(m.group())
        except ValueError:
            return None
    for i, name in enumerate(_DAYS):
        if name in words or name[:3] in words:
            back = (today.weekday() - i) % 7
            if "last" in words and back == 0:
                back = 7
            return today - dt.timedelta(days=back)
    m = re.search(r"\b(\d{1,2})\s*(?:st|nd|rd|th)?\s+([a-z]{3})[a-z]*\b|\b([a-z]{3})[a-z]*\s+(\d{1,2})\b", s)
    if m:
        dnum, mon = (m.group(1), m.group(2)) if m.group(1) else (m.group(4), m.group(3))
        if mon in _MONTHS:
            month = _MONTHS.index(mon) + 1
            year = today.year if (month, int(dnum)) <= (today.month, today.day) else today.year - 1
            try:
                return dt.date(year, month, int(dnum))
            except ValueError:
                return None
    m = re.fullmatch(r".*?\b(\d{1,2})/(\d{1,2})\b.*", s)  # 18/11, day first
    if m:
        d, mo = int(m.group(1)), int(m.group(2))
        try:
            got = dt.date(today.year, mo, d)
        except ValueError:
            return None
        return got if got <= today else got.replace(year=today.year - 1)
    return None


def place_words(q: str) -> str:
    return " ".join(w for w in re.findall(r"[\w'&-]+", q.lower()) if w not in _FILLER)


def _clock(t: float) -> tuple[str, str]:
    d = dt.datetime.fromtimestamp(t)
    h = d.hour % 12 or 12
    return (f"{h}:{d.minute:02d}" if d.minute else f"{h}"), ("am" if d.hour < 12 else "pm")


def span(a: float, b: float, ongoing: bool = False) -> str:
    """"1:40 to 3:15 pm", "11:30 am to 1 pm", or "since 1:40 pm"."""
    ta, pa = _clock(a)
    if ongoing:
        return f"since {ta} {pa}"
    tb, pb = _clock(b)
    return f"{ta} to {tb} {pb}" if pa == pb else f"{ta} {pa} to {tb} {pb}"


def day_name(t: float | dt.date, today: dt.date | None = None) -> str:
    today = today or dt.date.today()
    d = t if isinstance(t, dt.date) else dt.datetime.fromtimestamp(t).date()
    if d == today:
        return "today"
    if d == today - dt.timedelta(days=1):
        return "yesterday"
    return f"{d.strftime('%A')} {d.day} {d.strftime('%b')}" + ("" if d.year == today.year else f" {d.year}")


def _on(when: str) -> str:
    return when if when in ("today", "yesterday") else f"on {when}"


def _place(v: dict) -> str:
    return v.get("place") or "a place with no name yet"


def _km(m: float) -> str:
    return f"{m / 1000:.1f} km" if m >= 1000 else f"{round(m)} m"


def _refused(code: int, obj: dict) -> str | None:
    if code == 200:
        return None
    if code in (401, 403):
        return "This host's token was refused by the server, so the location history could not be read."
    return f"The location history could not be read (the server said {code}: {obj.get('error') or 'no reason'})."


def answer(question: str, call: Call | None = None, today: dt.date | None = None) -> str:
    """Where the user has been, in words."""
    from urllib.parse import quote

    ask = call or _call
    today = today or dt.date.today()
    q = (question or "").strip()
    if not q:
        q = "today"
    try:
        d = read_day(q, today)
        if d is not None:
            code, obj = ask("GET", f"/map/history?day={d.isoformat()}")
            if (why := _refused(code, obj)):
                return why
            when = day_name(d, today)
            visits = obj.get("visits") or []
            if not visits and not obj.get("points"):
                return (f"Nothing is kept for {when}: \"Keep where I've been\" may have been off, "
                        "or that day is past the history's keep.")
            parts = [f"{_place(v)} {span(v['from'], v['to'], bool(v.get('ongoing')))}" for v in visits[:8]]
            lead = _on(when)
            out = f"{lead[0].upper()}{lead[1:]} you were at " + (", then ".join(parts) if parts else "no one place for long")
            if len(visits) > 8:
                out += f", and {len(visits) - 8} more places"
            if obj.get("distance_m"):
                out += f"; about {_km(obj['distance_m'])} on the move"
            return out + "."
        words = place_words(q)
        if not words:
            return "Say a place or a day: \"Southland\", \"yesterday\", \"Tuesday\"."
        code, obj = ask("GET", f"/map/history/visits?q={quote(words)}&limit=4")
        if (why := _refused(code, obj)):
            return why
        visits = obj.get("visits") or []
        if not visits:
            return f"No visit to a place called \"{words}\" is in the history kept."
        v = visits[0]
        out = (f"You were last at {_place(v)} {_on(day_name(v['from'], today))}, "
               f"{span(v['from'], v['to'], bool(v.get('ongoing')))}.")
        if len(visits) > 1:
            out += " Before that: " + "; ".join(
                f"{day_name(w['from'], today)}, {span(w['from'], w['to'])}" for w in visits[1:]) + "."
        return out
    except OSError as e:
        return f"The server could not be reached for the location history ({e})."
