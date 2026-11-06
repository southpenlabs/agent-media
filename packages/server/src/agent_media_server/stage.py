"""Now playing on a big screen (server-contract.md §6.21h; roadmap item 57).

David, 6 Nov 2026, asked about "assigning now playing to a large screen
like a TV or projector", and chose both ways of doing it:

- **Sent from the phone** (`stage` "on"): Now playing's "Show on…" key
  sends it to a paired screen, which opens Now playing full screen and stays
  on it, whatever the phone does next; the phone stays the remote. Any
  paired device of the same person may send it or stop it
  (`POST /devices/stage {"id", "stage": "on"|"off"}`), and the screen stops
  it itself with a press on its own remote (`POST /device/stage
  {"on": false}`).
- **A screen set to always show it** (`stage` "auto"): Settings → Devices,
  per device, "Now playing: Always". The screen shows Now playing whenever
  something plays — a reply live (`/speech/now`'s `live`) or music playing
  (`GET /music`) — and is dark otherwise. Set as the mirroring switch is,
  by a device that may manage devices (`POST /devices/stage {"id",
  "auto": true|false}`, or `"stage": "auto"`).

A sent stage wins while it is on; when it ends the screen goes back to
"auto" if it is set to always show it, else off. Mirroring (follow.py,
§6.21g) is the app's to set aside while the screen is on stage.

Kept with the device record in devices.json: `stage_auto` (bool),
`stage_by` (the device that sent it, null when not sent) and `stage_at`.
Every `/devices` row carries `stage`, the mode now ("on", "auto", "off").
The screen hears its stage in the `follow` frame it already holds
(`GET /sessions/events?follow=1`) and in `GET /device/follow`, as the
`stage` object `state()` makes.
"""

from __future__ import annotations

from . import auth, devices

MODES = ("off", "on", "auto")


def mode_of(row: dict | None) -> str:
    """The stage mode a device row is in: sent ("on") wins over "auto"."""
    return devices.stage_mode(row)


def playing() -> dict:
    """What plays now, as `/speech/now` and `GET /music` would say it:
    `{"reply": a voice live on a reply (speaking or paused), "music": a
    track playing, not paused}`. Each a cheap local look (the speech
    snapshot; music's own 2.5 s cache), false when it cannot be read."""
    out = {"reply": False, "music": False}
    try:
        from . import speech

        s = speech.current_state()
        # A thinking line or a notification read out is not a reply (the
        # app's Now playing and hpo's ducker leave both alone too).
        out["reply"] = bool(s.get("speaking") or s.get("paused")) \
            and not s.get("thinking") and not s.get("notif")
    except Exception:  # noqa: BLE001 — a failed read is "nothing"
        pass
    try:
        from . import music

        now = music._now()[0]
        out["music"] = now.get("pos_ms") is not None and now.get("paused") is False
    except Exception:  # noqa: BLE001
        pass
    return out


def _decide(row: dict, rows: dict) -> dict:
    mode = mode_of(row)
    by = row.get("stage_by") or None
    out = {"mode": mode, "auto": bool(row.get("stage_auto")), "by": by,
           "by_name": (rows.get(by) or {}).get("name") or None if by else None,
           "at": row.get("stage_at") if by else None, "live": False, "why": None}
    if mode == "on":
        out["live"], out["why"] = True, "sent"
    elif mode == "auto":
        p = playing()
        why = "reply" if p["reply"] else "music" if p["music"] else None
        out["live"], out["why"] = why is not None, why
    return out


def state(device_id: str) -> dict:
    """This device's stage: `{"mode", "auto", "by", "by_name", "at", "live",
    "why"}`. `mode` "on" (sent: `by` the device that sent it, `by_name` its
    name, `at` when), "auto" (shows Now playing whenever something plays) or
    "off"; `auto` whether it is set to always show it (still true under a
    sent stage, which goes back to it); `live` whether it should show Now
    playing now, and `why`: "sent", "reply" or "music" (null when not)."""
    off = {"mode": "off", "auto": False, "by": None, "by_name": None, "at": None,
           "live": False, "why": None}
    if not device_id:
        return off
    rows = {d.get("id"): d for d in devices.list_devices()}
    me = rows.get(device_id)
    return _decide(me, rows) if me else off


def with_live(rows: list[dict]) -> list[dict]:
    """`/devices` rows with `stage_live`: whether each shows Now playing now
    (what is playing is read once, and only when a row is on "auto")."""
    p = None
    out = []
    for d in rows:
        mode = d.get("stage") or mode_of(d)
        live = mode == "on"
        if mode == "auto":
            p = p if p is not None else playing()
            live = p["reply"] or p["music"]
        out.append({**d, "stage_live": live})
    return out


def live_key() -> tuple:
    """For the session stream's watcher, read every tick: what plays, while
    some device is on "auto" (a change wakes the streams, so an "auto"
    screen hears that something started); () otherwise, so nothing is read
    for a server with no such screen."""
    if not any(mode_of(d) == "auto" for d in devices.list_devices()):
        return ()
    p = playing()
    return (p["reply"], p["music"])


def _changed() -> None:
    from . import follow

    follow.changed()


def _caller(bearer: str) -> tuple[dict | None, dict]:
    """The sending device's user (`auth.gate`), or an error: a paired device
    only, and not a guest (a guest reads and listens)."""
    user, err = auth.gate(bearer)
    if not user:
        return None, {"status": err.get("status", 401), "code": err.get("code"),
                      "error": err.get("error", "not allowed")}
    if not user.get("device"):
        return None, {"status": 403, "code": "not_a_device", "error": "a paired device only"}
    if user.get("role") == "guest":
        return None, {"status": 403, "code": "guest",
                      "error": "a guest may read and listen, not send to a screen"}
    return user, {}


def _same_person(user: dict, row: dict) -> bool:
    return auth.device_account(row) == str(user.get("account_key") or "")


def screens(bearer: str) -> tuple[bool, dict]:
    """`GET /devices/stage` — the screens this device may send Now playing
    to: every other paired device of the same person, each `{id, name,
    role, last_seen, stage, stage_live, by, by_name}`, the sheet behind Now
    playing's "Show on…" key."""
    user, err = _caller(bearer)
    if not user:
        return False, err
    me = user.get("device")
    rows = devices.list_devices()
    named = {d.get("id"): d.get("name") for d in rows}
    mine = [d for d in rows if d.get("id") != me and _same_person(user, d)]
    out = []
    for d in with_live(mine):
        by = d.get("stage_by") or None
        out.append({"id": d.get("id"), "name": d.get("name") or d.get("id"),
                    "role": d.get("role"), "last_seen": d.get("last_seen"),
                    "stage": d.get("stage"), "stage_live": d.get("stage_live"),
                    "auto": bool(d.get("stage_auto")),
                    "by": by, "by_name": named.get(by) if by else None})
    return True, {"self": me, "screens": out}


def send(bearer: str, body) -> tuple[bool, dict]:
    """`POST /devices/stage {"id", "stage": "on"|"off"}` — Now playing sent
    to a screen, or taken off it: any paired device of the same person (not
    a guest). `{"id", "auto": true|false}` (or `"stage": "auto"`) — the
    screen set to show Now playing whenever something plays, or not: a
    device that may manage devices, as `/devices/follow`."""
    user, err = _caller(bearer)
    if not user:
        return False, err
    b = body if isinstance(body, dict) else {}
    device_id = str(b.get("id") or "").strip()
    stage, auto = b.get("stage"), b.get("auto")
    if stage == "auto":
        stage, auto = None, True if auto is None else auto
    if not device_id or (stage is None and auto is None) \
            or (stage is not None and stage not in ("on", "off")) \
            or (auto is not None and not isinstance(auto, bool)):
        return False, {"status": 400, "code": "bad_stage",
                       "error": 'expected {"id", "stage": "on"|"off"|"auto"} or {"id", "auto": bool}'}
    rows = {d.get("id"): d for d in devices.list_devices()}
    row = rows.get(device_id)
    if row is None:
        return False, {"status": 404, "code": "no_such_device", "error": "no device with that id"}
    if auto is not None and not user.get("enrol"):
        return False, {"status": 403, "code": "not_enrolled",
                       "error": "this device may not manage other devices"}
    if stage is not None and not _same_person(user, row):
        return False, {"status": 403, "code": "not_yours",
                       "error": "that screen is someone else's"}
    kw: dict = {}
    if stage == "on":
        kw["by"] = user.get("device")
    elif stage == "off":
        kw["by"] = None
    if auto is not None:
        kw["auto"] = auto
    devices.set_stage(device_id, **kw)
    _changed()
    return True, {"id": device_id, "stage": state(device_id)}


def stop(bearer: str, body) -> tuple[bool, dict]:
    """`POST /device/stage {"on": false}` — a screen takes a sent Now playing
    off itself (a press on its own remote or screen). It goes back to "auto"
    if it is set to always show it. Sending it is the phone's
    (`POST /devices/stage`), so `on: true` is refused."""
    me = auth.device_id(bearer)
    if not me:
        return False, {"status": 401, "error": "a paired device only"}
    if not isinstance(body, dict) or body.get("on") is not False:
        return False, {"status": 400, "code": "bad_stage",
                       "error": 'expected {"on": false}; it is sent from another device'}
    devices.set_stage(me, by=None)
    _changed()
    return True, state(me)


def get(bearer: str) -> tuple[bool, dict]:
    """`GET /device/stage` — any paired device, for itself."""
    me = auth.device_id(bearer)
    if not me:
        return False, {"status": 401, "error": "a paired device only"}
    return True, state(me)

