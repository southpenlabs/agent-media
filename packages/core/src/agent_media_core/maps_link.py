"""A map link someone shared, read into places: `media place link <url>` and
`GET /map/link?url=` (David, 21 Nov 2026: "I'd like to be able to share
directions and locations to sasonica").

Google Maps shares a route or a place as a short link
(`https://maps.app.goo.gl/…`), which redirects to the long one; the long one
carries the stops in its path (`/maps/dir/<from>/<to>/…`) and their points in
its `data=` blob (`!3d<lat>!4d<lon>`), or one place (`/maps/place/<name>/
@lat,lon,z/data=…!3d…!4d…`). Also read: `?q=` / `?query=` / `?ll=` /
`?daddr=`, the `api=1` form (`origin`, `destination`, `waypoints`,
`travelmode`), `geo:` URIs, Apple Maps (`maps.apple.com/?daddr=&saddr=&ll=&q=`)
and OpenStreetMap (`?mlat=&mlon=`, `#map=z/lat/lon`, `/directions?route=`).

The answer: `{"kind": "route"|"place", "points": [{"name", "lat", "lon",
"here"?}], "mode"?: "drive"|"walk"|"bike"|"transit", "url"}`. A route's first
point with `here` is "where I am" (the sender's place when they shared, its
point kept when the link has one): the app routes from where it is instead.
A stop named and not placed is looked up with the geocoder (place_find),
leaning towards the stops placed already.

Only a short link is fetched, and only its redirect is read (no page), from
Google's own hosts; nothing else leaves the machine but those lookups.
"""

from __future__ import annotations

import re
import urllib.error
import urllib.parse
import urllib.request

TIMEOUT_S = 10
USER_AGENT = "agent-media maps link (https://github.com/southpenlabs/agent-media)"
SHORT_HOSTS = ("maps.app.goo.gl", "goo.gl", "g.co")
#: Google's travel modes in a dir link's `!3e<n>`.
GOOGLE_MODES = {"0": "drive", "1": "bike", "2": "walk", "3": "transit"}
API_MODES = {"driving": "drive", "walking": "walk", "bicycling": "bike", "transit": "transit"}
APPLE_MODES = {"d": "drive", "w": "walk", "r": "transit", "c": "bike"}
OSM_MODES = {"car": "drive", "bicycle": "bike", "foot": "walk"}

_URL_RE = re.compile(r"(?:https?://|geo:)[^\s<>\"']+", re.I)
_LATLON_RE = re.compile(r"^\s*(-?\d{1,2}(?:\.\d+)?)\s*,\s*(-?\d{1,3}(?:\.\d+)?)\s*$")


class LinkError(ValueError):
    """Not a map link, or one that names no place."""


def _host(url: str) -> str:
    return (urllib.parse.urlsplit(url).hostname or "").lower()


def _google(host: str) -> bool:
    return bool(re.match(r"^(?:www\.|maps\.)?google\.[a-z.]{2,6}$", host)) or host in SHORT_HOSTS


def is_map_url(url: str) -> bool:
    """A link this reads: Google, Apple or OpenStreetMap maps, or `geo:`."""
    if url.lower().startswith("geo:"):
        return True
    host, path = _host(url), urllib.parse.urlsplit(url).path
    if host == "maps.app.goo.gl" or (host == "goo.gl" and path.startswith("/maps")):
        return True
    if _google(host) and host not in SHORT_HOSTS:
        return host.startswith("maps.") or path.startswith("/maps")
    return host in ("maps.apple.com", "www.openstreetmap.org", "openstreetmap.org", "osm.org")


def find_url(text: str) -> str | None:
    """The first map link in some shared words (Google's share puts words before it)."""
    for m in _URL_RE.finditer(text or ""):
        url = m.group(0).rstrip(".,;)")
        if is_map_url(url):
            return url
    return None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):  # noqa: ANN002, ANN003 - read the Location ourselves
        return None


def expand(url: str, hops: int = 5) -> str:
    """A short link's long form: its redirects followed, each from Google's own hosts."""
    opener = urllib.request.build_opener(_NoRedirect)
    for _ in range(hops):
        if _host(url) not in SHORT_HOSTS:
            return url
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
        try:
            with opener.open(req, timeout=TIMEOUT_S) as res:
                loc = res.headers.get("Location")
        except urllib.error.HTTPError as e:
            loc = e.headers.get("Location") if 300 <= e.code < 400 else None
            if loc is None:
                raise LinkError(f"the short link answered {e.code}") from e
        if not loc:
            raise LinkError("the short link did not say where it goes")
        nxt = urllib.parse.urljoin(url, loc)
        if not _google(_host(nxt)):
            raise LinkError(f"the short link goes off Google ({_host(nxt)})")
        url = nxt
    return url


def _point(text: str) -> tuple[float, float] | None:
    m = _LATLON_RE.match(text or "")
    if not m:
        return None
    lat, lon = float(m.group(1)), float(m.group(2))
    return (lat, lon) if -90 <= lat <= 90 and -180 <= lon <= 180 else None


def _stop(text: str) -> dict:
    """One stop from a path part or a parameter: a point, a name, or "here" (empty)."""
    words = urllib.parse.unquote_plus(text or "").strip()
    if not words or words.lower() in ("my location", "current location", "your location"):
        return {"name": "", "here": True}
    pt = _point(words)
    if pt:
        return {"name": "", "lat": pt[0], "lon": pt[1]}
    return {"name": words}


# Google's `data=` blob: `!<field><type><value>` tokens, an `m` token holding
# the next <value> tokens (all of them, however deep).
def _tokens(data: str) -> list[tuple[str, str, str]]:
    out = []
    for part in data.split("!"):
        m = re.match(r"^(\d+)([a-z])(.*)$", part)
        if m:
            out.append((m.group(1), m.group(2), urllib.parse.unquote(m.group(3))))
    return out


def _tree(toks: list, i: int = 0, n: int | None = None) -> tuple[list, int]:
    """Nodes `(field, type, value, children)`; an `m` takes the next <value> tokens."""
    nodes, end = [], len(toks) if n is None else min(len(toks), i + n)
    while i < end:
        field, typ, val = toks[i]
        i += 1
        if typ == "m" and val.isdigit():
            kids, i = _tree(toks, i, int(val))
            nodes.append((field, typ, val, kids))
        else:
            nodes.append((field, typ, val, []))
    return nodes, i


def _walk(nodes):
    for nd in nodes:
        yield nd
        yield from _walk(nd[3])


def _latlon_in(nodes) -> tuple[float, float] | None:
    lat = lon = None
    for field, typ, val, _ in _walk(nodes):
        try:
            if typ == "d" and field == "3" and lat is None:
                lat = float(val)
            elif typ == "d" and field == "4" and lat is not None and lon is None:
                lon = float(val)
        except ValueError:
            continue
    return (lat, lon) if lat is not None and lon is not None else None


def _dir_data(data: str) -> tuple[list[dict], str | None]:
    """A dir link's stops as its blob has them (`4m` → `1m` per stop), and the mode."""
    nodes, _ = _tree(_tokens(data))
    for field, typ, _v, kids in _walk(nodes):
        if field == "4" and typ == "m" and any(k[0] == "1" and k[1] == "m" for k in kids):
            stops, mode = [], None
            for k in kids:
                if k[0] == "1" and k[1] == "m":
                    here = any(x[0] == "4" and x[1] == "e" and x[2] == "1" for x in k[3])
                    pt = _latlon_in(k[3])
                    s: dict = {"here": True} if here else {}
                    if pt:
                        s["lat"], s["lon"] = pt
                    stops.append(s)
                elif k[0] == "3" and k[1] == "e":
                    mode = GOOGLE_MODES.get(k[2])
            return stops, mode
    return [], None


def _at(part: str) -> tuple[float, float] | None:
    m = re.match(r"^@(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)", part or "")
    return (float(m.group(1)), float(m.group(2))) if m else None


def _parse_google(url: str) -> dict:
    sp = urllib.parse.urlsplit(url)
    q = urllib.parse.parse_qs(sp.query)
    parts = [p for p in sp.path.split("/") if p != ""]
    raw_parts = sp.path.split("/")
    data = next((p[5:] for p in parts if p.startswith("data=")), "")
    at = next((_at(p) for p in parts if p.startswith("@")), None)

    def one(key: str) -> str:
        return (q.get(key) or [""])[0]

    if q.get("api") == ["1"] and (one("destination") or one("query")):
        if one("destination"):
            stops = [_stop(one("origin"))] if one("origin") else [{"name": "", "here": True}]
            stops += [_stop(w) for w in one("waypoints").split("|") if w.strip()]
            stops.append(_stop(one("destination")))
            return {"kind": "route", "points": stops, "mode": API_MODES.get(one("travelmode"))}
        return {"kind": "place", "points": [_stop(one("query"))]}

    if "dir" in parts:
        # /maps/dir/<from>/<stop>/…/<to>/@…/data=…: an empty part is "here".
        i = raw_parts.index("dir")
        segs = []
        for p in raw_parts[i + 1:]:
            if p.startswith("@") or p.startswith("data=") or p.startswith("am="):
                break
            segs.append(p)
        while segs and segs[-1] == "":
            segs.pop()
        stops = [_stop(s) for s in segs]
        blob, mode = _dir_data(data) if data else ([], None)
        if len(blob) == len(stops):
            for s, b in zip(stops, blob):
                if b.get("here") and not s.get("name"):
                    s["here"] = True
                if "lat" in b and "lat" not in s:
                    s["lat"], s["lon"] = b["lat"], b["lon"]
        if not stops and (one("daddr") or one("saddr")):
            stops = [_stop(one("saddr")), _stop(one("daddr"))]
        if len(stops) == 1:
            stops.insert(0, {"name": "", "here": True})
        return {"kind": "route", "points": stops, "mode": mode}

    if one("daddr"):
        stops = [_stop(one("saddr"))] + [_stop(w) for w in re.split(r"\s+to:", one("daddr"))]
        return {"kind": "route", "points": stops, "mode": {"w": "walk", "r": "transit", "b": "bike"}.get(one("dirflg"), None)}

    if "place" in parts or "search" in parts:
        key = "place" if "place" in parts else "search"
        name_part = parts[parts.index(key) + 1] if len(parts) > parts.index(key) + 1 else ""
        stop = _stop(name_part) if name_part and not name_part.startswith("@") else {"name": ""}
        stop.pop("here", None)
        pt = None
        if data:
            nodes, _ = _tree(_tokens(data))
            pt = _latlon_in(nodes)
        if pt and "lat" not in stop:
            stop["lat"], stop["lon"] = pt
        elif key == "place" and at and "lat" not in stop:
            # The view's middle: the place itself when the link has no blob.
            stop["lat"], stop["lon"] = at
        elif at:
            stop["near"] = at
        return {"kind": "place", "points": [stop]}

    for key in ("q", "query", "ll", "center"):
        if one(key):
            s = _stop(one(key))
            s.pop("here", None)
            if "lat" not in s and at:
                s["near"] = at
            return {"kind": "place", "points": [s]}
    if at:
        return {"kind": "place", "points": [{"name": "", "lat": at[0], "lon": at[1]}]}
    raise LinkError("the link names no place")


def _parse_geo(url: str) -> dict:
    # geo:lat,lon[;u=…][?q=lat,lon(Label)|?q=words]
    body = url[4:]
    head, _, query = body.partition("?")
    pt = _point(head.split(";")[0])
    q = urllib.parse.parse_qs(query).get("q", [""])[0]
    m = re.match(r"^\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*(?:\((.*)\))?\s*$", q)
    if m:
        p = _point(f"{m.group(1)},{m.group(2)}")
        if p:
            return {"kind": "place", "points": [{"name": (m.group(3) or "").strip(), "lat": p[0], "lon": p[1]}]}
    if q.strip():
        s: dict = {"name": q.strip()}
        if pt and pt != (0.0, 0.0):
            s["near"] = pt
        return {"kind": "place", "points": [s]}
    if pt:
        return {"kind": "place", "points": [{"name": "", "lat": pt[0], "lon": pt[1]}]}
    raise LinkError("the geo: link names no place")


def _parse_apple(url: str) -> dict:
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)

    def one(key: str) -> str:
        return (q.get(key) or [""])[0]

    if one("daddr"):
        stops = [_stop(one("saddr")), _stop(one("daddr"))]
        return {"kind": "route", "points": stops, "mode": APPLE_MODES.get(one("dirflg"))}
    s: dict = {"name": one("q") or one("address") or one("name")}
    pt = _point(one("ll") or one("coordinate"))
    if pt:
        s["lat"], s["lon"] = pt
    elif _point(one("sll")):
        s["near"] = _point(one("sll"))
    if not s["name"] and "lat" not in s:
        raise LinkError("the Apple Maps link names no place")
    return {"kind": "place", "points": [s]}


def _parse_osm(url: str) -> dict:
    sp = urllib.parse.urlsplit(url)
    q = urllib.parse.parse_qs(sp.query)

    def one(key: str) -> str:
        return (q.get(key) or [""])[0]

    if one("route"):
        stops = [_stop(p) for p in one("route").split(";")]
        mode = OSM_MODES.get(one("engine").split("_")[-1]) if one("engine") else None
        return {"kind": "route", "points": stops, "mode": mode}
    if one("mlat") and one("mlon"):
        pt = _point(f"{one('mlat')},{one('mlon')}")
        if pt:
            return {"kind": "place", "points": [{"name": "", "lat": pt[0], "lon": pt[1]}]}
    m = re.search(r"map=\d+/(-?[\d.]+)/(-?[\d.]+)", sp.fragment)
    if m:
        pt = _point(f"{m.group(1)},{m.group(2)}")
        if pt:
            return {"kind": "place", "points": [{"name": "", "lat": pt[0], "lon": pt[1]}]}
    if one("query"):
        return {"kind": "place", "points": [_stop(one("query"))]}
    raise LinkError("the OpenStreetMap link names no place")


def parse(url: str) -> dict:
    """A long map link's places, as far as the link itself says (no lookups)."""
    if url.lower().startswith("geo:"):
        out = _parse_geo(url)
    else:
        host = _host(url)
        if host == "maps.apple.com":
            out = _parse_apple(url)
        elif "openstreetmap.org" in host or host == "osm.org":
            out = _parse_osm(url)
        elif _google(host):
            out = _parse_google(url)
        else:
            raise LinkError("not a map link")
    if not out.get("mode"):
        out.pop("mode", None)
    return out


def _place_named(stop: dict, near: tuple[float, float] | None, geocode) -> None:
    rows = geocode(stop["name"], near)
    if rows:
        stop["lat"], stop["lon"] = rows[0]["lat"], rows[0]["lon"]
        if rows[0].get("address"):
            stop["address"] = rows[0]["address"]
    else:
        stop["unplaced"] = True


def _default_geocode(words: str, near):
    from . import place_find

    return place_find.find(words, near=near, limit=1)


def read(url: str, geocode=None) -> dict:
    """A shared map link, short or long, as places: named stops looked up, points rounded."""
    url = (url or "").strip()
    if not is_map_url(url):
        found = find_url(url)
        if not found:
            raise LinkError("not a map link")
        url = found
    long_url = expand(url)
    out = parse(long_url)
    geocode = geocode or _default_geocode
    placed = [(s["lat"], s["lon"]) for s in out["points"] if "lat" in s]
    for s in out["points"]:
        if "lat" in s or s.get("here") or not s.get("name"):
            continue
        near = s.pop("near", None) or (placed[0] if placed else None)
        try:
            _place_named(s, near, geocode)
        except Exception as e:  # noqa: BLE001 - the geocoder is out of reach: kept by name
            from . import failures

            failures.swallowed(e)
            s["unplaced"] = True
        if "lat" in s:
            placed.append((s["lat"], s["lon"]))
    for s in out["points"]:
        s.pop("near", None)
        if "lat" in s:
            s["lat"], s["lon"] = round(float(s["lat"]), 6), round(float(s["lon"]), 6)
    if not any("lat" in s for s in out["points"] if not s.get("here")):
        raise LinkError("none of the link's places could be found")
    out["url"] = long_url
    return out


def marker(info: dict) -> str:
    """The reply marker for it: `[[route: …]]` or `[[map: …]]`, placed points only."""
    parts = []
    for s in info.get("points", []):
        if "lat" not in s:
            continue
        name = s.get("name") or ("Start" if s.get("here") else "Here")
        parts.append(f"{name.replace('|', '/').replace(']', ')')} @ {s['lat']},{s['lon']}")
    tag = "route" if info.get("kind") == "route" and len(parts) > 1 else "map"
    return f"[[{tag}: {' | '.join(parts)}]]"
