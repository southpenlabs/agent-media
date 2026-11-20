"""Radio filters: rules that hold for the whole station.

David, 20 Nov 2026: "Can we set parameters for radio?... like acoustic
versions only or something like that?" (agent-mail #1104, from the app).
The mood (music_mood.py) says what kind of music; a filter says which
recordings: acoustic only, instrumental only, no live recordings, no
remixes, no covers. Any number can be on at once. Server-wide, like the
mood: ``<state_dir>/radio-filters.json`` (`{"on": [ids]}`), none on by
default.

**Two kinds.** A *wanting* filter (acoustic, instrumental) asks for
something a Mix cannot give, so only the DJ can keep to it; a *leaving-out*
filter (no live, no remixes, no covers) can be kept by any station, by the
title.

**How a station keeps to them.**

- The DJ (radio_dj.brief) is told each filter on, as a rule every pick
  must keep, and to name a wanted version in its line ("Artist - Song
  (Acoustic)"), so the YouTube search finds that take.
- Every song a station takes (radio._merge, DJ's or Mix's) is checked by
  its title (`allows`): a leaving-out filter drops a title that says it is
  that kind (music_versions' words, the song's own name aside, so "Live
  Forever" is not live); acoustic needs a title that says acoustic,
  stripped, unplugged or piano version. Instrumental has no title check:
  most instrumentals do not say so, and the DJ is trusted with it.
- **Starving.** When a batch of picks had songs but none got past a
  wanting filter and the station has nothing left to play, the batch is
  taken with only the leaving-out filters checked, and the station's
  ``filters_note`` says so ("Few acoustic versions found; playing the
  closest picks."), which GET /music carries as ``radio_filters_note``. The
  next batch that gets through clears it.

**Changing one** (`set_filter`, which the CLI and ``POST /music`` share):

- A DJ station on: its songs to come are dropped and the DJ asked again
  (radio.repick), as for a change of mood.
- A Mix station on: a wanting filter turned on hands it to the DJ
  (radio.start_dj, the song playing plays on); a leaving-out one drops the
  songs to come that it would not have taken.
- No station, music playing, a wanting filter turned on: the DJ starts on
  the default player, as a mood does. Otherwise only stored.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Callable, NamedTuple, Optional

from ._paths import state_dir

log = logging.getLogger(__name__)


class Filter(NamedTuple):
    id: str
    name: str
    #: What the DJ is told.
    rule: str
    #: True: asks for something only the DJ can find (a Mix cannot keep it).
    wants: bool
    #: music_versions kinds a title may not be (leaving-out filters).
    not_kinds: tuple[str, ...] = ()


FILTERS: tuple[Filter, ...] = (
    Filter("acoustic", "Acoustic only",
           "Acoustic versions only: an acoustic, unplugged or stripped-back take of each "
           "song (a song first released acoustic counts). Name it in the line, e.g. "
           "'Artist - Song (Acoustic)'.", True),
    Filter("instrumental", "Instrumental only",
           "Instrumental music only: no vocals at all. An instrumental version of a song "
           "is fine; name it in the line, e.g. 'Artist - Song (Instrumental)'.", True),
    Filter("no_live", "No live recordings",
           "No live recordings: studio versions only, never a concert or live session.",
           False, ("live",)),
    Filter("no_remix", "No remixes",
           "No remixes, edits, sped-up or slowed versions: the original mix only.",
           False, ("remix", "slowed")),
    Filter("no_cover", "No covers",
           "No covers: each song by the artist who made it.", False, ("cover", "karaoke")),
)

BY_ID = {f.id: f for f in FILTERS}
IDS = tuple(f.id for f in FILTERS)

#: Title words that make a recording acoustic, for "Acoustic only".
_ACOUSTIC = ("acoustic", "unplugged", "stripped", "piano version")


def _path() -> Path:
    return state_dir() / "radio-filters.json"


def on() -> list[str]:
    """The ids switched on, in FILTERS' order."""
    try:
        d = json.loads(_path().read_text())
    except (OSError, ValueError):
        return []
    have = set(d.get("on") or []) if isinstance(d, dict) else set()
    return [i for i in IDS if i in have]


def _store(ids: list[str]) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{os.getpid()}")
    tmp.write_text(json.dumps({"on": [i for i in IDS if i in set(ids)]}))
    os.replace(tmp, p)


def shown() -> list[dict]:
    """For GET /music `radio_filters`: every filter, in order, with `on`."""
    have = set(on())
    return [{"id": f.id, "name": f.name, "on": f.id in have} for f in FILTERS]


def dj_rules(ids: Optional[list[str]] = None) -> list[str]:
    """What the DJ's brief lists as rules; empty with none on."""
    return [BY_ID[i].rule for i in (on() if ids is None else ids)]


def wanting(ids: Optional[list[str]] = None) -> list[str]:
    return [i for i in (on() if ids is None else ids) if BY_ID[i].wants]


# ---- by the title ------------------------------------------------------------------

def _song_of(song: dict) -> str:
    """The song's own name, when the DJ's line gives it ("Artist - Song")."""
    q = song.get("q") or ""
    return q.partition(" - ")[2].strip() if " - " in q else ""


def _kinds(title: str, song: str) -> set[str]:
    """Every music_versions kind the title says, the song's own name aside.
    No cover-by-channel guess: a station's songs have no artist to go by."""
    from .music_versions import KINDS, fold

    t = f" {fold(title)} "
    raw = (title or "").lower()
    if fold(song):
        t = t.replace(f" {fold(song)} ", " ", 1)
        raw = raw.replace(song.lower(), " ", 1)
    out = set()
    for kind, words in KINDS:
        for w in words:
            if (f" {fold(w)} " in t) if w.isalnum() or " " in w else (w in raw):
                out.add(kind)
                break
    if "live" in out and not song and not _live_marked(raw):
        # "Live" with no song name to set aside counts only where a version
        # is named: in brackets, or "live at/in/from/…".
        out.discard("live")
    return out


def _live_marked(raw: str) -> bool:
    return bool(re.search(r"[\(\[][^\)\]]*\blive\b|\blive\s+(at|in|from|on|@|session|version|performance)\b"
                          r"|\bconcert\b|\bunplugged\b|\bin session\b|-\s*live\s*$", raw))


def allows(song: dict, ids: Optional[list[str]] = None, wanting_too: bool = True) -> bool:
    """Whether a station may take this song under the filters on (`ids`,
    default the stored ones). `wanting_too` False checks only the
    leaving-out filters (a starving station's relaxed check)."""
    ids = on() if ids is None else ids
    if not ids:
        return True
    title = song.get("title") or ""
    kinds = _kinds(title, _song_of(song))
    for i in ids:
        f = BY_ID[i]
        if kinds & set(f.not_kinds):
            if i == "no_live" and "acoustic" in ids and "unplugged" in title.lower():
                continue   # an unplugged session is what "acoustic" asked for
            return False
        if wanting_too and i == "acoustic":
            low = title.lower()
            if not any(w in low for w in _ACOUSTIC):
                return False
    return True


def starved_note(ids: Optional[list[str]] = None) -> str:
    names = {"acoustic": "acoustic versions", "instrumental": "instrumentals"}
    want = [names[i] for i in wanting(ids) if i in names]
    what = " or ".join(want) if want else "songs that fit"
    return f"Few {what} found; playing the closest picks."


# ---- changing them -----------------------------------------------------------------

def _follow(fid: str, now_on: bool, playing: Callable[[], bool]) -> Optional[str]:
    """The station after a filter changed: "repicked", "started", "pruned" or None."""
    from . import music_mood, radio

    st = radio.read()
    try:
        if st.get("on") and st.get("kind") == "dj":
            return "repicked" if radio.repick() else None
        wants = BY_ID[fid].wants and now_on
        if st.get("on"):
            if wants and radio.available():
                radio.start_dj(st.get("where") or music_mood._default_where())
                return "started"
            return "pruned" if radio.prune() else None
        if wants and radio.available() and playing():
            radio.start_dj(music_mood._default_where())
            return "started"
    except (ValueError, RuntimeError) as e:
        log.info("radio-filters: no DJ station for %s: %s", fid, e)
    return None


def set_filter(fid, want_on: bool, playing: Optional[Callable[[], bool]] = None) -> dict:
    """Switch one filter on or off and bring the station in line (module
    docstring). ``{"id", "on", "changed", "station", "filters"}``;
    ValueError for an unknown id."""
    if not isinstance(fid, str) or fid.strip().lower() not in BY_ID:
        raise ValueError(f"filter must be one of {', '.join(IDS)}")
    fid = fid.strip().lower()
    before = on()
    after = [i for i in before if i != fid] + ([fid] if want_on else [])
    _store(after)
    if (fid in before) == bool(want_on):
        return {"id": fid, "on": bool(want_on), "changed": False, "station": None, "filters": shown()}
    from . import music_mood

    station = _follow(fid, bool(want_on), playing or music_mood._playing_now)
    return {"id": fid, "on": bool(want_on), "changed": True, "station": station, "filters": shown()}
