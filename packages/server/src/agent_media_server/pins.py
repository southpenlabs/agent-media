"""Keep-open pins: sessions the idle reaper must never close.

`POST /session/pin {"session", "pinned": bool}` sets one; `/targets` and
`/conversations` rows carry `pinned`. A pin is about the reaper only — it does
not stop you closing the session yourself, and it survives the session ending
(pin a thread, close it, resume it next week: still pinned).

Stored in `<state_dir>/pinned.json` as `{"<session>": <pinned at, epoch>}`
(`_jsonmap.JsonMap`).
"""

from __future__ import annotations

import sys
import time

from . import auth, sessions
from ._jsonmap import JsonMap

PINS = JsonMap("pinned.json")


def pinned() -> dict[str, float]:
    return {k: v for k, v in PINS.all().items() if isinstance(v, (int, float))}


def is_pinned(session: str) -> bool:
    return session in pinned()


def set_pinned(session: str, flag: bool) -> bool:
    """Pin or unpin. True when that changed anything."""
    if flag:
        def fn(rows: dict) -> bool:
            if session in rows:
                return False
            rows[session] = round(time.time(), 3)
            return True
        return PINS.update(fn)
    return PINS.drop(session)


def _known(session: str) -> bool:
    """A session these settings may be set on: live, with a transcript, in a
    folder, or a new chat sessiond has an id for but is still holding for a
    free session slot (its record, or `held.json`) — David, 11 Nov 2026: a
    speech level set on a queued new chat was refused "no such session"."""
    if (sessions.live_sessions().get(session) or sessions.session_exists(session)
            or sessions._folder_for_session(session)):
        return True
    from . import sessiond

    return session in sessiond.held_waits() or sessiond.record_path(session).exists()


def session_pin(session: str, flag, bearer: str) -> tuple[bool, dict]:
    """`POST /session/pin {"session", "pinned"}`. Gated like `/session/archive`,
    and refused the same ways: 400 for a bad id or a non-boolean, 404 for a
    session nothing knows. `pinned` defaults to true."""
    session = (session or "").strip()
    if not sessions._SESSION.fullmatch(session):
        return False, {"error": "not a session id", "status": 400}
    if flag is None:
        flag = True
    if not isinstance(flag, bool):
        return False, {"error": "pinned must be true or false", "status": 400}
    user, err = auth.gate(bearer)
    if not user:
        return False, err
    if not _known(session):
        return False, {"error": f"no such session {session[:8]}", "status": 404}
    try:
        set_pinned(session, flag)
    except OSError as e:
        return False, {"error": f"could not save the pin ({e})", "status": 500}
    return True, {"session": session, "pinned": flag}


def session_priority(session: str, flag, bearer: str, level=None) -> tuple[bool, dict]:
    """`POST /session/priority {"session", "level"}`: the thread's speech
    level — `interrupt`, `auto`, `pocket`, `normal` or `quiet`
    (`agent_media_core.speak_priority`), or `default` to clear its own so it
    follows the default. The older `{"priority": bool}` is auto / normal and
    still accepted. Refused exactly as `/session/pin` is; with neither given,
    auto. Answers the level it now has, and `own`: whether that is its own."""
    from agent_media_core import speak_priority

    session = (session or "").strip()
    if not sessions._SESSION.fullmatch(session):
        return False, {"error": "not a session id", "status": 400}
    if level is None:
        if flag is None:
            flag = True
        if not isinstance(flag, bool):
            return False, {"error": "priority must be true or false", "status": 400}
        level = "auto" if flag else "normal"
    if level not in speak_priority.LEVELS and level != "default":
        return False, {"error": "level must be interrupt, auto, pocket, normal, quiet or default",
                       "status": 400}
    user, err = auth.gate(bearer)
    if not user:
        return False, err
    from . import matrix

    if not (_known(session)
            or matrix.room(session) is not None):     # a Matrix room's level
        return False, {"error": f"no such session {session[:8]}", "status": 404}
    try:
        if level == "default":
            speak_priority.clear_level(session)
        else:
            speak_priority.set_level(session, level)
    except OSError as e:
        return False, {"error": f"could not save the level ({e})", "status": 500}
    level = speak_priority.level_of(session)
    return True, {"session": session, "level": level,
                  "own": session in speak_priority.levels(),
                  "from": speak_priority.source_of(session),
                  "priority": level in speak_priority.SPEAKS}


def session_pocket(session: str, flag, bearer: str) -> tuple[bool, dict]:
    """`POST /session/pocket {"session", "pocketed"}`: the app's page hid
    with the screen off while this thread was open (true), or showed again
    (false). A reply from it at the pocket level plays until the lease runs out
    (`agent_media_core.watching.pocket`). Refused as `/session/priority` is;
    answers `until`, the lease's end in epoch seconds, 0 for none."""
    from agent_media_core import watching

    session = (session or "").strip()
    if not sessions._SESSION.fullmatch(session):
        return False, {"error": "not a session id", "status": 400}
    if not isinstance(flag, bool):
        return False, {"error": "pocketed must be true or false", "status": 400}
    user, err = auth.gate(bearer)
    if not user:
        return False, err
    try:
        until = watching.pocket(session, flag)
    except OSError as e:
        return False, {"error": f"could not save the lease ({e})", "status": 500}
    return True, {"session": session, "until": until}


#: The black screen's reach (`POST /device/black`'s `reach`).
BLACK_REACHES = ("thread", "pinned", "all")


def device_black(flag, bearer: str, device="", session=None, reach=None) -> tuple[bool, dict]:
    """`POST /device/black {"on", "device"?}`: the app's black screen went up
    (true; renewed every 30 s while it stays) or down (false). While the lease
    lasts a question card is read in full even with its card out of view: it
    can be answered by voice there (`agent_media_core.question_gate`); down,
    a question still being read for a thread not in view is stopped and cued
    (`speech.recheck_question_soon`). Gated as `/session/pocket`; answers
    `until`, the lease's end, 0 for none."""
    from agent_media_core import watching

    if not isinstance(flag, bool):
        return False, {"error": "on must be true or false", "status": 400}
    if device is not None and not isinstance(device, str):
        return False, {"error": "device must be a string", "status": 400}
    if session is not None and not isinstance(session, str):
        return False, {"error": "session must be a string", "status": 400}
    if reach is not None and reach not in BLACK_REACHES:
        return False, {"error": "reach must be thread, pinned or all", "status": 400}
    device = (device or "").strip()[:80] or "app"
    session = (session or "").strip()[:128]
    user, err = auth.gate(bearer)
    if not user:
        return False, err
    # The threads it answers for (watching.black_covers): the one under it,
    # with the pinned ones for "pinned" (read again at each renewal, every
    # 30 s), none named for "all" — and none for an app that names no
    # thread, which reaches every thread as before. With no thread open,
    # "thread" and "pinned" answer for the pinned ones only.
    reach = reach or ("thread" if session else "all")
    sessions = None
    if reach != "all":
        sessions = [session] if session else []
        if reach == "pinned":
            sessions += sorted(pinned())
    try:
        until = watching.black(device, flag, sessions)
    except OSError as e:
        return False, {"error": f"could not save the lease ({e})", "status": 500}
    # Down, or up over another thread or with a narrower reach: a question
    # being read for a thread it no longer reaches can no longer be answered
    # by voice, so it is stopped and cued.
    try:
        from . import speech

        speech.recheck_question_soon()
    except Exception:  # noqa: BLE001
        pass
    return True, {"device": device, "on": bool(until), "until": until,
                  "reach": reach, "session": session}


def speech_default(bearer: str, level=None) -> tuple[bool, dict]:
    """`GET /speech/default` and `POST /speech/default {"level"}`: the speech
    level of every thread with none of its own (`speak_priority.default_level`).
    The server's, so every device's. With `level` None, only read."""
    from agent_media_core import speak_priority

    if level is not None and level not in speak_priority.LEVELS:
        return False, {"error": "level must be interrupt, auto, normal or quiet",
                       "status": 400}
    user, err = auth.gate(bearer)
    if not user:
        return False, err
    if level is not None:
        try:
            speak_priority.set_default(level)
        except OSError as e:
            return False, {"error": f"could not save the default ({e})", "status": 500}
    # `set`: whether anyone chose it, so the app can tell the server's own
    # "normal until set" from a level picked on purpose.
    return True, {"level": speak_priority.default_level(),
                  "set": speak_priority.default_is_set()}


def speech_balance(bearer: str, balance=None) -> tuple[bool, dict]:
    """`GET /speech/balance` and `POST /speech/balance {"balance"}`: how loud
    speech is next to music in the app, -100 (music louder) .. 100 (speech
    louder), applied at once (speech_balance.py). The server's, so every
    device's. With `balance` None, only read."""
    from agent_media_core import speech_balance as sb

    if balance is not None and (isinstance(balance, bool)
                                or not isinstance(balance, (int, float))
                                or not -sb.LIMIT <= balance <= sb.LIMIT):
        return False, {"error": "balance must be a number from -100 to 100",
                       "status": 400}
    user, err = auth.gate(bearer)
    if not user:
        return False, err
    if balance is not None:
        try:
            sb.set_balance(balance)
        except OSError as e:
            return False, {"error": f"could not save the balance ({e})", "status": 500}
        sb.apply_now()
    b = sb.balance()
    return True, {"balance": b, "speech": sb.speech_level(b), "music": sb.music_level(b)}


def speech_speed_reset(bearer: str, reset=None, start=None, per_thread=None, break_min=None, step=None) -> tuple[bool, dict]:
    """`GET /speech/speed-reset` and `POST /speech/speed-reset {"reset"?,
    "start"?, "per_thread"?, "break_min"?, "step"?}`: where the speech rate starts each
    reply (speech_speed.py): back to the start speed as each reply ends, the
    start speed, each thread's own, back after a break. The server's, so
    every device's. `step`: how far a faster or slower press moves, 0 the
    ladder. All None, only read."""
    from agent_media_core import speech_speed

    for name, v in (("reset", reset), ("per_thread", per_thread)):
        if v is not None and not isinstance(v, bool):
            return False, {"error": f"{name} must be true or false", "status": 400}
    if start is not None and (isinstance(start, bool) or not isinstance(start, (int, float)) or float(start) not in speech_speed.STARTS):
        return False, {"error": "start must be one of " + ", ".join(map(str, speech_speed.STARTS)), "status": 400}
    if break_min is not None and (isinstance(break_min, bool) or not isinstance(break_min, (int, float)) or int(break_min) != break_min or int(break_min) not in speech_speed.BREAKS):
        return False, {"error": "break_min must be one of " + ", ".join(map(str, speech_speed.BREAKS)), "status": 400}
    if step is not None and (isinstance(step, bool) or not isinstance(step, (int, float)) or (float(step) != 0 and float(step) not in speech_speed.STEPS)):
        return False, {"error": "step must be 0 or one of " + ", ".join(map(str, speech_speed.STEPS)), "status": 400}
    user, err = auth.gate(bearer)
    if not user:
        return False, err
    if any(v is not None for v in (reset, start, per_thread, break_min, step)):
        try:
            speech_speed.save_settings(reset=reset, start=start, per_thread=per_thread, break_min=break_min, step=step)
        except OSError as e:
            return False, {"error": f"could not save the setting ({e})", "status": 500}
    s = speech_speed.settings()
    return True, {**s, "starts": list(speech_speed.STARTS), "breaks": list(speech_speed.BREAKS), "steps": list(speech_speed.STEPS)}


_UNSET = object()


def speech_music_bed(bearer: str, choice=_UNSET, source=_UNSET,
                     style=None, settings=_UNSET) -> tuple[bool, dict]:
    """`GET /speech/music-bed` and `POST /speech/music-bed {"choice"}` or
    `{"source", "style"?}`: the music under a long reading
    (agent_media_core/music_bed.py) — "off", "auto" (generated, by time and
    weather), a generated mood or style, or "<source>:<style>" for the
    streaming and library sources, as the answer lists them; null forgets
    the choice (back to MEDIA_MUSIC_BED). `settings` `{id: value}` sets the
    switches the answer lists (`slow`: true, false, or null for
    MEDIA_MUSIC_BED_SLOW). The server's, so every device's. Without any,
    only read."""
    from agent_media_core import music_bed

    if source is not _UNSET:
        if source is None:
            choice = None
        else:
            choice = (music_bed.compose(source, style if isinstance(style, str) else None)
                      if isinstance(source, str) else None)
            if choice is None:
                return False, {"error": "source must be off, generated, streaming, library "
                                        "or favourites, "
                                        "with one of its styles", "status": 400}
    if choice is not _UNSET and choice is not None and music_bed.normalise(choice) is None:
        return False, {"error": "choice must be off, auto or one of the choices listed",
                       "status": 400}
    if settings is not _UNSET:
        # Each kind's value is checked by set_settings (a range's is a
        # number): checking for a boolean here turned every gain_db away
        # with a 400, and the app's slider jumped back.
        ids = {s["id"] for s in music_bed.settings()}
        if (not isinstance(settings, dict) or not settings
                or any(k not in ids for k in settings)):
            return False, {"error": "settings must be {id: value} for the "
                                    "settings listed", "status": 400}
    user, err = auth.gate(bearer)
    if not user:
        return False, err
    if settings is not _UNSET:
        try:
            music_bed.set_settings(settings)
        except ValueError as e:
            return False, {"error": str(e), "status": 400}
        except OSError as e:
            return False, {"error": f"could not save the music settings ({e})", "status": 500}
    if choice is not _UNSET:
        try:
            music_bed.set_choice(choice)
        except OSError as e:
            return False, {"error": f"could not save the music choice ({e})", "status": 500}
    return True, music_bed.status()


#: What the app may report of a song around a reply (`event`): its start,
#: the three ways it stops, and a turn of the volume.
SONG_EVENTS = ("started", "ended", "stopped", "skipped", "volume")


def _number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v


def speech_music_bed_song(bearer: str, key=None, verdict=None, event=None,
                          title=None, artist=None, video_id=None, kind=None,
                          pos_s=None, level=None, delta=None, log_id=None) -> tuple[bool, dict]:
    """`POST /speech/music-bed/song {"key", "verdict"}`: his word on a
    favourite sung out after a long reply (David, 31 Oct 2026: "The new
    singing it out feature is awesome!"). `like` records a like, so it ranks
    higher at the next gather; `dislike` drops it from the favourites for
    good, its files with it (music_bed_favourites.verdict). 400 for an
    unknown key or verdict.

    Since 8 Nov 2026 (David: "add it to the music history when you engage"
    — "maybe you can have its own recently played"; music_sung): `kept` is
    Keep playing, told by the app; it and `like` (and Start radio, POST
    /music/radio) put the song in the music history once, with its thread.
    `{"key", "event": "started"}` is the app's report that the song began
    singing out, for the Sung out list. `title`, `artist` and `video_id`
    may come with either, for a key the favourites no longer know.

    Since 14 Nov 2026 (David: "Maybe we need to keep a log of Sing it out";
    speech_music, /features `speech_music_log`) the app also says how it
    ended: `event` `ended` (it ran to its end), `stopped` (with `pos_s`),
    `skipped`, and `volume` (`level`, 0 to 100, or `delta`), which is noted
    on the song's row and moves nothing. `kind` says what played — `sing_out`
    (the default: what an app from before means), `tune` for the reply's
    tune (its `key` the hand-off entry's `id`), `bed`, `sing_in` — and
    `log_id`, a row's id from an earlier answer, names the row outright.
    Everything above is also written to the log: a start is a row, a
    verdict its outcome. The log is best-effort: a report whose row is not
    found is still answered 200, with `log_id` null."""
    from agent_media_core import music_bed_favourites as fav
    from agent_media_core import music_sung, speech_music

    if kind is not None and kind not in speech_music.KINDS:
        return False, {"error": "kind must be " + ", ".join(speech_music.KINDS), "status": 400}
    if event is not None and event not in SONG_EVENTS:
        return False, {"error": "event must be " + ", ".join(SONG_EVENTS), "status": 400}
    if event is None and verdict not in fav.VERDICTS + ("kept",):
        return False, {"error": "verdict must be like, dislike or kept", "status": 400}
    if log_id is not None and (not isinstance(log_id, int) or isinstance(log_id, bool)
                               or log_id <= 0):
        return False, {"error": "log_id must be a row's id", "status": 400}
    kind = kind or "sing_out"
    # A song sung out is known by its favourite's key, as before; the new
    # reports may name their row instead.
    logged = event is not None and not (event == "started" and kind == "sing_out")
    if not isinstance(key, str) or not key:
        if not (logged and log_id is not None and key is None):
            return False, {"error": "key must be a favourite's key", "status": 400}
    if pos_s is not None and (not _number(pos_s) or pos_s < 0):
        return False, {"error": "pos_s must be seconds, 0 or more", "status": 400}
    if event == "volume":
        if level is None and delta is None:
            return False, {"error": "volume needs a level (0 to 100) or a delta", "status": 400}
        if level is not None and not (_number(level) and 0 <= level <= 100):
            return False, {"error": "level must be 0 to 100", "status": 400}
        if delta is not None and not _number(delta):
            return False, {"error": "delta must be a number", "status": 400}
    user, err = auth.gate(bearer)
    if not user:
        return False, err
    said = {k: v for k, v in (("title", title), ("artist", artist), ("video_id", video_id))
            if isinstance(v, str) and v}
    match = log_id if log_id is not None else {"kind": kind, "track_key": key}
    try:
        if logged:
            return True, _song_logged(event, kind, key, match, said, pos_s, level, delta)
        if event == "started":
            e = music_sung.started(key, **said)
            row = speech_music.sung_out(key, title=e.get("title") or "",
                                        artist=e.get("artist") or "", session=e.get("session"))
            return True, {"key": key, "event": event, "session": e.get("session"),
                          "log_id": row or None}
        if verdict == "kept":
            music_sung.engaged(key, "kept", **said)
            speech_music.end(match, "kept")
            return True, {"key": key, "verdict": verdict}
        out = fav.verdict(key, verdict)
        speech_music.end(match, "liked" if verdict == "like" else "disliked")
        if verdict == "like":
            try:
                music_sung.engaged(key, "like", **said)
            except Exception as e:  # noqa: BLE001 — the like is what was asked
                print(f"pins: sung-out like not noted: {e}", file=sys.stderr)
        return True, out
    except ValueError as e:
        return False, {"error": str(e), "status": 400}
    except OSError as e:
        return False, {"error": f"could not save it ({e})", "status": 500}


def _song_logged(event: str, kind: str, key, match, said: dict, pos_s, level, delta) -> dict:
    """The reports that only the log takes (speech_music): how a song
    ended, a turn of its volume, and the start of anything but a Sing it
    out. The answer, with the row's id (None when none was found or
    written). A volume is a note on the row: no level anywhere is set."""
    from agent_media_core import speech_music

    if event == "started":
        # The server wrote the row when it asked a player (a tune, a bed);
        # the app's report says the phone began it. A song the server has
        # no row for gets one.
        row = speech_music.find(match, speech_music.SAME_S)
        if row is not None:
            done = speech_music.note(row["id"], {"what": "started"})
        elif isinstance(match, dict):
            done = speech_music.start(kind, track_key=key, title=said.get("title", ""),
                                      artist=said.get("artist", ""), source="app",
                                      why={"reported": "app"})
        else:
            done = 0
    elif event == "volume":
        what = {"what": "volume"}
        if level is not None:
            what["level"] = level
        if delta is not None:
            what["delta"] = delta
        if pos_s is not None:
            what["pos_s"] = pos_s
        done = speech_music.note(match, what)
    else:
        done = speech_music.end(match, event, pos_s)
    return {"key": key, "event": event, "kind": kind, "log_id": done or None}


#: `GET /speech/music/log`: how many rows by default, and at most.
MUSIC_LOG_DEFAULT = 50
MUSIC_LOG_MAX = 500


def speech_music_log(bearer: str, limit=None, kind=None, since=None) -> tuple[bool, dict]:
    """`GET /speech/music/log?limit=&kind=&since=`: the log of music around
    speech (speech_music; David, 14 Nov 2026: "Maybe we need to keep a log
    of Sing it out"), newest first — each bed, Sing it in, Sing it out and
    reply tune with its track, why it was chosen, how it was mixed and how
    it ended, and `thread_title`, the reply's thread's title where its
    history row has one. `kind` one of the four; `since` an epoch time.
    Gated like `POST /speech/music-bed`: titles are the conversations'."""
    from agent_media_core import speech_music

    try:
        n = int(limit) if limit not in (None, "") else MUSIC_LOG_DEFAULT
    except (TypeError, ValueError):
        return False, {"status": 400, "error": "limit is a number"}
    try:
        after = float(since) if since not in (None, "") else None
    except (TypeError, ValueError):
        return False, {"status": 400, "error": "since is a time in seconds"}
    kind = kind or None
    if kind is not None and kind not in speech_music.KINDS:
        return False, {"status": 400, "error": "kind must be " + ", ".join(speech_music.KINDS)}
    user, err = auth.gate(bearer)
    if not user:
        return False, err
    try:
        rows = speech_music.with_titles(
            speech_music.recent(max(1, min(MUSIC_LOG_MAX, n)), kind, after))
    except Exception as e:  # noqa: BLE001 — an empty list, not a 500
        print(f"speech/music/log: {e}", file=sys.stderr)
        rows = []
    return True, {"items": rows}


def speech_voice(bearer: str, mode=None, voice=None) -> tuple[bool, dict]:
    """`GET /speech/voice` and `POST /speech/voice {"mode", "voice"?}`: whether
    the current speech target's replies are rendered on the phone, by
    Android's TextToSpeech or Microsoft, or here (render/device.py), and in
    which voice. The server's, so every device's. With `mode` None, only
    read."""
    from agent_media_core import audio_targets
    from agent_media_core.render import device

    if mode is not None and mode not in device.MODES:
        return False, {"error": "mode must be phone or server", "status": 400}
    user, err = auth.gate(bearer)
    if not user:
        return False, err
    languages = device.languages()
    voices = [v for lang in languages for a in lang["accents"] for v in a["voices"]]
    # Google's aren't listed but are still taken (an app from before, a
    # choice already made), so they are looked up among them too.
    chosen = (device.find_voice(voice, voices + [dict(v) for v in device.GOOGLE_VOICES])
              if voice is not None else None)
    if voice is not None and not chosen:
        return False, {"error": "no such voice", "status": 400}
    if chosen and mode is not None and mode not in chosen["where"]:
        if mode == "server":
            return False, {"error": f"{chosen['label']} is only on the phone: "
                                    "choose a Microsoft voice for the server", "status": 400}
        return False, {"error": f"{chosen['label']} is only on the server: "
                                "choose a Microsoft voice for the phone", "status": 400}
    target = audio_targets.speech_default()
    if mode is not None:
        if not device.can_render(target):
            return False, {"error": f"{target} cannot render words", "status": 409}
        keep = voice or device.voice_for(target)
        if voice is None and keep and mode == "phone" and device.engine_voice(keep) \
                and not keep.startswith("edge:"):
            # Moving to the phone from a voice only the server has
            # (Kokoro, Piper): the phone's own default, not a voice it
            # cannot say.
            keep = None
        try:
            device.set_override(target, mode, keep)
        except OSError as e:
            return False, {"error": f"could not save the voice ({e})", "status": 500}
    return True, {"target": target,
                  "mode": "phone" if device.renders_on_device(target) else "server",
                  "voice": device.voice_for(target),
                  "can_phone": device.can_render(target),
                  "voices": voices,
                  "languages": languages,
                  "server_voice": device.server_voice(target)}


def settings_language(bearer: str, language=None, accents=None) -> tuple[bool, dict]:
    """`GET /settings/language` and `POST /settings/language {"language"}`: the
    app's language, site-wide (language.py): one the voices come in, or
    one known by name. With `language` None, only read."""
    from agent_media_core import language as site
    from agent_media_core.render import device

    user, err = auth.gate(bearer)
    if not user:
        return False, err
    # Those the voices come in, and those known by name (offline, the
    # built-in voices are English only), known names first.
    names = {lang["code"]: lang["name"] for lang in device.languages()}
    names.update(site.NAMES)
    languages = [{"code": code, "name": name} for code, name in
                 sorted(names.items(), key=lambda kv: (kv[0] != "en", kv[1].lower()))]
    # Each language with each country it has voices for — what Settings
    # picks, so Voice shows one country's voices, not a language's dozen.
    locales = [{"code": acc["locale"], "language": lang["code"],
                "name": f'{lang["name"]} ({acc["name"]})'}
               for lang in device.languages() for acc in lang["accents"]]
    if language is not None:
        want = site.normalise(str(language))
        known = {loc["code"] for loc in locales} | {lang["code"] for lang in languages}
        if want not in known:
            return False, {"error": "no such language", "status": 400}
        try:
            site.set_language(want)
        except OSError as e:
            return False, {"error": f"could not save the language ({e})", "status": 500}
    if accents is not None:
        # Several of one language's accents, whose voices Voice offers
        # together (David, 27 Sep 2026); the first is the locale.
        if not isinstance(accents, list) or not accents:
            return False, {"error": "accents: a list of at least one", "status": 400}
        codes = [site.normalise(str(a)) for a in accents]
        if any(c not in {loc["code"] for loc in locales} for c in codes):
            return False, {"error": "no such accent", "status": 400}
        try:
            site.set_accents(codes)
        except ValueError as e:
            return False, {"error": str(e), "status": 400}
        except OSError as e:
            return False, {"error": f"could not save the accents ({e})", "status": 500}
    return True, {"language": site.current(), "locale": site.locale(),
                  "accents": site.accents(), "languages": languages, "locales": locales}
