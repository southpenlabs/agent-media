"""Triggers by place: something happens when the person arrives somewhere or leaves it
(maps proposal step 11, "Leave now" and "Arriving home"; docs/proposals/2026-11-06-maps.md).

David, 24 Nov 2026, choosing triggers by place first: "remind me at Bunnings", the house
music or what's waiting as he nears home. A trigger is a circle and what to do on
crossing it:

    {id, name, lat, lon, radius_m, on: arrive|leave, do: remind|say|music|catchup,
     text?, uri?, once, quiet_s, thread?, made, fired?, count, off?}

- `remind`: a note in the phone's shade ("At Bunnings", the text) and the same said
  aloud; `once` by default, so it turns itself off after.
- `say`: the text said aloud only.
- `music`: `media music play <uri>` in the rooms, or `resume` there with no uri.
- `catchup`: `media catchup`, what is waiting said in one clip.

Kept per account in `state_dir()/place-triggers.json` (`{account: [trigger…]}`), and
who is inside which circle in `place-triggers-state.json` (`{account: {"<device>
<id>": {in, at}}}`, no coordinates).

Where the fixes come from: every fix the server already gets is checked here — the
background share (`POST /device/location`), "Keep where I've been"'s batches (`POST
/device/location/history`) — and the phone, which knows the circles, posts a fix of its
own the moment it thinks it crossed one (`POST /device/places/cross`), so an arrival is
not left waiting for the next batch. The phone's guess only says when to send; the
crossing is decided here, so every way of getting a fix agrees:

- inside when the fix is within `radius_m` and its accuracy is no worse than
  `max(2 × radius, 200 m)`; outside when it is more than `radius_m + LEAVE_MARGIN_M`
  (and its accuracy) away. Between the two the last state holds, so a fix wobbling on
  the edge never fires twice.
- The first fix a device gives for a circle only sets where it is: making "when I get
  home" at home does not fire it.
- A fix older than STALE_S sets the state but fires nothing (a batch arriving late does
  not say "you're home" an hour after).
- `quiet_s` (default 30 min): the same trigger does not fire again sooner.

Routes (app.py):

    GET  /map/triggers → {triggers}            the owner's devices and the host's token
    POST /map/triggers {id?, name, lat, lon, …} → {trigger}; {id, delete: true} → {deleted}
    GET  /device/places/watch → {regions: [{id, lat, lon, radius_m}], version}
         a paired device, not a guest: the circles only, no names or texts
    POST /device/places/cross {lat, lon, accuracy_m?, at?} → {fired: [id…]}

400 codes: bad_point, bad_name, bad_radius, bad_on, bad_do, bad_text, bad_uri, bad_id,
too_many (at most MAX).
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable

MAX = 50
RADIUS_DEFAULT = 150.0
RADIUS_MIN = 50.0
RADIUS_MAX = 2000.0
LEAVE_MARGIN_M = 100.0
QUIET_DEFAULT_S = 1800.0
STALE_S = 900.0
TEXT_MAX = 300
NAME_MAX = 80
ONS = ("arrive", "leave")
DOS = ("remind", "say", "music", "catchup")
_ID = re.compile(r"pt-[a-z0-9]{4,16}")
_URI = re.compile(r"[A-Za-z][A-Za-z0-9+.-]{0,20}:\S{1,400}")
_SESSION = re.compile(r"[A-Za-z0-9_-]{1,80}")

_LOCK = threading.Lock()
#: Set by tests: run actions here instead of in a child process.
RUN: Callable[[dict, str], None] | None = None


def _dir() -> Path:
    from agent_media_core._paths import state_dir

    return state_dir()


def _load(name: str, path: Path | None) -> dict:
    try:
        got = json.loads((path or (_dir() / name)).read_text())
    except (OSError, ValueError):
        return {}
    return got if isinstance(got, dict) else {}


def _save(name: str, book: dict, path: Path | None) -> None:
    p = path or (_dir() / name)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(book, ensure_ascii=False, indent=1))
    os.chmod(tmp, 0o600)
    os.replace(tmp, p)


def _book(path: Path | None) -> dict:
    return _load("place-triggers.json", path)


def _state_path(path: Path | None) -> Path | None:
    return path.with_name(path.stem + "-state.json") if path else None


def metres(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(min(1.0, a)))


def _num(v) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float, str)):
        return None
    try:
        f = float(v)
    except ValueError:
        return None
    return f if math.isfinite(f) else None


def _bad(code: str, error: str) -> tuple[bool, dict]:
    return False, {"status": 400, "code": code, "error": error}


# --- the list ------------------------------------------------------------------------

def listing(account: str, path: Path | None = None) -> tuple[bool, dict]:
    return True, {"triggers": list(_book(path).get(account) or [])}


def change(account: str, body, path: Path | None = None, now: float | None = None) -> tuple[bool, dict]:
    if not isinstance(body, dict):
        return _bad("bad_body", "not JSON")
    now = time.time() if now is None else now
    tid = str(body.get("id") or "").strip()
    if tid and not _ID.fullmatch(tid):
        return _bad("bad_id", "id: pt-…")
    with _LOCK:
        book = _book(path)
        rows: list[dict] = list(book.get(account) or [])
        old = next((r for r in rows if r.get("id") == tid), None) if tid else None
        if body.get("delete") is True:
            if old is None:
                return False, {"status": 404, "code": "not_found", "error": "no such trigger"}
            book[account] = [r for r in rows if r is not old]
            _save("place-triggers.json", book, path)
            return True, {"deleted": tid}
        if tid and old is None:
            return False, {"status": 404, "code": "not_found", "error": "no such trigger"}
        merged = {**(old or {}), **body}
        ok, row = _clean(merged, now)
        if not ok:
            return False, row
        if old is None:
            if len(rows) >= MAX:
                return _bad("too_many", f"at most {MAX} triggers")
            row.update(id="pt-" + secrets.token_hex(4), made=now, count=0)
            rows.append(row)
        else:
            row.update(id=old["id"], made=old.get("made", now), count=old.get("count", 0))
            if old.get("fired"):
                row["fired"] = old["fired"]
            rows[rows.index(old)] = row
        book[account] = rows
        _save("place-triggers.json", book, path)
    return True, {"trigger": row}


def _clean(b: dict, now: float) -> tuple[bool, dict]:
    lat, lon = _num(b.get("lat")), _num(b.get("lon"))
    if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return _bad("bad_point", "lat and lon, in range")
    name = " ".join(str(b.get("name") or "").split())[:NAME_MAX]
    if not name:
        return _bad("bad_name", "the place's name")
    radius = _num(b.get("radius_m")) if b.get("radius_m") is not None else RADIUS_DEFAULT
    if radius is None or not (RADIUS_MIN <= radius <= RADIUS_MAX):
        return _bad("bad_radius", f"radius_m between {RADIUS_MIN:.0f} and {RADIUS_MAX:.0f}")
    on = b.get("on") or "arrive"
    if on not in ONS:
        return _bad("bad_on", "on: arrive or leave")
    do = b.get("do") or "remind"
    if do not in DOS:
        return _bad("bad_do", "do: " + ", ".join(DOS))
    text = " ".join(str(b.get("text") or "").split())
    if do in ("remind", "say") and not text:
        return _bad("bad_text", "what to say")
    if len(text) > TEXT_MAX:
        return _bad("bad_text", f"at most {TEXT_MAX} characters")
    uri = str(b.get("uri") or "").strip()
    if uri and (do != "music" or not _URI.fullmatch(uri)):
        return _bad("bad_uri", "uri: a music address, for do: music")
    quiet = _num(b.get("quiet_s")) if b.get("quiet_s") is not None else QUIET_DEFAULT_S
    if quiet is None or not (0 <= quiet <= 7 * 86400):
        return _bad("bad_quiet", "quiet_s up to a week")
    row = {"name": name, "lat": round(lat, 6), "lon": round(lon, 6), "radius_m": round(radius),
           "on": on, "do": do, "once": bool(b.get("once", do == "remind")), "quiet_s": quiet}
    if text:
        row["text"] = text
    if uri:
        row["uri"] = uri
    thread = str(b.get("thread") or "")
    if thread and _SESSION.fullmatch(thread):
        row["thread"] = thread
    if b.get("off") is True:
        row["off"] = True
    return True, row


def regions(account: str, path: Path | None = None) -> tuple[bool, dict]:
    """What the phone watches: the circles of the triggers that are on, nothing else."""
    rows = [r for r in (_book(path).get(account) or []) if not r.get("off")]
    out = [{"id": r["id"], "lat": r["lat"], "lon": r["lon"], "radius_m": r["radius_m"]} for r in rows]
    version = hashlib.sha1(json.dumps(out, sort_keys=True).encode()).hexdigest()[:10]
    return True, {"regions": out, "version": version}


# --- crossings -----------------------------------------------------------------------

def side(dist: float, acc: float | None, radius: float) -> bool | None:
    """True inside, False outside, None not sure (the edge, or too rough a fix)."""
    a = acc or 0.0
    if dist <= radius and a <= max(2 * radius, 200.0):
        return True
    if dist - a > radius + LEAVE_MARGIN_M:
        return False
    return None


def on_fix(account: str, device: str, lat: float, lon: float, acc: float | None, at: float,
           path: Path | None = None, now: float | None = None) -> list[str]:
    """Check one fix against the account's triggers; fire what it crossed. → ids fired."""
    return on_fixes(account, device, [(at, lat, lon, acc)], path, now)


def on_fixes(account: str, device: str, fixes: list[tuple[float, float, float, float | None]],
             path: Path | None = None, now: float | None = None) -> list[str]:
    now = time.time() if now is None else now
    fired: list[tuple[dict, str]] = []
    with _LOCK:
        book = _book(path)
        rows = [r for r in (book.get(account) or []) if not r.get("off")]
        if not rows or not device:
            return []
        sp = _state_path(path)
        states = _load("place-triggers-state.json", sp)
        mine = states.setdefault(account, {})
        changed = False
        for at, lat, lon, acc in sorted(fixes):
            for r in rows:
                if r.get("off"):
                    continue
                inside = side(metres(lat, lon, r["lat"], r["lon"]), acc, float(r["radius_m"]))
                if inside is None:
                    continue
                key = f"{device} {r['id']}"
                was = mine.get(key)
                if was is not None and at < float(was.get("at") or 0):
                    continue
                mine[key] = {"in": inside, "at": at}
                changed = True
                if was is None or bool(was.get("in")) == inside:
                    continue
                if (r["on"] == "arrive") != inside:
                    continue
                if now - at > STALE_S:
                    continue
                if r.get("fired") and now - float(r["fired"]) < float(r.get("quiet_s", QUIET_DEFAULT_S)):
                    continue
                r["fired"] = now
                r["count"] = int(r.get("count") or 0) + 1
                if r.get("once"):
                    r["off"] = True
                fired.append((dict(r), "arrive" if inside else "leave"))
        ids = {r["id"] for r in rows}
        for k in [k for k in mine if k.split(" ", 1)[-1] not in ids]:
            mine.pop(k, None)
            changed = True
        if changed:
            _save("place-triggers-state.json", states, sp)
        if fired:
            _save("place-triggers.json", book, path)
    for r, how in fired:
        act(r, how)
    return [r["id"] for r, _ in fired]


def points_of(body) -> list[tuple[float, float, float, float | None]]:
    """The fixes in a `/device/location` body or a history batch's `points`."""
    raw = body.get("points") if isinstance(body, dict) and isinstance(body.get("points"), list) else [body]
    out = []
    for p in raw:
        if not isinstance(p, dict):
            continue
        lat, lon = _num(p.get("lat")), _num(p.get("lon"))
        if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
            continue
        at = _num(p.get("at"))
        acc = _num(p.get("accuracy_m"))
        out.append((at if at is not None else time.time(), lat, lon, acc))
    return out


def cross(account: str, device: str, body, path: Path | None = None, now: float | None = None) -> tuple[bool, dict]:
    """`POST /device/places/cross`: the phone thinks it crossed a circle; check its fix."""
    pts = points_of(body) if isinstance(body, dict) and "points" not in body else []
    if not pts:
        return _bad("bad_point", "lat and lon, in range")
    return True, {"fired": on_fixes(account, device, pts, path, now)}


# --- doing it ------------------------------------------------------------------------

def spoken(r: dict, how: str) -> tuple[str, str]:
    """The note's title and what is said, in English (the server's voice is English)."""
    title = ("At " if how == "arrive" else "Leaving ") + r["name"]
    text = r.get("text") or ""
    return title, (f"{title}: {text}" if text else title)


def _media_bin() -> str:
    return shutil.which("media") or str(Path(sys.executable).parent / "media")


def _child(argv: list[str]) -> None:
    env = {k: v for k, v in os.environ.items()
           if k not in ("TMUX_PANE", "CLAUDE_CODE_SESSION_ID", "MEDIA_SOURCE_SESSION",
                        "MEDIA_SESSIOND_SESSION", "MEDIA_SOURCE_WORKSPACE")}
    env["PATH"] = os.pathsep.join([str(Path(sys.executable).parent),
                                   str(Path.home() / ".local" / "bin"), env.get("PATH", "")])
    try:
        subprocess.Popen([_media_bin(), *argv], env=env, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError as e:
        print(f"place_triggers: could not run media {argv[0]}: {e}", file=sys.stderr)


def act(r: dict, how: str) -> None:
    if RUN is not None:
        RUN(r, how)
        return
    title, said = spoken(r, how)
    if r["do"] == "remind":
        from . import notes

        notes.post({"id": f"place-{r['id'][3:]}", "title": title, "text": r.get("text") or title,
                    "priority": "high", "ttl_s": 7200})
        _child(["say", said])
    elif r["do"] == "say":
        _child(["say", said])
    elif r["do"] == "music":
        _child(["music", "play", r["uri"], "--where", "rooms"] if r.get("uri")
               else ["music", "resume", "--where", "rooms"])
    elif r["do"] == "catchup":
        _child(["catchup"])
