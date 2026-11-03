"""A device that follows a phone: the thread opened on the phone opens there
too (server-contract.md §6.21g; roadmap item 52).

David, 3 Nov 2026: "using the remote for sasonica is a bit cumbersome" — he
types and talks on the phone and watches the living-room TV. Not screen
mirroring: the following device runs its own app and is told which thread
to show; its own thread page keeps the reply being read in view.

- The switch is kept with the device record (`follow`: the phone's device
  id, absent or null when off), set from an admin's app with
  `POST /devices/follow {"id", "phone"}` (Settings → Devices, beside "Pause
  what's playing here"). The following device turns itself off with
  `POST /device/follow {"on": false}`: a press on its own remote or screen.
- What a phone has open is the thread it opened a stream to last
  (`GET /threads/{session}/events`, recorded by `opened`): the app opens one
  for the thread on its screen and nothing else, and a reconnect of the same
  thread is no change.
- The following device hears it as a `follow` frame on the stream it
  already holds outbound, `GET /sessions/events?follow=1` (session_events.py),
  and asks `GET /device/follow` for its state when it has no stream.

Kept in memory: a restart forgets what each phone had open (the next open
tells it again); the switch itself is in devices.json.
"""

from __future__ import annotations

import threading
import time

from . import auth, devices

_LOCK = threading.Lock()
#: {device id: {"session", "at"}} — the thread each device opened last.
_OPEN: dict[str, dict] = {}
_VERSION = 0


def version() -> int:
    """Goes up whenever a device opens a different thread or a switch changes:
    the session stream's watcher sends `follow` frames again."""
    with _LOCK:
        return _VERSION


def _bump() -> None:
    global _VERSION
    with _LOCK:
        _VERSION += 1
    from . import session_events

    session_events.poke()


def opened(device_id: str, session: str) -> None:
    """`device_id` opened `session`'s stream (app.py `_thread_events`)."""
    if not device_id or not session:
        return
    with _LOCK:
        was = _OPEN.get(device_id)
        if was and was["session"] == session:
            return
        _OPEN[device_id] = {"session": session, "at": round(time.time(), 3)}
    _bump()


def changed() -> None:
    """A switch was set or cleared (`POST /devices/follow`, `/device/follow`)."""
    _bump()


def state(device_id: str) -> dict:
    """What `device_id` follows: `{"phone", "name", "session", "title", "at"}`,
    every field null when its switch is off (or names a device since revoked).
    `session` is null until the phone opens a thread after a restart."""
    off = {"phone": None, "name": None, "session": None, "title": None, "at": None}
    if not device_id:
        return off
    rows = {d.get("id"): d for d in devices.list_devices()}
    phone = str((rows.get(device_id) or {}).get("follow") or "")
    if not phone or phone not in rows:
        return off
    with _LOCK:
        got = dict(_OPEN.get(phone) or {})
    session = got.get("session")
    title = None
    if session:
        from . import sessions

        try:
            title = sessions.title_of(session) or None
        except Exception:  # noqa: BLE001 — a title is never worth the frame
            title = None
    return {"phone": phone, "name": rows[phone].get("name") or None, "session": session,
            "title": title, "at": got.get("at")}


def get(bearer: str) -> tuple[bool, dict]:
    """`GET /device/follow` — any paired device, for itself."""
    me = auth.device_id(bearer)
    if not me:
        return False, {"status": 401, "error": "a paired device only"}
    return True, state(me)


def stop(bearer: str, body) -> tuple[bool, dict]:
    """`POST /device/follow {"on": false}` — a device turns its own following
    off (a key press on it). Turning it on is Settings → Devices'
    (`POST /devices/follow`), so `on: true` is refused."""
    me = auth.device_id(bearer)
    if not me:
        return False, {"status": 401, "error": "a paired device only"}
    if not isinstance(body, dict) or body.get("on") is not False:
        return False, {"status": 400, "code": "bad_follow",
                       "error": 'expected {"on": false}; it is turned on from Settings → Devices'}
    devices.set_follow(me, None)
    changed()
    return True, state(me)


def _reset() -> None:
    """Tests: forget what every device had open (the version only goes up)."""
    with _LOCK:
        _OPEN.clear()
