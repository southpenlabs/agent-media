"""Limits a paired device can carry, enforced here (server-contract.md §6.59a).

David, 20 Nov 2026, after the limits a paired device could carry were
listed: "Great ideas, let's implement all of them" (agent mail #1106 and
#1107 from sasonica-app). Five, each optional, set per device:

- **Expiry** (`expires_at` on the row; `POST /devices/expiry`, and
  `expires_at` on `POST /devices/code` and `POST /pair/approve`): when it
  passes the device is revoked, and its token answers 401 `device_expired`
  on every request for as long as the marker is kept (devices.py).
- **Forget it when unused** (rule `forget_unused`: 30d, 90d, never): a
  device unseen that long is revoked and logged (devices.sweep).
- **Plays on the speakers** (rule `speakers`): false, and nothing it asks
  may sound anywhere but on itself: 403 `no_speakers` (`speaker_refusal`).
- **Only at home** (rule `home_network_only`): true, and a request that does
  not come straight from the home network is 403 `off_network`
  (`at_home`).
- **Only some chats** (`scope` on the row; `POST /devices/scope`): the
  thread lists leave out threads outside it (device_kinds.filter_rows) and
  a per-thread route answers 404 as for a thread that does not exist
  (`thread_refusal`).

`check` is the one call in front of the app's routes (app.dispatch).
"""

from __future__ import annotations

import ipaddress
import os
import re

#: The scope entry for chats with people (Matrix rooms, bridged chats, the
#: phone's Local chats), beside the projects.
PEOPLE = "people"

#: Routes that work whatever the limits: pairing, which carries no device
#: token, and what the app reads to explain a refusal.
_FREE = frozenset({"/pair", "/enrol", "/enrol/wait", "/pair/request", "/pair/wait",
                   "/features", "/device/rules"})


# --- only at home --------------------------------------------------------------------

def home_networks() -> list:
    """The networks that are "home": `MEDIA_HOME_NETWORKS` (comma-separated
    CIDRs) when set, else the private LAN ranges. Loopback is never home: a
    request through the tunnel or the relay arrives from this machine."""
    raw = (os.environ.get("MEDIA_HOME_NETWORKS") or "").strip()
    nets = []
    for part in raw.split(","):
        try:
            nets.append(ipaddress.ip_network(part.strip(), strict=False))
        except ValueError:
            continue
    return nets


_FORWARDED = ("X-Forwarded-For", "CF-Connecting-IP", "X-Real-IP", "Forwarded")


def at_home(ip: str, headers=None) -> bool:
    """Whether a request from `ip` (with these headers) came straight from
    the home network: a LAN address, not loopback, and no proxy's header
    saying it came from elsewhere."""
    if headers is not None and any(headers.get(k) for k in _FORWARDED):
        return False
    try:
        addr = ipaddress.ip_address((ip or "").split("%")[0])
    except ValueError:
        return False
    if getattr(addr, "ipv4_mapped", None):
        addr = addr.ipv4_mapped
    if addr.is_loopback:
        return False
    nets = home_networks()
    if nets:
        return any(addr in n for n in nets if n.version == addr.version)
    # Private LAN ranges only: not the tailnet's 100.64/10 (shared address
    # space, not private), so the tailnet is not home either.
    return addr.is_private


# --- plays on the speakers -----------------------------------------------------------

def _rule(device: dict | None, rule: str):
    from . import device_kinds

    return (device_kinds.rules_for(device).get(rule) or {}).get("value")


def _plays_here(device: dict, channel: str) -> bool:
    """Whether `channel` (speech, music) goes to this device's own player."""
    from . import speech_frames
    from agent_media_core import audio_targets

    try:
        where = (audio_targets.music_pref() if channel == "music"
                 else audio_targets.speech_override() or audio_targets.speech_default())
    except Exception as e:  # noqa: BLE001 — unknown is not here
        from agent_media_core import failures

        failures.swallowed(e)
        return False
    return where == "sasonica" and speech_frames.output_device(channel) == device.get("id")


#: What sounds somewhere: the music actions that start or move playback,
#: and the speech bar's verbs that play a reply again.
_MUSIC_SOUNDS = frozenset({"play", "add", "radio", "resume", "toggle", "next", "prev", "mood"})
_SPEECH_SOUNDS = frozenset({"toggle", "prev", "replay", "replay-unheard", "replay-id", "again",
                            "goto-sentence", "say"})


def speaker_refusal(device: dict | None, path: str, body: dict) -> dict | None:
    """A 403 `no_speakers` when a device without the speakers sends sound
    anywhere but to itself; None otherwise."""
    if not device or _rule(device, "speakers") is not False:
        return None
    own = f"device:{device.get('id')}"
    bad = False
    if path == "/audio/target":
        bad = body.get("target") != own
    elif path in ("/music", "/music/radio", "/music/brainz"):
        action = "radio" if path == "/music/radio" else (
            "play" if path == "/music/brainz" else str(body.get("action") or ""))
        bad = action in _MUSIC_SOUNDS and not _plays_here(device, "music")
    elif path in ("/speech/ctl", "/speech/say"):
        action = "say" if path == "/speech/say" else str(body.get("action") or "")
        bad = action in _SPEECH_SOUNDS and not _plays_here(device, "speech")
    if not bad:
        return None
    return {"status": 403, "code": "no_speakers",
            "error": "this device may not play on the house speakers or rooms"}


# --- only some chats -----------------------------------------------------------------

def scope_of(device: dict | None) -> set[str] | None:
    """The scope ids this device is limited to, or None for everything."""
    from . import devices

    got = devices.scope_field(device)
    return set(got["projects"]) if got else None


def choices() -> list[dict]:
    """`GET /devices` `scope_choices`: the projects, then chats with people."""
    from agent_media_core import layout

    try:
        names = sorted(layout._index()["projects"], key=str.lower)
    except Exception as e:  # noqa: BLE001 — no layout is no projects
        from agent_media_core import failures

        failures.swallowed(e)
        names = []
    return [{"id": n, "name": n} for n in names] + [{"id": PEOPLE, "name": "Chats with people"}]


def _is_people(session: str, row: dict | None = None) -> bool:
    if row is not None and (row.get("room") or row.get("local") or row.get("harness") == "matrix"):
        return True
    from . import local_chats, matrix

    try:
        if local_chats.is_local(session):
            return True
        return matrix.room(session) is not None
    except Exception as e:  # noqa: BLE001 — not a room as far as anyone can tell
        from agent_media_core import failures

        failures.swallowed(e)
        return False


def thread_scope(session: str, row: dict | None = None) -> str | None:
    """The scope id a thread falls under: "people", its project, or None
    (nothing says; outside every scope but "everything")."""
    if _is_people(session, row):
        return PEOPLE
    if row is not None and row.get("project"):
        return str(row["project"])
    from agent_media_core import layout

    from . import moves, sessions

    try:
        return (layout.canonical_project(moves.moved_project(session))
                or sessions.project_of(sessions.session_cwd(session),
                                       sessions._shelf_folders().get(session, "")))
    except Exception as e:  # noqa: BLE001 — unknown is outside the scope
        from agent_media_core import failures

        failures.swallowed(e)
        return None


def in_scope(device: dict | None, session: str, row: dict | None = None) -> bool:
    scope = scope_of(device)
    return scope is None or thread_scope(session, row) in scope


def filter_scope(rows: list, device: dict | None) -> list:
    """`rows` (each with `session`) without the threads outside the scope."""
    scope = scope_of(device)
    if scope is None:
        return rows
    return [r for r in rows if isinstance(r, dict) and r.get("session")
            and thread_scope(str(r["session"]), r) in scope]


_THREAD_PATH = re.compile(r"/threads/([^/]+)/")
_SESSION_KEYS = ("session", "thread")


def threads_named(path: str, query: dict, body: dict | None) -> list[str]:
    """The threads a request names: `?session=`/`?thread=`, the thread in a
    `/threads/<id>/…` path, and `session`/`thread` in a JSON body."""
    out = [v for k in _SESSION_KEYS for v in query.get(k) or [] if v]
    m = _THREAD_PATH.match(path + "/")
    if m:
        out.append(m.group(1))
    if isinstance(body, dict):
        out += [str(body[k]) for k in _SESSION_KEYS if isinstance(body.get(k), str) and body[k]]
    return out


def thread_refusal(device: dict | None, threads: list[str]) -> dict | None:
    """404, as for a thread that does not exist, when any named thread is
    outside this device's scope."""
    if scope_of(device) is None:
        return None
    if all(in_scope(device, t) for t in threads):
        return None
    return {"status": 404, "code": "no_such_session", "error": "no such thread"}


# --- the one check -------------------------------------------------------------------

def needs_body(device: dict | None, method: str, path: str) -> bool:
    """Whether `check` must see this request's JSON body."""
    if method != "POST" or device is None or path in _FREE:
        return False
    return scope_of(device) is not None or (
        _rule(device, "speakers") is False and path in (
            "/audio/target", "/music", "/music/radio", "/music/brainz", "/speech/ctl", "/speech/say"))


def check(bearer: str, method: str, path: str, ip: str, headers, query: dict,
          body: dict | None) -> dict | None:
    """The refusal (with `status`) a device's limits give this request, or
    None to go on. Run before every app route."""
    from . import auth, devices

    if method == "OPTIONS" or path in _FREE or not bearer:
        return None
    device = auth._device(bearer)
    if device is None:
        if devices.expired(bearer):
            return {"status": 401, "code": "device_expired",
                    "error": "this device's access ran out"}
        return None
    if _rule(device, "home_network_only") is True and not at_home(ip, headers):
        return {"status": 403, "code": "off_network",
                "error": "this device may only be used at home"}
    if method == "POST" and isinstance(body, dict):
        refused = speaker_refusal(device, path, body)
        if refused:
            return refused
    return thread_refusal(device, threads_named(path, query, body))
