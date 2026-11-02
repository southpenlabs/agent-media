"""Saved YouTube links for the Podcasts tab: kept as a link, downloaded small.

David, 1 Nov 2026: "Can we have a paste link from YouTube option for
podcasts like the meditations tab has ... actually a share option would be
even better" … "Maybe a combination of both... keep the link in cases the
downloaded version has been removed to save space."

Each saved link is ``{vid, url, title, added}`` in
``<state_dir>/podcast-links.json``. Saving one starts a download in the
background (`start_fetch`): yt-dlp on the phone, as `audiobook-fetch` does,
because YouTube refuses a datacenter address, into a phone folder of its own
(``.cache/agent-media/podcast-links``) so the books' sync never carries it;
rsync'd here into `folder()`, deleted from the phone, then shrunk to mono
Opus at MEDIA_PODCAST_LINK_KBPS (32): about 15 MB an hour of talk, where the
phone's best audio is about 50. Past MEDIA_PODCAST_LINK_MAX_MB (1000) in
all, the oldest downloads are deleted (`prune`); their links stay.

The list (`items`, music_recent's `saved` list for podcasts) gives each link
the file as its uri while the file is there, and ``yt:<url>`` when it is
not — deleted to save space, or not downloaded yet — so a tap still plays it:
`media book play yt:…` fetches it again through the phone.

MEDIA_PODCAST_LINK_FETCH=0 saves links without downloading (pinned off in
the tests' conftest).

A podcast app's episode link (PodcastOne, Pocket Casts, Apple Podcasts, …)
is saved too (David, 1 Nov 2026: "I tried to share a podcast with sasonica
but it came through as music"): podcast_resolve finds the episode's audio,
kept as ``{ep, url, audio, title, show, art, published, duration_s,
added}`` and played from its audio URL, never downloaded.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from ._paths import cache_dir, state_dir

AUDIO_EXTS = {".mp3", ".m4a", ".mka", ".webm", ".ogg", ".opus", ".aac", ".flac", ".wav"}
PHONE_DIR = ".cache/agent-media/podcast-links"


def _store_path() -> Path:
    return state_dir() / "podcast-links.json"


def folder() -> Path:
    """Where the downloads are kept (MEDIA_PODCAST_LINK_DIR): a cache, since
    each can be fetched again from its link."""
    env = os.environ.get("MEDIA_PODCAST_LINK_DIR")
    return Path(env).expanduser() if env else cache_dir() / "podcast-links"


def _load() -> list[dict]:
    try:
        d = json.loads(_store_path().read_text())
    except (OSError, ValueError):
        return []
    links = d.get("links") if isinstance(d, dict) else None
    return [x for x in links if isinstance(x, dict) and (x.get("vid") or x.get("ep"))] if isinstance(links, list) else []


def _save(links: list[dict]) -> None:
    p = _store_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(f".tmp.{os.getpid()}")
    tmp.write_text(json.dumps({"links": links}, indent=1))
    tmp.replace(p)


def youtube_id(url: str) -> Optional[str]:
    from . import library

    return library.video_id(url or "")


def file_for(vid: str) -> Optional[Path]:
    """The downloaded file for `vid`: ours, else one the book library holds
    (a link played after its file was deleted is fetched into the books)."""
    d = folder()
    if d.is_dir():
        hits = sorted(p for p in d.glob(f"*[[]{vid}[]]*") if p.suffix.lower() in AUDIO_EXTS)
        if hits:
            return hits[0]
    try:
        from . import library

        return library.cached_path(vid) or next(
            iter(sorted(library.abs_import_dir().glob(f"*[[]{vid}[]]*"))), None)
    except Exception:  # noqa: BLE001 — no book library is no file
        return None


def _title(vid: str) -> str:
    from . import music_recent

    return music_recent._cached_title(vid) or music_recent._oembed(vid)


def _key(x: dict) -> str:
    return x.get("vid") or f"ep-{x['ep']}"


def add(url: str, title: str = "", fetch: bool = True) -> dict:
    """Save a YouTube video link, or a podcast episode's link (its audio
    found by podcast_resolve, `title` the words shared with it). Returns
    ``{"added": "podcast", "id", "title", "fetching"}``; raises ValueError
    for a link that is neither."""
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        raise ValueError("a podcast link must be an http(s) link")
    vid = youtube_id(url)
    if not vid:
        return _add_episode(url, title)
    # YouTube's own title first: what comes with a share is often the
    # sharing app's label, not the video's ("Share link", 2 Nov 2026).
    title = _title(vid) or title.strip() or url
    links = [x for x in _load() if x.get("vid") != vid]
    links.append({"vid": vid, "url": f"https://www.youtube.com/watch?v={vid}",
                  "title": title, "added": time.time()})
    _save(links)
    fetching = fetch and file_for(vid) is None and start_fetch(vid)
    return {"added": "podcast", "id": f"podcast-link:{vid}", "title": title,
            "fetching": bool(fetching)}


def _add_episode(url: str, text: str) -> dict:
    import hashlib

    from . import podcast_resolve
    got = podcast_resolve.episode(url, text)
    if not got:
        raise ValueError("couldn't find that episode's audio: try sharing it with its title, or paste the show's RSS feed into Practice")
    ep = hashlib.sha256(got["audio"].encode()).hexdigest()[:12]
    links = [x for x in _load() if x.get("ep") != ep]
    links.append({"ep": ep, "url": url, "audio": got["audio"], "title": got.get("title") or url,
                  "show": got.get("show"), "art": got.get("art"), "published": got.get("published"),
                  "duration_s": got.get("duration_s"), "added": time.time()})
    _save(links)
    return {"added": "podcast", "id": f"podcast-link:ep-{ep}", "title": got.get("title") or url,
            "show": got.get("show"), "fetching": False}


def remove(key: str, keep_link: bool = False) -> bool:
    """Forget a link (``podcast-link:<vid>`` or the bare id) and delete its
    download; with `keep_link`, delete only the download."""
    vid = key.split(":", 1)[1] if key.startswith("podcast-link:") else key
    links = _load()
    if not any(_key(x) == vid for x in links):
        return False
    if vid.startswith("ep-"):
        # An episode is its link alone: nothing downloaded to delete.
        if not keep_link:
            _save([x for x in links if _key(x) != vid])
        return True
    d = folder()
    if d.is_dir():
        for p in d.glob(f"*[[]{vid}[]]*"):
            p.unlink(missing_ok=True)
    if not keep_link:
        _save([x for x in links if _key(x) != vid])
    return True


def items() -> list[dict]:
    """The saved links, newest first, in Recently played's row shape."""
    from . import music_recent

    out = []
    for x in sorted(_load(), key=lambda x: -float(x.get("added") or 0)):
        if x.get("ep"):
            out.append({"id": f"podcast-link:ep-{x['ep']}", "uri": x["audio"], "title": x.get("title") or None,
                        "art": x.get("art"), "at": float(x.get("added") or 0), "session": None,
                        "inferred": False, "kind": "podcast", "author": x.get("show") or None,
                        "series": None, "narrator": None, "genre": None, "downloaded": False,
                        "size": None, "published": x.get("published"), "duration_s": x.get("duration_s")})
            continue
        f = file_for(x["vid"])
        out.append({"id": f"podcast-link:{x['vid']}", "uri": str(f) if f else f"yt:{x['url']}",
                    "title": x.get("title") or None, "art": music_recent.art(x["vid"]),
                    "at": float(x.get("added") or 0), "session": None, "inferred": False,
                    "kind": "podcast", "author": x.get("channel") or "YouTube",
                    "series": None, "narrator": None, "genre": None,
                    "downloaded": f is not None,
                    "size": f.stat().st_size if f else None})
    return out


#: The download types the public route serves, and how it names them.
SERVED_TYPES = {".opus": "audio/ogg", ".ogg": "audio/ogg", ".m4a": "audio/mp4", ".mp3": "audio/mpeg",
                ".webm": "audio/webm", ".mka": "audio/x-matroska", ".aac": "audio/aac"}


def _sig(name: str) -> str:
    from . import clip_sign

    # Its own scope, so a speech clip's signature never opens a download.
    return clip_sign.sign("podcast-link:" + name)


def play_url(uri: str) -> Optional[str]:
    """The web address the phone's music player fetches a saved link's
    download from, or None when `uri` is not one. The book channel cannot
    take it: on the phone that is the app's speech player, which cannot read
    Termux's cache and drops a book for the next spoken line (David, 2 Nov
    2026: tapped, the player "grays out like it's going to but it doesn't
    play").

    A signed link on the public server (MEDIA_PUBLIC_URL, `GET /podcasts/
    files/<id><ext>?sig=`, good for that one file), so it plays off the
    tailnet; without one, the book files' server (MEDIA_BOOK_BASEURL_PHONE
    over MEDIA_BOOK_HTTP_ROOT) as the fallback."""
    p = Path(uri).expanduser()
    if p.parent != folder() or not p.is_file():
        return None
    x = next((x for x in _load() if x.get("vid") and file_for(x["vid"]) == p), None)
    if x is None:
        return None
    public = os.environ.get("MEDIA_PUBLIC_URL", "").strip().rstrip("/")
    if public and p.suffix.lower() in SERVED_TYPES:
        name = x["vid"] + p.suffix.lower()
        return f"{public}/podcasts/files/{name}?sig={_sig(name)}"
    from .sinks.book import http_url_for
    from .types import Target

    return http_url_for(p, Target(name="phone"))


def served(name: str, sig: str) -> Optional[tuple[Path, str]]:
    """A download and its content type for `GET /podcasts/files/<name>?sig=`,
    else None (a bad name, signature, or no file alike)."""
    import hmac

    vid, _, ext = (name or "").rpartition(".")
    ctype = SERVED_TYPES.get("." + ext.lower())
    if not ctype or not vid.replace("-", "").replace("_", "").isalnum() or len(vid) != 11:
        return None
    if not sig or not hmac.compare_digest(_sig(name), sig):
        return None
    if not any(x.get("vid") == vid for x in _load()):
        return None
    f = file_for(vid)
    if f is None or f.parent != folder() or f.suffix.lower() != "." + ext.lower():
        return None
    return f, ctype


# ---- downloading -----------------------------------------------------------------

def _log_path() -> Path:
    return cache_dir() / "podcast-links.log"


def start_fetch(vid: str) -> bool:
    """Download `vid` in the background (`python -m … fetch <vid>`), so the
    add answers at once. False when downloads are off."""
    if os.environ.get("MEDIA_PODCAST_LINK_FETCH", "1") == "0":
        return False
    log = _log_path()
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "ab") as out:
        subprocess.Popen([sys.executable, "-m", "agent_media_core.podcast_links", "fetch", vid],
                         stdin=subprocess.DEVNULL, stdout=out, stderr=out,
                         start_new_session=True)
    return True


def shrink(path: Path, kbps: Optional[int] = None) -> Path:
    """Re-encode `path` as mono Opus at `kbps`, keeping its name (and so the
    ``[<id>]`` that finds it), and delete the original. An .opus file, or one
    ffmpeg cannot read, is left as it is."""
    if path.suffix.lower() == ".opus":
        return path
    kbps = kbps or int(os.environ.get("MEDIA_PODCAST_LINK_KBPS", "32"))
    out = path.with_suffix(".opus")
    tmp = path.with_name(f".{out.name}.part")
    r = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(path),
                        "-vn", "-ac", "1", "-c:a", "libopus", "-b:a", f"{kbps}k",
                        "-map_metadata", "0", "-f", "ogg", str(tmp)],
                       capture_output=True, text=True, timeout=3600)
    if r.returncode != 0 or not tmp.exists() or tmp.stat().st_size == 0:
        tmp.unlink(missing_ok=True)
        print(f"podcast-links: shrink failed for {path.name}: {r.stderr.strip()[-200:]}", file=sys.stderr)
        return path
    tmp.replace(out)
    path.unlink(missing_ok=True)
    return out


def fetch(vid: str) -> Optional[Path]:
    """Download `vid` on the phone, bring it here, delete the phone's copy,
    shrink it. The file, or None when any step failed."""
    if not any(x.get("vid") == vid for x in _load()) or not vid.replace("-", "").replace("_", "").isalnum():
        return None
    have = file_for(vid)
    if have and have.parent == folder():
        return shrink(have)
    from . import youtube_addon
    addon = youtube_addon.get("youtube-downloads")
    if addon is None:
        print("podcast-links: downloading from YouTube isn't available on this server", file=sys.stderr)
        return None
    got = addon.fetch_audio(vid, folder())
    if got is None:
        return None
    out = shrink(got)
    print(f"podcast-links: kept {out.name} ({out.stat().st_size // 1_000_000} MB)", file=sys.stderr)
    for gone in prune(keep=out):
        print(f"podcast-links: cleared {gone.name} for space (its link stays)", file=sys.stderr)
    return out


def max_bytes() -> int:
    """The most the downloads may hold (MEDIA_PODCAST_LINK_MAX_MB, 1000:
    about 65 hours of talk at 32 kb/s); 0 for no cap."""
    try:
        return max(0, int(os.environ.get("MEDIA_PODCAST_LINK_MAX_MB", "1000"))) * 1_000_000
    except ValueError:
        return 1000 * 1_000_000


def prune(keep: Optional[Path] = None) -> list[Path]:
    """Delete the oldest downloads (by when each was fetched) until they fit
    under `max_bytes`, never `keep`. Their links stay, so each still plays
    (fetched again). The files deleted."""
    cap = max_bytes()
    d = folder()
    if not cap or not d.is_dir():
        return []
    files = sorted((p for p in d.iterdir() if p.is_file() and p.suffix.lower() in AUDIO_EXTS),
                   key=lambda p: p.stat().st_mtime)
    total = sum(p.stat().st_size for p in files)
    gone = []
    for p in files:
        if total <= cap:
            break
        if keep is not None and p == keep:
            continue
        total -= p.stat().st_size
        p.unlink(missing_ok=True)
        gone.append(p)
    return gone


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "fetch":
        sys.exit(0 if fetch(sys.argv[2]) else 1)
    print("usage: python -m agent_media_core.podcast_links fetch <video id>", file=sys.stderr)
    sys.exit(2)
