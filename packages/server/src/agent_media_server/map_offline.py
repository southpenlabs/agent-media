"""Offline maps: the Map and its guidance with no signal (maps proposal step 7
item 4 and step 10's "Offline"; David, 6 Nov 2026: "A self-hosted Victoria
PMTiles file ... later as the fallback").

Open source first (22 Nov 2026). Protomaps' basemap (github.com/protomaps/
basemaps, BSD-3 code; tiles an ODbL Produced Work of OpenStreetMap, credited
"© OpenStreetMap") is built daily for the planet as one PMTiles archive at
build.protomaps.com, zoom 0–15, and Protomaps' own `pmtiles extract` reads
just one area's tiles out of it by HTTP range requests. The same is done
here in Python (`extract`, a few pages after the PMTiles v3 spec: header,
directories, tiles), so the server needs no Go binary and no Java: building
our own tiles with Planetiler from the OSM extract would need a JVM with
gigabytes of memory for an hour, and the result would be the same data.
The style that draws them is Protomaps' own (the app's @protomaps/basemaps,
BSD-3), its fonts (Noto, SIL OFL) and sprites (MIT) from Protomaps'
basemaps-assets, packed beside the tiles here so nothing is asked of the
network on the phone.

`python -m agent_media_server.map_offline build` (a monthly timer on the
server) picks the newest planet build, extracts Victoria's box (BOUNDS, to
MAX_ZOOM) into `victoria.pmtiles`, packs the fonts and sprites into
`victoria-assets.zip`, and writes `offline.json`, all in the offline dir
(`MEDIA_MAP_OFFLINE_DIR`, default `~/.local/share/agent-media/offline-maps`).
`--dry-run` says how big it would be without reading the tiles.

`GET /map/offline` lists what there is to download (`areas`: id, name,
version, bounds, max_zoom, bytes, files with their paths), `{areas: []}`
until a build is done; `GET /map/offline/<area>/<file>` hands a file over,
a byte range at a time when asked (the phone downloads over Wi-Fi and
resumes), behind the map's gate.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import gzip
import json
import math
import os
import re
import struct
import sys
import time
import urllib.error
import urllib.request
import zipfile
from bisect import bisect_left
from collections.abc import Callable
from pathlib import Path

from agent_media_core import failures as _failures

BUILDS = "https://build-metadata.protomaps.dev/builds.json"
BUILD_URL = "https://build.protomaps.com/{key}"
ASSETS = "https://protomaps.github.io/basemaps-assets"
#: Victoria, with a little of the Murray's far bank: w, s, e, n.
BOUNDS = (140.95, -39.25, 150.05, -33.95)
AREA = "victoria"
AREA_NAME = "Victoria"
#: Zoom 15 is the builds' deepest, but Victoria to 15 is 525 MB and to 14
#: is 240 MB (22 Nov 2026); MapLibre draws 14's tiles on past it, every road
#: and its name there, the buildings' outlines and house numbers coarser.
MAX_ZOOM = 14
FONTS = ("Noto Sans Regular", "Noto Sans Medium", "Noto Sans Italic")
#: Latin, Greek and Cyrillic, and punctuation: the names on Victoria's map.
GLYPH_RANGES = ((0, 255), (256, 511), (512, 767), (768, 1023), (1024, 1279), (8192, 8447), (8448, 8703))
FLAVOURS = ("light", "dark")
USER_AGENT = "agent-media offline map (https://github.com/southpenlabs/agent-media)"
MANIFEST = "offline.json"
#: A range read in one go at most, and the gap between two tiles read across
#: rather than asked apart.
CHUNK = 8 << 20
GAP = 64 << 10
HEADER_LEN = 127
ROOT_MAX = 16384 - HEADER_LEN

#: (url, first byte, last byte) → the bytes. Tests pass their own.
RangeFetch = Callable[[str, int, int], bytes]


def offline_dir() -> Path:
    d = os.environ.get("MEDIA_MAP_OFFLINE_DIR")
    if d:
        return Path(d).expanduser()
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "agent-media" / "offline-maps"


# --- PMTiles v3 ---------------------------------------------------------------

def _rotate(n: int, x: int, y: int, rx: int, ry: int) -> tuple[int, int]:
    if ry == 0:
        if rx == 1:
            x, y = n - 1 - x, n - 1 - y
        x, y = y, x
    return x, y


def zxy_to_id(z: int, x: int, y: int) -> int:
    """A tile's PMTiles id: the tiles of every zoom before, then its place on the Hilbert curve."""
    acc = ((1 << (2 * z)) - 1) // 3
    d = 0
    s = (1 << z) >> 1
    while s > 0:
        rx = 1 if x & s else 0
        ry = 1 if y & s else 0
        d += s * s * ((3 * rx) ^ ry)
        x, y = _rotate(s, x, y, rx, ry)
        s >>= 1
    return acc + d


def id_to_zxy(tid: int) -> tuple[int, int, int]:
    z = 0
    acc = 0
    while True:
        n = 1 << (2 * z)
        if tid < acc + n:
            break
        acc += n
        z += 1
    d = tid - acc
    x = y = 0
    s = 1
    t = d
    while s < (1 << z):
        rx = 1 & (t >> 1)
        ry = 1 & (t ^ rx)
        x, y = _rotate(s, x, y, rx, ry)
        x += s * rx
        y += s * ry
        t >>= 2
        s <<= 1
    return z, x, y


def _varint(b: bytes, i: int) -> tuple[int, int]:
    out = shift = 0
    while True:
        c = b[i]
        i += 1
        out |= (c & 0x7F) << shift
        if c < 0x80:
            return out, i
        shift += 7


def _put_varint(out: bytearray, n: int) -> None:
    while n >= 0x80:
        out.append((n & 0x7F) | 0x80)
        n >>= 7
    out.append(n)


def read_header(b: bytes) -> dict:
    if len(b) < HEADER_LEN or b[:7] != b"PMTiles" or b[7] != 3:
        raise ValueError("not a PMTiles v3 archive")
    q = struct.unpack_from("<QQQQQQQQQQQ", b, 8)
    names = ("root_offset", "root_length", "metadata_offset", "metadata_length", "leaf_offset", "leaf_length",
             "data_offset", "data_length", "addressed_tiles", "tile_entries", "tile_contents")
    h = dict(zip(names, q))
    h["clustered"], h["internal_compression"], h["tile_compression"], h["tile_type"], h["min_zoom"], h["max_zoom"] = \
        struct.unpack_from("<BBBBBB", b, 96)
    h["min_lon_e7"], h["min_lat_e7"], h["max_lon_e7"], h["max_lat_e7"] = struct.unpack_from("<iiii", b, 102)
    h["center_zoom"] = b[118]
    h["center_lon_e7"], h["center_lat_e7"] = struct.unpack_from("<ii", b, 119)
    return h


def write_header(h: dict) -> bytes:
    return (b"PMTiles" + bytes([3])
            + struct.pack("<QQQQQQQQQQQ", h["root_offset"], h["root_length"], h["metadata_offset"],
                          h["metadata_length"], h["leaf_offset"], h["leaf_length"], h["data_offset"],
                          h["data_length"], h["addressed_tiles"], h["tile_entries"], h["tile_contents"])
            + struct.pack("<BBBBBB", h["clustered"], h["internal_compression"], h["tile_compression"],
                          h["tile_type"], h["min_zoom"], h["max_zoom"])
            + struct.pack("<iiii", h["min_lon_e7"], h["min_lat_e7"], h["max_lon_e7"], h["max_lat_e7"])
            + bytes([h["center_zoom"]]) + struct.pack("<ii", h["center_lon_e7"], h["center_lat_e7"]))


def _decompress(b: bytes, kind: int) -> bytes:
    if kind == 2:
        return gzip.decompress(b)
    if kind in (0, 1):
        return b
    raise ValueError(f"compression {kind} not read here")


def read_directory(b: bytes) -> list[tuple[int, int, int, int]]:
    """A directory's entries: (tile id, offset, length, run length); run 0 is a leaf directory."""
    n, i = _varint(b, 0)
    ids, runs, lens, offs = [], [], [], []
    last = 0
    for _ in range(n):
        v, i = _varint(b, i)
        last += v
        ids.append(last)
    for _ in range(n):
        v, i = _varint(b, i)
        runs.append(v)
    for _ in range(n):
        v, i = _varint(b, i)
        lens.append(v)
    for k in range(n):
        v, i = _varint(b, i)
        offs.append(offs[k - 1] + lens[k - 1] if v == 0 and k > 0 else v - 1)
    return list(zip(ids, offs, lens, runs))


def write_directory(entries: list[tuple[int, int, int, int]]) -> bytes:
    out = bytearray()
    _put_varint(out, len(entries))
    last = 0
    for tid, _o, _l, _r in entries:
        _put_varint(out, tid - last)
        last = tid
    for e in entries:
        _put_varint(out, e[3])
    for e in entries:
        _put_varint(out, e[2])
    for k, (_t, off, _l, _r) in enumerate(entries):
        if k > 0 and off == entries[k - 1][1] + entries[k - 1][2]:
            _put_varint(out, 0)
        else:
            _put_varint(out, off + 1)
    return bytes(out)


def _http_range(url: str, first: int, last: int) -> bytes:
    req = urllib.request.Request(url, headers={"Range": f"bytes={first}-{last}", "User-Agent": USER_AGENT})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=60) as res:
                body = res.read()
            if len(body) != last - first + 1:
                raise OSError(f"short read: {len(body)} of {last - first + 1}")
            return body
        except (urllib.error.URLError, TimeoutError, OSError):
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)
    raise OSError("unreachable")


def wanted_ids(bounds: tuple[float, float, float, float], max_zoom: int, min_zoom: int = 0) -> list[int]:
    """Every tile id inside the box, zoom min_zoom to max_zoom, in order."""
    w, s, e, n = bounds
    out: list[int] = []
    for z in range(min_zoom, max_zoom + 1):
        x0, y0 = _tile(w, n, z)
        x1, y1 = _tile(e, s, z)
        for x in range(x0, x1 + 1):
            for y in range(y0, y1 + 1):
                out.append(zxy_to_id(z, x, y))
    out.sort()
    return out


def _tile(lon: float, lat: float, z: int) -> tuple[int, int]:
    nn = 1 << z
    x = int((lon + 180.0) / 360.0 * nn)
    y = int((1 - math.asinh(math.tan(math.radians(max(min(lat, 85.0511), -85.0511)))) / math.pi) / 2 * nn)
    return min(max(x, 0), nn - 1), min(max(y, 0), nn - 1)


def _resolve(url: str, h: dict, entries: list, want: list[int], fetch: RangeFetch,
             found: dict[int, tuple[int, int]]) -> None:
    """Find each wanted id's (offset, length) in the source's tile data, reading leaves as needed."""
    for k, (tid, off, ln, run) in enumerate(entries):
        end = entries[k + 1][0] if k + 1 < len(entries) else (1 << 62)
        if run:
            end = tid + run
        a = bisect_left(want, tid)
        b = bisect_left(want, end)
        if a == b:
            continue
        if run == 0:
            raw = fetch(url, h["leaf_offset"] + off, h["leaf_offset"] + off + ln - 1)
            leaf = read_directory(_decompress(raw, h["internal_compression"]))
            _resolve(url, h, leaf, want[a:b], fetch, found)
        else:
            for t in want[a:b]:
                found[t] = (off, ln)


def plan(url: str, bounds=BOUNDS, max_zoom: int = MAX_ZOOM, fetch: RangeFetch | None = None) -> dict:
    """What an extract would hold: the source's header and metadata, each wanted
    tile's place in the source, and the bytes it would come to."""
    fetch = fetch or _http_range
    head = fetch(url, 0, 16383)
    h = read_header(head)
    root_raw = head[h["root_offset"]:h["root_offset"] + h["root_length"]] \
        if h["root_offset"] + h["root_length"] <= len(head) else \
        fetch(url, h["root_offset"], h["root_offset"] + h["root_length"] - 1)
    root = read_directory(_decompress(root_raw, h["internal_compression"]))
    meta = fetch(url, h["metadata_offset"], h["metadata_offset"] + h["metadata_length"] - 1) \
        if h["metadata_length"] else b"{}"
    try:
        metadata = json.loads(_decompress(meta, h["internal_compression"]) or b"{}")
    except ValueError:
        metadata = {}
    mz = min(max_zoom, h["max_zoom"])
    want = wanted_ids(bounds, mz, h["min_zoom"])
    found: dict[int, tuple[int, int]] = {}
    _resolve(url, h, root, want, fetch, found)
    unique = {v for v in found.values()}
    return {"header": h, "metadata": metadata, "found": found, "max_zoom": mz,
            "bytes": sum(ln for _o, ln in unique), "tiles": len(found), "contents": len(unique)}


def _directories(entries: list[tuple[int, int, int, int]]) -> tuple[bytes, bytes]:
    """(root, leaves), gzipped, the root small enough to sit in the first 16 KiB."""
    root = gzip.compress(write_directory(entries), mtime=0)
    if len(root) <= ROOT_MAX:
        return root, b""
    size = 4096
    while True:
        leaves = bytearray()
        root_entries = []
        for i in range(0, len(entries), size):
            chunk = entries[i:i + size]
            leaf = gzip.compress(write_directory(chunk), mtime=0)
            root_entries.append((chunk[0][0], len(leaves), len(leaf), 0))
            leaves += leaf
        root = gzip.compress(write_directory(root_entries), mtime=0)
        if len(root) <= ROOT_MAX:
            return root, bytes(leaves)
        size *= 2


def extract(url: str, out: Path, bounds=BOUNDS, max_zoom: int = MAX_ZOOM, fetch: RangeFetch | None = None,
            progress: Callable[[int, int], None] | None = None) -> dict:
    """Write the box's tiles from the PMTiles archive at `url` to `out`
    (a new PMTiles v3 archive); answers what `plan` found."""
    fetch = fetch or _http_range
    p = plan(url, bounds, max_zoom, fetch)
    h, found = p["header"], p["found"]
    # Output offsets, in tile id order, each content once.
    where: dict[tuple[int, int], int] = {}
    order: list[tuple[int, int]] = []
    entries: list[tuple[int, int, int, int]] = []
    at = 0
    for tid in sorted(found):
        src = found[tid]
        if src not in where:
            where[src] = at
            order.append(src)
            at += src[1]
        off = where[src]
        if entries and entries[-1][1] == off and entries[-1][0] + entries[-1][3] == tid:
            t0, o0, l0, r0 = entries[-1]
            entries[-1] = (t0, o0, l0, r0 + 1)
        else:
            entries.append((tid, off, src[1], 1))
    root, leaves = _directories(entries)
    w, s, e, n = bounds
    meta = dict(p["metadata"]) if isinstance(p["metadata"], dict) else {}
    meta["sasonica:area"] = AREA
    meta_b = gzip.compress(json.dumps(meta).encode(), mtime=0)
    nh = dict(h)
    nh.update(root_offset=HEADER_LEN, root_length=len(root),
              metadata_offset=HEADER_LEN + len(root), metadata_length=len(meta_b),
              leaf_offset=HEADER_LEN + len(root) + len(meta_b), leaf_length=len(leaves),
              data_offset=HEADER_LEN + len(root) + len(meta_b) + len(leaves), data_length=at,
              addressed_tiles=sum(x[3] for x in entries), tile_entries=len(entries), tile_contents=len(order),
              clustered=1, internal_compression=2, max_zoom=p["max_zoom"],
              min_lon_e7=round(w * 1e7), min_lat_e7=round(s * 1e7), max_lon_e7=round(e * 1e7),
              max_lat_e7=round(n * 1e7), center_zoom=min(10, p["max_zoom"]),
              center_lon_e7=round((w + e) / 2 * 1e7), center_lat_e7=round((s + n) / 2 * 1e7))
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".part")
    done = 0
    with open(tmp, "wb") as f:
        f.write(write_header(nh))
        f.write(root)
        f.write(meta_b)
        f.write(leaves)
        # Tile data in its order, nearby source tiles read in one range.
        i = 0
        while i < len(order):
            j = i
            first = h["data_offset"] + order[i][0]
            last = first + order[i][1] - 1
            while j + 1 < len(order):
                nxt = h["data_offset"] + order[j + 1][0]
                nlast = nxt + order[j + 1][1] - 1
                if nxt < first or nxt - last > GAP or nlast - first > CHUNK:
                    break
                last = max(last, nlast)
                j += 1
            blob = fetch(url, first, last)
            for k in range(i, j + 1):
                o = h["data_offset"] + order[k][0] - first
                f.write(blob[o:o + order[k][1]])
                done += order[k][1]
            if progress:
                progress(done, at)
            i = j + 1
    os.replace(tmp, out)
    return p


def tile(path: Path, z: int, x: int, y: int) -> bytes | None:
    """One tile's bytes (as stored) from a local archive: for tests and checks."""
    with open(path, "rb") as f:
        h = read_header(f.read(HEADER_LEN))
        tid = zxy_to_id(z, x, y)
        off, ln = h["root_offset"], h["root_length"]
        for _depth in range(4):
            f.seek(off)
            entries = read_directory(_decompress(f.read(ln), h["internal_compression"]))
            k = bisect_left([e[0] for e in entries], tid + 1) - 1
            if k < 0:
                return None
            t0, o0, l0, r0 = entries[k]
            if r0 == 0:
                off, ln = h["leaf_offset"] + o0, l0
                continue
            if tid >= t0 + r0:
                return None
            f.seek(h["data_offset"] + o0)
            return f.read(l0)
    return None


# --- the fonts and sprites ----------------------------------------------------

def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as res:
        return res.read()


def assets(out: Path, get: Callable[[str], bytes] | None = None) -> int:
    """Pack the style's fonts and sprites into a zip at `out`: fonts/<stack>/<range>.pbf
    and sprites/<flavour>[@2x].json|png. Answers its size."""
    get = get or _get
    tmp = out.with_name(out.name + ".part")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for stack in FONTS:
            for a, b in GLYPH_RANGES:
                name = f"fonts/{stack}/{a}-{b}.pbf"
                try:
                    z.writestr(name, get(f"{ASSETS}/{urllib.request.quote(name)}"))
                except urllib.error.HTTPError as e:
                    if e.code != 404:
                        raise
        for fl in FLAVOURS:
            for suffix in ("", "@2x"):
                for ext in ("json", "png"):
                    name = f"sprites/{fl}{suffix}.{ext}"
                    z.writestr(name, get(f"{ASSETS}/sprites/v4/{fl}{suffix}.{ext}"))
        z.writestr("NOTICE.txt", "Fonts: Noto Sans, SIL Open Font License 1.1. Sprites: Protomaps basemaps-assets, "
                                 "derived from tangrams/icons, MIT. Map data © OpenStreetMap contributors, ODbL.\n")
    os.replace(tmp, out)
    return out.stat().st_size


# --- the build and the listing ------------------------------------------------

def newest_build(get: Callable[[str], bytes] | None = None) -> str:
    rows = json.loads((get or _get)(BUILDS))
    keys = sorted(r["key"] for r in rows if isinstance(r, dict) and re.fullmatch(r"\d{8}\.pmtiles", str(r.get("key"))))
    if not keys:
        raise RuntimeError("no Protomaps build listed")
    return keys[-1]


def build(dry_run: bool = False, max_zoom: int = MAX_ZOOM, out_dir: Path | None = None) -> dict:
    d = out_dir or offline_dir()
    key = newest_build()
    url = BUILD_URL.format(key=key)
    if dry_run:
        p = plan(url, BOUNDS, max_zoom)
        return {"build": key, "tiles": p["tiles"], "contents": p["contents"], "bytes": p["bytes"],
                "max_zoom": p["max_zoom"]}
    d.mkdir(parents=True, exist_ok=True)
    started = time.time()
    last = [0.0]

    def progress(done: int, total: int) -> None:
        if time.time() - last[0] > 30:
            last[0] = time.time()
            print(f"{done >> 20} of {total >> 20} MB", flush=True)

    p = extract(url, d / f"{AREA}.pmtiles", BOUNDS, max_zoom, progress=progress)
    size_a = assets(d / f"{AREA}-assets.zip")
    version = key.split(".")[0]
    area = {"id": AREA, "name": AREA_NAME, "version": version, "source": f"Protomaps build {key}",
            "built_at": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "bounds": list(BOUNDS), "max_zoom": p["max_zoom"],
            "credit": "© OpenStreetMap contributors · Protomaps",
            "files": [{"name": f"{AREA}.pmtiles", "bytes": (d / f"{AREA}.pmtiles").stat().st_size, "kind": "tiles"},
                      {"name": f"{AREA}-assets.zip", "bytes": size_a, "kind": "assets"}]}
    tmp = d / f".{MANIFEST}.tmp"
    tmp.write_text(json.dumps({"areas": [area]}, indent=1))
    os.replace(tmp, d / MANIFEST)
    print(f"built {AREA} from {key} in {round(time.time() - started)} s: "
          f"{sum(f['bytes'] for f in area['files']) >> 20} MB", flush=True)
    return area


def listing(out_dir: Path | None = None) -> dict:
    """`GET /map/offline`: what there is to download, with each file's path."""
    d = out_dir or offline_dir()
    try:
        man = json.loads((d / MANIFEST).read_text())
    except FileNotFoundError:
        return {"areas": []}
    except (OSError, ValueError) as e:
        _failures.swallowed(e)
        return {"areas": []}
    areas = []
    for a in man.get("areas") or []:
        files = []
        for f in a.get("files") or []:
            p = d / str(f.get("name") or "")
            if not p.is_file():
                break
            files.append({**f, "bytes": p.stat().st_size, "path": f"/map/offline/{a['id']}/{f['name']}"})
        else:
            areas.append({**a, "files": files, "bytes": sum(f["bytes"] for f in files)})
    return {"areas": areas}


FILE = re.compile(r"/map/offline/([a-z0-9-]{1,40})/([A-Za-z0-9._-]{1,80})")


def file_for(area: str, name: str, out_dir: Path | None = None) -> Path | None:
    """The file `name` of `area` when it is one the listing names, else None."""
    for a in listing(out_dir)["areas"]:
        if a["id"] == area:
            for f in a["files"]:
                if f["name"] == name:
                    return (out_dir or offline_dir()) / name
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m agent_media_server.map_offline")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="extract Victoria's tiles from the newest Protomaps build")
    b.add_argument("--dry-run", action="store_true", help="say how big it would be, read no tiles")
    b.add_argument("--max-zoom", type=int, default=MAX_ZOOM)
    sub.add_parser("list", help="what GET /map/offline answers")
    a = ap.parse_args(argv)
    from agent_media_core import logs

    logs.setup()
    if a.cmd == "list":
        print(json.dumps(listing(), indent=1))
        return 0
    try:
        print(json.dumps(build(dry_run=a.dry_run, max_zoom=a.max_zoom), indent=1))
    except (OSError, ValueError, RuntimeError) as e:
        print(f"offline map not built: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


