"""Aerial photos under the Map, through this server (David, 19 Nov 2026: "Is
it possible to get an arial view photo overlay?"; he chose Esri World
Imagery).

Esri's World Imagery tiles, asked with the owner's ArcGIS Location Platform
key (`MEDIA_MAP_AERIAL_KEY`, a free developer account's; outside-services
row 64). The key never leaves this server: the app asks
`GET /map/tiles/aerial/{z}/{x}/{y}` with its own bearer, and this asks Esri.
Victoria's own aerial basemap (Vicmap Basemaps) was looked at first: its
capabilities say it is a licensed service with an annual fee.

While the key is set, `GET /map/layers` lists `{id: "aerial", name: "Aerial
photo", kind: "tiles", default_on: false, tiles: {url, max_zoom, credit}}`.
Tiles are kept in memory a day (`KEEP`, at most `MOST`), so panning back and
forth asks Esri once; nothing is written to disk.
"""
from __future__ import annotations

import os
import threading
import time
import urllib.error
import urllib.request
from collections import OrderedDict

ESRI = "https://ibasemaps-api.arcgis.com/arcgis/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
CREDIT = "Powered by Esri · Esri, Maxar, Earthstar Geographics, and the GIS User Community"
MAX_ZOOM = 19
KEEP = 86400.0
MOST = 1500
_CACHE: OrderedDict[tuple[int, int, int], tuple[float, bytes, str]] = OrderedDict()
_LOCK = threading.Lock()


def key() -> str:
    return os.environ.get("MEDIA_MAP_AERIAL_KEY", "").strip()


def layers() -> list[dict]:
    if not key():
        return []
    return [{"id": "aerial", "name": "Aerial photo", "kind": "tiles", "default_on": False,
             "tiles": {"url": "/map/tiles/aerial/{z}/{x}/{y}", "max_zoom": MAX_ZOOM, "credit": CREDIT}}]


def tile(z: int, x: int, y: int, fetch=None, now: float | None = None) -> tuple[int, bytes, str]:
    """`(status, body, content type)` for one tile; 404 without a key or out of range."""
    if not key() or not (0 <= z <= MAX_ZOOM and 0 <= x < 2 ** z and 0 <= y < 2 ** z):
        return 404, b"", "text/plain"
    now = time.time() if now is None else now
    k = (z, x, y)
    with _LOCK:
        hit = _CACHE.get(k)
        if hit and now - hit[0] < KEEP:
            _CACHE.move_to_end(k)
            return 200, hit[1], hit[2]
    url = ESRI.format(z=z, x=x, y=y) + "?token=" + urllib.request.quote(key())
    try:
        status, body, ctype = (fetch or _fetch)(url)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        from agent_media_core import failures

        failures.swallowed(e)
        return 502, b"", "text/plain"
    if status == 200 and body:
        with _LOCK:
            _CACHE[k] = (now, body, ctype)
            _CACHE.move_to_end(k)
            while len(_CACHE) > MOST:
                _CACHE.popitem(last=False)
    return status, body, ctype


def _fetch(url: str) -> tuple[int, bytes, str]:
    req = urllib.request.Request(url, headers={"User-Agent": "agent-media"})
    try:
        with urllib.request.urlopen(req, timeout=10) as res:
            return res.status, res.read(), res.headers.get("Content-Type") or "image/jpeg"
    except urllib.error.HTTPError as e:
        return e.code, b"", "text/plain"


def _reset_for_tests() -> None:
    with _LOCK:
        _CACHE.clear()
