"""Saved practice: meditations, yoga, breathwork, any practice kept —
recorded ones beside the meditations the agent writes.

David, 31 Oct 2026: "I'd like to use recorded meditations too" — from
YouTube links, audio files in a folder, and podcast feeds, picked from a
list in the app (server-contract.md §6.45, `GET`/`POST /practice`). 1 Nov
2026: "Maybe we should rename meditation to practise... which also means
it could be used for any kind of practise" — renamed from meditations
everywhere, no aliases; `_moved` brings the old state files across once.

Four sources, one list (`items`), each item ``{id, title, source, …}``:

- **youtube** — a link added by hand (`add`), kept in
  ``<state_dir>/practice.json``. Played as `media book play` plays a
  YouTube link: downloaded once into the library and kept.
- **file** — every audio file under MEDIA_PRACTICE_DIR
  (``~/Music/Practice``), read each time, never stored.
- **podcast** — the newest FEED_EPISODES episodes of each feed added
  (`add` with a feed's URL), read over HTTP and kept for FEED_TTL_S in
  ``<cache>/practice-feeds/``; or one episode saved alone (`add_episode`).
- **made** — the meditations the agent wrote (meditate.py), newest
  MADE_KEPT, titled by the sidecar `remember` writes beside each render.

Each item also says who it is by (`teacher`: the name after the title's
" — ", or a podcast's show), what kind of practice it is and what it is
for (`style`, `benefit`: from the title's words), how long it is
(`duration_s`: a feed's, else looked up in the background — a YouTube
link's by yt-dlp on the phone, a file's or episode's by ffprobe — and
kept in ``practice-durations.json``) and how often it was played (`plays`,
`last_played`, kept in ``practice-plays.json`` by `play`), for the
app's Show ▾ (David, 1 Nov 2026: "Can we have show filters for the
meditation tab too please?"; groupings after Insight Timer's).

Playing (`play`) takes an id from this list only, never a path or URL, so
the route is not a way to play anything on the host. It goes to the book
channel, and marks what it started (``practice-now.json``) so a reply
eases it out rather than cutting it (meditate.is_meditation), as it does a
written meditation.
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


def _moved(new: Path, old: str) -> Path:
    """`new`, the state file under its practice name: one still under its
    old meditation name (before 1 Nov 2026) is moved across the first time."""
    was = new.with_name(old)
    if not new.exists() and was.exists():
        try:
            was.replace(new)
        except OSError:
            return was
    return new


def _store_path() -> Path:
    return _moved(state_dir() / "practice.json", "meditations.json")


def _now_path() -> Path:
    return _moved(state_dir() / "practice-now.json", "meditation-now.json")


def _load() -> dict:
    try:
        d = json.loads(_store_path().read_text())
    except (OSError, ValueError):
        d = {}
    if not isinstance(d, dict):
        d = {}
    d.setdefault("youtube", [])
    d.setdefault("feeds", [])
    d.setdefault("episodes", [])
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
    raw = os.environ.get("MEDIA_PRACTICE_DIR", "").strip()
    return Path(raw).expanduser() if raw else Path.home() / "Music" / "Practice"


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
    return cache_dir() / "practice-feeds" / (_hid("feed", url).split(":")[1] + ".json")


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
    eps = [{"id": e["id"], "title": e.get("title") or e["audio"], "source": "podcast",
            "show": e.get("show", ""), "added": e.get("added", 0), "_uri": e["audio"]}
           for e in d["episodes"]]
    try:
        from .practice_embed import items as _embeds
        embeds = _embeds()
    except Exception:  # noqa: BLE001 — a bad index costs only its own items
        embeds = []
    return _made() + yt + _files() + eps + embeds + _podcasts(d)


def items() -> list[dict]:
    """Everything saved, for the app's list (no paths or URLs), with
    `teacher`, `style`, `benefit`, `duration_s`, `plays` and `last_played`."""
    every = _all()
    plays = _plays()
    lengths = _durations()
    out = []
    for i in every:
        d = {k: v for k, v in i.items() if not k.startswith("_")}
        p = plays.get(i["id"]) or {}
        d.update(teacher=teacher_of(i), style=style_of(i["title"]), benefit=benefit_of(i["title"]),
                 duration_s=_seconds(i.get("duration")) or lengths.get(i["id"]),
                 plays=int(p.get("n") or 0), last_played=p.get("last") or None)
        out.append(d)
    _fill_durations([i for i in every if not _seconds(i.get("duration")) and i["id"] not in lengths])
    return out


# ---- who, what, how long, how often ----------------------------------------------

_BY = re.compile(r"\s+[—–]\s+([^—–]{2,60})$")

#: What kind of practice a title says it is, first match wins (Insight Timer's
#: "Practice"; "Style" here, the app's tab being Practice).
STYLES = (
    ("Yoga Nidra", r"yoga\s*nidra|\bnidra\b"),
    ("Body scan", r"body\s*scan"),
    ("Breathwork", r"breath|pranayama"),
    ("Journey", r"journey|astral|shaman|lucid|out of body|travel|visuali[sz]"),
    ("Sound & mantra", r"\bsound\b|\bhz\b|\bbowls?\b|chant|mantra|asatoma|\bom\b"),
    ("Affirmations", r"affirmation|manifest|abundance|dream life|confiden"),
    ("Movement", r"\byoga\b(?!\s*nidra)|stretch|qigong|tai chi|vinyasa|flow\b"),
    ("Energy & chakra", r"chakra|reiki|cleans|cutting cords|energy wash|wash meditation"),
    ("Talk", r"explained|wisdom|karma|lecture|\btalk\b|\[part \d"),
    ("Music", r"music|piano|prayer|devi\b|lokah|instrumental"),
)
#: A practice no rule names is a guided one.
STYLE_DEFAULT = "Guided"
#: What a title says it is for, first match wins (Insight Timer's "Benefit").
BENEFITS = (
    ("Sleep", r"sleep|insomnia|bed\s*time|middle of the night|night sky|stargaz"),
    ("Morning & energy", r"morning|wake|energy|energi"),
    ("Healing", r"heal|headache|tension|jaw|spin|nervous|pain|reboot|release|trapped|chakra|cleans|cords|karma|embodiment"),
    ("Heart & love", r"heart|love|compassion|kindness|bliss|gratitude|relationship"),
    ("Confidence & strength", r"confiden|strength|power|abundance|manifest|dream life|success|goal|intention|empower|authentic self"),
    ("Intuition & spirit", r"intuition|third eye|spirit|astral|out of body|lucid|soul|upper world|past life|guides|medicine wheel|shaman|prayer|devi\b"),
    ("Calm & grounding", r"calm|stress|anxi|relax|ground|stabili|peace|clarity|focus|fear|wonderment|easy|lokah|rest\b"),
)


def teacher_of(it: dict) -> Optional[str]:
    """Who a practice is by: the name after " — " in its title, else a
    podcast's show; None for the agent's own and an unsigned file."""
    m = _BY.search(it.get("title") or "")
    if m:
        return m.group(1).strip()
    return (it.get("show") or "").strip() or None


def _first(rules: tuple, title: str) -> Optional[str]:
    t = (title or "").lower()
    return next((name for name, rx in rules if re.search(rx, t)), None)


def style_of(title: str) -> str:
    return _first(STYLES, title) or STYLE_DEFAULT


def benefit_of(title: str) -> Optional[str]:
    return _first(BENEFITS, title)


def _seconds(raw) -> Optional[int]:
    """A feed's duration ("754", "12:34", "1:02:03") in seconds."""
    if isinstance(raw, (int, float)):
        return int(raw) or None
    raw = (raw or "").strip()
    if re.fullmatch(r"\d+", raw):
        return int(raw) or None
    if re.fullmatch(r"\d+(?::\d{1,2}){1,2}", raw):
        n = 0
        for part in raw.split(":"):
            n = n * 60 + int(part)
        return n or None
    return None


def _plays_path() -> Path:
    return _moved(state_dir() / "practice-plays.json", "meditation-plays.json")


def _plays() -> dict:
    try:
        d = json.loads(_plays_path().read_text())
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def note_play(item_id: str) -> None:
    """Count a play of `item_id` and when it was."""
    d = _plays()
    p = d.get(item_id) if isinstance(d.get(item_id), dict) else {}
    d[item_id] = {"n": int(p.get("n") or 0) + 1, "last": time.time()}
    path = _plays_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".tmp.{os.getpid()}")
    tmp.write_text(json.dumps(d))
    tmp.replace(path)


def _durations_path() -> Path:
    return _moved(state_dir() / "practice-durations.json", "meditation-durations.json")


def _durations() -> dict:
    try:
        d = json.loads(_durations_path().read_text())
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in d.items() if isinstance(v, (int, float)) and v > 0} if isinstance(d, dict) else {}


#: A length that could not be had is asked again after this long.
RETRY_S = 86400


def _probe(uri: str) -> Optional[int]:
    """A file's or an audio URL's length, by ffprobe (an URL is read only as
    far as its header)."""
    import subprocess
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                            "-of", "default=nw=1:nk=1", uri], capture_output=True, text=True, timeout=30)
        return int(float(r.stdout.strip().splitlines()[0])) if r.returncode == 0 and r.stdout.strip() else None
    except (OSError, ValueError, IndexError, Exception):  # noqa: BLE001
        return None


def _youtube_lengths(vids: list[str]) -> dict:
    """`{vid: seconds}` for YouTube videos, asked of yt-dlp on the phone in
    one call (no download): YouTube refuses this host's address, as
    podcast_links.fetch and audiobook-fetch know."""
    import shlex
    import subprocess
    if not vids:
        return {}
    phone = os.environ.get("AUDIOBOOK_PHONE_HOST", "p8a")
    urls = " ".join(shlex.quote(f"https://www.youtube.com/watch?v={v}") for v in vids)
    remote = f"yt-dlp --no-warnings --ignore-errors --skip-download --print '%(id)s %(duration)s' {urls}"
    try:
        r = subprocess.run(["ssh", "-o", "ConnectTimeout=12", "-o", "BatchMode=yes", phone, remote],
                           capture_output=True, text=True, timeout=60 + 15 * len(vids))
    except (OSError, subprocess.TimeoutExpired):
        return {}
    out = {}
    for line in r.stdout.splitlines():
        vid, _, dur = line.strip().partition(" ")
        try:
            out[vid] = int(float(dur))
        except ValueError:
            continue
    return out


def _measure_all(todo: list[dict]) -> dict:
    """`{id: seconds}` for those that could be measured: YouTube links
    through the phone, files and episode links by ffprobe."""
    got: dict = {}
    yt = {youtube_id(i["_uri"]): i["id"] for i in todo if i.get("_uri", "").startswith("yt:")}
    yt.pop(None, None)
    for vid, n in _youtube_lengths(list(yt)).items():
        if vid in yt and n > 0:
            got[yt[vid]] = n
    for it in todo:
        uri = it.get("_uri") or ""
        if uri.startswith(("/", "http://", "https://")):
            n = _probe(uri)
            if n:
                got[it["id"]] = n
    return got


def _tried() -> dict:
    """Every length asked for: seconds, or `{"failed": at}`."""
    try:
        d = json.loads(_durations_path().read_text())
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


_FILLING: list = [False]


def _fill_durations(missing: list[dict]) -> None:
    """Look up the lengths not known yet in the background; a next listing
    has them. One that could not be had is asked again after RETRY_S."""
    if not missing or _FILLING[0] or os.environ.get("MEDIA_PRACTICE_DURATIONS") == "0":
        return
    import threading
    tried = _tried()
    now = time.time()
    todo = [i for i in missing if not (isinstance(tried.get(i["id"]), dict)
                                       and now - float(tried[i["id"]].get("failed") or 0) < RETRY_S)]
    if not todo:
        return
    _FILLING[0] = True

    def run():
        try:
            shared = _from_central(todo)
            got = {**_measure_all([i for i in todo if i["id"] not in shared]), **shared}
            d = _tried()
            for it in todo:
                d[it["id"]] = got.get(it["id"]) or {"failed": time.time()}
            p = _durations_path()
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(f".tmp.{os.getpid()}")
            tmp.write_text(json.dumps(d))
            tmp.replace(p)
        finally:
            _FILLING[0] = False

    threading.Thread(target=run, name="practice-durations", daemon=True).start()


# ---- the lengths every install shares (central_facts; the three-layers proposal) --

def _shared_path() -> Path:
    return state_dir() / "practice-durations-shared.json"


def _shared() -> dict:
    """`{id: "from" | "sent"}`: a length had from the shared store, or one
    this install measured and sent to it. Neither is sent (again)."""
    try:
        d = json.loads(_shared_path().read_text())
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def _mark_shared(ids: list[str], how: str) -> None:
    if not ids:
        return
    d = _shared()
    d.update({i: how for i in ids})
    p = _shared_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(f".tmp.{os.getpid()}")
    tmp.write_text(json.dumps(d))
    tmp.replace(p)


def _from_central(todo: list[dict]) -> dict:
    """`{id: seconds}` the shared store already knows, for those of `todo`
    that have a shared key (never a file or the agent's own)."""
    from . import central_facts

    keys = {central_facts.key_for(i): i["id"] for i in todo}
    keys.pop(None, None)
    if not keys:
        return {}
    got = {keys[k]: n for k, n in central_facts.lookup(list(keys)).items() if k in keys}
    _mark_shared(list(got), "from")
    return got


def to_report() -> dict:
    """`{id: (key, seconds)}`: lengths this install measured itself that the
    shared store has not been sent yet."""
    from . import central_facts

    lengths = _durations()
    shared = _shared()
    out = {}
    for it in _all():
        if it["id"] in lengths and it["id"] not in shared:
            key = central_facts.key_for(it)
            if key:
                out[it["id"]] = (key, int(lengths[it["id"]]))
    return out


def mark_reported(ids: list[str]) -> None:
    _mark_shared(ids, "sent")


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


def add_episode(audio: str, title: str, show: str = "") -> dict:
    """Save one podcast episode by its audio URL (not its whole feed): the
    free copy of a meditation found in a teacher's podcast. ``ep:<hash>``."""
    audio = (audio or "").strip()
    if not audio.startswith(("http://", "https://")):
        raise ValueError("an episode's audio must be an http(s) URL")
    d = _load()
    eid = _hid("ep", audio)
    d["episodes"] = [e for e in d["episodes"] if e["id"] != eid]
    d["episodes"].append({"id": eid, "audio": audio, "title": title.strip() or audio,
                          "show": show.strip(), "added": time.time()})
    _save(d)
    return {"added": "episode", "id": eid, "title": title}


def remove(key: str) -> bool:
    """Forget a YouTube link (``yt:<id>``), an episode (``ep:<hash>``), a feed
    (by its URL) or one of the agent's renders (``made:<hash>``). Files in the folder are the folder's."""
    d = _load()
    if key.startswith("yt:"):
        before = len(d["youtube"])
        d["youtube"] = [v for v in d["youtube"] if f"yt:{v['vid']}" != key]
        _save(d)
        return len(d["youtube"]) < before
    if key.startswith("ep:"):
        before = len(d["episodes"])
        d["episodes"] = [e for e in d["episodes"] if e["id"] != key]
        _save(d)
        return len(d["episodes"]) < before
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


# ---- a teacher's profile -----------------------------------------------------------

#: Insight Timer's public search and profile data (what its web app reads).
IT_SEARCH = "https://search.insighttimer-api.net/api/v1/top"
IT_USER = "https://users.insighttimer.com/{id}/data/user.json"
IT_PICTURE = "https://users.insighttimer.com/{id}/pictures/square_medium.jpeg"
#: A profile found is kept a month; a teacher not found is asked again after a week.
PROFILE_TTL_S = 30 * 86400
PROFILE_MISS_TTL_S = 7 * 86400


def _profiles_path() -> Path:
    return state_dir() / "practice-teachers.json"


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def _get_json(url: str, timeout: float = 15.0):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (agent-media practice)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _insight_timer(name: str) -> Optional[dict]:
    """The Insight Timer teacher named exactly `name` (letters and digits
    compared), as a profile; None when there is no such teacher."""
    import urllib.parse
    q = urllib.parse.urlencode({"content_langs": "en", "device_lang": "en", "limit": 15, "offset": 0,
                                "publishers_only_users": "true", "query": name, "search_friends": "false"})
    found = None

    def walk(o):
        nonlocal found
        if found is not None:
            return
        if isinstance(o, dict):
            if o.get("is_publisher") and _norm(o.get("name", "")) == _norm(name) and o.get("id"):
                found = o
                return
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(_get_json(f"{IT_SEARCH}?{q}"))
    if not found:
        return None
    uid = found["id"]
    u = _get_json(IT_USER.format(id=uid))
    region = u.get("region") if isinstance(u.get("region"), dict) else {}
    where = (region.get("name") or ", ".join(x for x in (region.get("city"), region.get("country") or region.get("country_name")) if x)) \
        if region else (u.get("region") or "")
    country = (region.get("country") or region.get("country_name") if region else "") or (where.rsplit(",", 1)[-1].strip() if where else "")
    loc = (region.get("location") or {}) if region else {}
    return {"source": "insight-timer", "name": u.get("name") or name,
            "photo": IT_PICTURE.format(id=uid) if u.get("has_avatar") else None,
            "description": (u.get("publisher_description") or "").strip() or None,
            "region": where or None, "country": country or None,
            "state": (region.get("state_long_name") or region.get("state") or None) if region else None,
            "city": (region.get("city") or None) if region else None,
            "lat": loc.get("lat"), "lon": loc.get("lon"), "website": u.get("website") or None,
            "url": u.get("public_url") or None,
            "followers": u.get("publisher_follower_count") or u.get("follower_count") or None,
            "free_tracks": u.get("total_free_tracks") or None,
            "courses": u.get("total_courses") or None,
            "yoga": bool(u.get("is_yoga_teacher"))}


_US_STATES = set("AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM "
                 "NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC".split())
_US_STATE_NAMES = {"alabama", "alaska", "arizona", "arkansas", "california", "colorado", "connecticut", "delaware",
                   "florida", "georgia", "hawaii", "idaho", "illinois", "indiana", "iowa", "kansas", "kentucky",
                   "louisiana", "maine", "maryland", "massachusetts", "michigan", "minnesota", "mississippi",
                   "missouri", "montana", "nebraska", "nevada", "new hampshire", "new jersey", "new mexico",
                   "new york", "north carolina", "north dakota", "ohio", "oklahoma", "oregon", "pennsylvania",
                   "rhode island", "south carolina", "south dakota", "tennessee", "texas", "utah", "vermont",
                   "virginia", "washington", "west virginia", "wisconsin", "wyoming"}
_AU_STATES = {"NSW", "VIC", "QLD", "WA", "SA", "TAS", "ACT", "NT"}
_CA_PROVINCES = {"BC", "AB", "SK", "MB", "ON", "QC", "NB", "NS", "PE", "NL", "YT", "NU"}
_COUNTRY_ALIASES = {"usa": "United States", "us": "United States", "united states of america": "United States",
                    "uk": "United Kingdom", "gb": "United Kingdom", "great britain": "United Kingdom",
                    "england": "United Kingdom", "scotland": "United Kingdom", "wales": "United Kingdom"}


def _country(profile: Optional[dict]) -> Optional[str]:
    """A profile's country, written one way: "USA", "US" and a US state's
    code are the United States, "UK" and England the United Kingdom, an
    Australian state's or Canadian province's code its country."""
    if not profile:
        return None
    raw = (profile.get("country") or (profile.get("region") or "").rsplit(",", 1)[-1]).strip()
    if not raw:
        return None
    code = raw.split()[0].upper() if raw.split() else raw.upper()
    if raw.lower() in _COUNTRY_ALIASES:
        return _COUNTRY_ALIASES[raw.lower()]
    if raw.lower() in _US_STATE_NAMES:
        return "United States"
    last = raw.split()[-1]
    if len(raw.split()) > 1 and last.lower() in _COUNTRY_ALIASES:
        return _COUNTRY_ALIASES[last.lower()]
    if raw.upper() in _AU_STATES or code in _AU_STATES and re.search(r"\d{4}$", raw):
        return "Australia"
    if raw.upper() in _CA_PROVINCES and raw.upper() not in _US_STATES:
        return "Canada"
    if raw.upper() in _US_STATES:
        return "United States"
    return raw


def _state_city(profile: dict) -> tuple[Optional[str], Optional[str]]:
    """A profile's state and city: Insight Timer's own, else from its
    region's words ("Manly NSW 2095, Australia" is Manly, New South
    Wales; "Asheville, NC" Asheville, North Carolina)."""
    state, city = profile.get("state"), profile.get("city")
    if state or city:
        return _state_name(state), city
    parts = [p.strip() for p in (profile.get("region") or "").split(",") if p.strip()]
    if len(parts) >= 2:
        first = re.sub(r"\s+\w*\d\w*$", "", parts[0])
        words = first.split()
        if len(parts) == 2 and len(words) > 1 and words[-1].upper() in _AU_STATES:
            return _state_name(words[-1]), " ".join(words[:-1])
        if len(parts) >= 3:
            return _state_name(re.sub(r"\s+\w*\d\w*$", "", parts[1])), first
        if parts[1].upper() in _US_STATES | _AU_STATES | _CA_PROVINCES:
            return _state_name(parts[1]), first
        return None, first
    return None, None


_STATE_NAMES = {
    "NSW": "New South Wales", "VIC": "Victoria", "QLD": "Queensland", "TAS": "Tasmania", "ACT": "Australian Capital Territory",
    "NT": "Northern Territory", "BC": "British Columbia", "AB": "Alberta", "ON": "Ontario", "QC": "Quebec",
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
    "CT": "Connecticut", "DE": "Delaware", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey",
    "NM": "New Mexico", "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming", "DC": "District of Columbia"}


def _state_name(state: Optional[str]) -> Optional[str]:
    """A state's name, not its code ("CA" is California)."""
    if not state:
        return None
    return _STATE_NAMES.get(state.strip().upper(), state.strip())


def teacher_places(names: list[str]) -> dict:
    """`{name: {"region", "country", "state", "city", "photo"}}` for the teachers whose
    profile is kept (none for one not found), for the Teachers list's
    Where filter; those never looked up are looked up in the background, a
    few seconds apart, and a next listing has them. None where personal use
    does not allow Insight Timer's profiles."""
    from . import personal_use
    if not personal_use.allowed("insight-timer-profiles"):
        return {}
    try:
        kept = json.loads(_profiles_path().read_text())
    except (OSError, ValueError):
        kept = {}
    out, missing = {}, []
    for name in names:
        hit = kept.get(_norm(name))
        if not isinstance(hit, dict):
            missing.append(name)
            continue
        pr = hit.get("profile")
        if pr:
            out[name] = _place_of(pr)
    _fill_profiles(missing)
    return out


def _place_of(pr: dict) -> dict:
    state, city = _state_city(pr)
    return {"region": pr.get("region"), "country": _country(pr), "state": state, "city": city,
            "photo": pr.get("photo")}


_PROFILING: list = [False]


def _fill_profiles(names: list[str]) -> None:
    from . import personal_use
    if not names or _PROFILING[0] or not personal_use.allowed("insight-timer-profiles"):
        return
    import threading
    _PROFILING[0] = True

    def run():
        try:
            for n in names:
                teacher_profile(n)
                time.sleep(1.0)
        finally:
            _PROFILING[0] = False

    threading.Thread(target=run, name="practice-profiles", daemon=True).start()


def teacher_profile(name: str, fresh: bool = False) -> dict:
    """`{"name", "profile": {...} | None}`: who a teacher is, from Insight
    Timer's public profile (photo, description, region, website, followers,
    free tracks), looked up once and kept in ``practice-teachers.json``.
    Only an exact name match counts, so a teacher is never given someone
    else's page; one not found is asked again after a week."""
    name = (name or "").strip()
    if not name:
        return {"name": name, "profile": None}
    try:
        kept = json.loads(_profiles_path().read_text())
    except (OSError, ValueError):
        kept = {}
    key = _norm(name)
    hit = kept.get(key) if isinstance(kept.get(key), dict) else None
    ttl = PROFILE_TTL_S if hit and hit.get("profile") else PROFILE_MISS_TTL_S
    if hit and not fresh and time.time() - float(hit.get("at") or 0) < ttl:
        return {"name": name, "profile": hit.get("profile")}
    from . import personal_use
    if not personal_use.allowed("insight-timer-profiles"):
        # Not asked of Insight Timer's internal endpoints, and what was kept
        # from them not shown, where personal use does not allow it.
        return {"name": name, "profile": None}
    try:
        profile = _insight_timer(name)
    except Exception:  # noqa: BLE001 — the last one known, or none
        return {"name": name, "profile": (hit or {}).get("profile")}
    kept[key] = {"profile": profile, "at": time.time()}
    p = _profiles_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(f".tmp.{os.getpid()}")
    tmp.write_text(json.dumps(kept))
    tmp.replace(p)
    return {"name": name, "profile": profile}


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
    """Whether the book's `path` is the recorded practice last played."""
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
    """The saved practice `query` names: an id, else the title holding
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
    """Play a saved practice on the book channel (by id, or a `match`)."""
    it = match(item_id)
    if not it:
        return {"ok": False, "error": f"nothing saved as {item_id!r}"}
    if it.get("source") == "embed":
        # Played in the maker's own player, by the app: counted here, nothing
        # started on the book channel (practice_embed).
        note_play(it["id"])
        return {"ok": True, "id": it["id"], "title": it.get("title"),
                "embed": it["embed"], "page": it.get("page")}
    from . import mcp_server
    mark_playing(it["_uri"])
    note_play(it["id"])
    r = mcp_server.book_play(it["_uri"], resume=resume, start_ms=-1,
                             target=target, title=it.get("title") or "Practice")
    r = r if isinstance(r, dict) else {}
    return {**r, "ok": not r.get("error"), "id": it["id"], "title": it.get("title")}


def uri_for(item_id: str) -> Optional[str]:
    it = find(item_id)
    return it["_uri"] if it else None
