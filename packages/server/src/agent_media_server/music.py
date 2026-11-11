"""What is playing, and its controls: the app's Media tab.

server-contract.md §6.9a. Step 1 of docs/proposals/2026-09-28-music-tab.md
(David, 28 Sep 2026: "Let's build the Media tab UI").

  GET  /music                → {"ok", "now": {...}, "chapters": [...], "where": {...}}
  GET  /music/recent[?kind=music|book|podcast][&list=recent|liked|most|…][&q=words]
                             → {"ok", "kind", "list", "items": [{"uri", "title", "at", "session", …}]}
  GET  /music/channel?name=…|url=…|vid=…
                             → {"ok", "channel": {"name", "key", "youtube", "details", "played", "uploads", …}}
  GET  /music/versions?uri=…[&title=…]
                             → {"ok", "uri", "query", "source", "versions": [{"uri", "title", "channel", "duration_s", "kind"}]}
  POST /music {"action", …}  → the same, after doing it

`radio` is the station (agent_media_core.radio; docs/proposals/
2026-09-29-radio.md): `{"on", "seed", "next": [{id, title, channel, state}],
"more"}`. `{"action": "radio"}` starts one from what is playing (or `uri`),
`{"action": "radio", "dj": true}` starts the DJ's station (radio_dj), `{"action": "radio", "off": true}` ends it, `{"action": "radio", "play": id}`
plays a song from its list now, and `{"action": "dislike"}` is 👎:
that song off the station, and the next one on.
`{"action": "radio", "from_song": key, "video_id"?}` (also `POST
/music/radio`) starts one from the favourite a long reply sang out, on the
app's player, where the phone has put it (31 Oct 2026).

`downloads` is what is downloading now (agent_media_core.downloads):
`[{id, purpose, title, pct, phase}]`, a song or link before it plays
(`music`, `book`), the station's next (`radio`, also as `pct` on its row of
`radio.next`), a saved link or kept practice (`podcast`, `practice`); `pct`
0-100 or null while not known (10 Nov 2026).

`{"action": "radio", "resume": true | sid}` puts a kept station back on
where it was turned off (radio.resume): `radio.last` while off names the
newest for a day ("Back to radio"); `GET /music/recent?list=stations` lists
them, each row's `uri` `radio:<sid>`, which `{"action": "play"}` resumes
(1 Nov 2026).

`{"action": "bed", "uri"?, "off"?}` suggests what is playing (or `uri`)
for the backing music under replies, or takes it back; `bed.suggested` in
every answer says whether what is playing is suggested (1 Nov 2026).

A song not playing gets its ♥, 👎 and 🎼 by its `uri` (a row's from GET
/music/recent; David, 11 Nov 2026, the Media tab's song card: "Can we add
the like, dislike, sing it out and those other type of buttons to the song
card?"): `{"action": "like" | "unlike" | "dislike" | "undislike", "uri"}`.
A like goes on the likes list (and takes a 👎 back); a 👎 keeps the song off
every station (radio.dislike_song) and off the backing-music favourites, so
it is never under a reply nor sung out (music_bed_favourites.ban), and takes
a like and a 🎼 back; nothing plays or skips. The answer's `voted` is
`{"uri", "liked", "disliked", "bed_suggested", "rotation"}` for that song,
as `bed` with a `uri` answers too; music rows of GET /music/recent carry
the same (`disliked`, `bed_suggested` and `rotation` on a YouTube song
only). `{"action": "rotation", "uri"?, "step": 1 | -1}` turns a song (or
what is playing) up or down a level, -3 to 3 (music_rotation: More often /
Less often; David, 11 Nov 2026); every answer's `rotation` is what is
playing's level (null for no YouTube song). A row that is a ready
backing-music favourite with its tempo and energy measured has `moods`:
the moods it is favoured under (music_mood.favoured); the radio's DJ goes
by words, so no song has a rule there.

A song inside a mix is a song in its own right (agent_media_core.mix_songs;
David, 11–12 Nov 2026): with no `uri`, like, unlike, dislike, undislike and
rotation act on the chapter playing when what plays is a clip with chapters
(`"whole": true` for the clip itself); with a `uri`, `"start": <seconds>`
names one of its chapters (the chapter starting there), else the whole clip.
A 👎 on the chapter playing keeps that song from every pick and goes on to
the mix's next chapter (a station's next song when the station played just
that one). Every answer's `mix_song` is the chapter song playing (`{key,
start, end, title, artist, song, label, liked, disliked, rotation}`, null
for none), and `verdict` and `rotation` are its; `whole` (`{verdict,
rotation}`) the clip's while one plays. `voted` carries `start` for a
chapter. `{"action": "play", "uri", "start", "end"}` plays just that song:
from `start`, stopped (or the queue's next) at `end` (music_segment).

`mood` is the listening mood (agent_media_core.music_mood; David, 11 Nov
2026): `{"id", "moods": [{"id", "label", "blurb"}, …]}`, auto, chill, focus,
upbeat, sleep, party. `{"action": "mood", "mood": id}` sets it (400 for
another id): a DJ station drops its songs to come and picks again, a Mix
station or music playing without one becomes the DJ's.

`{"action": "song", "id"}` plays the song the music bed played under reply
`id` (its history row's `bed_song`, agent_media_core.music_bed_song): the
reply's ⋯ "Play the song". The answer's `song` says what and `when` ("now",
or "after" the reply being read ends).

`where.rooms` is the house speakers one by one (agent_media_core.music_rooms):
`{"action": "room", "id", "on"?, "volume"?}` switches one on or off and sets
how loud it is. `{"action": "move"}` takes what is playing to where the next
play goes (the picker's choice), at the same spot: it runs in the background
and the answer's `move` says how it went (David, 20 Oct 2026).

`now` is `media music status --json`: the live player's track, position and
state, whichever player that is (Sasonica's own, the Termux mpv, Mopidy), so
the tab and the desk popup can never disagree. `where` is the music block of
`GET /audio/targets`, for the tab's picker.

The controls are the `media music` verbs, run as the CLI runs them rather
than re-implemented here: they already follow the live player, keep the play
history and likes, and know each player's quirks (the app's socket takes
`seek`, not a time-pos write). Nothing here knows which player that is.

Reading asks the phone over the tailnet (two players, ~0.5-1 s each at
worst), so an answer is kept for a moment: the tab polls while it is open.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time

from . import audio, auth

#: `action` → the `media music` verb. `seek` and `seek-by` take a number.
ACTIONS = {
    "pause": "pause", "resume": "resume", "toggle": "toggle",
    "next": "next", "prev": "prev", "stop": "stop", "like": "like",
    "seek": "seek", "seek-by": "seek",
    # Recently played: put one on again, now or after what is playing.
    "play": "play", "add": "play",
    # A book or podcast from Recently played: `media book play`, resumed.
    "book": "book",
    # The station, run here rather than by the CLI (radio.py).
    "radio": "radio", "dislike": "dislike",
    # ♥ or 👎 on what is playing taken back (its `verdict` goes to null).
    "unlike": "unlike", "undislike": "undislike",
    # The house speakers one by one, and what is playing to another place.
    "room": "room", "move": "move",
    # A saved Spotify album played on one of his Spotify devices (spotify_remote).
    "spotify": "spotify",
    # The song the music bed played under a reply, by its speech history id.
    "song": "play",
    # Suggest what is playing (or `uri`) for the backing music; `off` takes
    # it back (agent_media_core.music_bed_favourites.suggest).
    "bed": "bed",
    # The listening mood (agent_media_core.music_mood): `mood` one of its ids.
    "mood": "mood",
    # A song's rotation, a step up or down (agent_media_core.music_rotation).
    "rotation": "rotation",
    # A start the play gate held (agent_media_core.play_gate): played now on
    # purpose, or forgotten.
    "held": "held",
}

_CACHE_TTL_S = 2.5
#: A read that finds nothing right after one that found a track is believed
#: only this long later: over the phone's link (~0.5 s each way on mobile) a
#: read can fail, and a failed read looks like "nothing playing" — the tab
#: flickered between the two (David, 28 Sep 2026).
_STICKY_S = 12.0
_CMD_TIMEOUT_S = 30.0
_LOCK = threading.Lock()
_CACHE: list = [0.0, None]
#: The last answer that found a track: `(at, (now, chapters))`.
_LAST: list = [None]


def _status() -> dict:
    """`media music status --json`, in-process."""
    from agent_media_core import cli
    from agent_media_core.sinks.music import SinkMusic
    from agent_media_core.sinks.music_router import SinkMusicRouter

    return cli._music_status_json(SinkMusicRouter(SinkMusic()))


#: The last `_run`'s exit code, per thread: EXIT_HELD says the play gate held
#: it (agent_media_core.play_gate), which the answer reports as `held`.
_RC = threading.local()


def _cli(argv: list[str], requested_at: float | None = None) -> tuple[bool, str]:
    """`media <argv>`, carrying when the play was asked for (play_gate:
    a play that would start too late is held, not started)."""
    from agent_media_core import play_gate

    env = play_gate.child_env()
    if requested_at is not None:
        env["MEDIA_PLAY_REQUESTED_AT"] = f"{requested_at:.3f}"
    _RC.code = None
    try:
        r = subprocess.run([sys.executable, "-m", "agent_media_core.cli", *argv],
                           capture_output=True, text=True, timeout=_CMD_TIMEOUT_S, env=env)
    except (subprocess.TimeoutExpired, OSError) as e:
        return False, str(e)
    _RC.code = r.returncode
    said = (r.stderr or r.stdout or "").strip()
    # A held play's words are said whole: why, and how to play it anyway.
    return r.returncode == 0, said[-1000:] if _was_held() else said[-300:]


def _was_held() -> bool:
    from agent_media_core.play_gate import EXIT_HELD

    return getattr(_RC, "code", None) == EXIT_HELD


def _run(argv: list[str], requested_at: float | None = None) -> tuple[bool, str]:
    """`media music <argv>`, as the desk runs it."""
    return _cli(["music", *argv], requested_at)


def _run_book(uri: str, force: bool = False) -> tuple[bool, str]:
    """`media book play <uri>` (resumes where it was left)."""
    return _cli(["book", "play", *(["--in-treatment"] if force else []), "--", uri])


def _held_answer(said: str) -> dict:
    """The answer to a play the play gate held: 409, `held`, and why."""
    return {"error": said.removeprefix("media: ") or "held: a treatment is under way",
            "held": True, "code": "held", "status": 409, **_answer()}


def _reset_cache(forget: bool = False) -> None:
    with _LOCK:
        _CACHE[:] = [0.0, None]
        if forget:
            _LAST[0] = None


def _now() -> tuple[dict, list[dict]]:
    """The status and the live file's chapters, kept together a moment."""
    t = time.monotonic()
    with _LOCK:
        if _CACHE[1] is not None and t - _CACHE[0] < _CACHE_TTL_S:
            return _CACHE[1]
    try:
        now = _status()
    except Exception as e:  # noqa: BLE001 — the tab must answer without it
        print(f"music: status failed: {e}", file=sys.stderr)
        now = {"backend": None}
    got = (now, _chapters(now))
    with _LOCK:
        last = _LAST[0]
        if now.get("pos_ms") is None and last and time.monotonic() - last[0] < _STICKY_S:
            got = last[1]
        elif now.get("pos_ms") is not None:
            _LAST[0] = (time.monotonic(), got)
        _CACHE[:] = [time.monotonic(), got]
    return got


def _chapters(now: dict) -> list[dict]:
    """A mix's tracks, when the live file is one the phone can read chapters
    from: ``[{"title", "start_ms"}]``, else []."""
    try:
        from agent_media_core.sinks import music_sasonica

        path = now.get("path") or ""
        if not music_sasonica.phone_path(path):
            return []
        return [{"title": c["title"], "start_ms": int(c["start"] * 1000)}
                for c in music_sasonica.chapters(path)]
    except Exception:  # noqa: BLE001 — a list the tab can do without
        return []


def _art(now: dict) -> str | None:
    from agent_media_core.music_recent import art

    return art(now.get("uri") or "") or art(now.get("media_id") or "")


def _radio() -> dict:
    from agent_media_core import radio

    try:
        return radio.snapshot()
    except Exception as e:  # noqa: BLE001 — the tab can do without it
        print(f"music: radio snapshot failed: {e}", file=sys.stderr)
        return {"on": False, "seed": None, "next": [], "available": False}


_ROOMS_TTL_S = 2.5
_ROOMS: list = [0.0, None]


def _rooms(fresh: bool = False) -> list[dict] | None:
    """The house speakers (music_rooms.rooms), kept a moment; None when the
    Snapcast server cannot be reached, [] when this host has no rooms."""
    from agent_media_core import music_rooms, snapcast

    t = time.monotonic()
    with _LOCK:
        if not fresh and _ROOMS[1] is not None and t - _ROOMS[0] < _ROOMS_TTL_S:
            return _ROOMS[1]
    try:
        got = music_rooms.rooms(timeout=1.5)
    except (snapcast.SnapcastError, OSError) as e:
        print(f"music: rooms failed: {e}", file=sys.stderr)
        return None
    with _LOCK:
        _ROOMS[:] = [time.monotonic(), got]
    return got


#: The last move: `{"to", "state": "moving" | "done" | "failed", "error", "at"}`.
_MOVE: dict = {}
#: A finished move is reported this long, so the tab that asked sees it.
_MOVE_SHOWN_S = 60.0


def _move_state() -> dict | None:
    with _LOCK:
        m = dict(_MOVE)
    if not m or (m["state"] != "moving" and time.time() - m["at"] > _MOVE_SHOWN_S):
        return None
    return {k: m[k] for k in ("to", "state", "error")}


def _who(now: dict) -> dict:
    """`artist`, `song` and `album` for a YouTube track (media_meta: the
    iTunes catalogue, else what the title says), so the app's banner can
    name them when the song changes (David, 29 Oct 2026; the album, 9 Nov
    2026: "can we get the artist and album info with it?"); null for
    anything else."""
    from agent_media_core import media_meta
    from agent_media_core.music_recent import _music_meta

    from agent_media_core import music_channel

    vid = now.get("media_id") or ""
    if not vid or len(vid) != 11:
        return {"artist": None, "song": None, "album": None, "channel": None}
    try:
        got = _music_meta(f"yt:{vid}", now.get("title") or "")
        if not media_meta.known(f"yt:{vid}"):
            media_meta.fill([(f"yt:{vid}", "music", {"vid": vid, "title": now.get("title") or ""})])
        # Who uploaded it (music_channel; 9 Nov 2026): the channel page's way in.
        ch = music_channel.row(vid)
        if ch is None:
            music_channel.fill([vid])
        return {"artist": got.get("artist"), "song": got.get("song"), "album": got.get("album"), "channel": ch}
    except Exception:  # noqa: BLE001 — the title alone will do
        return {"artist": None, "song": None, "album": None, "channel": None}


def _answer() -> dict:
    now, chapters = _now()
    now = {**now, **_who(now)}
    where = audio.channel_block("music")
    loading = _downloads()
    out = {"now": now, "art": _art(now), "chapters": chapters,
           "where": {**where, "rooms": _rooms()}, "radio": _with_pct(_radio(), loading),
           "downloads": loading}
    move = _move_state()
    if move:
        out["move"] = move
    out["bed"] = _bed_state(now)
    out["mood"] = _mood_state()
    ch = _playing_chapter(now)
    out["verdict"] = _verdict(now, ch)
    out["rotation"] = _rotation_of(now.get("media_id") or "", ch)
    out["mix_song"] = _mix_song_state(ch, out["verdict"], out["rotation"])
    if ch is not None:
        out["whole"] = {"verdict": _verdict(now, None, whole=True),
                        "rotation": _rotation_of(now.get("media_id") or "")}
    held = _held_list()
    if held is not None:
        out["on_hold"] = held
    return out


def _downloads() -> list[dict]:
    """The downloads under way (agent_media_core.downloads), for the app's
    progress marks: a song or link before it plays, a saved link or a kept
    practice on its row, the radio's next song (David, 10 Nov 2026: "a
    little progress indicator when something is downloading")."""
    try:
        from agent_media_core import downloads

        return downloads.active()
    except Exception as e:  # noqa: BLE001 — the tab can do without it
        print(f"music: downloads failed: {e}", file=sys.stderr)
        return []


def _with_pct(radio: dict, loading: list[dict]) -> dict:
    """Each song of the radio's list being downloaded gets its `pct` (null
    while not known), from the download under way for that id."""
    by_id = {}
    for d in loading:
        if d["purpose"] == "radio" or d["id"] not in by_id:
            by_id[d["id"]] = d
    rows = radio.get("next")
    if not rows or not by_id:
        return radio
    return {**radio, "next": [{**r, "pct": by_id[r["id"]]["pct"]} if r.get("state") == "fetching" and r.get("id") in by_id
                              else r for r in rows]}


def _held_list() -> list | None:
    """The starts the play gate held, newest first, for a "Play now"; None
    where there is no gate (the key is left out)."""
    from agent_media_core import play_gate

    if not play_gate.enabled():
        return None
    keep = ("id", "at", "uri", "title", "kind", "target", "session", "reason")
    return [{k: e.get(k) for k in keep} for e in play_gate.held()]


def _row_key(uri: str) -> str:
    """One song, whether named as `yt:` or a link (music_recent._key)."""
    from agent_media_core import music_recent

    return music_recent._key(uri)


def _row_vid(uri: str) -> str:
    from agent_media_core import music_recent

    return music_recent._vid(uri) or ""


def _mark_votes(items: list[dict]) -> None:
    """Give each music row its button states: `liked` (on the likes list),
    and for a YouTube song `disliked` (kept off a station, every station,
    or the backing music) and `bed_suggested` (suggested for the backing
    music, which Sing it out sings from). Each store read once."""
    rows = [r for r in items if isinstance(r.get("uri"), str)
            and r["uri"].startswith(("yt:", "http://", "https://"))]
    if not rows:
        return
    try:
        from agent_media_core import music_bed_favourites as fav
        from agent_media_core import radio
        from agent_media_core.state import StateStore

        liked = set()
        for like in StateStore().list_likes(2000, channel="music"):
            liked.add(like.get("media_id") or "")
            if like.get("uri"):
                liked.add(_row_key(like["uri"]))
        liked.discard("")
        gone = fav.excluded()
        off = radio.disliked_ids() | {k[3:] for k in gone if k.startswith("id:")}
        bed = {s.get("id") for s in fav.suggested().values()}
        from agent_media_core import music_mood, music_rotation

        turned = music_rotation.load()
        # The moods a backing-music favourite is favoured under, by its
        # measured tempo and energy (music_mood.favoured).
        measured = {}
        for e in fav.load().get("tracks") or []:
            got = music_mood.favoured(e.get("features"))
            if got is not None:
                for v in (e.get("original_id"), e.get("id")):
                    if v:
                        measured.setdefault(v, got)
    except Exception as e:  # noqa: BLE001 — button states, never a failed list
        print(f"music: votes failed: {e}", file=sys.stderr)
        return
    try:
        from agent_media_core import mix_songs

        gone_songs = mix_songs.disliked_keys()
        chapter_likes = {mix_songs.key(c["vid"], c["start"]) for c in mix_songs.liked()}
    except Exception as e:  # noqa: BLE001
        print(f"music: votes of songs of mixes failed: {e}", file=sys.stderr)
        gone_songs, chapter_likes = set(), set()
    for r in rows:
        vid = _row_vid(r["uri"])
        if vid and isinstance(r.get("start"), (int, float)) and r.get("song_key"):
            # A song of a mix (a liked chapter's row): its own keys' state.
            k = r["song_key"]
            r["liked"] = k in chapter_likes
            r["disliked"] = k in gone_songs
            r["rotation"] = music_rotation.level(k, turned)
            r.pop("bed_suggested", None)
            continue
        r["liked"] = _row_key(r["uri"]) in liked
        if vid:
            r["disliked"] = vid in off
            r["bed_suggested"] = vid in bed
            r["rotation"] = music_rotation.level(vid, turned)
            if vid in measured:
                r["moods"] = measured[vid]


def _rotation_of(vid: str, ch: dict | None = None):
    """A YouTube song's rotation level (music_rotation), the chapter song's
    when `ch` is one; else null."""
    try:
        from agent_media_core import music_rotation, radio

        if ch is not None:
            return music_rotation.level(ch["key"])
        return music_rotation.level(vid) if radio._ID.match(vid or "") else None
    except Exception:  # noqa: BLE001 — a button state, never a failed read
        return None


def _playing_chapter(now: dict) -> dict | None:
    """The song of a mix playing (agent_media_core.mix_songs.playing), else None."""
    try:
        from agent_media_core import mix_songs

        return mix_songs.playing(now)
    except Exception as e:  # noqa: BLE001 — the whole clip, as before
        print(f"music: chapter playing: {e}", file=sys.stderr)
        return None


def _mix_song_state(ch: dict | None, verdict, rotation) -> dict | None:
    """`mix_song` in an answer: the chapter song playing and its keys."""
    if ch is None:
        return None
    from agent_media_core import mix_songs

    return {**mix_songs.shown(ch), "liked": verdict == "like", "disliked": verdict == "dislike",
            "rotation": rotation}


def _named_chapter(uri: str, body: dict) -> tuple[dict | None, dict | None]:
    """`(chapter, None)` for a `start` naming one of the clip's chapters,
    `(None, None)` without a `start`, `(None, error)` for a bad one."""
    start = body.get("start")
    if start is None:
        return None, None
    if isinstance(start, bool) or not isinstance(start, (int, float)) or start < 0:
        return None, {"error": "start must be a number of seconds", "status": 400}
    from agent_media_core import mix_songs

    vid = _row_vid(uri)
    ch = mix_songs.find(vid, start=float(start)) if vid else None
    if ch is None:
        return None, {"error": "no song of that clip starts there", "status": 404}
    return ch, None


def _target_chapter(body: dict) -> tuple[str, dict | None, dict | None]:
    """What a key with no `uri` acts on: `(uri, chapter or None, error)` —
    the chapter playing unless `whole`."""
    now, _ = _now()
    uri = now.get("uri") or ""
    ch = None if body.get("whole") is True else _playing_chapter(now)
    return uri, ch, None


def _rotation_control(body: dict) -> tuple[bool, dict]:
    """`{"action": "rotation", "uri"?, "step": 1 | -1}`: the song named (or
    what is playing) comes round more or less often (music_rotation; David,
    11 Nov 2026: "an option to increase or decrease a song's rotation").
    Nothing plays or skips."""
    from agent_media_core import music_rotation

    by = body.get("step")
    if by not in (1, -1) or isinstance(by, bool):
        return False, {"error": "step must be 1 or -1", "status": 400}
    uri = body.get("uri")
    ch = None
    if uri is None:
        uri, ch, _ = _target_chapter(body)
    if not isinstance(uri, str) or not uri.strip().startswith(("yt:", "http://", "https://")):
        return False, {"error": "uri must be a yt: or web address", "status": 400}
    uri = uri.strip()
    if body.get("uri") is not None:
        ch, err = _named_chapter(uri, body)
        if err:
            return False, err
    vid = _row_vid(uri)
    if not vid:
        return False, {"error": "only a YouTube song has a rotation", "status": 400}
    try:
        if ch is not None:
            # A song of a mix, by its chapter (mix_songs.key).
            from agent_media_core import mix_songs

            music_rotation.step(ch["key"], by, mix_songs.label(ch),
                                extra={"vid": vid, "start": float(ch.get("start") or 0), "end": ch.get("end"),
                                       "artist": ch.get("artist"), "song": ch.get("song")})
            _reset_cache()
            return True, {**_answer(), "voted": _voted(uri, ch)}
        music_rotation.step(vid, by, _title_of(uri, body.get("title")))
    except ValueError as e:
        return False, {"error": str(e), "status": 400}
    except OSError as e:
        return False, {"error": f"could not save it ({e})", "status": 500}
    _reset_cache()
    return True, {**_answer(), "voted": _voted(uri)}


def _voted(uri: str, ch: dict | None = None) -> dict:
    """`{"uri", "liked", "disliked", "bed_suggested"}` for one song; for a
    song of a mix (`ch`), with its `start` and `key`."""
    if ch is not None:
        row = {"uri": uri, "start": float(ch.get("start") or 0), "song_key": ch["key"]}
        _mark_votes([row])
        return {"uri": uri, "start": row["start"], "key": ch["key"], "liked": bool(row.get("liked")),
                "disliked": bool(row.get("disliked")), "bed_suggested": False,
                "rotation": row.get("rotation"), "moods": None}
    row = {"uri": uri}
    _mark_votes([row])
    return {"uri": uri, "liked": bool(row.get("liked")), "disliked": bool(row.get("disliked")),
            "bed_suggested": bool(row.get("bed_suggested")), "rotation": row.get("rotation"),
            "moods": row.get("moods")}


def _title_of(uri: str, given) -> str:
    """A name for a song liked by its `uri`: the one sent, else the one the
    Media tab's rows show (the titles kept, the rooms cache's, the play
    history's)."""
    if isinstance(given, str) and given.strip():
        return given.strip()[:300]
    from agent_media_core import music_recent
    from agent_media_core.state import StateStore

    key, vid = _row_key(uri), _row_vid(uri)
    title = music_recent._titles().get(key) or (music_recent._cached_title(vid) if vid else "")
    if title:
        return title
    try:
        for r in StateStore().recent_history(sink="music", limit=2000, requested=True):
            if (r.get("text") or "").strip() and _row_key(r.get("uri") or "") == key:
                return r["text"].strip()
    except Exception:  # noqa: BLE001 — a name is a nicety
        pass
    return ""


def _vote_by_uri(action: str, body: dict) -> tuple[bool, dict]:
    """`like` / `unlike` / `dislike` / `undislike` with a `uri`: the song
    named, playing or not (the song card). Nothing plays or skips."""
    from agent_media_core import music_bed_favourites as fav
    from agent_media_core import radio
    from agent_media_core.state import StateStore

    uri = body.get("uri")
    if not isinstance(uri, str) or not uri.strip() or uri.lstrip().startswith("-") \
            or not uri.strip().startswith(("yt:", "http://", "https://")):
        return False, {"error": "uri must be a yt: or web address", "status": 400}
    uri = uri.strip()
    key, vid = _row_key(uri), _row_vid(uri)
    if action in ("dislike", "undislike") and not vid:
        return False, {"error": "only a YouTube song can be disliked", "status": 400}
    ch, err = _named_chapter(uri, body)
    if err:
        return False, err
    if ch is not None:
        return _vote_chapter(action, uri, ch)
    try:
        store = StateStore()
        if action == "like":
            if not store.is_liked("music", key, whole=True):
                store.add_like("music", key, uri=uri, title=_title_of(uri, body.get("title")),
                               backend="media-tab", note="liked on its info page")
            if vid:
                radio.undislike(vid)
                fav.unban(vid)
        elif action == "unlike":
            store.remove_likes("music", key, whole=True)
        elif action == "dislike":
            store.remove_likes("music", key, whole=True)
            title = _title_of(uri, body.get("title"))
            radio.dislike_song(vid, title)
            fav.ban(vid, title)
        else:
            radio.undislike(vid)
            fav.unban(vid)
    except ValueError as e:
        return False, {"error": str(e), "status": 400}
    except OSError as e:
        return False, {"error": f"could not save it ({e})", "status": 500}
    _reset_cache()
    return True, {**_answer(), "voted": _voted(uri)}


def _vote_chapter(action: str, uri: str, ch: dict, backend: str = "media-tab") -> tuple[bool, dict]:
    """♥ / 👎 (or either taken back) on a song of a mix, by its chapter
    (mix_songs). Nothing plays or skips."""
    from agent_media_core import mix_songs
    from agent_media_core.state import StateStore

    try:
        store = StateStore()
        if action == "like":
            mix_songs.add_like(store, ch, uri=uri, backend=backend, note="liked as a song of a mix",
                               mix_title=_title_of(uri, None))
            mix_songs.undislike(ch)
        elif action == "unlike":
            mix_songs.remove_like(store, ch)
        elif action == "dislike":
            mix_songs.remove_like(store, ch)
            mix_songs.dislike(ch)
        else:
            mix_songs.undislike(ch)
    except OSError as e:
        return False, {"error": f"could not save it ({e})", "status": 500}
    _reset_cache()
    return True, {**_answer(), "voted": _voted(uri, ch)}


def _verdict(now: dict, ch: dict | None = None, whole: bool = False) -> str | None:
    """`"like"` or `"dislike"` for what is playing, else null: the player's
    ♥ and 👎 stay lit when the song is come back to (David, 1 Nov 2026:
    "the like or dislike is no longer highlighted. can we fix that so it
    remembers?"). A like is the likes list's (on a mix, this chapter's); a
    dislike is a station's 👎, this one's or a kept one's."""
    vid = now.get("media_id") or ""
    if not vid:
        return None
    try:
        from agent_media_core import radio
        from agent_media_core.state import StateStore

        from agent_media_core import music_bed_favourites as fav

        if ch is not None:
            # A song of a mix: its own ♥ or 👎 (mix_songs).
            from agent_media_core import mix_songs

            if mix_songs.is_disliked(ch):
                return "dislike"
            return "like" if mix_songs.is_liked(StateStore(), ch) else None
        if radio.disliked(vid) or fav.banned(vid):
            return "dislike"
        if whole:
            return "like" if StateStore().is_liked("music", vid, whole=True) else None
        if StateStore().is_liked("music", vid, chapter=now.get("chapter") or ""):
            return "like"
    except Exception:  # noqa: BLE001 — a button state, never a failed read
        pass
    return None


def _unvote(action: str, body: dict | None = None) -> tuple[bool, dict]:
    """`unlike` / `undislike`: the ♥ or 👎 on what is playing taken back
    (David, 1 Nov 2026: "let's add being able to remove an up or down
    vote"). A like goes from the likes list (on a mix, this chapter's); a
    👎 from every station that has it."""
    from agent_media_core import radio
    from agent_media_core.state import StateStore

    _reset_cache()
    now, _ = _now()
    vid = now.get("media_id") or ""
    if not vid:
        return False, {"error": "nothing playing", "status": 409, **_answer()}
    whole = (body or {}).get("whole") is True
    ch = None if whole else _playing_chapter(now)
    if ch is not None:
        return _vote_chapter(action, now.get("uri") or f"yt:https://www.youtube.com/watch?v={vid}", ch)
    try:
        if action == "unlike":
            StateStore().remove_likes("music", vid, chapter="" if whole else (now.get("chapter") or ""),
                                      whole=whole)
        else:
            from agent_media_core import music_bed_favourites as fav

            radio.undislike(vid)
            fav.unban(vid)
    except OSError as e:
        return False, {"error": f"could not save it ({e})", "status": 500}
    _reset_cache()
    return True, _answer()


def _bed_state(now: dict) -> dict:
    """Whether what is playing is suggested for the backing music:
    `{"suggested": bool}`, for the player's 🎼 button."""
    try:
        from agent_media_core import music_bed_favourites as fav

        vid = fav._vid(now.get("uri") or "") or ""
        return {"suggested": bool(fav.suggested_key(vid))}
    except Exception:  # noqa: BLE001 — a button state, never a failed read
        return {"suggested": False}


def _bed_control(body: dict) -> tuple[bool, dict]:
    """`bed`: suggest what is playing (or `uri`) for the music under
    replies, or with `off` take it back (David, 1 Nov 2026: "an easy way to
    be able to suggest tracks for backing music"). The next background pass
    looks for its instrumental at once, in its own thread."""
    import threading as _t

    from agent_media_core import music_bed_favourites as fav

    uri = body.get("uri")
    if uri is not None and (not isinstance(uri, str) or not uri.strip()):
        return False, {"error": "uri must be a yt: or web address", "status": 400}
    if uri is None:
        now, _ = _now()
        uri = now.get("uri") or ""
    if not fav._vid(uri.strip()):
        return False, {"error": "only a YouTube track can be suggested for the backing music",
                       "status": 400, **_answer()}
    try:
        if body.get("off") is True:
            done = fav.unsuggest(uri.strip())
        else:
            done = fav.suggest(uri.strip())
            if fav.chosen() and not done.get("ready"):
                _t.Thread(target=fav.fill, daemon=True, name="bed-suggest").start()
    except ValueError as e:
        return False, {"error": str(e), "status": 400, **_answer()}
    except OSError as e:
        return False, {"error": f"could not save it ({e})", "status": 500}
    out = {**_answer(), "suggested": done}
    if body.get("uri") is not None:
        out["voted"] = _voted(uri.strip())
    return True, out


def _room_control(body: dict) -> tuple[bool, dict]:
    """`room`: one house speaker on or off, and how loud."""
    from agent_media_core import music_rooms, snapcast

    cid, on, volume = body.get("id"), body.get("on"), body.get("volume")
    if not isinstance(cid, str) or not cid:
        return False, {"error": "id must be a room's id", "status": 400}
    if on is not None and not isinstance(on, bool):
        return False, {"error": "on must be true or false", "status": 400}
    if volume is not None and (isinstance(volume, bool) or not isinstance(volume, (int, float))
                               or not 0 <= volume <= 100):
        return False, {"error": "volume must be 0-100", "status": 400}
    if on is None and volume is None:
        return False, {"error": "give on or volume", "status": 400}
    try:
        music_rooms.set_room(cid, on=on, volume=None if volume is None else int(round(volume)))
    except ValueError as e:
        return False, {"error": str(e), "status": 400}
    except (snapcast.SnapcastError, OSError) as e:
        return False, {"error": f"the speakers did not answer: {e}", "status": 502, **_answer()}
    _rooms(fresh=True)
    return True, _answer()


#: How long a move waits for the new place to start playing.
_MOVE_WAIT_S = 90.0


def _replay_uri(now: dict) -> str | None:
    """What to play elsewhere to carry on with `now`: the track asked for, or
    the YouTube video its file is; None for anything else (a book, a file)."""
    uri = now.get("uri") or ""
    if uri.startswith(("yt:", "http://", "https://")):
        return uri
    from agent_media_core.sinks.music_fetch import watch_id

    vid = watch_id(now.get("media_id") or "") or watch_id(uri)
    return f"yt:https://www.youtube.com/watch?v={vid}" if vid else None


def _kind(where: str) -> str:
    """`_resolve_music_where`'s answer, or a status backend, as rooms/phone."""
    return "rooms" if where in ("rooms", "mopidy") else "phone"


def _move(uri: str, to: str, from_kind: str, pos_s: float, paused: bool) -> None:
    """Pause where it is, play `uri` at `to`, and once it plays there stop
    the old place and seek to where it was. Back where it was on a failure."""
    from agent_media_core import cli
    from agent_media_core.sinks.music import SinkMusic
    from agent_media_core.sinks.music_router import SinkMusicRouter

    def done(state: str, error: str | None = None) -> None:
        with _LOCK:
            _MOVE.update(state=state, error=error, at=time.time())
        _reset_cache()
        print(f"music: move to {to}: {state}{f' ({error})' if error else ''}", file=sys.stderr)

    _run(["pause"])
    ok, said = _run(["play", uri, "--where", to])
    if not ok:
        _run(["resume"])
        return done("failed", said or "play failed")
    from agent_media_core.sinks.music_fetch import watch_id

    # A track left loaded there is not this one starting.
    want = watch_id(uri)
    deadline = time.monotonic() + _MOVE_WAIT_S
    while True:
        try:
            st = cli._music_status_json(SinkMusicRouter(SinkMusic()), where=_kind(to))
        except Exception:  # noqa: BLE001 — keep asking until the deadline
            st = {}
        if st.get("pos_ms") is not None and _kind(st.get("backend") or "") == _kind(to) \
                and (not want or st.get("media_id") == want):
            break
        if time.monotonic() > deadline:
            _run(["resume"])
            return done("failed", "it did not start there")
        time.sleep(1.0)
    # The old place: the house's Mopidy is stopped directly (a phone player
    # is the live one now); a phone player is still the live one, so the
    # verb reaches it.
    if from_kind == "rooms":
        try:
            SinkMusic().stop()
        except OSError:
            pass
    else:
        _run(["stop"])
    if pos_s > 2:
        _run(["seek", _clock(pos_s)])
    if paused:
        _run(["pause"])
    done("done")


def _move_control() -> tuple[bool, dict]:
    """`move`: what is playing to where the next play goes."""
    from agent_media_core import radio
    from agent_media_core.cli import _resolve_music_where

    now, _ = _now()
    if now.get("pos_ms") is None:
        return True, _answer()
    to = _resolve_music_where("default")
    if _kind(to) == _kind(now.get("backend") or ""):
        # Already there: phone players are one place to a move.
        return True, _answer()
    station = radio.read()
    if station.get("on"):
        # The radio goes with it: the station's own player is moved, so it
        # keeps its list (a play there would end it).
        if to not in radio.WHERES or station.get("where") == "handoff":
            return False, {"error": f"the radio does not play on {to}", "status": 409, **_answer()}
        return _begin_move(to, _move_station, to, int(now.get("pos_ms") or 0),
                           bool(now.get("paused")))
    uri = _replay_uri(now)
    if not uri:
        return False, {"error": "this can only play where it is", "status": 409, **_answer()}
    return _begin_move(to, _move, uri, to, _kind(now.get("backend") or ""),
                       (now.get("pos_ms") or 0) / 1000, bool(now.get("paused")))


def _begin_move(to: str, fn, *args) -> tuple[bool, dict]:
    """`fn(*args)` in the background, reported as the move to `to`."""
    with _LOCK:
        if _MOVE.get("state") == "moving":
            return False, {"error": "a move is under way", "status": 409}
        _MOVE.clear()
        _MOVE.update(to=to, state="moving", error=None, at=time.time())
    _spawn(fn, *args)
    return True, _answer()


def _move_station(to: str, pos_ms: int, paused: bool) -> None:
    """The radio to `to` (agent_media_core.radio.move), reported as a move."""
    from agent_media_core import radio

    from . import radio as loop

    try:
        radio.move(to, pos_ms, paused)
        state, error = "done", None
    except (ValueError, RuntimeError) as e:
        state, error = "failed", str(e)
    with _LOCK:
        _MOVE.update(state=state, error=error, at=time.time())
    _reset_cache()
    loop.wake()
    print(f"music: radio to {to}: {state}{f' ({error})' if error else ''}", file=sys.stderr)


def _spawn(fn, *args) -> None:
    threading.Thread(target=fn, args=args, name="music-move", daemon=True).start()


#: A reply still being read when its song is asked for: the song waits for
#: it to end, this long at most, then this long more for the bed's own
#: tidy-up (a streaming bed stops Mopidy and takes its tracks back).
_SONG_WAIT_S = 600.0
_SONG_SETTLE_S = 2.0
_SONG_POLL_S = 1.0


def _speaking() -> bool:
    """Whether a reply is being read on this host now (cli._speech_in_flight)."""
    try:
        from agent_media_core.cli import _speech_in_flight

        return bool(_speech_in_flight())
    except Exception:  # noqa: BLE001 — not known: as if quiet
        return False


def _song_after_speech(argv: list[str], requested_at: float | None = None) -> None:
    """Play the song once the reading ends (bounded), never over it. Judged
    by when it was asked for (play_gate): too late, it is held, not played."""
    deadline = time.monotonic() + _SONG_WAIT_S
    while _speaking() and time.monotonic() < deadline:
        time.sleep(_SONG_POLL_S)
    time.sleep(_SONG_SETTLE_S)
    done, said = _run(argv, requested_at)
    _reset_cache()
    if not done:
        print(f"music: song after the reply failed: {said}", file=sys.stderr)


def _song_control(body: dict) -> tuple[bool, dict]:
    """`{"action": "song", "id", "session"?}`: the song the music bed played
    under reply `id` (its speech history row's `extras.bed_song`,
    agent_media_core.music_bed_song), played in the music player as
    `media music play` does — the original of a favourite, a streaming
    track, a library track's own address. Only a row's recorded song, so it
    is not a way to play any address. While a reply is being read it waits
    for the reading to end (`song.when` "after"), so it neither cuts the
    reply off nor plays over it, and a streaming bed's tidy-up cannot stop
    it; else it starts now ("now")."""
    from agent_media_core.music_bed_song import label, shown
    from agent_media_core.state.store import StateStore

    rid = body.get("id")
    if isinstance(rid, bool) or not isinstance(rid, int) or rid <= 0:
        return False, {"error": "id must be a reply's speech history id", "status": 400}
    row = StateStore().history_row(rid)
    ex = (row or {}).get("extras") if (row or {}).get("sink") == "speech" else None
    ex = ex if isinstance(ex, dict) else {}
    song = ex.get("bed_song")
    session = body.get("session")
    if not shown(song) or (isinstance(session, str) and session
                           and ex.get("source_session") not in (None, "", session)):
        return False, {"error": "no song was played under that reply", "status": 404}
    uri = str(song.get("uri") or "").strip()
    if uri.startswith("-") or not uri.startswith(("yt:", "http://", "https://")):
        return False, {"error": "no song was played under that reply", "status": 404}
    argv = ["play", uri]
    if label(song):
        argv.append(f"--title={label(song)}")
    if isinstance(song.get("start"), (int, float)) and song["start"] > 0:
        # A song of a mix: just its chapter (mix_songs, music_segment).
        argv.append(f"--start={float(song['start']):g}")
    if isinstance(song.get("end"), (int, float)) and song["end"] > float(song.get("start") or 0):
        argv.append(f"--end={float(song['end']):g}")
    if body.get("force") is True:
        argv.append("--in-treatment")
    if _speaking():
        threading.Thread(target=_song_after_speech, args=(argv, time.time()), name="music-song",
                         daemon=True).start()
        return True, {**_answer(), "song": {**shown(song), "when": "after"}}
    done, said = _run(argv)
    _reset_cache()
    if not done:
        print(f"music: song {uri} failed: {said}", file=sys.stderr)
        return False, {"error": said or "play failed", "status": 502, **_answer()}
    return True, {**_answer(), "song": {**shown(song), "when": "now"}}


def _radio_control(body: dict) -> tuple[bool, dict]:
    """`radio` (start, or `off`) and `dislike`."""
    from agent_media_core import radio
    from agent_media_core.cli import _resolve_music_where

    from . import radio as loop

    if body.get("action") == "dislike":
        if body.get("whole") is not True and _dislike_playing_chapter():
            _reset_cache()
            loop.wake()
            return True, _answer()
        if radio.dislike() is None:
            # No station: 👎 is a skip.
            _run(["next"])
        _reset_cache()
        loop.wake()
        return True, _answer()
    if body.get("off"):
        radio.stop()
        return True, _answer()
    if "resume" in body:
        # Back to a station kept when it was turned off: `true` the newest,
        # else its `sid` (radio.resume).
        sid = body.get("resume")
        if not (sid is True or (isinstance(sid, str) and sid.isdigit())):
            return False, {"error": "resume must be true or a station's sid", "status": 400}
        try:
            radio.resume(None if sid is True else sid)
        except ValueError as e:
            return False, {"error": str(e), "status": 409, **_answer()}
        except RuntimeError as e:
            return False, {"error": str(e), "status": 502, **_answer()}
        _reset_cache()
        loop.wake()
        return True, _answer()
    if "play" in body:
        # A song from Up next, now.
        vid = body.get("play")
        if not isinstance(vid, str) or not radio._ID.match(vid):
            return False, {"error": "play must be a song id from the station's list", "status": 400}
        try:
            if radio.play(vid) is None:
                return False, {"error": "no station is on", "status": 409, **_answer()}
        except ValueError as e:
            return False, {"error": str(e), "status": 400, **_answer()}
        except RuntimeError as e:
            return False, {"error": str(e), "status": 502, **_answer()}
        _reset_cache()
        loop.wake()
        return True, _answer()
    if "from_song" in body:
        return _radio_from_song(body)
    if not radio.available():
        return False, {"error": radio.OFF_HERE, "status": 409, **_answer()}
    if body.get("dj"):
        # The DJ's station: what plays now plays on until its first pick.
        # On the named player, else the default (the hand-off player where
        # YouTube is off; MEDIA_RADIO_PLAYER).
        where = body.get("player") if body.get("player") in radio.radio_io.PLAYERS \
            else radio.radio_io.default_player(_resolve_music_where("default"))
        try:
            radio.start_dj(where)
        except ValueError as e:
            return False, {"error": str(e), "status": 409, **_answer()}
        loop.wake()
        return True, _answer()
    uri = body.get("uri")
    playing = False
    if uri is None:
        now, _ = _now()
        uri = now.get("uri") or now.get("media_id") or ""
        playing = True
    if not isinstance(uri, str) or not uri.strip() or uri.lstrip().startswith("-"):
        return False, {"error": "uri must be a YouTube track", "status": 400}
    where = _resolve_music_where("default")
    if where not in radio.WHERES:
        return False, {"error": f"the radio does not play on {where}", "status": 409, **_answer()}
    if not playing:
        done, said = _run(["play", uri.strip()])
        if not done:
            return False, {"error": said or "play failed", "status": 502, **_answer()}
    try:
        radio.start(uri.strip(), where, playing=True)
    except ValueError as e:
        return False, {"error": str(e), "status": 400, **_answer()}
    except RuntimeError as e:
        return False, {"error": str(e), "status": 502, **_answer()}
    _reset_cache()
    loop.wake()
    return True, _answer()


def _dislike_playing_chapter() -> bool:
    """👎 with no uri on a song of a mix playing: that song kept from every
    pick (mix_songs.dislike, its like taken back), and on to the mix's next
    chapter — the station's next song when it played just that one. False
    when no song of a mix is playing."""
    from agent_media_core import mix_songs, radio
    from agent_media_core.state import StateStore

    _reset_cache()
    now, _ = _now()
    ch = _playing_chapter(now)
    if ch is None:
        return False
    mix_songs.remove_like(StateStore(), ch)
    mix_songs.dislike(ch)
    if radio.bounded_current():
        radio.skip()
    else:
        _run(["chapter", "next"])
    return True


#: The player a station from a sung-out song runs on: the app's own, where
#: the phone put the song as the reply ended.
FROM_SONG_PLAYER = "sasonica"


def _radio_from_song(body: dict) -> tuple[bool, dict]:
    """`{"action": "radio", "from_song": <favourite key>, "video_id"?}`: a
    YouTube Mix station seeded with the song a long reply sang out (David,
    31 Oct 2026: "The new singing it out feature is awesome!" — radio from
    the song). The phone has already put the song on its own player
    (labelled with `&v=<video_id>`, so the station finds it there), so it is
    built behind it on `sasonica`. The video is the favourite's original
    (`original_id`), else the `video_id` given; neither: 400."""
    from agent_media_core import music_bed_favourites as fav
    from agent_media_core import radio

    from . import radio as loop

    key = body.get("from_song")
    if not isinstance(key, str) or not key:
        return False, {"error": "from_song must be a favourite's key", "status": 400}
    given = body.get("video_id")
    given = given if isinstance(given, str) and radio._ID.match(given) else ""
    try:
        song = fav._song(fav.load(), key)
    except Exception as e:  # noqa: BLE001 — the id given may still do
        print(f"music: favourites unreadable: {e}", file=sys.stderr)
        song = None
    if song is None and not given:
        return False, {"error": f"no favourite {key!r}", "status": 400}
    vid = ((song or {}).get("original_id") or (song or {}).get("id") or given)
    if not vid:
        return False, {"error": "this song has no YouTube original", "status": 400}
    # Taken up: the phone already plays it in its music player, so it is in
    # the music history with its thread whether or not a station follows
    # (music_sung; David, 8 Nov 2026).
    try:
        from agent_media_core import music_sung

        music_sung.engaged(key, "radio", video_id=vid)
    except Exception as e:  # noqa: BLE001 — the station is what was asked
        print(f"music: sung-out radio not noted: {e}", file=sys.stderr)
    if not radio.available():
        return False, {"error": radio.OFF_HERE, "status": 409, **_answer()}
    try:
        radio.start(radio.radio_io.yt_uri(vid), FROM_SONG_PLAYER, playing=True)
    except ValueError as e:
        return False, {"error": str(e), "status": 400, **_answer()}
    except RuntimeError as e:
        return False, {"error": str(e), "status": 502, **_answer()}
    _reset_cache()
    loop.wake()
    return True, _answer()


def now(bearer: str) -> tuple[bool, dict]:
    """`GET /music` — gated like /speech/now."""
    user, err = auth.gate(bearer)
    if not user:
        return False, err
    return True, _answer()


def recent(bearer: str, kind: str = "music", which: str = "recent", q: str = "") -> tuple[bool, dict]:
    """`GET /music/recent` — gated like /speech/now: what was played, newest
    first, one per track, with the conversation that put it on
    (agent_media_core.music_recent). `kind` is music, book or podcast;
    `which` (`?list=`) one of its lists (music_recent.LISTS): what was
    played, the most played, the songs liked, a book library's, the newest
    podcast episodes. `q` (`?q=`, 9 Nov 2026) searches: the whole history
    for what was played and the most played, the list's own rows for the
    others; the answer echoes it."""
    user, err = auth.gate(bearer)
    if not user:
        return False, err
    from agent_media_core import music_recent

    if kind not in music_recent.KINDS:
        return False, {"error": "kind must be music, book or podcast", "status": 400}
    if which not in music_recent.LISTS[kind]:
        return False, {"error": f"list must be one of {', '.join(music_recent.LISTS[kind])}", "status": 400}
    q = (q or "").strip()[:200]
    try:
        items = music_recent.listed(kind=kind, which=which, q=q)
    except Exception as e:  # noqa: BLE001 — an empty list, not a 500
        print(f"music: recent failed: {e}", file=sys.stderr)
        items = []
    if kind == "music":
        _mark_votes(items)
    out = {"kind": kind, "list": which, "lists": list(music_recent.LISTS[kind]), "items": items}
    if q:
        out["q"] = q
    return True, out


def channel(bearer: str, name: str = "", url: str = "", vid: str = "") -> tuple[bool, dict]:
    """`GET /music/channel` — gated like /music/recent: a YouTube channel's
    page (agent_media_core.music_channel.page), found by `name` (a channel
    or an artist), `url` (its address) or `vid` (a video of its): its
    details and latest uploads where they can be had, and everything played
    from it or by that artist."""
    user, err = auth.gate(bearer)
    if not user:
        return False, err
    name, url, vid = (name or "").strip()[:200], (url or "").strip()[:300], (vid or "").strip()
    if url and not url.startswith("https://www.youtube.com/"):
        return False, {"error": "url must be a YouTube channel's address", "status": 400}
    if not (name or url or vid):
        return False, {"error": "say name, url or vid", "status": 400}
    from agent_media_core import music_channel

    try:
        got = music_channel.page(name=name, url=url, vid=vid)
    except Exception as e:  # noqa: BLE001 — a name and nothing else, not a 500
        print(f"music: channel failed: {e}", file=sys.stderr)
        got = {"name": name, "key": music_channel.key(name), "url": url or None, "youtube": url or None,
               "details": None, "played": [], "uploads": [], "sources": {"details": None, "uploads": None}}
    return True, {"channel": got}


def versions(bearer: str, uri: str = "", title: str = "") -> tuple[bool, dict]:
    """`GET /music/versions?uri=…[&title=…]` — gated like /music/recent:
    other recordings of the song (live, acoustic, remix, cover, remaster,
    …; agent_media_core.music_versions; David, 11 Nov 2026: "Can we also
    have a button for alternative versions of a song?"), searched by its
    artist and song with the host's YouTube search, without the video
    named."""
    user, err = auth.gate(bearer)
    if not user:
        return False, err
    uri = (uri or "").strip()
    if not uri.startswith(("yt:", "http://", "https://")):
        return False, {"error": "uri must be a yt: or web address", "status": 400}
    from agent_media_core import music_bed_favourites as fav
    from agent_media_core import music_recent, music_versions

    title = (title or "").strip()[:300] or _title_of(uri, None)
    meta = music_recent._music_meta(uri, title)
    artist, song = (meta.get("artist") or "").strip(), (meta.get("song") or "").strip()
    if not song:
        artist, song = fav.clean_original(artist, title)
    if not song:
        return False, {"error": "no name to look for: give a title", "status": 400}
    try:
        got = music_versions.versions(artist, song, exclude=_row_vid(uri))
    except Exception as e:  # noqa: BLE001 — none found, not a 500
        print(f"music: versions failed: {e}", file=sys.stderr)
        got = {"query": f"{artist} {song}".strip(), "source": None, "versions": []}
    return True, {"uri": uri, **got}


def _find(find: dict) -> tuple[str | None, dict]:
    artist = str(find.get("artist") or "").strip()[:200]
    title = str(find.get("title") or "").strip()[:300]
    if not title:
        return None, {"error": "find needs a title", "status": 400}
    from agent_media_core import music_discover

    try:
        uri = music_discover.resolve(artist, title)
    except Exception as e:  # noqa: BLE001
        print(f"music: finding {artist} - {title} failed: {e}", file=sys.stderr)
        uri = None
    if not uri:
        return None, {"error": "couldn't find that on YouTube here", "status": 404}
    return uri, {}


def discover(bearer: str, which: str = "", page: str = "1", hide_played: bool = True, genre: str = "",
             playlist: str = "", q: str = "") -> tuple[bool, dict]:
    """`GET /music/discover` — gated like /music/recent: music not played
    here yet (agent_media_core.music_discover; David, 11 Nov 2026: "Can we
    add a discover section for music too?")."""
    user, err = auth.gate(bearer)
    if not user:
        return False, err
    from agent_media_core import music_discover

    if not str(page or "1").isdigit():
        return False, {"error": "page is a number", "status": 400}
    vid = ""
    if (which or "") == "like_playing":
        try:
            now, _ = _now()
            vid = _row_vid(str(now.get("uri") or ""))
        except Exception:  # noqa: BLE001
            vid = ""
    try:
        got = music_discover.listing(which or "for_you", int(page or 1), hide_played, (genre or "").strip()[:60],
                                     (playlist or "").strip(), vid, q=q or "")
    except ValueError as e:
        return False, {"error": str(e), "status": 400}
    except Exception as e:  # noqa: BLE001 — the source failed
        print(f"music: discover {which} failed: {e}", file=sys.stderr)
        return False, {"error": f"couldn't reach the list's source: {e}", "status": 502}
    if got.get("items"):
        _mark_votes([i for i in got["items"] if i.get("uri")])
    return True, got


def brainz(bearer: str, search: str = "", artist: str = "", album: str = "", name: str = "") -> tuple[bool, dict]:
    """`GET /music/brainz` — gated like /music/recent: MusicBrainz's
    artists and albums (agent_media_core.music_brainz): `?search=`,
    `?artist=<mbid>`, `?album=<mbid>`, or `?name=` (the artist a name surely
    is, or null)."""
    user, err = auth.gate(bearer)
    if not user:
        return False, err
    from agent_media_core import music_brainz as mb

    try:
        if search:
            return True, mb.search(search)
        if artist:
            return True, mb.artist(artist)
        if album:
            return True, mb.album(album)
        if name:
            return True, {"artist": mb.artist_by_name(name[:200])}
    except ValueError as e:
        return False, {"error": str(e), "status": 400}
    except LookupError as e:
        return False, {"error": str(e), "status": 404}
    except Exception as e:  # noqa: BLE001
        print(f"music: musicbrainz failed: {e}", file=sys.stderr)
        return False, {"error": f"MusicBrainz didn't answer: {e}", "status": 502}
    return False, {"error": "say search, artist, album or name", "status": 400}


def brainz_play(body: dict, bearer: str) -> tuple[bool, dict]:
    """`POST /music/brainz {"album": <mbid>, "from"?: n}`: the album in
    order — its first track (or track `from`) found and played as a
    Discover row is, the rest found and queued one by one in the background."""
    ok, err = auth.may_control_speech(bearer)
    if not ok:
        return False, err
    from agent_media_core import music_brainz as mb

    try:
        got = mb.album(str(body.get("album") or ""))
    except ValueError as e:
        return False, {"error": str(e), "status": 400}
    except LookupError as e:
        return False, {"error": str(e), "status": 404}
    except Exception as e:  # noqa: BLE001
        return False, {"error": f"MusicBrainz didn't answer: {e}", "status": 502}
    start = body.get("from") or 1
    tracks = [t for t in got["tracks"] if isinstance(start, int) and t["n"] >= start]
    if not tracks:
        return False, {"error": "that album has no tracks listed", "status": 404}
    first, rest = tracks[0], tracks[1:]
    ok, out = control({"action": "play", "find": {"artist": first["artist"] or got["album"]["artist"] or "",
                                                   "title": first["title"]}}, bearer)
    if not ok:
        return False, out

    def queue():
        for t in rest:
            try:
                control({"action": "add", "find": {"artist": t["artist"] or got["album"]["artist"] or "",
                                                    "title": t["title"]}}, bearer)
            except Exception as e:  # noqa: BLE001 — one missing, the rest still queued
                print(f"music: queueing {t['title']} failed: {e}", file=sys.stderr)
    if rest:
        _spawn(queue)
    return True, {**out, "queued": len(rest)}


def _played_book(uri: str) -> bool:
    """Whether `uri` is one the book and podcast lists hold (what was
    played, the library, new episodes) — the only books this route will
    start, so it is not a way to play any file on the host."""
    from agent_media_core import music_recent

    return music_recent.playable_book(uri)


def _spotify_play(body: dict) -> tuple[bool, dict]:
    """A saved album (`uri` album:…) played on Spotify itself, on `device`
    or the one Spotify makes plain (agent_media_core.spotify_remote). A
    refusal carries `code`: signed_out, sign_in_again, premium, no_device
    (with `link`, the album on Spotify, which opens the app), choose (with
    `devices`) or spotify."""
    from agent_media_core import music_links, play_gate, spotify_remote

    uri = str(body.get("uri") or "")
    a = music_links.find(uri) if uri.startswith("album:") else None
    if not a:
        return False, {"error": "uri must be a saved album (album:…)", "status": 404}
    try:
        r = spotify_remote.play(music_links.spotify_uri(uri) or "", device=str(body.get("device") or ""))
    except spotify_remote.RemoteError as e:
        return False, {"error": str(e), "code": e.code, "link": a.get("url"), **e.extra, "status": 409}
    except play_gate.PlayHeld:
        raise                      # control() answers it as held
    except Exception as e:  # noqa: BLE001 — Spotify unreachable
        return False, {"error": f"couldn't reach Spotify: {e}", "code": "spotify", "status": 502}
    return True, {"spotify": True, "device": r.get("device"), "title": a.get("title")}


def _clock(seconds: float) -> str:
    s = max(0, int(round(seconds)))
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


def control(body: dict, bearer: str) -> tuple[bool, dict]:
    """`POST /music` — gated like /speech/ctl: the listener's controls."""
    ok, err = auth.may_control_speech(bearer)
    if not ok:
        return False, err
    action = str(body.get("action") or "")
    if action not in ACTIONS:
        return False, {"error": "unknown action", "status": 400}
    if action in ("play", "add", "like") and not body.get("uri") and isinstance(body.get("find"), dict):
        # A Discover row by name (music_discover.resolve): found with the
        # host's YouTube search, then played, queued or liked as any song.
        found, why = _find(body["find"])
        if not found:
            return False, why
        body = {**{k: v for k, v in body.items() if k != "find"}, "uri": found}
    if action == "held":
        return _held_control(body)
    if action == "mood":
        # Not a play: a station it starts is fed by the loop, which keeps
        # to the play gate itself.
        return _mood_control(body)
    if action in ("like", "unlike", "dislike", "undislike") and "uri" in body:
        # A song's ♥ or 👎 by its uri: nothing plays, so no play gate.
        return _vote_by_uri(action, body)
    if action == "rotation":
        return _rotation_control(body)
    from agent_media_core import play_gate

    # `force: true`: through the play gate on purpose (music in a treatment).
    force = body.get("force") is True
    try:
        with play_gate.request(force=force, session=str(body.get("session") or ""),
                               source="media-tab"):
            ok, out = _control(action, body, force)
    except play_gate.PlayHeld as e:
        return False, _held_answer(str(e))
    if not ok and _was_held():
        return False, _held_answer(str(out.get("error") or ""))
    return ok, out


def _mood_state() -> dict:
    """`{"id", "moods": [{"id", "label", "blurb"}]}`: the listening mood
    and the six, for the Media tab's chips and Settings → Music."""
    try:
        from agent_media_core import music_mood

        return music_mood.shown()
    except Exception as e:  # noqa: BLE001 — the tab can do without it
        print(f"music: mood failed: {e}", file=sys.stderr)
        return {"id": "auto", "moods": []}


def _mood_control(body: dict) -> tuple[bool, dict]:
    """`{"action": "mood", "mood": id}`: set the listening mood (music_mood.
    set_mood: a DJ station picks again, music playing gets one)."""
    from agent_media_core import music_mood

    from . import radio as loop

    mood = music_mood.normalise(body.get("mood"))
    if mood is None:
        return False, {"error": f"mood must be one of {', '.join(music_mood.IDS)}", "status": 400}

    def playing() -> bool:
        now, _ = _now()
        return bool(now.get("uri") or now.get("media_id") or now.get("title")) and not now.get("paused")

    try:
        music_mood.set_mood(mood, playing=playing)
    except OSError as e:
        return False, {"error": f"could not save it ({e})", "status": 500}
    _reset_cache()
    loop.wake()
    return True, _answer()


def _held_control(body: dict) -> tuple[bool, dict]:
    """`{"action": "held", "id"?, "play": true}` plays a start the play gate
    held, now, on purpose (the newest when no id); `"dismiss": true` forgets
    it. The answer's `on_hold` lists what is still held."""
    from agent_media_core import play_gate

    hid = body.get("id")
    hid = hid if isinstance(hid, str) else ""
    if body.get("dismiss") is True:
        if not hid or not play_gate.dismiss(hid):
            return False, {"error": "no held play with that id", "status": 404, **_answer()}
        return True, _answer()
    if body.get("play") is not True:
        return False, {"error": "say play or dismiss", "status": 400}
    r = play_gate.play_now(hid, str(body.get("session") or ""))
    _reset_cache()
    if not r.get("ok"):
        return False, {"error": r.get("error") or "play failed",
                       "status": 404 if not r.get("played") else 502, **_answer()}
    return True, _answer()


def _control(action: str, body: dict, force: bool) -> tuple[bool, dict]:
    if action in ("radio", "dislike"):
        return _radio_control(body)
    if action == "room":
        return _room_control(body)
    if action == "move":
        return _move_control()
    if action == "song":
        return _song_control(body)
    if action == "bed":
        return _bed_control(body)
    if action in ("unlike", "undislike"):
        return _unvote(action, body)
    if action == "like" and body.get("whole") is True:
        # The whole clip, not the song of a mix playing.
        done, said = _run(["like", "--whole"])
        _reset_cache()
        if not done:
            return False, {"error": said or "like failed", "status": 502, **_answer()}
        return True, _answer()
    argv = [ACTIONS[action]]
    if action in ("seek", "seek-by"):
        key = "to" if action == "seek" else "by"
        v = body.get(key)
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return False, {"error": f"{key} must be a number of seconds", "status": 400}
        if action == "seek":
            argv.append(_clock(v))
        else:
            argv.append(f"{'+' if v >= 0 else '-'}{abs(int(round(v)))}")
    elif action == "prev":
        argv.append("--restart-first")
    elif action == "book":
        uri = body.get("uri")
        if not isinstance(uri, str) or not uri.strip() or not _played_book(uri.strip()):
            return False, {"error": "uri must be a book or podcast from the Media tab's lists", "status": 400}
        from agent_media_core import podcast_links

        # A saved link's download plays as music, from its web address.
        url = podcast_links.play_url(uri.strip())
        done, said = (_run(["play", *(["--in-treatment"] if force else []), url]) if url
                      else _run_book(uri.strip(), force))
        _reset_cache()
        if not done:
            print(f"music: book play failed: {said}", file=sys.stderr)
            return False, {"error": said or "book failed", "status": 502, **_answer()}
        return True, _answer()
    elif action == "spotify":
        return _spotify_play(body)
    elif action == "play" and isinstance(body.get("uri"), str) and body["uri"].startswith("album:"):
        # A row of Show → Albums: a saved Spotify album, played from YouTube,
        # or a saved YouTube video or playlist (refused with code "youtube"
        # and its link where YouTube can't be played here).
        from agent_media_core import music_links

        if not music_links.find(body["uri"]):
            return False, {"error": "no saved album under that id", "status": 404}
        r = music_links.play(body["uri"])
        _reset_cache()
        if r.get("held"):
            return False, _held_answer(str(r.get("error") or ""))
        if not r.get("ok"):
            extra = {k: r[k] for k in ("code", "link") if r.get(k)}
            return False, {"error": r.get("error") or "album failed", "status": 409 if extra else 502,
                           **extra, **_answer()}
        return True, _answer()
    elif action == "play" and isinstance(body.get("uri"), str) and body["uri"].startswith("radio:"):
        # A row of Show → Stations: that station back on.
        return _radio_control({"action": "radio", "resume": body["uri"][6:]})
    elif action in ("play", "add"):
        uri = body.get("uri")
        # A name the CLI would read as an option is not a track.
        if not isinstance(uri, str) or not uri.strip() or uri.lstrip().startswith("-") \
                or not uri.strip().startswith(("yt:", "http://", "https://")):
            return False, {"error": "uri must be a yt: or web address", "status": 400}
        argv.append(uri.strip())
        if action == "add":
            argv.append("--add")
        start = body.get("start")
        if start is not None:
            # A mix's chapter from the Media tab's songs (music_chapters): the
            # clip from that many seconds in, through the play gate as any play.
            if isinstance(start, bool) or not isinstance(start, (int, float)) or start < 0:
                return False, {"error": "start must be a number of seconds", "status": 400}
            if start > 0 and action == "play":
                argv.append(f"--start={float(start):g}")
        end = body.get("end")
        if end is not None:
            # Just that song of a mix (mix_songs): stopped, or on to the
            # queue's next, at `end` seconds (music_segment).
            if isinstance(end, bool) or not isinstance(end, (int, float)) or end <= float(start or 0):
                return False, {"error": "end must be a number of seconds after start", "status": 400}
            if action == "play":
                argv.append(f"--end={float(end):g}")
        if force:
            argv.append("--in-treatment")
    done, said = _run(argv)
    _reset_cache(forget=action == "stop")
    if not done:
        print(f"music: {' '.join(argv)} failed: {said}", file=sys.stderr)
        return False, {"error": said or f"{action} failed", "status": 502, **_answer()}
    return True, _answer()
