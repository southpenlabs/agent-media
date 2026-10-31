"""Saved meditations: recorded ones beside the ones the agent writes.

David, 31 Oct 2026: "I'd like to use recorded meditations too" — from
YouTube links, audio files in a folder, and podcast feeds, picked from a
list in the app (server-contract.md §6.45, `GET`/`POST /meditations`).

Four sources, one list (`items`), each item ``{id, title, source, …}``:

- **youtube** — a link added by hand (`add`), kept in
  ``<state_dir>/meditations.json``. Played as `media book play` plays a
  YouTube link: downloaded once into the library and kept.
- **file** — every audio file under MEDIA_MEDITATION_DIR
  (``~/Music/Meditations``), read each time, never stored.
- **podcast** — the newest FEED_EPISODES episodes of each feed added
  (`add` with a feed's URL), read over HTTP and kept for FEED_TTL_S in
  ``<cache>/meditation-feeds/``.
- **made** — the ones the agent wrote (meditate.py), newest
  MADE_KEPT, titled by the sidecar `remember` writes beside each render.

Playing (`play`) takes an id from this list only, never a path or URL, so
the route is not a way to play anything on the host. It goes to the book
channel, and marks what it started (``meditation-now.json``) so a reply
eases it out rather than cutting it (meditate.is_meditation), as it does a
written one.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

from ._paths import cache_dir, state_dir

AUDIO_EXTS = {".mp3", ".m4a", ".m4b", ".aac", ".ogg", ".opus", ".flac", ".wav"}
FEED_EPISODES = 20
FEED_TTL_S = 6 * 3600
MADE_KEPT = 20
_YT = re.compile(r"(?:youtube\.com/(?:watch\?(?:.*&)?v=|shorts/|live/)|youtu\.be/)([A-Za-z0-9_-]{11})")


def _store_path() -> Path:
    return state_dir() / "meditations.json"


def _now_path() -> Path:
    return state_dir() / "meditation-now.json"


def _load() -> dict:
    try:
        d = json.loads(_store_path().read_text())
    except (OSError, ValueError):
        d = {}
    if not isinstance(d, dict):
        d = {}
    d.setdefault("youtube", [])
    d.setdefault("feeds", [])
    return d


def _save(d: dict) -> None:
    p = _store_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(f".tmp.{os.getpid()}")
    tmp.write_text(json.dumps(d, indent=1))
    tmp.replace(p)


def _hid(prefix: str, raw: str) -> str:
    return f"{prefix}:{hashlib.sha256(raw.encode()).hexdigest()[:12]}"


def folder() -> Path:
    raw = os.environ.get("MEDIA_MEDITATION_DIR", "").strip()
    return Path(raw).expanduser() if raw else Path.home() / "Music" / "Meditations"


def youtube_id(url: str) -> Optional[str]:
    m = _YT.search(url or "")
    return m.group(1) if m else None


# ---- the agent's own ------------------------------------------------------------

def remember(clip: Path, title: str) -> None:
    """Title a render (meditate.py) so the list can show it. Best effort."""
    try:
        clip.with_suffix(".json").write_text(json.dumps({"title": title, "at": time.time()}))
    except OSError:
        pass


def _made() -> list[dict]:
    from . import meditate
    out = []
    try:
        clips = sorted(meditate.out_dir().glob("meditation-*.mp3"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return []
    for clip in clips[:MADE_KEPT]:
        try:
            meta = json.loads(clip.with_suffix(".json").read_text())
        except (OSError, ValueError):
            meta = {}
        out.append({"id": f"made:{clip.stem.split('-', 1)[1]}",
                    "title": meta.get("title") or "Meditation",
                    "source": "made", "added": meta.get("at") or clip.stat().st_mtime,
                    "_uri": str(clip)})
    return out


# ---- files ---------------------------------------------------------------------

def _title_from_name(p: Path) -> str:
    t = re.sub(r"\s*\[[A-Za-z0-9_-]{11}\]$", "", p.stem)   # yt-dlp's [id]
    return re.sub(r"[_]+", " ", t).strip() or p.name


def _files() -> list[dict]:
    root = folder()
    if not root.is_dir():
        return []
    out = []
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.suffix.lower() in AUDIO_EXTS and not p.name.startswith("."):
            out.append({"id": _hid("file", str(p)), "title": _title_from_name(p),
                        "source": "file", "added": p.stat().st_mtime, "_uri": str(p)})
    return out


# ---- podcasts ------------------------------------------------------------------

def _feed_cache(url: str) -> Path:
    return cache_dir() / "meditation-feeds" / (_hid("feed", url).split(":")[1] + ".json")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_feed(xml_bytes: bytes, limit: int = FEED_EPISODES) -> dict:
    """An RSS feed's title and newest episodes with audio:
    ``{"title", "episodes": [{"title", "url", "published", "duration"}]}``."""
    root = ET.fromstring(xml_bytes)
    channel = next((e for e in root.iter() if _local(e.tag) == "channel"), root)
    title = ""
    episodes = []
    for child in channel:
        name = _local(child.tag)
        if name == "title" and not title:
            title = (child.text or "").strip()
        elif name == "item":
            ep = {"title": "", "url": "", "published": "", "duration": ""}
            for f in child:
                fn = _local(f.tag)
                if fn == "title":
                    ep["title"] = (f.text or "").strip()
                elif fn == "enclosure" and (f.get("type", "").startswith("audio")
                                            or not f.get("type")):
                    ep["url"] = f.get("url", "")
                elif fn == "pubDate":
                    ep["published"] = (f.text or "").strip()
                elif fn == "duration":
                    ep["duration"] = (f.text or "").strip()
            if ep["url"].startswith(("http://", "https://")):
                episodes.append(ep)
                if len(episodes) >= limit:
                    break
    return {"title": title, "episodes": episodes}


def _fetch(url: str, timeout: float = 15.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "agent-media"})
    with urllib.request.urlopen(req, timeout=timeout) as r:   # noqa: S310 — http(s) only, checked by add
        return r.read(8 * 1024 * 1024)


def read_feed(url: str, fresh: bool = False) -> Optional[dict]:
    """The feed, from its cache while it is under FEED_TTL_S old (or when it
    cannot be fetched), else fetched. None when neither is there."""
    cache = _feed_cache(url)
    try:
        cached = json.loads(cache.read_text())
    except (OSError, ValueError):
        cached = None
    if cached and not fresh and time.time() - cached.get("at", 0) < FEED_TTL_S:
        return cached
    try:
        got = {**parse_feed(_fetch(url)), "at": time.time()}
    except Exception:  # noqa: BLE001 — a dead feed keeps its last episodes
        return cached
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(got))
    return got


def _podcasts(d: dict) -> list[dict]:
    out = []
    for feed in d["feeds"]:
        got = read_feed(feed["url"]) or {}
        show = feed.get("title") or got.get("title") or feed["url"]
        for ep in got.get("episodes", []):
            out.append({"id": _hid("pod", ep["url"]), "title": ep["title"] or show,
                        "source": "podcast", "show": show,
                        "published": ep.get("published", ""),
                        "duration": ep.get("duration", ""), "_uri": ep["url"]})
    return out


# ---- the list ------------------------------------------------------------------

def _all() -> list[dict]:
    d = _load()
    yt = [{"id": f"yt:{v['vid']}", "title": v.get("title") or v["url"],
           "source": "youtube", "added": v.get("added", 0), "_uri": f"yt:{v['url']}"}
          for v in d["youtube"]]
    return _made() + yt + _files() + _podcasts(d)


def items() -> list[dict]:
    """Every saved meditation, for the app's list (no paths or URLs)."""
    return [{k: v for k, v in i.items() if not k.startswith("_")} for i in _all()]


def feeds() -> list[dict]:
    return [{"url": f["url"], "title": f.get("title", "")} for f in _load()["feeds"]]


def _yt_title(url: str) -> str:
    try:
        import subprocess
        r = subprocess.run(["yt-dlp", "--no-warnings", "--skip-download", "--print", "title", url],
                           capture_output=True, text=True, timeout=30)
        return r.stdout.strip().splitlines()[0] if r.returncode == 0 and r.stdout.strip() else ""
    except (OSError, IndexError, Exception):  # noqa: BLE001
        return ""


def add(url: str, title: str = "") -> dict:
    """Save a YouTube link or a podcast feed. Returns ``{"added": "youtube" |
    "podcast", "id"?, "title"}``; raises ValueError for anything else."""
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        raise ValueError("a link must start with http:// or https://")
    d = _load()
    vid = youtube_id(url)
    if vid:
        title = title.strip() or _yt_title(url) or url
        d["youtube"] = [v for v in d["youtube"] if v["vid"] != vid]
        d["youtube"].append({"vid": vid, "url": f"https://www.youtube.com/watch?v={vid}",
                             "title": title, "added": time.time()})
        _save(d)
        return {"added": "youtube", "id": f"yt:{vid}", "title": title}
    got = read_feed(url, fresh=True)
    if not got or not got.get("episodes"):
        raise ValueError("that link is neither a YouTube video nor a podcast feed with audio")
    title = title.strip() or got.get("title") or url
    d["feeds"] = [f for f in d["feeds"] if f["url"] != url]
    d["feeds"].append({"url": url, "title": title, "added": time.time()})
    _save(d)
    return {"added": "podcast", "title": title, "episodes": len(got["episodes"])}


def remove(key: str) -> bool:
    """Forget a YouTube link (``yt:<id>``), a feed (by its URL) or one of the
    agent's renders (``made:<hash>``). Files in the folder are the folder's."""
    d = _load()
    if key.startswith("yt:"):
        before = len(d["youtube"])
        d["youtube"] = [v for v in d["youtube"] if f"yt:{v['vid']}" != key]
        _save(d)
        return len(d["youtube"]) < before
    if key.startswith("made:"):
        from . import meditate
        clip = meditate.out_dir() / f"meditation-{key[5:]}.mp3"
        if not re.fullmatch(r"[0-9a-f]{16}", key[5:]) or not clip.exists():
            return False
        clip.unlink(missing_ok=True)
        clip.with_suffix(".json").unlink(missing_ok=True)
        return True
    before = len(d["feeds"])
    d["feeds"] = [f for f in d["feeds"] if f["url"] != key]
    _save(d)
    return len(d["feeds"]) < before


def find(item_id: str) -> Optional[dict]:
    return next((i for i in _all() if i["id"] == item_id), None)


# ---- playing -------------------------------------------------------------------

def mark_playing(uri: str) -> None:
    """Remember what the book channel was given, so a reply eases it out."""
    vid = youtube_id(uri)
    p = _now_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"uri": uri, "name": Path(uri.split("?")[0]).name,
                             "vid": vid or "", "at": time.time()}))


def is_marked(path: Optional[str]) -> bool:
    """Whether the book's `path` is the recorded meditation last played."""
    if not path:
        return False
    try:
        mark = json.loads(_now_path().read_text())
    except (OSError, ValueError):
        return False
    uri = mark.get("uri", "")
    if path == uri or (uri.startswith("yt:") and path == uri[3:]):
        return True
    vid = mark.get("vid")
    if vid and (f"[{vid}]" in path or youtube_id(path) == vid):
        return True
    name = mark.get("name")
    return bool(name) and Path(path.split("?")[0]).name == name


def match(query: str) -> Optional[dict]:
    """The saved meditation `query` names: an id, else the title holding
    every word of it (the newest such), else None."""
    q = (query or "").strip()
    if not q:
        return None
    hit = find(q)
    if hit:
        return hit
    words = [w for w in re.findall(r"\w+", q.lower())]
    for it in _all():
        title = f"{it.get('title', '')} {it.get('show', '')}".lower()
        if words and all(w in title for w in words):
            return it
    return None


def play(item_id: str, target: str = "", resume: bool = False) -> dict:
    """Play a saved meditation on the book channel (by id, or a `match`)."""
    it = match(item_id)
    if not it:
        return {"ok": False, "error": f"no saved meditation {item_id!r}"}
    from . import mcp_server
    mark_playing(it["_uri"])
    r = mcp_server.book_play(it["_uri"], resume=resume, start_ms=-1,
                             target=target, title=it.get("title") or "Meditation")
    r = r if isinstance(r, dict) else {}
    return {**r, "ok": not r.get("error"), "id": it["id"], "title": it.get("title")}


def uri_for(item_id: str) -> Optional[str]:
    it = find(item_id)
    return it["_uri"] if it else None
