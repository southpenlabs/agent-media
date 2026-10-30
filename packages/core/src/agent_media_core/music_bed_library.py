"""The music bed's online library: free recordings, fetched and kept on red5.

Roadmap item 39 (David, 26 Oct 2026: "it will be good to use some kind of
online library"). The third source in Settings → Music under long readings,
beside the generated moods and styles and his own streaming music.

**Where from.** The Internet Archive (archive.org), through its
`advancedsearch` and `metadata` APIs, is the main library: Musopen's
public-domain classical recordings (the complete Chopin is solo piano) and
Kevin MacLeod's CC BY albums are both there. ccMixter's query API is the
second, CC BY uploads tagged `instrumental` only (`lic=by`), which is where
most of the lo-fi is. Each genre (lo-fi, ambient, classical, jazz, piano)
has its searches in IA_QUERIES and CC_TAGS.

**Which licences.** Only those that allow any use, with no conditions
beyond credit: public domain (the Public Domain Mark, the old CC public
domain dedication), CC0, and CC BY (any version). Never NC, ND, or SA, and
never an item without a licence. The licence is read from the item's own
metadata (`licenseurl` on archive.org, `license_url` on ccMixter), never
assumed from the search that found it (`licence`).

**Instrumental.** Kevin MacLeod and Musopen's library are almost all
instrumental, ccMixter's `instrumental` tag says so; a title, subject or
file name that says it is sung, spoken or an audiobook (opera, choir, aria,
lyrics, LibriVox …) is skipped all the same (`_BAD`).

**The cache.** A few tracks a genre (MEDIA_MUSIC_BED_LIBRARY_PER_STYLE, 3),
normalised with ffmpeg — leading silence trimmed, loudnorm to -30 LUFS
integrated (MEDIA_MUSIC_BED_LIBRARY_LUFS), so it sits under speech as the
generated beds do, at most ten minutes, 48 kHz stereo mp3 — and written to
the speech clip dir (`<state_dir>/audio/music-bed-lib-<key>.mp3`), the one
place every player can fetch from: the phone by its signed clip URL
(clip_sign.py), this host's own mpv by path. What each one is — title,
creator, licence, the item's page — is kept in
`<state_dir>/music-bed-library.json`, which is also the cache's index.
Total size is capped (MEDIA_MUSIC_BED_LIBRARY_MAX_MB, 300); the least
recently played go first.

**When.** Only in music_bed's detached background process (`warm`), never
while a reply waits; a genre that came up short is not searched again for
six hours (MEDIA_MUSIC_BED_LIBRARY_RETRY_S). A reading with nothing cached
gets the generated bed instead.

**Manners.** Every request carries a User-Agent naming this program and
where it lives, and they are spaced at least a second apart (`_http`).

The tests never reach the network: `_http` and `_download` are patched in
conftest.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import re
import subprocess
import time
import urllib.parse
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

GENRES = ("lofi", "ambient", "classical", "jazz", "piano")
LABELS = {"lofi": "Lo-fi", "ambient": "Ambient", "classical": "Classical",
          "jazz": "Jazz", "piano": "Piano"}

USER_AGENT = ("agent-media-music-bed/1.0 "
              "(+https://github.com/southpenlabs/agent-media; quiet music "
              "under long readings; a few tracks a day)")
#: The least time between two requests to the libraries.
MIN_GAP_S = 1.0
#: The most a single download may be.
MAX_DOWNLOAD = 60 * 1024 * 1024

DEFAULT_PER_STYLE = 3
DEFAULT_MAX_MB = 300.0
DEFAULT_RETRY_S = 6 * 3600.0
DEFAULT_LUFS = -30.0
#: Tracks shorter than this are not worth a fetch; longer ones are cut.
MIN_TRACK_S = 90.0
MAX_TRACK_S = 900.0
KEEP_S = 600

IA_SEARCH = "https://archive.org/advancedsearch.php"
IA_META = "https://archive.org/metadata/"
IA_DOWNLOAD = "https://archive.org/download/"
IA_DETAILS = "https://archive.org/details/"
CC_QUERY = "https://ccmixter.org/api/query"

_NOT_SPOKEN = ("-collection:librivoxaudio -subject:librivox -subject:audiobook "
               "-subject:podcast")
#: archive.org searches per genre, most wanted first.
IA_QUERIES = {
    # The Chopin is piano's; classical is the rest of Musopen.
    "classical": [f"collection:musopen AND mediatype:audio -identifier:musopen-chopin "
                  f"{_NOT_SPOKEN}",
                  f'creator:"Kevin MacLeod" AND (subject:classical OR title:classical) '
                  f"AND mediatype:audio {_NOT_SPOKEN}"],
    "piano": ["identifier:musopen-chopin",
              f'creator:"Kevin MacLeod" AND subject:piano AND mediatype:audio {_NOT_SPOKEN}'],
    "jazz": [f'creator:"Kevin MacLeod" AND (subject:jazz OR title:jazz) AND mediatype:audio {_NOT_SPOKEN}'],
    "ambient": [f'creator:"Kevin MacLeod" AND (subject:ambient OR title:ambient OR subject:calm '
                f'OR subject:relaxing) AND mediatype:audio {_NOT_SPOKEN}'],
    "lofi": [f'creator:"Kevin MacLeod" AND (subject:chill OR subject:lounge OR subject:lofi '
             f'OR subject:"lo-fi") AND mediatype:audio {_NOT_SPOKEN}'],
}
#: ccMixter tags per genre (all of them must match); `instrumental` always.
CC_TAGS = {"lofi": "instrumental,chill,hip_hop", "ambient": "instrumental,ambient",
           "jazz": "instrumental,jazz", "piano": "instrumental,piano"}

_BAD = re.compile(
    r"\b(vocals?|voice|sung|singing|songs?|lyrics?|choir|choral|chorus|opera|"
    r"arias?|cantata|requiem|lieder|soprano|tenor|baritone|spoken|speech|"
    r"podcast|audiobooks?|librivox|poetry|poems?|interview|sermon|rap|"
    r"karaoke|acapella|a cappella)\b", re.I)


# ---- licences ------------------------------------------------------------------------

def licence(url) -> Optional[dict]:
    """`{"id", "label", "url"}` for a licence that allows any use with at
    most credit — public domain, CC0, CC BY — else None (NC, ND, SA, an
    unknown one, or none)."""
    if not isinstance(url, str) or not url.strip():
        return None
    u = url.strip().lower().replace("http://", "https://")
    if "creativecommons.org/" not in u:
        return None
    if "/publicdomain/zero/" in u:
        return {"id": "cc0", "label": "CC0", "url": url.strip()}
    if "/publicdomain/mark/" in u or re.search(r"/licenses/publicdomain/?$", u):
        return {"id": "pd", "label": "Public domain", "url": url.strip()}
    m = re.search(r"creativecommons\.org/licenses/([a-z-]+)/(\d+(?:\.\d+)?)", u)
    if m and m.group(1) == "by":
        return {"id": "cc-by", "label": f"CC BY {m.group(2)}", "url": url.strip()}
    return None


# ---- settings ---------------------------------------------------------------------------

def _num_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


def per_style() -> int:
    return max(1, int(_num_env("MEDIA_MUSIC_BED_LIBRARY_PER_STYLE", DEFAULT_PER_STYLE)))


def max_bytes() -> int:
    return int(_num_env("MEDIA_MUSIC_BED_LIBRARY_MAX_MB", DEFAULT_MAX_MB) * 1024 * 1024)


def retry_s() -> float:
    return _num_env("MEDIA_MUSIC_BED_LIBRARY_RETRY_S", DEFAULT_RETRY_S)


def enabled() -> bool:
    return (os.environ.get("MEDIA_MUSIC_BED_LIBRARY") or "").strip().lower() not in (
        "0", "off", "false", "no")


def ccmixter_on() -> bool:
    return (os.environ.get("MEDIA_MUSIC_BED_CCMIXTER") or "").strip().lower() not in (
        "0", "off", "false", "no")


# ---- the index ---------------------------------------------------------------------------

def _index_path() -> Path:
    from ._paths import state_dir
    return state_dir() / "music-bed-library.json"


def _dir() -> Path:
    from ._paths import speech_audio_dir
    return speech_audio_dir()


def _work_dir() -> Path:
    from ._paths import state_dir
    return state_dir() / "music-bed-library"


def load() -> dict:
    try:
        data = json.loads(_index_path().read_text())
        if isinstance(data, dict):
            data.setdefault("tracks", [])
            data.setdefault("tried", {})
            return data
    except (OSError, ValueError):
        pass
    return {"tracks": [], "tried": {}}


def save(data: dict) -> None:
    p = _index_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{os.getpid()}")
    tmp.write_text(json.dumps(data, indent=1))
    os.replace(tmp, p)


def _file(entry: dict) -> Path:
    return _dir() / entry["file"]


def tracks(genre: Optional[str] = None, data: Optional[dict] = None) -> list[dict]:
    """The cached tracks (of `genre`) whose files are there."""
    data = load() if data is None else data
    out = []
    for e in data["tracks"]:
        if genre is not None and e.get("genre") != genre:
            continue
        try:
            if _file(e).is_file():
                out.append(e)
        except (OSError, KeyError):
            continue
    return out


def counts() -> dict:
    data = load()
    return {g: len(tracks(g, data)) for g in GENRES}


def needs(genre: str, now: Optional[float] = None) -> bool:
    """Whether the background process should look for more of `genre`:
    fewer than per_style cached, and not tried in the last retry_s."""
    if not enabled() or genre not in GENRES:
        return False
    data = load()
    if len(tracks(genre, data)) >= per_style():
        return False
    now = time.time() if now is None else now
    return now - float(data["tried"].get(genre, 0)) >= retry_s()


def credit(entry: dict) -> dict:
    """What the app shows: title, creator, the licence and the page."""
    return {"title": entry.get("title") or "", "creator": entry.get("creator") or "",
            "license": entry.get("license") or "", "license_url": entry.get("license_url") or "",
            "url": entry.get("url") or "", "source": entry.get("source") or "",
            # The address of the audio itself: what "Play the song" plays
            # (music_bed_song).
            "download": entry.get("download") or ""}


def track_for(genre: str) -> Optional[tuple[Path, dict]]:
    """A cached track of `genre` to play now — the one played longest ago —
    and its credit; None with none cached. Marks it played. Never fetches."""
    try:
        have = tracks(genre)
        if not have:
            return None
        have.sort(key=lambda e: _file(e).stat().st_mtime)
        e = have[0]
        p = _file(e)
        try:
            os.utime(p)
        except OSError:
            pass
        return p, credit(e)
    except Exception as e:  # noqa: BLE001 — a bed must never cost a reply
        log.warning("music-bed library: %s", e)
        return None


# ---- the network (patched in the tests) ------------------------------------------------------

_last_request = 0.0


def _pace() -> None:
    global _last_request
    wait = _last_request + MIN_GAP_S - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    _last_request = time.monotonic()


def _http(url: str, timeout: float = 30.0) -> bytes:
    """GET `url` politely: our User-Agent, a second after the last request."""
    import urllib.request
    _pace()
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(4 * 1024 * 1024)


def _download(url: str, dest: Path, timeout: float = 120.0,
              referer: Optional[str] = None) -> None:
    """Fetch `url` into `dest`, at most MAX_DOWNLOAD bytes. ccMixter serves
    a file only to a request that comes from its page (`referer`)."""
    import urllib.request
    _pace()
    headers = {"User-Agent": USER_AGENT}
    if referer:
        headers["Referer"] = referer
    req = urllib.request.Request(url, headers=headers)
    got = 0
    with urllib.request.urlopen(req, timeout=timeout) as r, open(dest, "wb") as f:
        while True:
            chunk = r.read(256 * 1024)
            if not chunk:
                break
            got += len(chunk)
            if got > MAX_DOWNLOAD:
                raise OSError("too large")
            f.write(chunk)


def _json(url: str):
    return json.loads(_http(url).decode("utf-8", "replace"))


# ---- searching ---------------------------------------------------------------------------------

def _length_s(v) -> Optional[float]:
    """archive.org's `length`: seconds ("212.12") or "m:ss" / "h:mm:ss"."""
    if v is None:
        return None
    s = str(v).strip()
    try:
        if ":" in s:
            secs = 0.0
            for part in s.split(":"):
                secs = secs * 60 + float(part)
            return secs
        return float(s)
    except ValueError:
        return None


def _text(v) -> str:
    if isinstance(v, list):
        return " ".join(str(x) for x in v)
    return str(v or "")


def _key(source: str, url: str) -> str:
    return f"{source}-{hashlib.sha1(url.encode()).hexdigest()[:12]}"


def _ia_item(identifier: str, genre: str) -> list[dict]:
    """The usable tracks of one archive.org item: its licence (from its own
    metadata) allows any use, it and the track are not sung or spoken, an
    mp3 of 1.5 to 15 minutes."""
    meta = _json(IA_META + urllib.parse.quote(identifier))
    md = meta.get("metadata") or {}
    lic = licence(md.get("licenseurl"))
    if lic is None:
        log.info("music-bed library: %s refused (licence %r)", identifier,
                 md.get("licenseurl"))
        return []
    if _BAD.search(_text(md.get("title")) + " " + _text(md.get("subject"))):
        return []
    out = []
    for f in meta.get("files") or []:
        name = str(f.get("name") or "")
        if not name.lower().endswith(".mp3") or "mp3" not in str(f.get("format", "")).lower():
            continue
        secs = _length_s(f.get("length"))
        if secs is None or not (MIN_TRACK_S <= secs <= MAX_TRACK_S):
            continue
        title = str(f.get("title") or Path(name).stem.replace("_", " "))
        if _BAD.search(title + " " + name):
            continue
        url = IA_DOWNLOAD + urllib.parse.quote(identifier) + "/" + urllib.parse.quote(name)
        out.append({"key": _key("ia", url), "genre": genre, "download": url,
                    "title": title.strip(),
                    "creator": str(f.get("creator") or _text(md.get("creator"))).strip(),
                    "album": _text(md.get("title")).strip(),
                    "license": lic["label"], "license_id": lic["id"],
                    "license_url": lic["url"], "url": IA_DETAILS + identifier,
                    "source": "archive.org", "seconds": secs})
    return out


def search_ia(genre: str, rows: int = 12) -> list[dict]:
    out: list[dict] = []
    for q in IA_QUERIES.get(genre, []):
        params = [("q", q), ("fl[]", "identifier"), ("fl[]", "licenseurl"),
                  ("rows", str(rows)), ("output", "json"), ("sort[]", "downloads desc")]
        try:
            docs = (_json(IA_SEARCH + "?" + urllib.parse.urlencode(params))
                    .get("response") or {}).get("docs") or []
        except Exception as e:  # noqa: BLE001
            log.info("music-bed library: archive.org search failed: %s", e)
            continue
        for d in docs:
            ident = d.get("identifier")
            # The search's licence is only a first sieve; the item's own
            # metadata decides (_ia_item).
            if not ident or licence(d.get("licenseurl")) is None:
                continue
            try:
                out.extend(_ia_item(str(ident), genre))
            except Exception as e:  # noqa: BLE001
                log.info("music-bed library: %s: %s", ident, e)
            if len(out) >= 40:
                return out
    return out


def search_ccmixter(genre: str, limit: int = 15) -> list[dict]:
    tags = CC_TAGS.get(genre)
    if not tags or not ccmixter_on():
        return []
    q = urllib.parse.urlencode({"tags": tags, "lic": "by", "f": "js",
                                "limit": str(limit), "sort": "rank"})
    try:
        items = _json(f"{CC_QUERY}?{q}")
    except Exception as e:  # noqa: BLE001
        log.info("music-bed library: ccMixter search failed: %s", e)
        return []
    out = []
    for it in items if isinstance(items, list) else []:
        lic = licence(it.get("license_url"))
        if lic is None or lic["id"] != "cc-by":
            continue
        tagset = str(it.get("upload_tags") or "").lower()
        if "instrumental" not in tagset:
            continue
        title = str(it.get("upload_name") or "").strip()
        if _BAD.search(title):
            continue
        for f in it.get("files") or []:
            url = str(f.get("download_url") or "")
            info = f.get("file_format_info") or {}
            if not url.lower().endswith(".mp3"):
                continue
            secs = _length_s(info.get("ps"))
            if secs is None or not (MIN_TRACK_S <= secs <= MAX_TRACK_S):
                continue
            out.append({"key": _key("cc", url), "genre": genre, "download": url,
                        "title": title, "creator": str(it.get("user_name") or "").strip(),
                        "album": "", "license": lic["label"], "license_id": lic["id"],
                        "license_url": lic["url"],
                        "url": str(it.get("file_page_url") or ""),
                        "source": "ccmixter.org", "seconds": secs})
            break
    return out


# ---- fetching -----------------------------------------------------------------------------------

def normalise(src: Path, dst: Path, timeout: float = 600.0) -> bool:
    """`src` as a bed: leading silence trimmed, at most KEEP_S long,
    loudnorm to -30 LUFS integrated, 48 kHz stereo mp3."""
    lufs = _num_env("MEDIA_MUSIC_BED_LIBRARY_LUFS", DEFAULT_LUFS)
    af = ("silenceremove=start_periods=1:start_duration=0.2:start_threshold=-50dB,"
          f"loudnorm=I={lufs:g}:TP=-9:LRA=11")
    tmp = dst.with_name(f".{dst.stem}.{os.getpid()}.mp3")
    try:
        r = subprocess.run(
            ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
             "-i", str(src), "-vn", "-t", str(KEEP_S), "-af", af, "-ar", "48000",
             "-ac", "2", "-c:a", "libmp3lame", "-q:a", "5", str(tmp)],
            stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout)
        if r.returncode != 0 or not tmp.is_file() or tmp.stat().st_size < 1024:
            log.info("music-bed library: ffmpeg failed: %s",
                     r.stderr.decode(errors="replace")[-300:])
            return False
        os.replace(tmp, dst)
        return True
    except (OSError, subprocess.SubprocessError) as e:
        log.info("music-bed library: ffmpeg: %s", e)
        return False
    finally:
        tmp.unlink(missing_ok=True)


def fetch(cand: dict) -> Optional[dict]:
    """Download and normalise one candidate; its index entry, or None."""
    work = _work_dir()
    work.mkdir(parents=True, exist_ok=True)
    raw = work / f"{cand['key']}.part"
    name = f"music-bed-lib-{cand['key']}.mp3"
    dst = _dir() / name
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        _download(cand["download"], raw, referer=cand.get("url") or None)
        if not normalise(raw, dst):
            return None
    except Exception as e:  # noqa: BLE001
        log.info("music-bed library: fetching %s failed: %s", cand.get("download"), e)
        return None
    finally:
        raw.unlink(missing_ok=True)
    entry = {k: v for k, v in cand.items() if k != "download"}
    entry.update(file=name, bytes=dst.stat().st_size, added=time.time(),
                 download=cand["download"])
    return entry


def prune(data: dict, cap: Optional[int] = None) -> dict:
    """Drop entries whose files are gone, then the least recently played
    until the cache is under `cap` bytes (MEDIA_MUSIC_BED_LIBRARY_MAX_MB)."""
    cap = max_bytes() if cap is None else cap
    live = []
    for e in data["tracks"]:
        try:
            st = _file(e).stat()
            live.append((st.st_mtime, st.st_size, e))
        except (OSError, KeyError):
            continue
    live.sort(key=lambda x: x[0])
    total = sum(size for _, size, _ in live)
    while live and total > cap:
        _, size, e = live.pop(0)
        try:
            _file(e).unlink()
        except OSError:
            pass
        total -= size
        log.info("music-bed library: pruned %s", e.get("file"))
    data["tracks"] = [e for _, _, e in live]
    return data


def fill(genres, now: Optional[float] = None) -> int:
    """Fetch tracks for each genre in `genres` that `needs` them. The
    background process's work; never a reply's. Returns how many came."""
    got = 0
    for g in genres:
        if not needs(g, now):
            continue
        data = load()
        have = {e.get("key") for e in data["tracks"]}
        cands = [c for c in search_ia(g) + search_ccmixter(g) if c["key"] not in have]
        random.Random(f"{g}-{int(time.time() // 86400)}").shuffle(cands)
        want = per_style() - len(tracks(g, data))
        for c in cands:
            if want <= 0:
                break
            entry = fetch(c)
            if entry is None:
                continue
            data = load()
            data["tracks"].append(entry)
            data = prune(data)
            save(data)
            got += 1
            want -= 1
        data = load()
        data["tried"][g] = time.time() if now is None else now
        save(data)
    return got
