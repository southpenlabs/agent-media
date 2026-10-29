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
- Resumed RESUME_AFTER_S after both have stopped.

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

from . import auth, devices, mic_state, speech_frames

SPEAK_HOLD_S = 2.2
RESUME_AFTER_S = 1.5
#: A phone last seen longer ago than this is not known to be anywhere.
SEEN_WITHIN_S = 900.0
_TAILNET = ipaddress.ip_network("100.64.0.0/10")
_TAILNET_TTL_S = 30.0

_LOCK = threading.Lock()
_SCREENS: dict[str, dict] = {}
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


def now(bearer: str, state: dict) -> tuple[bool, dict]:
    """`GET /screen/now` → `{on, pause, why}`: whether the asking device
    should be paused now. `state` is the speech snapshot (/speech/now's)."""
    user, err = auth.gate(bearer)
    me = auth.device_id(bearer) if user else ""
    if not me:
        return False, err or {"status": 401, "error": "a paired device only"}
    rows = {d.get("id"): d for d in devices.list_devices()}
    screen = rows.get(me) or {}
    if not screen.get("pause_here"):
        with _LOCK:
            _SCREENS.pop(me, None)
        return True, {"on": False, "pause": False, "why": None}
    here = place(auth.client_ip())
    mic = voice = False
    if here:
        mic = any(_counts(screen, rows.get(d)) and place(ip) == here
                  for d, ip in mic_state.open_mics().items())
        if state.get("speaking") and not state.get("thinking"):
            player = speech_frames.playing_device("speech")
            if player:
                voice = _counts(screen, rows.get(player)) and _seen_here(rows.get(player), here)
            else:
                # This host's own players (the rooms): heard wherever they
                # are, so it counts while one of the screen's phones is home.
                voice = any(_counts(screen, d) and _seen_here(d, here) for d in rows.values())
    t = time.monotonic()
    with _LOCK:
        st = _SCREENS.setdefault(me, {"speak_since": None, "paused": False,
                                      "quiet_since": None, "why": None})
        why = "mic" if mic else None
        if voice:
            if st["speak_since"] is None:
                st["speak_since"] = t
            if st["paused"] or t - st["speak_since"] >= SPEAK_HOLD_S:
                why = why or "reply"
        else:
            st["speak_since"] = None
        if why:
            st.update(paused=True, quiet_since=None, why=why)
        elif st["paused"]:
            if st["quiet_since"] is None:
                st["quiet_since"] = t
            if t - st["quiet_since"] >= RESUME_AFTER_S:
                st.update(paused=False, quiet_since=None, why=None)
        return True, {"on": True, "pause": st["paused"], "why": st["why"]}


def _reset() -> None:
    """Tests: forget every screen's state and the tailnet map."""
    with _LOCK:
        _SCREENS.clear()
        _PEERS.update(at=None, map={})
