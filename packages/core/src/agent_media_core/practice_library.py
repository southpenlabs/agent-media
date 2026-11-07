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
- **file** — every audio or video file under MEDIA_PRACTICE_DIR
  (``~/Music/Practice``), read each time, never stored. A video file
  (VIDEO_TYPES) also says ``video: "file"``.
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

A video, a saved YouTube link (``video: "youtube"``, ``youtube_id``) or a
video file, can also be watched in the app (`watch`, `served`); playing it
still plays only its sound on the book channel.

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
#: Video files the folder also lists, watchable in the app (`watch`): the
#: kinds Android's WebView plays, as the app's video chips take.
VIDEO_TYPES = {".mp4": "video/mp4", ".m4v": "video/mp4", ".webm": "video/webm",
               ".mov": "video/quicktime"}
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
        ext = p.suffix.lower()
        if p.is_file() and (ext in AUDIO_EXTS or ext in VIDEO_TYPES) and not p.name.startswith("."):
            it = {"id": _hid("file", str(p)), "title": _title_from_name(p),
                  "source": "file", "added": p.stat().st_mtime, "_uri": str(p)}
            if ext in VIDEO_TYPES:
                it["video"] = "file"
            out.append(it)
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
           "source": "youtube", "added": v.get("added", 0), "_uri": f"yt:{v['url']}",
           "video": "youtube", "youtube_id": v["vid"], "kept": _kept_state(v["vid"])}
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
    `teacher`, `style` (and `style_path`, `style_custom`), `benefit`,
    `duration_s`, `plays` and `last_played`."""
    every = _all()
    plays = _plays()
    custom = _custom_styles()
    lengths = _durations()
    out = []
    for i in every:
        d = {k: v for k, v in i.items() if not k.startswith("_")}
        p = plays.get(i["id"]) or {}
        path = custom.get(i["id"]) or style_path_of(i["title"])
        d.update(teacher=teacher_of(i), style=style_name(path), style_path=list(path),
                 style_custom=i["id"] in custom, benefit=benefit_of(i["title"]),
                 duration_s=_seconds(i.get("duration")) or lengths.get(i["id"]),
                 plays=int(p.get("n") or 0), last_played=p.get("last") or None)
        out.append(d)
    _fill_durations([i for i in every if not _seconds(i.get("duration")) and i["id"] not in lengths])
    return out


# ---- who, what, how long, how often ----------------------------------------------

_BY = re.compile(r"\s+[—–]\s+([^—–]{2,60})$")

#: What kind of practice a title says it is, as a path from the top level
#: down (Insight Timer's "Practice"; "Style" here, the app's tab being
#: Practice). First match wins, so a sub-style comes before its parent and
#: an exception before the rule it escapes. David, 7 Nov 2026: "I think we
#: need to be able to embed styles within styles"
#: (docs/proposals/practice-style-tree.md).
STYLE_RULES = (
    (("Meditation", "Yoga Nidra", "For sleep"), r"nidr[aä].*sleep|sleep.*nidr[aä]"),
    (("Meditation", "Yoga Nidra"), r"yoga\s*nidr[aä]|\bnidr[aä]\b"),
    (("Meditation", "Body scan"), r"body\s*scan"),
    (("Breathwork",), r"breath|pranayama"),
    (("Spirit & energy", "Journey", "Shamanic"), r"shaman|\bdrum"),
    (("Spirit & energy", "Journey", "Astral & lucid"), r"astral|lucid|out of body"),
    (("Spirit & energy", "Journey"), r"journey|travel"),
    (("Sound", "Frequencies"), r"\bhz\b|binaural|solfeggio"),
    (("Sound", "Bowls & sound baths"), r"\bbowls?\b|\bgong|sound bath"),
    (("Sound", "Mantra & chant"), r"chant|mantra|asatoma|\bom\b"),
    (("Sound",), r"\bsound\b"),
    (("Mind", "Manifesting"), r"manifest|abundance|dream life"),
    (("Mind", "Affirmations"), r"affirmation|confiden"),
    # Music made for a class, or a talk on yoga's limbs, is not one.
    (("Sound", "Music"), r"^music\b"),
    (("Mind", "Talks"), r"limb of yoga"),
    (("Movement", "Yoga", "Yin"), r"\byin\b"),
    (("Movement", "Yoga", "Vinyasa & flow"), r"vinyasa|power flow|yoga flow|sun salutation"),
    (("Movement", "Yoga", "Restorative & gentle"), r"restorative|gentle.*yoga|yoga.*gentle"),
    (("Movement", "Yoga", "Kundalini"), r"kundalini yoga"),
    (("Movement", "Yoga"), r"\byoga\b|hatha|ashtanga|asana\b"),
    (("Movement", "Stretching"), r"stretch|mobility|flexib|limber|loosen|pilates"),
    (("Movement", "Qigong, tai chi & somatic"), r"qigong|tai chi|somatic|shaking"),
    (("Movement",), r"\bdance|walking meditation|mindful walk|\bmovement"),
    (("Spirit & energy", "Chakra & energy"), r"chakra|reiki|cleans|cutting cords|energy wash|wash meditation"),
    (("Spirit & energy", "Spirit guides"), r"spirit guide|\bguides\b|upper world|lower world|power animal"),
    (("Mind", "Talks"), r"explained|wisdom|karma|lecture|\btalk\b|\[part \d"),
    (("Sound", "Music"), r"music|piano|prayer|devi\b|lokah|instrumental"),
    (("Meditation", "Heart"), r"gratitude|self.?love|loving|metta|kindness|compassion|forgiv"),
    (("Meditation", "Mindfulness & awareness"), r"mindful|awareness|presence|stillness|silent"),
    (("Meditation", "Visualisation"), r"visuali[sz]"),
)
#: A practice no rule names is a guided meditation.
STYLE_DEFAULT_PATH = ("Meditation", "Guided")
STYLE_DEFAULT = STYLE_DEFAULT_PATH[-1]
#: The top levels in the order the app shows them.
STYLE_TOP = ("Meditation", "Movement", "Breathwork", "Sound", "Spirit & energy", "Mind")
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


def style_path_of(title: str) -> tuple:
    """The style the title names, as a path from the top level down
    (("Movement", "Yoga", "Yin")), read without the " — teacher" after it
    (a teacher called "Yoga Wild" or "Jaw Yoga by …" does not make a
    savasana or a jaw meditation a yoga class)."""
    t = _BY.sub("", title or "").lower()
    return next((path for path, rx in STYLE_RULES if re.search(rx, t)), STYLE_DEFAULT_PATH)


def style_name(path) -> str:
    """The one name an older app groups by: the level under the top
    ("Yoga", "Yoga Nidra", "Journey"), or the top when it has none
    ("Breathwork")."""
    return path[1] if len(path) > 1 else path[0]


def style_of(title: str) -> str:
    return style_name(style_path_of(title))


# ---- styles set by hand -------------------------------------------------------------

#: How deep, and how long a name, a style set by hand may be.
STYLE_DEPTH, STYLE_NAME_MAX = 3, 40


def _styles_path() -> Path:
    return state_dir() / "practice-styles.json"


def _custom_styles() -> dict:
    try:
        d = json.loads(_styles_path().read_text())
    except (OSError, ValueError):
        return {}
    return {k: tuple(v) for k, v in d.items() if isinstance(v, list) and v} if isinstance(d, dict) else {}


def clean_style_path(path) -> Optional[tuple]:
    """A style path given by hand, tidied (each name trimmed, empty ones
    dropped), or None when it is not a list of 1–3 short names."""
    if not isinstance(path, (list, tuple)):
        return None
    names = tuple(" ".join(str(n).split()) for n in path if isinstance(n, str) and n.strip())
    if not names or len(names) > STYLE_DEPTH or any(len(n) > STYLE_NAME_MAX for n in names):
        return None
    return names


def set_style(item_id: str, path) -> bool:
    """Set `item_id`'s style by hand (a path, as clean_style_path takes),
    or with `path` None go back to what its title says. False for an id
    not in the list."""
    if not any(i["id"] == item_id for i in _all()):
        return False
    d = {k: list(v) for k, v in _custom_styles().items()}
    if path is None:
        d.pop(item_id, None)
    else:
        d[item_id] = list(path)
    p = _styles_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(f".tmp.{os.getpid()}")
    tmp.write_text(json.dumps(d, ensure_ascii=False))
    tmp.replace(p)
    return True


def style_tree(extra=()) -> list:
    """Every style as a tree, for the app's drill-down and its picker:
    [{"name", "children": [...]}], the rules' paths in STYLE_TOP's order,
    then any path set by hand (`extra`) that the rules don't have."""
    root: dict = {}
    for path in [p for p, _ in STYLE_RULES] + [STYLE_DEFAULT_PATH] + list(extra):
        node = root
        for name in path:
            node = node.setdefault(name, {})

    def out(node: dict) -> list:
        return [{"name": k, "children": out(v)} for k, v in node.items()]

    top = sorted(root, key=lambda k: (STYLE_TOP.index(k) if k in STYLE_TOP else len(STYLE_TOP)))
    return out({k: root[k] for k in top})


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
    """`{vid: seconds}` for YouTube videos, from the add-on (youtube_addon
    "youtube-metadata"); without one, none."""
    from . import youtube_addon
    addon = youtube_addon.get("youtube-metadata")
    if not vids or addon is None:
        return {}
    try:
        return {str(k): int(v) for k, v in dict(addon.lengths(list(vids))).items()}
    except Exception:  # noqa: BLE001 — no lengths this time
        return {}


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
    """A video's title: the add-on's (youtube_addon "youtube-metadata"),
    else YouTube's public oEmbed."""
    from . import music_recent, youtube_addon
    addon = youtube_addon.get("youtube-metadata")
    try:
        if addon is not None:
            got = str(addon.title(url) or "")
            if got:
                return got
    except Exception:  # noqa: BLE001
        pass
    vid = youtube_id(url)
    return music_recent._oembed(vid) if vid else ""


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
        if any(f"yt:{v['vid']}" == key for v in d["youtube"]):
            forget_download(key)          # its sound in the library goes with it
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

#: A teacher's profile comes from the insight-timer-profiles add-on (its
#: `lookup(name)`), where it is installed and allowed (personal_use).
#: A profile found is kept a month; a teacher not found is asked again after a week.
PROFILE_TTL_S = 30 * 86400
PROFILE_MISS_TTL_S = 7 * 86400


def _profiles_path() -> Path:
    return state_dir() / "practice-teachers.json"


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


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
    # One kept before profiles carried photo_large (the picture full size) is asked again.
    stale = bool(hit and hit.get("profile") and "photo_large" not in hit["profile"])
    if hit and not fresh and not stale and time.time() - float(hit.get("at") or 0) < ttl:
        return {"name": name, "profile": hit.get("profile")}
    from . import personal_use
    if not personal_use.allowed("insight-timer-profiles"):
        # Not asked of Insight Timer's internal endpoints, and what was kept
        # from them not shown, where personal use does not allow it.
        return {"name": name, "profile": None}
    try:
        profile = personal_use.provider("insight-timer-profiles").lookup(name)
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

def mark_playing(uri: str, item_id: str = "") -> None:
    """Remember what the book channel was given, so a reply eases it out,
    and which practice it is (`id`: a video file's sound is played from its
    extracted copy, not the file the list names)."""
    vid = youtube_id(uri)
    p = _now_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"uri": uri, "name": Path(uri.split("?")[0]).name,
                             "vid": vid or "", "id": item_id, "at": time.time()}))


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


def play(item_id: str, target: str = "", resume: bool = False, start_ms: int = -1) -> dict:
    """Play a saved practice on the book channel (by id, or a `match`), from
    `start_ms` when given (a video handed back to sound, `watch`). A video
    file plays from its extracted sound when there is one (`sound_for`):
    a far smaller copy to the phone than the video."""
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
    uri = it["_uri"]
    if it.get("video") == "file":
        uri = str(sound_for(it) or uri)
    elif it.get("video") == "youtube":
        uri = str(_yt_sound(it["youtube_id"]) or uri)
    mark_playing(uri, it["id"])
    note_play(it["id"])
    r = mcp_server.book_play(uri, resume=resume, start_ms=start_ms,
                             target=target, title=it.get("title") or "Practice")
    _keep_within_cap()
    r = r if isinstance(r, dict) else {}
    return {**r, "ok": not r.get("error"), "id": it["id"], "title": it.get("title")}


# ---- watching ------------------------------------------------------------------

def _video_sig(name: str) -> str:
    from . import clip_sign

    # Its own scope, so no other signed link opens a practice video.
    return clip_sign.sign("practice-video:" + name)


def watch(item_id: str) -> dict:
    """What the app needs to show a saved practice as video (David, 6 Nov
    2026: "Can videos be added to the practise library?"), by id only:
    a YouTube link's id, for YouTube's own embedded player, or a signed
    link to a video file in the Practice folder (`GET /practice/files/
    <hash><ext>?sig=`, relative to the server, good for that one file).
    Counted as a play. If the book channel is playing this same practice
    aloud, it is paused first (`paused: true`; David, 6 Nov 2026: "Add the
    pause too"), so its sound is not heard under the video, and the video
    starts where the sound was (`start_s`, when known). Its sound is got
    ready meanwhile (`prepare_sound`), for the app to hand back to when the
    screen goes off (`play` with `start_ms`).
    ``{"ok": False}`` for anything without a video."""
    it = find(item_id)
    if not it:
        return {"ok": False, "error": f"nothing saved as {item_id!r}", "status": 404}
    kind = it.get("video")
    if kind not in ("youtube", "file"):
        return {"ok": False, "error": "that practice has no video", "status": 400}
    note_play(it["id"])
    out = {"ok": True, "id": it["id"], "title": it.get("title")}
    heard = _pause_if_heard(it)
    if heard is not None:
        out["paused"] = True
        if heard > 1:
            out["start_s"] = int(heard)
    prepare_sound(it)
    if kind == "youtube":
        out["youtube_id"] = it["youtube_id"]
        if kept_video(it["youtube_id"]) is not None:
            # Kept (`keep`): the copy, for the app to watch or save offline.
            out["video"] = _yt_link(it["youtube_id"])
        return out
    return {**out, "video": _file_link(it)}


def _pause_if_heard(it: dict) -> Optional[float]:
    """Pause the book channel when what it holds is `it`: the practice last
    played (the mark) is this one and the book's now-playing is still that
    mark, so an audiobook started since is never touched. The seconds it
    was at (0.0 when not known), or None when nothing was paused. Never
    raises."""
    try:
        mark = json.loads(_now_path().read_text())
        if mark.get("id") != it["id"] and mark.get("uri") != it["_uri"]:
            return None
        from . import mcp_server
        np = mcp_server._state().get_now_playing("book") or {}
        if not is_marked(np.get("uri")):
            return None
        target = np.get("target") or ""
        at = _book_seconds(target)
        if not (mcp_server.book_pause(target=target) or {}).get("ok"):
            return None
        return at
    except Exception:  # noqa: BLE001 — the video is shown either way
        return None


def _book_seconds(target: str) -> float:
    """Where the book channel is, in seconds: the phone's player when it
    holds the item, else the book mpv's. 0.0 when neither says."""
    try:
        from . import mcp_server, phone_player
        t = mcp_server._book_target(target)
        st = phone_player.state(t) if phone_player.enabled() else None
        if st and st.get("item"):
            return float(st.get("t") or 0)
        ms = mcp_server._book().position(t)
        return (ms or 0) / 1000
    except Exception:  # noqa: BLE001
        return 0.0


# ---- a video's sound, and keeping downloads within a cap ------------------------

#: How much the practice's own downloads may hold (MB): YouTube practices'
#: sound in the audiobook library, and video files' extracted sound. Over it,
#: the longest unplayed go first; played again, they are fetched again.
KEEP_MB_DEFAULT = 2000


def _sound_dir() -> Path:
    return cache_dir() / "practice-sound"


def sound_for(it: dict) -> Optional[Path]:
    """A video file's extracted sound, when it has been made."""
    p = _sound_dir() / (it["id"].split(":", 1)[1] + ".m4a")
    return p if p.is_file() and p.stat().st_size > 0 else None


def _extract(src: Path, dest: Path) -> bool:
    """The sound of `src` into `dest` (m4a): copied as it is, else as AAC."""
    import subprocess
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(f".tmp.{os.getpid()}.m4a")
    for codec in (["-c:a", "copy"], ["-c:a", "aac", "-b:a", "128k"]):
        try:
            r = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(src),
                                "-vn", *codec, str(tmp)], capture_output=True, timeout=900)
        except (OSError, subprocess.TimeoutExpired):
            return False
        if r.returncode == 0 and tmp.is_file() and tmp.stat().st_size > 0:
            tmp.replace(dest)
            return True
    tmp.unlink(missing_ok=True)
    return False


def prepare_sound(it: dict, background: bool = True) -> None:
    """Get a video practice's sound ready to play: a YouTube link's download
    into the library (the add-on's fetch, not played), or a video file's
    sound extracted. Then the downloads are kept within their cap."""
    def run():
        try:
            if it.get("video") == "youtube":
                from . import library
                vid = it["youtube_id"]
                if _yt_sound(vid) is None and library.cached_path(vid) is None:
                    _fetch_small(vid, video=False)
            elif it.get("video") == "file" and sound_for(it) is None:
                _extract(Path(it["_uri"]), _sound_dir() / (it["id"].split(":", 1)[1] + ".m4a"))
            _keep_within_cap()
        except Exception:  # noqa: BLE001 — played without it, it is fetched then
            pass
    if background:
        import threading
        threading.Thread(target=run, daemon=True, name="practice-sound").start()
    else:
        run()


def _yt_dir(video: bool) -> Path:
    return cache_dir() / ("practice-video" if video else "practice-sound") / ("" if video else "yt")


def _yt_file(vid: str, video: bool) -> Optional[Path]:
    d = _yt_dir(video)
    if not d.is_dir():
        return None
    hits = sorted(p for p in d.glob(f"*[[]{vid}[]]*") if p.is_file() and p.stat().st_size > 0)
    return hits[0] if hits else None


def _yt_sound(vid: str) -> Optional[Path]:
    """A YouTube practice's own small sound (`prepare_sound`), when fetched."""
    return _yt_file(vid, video=False)


def kept_video(vid: str) -> Optional[Path]:
    """A YouTube practice's small video kept by `keep`, when it has arrived."""
    return _yt_file(vid, video=True)


#: The YouTube ids being fetched now, by kind ("sound", "video").
_fetching: set = set()


def _fetch_small(vid: str, video: bool) -> Optional[Path]:
    """A YouTube practice's sound or video at the add-on's small formats
    (LOW_AUDIO, LOW_VIDEO: David, 6 Nov 2026, "I'd even go lower quality to
    save space"), downloaded on the phone into the practice's own folder.
    None without the add-on, or when it fails."""
    addon = _downloads_addon()
    key = (vid, "video" if video else "sound")
    if addon is None or key in _fetching:
        return None
    _fetching.add(key)
    try:
        if video:
            fetch = getattr(addon, "fetch_video", None)
            return fetch(vid, _yt_dir(True)) if fetch else None
        fmt = getattr(addon, "LOW_AUDIO", None)
        return addon.fetch_audio(vid, _yt_dir(False), **({"fmt": fmt} if fmt else {}))
    finally:
        _fetching.discard(key)


def _downloads_addon():
    """The personal-use add-on's YouTube downloads, where installed and allowed."""
    from . import youtube_addon
    return youtube_addon.get("youtube-downloads")


def _kept_state(vid: str) -> Optional[str]:
    if kept_video(vid) is not None:
        return "ready"
    return "fetching" if (vid, "video") in _fetching else None


def keep(item_id: str) -> dict:
    """Keep a YouTube practice's video (small, `_fetch_small`) for the app to
    save and watch offline (David, 6 Nov 2026: "an option for downloading
    the video too", offline on the phone). Fetched in the background; the
    list's `kept` says "fetching", then "ready", and `watch` then answers
    its signed `video` link, as does `keep` itself once ready. A video file
    in the folder is ready at once: its link is answered."""
    it = find(item_id)
    if not it or not it.get("video"):
        return {"ok": False, "error": "only a practice with a video can be kept", "status": 404 if not it else 400}
    if it["video"] == "file":
        return {"ok": True, "id": it["id"], "kept": "ready", "video": _file_link(it)}
    vid = it["youtube_id"]
    state = _kept_state(vid)
    if state is None:
        if _downloads_addon() is None:
            return {"ok": False, "error": "downloading from YouTube isn't available here", "status": 501}
        import threading

        def run():
            try:
                _fetch_small(vid, video=True)
                _keep_within_cap()
            except Exception:  # noqa: BLE001 — the list says it is not kept
                pass
        _fetching.add((vid, "video"))       # said at once; _fetch_small takes it over
        def go():
            _fetching.discard((vid, "video"))
            run()
        threading.Thread(target=go, daemon=True, name="practice-keep").start()
        state = "fetching"
    out = {"ok": True, "id": it["id"], "kept": state}
    if state == "ready":
        out["video"] = _yt_link(vid)
    return out


def _yt_link(vid: str) -> str:
    name = f"yt-{vid}.mp4"
    return f"/practice/files/{name}?sig={_video_sig(name)}"


def _file_link(it: dict) -> str:
    name = it["id"].split(":", 1)[1] + Path(it["_uri"]).suffix.lower()
    return f"/practice/files/{name}?sig={_video_sig(name)}"


def unkeep(item_id: str) -> bool:
    it = find(item_id)
    f = kept_video(it["youtube_id"]) if it and it.get("video") == "youtube" else None
    if f is None:
        return False
    f.unlink(missing_ok=True)
    return True


def _owned_downloads() -> list[tuple[str, Path]]:
    """The downloads the practice list owns, by item id: each saved YouTube
    link's file in the library (not one also kept as a podcast link, which
    is that list's), and each extracted sound."""
    from . import library
    try:
        from . import podcast_links
        shared = {x.get("vid") for x in podcast_links._load()}
    except Exception:  # noqa: BLE001
        shared = set()
    out = []
    for v in _load()["youtube"]:
        if v["vid"] in shared:
            continue
        f = library.cached_path(v["vid"])
        if f is not None and f.is_file():
            out.append((f"yt:{v['vid']}", f))
    for v in _load()["youtube"]:
        for f in (_yt_sound(v["vid"]), kept_video(v["vid"])):
            if f is not None:
                out.append((f"yt:{v['vid']}", f))
    d = _sound_dir()
    if d.is_dir():
        out += [(f"file:{f.stem}", f) for f in d.glob("*.m4a") if re.fullmatch(r"[0-9a-f]{12}", f.stem)]
    return out


def _keep_within_cap() -> list[Path]:
    """Delete the longest-unplayed practice downloads until they are under
    MEDIA_PRACTICE_KEEP_MB (KEEP_MB_DEFAULT), never the one last played,
    and an extracted sound whose video is gone. The files deleted."""
    try:
        cap = float(os.environ.get("MEDIA_PRACTICE_KEEP_MB") or KEEP_MB_DEFAULT) * 1024 * 1024
    except ValueError:
        cap = KEEP_MB_DEFAULT * 1024 * 1024
    try:
        mark = json.loads(_now_path().read_text()).get("id", "")
    except (OSError, ValueError):
        mark = ""
    owned = _owned_downloads()
    files = {i["id"] for i in _files() if i.get("video")}
    gone = []
    for iid, f in list(owned):
        if iid.startswith("file:") and iid not in files:
            f.unlink(missing_ok=True)
            gone.append(f)
            owned.remove((iid, f))
    plays = _plays()
    sized = [(iid, f, f.stat().st_size) for iid, f in owned]
    total = sum(n for _, _, n in sized)
    for iid, f, n in sorted(sized, key=lambda x: (plays.get(x[0]) or {}).get("last") or 0):
        if total <= cap:
            break
        if iid == mark:
            continue
        f.unlink(missing_ok=True)
        gone.append(f)
        total -= n
    return gone


def forget_download(item_id: str) -> None:
    """A removed YouTube practice's download, deleted (unless a podcast link
    keeps the same video)."""
    for iid, f in _owned_downloads():
        if iid == item_id:
            f.unlink(missing_ok=True)


def served(name: str, sig: str) -> Optional[tuple[Path, str]]:
    """A video file and its content type for `GET /practice/files/<name>?sig=`,
    else None (a bad name, signature, or a file no longer in the folder
    alike). Only a file the folder lists now can be reached."""
    import hmac

    stem, _, ext = (name or "").rpartition(".")
    ctype = VIDEO_TYPES.get("." + ext.lower())
    yt = re.fullmatch(r"yt-([A-Za-z0-9_-]{11})", stem)
    if not ctype or not (yt or re.fullmatch(r"[0-9a-f]{12}", stem)):
        return None
    if not sig or not hmac.compare_digest(_video_sig(name), sig):
        return None
    if yt:
        if not any(v["vid"] == yt.group(1) for v in _load()["youtube"]):
            return None
        f = kept_video(yt.group(1))
        return (f, VIDEO_TYPES.get(f.suffix.lower(), "video/mp4")) if f is not None else None
    it = next((i for i in _files() if i["id"] == f"file:{stem}" and i.get("video")), None)
    if it is None:
        return None
    f = Path(it["_uri"])
    if f.suffix.lower() != "." + ext.lower() or not f.is_file():
        return None
    return f, ctype


def uri_for(item_id: str) -> Optional[str]:
    it = find(item_id)
    return it["_uri"] if it else None
