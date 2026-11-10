"""Cameras from OpenStreetMap for the Map page (maps proposal step 3; David,
10 Nov 2026: "Camera overlays").

Two layers, both off until switched on and both read for the part of the map
on screen (`bbox: true`), only once zoomed in that far (`min_zoom`):

- `speed_cameras`: OSM `highway=speed_camera` nodes and the device nodes of
  `type=enforcement` relations (speed, red light, average speed, phone,
  seatbelt), each titled by what it enforces and the limit where known.
  `MEDIA_MAP_SPEED_CAMERAS=off` leaves it out, for a place where showing
  them is not allowed.
- `surveillance`: OSM `man_made=surveillance` nodes (CCTV and the like).

Asked of Overpass a 0.1° square at a time, in the background: a read
answers at once with the squares it has (`partial: true` while others are
on their way, so the app asks again shortly), and one worker fetches the
rest in turn, each square kept a day in the cache dir and served stale
when Overpass does not answer. The public Overpass servers are often busy
(10 Nov 2026: overpass-api.de 502, a mirror timing out, another 16 s a
square), so `MEDIA_OVERPASS_URL` is a comma-separated list tried in order,
by default overpass-api.de then two public mirrors. OSM data © OpenStreetMap
contributors, ODbL; nothing here says it is complete.

Or from a weekly file (David, 10 Nov 2026: "Weekly Australia file"): the
public Overpass and ohsome servers refuse a cloud host's address (red5: a
502, a 403 and two timeouts), so with `MEDIA_MAP_OSM_EXTRACT` naming an
`.osm.pbf` (Geofabrik's Australia, say) the layers read `osm-cameras.json`
in the state dir instead. `python -m agent_media_server.map_osm refresh`
(a weekly timer on the host) downloads the file to the cache dir, keeps
the cameras with pyosmium (the `map-extract` extra, BSD) and deletes the
download; nothing is asked of Overpass then. Between full downloads,
`... map_osm update` (daily) applies the extract's change files (Geofabrik
publishes a few MB a day beside it, `<extract>-updates/`; or
`MEDIA_MAP_OSM_UPDATES`) to the kept cameras, so a camera mapped today is on
the Map tomorrow (David, 10 Nov 2026: "some kind of incremental update a
little bit more regularly"). The kept file holds the raw elements and the
change sequence it is up to for that; a device node a changed relation names
but whose own record did not change is placed at the next full download.
"""

from __future__ import annotations

import json
import math
import os
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_URLS = ("https://overpass-api.de/api/interpreter,"
                "https://maps.mail.ru/osm/tools/overpass/api/interpreter,"
                "https://overpass.private.coffee/api/interpreter")
USER_AGENT = "agent-media map (https://github.com/southpenlabs/agent-media)"
SQUARE = 0.1
KEEP_S = 24 * 3600
#: Squares waiting for the worker at most; past it the oldest asks are dropped.
QUEUE_MAX = 64
MAX_SQUARES = 64
TIMEOUT_S = 30
SOURCE = "© OpenStreetMap contributors"

_lock = threading.Lock()
#: (layer, square) waiting for the worker, oldest first.
_queue: list[tuple[str, tuple[int, int]]] = []
_wake = threading.Event()
_worker: threading.Thread | None = None

ENFORCES = {"maxspeed": "Speed camera", "average_speed": "Average speed camera",
            "traffic_signals": "Red light camera", "mobile_phone": "Phone detection camera",
            "seatbelt": "Seatbelt camera", "check": "Checkpoint"}

LAYERS = {
    "speed_cameras": {"id": "speed_cameras", "name": "Speed cameras", "kind": "points",
                      "icon": "camera", "default_on": False, "bbox": True, "min_zoom": 11,
                      "source": SOURCE,
                      "note": "From OpenStreetMap: only cameras someone has mapped, and mobile ones move."},
    "surveillance": {"id": "surveillance", "name": "CCTV", "kind": "points", "icon": "eye",
                     "default_on": False, "bbox": True, "min_zoom": 14, "source": SOURCE,
                     "note": "From OpenStreetMap: only cameras someone has mapped."},
}


def layers() -> list[dict]:
    out = []
    for lid, lay in LAYERS.items():
        if lid == "speed_cameras" and (os.environ.get("MEDIA_MAP_SPEED_CAMERAS") or "").lower() in ("off", "0", "no"):
            continue
        out.append(dict(lay))
    return out


def _query(layer: str, s: float, w: float, n: float, e: float) -> str:
    box = f"({s},{w},{n},{e})"
    if layer == "surveillance":
        return f'[out:json][timeout:25];node["man_made"="surveillance"]{box};out body;'
    return ("[out:json][timeout:25];"
            f'node["highway"="speed_camera"]{box};out body;'
            f'rel["type"="enforcement"]{box}->.r;.r out body;node(r.r:"device");out body;')


def _cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME")
    d = (Path(base) if base else Path.home() / ".cache") / "agent-media" / "osm"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _fetch(layer: str, sq: tuple[int, int]) -> list[dict]:
    """The square's elements from the first Overpass server that answers."""
    s, w = sq[0] * SQUARE, sq[1] * SQUARE
    body = urllib.parse.urlencode({"data": _query(layer, round(s, 4), round(w, 4),
                                                  round(s + SQUARE, 4), round(w + SQUARE, 4))}).encode()
    urls = [u.strip() for u in (os.environ.get("MEDIA_OVERPASS_URL") or DEFAULT_URLS).split(",") if u.strip()]
    last: Exception | None = None
    for url in urls:
        req = urllib.request.Request(url, data=body,
                                     headers={"User-Agent": USER_AGENT,
                                              "Content-Type": "application/x-www-form-urlencoded"})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as res:
                got = json.loads(res.read().decode("utf-8") or "{}")
            return got.get("elements") or []
        except Exception as e:  # noqa: BLE001 - busy or away: the next one
            last = e
    raise last or RuntimeError("no Overpass server")


def _path(layer: str, sq: tuple[int, int]) -> Path:
    d = _cache_dir() / layer
    d.mkdir(exist_ok=True)
    return d / f"{sq[0]}_{sq[1]}.json"


def _fill(layer: str, sq: tuple[int, int]) -> None:
    """Fetch one square and keep it; a failure leaves what was kept."""
    try:
        got = _features(layer, _fetch(layer, sq))
    except Exception:  # noqa: BLE001
        return
    _path(layer, sq).write_text(json.dumps({"at": time.time(), "features": got}))


def _work() -> None:
    while True:
        _wake.wait(60)
        with _lock:
            item = _queue.pop(0) if _queue else None
            if not _queue:
                _wake.clear()
        if item:
            _fill(*item)


def _ask(layer: str, sq: tuple[int, int]) -> None:
    """Put a square in the worker's queue (once), starting the worker."""
    global _worker
    with _lock:
        if (layer, sq) not in _queue:
            _queue.append((layer, sq))
            del _queue[:-QUEUE_MAX]
        _wake.set()
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_work, name="map-osm", daemon=True)
            _worker.start()


def _features(layer: str, elements: list[dict]) -> list[dict]:
    """GeoJSON points from Overpass elements: a device node takes its
    enforcement relation's kind and limit."""
    of_device: dict[int, dict] = {}
    for el in elements:
        if el.get("type") == "relation":
            for m in el.get("members") or []:
                if m.get("type") == "node" and m.get("role") == "device":
                    of_device[m.get("ref")] = el.get("tags") or {}
    out, seen = [], set()
    for el in elements:
        if el.get("type") != "node" or "lat" not in el or el.get("id") in seen:
            continue
        tags = el.get("tags") or {}
        rel = of_device.get(el.get("id"))
        if layer == "speed_cameras":
            if rel is None and tags.get("highway") != "speed_camera":
                continue
            what = ENFORCES.get((rel or {}).get("enforcement", ""), "Speed camera")
            limit = (rel or {}).get("maxspeed") or tags.get("maxspeed") or ""
            title = f"{what} {limit}".strip()
        else:
            if tags.get("man_made") != "surveillance":
                continue
            kind = tags.get("surveillance:type") or "camera"
            title = "CCTV" if kind == "camera" else kind.replace("_", " ").capitalize()
            if tags.get("surveillance") in ("public", "outdoor", "indoor"):
                title += f" ({tags['surveillance']})"
        seen.add(el.get("id"))
        detail = tags.get("operator") or tags.get("name") or ""
        props = {"title": title, "osm": f"node/{el['id']}"}
        if detail:
            props["detail"] = detail
        out.append({"type": "Feature", "properties": props,
                    "geometry": {"type": "Point", "coordinates": [el["lon"], el["lat"]]}})
    return out


def _squares(bbox) -> list[tuple[int, int]]:
    w, s, e, n = bbox
    rows = range(math.floor(s / SQUARE), math.floor(n / SQUARE) + 1)
    cols = range(math.floor(w / SQUARE), math.floor(e / SQUARE) + 1) if w <= e else []
    return [(r, c) for r in rows for c in cols]


def _extract_path() -> Path:
    base = os.environ.get("XDG_STATE_HOME")
    d = (Path(base) if base else Path.home() / ".local" / "state") / "agent-media"
    d.mkdir(parents=True, exist_ok=True)
    return d / "osm-cameras.json"


_extract: dict = {"mtime": None, "data": None}


def _in_box(f: dict, bbox) -> bool:
    lon, lat = f["geometry"]["coordinates"][:2]
    w, s, e, n = bbox
    return s <= lat <= n and (w <= lon <= e if w <= e else (lon >= w or lon <= e))


def _from_extract(layer: str, bbox) -> tuple[list[dict], dict]:
    """The layer's cameras in the box, from the weekly file (re-read when it changes)."""
    path = _extract_path()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return [], {"missing": True}
    if _extract["mtime"] != mtime:
        try:
            _extract["data"] = json.loads(path.read_text())
            _extract["mtime"] = mtime
        except (OSError, ValueError):
            return [], {"missing": True}
    data = _extract["data"] or {}
    feats = [f for f in data.get(layer) or [] if _in_box(f, bbox)]
    return feats, {"partial": False, "as_of": round(data.get("as_of") or data.get("at") or 0)}


def _elements(pbf: str) -> list[dict]:
    """The cameras in an .osm.pbf as Overpass would give them: enforcement
    relations with their device members, then the camera nodes."""
    import osmium

    devices: set[int] = set()
    relations: list[dict] = []
    for rel in osmium.FileProcessor(pbf, osmium.osm.RELATION).with_filter(
            osmium.filter.TagFilter(("type", "enforcement"))):
        members = [{"type": "node", "ref": m.ref, "role": m.role} for m in rel.members
                   if m.type == "n" and m.role == "device"]
        if members:
            relations.append({"type": "relation", "id": rel.id, "tags": dict(rel.tags),
                              "members": members})
            devices.update(m["ref"] for m in members)
    nodes: list[dict] = []
    for node in osmium.FileProcessor(pbf, osmium.osm.NODE):
        tags = node.tags
        if node.id in devices or tags.get("highway") == "speed_camera" \
                or tags.get("man_made") == "surveillance":
            if node.location.valid():
                nodes.append({"type": "node", "id": node.id, "lat": node.location.lat,
                              "lon": node.location.lon, "tags": dict(tags)})
    return relations + nodes


def refresh(url: str | None = None) -> dict:
    """Download the extract, keep its cameras in osm-cameras.json, delete the
    download. Returns counts. Needs pyosmium (the `map-extract` extra)."""
    from email.utils import parsedate_to_datetime

    url = url or os.environ.get("MEDIA_MAP_OSM_EXTRACT") or ""
    if not url:
        raise SystemExit("set MEDIA_MAP_OSM_EXTRACT to an .osm.pbf address")
    tmp = _cache_dir() / "extract.osm.pbf"
    as_of = ""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=120) as res, open(tmp, "wb") as f:
            as_of = res.headers.get("Last-Modified") or ""
            while chunk := res.read(1 << 20):
                f.write(chunk)
        elements = _elements(str(tmp))
    finally:
        tmp.unlink(missing_ok=True)
    try:
        when = parsedate_to_datetime(as_of).timestamp() if as_of else time.time()
    except (TypeError, ValueError):
        when = time.time()
    return _keep(elements, when, url, sequence=None)


def _keep(elements: list[dict], as_of: float, source: str, sequence: int | None) -> dict:
    """Write the kept file: the layers' features, and the raw elements and
    change sequence the daily update works from. Returns counts."""
    out = {"at": time.time(), "as_of": as_of, "source": source, "sequence": sequence,
           "elements": elements,
           "speed_cameras": _features("speed_cameras", elements),
           "surveillance": _features("surveillance", elements)}
    path = _extract_path()
    part = path.with_suffix(".tmp")
    part.write_text(json.dumps(out))
    os.replace(part, path)
    return {"speed_cameras": len(out["speed_cameras"]), "surveillance": len(out["surveillance"]),
            "sequence": sequence}


def _updates_url(source: str) -> str:
    """Geofabrik's change files beside an extract: `x-latest.osm.pbf` → `x-updates`."""
    own = os.environ.get("MEDIA_MAP_OSM_UPDATES") or ""
    if own:
        return own.rstrip("/")
    base = source.rsplit("/", 1)
    name = base[-1].split("-latest")[0].split(".osm")[0]
    return f"{base[0]}/{name}-updates" if len(base) == 2 else ""


def _camera_node(tags) -> bool:
    return tags.get("highway") == "speed_camera" or tags.get("man_made") == "surveillance"


def apply_changes(elements: list[dict], changes) -> list[dict]:
    """The kept elements with a change set applied. `changes` yields
    `("node" | "relation", id, deleted, tags, lat, lon, members)`."""
    nodes = {e["id"]: e for e in elements if e["type"] == "node"}
    rels = {e["id"]: e for e in elements if e["type"] == "relation"}
    pending_nodes = []
    for kind, oid, deleted, tags, lat, lon, members in changes:
        if kind == "relation":
            devs = [{"type": "node", "ref": m[0], "role": "device"} for m in members or []
                    if m[1] == "device"]
            if deleted or tags.get("type") != "enforcement" or not devs:
                rels.pop(oid, None)
            else:
                rels[oid] = {"type": "relation", "id": oid, "tags": dict(tags), "members": devs}
        else:
            pending_nodes.append((oid, deleted, tags, lat, lon))
    devices = {m["ref"] for r in rels.values() for m in r["members"]}
    for oid, deleted, tags, lat, lon in pending_nodes:
        if deleted or lat is None or not (_camera_node(tags) or oid in devices):
            nodes.pop(oid, None)
        else:
            nodes[oid] = {"type": "node", "id": oid, "lat": lat, "lon": lon, "tags": dict(tags)}
    # A node kept only as a device that no relation names any more goes.
    for oid in [k for k, n in nodes.items() if not _camera_node(n.get("tags") or {}) and k not in devices]:
        del nodes[oid]
    return list(rels.values()) + list(nodes.values())


def update() -> dict:
    """Bring the kept cameras up to the extract's latest change file."""
    import datetime as dt

    import osmium
    from osmium.replication.server import ReplicationServer

    path = _extract_path()
    kept = json.loads(path.read_text())
    if "elements" not in kept:
        raise SystemExit("the kept file is from before updates: run refresh once")
    url = _updates_url(kept.get("source") or os.environ.get("MEDIA_MAP_OSM_EXTRACT") or "")
    if not url:
        raise SystemExit("no change files for this extract (set MEDIA_MAP_OSM_UPDATES)")
    rs = ReplicationServer(url)
    rs.set_request_parameter("headers", {"User-Agent": USER_AGENT})
    seq = kept.get("sequence")
    if seq is None:
        seq = rs.timestamp_to_sequence(dt.datetime.fromtimestamp(kept["as_of"], dt.timezone.utc))
        if seq is None:
            raise SystemExit("could not find where the kept file's date falls in the change files")
    changes: list = []

    class _H(osmium.SimpleHandler):
        def node(self, n):
            ok = n.location.valid() and not n.deleted
            changes.append(("node", n.id, n.deleted, dict(n.tags),
                            n.location.lat if ok else None, n.location.lon if ok else None, None))

        def relation(self, r):
            changes.append(("relation", r.id, r.deleted, dict(r.tags), None, None,
                            [(m.ref, m.role) for m in r.members if m.type == "n"]))

    got = rs.collect_diffs(seq + 1, max_size=200 * 1024)
    if got is None:
        if kept.get("sequence") is None:
            # Nothing new, but where it is up to is kept, so the next run starts there.
            _keep(kept["elements"], kept["as_of"], kept.get("source") or "", seq)
        return {"sequence": seq, "changed": 0, "up_to_date": True}
    got.reader.apply(_H(), simplify=True)
    state = rs.get_state_info(got.id)
    when = state.timestamp.timestamp() if state else time.time()
    counts = _keep(apply_changes(kept["elements"], changes), when, kept.get("source") or "", got.id)
    return {**counts, "changed": len(changes), "newest": got.newest}


def read(layer: str, bbox) -> tuple[list[dict], dict]:
    """`(features, extra)` for a layer in a box: `extra` has `as_of` (the
    oldest square's fetch time), `partial` when squares are still to come,
    and `zoom_in` when the box is too wide to ask at all."""
    if layer not in LAYERS or bbox is None:
        return [], {"zoom_in": True}
    squares = _squares(bbox)
    if not squares or len(squares) > MAX_SQUARES:
        return [], {"zoom_in": True}
    if os.environ.get("MEDIA_MAP_OSM_EXTRACT"):
        return _from_extract(layer, bbox)
    now = time.time()
    feats, ages, partial = [], [], False
    for sq in squares:
        kept = None
        try:
            kept = json.loads(_path(layer, sq).read_text())
        except (OSError, ValueError):
            pass
        if kept is None or now - kept.get("at", 0) > KEEP_S:
            # Fetched in the background; a stale square is served meanwhile.
            _ask(layer, sq)
            if kept is None:
                partial = True
        if kept:
            ages.append(kept.get("at", now))
            feats.extend(kept.get("features") or [])
    extra = {"partial": partial}
    if ages:
        extra["as_of"] = round(min(ages))
    return feats, extra


if __name__ == "__main__":
    import sys

    if sys.argv[1:2] not in (["refresh"], ["update"]):
        raise SystemExit("usage: python -m agent_media_server.map_osm refresh [url] | update")
    from agent_media_core.intake._env import load_env_file

    load_env_file("map-osm")
    if sys.argv[1] == "update":
        print(json.dumps(update()))
    else:
        print(json.dumps(refresh(sys.argv[2] if len(sys.argv) > 2 else None)))
