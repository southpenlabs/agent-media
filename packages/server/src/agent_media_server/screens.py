"""Devices that pause what is playing on them while someone talks to the
assistant or a reply is read out (docs/proposals/2026-10-30-screens-that-pause.md).

David, 30 Oct 2026: "how do we make this feature a little bit more
distributable? maybe we can have the option of assigning a media centre or
big screen". Any paired device can have the switch (`pause_here`, set from
an admin's app with `POST /devices/pause`), and it asks one question,
`GET /screen/now`: should I be paused now? The rule is decided here, so a
TV's app and a desktop helper only pause and resume:

- A phone's mic is open (§6.21e): pause at once.
- A voice is speaking that is not a thinking read-out, for SPEAK_HOLD_S: a
  beep alone (an earcon, at most ~1.8 s) is speaking too, and is no reason
  to pause (David, 30 Oct 2026: "spoken notifications are okay, just not
  for beeps alone"). Once paused, a gap between sentences does not count.
  What is playing longer than any beep (its `dur` over EARCON_MAX_S) is a
  voice, and pauses at once (David, 28 Nov 2026: "it was a little late to
  pause for speech"); a `dur` not yet known waits out the hold.
- Resumed RESUME_AFTER_S after both have stopped. A pause for a reply
  holds while the reply is still being read (its playback token held,
  submit.reply_reading, or a peer saying `reading`), so a gap between
  sentences longer than that does not resume and re-pause (David, 28 Nov
  2026: "it keeps cutting in and out with the slightest pause in the
  speech"); READING_HOLD_MAX_S of silence resumes anyway, so a wedged
  reply cannot hold a screen paused.

Another server's replies count too (28 Nov 2026, agent-mail #1440: hpo did
not pause for a reply in a thread red5 hosts, read through red5's own
server): a server paired here as a speaking peer (`pair --speaking`) posts
its speech state to `POST /screen/elsewhere` (`elsewhere`, sent by
speaking_peers.py there), and a fresh one that is speaking counts as this
host's own would. Its player is matched to a device here by the hash of its
app install id, else by name; unmatched, it counts as this host's own
players do. A state older than ELSEWHERE_FRESH_S is not speaking, so a lost
"stopped" never holds a screen paused.

Only for a phone that is **at home**, meaning on the screen's network: the
same public address as the screen's request, so the living-room TV does not
pause for David talking on the bus. A device reaching this server over the
tailnet shows a tailnet address; its network is then the public endpoint
the tailnet knows it by (`tailscale status`), a fallback only. Whose phones
count: the screen's `pause_for` list, or every member's and admin's device
when it is empty, so a guest's phone does not pause David's TV.
"""

from __future__ import annotations

import ipaddress
import json
import subprocess
import threading
import time
from typing import Callable

from agent_media_core import failures

from . import auth, devices, mic_state, speech_frames

SPEAK_HOLD_S = 2.2
#: The longest earcon; a clip longer than this is a voice, not a beep alone.
EARCON_MAX_S = 2.0
RESUME_AFTER_S = 1.5
#: The longest silence a reply still being read holds a screen paused.
READING_HOLD_MAX_S = 20.0
#: A phone last seen longer ago than this is not known to be anywhere.
SEEN_WITHIN_S = 900.0
_TAILNET = ipaddress.ip_network("100.64.0.0/10")
_TAILNET_TTL_S = 30.0

#: A speaking peer's state older than this is not speaking (it posts each
#: second while speaking, and once when it stops).
ELSEWHERE_FRESH_S = 3.0

_LOCK = threading.Lock()
_SCREENS: dict[str, dict] = {}
#: Speaking peer's device id → {"at" (monotonic), "speaking", "thinking",
#: "dur", "player": {"name", "install_hash"} | None}.
_ELSEWHERE: dict[str, dict] = {}
_PEERS: dict = {"at": None, "map": {}}


def _tailnet_peers() -> dict[str, str]:
    """Tailnet address → the public address that peer is reached at now."""
    try:
        out = subprocess.run(["tailscale", "status", "--json"], capture_output=True,
                             text=True, timeout=5).stdout
        data = json.loads(out or "{}")
    except (OSError, ValueError, subprocess.SubprocessError):
        return {}
    peers = {}
    for p in (data.get("Peer") or {}).values():
        cur = str(p.get("CurAddr") or "")
        host = cur.rsplit(":", 1)[0].strip("[]") if cur else ""
        for ip in p.get("TailscaleIPs") or []:
            if host:
                peers[str(ip)] = host
    return peers


def place(ip: str) -> str:
    """The network an address is on: a public address as itself, a tailnet
    one as its peer's public endpoint; "" when unknown (a loopback, a
    relayed peer, nothing)."""
    try:
        addr = ipaddress.ip_address((ip or "").strip())
    except ValueError:
        return ""
    if addr.is_loopback or addr.is_unspecified:
        return ""
    if addr in _TAILNET:
        now = time.monotonic()
        with _LOCK:
            if _PEERS["at"] is None or now - _PEERS["at"] > _TAILNET_TTL_S:
                _PEERS["map"] = _tailnet_peers()
                _PEERS["at"] = now
            return _PEERS["map"].get(str(addr), "")
    return str(addr)


def _counts(screen: dict, dev: dict | None) -> bool:
    """Whether this device's mic or voice can pause this screen."""
    if not dev or dev.get("id") == screen.get("id"):
        return False
    only = screen.get("pause_for") or []
    if only:
        return dev.get("id") in only
    return dev.get("role") != "guest"


def _seen_here(dev: dict | None, here: str) -> bool:
    if not dev:
        return False
    if time.time() - float(dev.get("last_seen") or 0) > SEEN_WITHIN_S:
        return False
    return place(str(dev.get("last_ip") or "")) == here


def _long(state: dict) -> bool:
    """Is what is speaking longer than any beep alone?"""
    try:
        return float(state.get("dur") or 0) > EARCON_MAX_S
    except (TypeError, ValueError):
        return False


# --- check-ins, for media doctor ------------------------------------------
#
# David, 28 Nov 2026 ("should that be part of agent media's doctor?"): hpo's
# old ducker was dead for two weeks and nothing said so. Every ask is a
# check-in; the desktop helper also names its version (X-Screen-Helper). Kept
# in <state>/screens-seen.json, written at most once a minute per device or
# when the version changes, so `media doctor` (another process) can read it.

SEEN_WRITE_S = 60.0
#: A pause device quiet this long is reported (asleep, or its helper stopped).
QUIET_S = 600.0
#: A helper this long behind the version the server hands out is not updating
#: (it checks every 6 h).
BEHIND_S = 8 * 3600.0
_SEEN: dict[str, dict] = {}
_SEEN_WRITTEN: dict[str, float] = {}


def seen_path():
    return devices._dir() / "screens-seen.json"


def _check_in(me: str, helper: str, now: float | None = None) -> None:
    t = time.time() if now is None else now
    with _LOCK:
        prev = _SEEN.get(me) or {}
        rec = {"at": round(t, 1), "helper": helper or prev.get("helper") or None}
        _SEEN[me] = rec
        if t - _SEEN_WRITTEN.get(me, 0.0) < SEEN_WRITE_S and rec["helper"] == prev.get("helper"):
            return
        _SEEN_WRITTEN[me] = t
        try:
            path = seen_path()
            try:
                disk = json.loads(path.read_text())
            except (OSError, ValueError):
                disk = {}
            disk = {**(disk if isinstance(disk, dict) else {}), me: rec}
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(disk))
            tmp.replace(path)
        except OSError:
            pass


def _first_word(name) -> str:
    """A device's or host's first word, lower case: "hpo screen" → "hpo"."""
    import re

    words = re.split(r"[^a-z0-9]+", str(name or "").lower())
    return next((w for w in words if w), "")


def _tailnet_status() -> dict:
    try:
        out = subprocess.run(["tailscale", "status", "--json"], capture_output=True,
                             text=True, timeout=5).stdout
        data = json.loads(out or "{}")
    except (OSError, ValueError, subprocess.SubprocessError):
        return {}
    return data if isinstance(data, dict) else {}


def _iso_ts(v) -> float:
    from datetime import datetime

    try:
        ts = datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0
    return ts if ts > 0 else 0.0


def _in_tailnet(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip) in _TAILNET
    except ValueError:
        return False


def computer_on(dev: dict, asked_at: float, now: float, *, tailnet=None,
                rows: list[dict] | None = None) -> str:
    """Why the computer a quiet pause device runs on is seen to be on since it
    last asked, or "" when nothing says so (asleep or off): its tailnet node
    online (or seen) after the ask, by its address or by the host its name
    starts with ("hpo screen" → hpo), or another of its devices (the same
    first word, or the same tailnet address) seen after the ask. Something must be
    seen at least QUIET_S after the last ask, so a computer that just went
    to sleep is not called on."""
    after = asked_at + QUIET_S
    host = _first_word(dev.get("name"))
    ip = str(dev.get("last_ip") or "")
    data = (tailnet or _tailnet_status)()
    for p in (data.get("Peer") or {}).values() if isinstance(data, dict) else []:
        ips = [str(x) for x in p.get("TailscaleIPs") or []]
        names = {_first_word(p.get("HostName")),
                 _first_word(str(p.get("DNSName") or "").split(".")[0])}
        if not ((ip and ip in ips) or (host and host in names)):
            continue
        if p.get("Online") and now >= after:
            return f"{p.get('HostName') or host} is online on the tailnet"
        seen = _iso_ts(p.get("LastSeen"))
        if seen >= after:
            return f"{p.get('HostName') or host} was on the tailnet {_ago(now - seen)} ago"
    for o in (devices.list_devices() if rows is None else rows):
        if o.get("id") == dev.get("id"):
            continue
        # The same address counts only on the tailnet (one per node): at home
        # every device shares the router's public one.
        same = (host and _first_word(o.get("name")) == host) or (
            ip and o.get("last_ip") == ip and _in_tailnet(ip))
        seen = float(o.get("last_seen") or 0)
        if same and seen >= after:
            return f"{o.get('name') or o.get('id')!r} was seen {_ago(now - seen)} ago"
    return ""


def doctor_lines(now: float | None = None, latest: tuple[str, float] | None = None,
                 tailnet=None) -> list[tuple[bool, str]]:
    """`(bad, line)` for each device with "Pause what's playing here" on:
    never asked, quiet for QUIET_S (a line, not a fault: a laptop sleeps),
    or a helper BEHIND_S behind the version handed out (a fault: its
    self-update is not working). `latest` is (version, since) of the
    helper the server hands out, from screen_dist."""
    t = time.time() if now is None else now
    try:
        seen = json.loads(seen_path().read_text())
    except (OSError, ValueError):
        seen = {}
    if latest is None:
        from . import screen_dist
        latest = screen_dist.latest()
    out: list[tuple[bool, str]] = []
    for d in devices.list_devices():
        if not d.get("pause_here"):
            continue
        name = d.get("name") or d.get("id")
        rec = seen.get(d.get("id")) if isinstance(seen, dict) else None
        if not rec:
            out.append((False, f"pause device {name!r}: has not asked since checks were kept "
                               "(an app or helper that never runs?)"))
            continue
        quiet = t - float(rec.get("at") or 0)
        if quiet >= QUIET_S:
            on = computer_on(d, float(rec.get("at") or 0), t, tailnet=tailnet)
            if on:
                # The computer is up and its helper is not asking: broken, not
                # asleep (agent-mail #1449: hpo's install was corrupted by a
                # crash, every start failed on import, and this said "asleep").
                out.append((True, f"pause device {name!r}: last asked {_ago(quiet)} ago, but "
                                  f"the computer is on ({on}): its helper is not running "
                                  "(on it: screen.log beside screen.json; media screen run "
                                  "shows why)"))
            else:
                out.append((False, f"pause device {name!r}: last asked {_ago(quiet)} ago "
                                   "(asleep or off, as far as can be seen)"))
        helper = rec.get("helper")
        # Behind only counts while it runs: asking BEHIND_S after the new
        # version came out and still on the old one. A computer left off for
        # a week is not a broken update (David, 28 Nov 2026: "hpo is left off
        # for about a week at a time").
        asked_at = float(rec.get("at") or 0)
        if helper and latest and helper not in ("unknown", latest[0]) \
                and asked_at - latest[1] >= BEHIND_S:
            out.append((True, f"pause device {name!r}: helper {helper} is behind {latest[0]} "
                              f"for {_ago(t - latest[1])}: its self-update is not working "
                              "(on it: media screen update)"))
    return out


def _ago(s: float) -> str:
    if s < 3600:
        return f"{s / 60:.0f} min"
    if s < 172800:
        return f"{s / 3600:.0f} h"
    return f"{s / 86400:.0f} days"


def _norm_name(name) -> str:
    return "".join(c for c in str(name or "").lower() if c.isalnum())


def elsewhere(bearer: str, body: dict) -> tuple[bool, dict]:
    """`POST /screen/elsewhere {speaking, thinking?, dur?, player?: {name,
    install_hash}}`: another server (a speaking peer, `pair --speaking`)
    says whether it is reading a reply out now. Any other token is refused
    like an unknown one."""
    dev = auth.speaking_peer(bearer)
    if not dev:
        return False, {"status": 401, "error": "a speaking peer only (pair --speaking)"}
    if not isinstance(body, dict):
        return False, {"status": 400, "error": "a JSON object"}
    player = body.get("player") if isinstance(body.get("player"), dict) else None
    try:
        dur = float(body["dur"]) if body.get("dur") is not None else None
    except (TypeError, ValueError):
        dur = None
    got = {"at": time.monotonic(), "speaking": body.get("speaking") is True,
           "thinking": body.get("thinking") is True, "dur": dur,
           "reading": body.get("reading") is True,
           "player": ({"name": str(player.get("name") or "")[:80],
                       "install_hash": str(player.get("install_hash") or "")[:64]}
                      if player else None)}
    with _LOCK:
        _ELSEWHERE[str(dev.get("id"))] = got
    return True, {}


def _elsewhere_now() -> list[dict]:
    """The speaking peers' fresh states that are a voice (speaking, not a
    thinking read-out)."""
    t = time.monotonic()
    with _LOCK:
        states = list(_ELSEWHERE.values())
    return [e for e in states if t - e["at"] <= ELSEWHERE_FRESH_S
            and e["speaking"] and not e["thinking"]]


def _elsewhere_reading() -> list[dict]:
    """The speaking peers' fresh states that say a reply is being read
    (between its sentences too)."""
    t = time.monotonic()
    with _LOCK:
        states = list(_ELSEWHERE.values())
    return [e for e in states if t - e["at"] <= ELSEWHERE_FRESH_S
            and e.get("reading") and not e["thinking"]]


def _peer_player(e: dict, rows: dict) -> dict | None:
    """The device here that plays a peer's voice: by install hash, then by
    name ignoring case and punctuation; None when neither matches."""
    p = e.get("player") or {}
    hit = devices.by_install_hash(p.get("install_hash") or "")
    if hit and hit in rows:
        return rows[hit]
    name = _norm_name(p.get("name"))
    if name:
        same = [d for d in rows.values() if _norm_name(d.get("name")) == name]
        if len(same) == 1:
            return same[0]
    return None


def _reply_reading() -> bool:
    from agent_media_core.intake import submit

    return submit.reply_reading()


def now(bearer: str, state: dict, helper: str = "",
        reading_here: Callable[[], bool] = _reply_reading) -> tuple[bool, dict]:
    """`GET /screen/now` → `{on, pause, why}`: whether the asking device
    should be paused now. `state` is the speech snapshot (/speech/now's);
    `helper` the desktop helper's version (its X-Screen-Helper header)."""
    user, err = auth.gate(bearer)
    me = auth.device_id(bearer) if user else ""
    if not me:
        return False, err or {"status": 401, "error": "a paired device only"}
    _check_in(me, helper[:40])
    rows = {d.get("id"): d for d in devices.list_devices()}
    screen = rows.get(me) or {}
    if not screen.get("pause_here"):
        with _LOCK:
            _SCREENS.pop(me, None)
        return True, {"on": False, "pause": False, "why": None}
    here = place(auth.client_ip())
    mic = voice = long_ = reading = False
    if here:
        mic = any(_counts(screen, rows.get(d)) and place(ip) == here
                  for d, ip in mic_state.open_mics().items())

        def heard(player: dict | None) -> bool:
            if player:
                return _counts(screen, player) and _seen_here(player, here)
            # This host's own players (the rooms), or a peer's we cannot
            # place: heard wherever they are, so it counts while one of the
            # screen's phones is home.
            return any(_counts(screen, d) and _seen_here(d, here) for d in rows.values())

        if state.get("speaking") and not state.get("thinking"):
            player = speech_frames.playing_device("speech")
            if (bool(rows.get(player)) and heard(rows[player])) if player else heard(None):
                voice, long_ = True, _long(state)
        for e in _elsewhere_now():
            if heard(_peer_player(e, rows)):
                voice = True
                long_ = long_ or _long(e)
        if not voice:
            try:
                reading = bool(reading_here()) and heard(None)
            except Exception as e:  # noqa: BLE001 - unknown is the voice alone
                failures.swallowed(e)
            reading = reading or any(heard(_peer_player(e, rows))
                                     for e in _elsewhere_reading())
    t = time.monotonic()
    with _LOCK:
        st = _SCREENS.setdefault(me, {"speak_since": None, "paused": False,
                                      "quiet_since": None, "why": None})
        why = "mic" if mic else None
        if voice:
            if st["speak_since"] is None:
                st["speak_since"] = t
            if st["paused"] or t - st["speak_since"] >= SPEAK_HOLD_S or long_:
                why = why or "reply"
        else:
            st["speak_since"] = None
        if why:
            st.update(paused=True, quiet_since=None, why=why)
        elif st["paused"]:
            if st["quiet_since"] is None:
                st["quiet_since"] = t
            hold = (READING_HOLD_MAX_S if reading and st["why"] == "reply"
                    else RESUME_AFTER_S)
            if t - st["quiet_since"] >= hold:
                st.update(paused=False, quiet_since=None, why=None)
        return True, {"on": True, "pause": st["paused"], "why": st["why"]}


def _reset() -> None:
    """Tests: forget every screen's state and the tailnet map."""
    with _LOCK:
        _SCREENS.clear()
        _ELSEWHERE.clear()
        _PEERS.update(at=None, map={})
