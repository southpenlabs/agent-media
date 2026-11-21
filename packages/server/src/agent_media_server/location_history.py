"""Where a person has been: the phone's "Keep where I've been" (maps proposal
step 5, "Location history and privacy"; docs/proposals/2026-11-06-maps.md).

Off by default, a switch of its own on the phone. While it is on, the phone
records fixes thinned on the phone (one when it has moved more than ~50 m,
or every 5 minutes while still) and sends them in batches:

    POST /device/location/history {points: [{lat, lon, accuracy_m?, at}]}
        a paired device, not a guest → {kept: n}

Kept here only, in its own file, `state_dir()/location.db` (SQLite, 0600,
`secure_delete` on so a forgotten day is overwritten, not just unlinked).
Never in the long-term memory store, never in a backup (backup.EXCLUDE),
never sent to a model provider; no coordinates are logged. An agent reads
it through `media location where-was-i` / the `location_history` MCP tool,
which answers in words, so only the answer enters the chat.

Stays are grouped into visits: points within VISIT_RADIUS_M of their running
centre for at least VISIT_MIN_S → `{lat, lon, from, to, place}`. A visit is
named, in order, by the person's saved places (map_saved), their placed
things (map_things), or a reverse lookup (Nominatim, `MEDIA_GEOCODER_URL`;
at most once per new visit, one a second, cached per account in this file;
`MEDIA_LOCATION_NAMES=0` turns lookups off). Saved places and things are
matched when read, so renaming Home renames the past.

Read by the owner's devices (not a guest) and the host's own token:

    GET  /map/history?day=YYYY-MM-DD → {day, points: [{lat, lon, at, device}]
         (thinned for drawing), visits: [...], distance_m}
    GET  /map/history/visits?q=<place words>&from=&to=&limit= → {visits}
         newest first ("when was I last at …")
    GET  /map/history/settings → {keep_days: 30|90|365|null, kept: {points,
         first}}; POST {keep_days} (null = forever)
    POST /map/history/forget {day: YYYY-MM-DD} | {all: true} → {forgot: n}

Kept KEEP_DEFAULT days unless the person chose otherwise; anything older is
deleted on the first access each day, and never read back meanwhile. Days
are this server's local days.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import sqlite3
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable

from agent_media_core import failures

KEEP_CHOICES = (30, 90, 365, None)
KEEP_DEFAULT = 90
#: A stay: this close to its centre…
VISIT_RADIUS_M = 100.0
#: …for at least this long.
VISIT_MIN_S = 300.0
#: A batch holds at most this many points.
BATCH_MAX = 500
#: A point older than this is refused (an offline queue keeps a week).
OLDEST_S = 8 * 86400
#: Points farther apart than this, or less accurate, are not counted in a day's distance.
POOR_ACCURACY_M = 150.0
#: Drawing: a point within this of the last kept one is dropped; at most DRAW_MAX points.
DRAW_STEP_M = 15.0
DRAW_MAX = 2000
#: A saved place or thing names a visit within this distance.
NAME_RADIUS_M = 150.0
PRUNE_EVERY_S = 86400.0
USER_AGENT = "agent-media location history (https://github.com/southpenlabs/agent-media)"
#: Whether naming runs in a thread after a batch (off in tests).
BACKGROUND = True

_LOCK = threading.Lock()
_NAMING = threading.Lock()
_LAST_LOOKUP = [0.0]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS points (
  account TEXT NOT NULL, device TEXT NOT NULL, at REAL NOT NULL,
  lat REAL NOT NULL, lon REAL NOT NULL, acc REAL,
  PRIMARY KEY (account, device, at));
CREATE TABLE IF NOT EXISTS visits (
  id INTEGER PRIMARY KEY, account TEXT NOT NULL, device TEXT NOT NULL,
  lat REAL NOT NULL, lon REAL NOT NULL, start REAL NOT NULL, "end" REAL NOT NULL,
  place TEXT);
CREATE INDEX IF NOT EXISTS visits_by_time ON visits (account, start);
CREATE TABLE IF NOT EXISTS names (
  account TEXT NOT NULL, lat REAL NOT NULL, lon REAL NOT NULL, name TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS settings (
  account TEXT PRIMARY KEY, keep_days INTEGER, forever INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""


def _path() -> Path:
    from agent_media_core._paths import state_dir

    return state_dir() / "location.db"


def _open(path: Path | None = None) -> sqlite3.Connection:
    p = path or _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    new = not p.exists()
    if new:
        os.close(os.open(p, os.O_CREAT | os.O_WRONLY, 0o600))
    conn = sqlite3.connect(p, timeout=10)
    conn.execute("PRAGMA secure_delete=ON")
    conn.executescript(_SCHEMA)
    try:
        os.chmod(p, 0o600)
    except OSError as e:
        failures.swallowed(e)
    return conn


def _metres(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371008.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


# --- settings and retention ---------------------------------------------------------

def _keep_days(conn: sqlite3.Connection, account: str) -> int | None:
    row = conn.execute("SELECT keep_days, forever FROM settings WHERE account=?", (account,)).fetchone()
    if not row:
        return KEEP_DEFAULT
    return None if row[1] else int(row[0] or KEEP_DEFAULT)


def _cutoff(conn: sqlite3.Connection, account: str, now: float) -> float:
    days = _keep_days(conn, account)
    return 0.0 if days is None else now - days * 86400


def _prune(conn: sqlite3.Connection, now: float, force: bool = False) -> None:
    """Delete what is past each account's keep, at most once a day."""
    row = conn.execute("SELECT v FROM meta WHERE k='pruned'").fetchone()
    if not force and row and now - float(row[0]) < PRUNE_EVERY_S:
        return
    accounts = {r[0] for r in conn.execute("SELECT DISTINCT account FROM points")}
    accounts |= {r[0] for r in conn.execute("SELECT DISTINCT account FROM visits")}
    for acc in accounts:
        cut = _cutoff(conn, acc, now)
        if cut > 0:
            conn.execute("DELETE FROM points WHERE account=? AND at<?", (acc, cut))
            conn.execute('DELETE FROM visits WHERE account=? AND "end"<?', (acc, cut))
    conn.execute("INSERT OR REPLACE INTO meta (k, v) VALUES ('pruned', ?)", (str(now),))
    conn.commit()


def get_settings(account: str, path: Path | None = None, now: float | None = None) -> tuple[bool, dict]:
    now = time.time() if now is None else now
    with _LOCK:
        conn = _open(path)
        try:
            _prune(conn, now)
            cut = _cutoff(conn, account, now)
            n, first = conn.execute("SELECT COUNT(*), MIN(at) FROM points WHERE account=? AND at>=?",
                                    (account, cut)).fetchone()
            return True, {"keep_days": _keep_days(conn, account), "choices": list(KEEP_CHOICES),
                          "kept": {"points": int(n or 0), "first": round(first) if first else None}}
        finally:
            conn.close()


def set_settings(account: str, body: dict, path: Path | None = None,
                 now: float | None = None) -> tuple[bool, dict]:
    if not isinstance(body, dict) or "keep_days" not in body:
        return False, {"status": 400, "code": "bad_keep", "error": "keep_days is 30, 90, 365 or null (forever)"}
    raw = body.get("keep_days")
    days = None if raw in (None, "forever", 0) else raw
    if days not in KEEP_CHOICES or isinstance(days, bool):
        return False, {"status": 400, "code": "bad_keep", "error": "keep_days is 30, 90, 365 or null (forever)"}
    now = time.time() if now is None else now
    with _LOCK:
        conn = _open(path)
        try:
            conn.execute("INSERT OR REPLACE INTO settings (account, keep_days, forever) VALUES (?, ?, ?)",
                         (account, days or 0, 1 if days is None else 0))
            conn.commit()
            _prune(conn, now, force=True)
        finally:
            conn.close()
    return get_settings(account, path, now)


def forget(account: str, body: dict, path: Path | None = None) -> tuple[bool, dict]:
    """`{day: YYYY-MM-DD}` (that local day's points and the visits touching it) or `{all: true}`."""
    if not isinstance(body, dict):
        return False, {"status": 400, "code": "bad_forget", "error": "say day or all"}
    with _LOCK:
        conn = _open(path)
        try:
            if body.get("all") is True:
                n = conn.execute("DELETE FROM points WHERE account=?", (account,)).rowcount
                conn.execute("DELETE FROM visits WHERE account=?", (account,))
                conn.execute("DELETE FROM names WHERE account=?", (account,))
                conn.commit()
                conn.execute("VACUUM")
                return True, {"forgot": n, "all": True}
            span = _day_span(str(body.get("day") or ""))
            if span is None:
                return False, {"status": 400, "code": "bad_day", "error": "day is YYYY-MM-DD"}
            a, b = span
            n = conn.execute("DELETE FROM points WHERE account=? AND at>=? AND at<?", (account, a, b)).rowcount
            conn.execute('DELETE FROM visits WHERE account=? AND start<? AND "end">=?', (account, b, a))
            conn.commit()
            return True, {"forgot": n, "day": body.get("day")}
        finally:
            conn.close()


# --- the phone's batches ------------------------------------------------------------

def _point(p) -> tuple[float, float, float, float | None] | None:
    if not isinstance(p, dict):
        return None
    try:
        lat, lon, at = float(p["lat"]), float(p["lon"]), float(p["at"])
    except (KeyError, TypeError, ValueError):
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180) or at != at:
        return None
    if at > 1e12:  # milliseconds
        at /= 1000.0
    acc = None
    try:
        acc = max(0.0, float(p["accuracy_m"])) if p.get("accuracy_m") is not None else None
    except (TypeError, ValueError):
        acc = None
    return round(at, 3), round(lat, 6), round(lon, 6), (round(acc, 1) if acc is not None else None)


def add(account: str, device: str, body, path: Path | None = None, now: float | None = None) -> tuple[bool, dict]:
    """A batch from a paired device (the route checks who)."""
    if not isinstance(body, dict) or not isinstance(body.get("points"), list):
        return False, {"status": 400, "code": "bad_points", "error": "points: [{lat, lon, accuracy_m?, at}]"}
    raw = body["points"]
    if len(raw) > BATCH_MAX:
        return False, {"status": 413, "code": "too_many", "error": f"at most {BATCH_MAX} points a batch"}
    now = time.time() if now is None else now
    pts = [q for q in (_point(p) for p in raw) if q and now - OLDEST_S <= q[0] <= now + 300]
    if not pts:
        return True, {"kept": 0}
    with _LOCK:
        conn = _open(path)
        try:
            _prune(conn, now)
            cut = _cutoff(conn, account, now)
            pts = [q for q in pts if q[0] >= cut]
            conn.executemany("INSERT OR REPLACE INTO points (account, device, at, lat, lon, acc) VALUES (?,?,?,?,?,?)",
                             [(account, device, *q) for q in pts])
            if pts:
                _rederive(conn, account, device, min(q[0] for q in pts))
            conn.commit()
        finally:
            conn.close()
    if BACKGROUND and pts and names_on():
        threading.Thread(target=_name_quietly, args=(account, path), name="location-names", daemon=True).start()
    return True, {"kept": len(pts)}


# --- visits ---------------------------------------------------------------------------

def stays(points: list[tuple[float, float, float]]) -> list[dict]:
    """Visits in `[(at, lat, lon)]` (time order): runs of points within
    VISIT_RADIUS_M of their running centre lasting VISIT_MIN_S or more."""
    out: list[dict] = []
    run: list[tuple[float, float, float]] = []
    clat = clon = 0.0

    def close() -> None:
        if run and run[-1][0] - run[0][0] >= VISIT_MIN_S:
            out.append({"lat": round(clat, 6), "lon": round(clon, 6), "from": run[0][0], "to": run[-1][0]})

    for p in points:
        if run and _metres(clat, clon, p[1], p[2]) <= VISIT_RADIUS_M:
            run.append(p)
            n = len(run)
            clat += (p[1] - clat) / n
            clon += (p[2] - clon) / n
            continue
        close()
        run = [p]
        clat, clon = p[1], p[2]
    close()
    return out


def _rederive(conn: sqlite3.Connection, account: str, device: str, since: float) -> None:
    """Work the visits out again from an hour before the earliest new point
    (or the start of a visit reaching into that hour), keeping names."""
    begin = since - 3600
    row = conn.execute('SELECT MIN(start) FROM visits WHERE account=? AND device=? AND "end">=?',
                       (account, device, begin)).fetchone()
    if row and row[0] is not None:
        begin = min(begin, float(row[0]))
    old = conn.execute('SELECT lat, lon, start, "end", place FROM visits WHERE account=? AND device=? AND start>=?',
                       (account, device, begin)).fetchall()
    conn.execute("DELETE FROM visits WHERE account=? AND device=? AND start>=?", (account, device, begin))
    pts = conn.execute("SELECT at, lat, lon FROM points WHERE account=? AND device=? AND at>=? ORDER BY at",
                       (account, device, begin)).fetchall()
    for v in stays([(float(a), float(b), float(c)) for a, b, c in pts]):
        place = next((o[4] for o in old if o[4] and o[2] <= v["to"] and o[3] >= v["from"]
                      and _metres(o[0], o[1], v["lat"], v["lon"]) <= VISIT_RADIUS_M), None)
        conn.execute('INSERT INTO visits (account, device, lat, lon, start, "end", place) VALUES (?,?,?,?,?,?,?)',
                     (account, device, v["lat"], v["lon"], v["from"], v["to"], place))


# --- names ----------------------------------------------------------------------------

def names_on() -> bool:
    return os.environ.get("MEDIA_LOCATION_NAMES", "1").strip().lower() not in ("0", "false", "no", "off")


def _known_places(account: str) -> list[dict]:
    """The person's saved places and placed things, `{lat, lon, name, kind}`."""
    out: list[dict] = []
    try:
        from . import map_saved

        for p in map_saved._load().get(account) or []:
            if "lat" in p and "lon" in p:
                out.append({"lat": p["lat"], "lon": p["lon"], "name": p.get("name") or p.get("id"), "kind": "saved"})
    except Exception as e:  # noqa: BLE001 - names are a nicety
        failures.swallowed(e)
    try:
        from . import map_things

        for t in map_things._load().get(account) or []:
            out.append({"lat": t["lat"], "lon": t["lon"], "name": t.get("label") or t.get("name") or t["id"],
                        "kind": "thing"})
    except Exception as e:  # noqa: BLE001 - names are a nicety
        failures.swallowed(e)
    return out


def _nearest(places: list[dict], lat: float, lon: float, within: float) -> dict | None:
    best, best_d = None, within
    for p in places:
        try:
            d = _metres(float(p["lat"]), float(p["lon"]), lat, lon)
        except (KeyError, TypeError, ValueError):
            continue
        if d <= best_d:
            best, best_d = p, d
    return best


def reverse(lat: float, lon: float) -> str | None:
    """A place's name from the geocoder (Nominatim's reverse), or None. One a second."""
    from agent_media_core import place_find

    wait = 1.1 - (time.monotonic() - _LAST_LOOKUP[0])
    if wait > 0:
        time.sleep(wait)
    _LAST_LOOKUP[0] = time.monotonic()
    q = urllib.parse.urlencode({"lat": f"{lat:.5f}", "lon": f"{lon:.5f}", "format": "jsonv2",
                                "zoom": "18", "addressdetails": "1"})
    req = urllib.request.Request(f"{place_find._base()}/reverse?{q}",
                                 headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as res:
        r = json.loads(res.read().decode("utf-8") or "{}")
    if not isinstance(r, dict):
        return None
    if r.get("name"):
        return str(r["name"])[:120]
    a = r.get("address") if isinstance(r.get("address"), dict) else {}
    road = " ".join(x for x in (a.get("house_number"), a.get("road")) if x)
    area = a.get("suburb") or a.get("town") or a.get("city") or a.get("village") or ""
    got = ", ".join(x for x in (road, area) if x)
    return got[:120] or (str(r.get("display_name") or "").split(",")[0].strip()[:120] or None)


def name_visits(account: str, path: Path | None = None,
                lookup: Callable[[float, float], str | None] | None = None, limit: int = 20) -> int:
    """Name the account's unnamed visits that no saved place or thing names:
    the cache first, else one lookup each. → how many were named."""
    look = lookup or reverse
    named = 0
    places = _known_places(account)
    with _LOCK:
        conn = _open(path)
        try:
            rows = conn.execute("SELECT id, lat, lon FROM visits WHERE account=? AND place IS NULL "
                                "ORDER BY start DESC LIMIT ?", (account, limit)).fetchall()
            cache = [{"lat": a, "lon": b, "name": n} for a, b, n in
                     conn.execute("SELECT lat, lon, name FROM names WHERE account=?", (account,))]
        finally:
            conn.close()
    for vid, lat, lon in rows:
        if _nearest(places, lat, lon, NAME_RADIUS_M):
            continue  # named when read
        hit = _nearest(cache, lat, lon, VISIT_RADIUS_M)
        name = hit["name"] if hit else None
        if name is None:
            try:
                name = look(lat, lon)
            except Exception as e:  # noqa: BLE001 - the geocoder out of reach: try next batch
                failures.swallowed(e)
                break
            if not name:
                continue
            cache.append({"lat": lat, "lon": lon, "name": name})
            with _LOCK:
                conn = _open(path)
                try:
                    conn.execute("INSERT INTO names (account, lat, lon, name) VALUES (?,?,?,?)", (account, lat, lon, name))
                    conn.commit()
                finally:
                    conn.close()
        with _LOCK:
            conn = _open(path)
            try:
                conn.execute("UPDATE visits SET place=? WHERE id=?", (name, vid))
                conn.commit()
            finally:
                conn.close()
        named += 1
    return named


def _name_quietly(account: str, path: Path | None) -> None:
    if not _NAMING.acquire(blocking=False):
        return
    try:
        name_visits(account, path)
    except Exception as e:  # noqa: BLE001 - a background nicety
        failures.swallowed(e)
    finally:
        _NAMING.release()


# --- reading --------------------------------------------------------------------------

def _day_span(day: str) -> tuple[float, float] | None:
    try:
        d = dt.date.fromisoformat(day)
    except ValueError:
        return None
    a = time.mktime(d.timetuple())
    b = time.mktime((d + dt.timedelta(days=1)).timetuple())
    return a, b


def _visit_out(row, places: list[dict], now: float) -> dict:
    _vid, device, lat, lon, start, end, place = row
    hit = _nearest(places, lat, lon, NAME_RADIUS_M)
    out: dict = {"lat": lat, "lon": lon, "from": round(start), "to": round(end),
                 "minutes": round((end - start) / 60), "device": device}
    if hit:
        out["place"], out["place_from"] = hit["name"], hit["kind"]
    elif place:
        out["place"], out["place_from"] = place, "lookup"
    else:
        out["place"] = None
    if now - end < VISIT_MIN_S * 2:
        out["ongoing"] = True
    return out


def _draw(rows: list[tuple]) -> list[dict]:
    kept: list[tuple] = []
    for r in rows:
        if kept and _metres(kept[-1][1], kept[-1][2], r[1], r[2]) < DRAW_STEP_M:
            continue
        kept.append(r)
    if len(kept) > DRAW_MAX:
        step = len(kept) / DRAW_MAX
        kept = [kept[int(i * step)] for i in range(DRAW_MAX - 1)] + [kept[-1]]
    return [{"lat": r[1], "lon": r[2], "at": round(r[0])} for r in kept]


def _distance(rows: list[tuple]) -> float:
    total, last = 0.0, None
    for r in rows:
        if r[3] is not None and r[3] > POOR_ACCURACY_M:
            continue
        if last is not None:
            total += _metres(last[1], last[2], r[1], r[2])
        last = r
    return total


def day(account: str, day_s: str, path: Path | None = None, now: float | None = None) -> tuple[bool, dict]:
    span = _day_span(day_s)
    if span is None:
        return False, {"status": 400, "code": "bad_day", "error": "day is YYYY-MM-DD"}
    now = time.time() if now is None else now
    a, b = span
    with _LOCK:
        conn = _open(path)
        try:
            _prune(conn, now)
            a = max(a, _cutoff(conn, account, now))
            pts = conn.execute("SELECT at, lat, lon, acc, device FROM points WHERE account=? AND at>=? AND at<? "
                               "ORDER BY device, at", (account, a, b)).fetchall()
            vis = conn.execute('SELECT id, device, lat, lon, start, "end", place FROM visits '
                               'WHERE account=? AND start<? AND "end">=? ORDER BY start', (account, b, a)).fetchall()
        finally:
            conn.close()
    by_dev: dict[str, list[tuple]] = {}
    for r in pts:
        by_dev.setdefault(r[4], []).append(r)
    points: list[dict] = []
    dist = 0.0
    for dev, rows in by_dev.items():
        points += [{**p, "device": dev} for p in _draw(rows)]
        dist += _distance(rows)
    places = _known_places(account)
    return True, {"day": day_s, "points": points, "visits": [_visit_out(v, places, now) for v in vis],
                  "distance_m": round(dist)}


def _date_arg(raw: str, end: bool = False) -> float | None:
    if not raw:
        return None
    span = _day_span(raw)
    if span:
        return span[1] if end else span[0]
    try:
        return float(raw)
    except ValueError:
        return None


def visits(account: str, q: str = "", from_: str = "", to: str = "", limit: str | int = 20,
           path: Path | None = None, now: float | None = None) -> tuple[bool, dict]:
    """Visits newest first, those whose place has every word of `q`."""
    now = time.time() if now is None else now
    try:
        lim = max(1, min(int(limit or 20), 200))
    except (TypeError, ValueError):
        lim = 20
    a, b = _date_arg(from_), _date_arg(to, end=True)
    if (from_ and a is None) or (to and b is None):
        return False, {"status": 400, "code": "bad_day", "error": "from and to are YYYY-MM-DD"}
    words = [w for w in q.lower().split() if w]
    with _LOCK:
        conn = _open(path)
        try:
            _prune(conn, now)
            lo = max(a or 0.0, _cutoff(conn, account, now))
            rows = conn.execute('SELECT id, device, lat, lon, start, "end", place FROM visits '
                                'WHERE account=? AND "end">=? AND start<? ORDER BY start DESC',
                                (account, lo, b if b is not None else now + 86400)).fetchall()
        finally:
            conn.close()
    places = _known_places(account)
    out = []
    for r in rows:
        v = _visit_out(r, places, now)
        if words and not all(w in (v.get("place") or "").lower() for w in words):
            continue
        out.append(v)
        if len(out) >= lim:
            break
    return True, {"visits": out}
