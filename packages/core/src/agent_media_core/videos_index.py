"""An index of the uploads of every channel a YouTube account subscribes to,
so the Videos tab searches all of an account's subscriptions at once, with
no request to YouTube per search (server-contract §6.70; David, 26 Nov 2026,
asked for "another way" than searching each channel live, and to filter
channels "by region or category").

Built in the background, in the server, through the personal-use add-on's
YouTube Data API reads (youtube_addon "youtube-accounts" `api`), with each
account's own token:

1. each followed channel's details, 50 a call: its title, country, video
   count and YouTube's topic categories (1 unit a call);
2. each channel's uploads playlist ("UU…"), newest first, 50 a call, up to
   MAX_PER_CHANNEL videos: title, description, date (1 unit a call). A
   channel half done resumes from its page token after a restart;
3. a channel done is kept fresh from its free RSS feed (its 15 newest)
   every RSS_EVERY_S, without quota.

The owner's Google project has 10,000 units a day (reset at midnight
Pacific time); the index spends at most DAY_UNITS of them, so the rest of
the add-on keeps its share. The account searched most recently goes first,
and within it the channels with fewest videos, so most channels are
searchable soonest.

The data lives in `<state_dir>/videos-index.db` (SQLite, FTS5): nothing
leaves the server.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Callable, Optional

from . import failures as _failures

MAX_PER_CHANNEL = 500
DAY_UNITS = 8000
RSS_EVERY_S = 6 * 3600.0
#: An API step's gap, an RSS fetch's gap, and the sleeps when there is
#: nothing to do or the day's units are spent.
STEP_GAP_S, RSS_GAP_S, IDLE_S, SPENT_S = 0.3, 1.0, 600.0, 1800.0

Api = Callable[[str, str, dict], dict]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS channels(id TEXT PRIMARY KEY, title TEXT, country TEXT, topics TEXT,
    videos INTEGER, detailed_at REAL, page TEXT, got INTEGER DEFAULT 0, done INTEGER DEFAULT 0,
    rss_at REAL DEFAULT 0, failed TEXT);
CREATE TABLE IF NOT EXISTS follows(account TEXT, channel TEXT, PRIMARY KEY(account, channel));
CREATE INDEX IF NOT EXISTS follows_channel ON follows(channel);
CREATE TABLE IF NOT EXISTS videos(id TEXT PRIMARY KEY, channel TEXT, title TEXT, description TEXT,
    published INTEGER, duration INTEGER);
CREATE INDEX IF NOT EXISTS videos_channel ON videos(channel);
CREATE VIRTUAL TABLE IF NOT EXISTS videos_fts USING fts5(title, description, content='videos',
    content_rowid='rowid', tokenize='unicode61 remove_diacritics 2');
CREATE TRIGGER IF NOT EXISTS videos_ai AFTER INSERT ON videos BEGIN
    INSERT INTO videos_fts(rowid, title, description) VALUES (new.rowid, new.title, new.description); END;
CREATE TRIGGER IF NOT EXISTS videos_ad AFTER DELETE ON videos BEGIN
    INSERT INTO videos_fts(videos_fts, rowid, title, description) VALUES ('delete', old.rowid, old.title, old.description); END;
CREATE TRIGGER IF NOT EXISTS videos_au AFTER UPDATE OF title, description ON videos BEGIN
    INSERT INTO videos_fts(videos_fts, rowid, title, description) VALUES ('delete', old.rowid, old.title, old.description);
    INSERT INTO videos_fts(rowid, title, description) VALUES (new.rowid, new.title, new.description); END;
CREATE TABLE IF NOT EXISTS quota(day TEXT PRIMARY KEY, units INTEGER);
"""

_LOCK = threading.RLock()
#: Set when there may be new work (an account's follows read), so the
#: indexer does not sleep out its idle wait.
_WAKE = threading.Event()
_CONN: dict = {}
_PRIORITY: list = []


def db_path() -> Path:
    from ._paths import state_dir
    return state_dir() / "videos-index.db"


def _db(path: Optional[Path] = None) -> sqlite3.Connection:
    p = Path(path) if path else db_path()
    key = str(p)
    with _LOCK:
        c = _CONN.get(key)
        if c is None:
            p.parent.mkdir(parents=True, exist_ok=True)
            c = sqlite3.connect(key, check_same_thread=False, timeout=30)
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript(_SCHEMA)
            _CONN[key] = c
        return c


def close() -> None:
    """Forget the open databases (tests)."""
    with _LOCK:
        for c in _CONN.values():
            c.close()
        _CONN.clear()
        _PRIORITY.clear()


def _day(now: float) -> str:
    """Google's quota day: midnight to midnight Pacific time."""
    try:
        from zoneinfo import ZoneInfo
        import datetime as dt
        return dt.datetime.fromtimestamp(now, ZoneInfo("America/Los_Angeles")).strftime("%Y-%m-%d")
    except Exception as e:  # noqa: BLE001 — no tz data: UTC-8
        _failures.swallowed(e)
        return time.strftime("%Y-%m-%d", time.gmtime(now - 8 * 3600))


def units_left(now: Optional[float] = None, path: Optional[Path] = None) -> int:
    now = time.time() if now is None else now
    row = _db(path).execute("SELECT units FROM quota WHERE day=?", (_day(now),)).fetchone()
    return DAY_UNITS - (row[0] if row else 0)


def _spend(n: int, now: float, path: Optional[Path] = None) -> None:
    c = _db(path)
    with _LOCK:
        c.execute("INSERT INTO quota(day, units) VALUES (?, ?) ON CONFLICT(day) DO UPDATE SET units=units+?",
                  (_day(now), n, n))
        c.commit()


def set_follows(account: str, channels: list[dict], path: Optional[Path] = None) -> None:
    """The channels `account` subscribes to now (its subscriptions read)."""
    c = _db(path)
    with _LOCK:
        c.execute("DELETE FROM follows WHERE account=?", (account,))
        for ch in channels:
            if not ch.get("id"):
                continue
            c.execute("INSERT OR IGNORE INTO channels(id, title) VALUES (?, ?)", (ch["id"], ch.get("title")))
            c.execute("INSERT OR IGNORE INTO follows(account, channel) VALUES (?, ?)", (account, ch["id"]))
        c.commit()
    _WAKE.set()


def prefer(account: str) -> None:
    """Index this account's channels next (it was just searched)."""
    with _LOCK:
        if account in _PRIORITY:
            _PRIORITY.remove(account)
        _PRIORITY.insert(0, account)


def _topic(url: str) -> str:
    """A topic category's Wikipedia URL as its name: "Lifestyle (sociology)" → "Lifestyle"."""
    name = urllib.parse.unquote(str(url).rsplit("/", 1)[-1]).replace("_", " ")
    return re.sub(r"\s*\(.*\)$", "", name).strip()


def _iso(s: Any) -> Optional[int]:
    import calendar
    try:
        return calendar.timegm(time.strptime(str(s)[:19], "%Y-%m-%dT%H:%M:%S"))
    except (ValueError, TypeError):
        return None


def _iso_duration(s: Any) -> Optional[int]:
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", str(s or ""))
    if not m or not any(m.groups()):
        return None
    d, h, mi, se = (int(x or 0) for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + se


def _owner(c: sqlite3.Connection, channel: str) -> Optional[str]:
    """An account following `channel`, whose token asks for it."""
    for (acct,) in c.execute("SELECT account FROM follows WHERE channel=?", (channel,)):
        if acct in _PRIORITY:
            return acct
    row = c.execute("SELECT account FROM follows WHERE channel=? LIMIT 1", (channel,)).fetchone()
    return row[0] if row else None


def _order() -> str:
    """SQL ordering the priority accounts' channels first."""
    with _LOCK:
        pri = list(_PRIORITY[:20])
    if not pri:
        return "(0+0)"   # a plain 0 would name a column
    cases = " ".join(f"WHEN EXISTS(SELECT 1 FROM follows f WHERE f.channel=channels.id AND f.account=?) THEN {i}"
                     for i in range(len(pri)))
    return f"CASE {cases} ELSE {len(pri)} END"


def step(api: Api, now: Optional[float] = None, path: Optional[Path] = None,
         fetch_rss: Optional[Callable[[str], bytes]] = None) -> str:
    """Do one piece of indexing. Answers what it did: "details", "uploads",
    "rss", "spent" (no units left today) or "idle" (nothing to do)."""
    now = time.time() if now is None else now
    c = _db(path)
    with _LOCK:
        pri = list(_PRIORITY[:20])
    order = _order()
    if units_left(now, path) > 0:
        rows = c.execute(f"SELECT id FROM channels WHERE detailed_at IS NULL AND id IN (SELECT channel FROM follows) "
                         f"ORDER BY {order} LIMIT 50", pri).fetchall()
        if rows:
            ids = [r[0] for r in rows]
            owner = _owner(c, ids[0])
            got = api(owner or "", "channels", {"part": "snippet,topicDetails,statistics", "id": ",".join(ids),
                                                "maxResults": 50})
            _spend(1, now, path)
            seen = set()
            with _LOCK:
                for it in got.get("items") or []:
                    sn, st = it.get("snippet") or {}, it.get("statistics") or {}
                    topics = sorted({_topic(u) for u in (it.get("topicDetails") or {}).get("topicCategories") or []})
                    c.execute("UPDATE channels SET title=COALESCE(?, title), country=?, topics=?, videos=?, detailed_at=? "
                              "WHERE id=?", (sn.get("title"), (sn.get("country") or None), json.dumps(topics),
                                             int(st.get("videoCount") or 0), now, it["id"]))
                    seen.add(it["id"])
                    if not int(st.get("videoCount") or 0):   # no uploads: nothing to ask for
                        c.execute("UPDATE channels SET done=1, rss_at=? WHERE id=?", (now, it["id"]))
                for cid in set(ids) - seen:   # gone or hidden: nothing to index
                    c.execute("UPDATE channels SET detailed_at=?, videos=0, done=1, failed='not found' WHERE id=?",
                              (now, cid))
                c.commit()
            return "details"
        row = c.execute(f"SELECT id, page, got FROM channels WHERE done=0 AND detailed_at IS NOT NULL "
                        f"AND id IN (SELECT channel FROM follows) ORDER BY {order}, videos LIMIT 1", pri).fetchone()
        if row:
            cid, page, n = row
            params = {"part": "snippet", "playlistId": "UU" + cid[2:], "maxResults": 50, **({"pageToken": page} if page else {})}
            try:
                got = api(_owner(c, cid) or "", "playlistItems", params)
            except Exception as e:  # noqa: BLE001 — a channel with no uploads list is done, the rest go on
                _spend(1, now, path)
                with _LOCK:
                    c.execute("UPDATE channels SET done=1, failed=? WHERE id=?", (str(e)[:200], cid))
                    c.commit()
                return "uploads"
            _spend(1, now, path)
            items = got.get("items") or []
            with _LOCK:
                for it in items:
                    sn = it.get("snippet") or {}
                    vid = (sn.get("resourceId") or {}).get("videoId")
                    if not vid or sn.get("title") in ("Private video", "Deleted video"):
                        continue
                    c.execute("INSERT OR IGNORE INTO videos(id, channel, title, description, published) VALUES (?, ?, ?, ?, ?)",
                              (vid, cid, sn.get("title") or "", (sn.get("description") or "")[:2000],
                               _iso(sn.get("publishedAt"))))
                nxt = got.get("nextPageToken")
                n += len(items)
                done = 1 if not nxt or n >= MAX_PER_CHANNEL else 0
                c.execute("UPDATE channels SET page=?, got=?, done=?, rss_at=? WHERE id=?",
                          (nxt, n, done, now if done else 0, cid))
                c.commit()
            return "uploads"
    row = c.execute("SELECT id FROM channels WHERE done=1 AND failed IS NULL AND rss_at < ? "
                    "AND id IN (SELECT channel FROM follows) ORDER BY rss_at LIMIT 1", (now - RSS_EVERY_S,)).fetchone()
    if row:
        cid = row[0]
        try:
            xml = (fetch_rss or _rss)(cid)
            fresh = parse_rss(xml)
        except Exception as e:  # noqa: BLE001 — tried again next round
            _failures.swallowed(e)
            fresh = []
        with _LOCK:
            for v in fresh:
                c.execute("INSERT OR IGNORE INTO videos(id, channel, title, description, published) VALUES (?, ?, ?, ?, ?)",
                          (v["id"], cid, v["title"], v["description"], v["published"]))
            c.execute("UPDATE channels SET rss_at=? WHERE id=?", (now, cid))
            c.commit()
        return "rss"
    return "spent" if units_left(now, path) <= 0 and c.execute(
        "SELECT 1 FROM channels WHERE done=0 AND id IN (SELECT channel FROM follows) LIMIT 1").fetchone() else "idle"


def _rss(cid: str) -> bytes:
    url = "https://www.youtube.com/feeds/videos.xml?channel_id=" + urllib.parse.quote(cid)
    req = urllib.request.Request(url, headers={"User-Agent": "agent-media/1.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.read(2_000_000)


_ATOM, _YT, _MEDIA = "{http://www.w3.org/2005/Atom}", "{http://www.youtube.com/xml/schemas/2015}", "{http://search.yahoo.com/mrss/}"


def parse_rss(xml: bytes | str) -> list[dict]:
    """A channel's feed as ``[{id, title, description, published}]``."""
    out = []
    for e in ET.fromstring(xml).findall(f"{_ATOM}entry"):
        vid = (e.findtext(f"{_YT}videoId") or "").strip()
        if not vid:
            continue
        out.append({"id": vid, "title": (e.findtext(f"{_ATOM}title") or "").strip(),
                    "description": (e.findtext(f"{_MEDIA}group/{_MEDIA}description") or "")[:2000],
                    "published": _iso(e.findtext(f"{_ATOM}published"))})
    return out


_THREAD: list = []


def start(api_of: Callable[[], Optional[Api]]) -> bool:
    """Run the indexer in the background (once per process). `api_of`
    answers the add-on's `api` each round, or None to wait."""
    with _LOCK:
        if _THREAD:
            return False
        t = threading.Thread(target=_run, args=(api_of,), name="videos-index", daemon=True)
        _THREAD.append(t)
    t.start()
    return True


def _run(api_of: Callable[[], Optional[Api]]) -> None:
    while True:
        api = api_of()
        if api is None:
            time.sleep(IDLE_S)
            continue
        try:
            did = step(api)
        except Exception as e:  # noqa: BLE001 — the indexer must not die; it tries again later
            _failures.swallowed(e)
            did = "error"
        gap = {"details": STEP_GAP_S, "uploads": STEP_GAP_S, "rss": RSS_GAP_S,
               "spent": SPENT_S, "error": 60.0}.get(did, IDLE_S)
        if did in ("idle", "spent"):
            _WAKE.clear()
            _WAKE.wait(gap)
        else:
            time.sleep(gap)


# ---- what the Videos tab asks --------------------------------------------------

def progress(account: str, path: Optional[Path] = None) -> dict:
    """``{channels, indexed, videos}``: how many channels the account
    follows, how many are fully indexed, how many videos are searchable."""
    c = _db(path)
    n, done = c.execute("SELECT COUNT(*), COALESCE(SUM(c.done), 0) FROM follows f JOIN channels c ON c.id=f.channel "
                        "WHERE f.account=?", (account,)).fetchone()
    vids = c.execute("SELECT COUNT(*) FROM videos v JOIN follows f ON f.channel=v.channel AND f.account=?",
                     (account,)).fetchone()[0]
    return {"channels": n, "indexed": int(done or 0), "videos": vids}


def facets(account: str, path: Optional[Path] = None) -> dict:
    """``{"topics": [{id, name, channels}], "countries": [{id, channels}]}``
    over the channels the account follows, most channels first."""
    c = _db(path)
    topics: dict = {}
    countries: dict = {}
    for t, co in c.execute("SELECT c.topics, c.country FROM follows f JOIN channels c ON c.id=f.channel "
                           "WHERE f.account=?", (account,)):
        for name in json.loads(t or "[]"):
            topics[name] = topics.get(name, 0) + 1
        if co:
            countries[co] = countries.get(co, 0) + 1
    return {"topics": [{"id": k, "name": k, "channels": v} for k, v in sorted(topics.items(), key=lambda x: (-x[1], x[0]))],
            "countries": [{"id": k, "channels": v} for k, v in sorted(countries.items(), key=lambda x: (-x[1], x[0]))]}


def channel_ids(account: str, topic: str = "", country: str = "", indexed: Optional[bool] = None,
                path: Optional[Path] = None) -> list[str]:
    """The followed channels, narrowed by topic and country; `indexed`
    True for those with videos in the index, False for those with none yet."""
    c = _db(path)
    sql = "SELECT c.id, c.topics, c.got, c.done FROM follows f JOIN channels c ON c.id=f.channel WHERE f.account=?"
    args: list = [account]
    if country:
        sql += " AND c.country=?"
        args.append(country.upper())
    out = []
    for cid, t, got, done in c.execute(sql, args):
        if topic and topic not in json.loads(t or "[]"):
            continue
        has = bool(got) or bool(done)
        if indexed is None or indexed == has:
            out.append(cid)
    return out


def _match(q: str) -> str:
    """The words as an FTS5 query: each word (a prefix for the last), all needed."""
    words = re.findall(r"\w+", q.casefold())
    if not words:
        return ""
    return " ".join(f'"{w}"' for w in words[:-1]) + (" " if len(words) > 1 else "") + f'"{words[-1]}"*'


def search(account: str, q: str, topic: str = "", country: str = "", since: Optional[int] = None,
           sort: str = "", limit: int = 12, offset: int = 0, path: Optional[Path] = None) -> list[dict]:
    """Indexed videos of the channels `account` follows that hold every word,
    best first (the title counts five times the description), or newest
    first (`sort="date"`)."""
    m = _match(q)
    if not m:
        return []
    c = _db(path)
    sql = ("SELECT v.id, v.channel, v.title, v.published, v.duration, c.title, c.topics, bm25(videos_fts, 5.0, 1.0) AS r "
           "FROM videos_fts JOIN videos v ON v.rowid=videos_fts.rowid JOIN channels c ON c.id=v.channel "
           "JOIN follows f ON f.channel=v.channel AND f.account=? WHERE videos_fts MATCH ?")
    args: list = [account, m]
    if country:
        sql += " AND c.country=?"
        args.append(country.upper())
    if topic:
        sql += " AND c.topics LIKE ?"
        args.append(f'%{json.dumps(topic)}%')
    if since:
        sql += " AND v.published >= ?"
        args.append(int(since))
    sql += " ORDER BY " + ("v.published DESC" if sort == "date" else "r") + " LIMIT ? OFFSET ?"
    args += [int(limit), int(offset)]
    try:
        rows = c.execute(sql, args).fetchall()
    except sqlite3.OperationalError as e:   # a query FTS5 cannot read: no rows
        _failures.swallowed(e)
        return []
    return [{"id": r[0], "channel": r[1], "title": r[2], "published": r[3], "duration_s": r[4], "channel_title": r[5]}
            for r in rows]


def fill_durations(api: Api, account: str, ids: list[str], path: Optional[Path] = None) -> dict:
    """``{id: seconds}`` for these videos: those known from the index, the
    rest asked of the Data API (videos.list, 1 unit per 50) and kept."""
    c = _db(path)
    out: dict = {}
    missing = []
    for vid in ids:
        row = c.execute("SELECT duration FROM videos WHERE id=?", (vid,)).fetchone()
        if row and row[0] is not None:
            out[vid] = row[0]
        else:
            missing.append(vid)
    for i in range(0, len(missing), 50):
        chunk = missing[i:i + 50]
        got = api(account, "videos", {"part": "contentDetails", "id": ",".join(chunk), "maxResults": 50})
        _spend(1, time.time(), path)
        with _LOCK:
            for it in got.get("items") or []:
                secs = _iso_duration((it.get("contentDetails") or {}).get("duration"))
                if secs is not None:
                    out[it["id"]] = secs
                    c.execute("UPDATE videos SET duration=? WHERE id=?", (secs, it["id"]))
            c.commit()
    return out


def units_needed(account: Optional[str] = None, path: Optional[Path] = None) -> int:
    """About how many units the index still needs (for one account, or all)."""
    c = _db(path)
    sql = "SELECT videos, got FROM channels WHERE done=0"
    args: list = []
    if account:
        sql += " AND id IN (SELECT channel FROM follows WHERE account=?)"
        args.append(account)
    total = 0
    for videos, got in c.execute(sql, args):
        left = max(0, min(videos or MAX_PER_CHANNEL, MAX_PER_CHANNEL) - (got or 0))
        total += -(-left // 50)
    return total
