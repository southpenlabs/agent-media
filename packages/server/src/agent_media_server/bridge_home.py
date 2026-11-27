"""Where a new bridge account should run: this server or the phone (§6.26).

The app picks it rather than asking each time (David, 27 Nov 2026: it
"basically depends on whether the host server is on all the time or not"),
so GET /bridges says two things this module decides:

- `host()`: whether this server is meant to run around the clock. From
  `MEDIA_HOST_ALWAYS_ON` when set; else from what the machine shows: a
  sleep since it started, a laptop's chassis, a virtual machine or a
  server's chassis, or days up without sleeping. None when none of those
  says (a desktop up only a short while), and the app keeps asking.
- `prefer(network)`: a network that objects to sign-ins from a server's
  address (Meta's flag them as suspicious) prefers the phone even on an
  always-on server; None for the others.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Callable, Mapping, Optional

# SMBIOS chassis types (/sys/class/dmi/id/chassis_type).
_PORTABLE = {"8", "9", "10", "11", "14", "30", "31", "32"}  # portable, laptop, notebook, handheld, sub-notebook, tablet, convertible, detachable
_SERVER = {"17", "23", "25", "28", "29"}  # main server chassis, rack mount, multi-system, blade, blade enclosure
_UP_DAYS = 3

# Networks whose services flag a sign-in from a data-centre or home-server
# address; the phone's own address is the one they already know.
_PHONE_FIRST = {"facebook", "facebookgo", "messenger", "meta", "instagram", "instagramgo"}
_PHONE_WHY = "Meta may flag a sign-in from a server's address as suspicious"


def _read(path: str) -> str:
    try:
        return Path(path).read_text().strip()
    except OSError:
        return ""


def _virt() -> str:
    try:
        out = subprocess.run(["systemd-detect-virt"], capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.SubprocessError):
        return ""
    v = out.stdout.strip()
    return "" if v == "none" else v


def host(env: Optional[Mapping[str, str]] = None,
         read: Callable[[str], str] = _read,
         virt: Callable[[], str] = _virt) -> Optional[dict]:
    """{"always_on": bool, "why": str} or None when it cannot be told."""
    env = os.environ if env is None else env
    setting = (env.get("MEDIA_HOST_ALWAYS_ON") or "").strip().lower()
    if setting in ("1", "true", "yes", "on"):
        return {"always_on": True, "why": "This server is set to be on all the time"}
    if setting in ("0", "false", "no", "off"):
        return {"always_on": False, "why": "This server is set as not always on"}
    slept = read("/sys/power/suspend_stats/success")
    if slept.isdigit() and int(slept) > 0:
        return {"always_on": False, "why": "This server has slept since it started"}
    chassis = read("/sys/class/dmi/id/chassis_type")
    if chassis in _PORTABLE:
        return {"always_on": False, "why": "This server is a laptop"}
    kind = virt()
    if kind:
        return {"always_on": True, "why": "This server is a virtual machine"}
    if chassis in _SERVER:
        return {"always_on": True, "why": "This server is a server machine"}
    try:
        up = float(read("/proc/uptime").split()[0])
    except (IndexError, ValueError):
        return None
    days = int(up // 86400)
    if days >= _UP_DAYS:
        return {"always_on": True, "why": f"This server has been up {days} days without sleeping"}
    return None


def prefer(network: str) -> tuple[Optional[str], Optional[str]]:
    """(prefer, prefer_why) for a bridge's network."""
    if (network or "").lower() in _PHONE_FIRST:
        return "phone", _PHONE_WHY
    return None, None


def annotate(listing: list[dict], env: Optional[Mapping[str, str]] = None) -> dict:
    """GET /bridges' body: the add-on's listing with prefer on each bridge,
    and host when it can be told."""
    for b in listing:
        if "prefer" in b:  # the add-on's own word wins
            continue
        p, why = prefer(str(b.get("network") or ""))
        b["prefer"] = p
        if why:
            b["prefer_why"] = why
    body: dict = {"ok": True, "bridges": listing}
    h = host(env)
    if h is not None:
        body["host"] = h
    return body
