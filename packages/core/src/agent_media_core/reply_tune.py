"""A song after the reply, picked for what it said.

docs/proposals/2026-11-03-reply-tune.md (David, 3 Nov 2026: "Can we play a
tune on Spotify after a reply chosen by the reply's relevance? Is that
legal in a distributable package?"). The agent ends a reply with
``[[tune: Artist - Title]]`` (intake/_text.extract_tune); once the reply has
been said (submit.submit_event), the song is asked of a player here.

**Who picks.** The agent, from what it knows of music. Nothing from a music
service goes back to a model: Spotify's Developer Terms (v10, IV.2.1)
forbid ingesting Spotify Content, which includes its metadata and search
results, into an AI model.

**Where it plays.** ``MEDIA_REPLY_TUNE`` names the route; unset or ``off``,
nothing plays:

- ``handoff``: the listener's own music app on the phone, asked by name
  (radio_io.HandoffPlayer; needs MEDIA_RADIO_HANDOFF_ENDPOINT). No music
  service's developer platform is involved: the listener's app plays a
  search they could have typed. The product's route.
- ``spotify``: Spotify's Web API plays the top hit on the listener's
  Spotify device (spotify_remote; MEDIA_REPLY_TUNE_DEVICE names one, else
  the active one). A personal-use method (personal_use
  ``spotify-reply-tune``): Spotify's Developer Policy III.3 (no voice
  assistant controlling Spotify) and III.5 (no product integrated with
  another service's content) leave an assistant doing this in doubt.

**When it does not.** In a thinking readout; within the cooldown
(``MEDIA_REPLY_TUNE_COOLDOWN_MIN``, default 30 minutes, across the whole
install); while a radio station is on; while the route's player is already
playing. It plays after the speech, never under it (Developer Policy III.7:
no overlapping Spotify with other audio).
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import time
from typing import Optional

from ._paths import state_dir

log = logging.getLogger(__name__)

ROUTES = ("handoff", "spotify")
DEFAULT_COOLDOWN_MIN = 30.0


def route() -> Optional[str]:
    """The route MEDIA_REPLY_TUNE names, when it may run here, else None."""
    r = (os.environ.get("MEDIA_REPLY_TUNE") or "").strip().lower()
    if r not in ROUTES:
        return None
    if r == "spotify":
        from . import personal_use
        if not personal_use.allowed("spotify-reply-tune"):
            return None
    return r


def cooldown_s() -> float:
    try:
        return max(0.0, float(os.environ.get("MEDIA_REPLY_TUNE_COOLDOWN_MIN") or DEFAULT_COOLDOWN_MIN)) * 60
    except ValueError:
        return DEFAULT_COOLDOWN_MIN * 60


def _state_path():
    return state_dir() / "reply-tune.json"


def last() -> dict:
    """The last tune played: ``{at, line, route, row, …}``, or {}."""
    try:
        return json.loads(_state_path().read_text())
    except (OSError, ValueError):
        return {}


def _record(entry: dict) -> None:
    p = _state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(entry))
    tmp.replace(p)


def split(line: str) -> tuple[str, str]:
    """``(artist, title)`` of a song line, "Artist - Title" (en and em dashes
    too); a line without one is all title."""
    for sep in (" - ", " – ", " — "):
        if sep in line:
            a, _, t = line.partition(sep)
            return a.strip(), t.strip()
    return "", line.strip()


def _song_id(line: str) -> str:
    """An 11-character id for the hand-off queue entry (radio_io.vid_of
    reads entries by YouTube's id shape)."""
    return base64.urlsafe_b64encode(hashlib.sha256(line.encode()).digest()).decode()[:11]


def _skip(why: str, line: str) -> Optional[str]:
    log.info("reply tune %r not played: %s", line, why)
    return why


def after_reply(event, row: int) -> Optional[str]:
    """Play the reply's tune (``event.metadata["tune"]``) on the route.
    None when it was asked of a player, else why not. Never raises: a tune
    is never worth failing a reply over."""
    try:
        return _after_reply(event, row)
    except Exception as e:  # noqa: BLE001
        log.warning("reply tune failed: %s", e)
        return f"failed: {e}"


def _after_reply(event, row: int) -> Optional[str]:
    md = event.metadata or {}
    line = str(md.get("tune") or "").strip()
    if not line:
        return "no tune"
    if md.get("thinking"):
        return _skip("a thinking readout", line)
    r = route()
    if r is None:
        return _skip("off (MEDIA_REPLY_TUNE)", line)
    prev = last()
    if prev.get("at") and time.time() - float(prev["at"]) < cooldown_s():
        return _skip("within the cooldown", line)
    from . import radio
    if radio.is_on():
        return _skip("a radio station is on", line)
    artist, title = split(line)
    if not title:
        return _skip("no title", line)
    entry = {"at": time.time(), "line": line, "route": r, "row": row,
             "session": md.get("session_id") or md.get("session") or None}
    if r == "handoff":
        why = _handoff(line, artist, title)
    else:
        why, found = _spotify(artist, title)
        if found:
            entry["track"] = found
    if why:
        return _skip(why, line)
    _record(entry)
    log.info("reply tune %r played on %s", line, r)
    return None


def _handoff(line: str, artist: str, title: str) -> Optional[str]:
    from . import radio_io

    why = radio_io.handoff_ready()
    if why:
        return why
    player = radio_io.HandoffPlayer()
    props = player.props() or {}
    if props and not props.get("idle-active") and not props.get("pause"):
        return "the hand-off player is playing"
    song = {"id": _song_id(line), "q": f"{artist} - {title}" if artist else title}
    if not player.send(song, replace=True):
        return "the hand-off player did not take it"
    player.label(song)
    return None


def _spotify(artist: str, title: str) -> tuple[Optional[str], Optional[dict]]:
    from . import spotify_remote as sr

    try:
        if sr.is_playing():
            return "Spotify is playing", None
        found = sr.search_track(artist, title)
        if not found or not found.get("uri"):
            return "Spotify has no such song", None
        sr.play(found["uri"], _device(sr))
        return None, found
    except sr.RemoteError as e:
        return f"Spotify: {e.code}: {e}", None


def _device(sr) -> str:
    """MEDIA_REPLY_TUNE_DEVICE as a device id: matched by id, then by name
    (case aside). "" leaves the choice to spotify_remote.play."""
    want = (os.environ.get("MEDIA_REPLY_TUNE_DEVICE") or "").strip()
    if not want:
        return ""
    for d in sr.devices():
        if d["id"] == want or d["name"].lower() == want.lower():
            return d["id"]
    return ""
