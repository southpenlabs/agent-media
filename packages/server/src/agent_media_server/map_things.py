"""`GET /map/things`, `POST /map/things`: where a person's devices, rooms and
speakers are, placed once by hand (maps proposal step 4, "Where things are";
docs/proposals/2026-11-06-maps.md).

A thing is one the server already knows, by the id it already uses:

- `device`: a paired device (`GET /devices`), `d_…`; a person sees their own
  account's devices (auth.device_account), the owner the owner's.
- `room`: a house speaker on the music stream (core music_rooms, Snapcast
  client ids), the rooms of the Media tab's picker.
- `speaker`: an audio target that is a place of its own (core audio_targets'
  options: `phone`, `local`, …); not `auto`, `sasonica` or `rooms`, which
  are choices rather than speakers, nor `device:…`, which is a device.

Kept per account (as map_saved.py) in `state_dir()/map-things.json`,
`{account: [{kind, id, lat, lon, label?, name}]}`; `name` is the thing's name
when it was placed, shown while its source cannot be asked (the Snapcast
server down). The host's own token places the owner's.

`GET /map/things` → `{things: [{kind, id, name, lat, lon, label?, state?}],
unplaced: [{kind, id, name}]}`. `state`, where it is cheap to know: a device
`{online, last_seen?}`; a room `{connected, on, volume, playing}`; a speaker
`{speech, music}` (whether the voice or the music goes there now). When the
rooms cannot be asked, `rooms_unreachable: true` and no unplaced rooms.

`POST /map/things {kind, id, lat, lon, label?}` places one (→ `{thing}`);
`{kind, id, delete: true}` takes it off (→ `{deleted: {kind, id}}`, 404
`not_placed`). 400 codes: `bad_kind`, `bad_point`; 404 `unknown` for an id
the server does not know; 503 `rooms_unreachable` placing a room not placed
before while the rooms cannot be asked.

Also the Map's built-in layer `things`, "Your things" (map_layers.py): a
Point per placed thing with `title`, `kind`, `thing` (its id), `icon`
(`device` | `room` | `speaker`), `label?`, `detail?` and `state?`.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Callable

from agent_media_core import failures

KINDS = ("device", "room", "speaker")
#: Things a person places, at most.
MAX = 200
#: A device seen this recently, or listening for speech or music now, is online.
ONLINE_S = 300.0
#: Audio target names that are choices, not speakers.
NOT_SPEAKERS = frozenset({"auto", "sasonica", "rooms"})
_LOCK = threading.Lock()


def _path() -> Path:
    from agent_media_core._paths import state_dir

    return state_dir() / "map-things.json"


def _load(path: Path | None = None) -> dict[str, list[dict]]:
    try:
        got = json.loads((path or _path()).read_text())
    except (OSError, ValueError):
        return {}
    return got if isinstance(got, dict) else {}


def _save(book: dict[str, list[dict]], path: Path | None = None) -> None:
    p = path or _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(book, ensure_ascii=False, indent=1))
    os.replace(tmp, p)


# --- what the server knows ---------------------------------------------------------

def _devices(account: str) -> list[dict]:
    from . import auth, devices, speech_frames

    connected: set[str] = set()
    for hub in speech_frames._HUBS.values():
        with hub.lock:
            connected |= {v[0] for v in hub.listeners.values()}
    now = time.time()
    out = []
    for d in devices.list_devices():
        if auth.device_account(d) != account:
            continue
        seen = float(d.get("last_seen") or 0) or None
        state: dict = {"online": d["id"] in connected or bool(seen and now - seen < ONLINE_S)}
        if seen:
            state["last_seen"] = round(seen)
        out.append({"id": d["id"], "name": d.get("name") or d["id"], "state": state})
    return out


def _rooms() -> list[dict] | None:
    from . import audio, music

    got = music._rooms()
    if got is None:
        return None
    playing = audio.channel_block("music").get("current") == "rooms"
    return [{"id": r["id"], "name": r.get("label") or r["id"],
             "state": {"connected": r.get("connected", False), "on": r.get("on", False),
                       "volume": r.get("volume"), "playing": playing and bool(r.get("on"))}}
            for r in got]


def _speakers() -> list[dict]:
    from . import audio

    blocks = {c: audio.channel_block(c) for c in ("speech", "music")}
    found: dict[str, dict] = {}
    for b in blocks.values():
        for o in b.get("options") or []:
            name = str(o.get("name") or "")
            if not name or name in NOT_SPEAKERS or name.startswith("device:") or name in found:
                continue
            found[name] = {"id": name, "name": o.get("label") or name}
    for s in found.values():
        s["state"] = {"speech": blocks["speech"].get("current") == s["id"],
                      "music": blocks["music"].get("current") == s["id"]}
    return list(found.values())


Sources = dict[str, Callable]


def _sources() -> Sources:
    return {"device": _devices, "room": _rooms, "speaker": _speakers}


def known(account: str, sources: Sources | None = None) -> dict[str, list[dict] | None]:
    """`{kind: [{id, name, state}] | None}`: None where the source cannot be asked."""
    src = sources or _sources()
    out: dict[str, list[dict] | None] = {}
    for kind in KINDS:
        try:
            out[kind] = src[kind](account) if kind == "device" else src[kind]()
        except Exception as e:  # noqa: BLE001 - one source down leaves the others
            failures.swallowed(e)
            out[kind] = None
    return out


# --- the routes ----------------------------------------------------------------------

def listing(account: str, path: Path | None = None, sources: Sources | None = None) -> tuple[bool, dict]:
    kn = known(account, sources)
    placed = list(_load(path).get(account) or [])
    things = []
    for t in placed:
        rows = kn.get(t["kind"])
        live = next((r for r in rows or [] if r["id"] == t["id"]), None)
        if rows is not None and live is None:
            continue  # unpaired or gone: no longer shown, kept should it come back
        item: dict = {"kind": t["kind"], "id": t["id"], "name": (live or {}).get("name") or t.get("name") or t["id"],
                      "lat": t["lat"], "lon": t["lon"]}
        if t.get("label"):
            item["label"] = t["label"]
        if live and live.get("state") is not None:
            item["state"] = live["state"]
        things.append(item)
    where = {(t["kind"], t["id"]) for t in placed}
    unplaced = [{"kind": k, "id": r["id"], "name": r["name"]}
                for k in KINDS for r in kn.get(k) or [] if (k, r["id"]) not in where]
    out: dict = {"things": things, "unplaced": unplaced}
    if kn.get("room") is None:
        out["rooms_unreachable"] = True
    return True, out


def change(account: str, body: dict, path: Path | None = None,
           sources: Sources | None = None) -> tuple[bool, dict]:
    kind = str(body.get("kind") or "")
    tid = str(body.get("id") or "").strip()
    if kind not in KINDS:
        return False, {"status": 400, "code": "bad_kind", "error": "kind is device, room or speaker"}
    with _LOCK:
        book = _load(path)
        mine = list(book.get(account) or [])
        was = next((t for t in mine if t["kind"] == kind and t["id"] == tid), None)
        if body.get("delete"):
            if was is None:
                return False, {"status": 404, "code": "not_placed", "error": "that is not on the map"}
            book[account] = [t for t in mine if t is not was]
            _save(book, path)
            return True, {"deleted": {"kind": kind, "id": tid}}
        try:
            lat, lon = float(body.get("lat")), float(body.get("lon"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return False, {"status": 400, "code": "bad_point", "error": "lat and lon are numbers"}
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            return False, {"status": 400, "code": "bad_point", "error": "lat and lon are numbers"}
        rows = known(account, sources).get(kind)
        live = next((r for r in rows or [] if r["id"] == tid), None)
        if rows is None and was is None:
            return False, {"status": 503, "code": f"{kind}s_unreachable",
                           "error": f"the {kind}s cannot be asked just now"}
        if rows is not None and live is None:
            return False, {"status": 404, "code": "unknown", "error": f"no {kind} here is called {tid!r}"}
        thing: dict = {"kind": kind, "id": tid, "lat": round(lat, 6), "lon": round(lon, 6),
                       "name": (live or was or {}).get("name") or tid}
        label = str(body.get("label") if body.get("label") is not None else (was or {}).get("label") or "").strip()[:80]
        if label:
            thing["label"] = label
        others = [t for t in mine if t is not was]
        if len(others) >= MAX:
            return False, {"status": 400, "code": "too_many", "error": f"at most {MAX} things"}
        book[account] = [*others, thing]
        _save(book, path)
        out = {k: v for k, v in thing.items()}
        if live and live.get("state") is not None:
            out["state"] = live["state"]
        return True, {"thing": out}


def _detail(t: dict) -> str:
    s = t.get("state") or {}
    if t["kind"] == "device":
        return "Online" if s.get("online") else ""
    if t["kind"] == "room" and s:
        return (f"On, {s.get('volume')}%" if s.get("on") else "Off") if s.get("connected") else "Not connected"
    return ""


def features(account: str, path: Path | None = None, sources: Sources | None = None) -> list[dict]:
    """The `things` layer: a Point per placed thing."""
    _ok, got = listing(account, path, sources)
    out = []
    for t in got["things"]:
        props: dict = {"title": t.get("label") or t["name"], "kind": t["kind"], "thing": t["id"],
                       "icon": t["kind"], "name": t["name"]}
        if t.get("label"):
            props["label"] = t["label"]
        if t.get("state") is not None:
            props["state"] = t["state"]
        if _detail(t):
            props["detail"] = _detail(t)
        out.append({"type": "Feature", "properties": props,
                    "geometry": {"type": "Point", "coordinates": [t["lon"], t["lat"]]}})
    return out
