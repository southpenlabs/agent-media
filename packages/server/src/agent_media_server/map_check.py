"""Places to check the hours of, for adding them to OpenStreetMap only from
sources OSM allows (David, 22 Nov 2026: "let's also upload it to open
street maps"; told OSM forbids copying from Google or Foursquare, he chose
"Both": a list to check when he visits, and hours read from the place's own
website, confirmed by him before sending).

Every place whose card asked `GET /map/place_hours` (OSM had no hours) goes
on its account's list, kept in `state_dir()/map-check-hours.json`: name,
point, the OSM element when known (the app sends it, or an Overpass search
by name within 80 m finds it), the website (OSM's tag, the app's, or the
one Google or Foursquare named — only where to look), first and last seen.

  GET  /map/check_hours  → {places: [{key, name, lat, lon, osm_id?, website?,
                           proposed?, site_info?, first_seen, seen_at,
                           nudged_at?}]}, newest first
  POST /map/check_hours  {key, done: true} (the app sent hours to OSM) or
                           {key, dismiss: true}: off the list, and not put
                           back (done for 30 days while Overpass catches up,
                           dismissed for good)

A place with a website has its page read at most once a week (site_read:
schema.org structured data only; one obvious hours/contact page on the same
site when the first has no hours). What it gives goes in `proposed`
(`{opening_hours?, tags, fields, source: "website", url}`) for the owner to
confirm; nothing here sends anything to OSM. Ratings go in `site_info`, the
card's buttons in `site` (on /map/place_hours answers). Fetching is polite:
an honest User-Agent, robots.txt obeyed, 8 s timeouts, 2 MB at most, only
http(s) to public addresses (redirects checked too).

The nudge: on each `POST /device/location` from one of the owner's devices,
a place on the owner's list within NEAR_M that was not nudged in the last 7
days is told to the phone through the alert store (§6.17: a `map.check.<key>`
status alert at warn, its `route` `/maps?check=<key>`, which the app posts
as a notification whose tap opens that screen); at most one per 30 min. The
alert is cleared once the phone is CLEAR_AWAY_M away, or the place is done
or dismissed. Only the owner's list nudges: the alert store's notices go to
the owner's devices.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import ipaddress
import json
import math
import os
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from pathlib import Path
from typing import Callable

from agent_media_core import failures as _failures

FILE = "map-check-hours.json"
NEAR_M = 60
NUDGE_EVERY_S = 7 * 86400
NUDGE_GAP_S = 30 * 60
CLEAR_AWAY_M = 500
#: A rough fix is no reason to say "you're here".
MAX_ACCURACY_M = 100
WEB_EVERY_S = 7 * 86400
DONE_KEEP_S = 30 * 86400
MAX_PLACES = 500
FETCH_TIMEOUT_S = 8
#: A card waits on the site only this long (one page), then Google is asked.
CARD_TIMEOUT_S = 4
FETCH_MAX_BYTES = 2_000_000
USER_AGENT = ("agent-media check-hours/1 (+https://github.com/southpenlabs/agent-media; "
              "reads a business's own schema.org opening hours for OpenStreetMap)")
#: Off in tests (conftest): a refresh runs only when a test calls it.
BACKGROUND = True

_LOCK = threading.Lock()
#: (url) → (final url, html text). Tests pass their own.
Fetch = Callable[[str], "tuple[str, str]"]


def _path() -> Path:
    from agent_media_core._paths import state_dir

    return state_dir() / FILE


def _load(path: Path | None = None) -> dict:
    try:
        d = json.loads((path or _path()).read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(d: dict, path: Path | None = None) -> None:
    p = path or _path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(f".{p.name}.{os.getpid()}.{threading.get_ident()}")
        tmp.write_text(json.dumps(d))
        os.replace(tmp, p)
    except OSError as e:
        _failures.swallowed(e)


def _book(d: dict, account: str) -> dict:
    b = d.setdefault("accounts", {}).setdefault(account, {})
    b.setdefault("places", {})
    b.setdefault("gone", {})
    return b


def key_of(name: str, lat: float, lon: float) -> str:
    return hashlib.sha1(f"{name.lower()}|{lat:.4f}|{lon:.4f}".encode()).hexdigest()[:12]


def _distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    kx = math.cos(math.radians((lat1 + lat2) / 2)) * 111_320.0
    return math.hypot((lon2 - lon1) * kx, (lat2 - lat1) * 110_540.0)


def _web(u: str) -> str:
    u = (u or "").strip()
    if u and "://" not in u:
        u = "https://" + u
    p = urllib.parse.urlsplit(u)
    return u[:500] if p.scheme in ("http", "https") and p.hostname else ""


def seen(account: str, name: str, lat, lon, *, osm_id: str = "", website: str = "",
         osm_hours: str = "", now: float | None = None, path: Path | None = None,
         kick: bool = True) -> str | None:
    """A card asked for hours OSM has not got: the place goes on (or stays
    on) the list as `kind: "missing"`. With `osm_hours` (OSM has some, and
    the card asks only so the website can be compared) it is kept out of
    sight (`watch`) unless the two disagree (`conflict`). Its key, or None
    (bad point, done or dismissed, or nothing to compare with)."""
    from . import osm_edit

    try:
        la, lo = float(lat), float(lon)
    except (TypeError, ValueError):
        return None
    name = " ".join((name or "").split())[:120]
    if not name or not (-90 <= la <= 90 and -180 <= lo <= 180):
        return None
    now = time.time() if now is None else now
    key = key_of(name, la, lo)
    oid = osm_id.strip() if osm_edit._ID.fullmatch((osm_id or "").strip()) else ""
    web = _web(website)
    osm_hours = " ".join((osm_hours or "").split())[:255]
    with _LOCK:
        d = _load(path)
        b = _book(d, account)
        g = b["gone"].get(key)
        if g and (g.get("why") == "dismissed" or now - float(g.get("at") or 0) < DONE_KEEP_S):
            return None
        if osm_hours and key not in b["places"] and not web:
            return None
        b["gone"].pop(key, None)
        e = b["places"].setdefault(key, {"key": key, "name": name, "lat": la, "lon": lo, "first_seen": now})
        e["seen_at"] = now
        if osm_hours:
            e["osm_hours"] = osm_hours
            if e.get("kind") != "conflict":
                e["kind"] = "watch"
        else:
            e.pop("osm_hours", None)
            e.pop("conflict", None)
            e["kind"] = "missing"
        clear = _compare(b, e, now)
        if oid:
            e["osm_id"] = oid
        if web and not e.get("website"):
            e["website"] = web
        if len(b["places"]) > MAX_PLACES:
            for k, _ in sorted(b["places"].items(), key=lambda kv: kv[1].get("seen_at") or 0)[:len(b["places"]) - MAX_PLACES]:
                b["places"].pop(k, None)
        _save(d, path)
    if clear:
        _clear(key)
    if kick and BACKGROUND and _due(e, now):
        _kick(account, key)
    return key


def kick_if_due(account: str, key: str | None, now: float | None = None, path: Path | None = None) -> None:
    now = time.time() if now is None else now
    e = _book(_load(path), account)["places"].get(key or "")
    if e and BACKGROUND and _due(e, now):
        _kick(account, e["key"])


def website_answer(account: str, key: str | None, *, fetch: Fetch | None = None,
                   now: float | None = None, path: Path | None = None) -> dict | None:
    """The place's hours from its own website, as a /map/place_hours answer
    (source "website"), or None: no website known, or no structured hours on
    it. The site is read at most once a week per place, shared with the
    list's own reading (one page here, CARD_TIMEOUT_S); otherwise what was
    read is used (David, 22 Nov 2026: "does it look it up from their website
    before it looks it up on Google?")."""
    now = time.time() if now is None else now
    e = _book(_load(path), account)["places"].get(key or "")
    if not e or not e.get("website"):
        return None
    if now - float(e.get("web_checked_at") or 0) >= WEB_EVERY_S:
        e = refresh(account, e["key"], fetch=fetch or (lambda u: fetch_page(u, CARD_TIMEOUT_S)),
                    now=now, path=path, deep=False) or {}
    p = e.get("proposed") or {}
    if not p.get("opening_hours"):
        return None
    return {"found": True, "source": "website", "opening_hours": p["opening_hours"],
            "display": p["opening_hours"], "link": p.get("url") or e.get("website")}


_PUBLIC = ("key", "kind", "name", "lat", "lon", "osm_id", "website", "proposed", "osm_tags", "conflict",
           "site_info", "first_seen", "seen_at", "nudged_at")
LISTED = ("missing", "conflict")


def _listed(e: dict) -> bool:
    return e.get("kind", "missing") in LISTED


def same_week(a: str, b: str, now: float | None = None) -> bool | None:
    """Whether two opening_hours open at the same times through a week (the
    one from next Monday), read with opening-hours-py: True, False, or None
    when either cannot be read or is unknown or open-ended somewhere. Read
    with no place, so public-holiday rules never match: a PH rule on one side
    only is no difference, nor is how either is written."""
    import opening_hours as _oh

    day = dt.date.fromtimestamp(time.time() if now is None else now)
    start = dt.datetime.combine(day + dt.timedelta(days=7 - day.weekday()), dt.time())
    end = start + dt.timedelta(days=7)

    def week(h: str):
        out: list[list] = []
        for s, e, state, _c in _oh.OpeningHours(h).intervals(start, end):
            name = getattr(state, "name", str(state)).upper()
            if name == "UNKNOWN":
                raise ValueError("unknown")
            if name == "OPEN":
                if out and out[-1][1] == s:
                    out[-1][1] = e
                else:
                    out.append([s, e])
        return out

    try:
        return week(a) == week(b)
    except Exception as e:  # noqa: BLE001 - one that cannot be read is no conflict to report
        _failures.swallowed(e)
        return None


def _compare(b: dict, e: dict, now: float) -> bool:
    """OSM's hours against the website's, once per pair: the entry's kind
    and conflict set, the account's stats counted. True when a conflict
    just went away (its nudge is to be taken down)."""
    osm, web = e.get("osm_hours"), (e.get("proposed") or {}).get("opening_hours")
    if not osm or not web or e.get("compared") == [osm, web]:
        return False
    same = same_week(osm, web, now)
    if same is None:
        return False
    e["compared"] = [osm, web]
    st = b.setdefault("stats", {"compared": 0, "conflicts": 0})
    st["compared"] = int(st.get("compared") or 0) + 1
    if same:
        was = e.get("kind") == "conflict"
        e.pop("conflict", None)
        e["kind"] = "watch"
        if was and e.get("alert_open"):
            e["alert_open"] = False
            return True
        return False
    st["conflicts"] = int(st.get("conflicts") or 0) + 1
    e["kind"] = "conflict"
    e["conflict"] = {"osm": osm, "website": web, "url": (e.get("proposed") or {}).get("url") or e.get("website")}
    return False


def conflict_of(account: str, key: str | None, path: Path | None = None) -> dict | None:
    e = _book(_load(path), account)["places"].get(key or "") or {}
    return dict(e["conflict"]) if e.get("kind") == "conflict" and e.get("conflict") else None


def listing(account: str, now: float | None = None, path: Path | None = None) -> tuple[bool, dict]:
    now = time.time() if now is None else now
    b = _book(_load(path), account)
    places = sorted((e for e in b["places"].values() if _listed(e)), key=lambda e: -(e.get("seen_at") or 0))
    if BACKGROUND:
        for e in places:
            if _due(e, now):
                _kick(account, e["key"])
    st = b.get("stats") or {}
    return True, {"places": [{**{k: e[k] for k in _PUBLIC if e.get(k) not in (None, "")}, "kind": e.get("kind", "missing")}
                             for e in places],
                  "stats": {"compared": int(st.get("compared") or 0), "conflicts": int(st.get("conflicts") or 0)}}


def change(account: str, body: dict, now: float | None = None, path: Path | None = None,
           report=None) -> tuple[bool, dict]:
    key = str((body or {}).get("key") or "")
    done, dismiss = (body or {}).get("done") is True, (body or {}).get("dismiss") is True
    if done == dismiss:
        return False, {"status": 400, "code": "bad_change", "error": "send {key, done: true} or {key, dismiss: true}"}
    now = time.time() if now is None else now
    with _LOCK:
        d = _load(path)
        b = _book(d, account)
        e = b["places"].pop(key, None)
        if e is not None and not _listed(e):
            b["places"][key] = e
            e = None
        if e is None:
            return False, {"status": 404, "code": "not_listed", "error": "that place is not on the list"}
        b["gone"][key] = {"at": now, "why": "done" if done else "dismissed"}
        _save(d, path)
    if e.get("alert_open"):
        _clear(key, report)
    return True, {"key": key}


def site_of(account: str, name: str, lat, lon, path: Path | None = None) -> dict | None:
    """What the place's own website was read to say for the card, if it was."""
    try:
        key = key_of(" ".join((name or "").split())[:120], float(lat), float(lon))
    except (TypeError, ValueError):
        return None
    e = _book(_load(path), account)["places"].get(key) or {}
    return dict(e["site"]) if isinstance(e.get("site"), dict) and e["site"] else None


# ── reading the website ──────────────────────────────────────────────────

def _due(e: dict, now: float) -> bool:
    if not e.get("osm_id") and not e.get("osm_looked_at"):
        return True
    if _tags_due(e):
        return True
    return bool(e.get("website")) and now - float(e.get("web_checked_at") or 0) >= WEB_EVERY_S


def _tags_due(e: dict) -> bool:
    """OSM's own values for what the website proposes, not read since it was."""
    return bool(e.get("osm_id") and (e.get("proposed") or {}).get("tags")
                and float(e.get("osm_tags_at") or 0) < float(e.get("web_checked_at") or 0))


def read_osm_tags(osm_id: str, http=None) -> dict:
    """The element's tags as OSM's API has them now (a public read, no sign-in)."""
    import xml.etree.ElementTree as ET

    from . import osm_edit

    kind = osm_id.split("/", 1)[0]
    st, body = (http or osm_edit._http)("GET", f"{osm_edit._base()}/api/0.6/{osm_id}", None, {"User-Agent": USER_AGENT})
    if st != 200:
        raise ValueError(f"OpenStreetMap could not give {osm_id} ({st})")
    el = ET.fromstring(body).find(kind)
    return {t.get("k"): t.get("v") for t in (el.findall("tag") if el is not None else [])}


def _public_host(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    return bool(infos) and all(ipaddress.ip_address(i[4][0].split("%")[0]).is_global for i in infos)


class _Checked(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        p = urllib.parse.urlsplit(newurl)
        if p.scheme not in ("http", "https") or not p.hostname or not _public_host(p.hostname):
            raise urllib.error.HTTPError(newurl, code, "redirect to a place not fetched", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _get(url: str, accept: str, timeout: float = FETCH_TIMEOUT_S) -> tuple[str, str, str]:
    p = urllib.parse.urlsplit(url)
    if p.scheme not in ("http", "https") or not p.hostname or not _public_host(p.hostname):
        raise ValueError(f"not a public web address: {url}")
    opener = urllib.request.build_opener(_Checked)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
    with opener.open(req, timeout=timeout) as r:
        body = r.read(FETCH_MAX_BYTES + 1)[:FETCH_MAX_BYTES]
        ctype = r.headers.get("Content-Type", "")
        charset = r.headers.get_content_charset() or "utf-8"
        return r.geturl(), ctype, body.decode(charset, errors="replace")


def fetch_page(url: str, timeout: float = FETCH_TIMEOUT_S) -> tuple[str, str]:
    """The page's final URL and HTML, if the site's robots.txt lets us."""
    p = urllib.parse.urlsplit(url)
    robots = urllib.robotparser.RobotFileParser()
    try:
        _u, _c, text = _get(f"{p.scheme}://{p.netloc}/robots.txt", "text/plain", timeout)
        robots.parse(text.splitlines())
    except urllib.error.HTTPError as e:
        robots.parse([] if e.code in (404, 410) else ["User-agent: *", "Disallow: /"])
    except Exception as e:  # noqa: BLE001 - no robots.txt to read: none to obey
        _failures.swallowed(e)
        robots.parse([])
    if not robots.can_fetch(USER_AGENT, url):
        raise PermissionError(f"{p.netloc}'s robots.txt asks us not to read {p.path or '/'}")
    final, ctype, text = _get(url, "text/html,application/xhtml+xml", timeout)
    if "html" not in ctype.lower():
        raise ValueError(f"not a web page ({ctype})")
    return final, text


def _find_osm(e: dict, overpass=None) -> dict:
    """The OSM element by this name within 80 m: {osm_id?, website?}."""
    from . import map_osm
    from .map_hours import _alike

    q = (f'[out:json][timeout:15];nwr(around:80,{e["lat"]:.6f},{e["lon"]:.6f})["name"];'
         "out tags center 30;")
    got = (overpass or map_osm.overpass)(q)
    best = None
    for el in got.get("elements") or []:
        tags = el.get("tags") or {}
        if not _alike(e["name"], str(tags.get("name") or "")):
            continue
        la = el.get("lat", (el.get("center") or {}).get("lat"))
        lo = el.get("lon", (el.get("center") or {}).get("lon"))
        dist = _distance_m(e["lat"], e["lon"], la, lo) if isinstance(la, (int, float)) and isinstance(lo, (int, float)) else 80
        if best is None or dist < best[0]:
            best = (dist, el)
    if best is None:
        return {}
    el = best[1]
    tags = el.get("tags") or {}
    out = {"osm_id": f"{el.get('type')}/{el.get('id')}"}
    if web := _web(str(tags.get("website") or tags.get("contact:website") or "")):
        out["website"] = web
    return out


def refresh(account: str, key: str, *, fetch: Fetch | None = None, overpass=None, osm_http=None,
            now: float | None = None, path: Path | None = None, deep: bool = True) -> dict | None:
    """Find the place's OSM element if not known, and read its website if a
    week has passed (`deep`: also one obvious hours page, and the Overpass
    search; a card waiting reads the one page only). The entry as kept, or
    None when it is not listed."""
    from . import site_read

    now = time.time() if now is None else now
    e = dict(_book(_load(path), account)["places"].get(key) or {})
    if not e:
        return None
    upd: dict = {}
    if deep and not e.get("osm_id") and not e.get("osm_looked_at"):
        upd["osm_looked_at"] = now
        try:
            found = _find_osm(e, overpass)
            upd.update({k: v for k, v in found.items() if k == "osm_id" or not e.get(k)})
        except Exception as ex:  # noqa: BLE001 - Overpass away: the app may still send the id
            _failures.swallowed(ex)
    web = upd.get("website") or e.get("website")
    if web and now - float(e.get("web_checked_at") or 0) >= WEB_EVERY_S:
        upd["web_checked_at"] = now
        get = fetch or fetch_page
        try:
            url, page = get(web)
            got = site_read.read(page, url, e["name"], dt.date.fromtimestamp(now))
            if deep and not (got.get("proposed") or {}).get("opening_hours"):
                more = site_read.hours_page(page, url)
                if more:
                    try:
                        url2, page2 = get(more)
                        got2 = site_read.read(page2, url2, e["name"], dt.date.fromtimestamp(now))
                        if (got2.get("proposed") or {}).get("opening_hours") or not got.get("proposed"):
                            got, url = got2 or got, url2 if got2 else url
                    except Exception as ex:  # noqa: BLE001 - the second page is a maybe
                        _failures.swallowed(ex)
            upd["proposed"] = ({**got["proposed"], "source": "website", "url": url}
                               if got.get("proposed") else None)
            upd["site"] = got.get("site")
            upd["site_info"] = got.get("site_info")
            upd["web_error"] = None
        except Exception as ex:  # noqa: BLE001 - a site down, refusing us, or not a page: next week
            _failures.swallowed(ex)
            upd["web_error"] = str(ex)[:200]
    # What OSM has now for the keys proposed, so the app shows it and leaves them unticked.
    after = {**e, **{k: v for k, v in upd.items() if v is not None}}
    if deep and _tags_due(after):
        upd["osm_tags_at"] = now
        try:
            have = read_osm_tags(after["osm_id"], osm_http)
            keys = set(after["proposed"]["tags"]) | {"opening_hours"}
            upd["osm_tags"] = {k: v for k, v in have.items() if k in keys} or None
        except Exception as ex:  # noqa: BLE001 - OSM away: the app's send still keeps what OSM has
            _failures.swallowed(ex)
    with _LOCK:
        d = _load(path)
        cur = _book(d, account)["places"].get(key)
        if cur is None:
            return None
        for k, v in upd.items():
            if v is None:
                cur.pop(k, None)
            else:
                cur[k] = v
        clear = _compare(_book(d, account), cur, now)
        _save(d, path)
        out = dict(cur)
    if clear:
        _clear(key)
    return out


_queue: list[tuple[str, str]] = []
_worker: threading.Thread | None = None


def _kick(account: str, key: str) -> None:
    global _worker
    with _LOCK:
        if (account, key) in _queue:
            return
        _queue.append((account, key))
        if _worker is not None and _worker.is_alive():
            return
        _worker = threading.Thread(target=_drain, name="map-check", daemon=True)
        _worker.start()


def _drain() -> None:
    while True:
        with _LOCK:
            if not _queue:
                return
            account, key = _queue[0]
        try:
            refresh(account, key)
        except Exception as e:  # noqa: BLE001 - one place's trouble never stops the rest
            _failures.swallowed(e)
        with _LOCK:
            if _queue and _queue[0] == (account, key):
                _queue.pop(0)


# ── the nudge ────────────────────────────────────────────────────────────

def _report(body: dict, report=None) -> None:
    from . import alerts

    try:
        (report or alerts.report)(body)
    except Exception as e:  # noqa: BLE001 - a nudge never fails the location post
        _failures.swallowed(e)


def _clear(key: str, report=None) -> None:
    _report({"id": f"map.check.{key}", "level": "ok"}, report)


def nudge(account: str, lat: float, lon: float, *, now: float | None = None,
          path: Path | None = None, report=None) -> str | None:
    """The phone is at (lat, lon): tell it about a listed place it is beside.
    The key nudged, or None."""
    now = time.time() if now is None else now
    clears: list[str] = []
    chosen = None
    with _LOCK:
        d = _load(path)
        b = _book(d, account)
        changed = False
        for e in b["places"].values():
            if not _listed(e) and not e.get("alert_open"):
                continue
            dist = _distance_m(lat, lon, e["lat"], e["lon"])
            if e.get("alert_open") and dist > CLEAR_AWAY_M:
                e["alert_open"] = False
                clears.append(e["key"])
                changed = True
            if _listed(e) and dist <= NEAR_M and now - float(e.get("nudged_at") or 0) >= NUDGE_EVERY_S:
                if chosen is None or dist < chosen[0]:
                    chosen = (dist, e)
        if chosen and now - float(b.get("last_nudge_at") or 0) >= NUDGE_GAP_S:
            e = chosen[1]
            e["nudged_at"] = now
            e["alert_open"] = True
            b["last_nudge_at"] = now
            changed = True
        else:
            chosen = None
        if changed:
            _save(d, path)
    for k in clears:
        _clear(k, report)
    if not chosen:
        return None
    e = chosen[1]
    if e.get("kind") == "conflict" and e.get("conflict"):
        title = f"OpenStreetMap and their website disagree on the hours at {e['name']}: which is right?"
        detail = (f"OpenStreetMap: {e['conflict']['osm']}. Their website: {e['conflict']['website']}. "
                  "Tap to check them against the sign.")
    else:
        title = f"Check the hours at {e['name']}?"
        detail = "OpenStreetMap has no opening hours for it. Tap to check them against the sign and add them."
        if (e.get("proposed") or {}).get("opening_hours"):
            detail += f" Its website says {e['proposed']['opening_hours']}."
    # A raise each time (a left-open one would be no change, and no notice).
    _clear(e["key"], report)
    _report({"id": f"map.check.{e['key']}", "level": "warn", "title": title[:200],
             "detail": detail, "route": f"/maps?check={e['key']}"}, report)
    return e["key"]


def on_location(bearer: str, body, *, now: float | None = None, report=None) -> str | None:
    """`POST /device/location` went in: nudge if the owner's phone is beside a listed place."""
    from . import auth

    if not isinstance(body, dict) or body.get("off") is True or body.get("precise") is False:
        return None
    try:
        lat, lon = float(body["lat"]), float(body["lon"])
        acc = float(body.get("accuracy_m") or 0)
    except (KeyError, TypeError, ValueError):
        return None
    if acc > MAX_ACCURACY_M:
        return None
    user, _ = auth.gate(bearer)
    if not user or auth.account_key(user) != "":
        return None
    return nudge("", lat, lon, now=now, report=report)
