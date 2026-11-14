"""One log of the music that plays around a spoken reply, and how it ended.

David, 14 Nov 2026: "Maybe we need to keep a log of Sing it out ... I'd like
more intelligence around song choice, including the backing music for
replies." Until now five choosers kept five memories (a reply's
`extras.bed`, music-sung.json, the store's likes, the reply tune's last
time, the radio's files), and that a song ended, was stopped or skipped, or
was turned down, was written nowhere
(docs/proposals/2026-11-14-music-around-speech.md, step one).

This is the one place: the store's `speech_music` table, one row for each
thing that played —

- ``bed``: the music under a long reply (music_bed.py), written by submit
  as it starts and again by a replay that plays one (music_bed_replay);
- ``sing_in``: the few seconds of a favourite's original before a long
  reply, written with its bed when the bed's mix carries one;
- ``sing_out``: the original after a long reply, written when the app
  reports it started (POST /speech/music-bed/song; music_sung.py keeps its
  own list beside this, unchanged);
- ``tune``: the reply's `[[tune: …]]` song, written when it is asked of a
  player (reply_tune.py).

Each row says when, which reply (`history_id`, `session`), which track (our
own ids: `track_key`, `artist`, `title`, `source`), `why` it was chosen and
how it was mixed (`mix`), and how it ended: `outcome`, `ended_at`, `pos_s`,
and `events`, small timestamped notes. A turn of the volume is one of those
notes and nothing more: it is noted and moves no level (David, 14 Nov 2026:
"Note it only").

Step one only records. Nothing reads this to choose music. Everything is
kept until a prune is asked for ("everything maybe with a pruning option":
`media music log --prune <age>`).

Writing is best-effort: `start`, `end`, `note` and `attach` never raise — a
log that cannot be written must never cost a bed, a song or a reply — and
say so at debug. `recent` and `prune` are asked for on purpose and do raise.
"""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Optional, Union

log = logging.getLogger(__name__)

#: What a row can be.
KINDS = ("bed", "sing_in", "sing_out", "tune")
#: How a row can end.
OUTCOMES = ("ended", "stopped", "skipped", "kept", "liked", "disliked", "replaced")
#: His word on a song. A plain ending reported after one (the kept song ran
#: to its end) closes the row and leaves the word standing.
VERDICTS = ("kept", "liked", "disliked")
#: Outcomes that leave the song playing: the row takes the outcome and stays
#: open (`ended_at` unset) until an ending is reported.
_PLAYS_ON = ("kept", "liked")
#: A second report of the same song this soon after it started is the same
#: playing (music_sung._SAME_S: the app reopened, a poll after a missed event).
SAME_S = 900.0
#: How far back a Sing it out looks for the bed it followed.
_BED_BACK_S = 2 * 3600.0
#: The parts of a bed's mix (music_bed_mix.params) worth keeping.
_MIX_KEYS = ("gain_db", "level_db", "breathe_db", "carve_db", "intro_ms", "sweep_hz",
             "carry_ms", "outro_ms", "xfade_ms", "kind")
#: The parts of Sing it in's and Sing it out's plan (music_bed_vocal).
_VOCAL_KEYS = ("aligned", "start_s", "len_ms", "fade_in_ms", "start_db", "out_ms", "bed_s",
               "pos_s", "end_s", "xfade_ms", "inst_s", "orig_s")

Match = Union[int, dict]


def _store(store=None):
    if store is not None:
        return store
    from .state import StateStore

    return StateStore()


def _row(store, id_or_match: Match, within_s: Optional[float] = None) -> Optional[dict]:
    """The row an id names, or the newest one a match fits (`{"kind",
    "track_key", "history_id", "session"}`, any of them)."""
    if isinstance(id_or_match, dict):
        since = None if within_s is None else time.time() - within_s
        return store.find_speech_music(id_or_match, since)
    if isinstance(id_or_match, bool) or not isinstance(id_or_match, int) or id_or_match <= 0:
        return None
    return store.speech_music_row(id_or_match)


# ---- writing (never raises) ---------------------------------------------------------------------

def start(kind: str, *, history_id: Optional[int] = None, session: Optional[str] = None,
          track_key: Optional[str] = None, artist: str = "", title: str = "",
          source: str = "", why: Optional[dict] = None, mix: Optional[dict] = None,
          started_at: Optional[float] = None, store=None) -> int:
    """Something began to play around a reply: its row's id, or 0 when it
    could not be written (or `kind` is not one of KINDS)."""
    try:
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {', '.join(KINDS)}")
        return _store(store).add_speech_music(
            kind, started_at=started_at, history_id=history_id, session=str(session or ""),
            track_key=str(track_key or ""), artist=str(artist or ""), title=str(title or ""),
            source=str(source or ""), why=why if isinstance(why, dict) else None,
            mix=mix if isinstance(mix, dict) else None)
    except Exception as e:  # noqa: BLE001 — the log is never worth the music
        log.debug("speech music: start not logged: %s", e)
        return 0


def end(id_or_match: Match, outcome: str, pos_s: Optional[float] = None, *,
        at: Optional[float] = None, store=None) -> int:
    """How a row ended: its id, or 0 when there is no such row or it could
    not be written. `id_or_match` is a row's id or a match (the newest row
    it fits). `kept` and `liked` set the outcome and leave the row open (the
    song plays on); the others close it (`ended_at`, `pos_s`), and only a
    verdict replaces a verdict already there. Every report is also added to
    the row's `events`, so nothing said about it is lost."""
    try:
        if outcome not in OUTCOMES:
            raise ValueError(f"outcome must be one of {', '.join(OUTCOMES)}")
        store = _store(store)
        row = _row(store, id_or_match)
        if row is None:
            return 0
        at = time.time() if at is None else float(at)
        cols: dict = {}
        if outcome in VERDICTS or row.get("outcome") not in VERDICTS:
            cols["outcome"] = outcome
        event = {"at": at, "what": outcome}
        if pos_s is not None:
            event["pos_s"] = round(max(0.0, float(pos_s)), 2)
        if outcome not in _PLAYS_ON and row.get("ended_at") is None:
            cols["ended_at"] = at
            if pos_s is not None:
                cols["pos_s"] = event["pos_s"]
        return row["id"] if store.update_speech_music(row["id"], event=event, **cols) else 0
    except Exception as e:  # noqa: BLE001
        log.debug("speech music: end not logged: %s", e)
        return 0


def note(id_or_match: Match, event: dict, *, at: Optional[float] = None, store=None) -> int:
    """Add a small note to a row's `events` (a volume change: `{"what":
    "volume", "level": 40}`), stamped `at`: its id, or 0. Only ever a note:
    nothing is moved by it."""
    try:
        if not isinstance(event, dict) or not event:
            raise ValueError("event must be a non-empty object")
        store = _store(store)
        row = _row(store, id_or_match)
        if row is None:
            return 0
        stamped = {"at": time.time() if at is None else float(at), **event}
        return row["id"] if store.update_speech_music(row["id"], event=stamped) else 0
    except Exception as e:  # noqa: BLE001
        log.debug("speech music: note not logged: %s", e)
        return 0


def attach(id_or_match: Match, *, history_id: Optional[int] = None,
           session: Optional[str] = None, store=None) -> int:
    """Say which reply a row belongs to, once that is known: a bed starts
    before its reply's history row exists. Its id, or 0."""
    try:
        cols = {k: v for k, v in (("history_id", history_id), ("session", session)) if v}
        if not cols:
            return 0
        store = _store(store)
        row = _row(store, id_or_match)
        if row is None:
            return 0
        return row["id"] if store.update_speech_music(row["id"], **cols) else 0
    except Exception as e:  # noqa: BLE001
        log.debug("speech music: reply not attached: %s", e)
        return 0


def find(id_or_match: Match, within_s: Optional[float] = None, store=None) -> Optional[dict]:
    """The row an id names or a match fits (the newest; with `within_s`,
    only one started that recently), or None. Never raises."""
    try:
        return _row(_store(store), id_or_match, within_s)
    except Exception as e:  # noqa: BLE001
        log.debug("speech music: find: %s", e)
        return None


# ---- reading and pruning (asked for: these raise) -----------------------------------------------

def recent(limit: int = 50, kind: Optional[str] = None, since: Optional[float] = None,
           store=None) -> list[dict]:
    """The log, newest first: every field of each row, `why`, `mix` and
    `events` parsed. `kind` one of KINDS; `since` an epoch time."""
    if kind is not None and kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}")
    return _store(store).list_speech_music(limit=limit, kind=kind, since=since)


def prune(older_than_s: float, store=None) -> int:
    """Delete what started more than `older_than_s` seconds ago; how many
    rows went. Kept until asked: here, or by the nightly state-gc once
    Settings → Storage sets an age (retention.py `music_log`)."""
    older_than_s = float(older_than_s)
    if older_than_s < 0:
        raise ValueError("an age is not negative")
    return _store(store).prune_speech_music(time.time() - older_than_s)


_AGE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([hdwmy])\s*$", re.I)
_AGE_S = {"h": 3600.0, "d": 86400.0, "w": 7 * 86400.0, "m": 30 * 86400.0, "y": 365 * 86400.0}


def age_s(text: str) -> float:
    """`90d`, `12w`, `6m`, `1y`, `36h` as seconds (a month is 30 days, a
    year 365). ValueError for anything else: a prune never guesses."""
    m = _AGE.match(str(text or ""))
    if not m:
        raise ValueError("an age is a number and h, d, w, m or y: 90d, 1y")
    return float(m.group(1)) * _AGE_S[m.group(2).lower()]


def with_titles(rows: list[dict], store=None) -> list[dict]:
    """`rows` with `thread_title`: the title the reply's history row kept
    (`extras.source_title`), None where there is no row or no title. A row
    without a `session` takes its reply's. Never raises."""
    out, seen = [], {}
    for r in rows:
        r = dict(r)
        r["thread_title"] = None
        hid = r.get("history_id")
        if hid:
            try:
                if hid not in seen:
                    h = _store(store).history_row(hid) or {}
                    seen[hid] = h.get("extras") if isinstance(h.get("extras"), dict) else {}
                ex = seen[hid]
                r["thread_title"] = str(ex.get("source_title") or "") or None
                if not r.get("session") and ex.get("source_session"):
                    r["session"] = str(ex["source_session"])
            except Exception as e:  # noqa: BLE001 — a row without its title
                log.debug("speech music: title: %s", e)
        out.append(r)
    return out


# ---- what the players' callers know -------------------------------------------------------------

def _favourite_key(path) -> str:
    """The favourite whose bed file is `path` (music_bed_favourites)."""
    try:
        from . import music_bed_favourites as fav

        name = Path(path).name
        for e in fav.load().get("tracks") or []:
            if e.get("file") == name:
                return str(e.get("key") or "")
    except Exception as e:  # noqa: BLE001
        log.debug("speech music: favourite key: %s", e)
    return ""


def track_of(plan, rec: Optional[dict] = None, mix: Optional[dict] = None) -> dict:
    """`track_key`, `artist`, `title` and `source` of a bed (music_bed.Plan)
    as it played (`rec`, music_bed_replay.record: a streaming plan that fell
    back is its generated stand-in). Our own ids only: the favourite's key,
    a library track's address, `yt:<id>` for a streaming one, and
    `generated:<mood>`."""
    source = str((rec or {}).get("source") or getattr(plan, "source", "") or "")
    style = str((rec or {}).get("style") or getattr(plan, "style", "") or "")
    credit = getattr(plan, "credit", None) or {}
    path = getattr(plan, "path", None)
    if source != getattr(plan, "source", ""):
        # Fell back as it started: what played is the stand-in, not the plan.
        credit, path = {}, None
    if source == "favourites":
        vocal = (mix or {}).get("after") or (mix or {}).get("before") or {}
        key = str(vocal.get("key") or "") or (_favourite_key(path) if path else "")
        return {"track_key": key or str(credit.get("original_id") or ""),
                "artist": str(credit.get("creator") or ""),
                "title": str(credit.get("original_title") or ""), "source": source}
    if source == "library":
        return {"track_key": str(credit.get("download") or credit.get("url") or "")
                or (Path(path).name if path else ""),
                "artist": str(credit.get("creator") or ""),
                "title": str(credit.get("title") or ""), "source": source}
    if source == "streaming":
        tracks = getattr(plan, "tracks", ()) or ()
        first = tracks[0] if tracks and isinstance(tracks[0], dict) else {}
        vid = str(first.get("id") or "")
        return {"track_key": f"yt:{vid}" if vid else "",
                "artist": str(first.get("artist") or credit.get("creator") or ""),
                "title": str(first.get("title") or credit.get("title") or ""),
                "source": "stream"}
    return {"track_key": f"generated:{style}" if style else "", "artist": "",
            "title": style, "source": source or "generated"}


def _vocal_mix(part: dict) -> dict:
    return {k: part[k] for k in _VOCAL_KEYS if k in part}


def bed_started(plan, rec: Optional[dict], mix: Optional[dict] = None, *,
                session: Optional[str] = None, history_id: Optional[int] = None,
                why: Optional[dict] = None, store=None) -> dict:
    """A bed began under a reading: its row, and Sing it in's when the bed's
    mix carries one (`before`: the phone plays it ahead of the first word).
    `rec` is what music_bed_replay.record kept of it (None: nothing played,
    nothing written); `mix` what its player was handed (music_bed.last_mix).
    Returns `{"bed": id, "sing_in": id}` with the rows written. Never raises."""
    out: dict = {}
    try:
        if not rec:
            return out
        track = track_of(plan, rec, mix)
        chosen = {"source": getattr(plan, "source", ""), "style": getattr(plan, "style", ""),
                  "for": rec.get("for") or "reply"}
        if rec.get("source") != chosen["source"]:
            chosen["fell_back_to"] = rec.get("style") or ""
        chosen.update(why or {})
        mixed = {k: mix[k] for k in _MIX_KEYS if k in mix} if mix else {}
        if mix:
            mixed.update(outro=bool(mix.get("outro")), sing_in=bool(mix.get("before")),
                         sing_out=bool(mix.get("after")))
        now = time.time()
        bed = start("bed", history_id=history_id, session=session, why=chosen, mix=mixed,
                    started_at=now, store=store, **track)
        if bed:
            out["bed"] = bed
        before = (mix or {}).get("before")
        if isinstance(before, dict):
            sung = start("sing_in", history_id=history_id, session=session,
                         why={"with": "bed", "bed_id": bed or None},
                         mix=_vocal_mix(before), started_at=now, store=store, **track)
            if sung:
                out["sing_in"] = sung
    except Exception as e:  # noqa: BLE001
        log.debug("speech music: bed not logged: %s", e)
    return out


def bed_ended(ids: Optional[dict], cut: bool = False, store=None) -> None:
    """The reading a bed was under is over (`ids` from `bed_started`): the
    bed ended with it, `stopped` when the listener cut the reading short,
    and so did a Sing it in still open. How far in is the time since it
    started (a bed loops, so not a place in the track). Never raises."""
    try:
        for kind in ("bed", "sing_in"):
            row = find(int((ids or {}).get(kind) or 0), store=store)
            if row is None or row.get("ended_at") is not None:
                continue
            now = time.time()
            end(row["id"], "stopped" if cut else "ended",
                None if kind == "sing_in" else now - float(row["started_at"]),
                at=now, store=store)
    except Exception as e:  # noqa: BLE001
        log.debug("speech music: bed's end not logged: %s", e)


def reply_known(ids: Optional[dict], history_id: Optional[int],
                session: Optional[str] = None, store=None) -> None:
    """The reply's history row now exists: put it on the rows written while
    it played (`ids` from `bed_started`), and on a Sing it out of the same
    favourite the app reported before the row was there. Never raises."""
    try:
        if not ids or not history_id:
            return
        for kind in ("bed", "sing_in"):
            if ids.get(kind):
                attach(int(ids[kind]), history_id=history_id, session=session, store=store)
        bed = find(int(ids.get("bed") or 0), store=store) or {}
        if bed.get("track_key") and bed.get("source") == "favourites":
            sung = find({"kind": "sing_out", "track_key": bed["track_key"]}, store=store)
            if (sung and not sung.get("history_id")
                    and float(sung["started_at"]) >= float(bed["started_at"])):
                attach(sung["id"], history_id=history_id,
                       session=None if sung.get("session") else session, store=store)
    except Exception as e:  # noqa: BLE001
        log.debug("speech music: reply not attached: %s", e)


def sung_out(key: str, title: str = "", artist: str = "", session: Optional[str] = None,
             store=None) -> int:
    """The app reports favourite `key` began singing out: its row. A second
    report within SAME_S of its start is the same singing (as music_sung
    has it) and gives the same row. The reply is the one whose bed it was:
    the newest bed row of the same favourite. 0 when not written."""
    try:
        store = _store(store)
        same = _row(store, {"kind": "sing_out", "track_key": key}, SAME_S)
        if same is not None:
            return same["id"]
        bed = _row(store, {"kind": "bed", "track_key": key}, _BED_BACK_S) or {}
        return start("sing_out", history_id=bed.get("history_id"),
                     session=session or bed.get("session"), track_key=key,
                     artist=artist or bed.get("artist") or "",
                     title=title or bed.get("title") or "", source="favourites",
                     why={"after": "bed", "bed_id": bed.get("id")}, store=store)
    except Exception as e:  # noqa: BLE001
        log.debug("speech music: sing it out not logged: %s", e)
        return 0
