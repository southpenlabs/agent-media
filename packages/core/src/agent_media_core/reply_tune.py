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

**When it does not.** While the switch is off (below); in a thinking
readout; within the cooldown (``MEDIA_REPLY_TUNE_COOLDOWN_MIN``, default 30
minutes, across the whole install); while a radio station is on; while the
route's player is already playing. It plays after the speech, never under
it (Developer Policy III.7: no overlapping Spotify with other audio).

**The switch and the start level** (David, 4 Nov 2026: a song he was not
already listening to can take him away from his thoughts on the reply, so
it should be his to switch on and start quiet — "put the sing it out
feature as an option on the full size media player"; "Use the speech
versus music slider for the volume adjustment").

- ``tune`` (bool, "A tune after some replies"): one setting among the music
  bed's (``GET /speech/music-bed`` ``settings``, ``settings()`` here),
  listed only while a route is on, so an app without one never offers it;
  the app puts it under Sing it out on the full player and in Settings →
  Music. Off by default, whatever the route; ``MEDIA_REPLY_TUNE_ON=1``
  turns it on for a host with no Settings. Stored in ``music-bed.json``
  with the other switches.
- The start level is the backing music level's (``level()``): the slider
  that sets how loud music sits under the voice, and how far his own music
  drops under a reply, also says how loud the song starts — its dB against
  the voice as a share of full volume (a fifth at the default -14
  dB, two thirds at -4, a twentieth at the bottom). No slider of its own.
  The hand-off route sets the hand-off player's volume before it asks for
  the song: the phone turns its media volume to that share and puts it
  back before the next reply speaks, or when the song is over or stopped
  (HandoffMusic.java). The Spotify route sets the device's volume with the
  Web API (an official call: no term bars it).
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
#: The settings an app renders among the music bed's (settings()).
SETTING_IDS = ("tune",)
ON_LABEL = "A tune after some replies"
ON_HINT = ("Rarely, after a reply that earns one, the agent names a song that fits it, "
           "and your music app plays it once the reply has been read. At most one every "
           "half hour, never over music you are playing. It starts at the backing music "
           "level and your volume keys take it from there.")
#: The song never starts below this share of full volume, whatever the slider says.
MIN_LEVEL = 5


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


def _stored(name: str):
    from . import music_bed
    return music_bed._stored().get(name)


def on() -> bool:
    """Whether a tune may play: Settings' switch, else MEDIA_REPLY_TUNE_ON,
    else off."""
    v = _stored("tune")
    if isinstance(v, bool):
        return v
    return (os.environ.get("MEDIA_REPLY_TUNE_ON") or "").strip().lower() in ("1", "on", "true", "yes")


def level() -> int:
    """How loud the song starts, percent of full: the backing music level
    (music_bed.gain_db, the slider on the full player and in Settings →
    Music) as shown against the voice, 10 ** (dB / 20); never under
    MIN_LEVEL, never over 100. Never raises (MIN_LEVEL on any failure)."""
    try:
        from . import music_bed
        db = music_bed.shown_gain(music_bed.gain_db())
        return int(round(min(100.0, max(float(MIN_LEVEL), 100.0 * 10 ** (db / 20.0)))))
    except Exception:  # noqa: BLE001 — a level must never cost a reply
        return MIN_LEVEL


def settings() -> list[dict]:
    """The switch as GET /speech/music-bed lists it; none without a route,
    so an app that cannot play a tune never offers one."""
    if route() is None:
        return []
    return [{"id": "tune", "kind": "bool", "label": ON_LABEL, "hint": ON_HINT,
             "value": on(), "set": isinstance(_stored("tune"), bool)}]


def check_setting(name: str, value) -> None:
    """ValueError unless `value` is right for `name` (null forgets it)."""
    if name != "tune":
        raise ValueError(f"no music bed setting {name!r}")
    if value is not None and not isinstance(value, bool):
        raise ValueError("tune must be true, false or null")


def set_stored(name: str, value) -> None:
    """Save one of SETTING_IDS in music-bed.json (null forgets it)."""
    from . import music_bed
    check_setting(name, value)
    d = music_bed._stored()
    if value is None:
        d.pop(name, None)
    else:
        d[name] = bool(value)
    music_bed._store(d)


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
    if not on():
        return _skip("off (the switch)", line)
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
             "level": level(),
             "session": md.get("session_id") or md.get("session") or None}
    # A tune is a start like any other (play_gate): in a treatment it is held
    # and offered back ("Play now"), never played later by itself.
    from . import play_gate
    with play_gate.request(session=str(entry["session"] or ""), title=line,
                           source="reply-tune"):
        try:
            play_gate.check(TUNE_PREFIX + line, target=r, kind="tune", title=line)
            why, found = _play_route(r, line, artist, title, entry["level"])
        except play_gate.PlayHeld as e:
            return _skip(f"held: {e.record.get('reason')}", line)
    if found:
        entry["track"] = found
    if why:
        return _skip(why, line)
    _record(entry)
    _log(line, artist, title, r, entry["level"], row=row, session=entry["session"])
    log.info("reply tune %r played on %s", line, r)
    return None


def _log(line: str, artist: str, title: str, r: str, start_level: int, *,
         row: Optional[int] = None, session=None, held: bool = False) -> None:
    """A tune asked of a player goes in the log of music around speech
    (speech_music; David, 14 Nov 2026: "Maybe we need to keep a log of Sing
    it out" — until now only the last tune's time was kept, for the
    cooldown). The track is named by the agent's own line and our id for it
    (`_song_id`, the hand-off entry's `id`, which the app reports its ending
    by), on either route: nothing Spotify answered is kept here. Never
    raises (speech_music.start)."""
    from . import speech_music
    speech_music.start("tune", history_id=row or None, session=session or None,
                       track_key=_song_id(line), artist=artist, title=title, source=r,
                       why={"asked": "held" if held else "reply", "line": line},
                       mix={"level": start_level})


#: A held tune's uri in play_gate's list: the line, asked of the route again.
TUNE_PREFIX = "tune:"


def _play_route(r: str, line: str, artist: str, title: str,
                start_level: int) -> tuple[Optional[str], Optional[dict]]:
    if r == "handoff":
        return _handoff(line, artist, title, start_level), None
    return _spotify(artist, title, start_level)


def play_now(line: str) -> Optional[str]:
    """Play a held tune now, on purpose (play_gate.play_now): the route as
    a reply would, without the switch, cooldown or the gate. None when it was
    asked of a player, else why not."""
    r = route()
    if r is None:
        return "no tune route here (MEDIA_REPLY_TUNE)"
    artist, title = split(line)
    if not title:
        return "no title"
    from . import play_gate
    with play_gate.request(force=True):
        why, _ = _play_route(r, line, artist, title, level())
    if why is None:
        _log(line, artist, title, r, level(), held=True)
    return why


def _handoff(line: str, artist: str, title: str, start_level: int = 100) -> Optional[str]:
    from . import radio_io

    why = radio_io.handoff_ready()
    if why:
        return why
    player = radio_io.HandoffPlayer()
    props = player.props() or {}
    if props and not props.get("idle-active") and not props.get("pause"):
        return "the hand-off player is playing"
    # `tune`: the phone shows it as the reply's song, with Stop and the
    # level slider under the reply and in the mini player (HandoffMusic).
    song = {"id": _song_id(line), "q": f"{artist} - {title}" if artist else title, "tune": True}
    # Quiet from the first note: the level goes before the ask, and the
    # phone puts its volume back when the song is over (HandoffMusic.java).
    player.volume(start_level)
    if not player.send(song, replace=True):
        return "the hand-off player did not take it"
    player.label(song)
    return None


def _spotify(artist: str, title: str, start_level: int = 100) -> tuple[Optional[str], Optional[dict]]:
    from . import spotify_remote as sr

    try:
        if sr.is_playing():
            return "Spotify is playing", None
        found = sr.search_track(artist, title)
        if not found or not found.get("uri"):
            return "Spotify has no such song", None
        device = _device(sr)
        try:
            # A device that does not take a volume (some Connect speakers)
            # says so; the song still plays, at the device's own level.
            sr.set_volume(start_level, device)
        except sr.RemoteError as e:
            log.info("reply tune: Spotify volume not set: %s", e)
        sr.play(found["uri"], device)
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
