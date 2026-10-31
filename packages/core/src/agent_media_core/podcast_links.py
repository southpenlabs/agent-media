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
phone's best audio is about 50.

The list (`items`, music_recent's `saved` list for podcasts) gives each link
the file as its uri while the file is there, and ``yt:<url>`` when it is
not — deleted to save space, or not downloaded yet — so a tap still plays it:
`media book play yt:…` fetches it again through the phone.

MEDIA_PODCAST_LINK_FETCH=0 saves links without downloading (pinned off in
the tests' conftest).
"""

from __future__ import annotations

import json
import os
import shlex
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
    return [x for x in links if isinstance(x, dict) and x.get("vid")] if isinstance(links, list) else []


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


def add(url: str, title: str = "", fetch: bool = True) -> dict:
    """Save a YouTube video link. Returns ``{"added": "podcast", "id",
    "title", "fetching"}``; raises ValueError for anything else."""
    url = (url or "").strip()
    vid = youtube_id(url)
    if not url.startswith(("http://", "https://")) or not vid:
        raise ValueError("a podcast link must be a YouTube video")
    title = title.strip() or _title(vid) or url
    links = [x for x in _load() if x["vid"] != vid]
    links.append({"vid": vid, "url": f"https://www.youtube.com/watch?v={vid}",
                  "title": title, "added": time.time()})
    _save(links)
    fetching = fetch and file_for(vid) is None and start_fetch(vid)
    return {"added": "podcast", "id": f"podcast-link:{vid}", "title": title,
            "fetching": bool(fetching)}


def remove(key: str, keep_link: bool = False) -> bool:
    """Forget a link (``podcast-link:<vid>`` or the bare id) and delete its
    download; with `keep_link`, delete only the download."""
    vid = key.split(":", 1)[1] if key.startswith("podcast-link:") else key
    links = _load()
    if not any(x["vid"] == vid for x in links):
        return False
    d = folder()
    if d.is_dir():
        for p in d.glob(f"*[[]{vid}[]]*"):
            p.unlink(missing_ok=True)
    if not keep_link:
        _save([x for x in links if x["vid"] != vid])
    return True


def items() -> list[dict]:
    """The saved links, newest first, in Recently played's row shape."""
    from . import music_recent

    out = []
    for x in sorted(_load(), key=lambda x: -float(x.get("added") or 0)):
        f = file_for(x["vid"])
        out.append({"id": f"podcast-link:{x['vid']}", "uri": str(f) if f else f"yt:{x['url']}",
                    "title": x.get("title") or None, "art": music_recent.art(x["vid"]),
                    "at": float(x.get("added") or 0), "session": None, "inferred": False,
                    "kind": "podcast", "author": x.get("channel") or "YouTube",
                    "series": None, "narrator": None, "genre": None,
                    "downloaded": f is not None,
                    "size": f.stat().st_size if f else None})
    return out


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
    if not any(x["vid"] == vid for x in _load()) or not vid.replace("-", "").replace("_", "").isalnum():
        return None
    have = file_for(vid)
    if have and have.parent == folder():
        return shrink(have)
    phone = os.environ.get("AUDIOBOOK_PHONE_HOST", "p8a")
    ssh = ["ssh", "-o", "ConnectTimeout=12", "-o", "BatchMode=yes", phone]
    url = f"https://www.youtube.com/watch?v={vid}"
    remote = (f"mkdir -p {PHONE_DIR} && cd {PHONE_DIR} && "
              "yt-dlp -f bestaudio --no-overwrites --no-part --no-progress --no-warnings "
              "--print after_move:filename -o '%(title).150B [%(id)s].%(ext)s' "
              + shlex.quote(url))
    print(f"podcast-links: [{phone}] downloading {url}", file=sys.stderr)
    r = subprocess.run([*ssh, remote], capture_output=True, text=True, timeout=3600)
    name = (r.stdout.strip().splitlines() or [""])[-1]
    if r.returncode != 0 or f"[{vid}]" not in name or "/" in name:
        print(f"podcast-links: download failed for {vid}: {r.stderr.strip()[-300:]}", file=sys.stderr)
        return None
    dest = folder()
    dest.mkdir(parents=True, exist_ok=True)
    src = f"{phone}:{PHONE_DIR}/{name}"
    r = subprocess.run(["rsync", "-a", "-s", "-e", "ssh -o ConnectTimeout=12", src, str(dest) + "/"],
                       capture_output=True, text=True, timeout=3600)
    got = dest / name
    if r.returncode != 0 or not got.exists():
        print(f"podcast-links: copy failed for {vid}: {r.stderr.strip()[-300:]}", file=sys.stderr)
        return None
    subprocess.run([*ssh, f"rm -f -- {shlex.quote(PHONE_DIR + '/' + name)}"],
                   capture_output=True, timeout=60)
    out = shrink(got)
    print(f"podcast-links: kept {out.name} ({out.stat().st_size // 1_000_000} MB)", file=sys.stderr)
    return out


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "fetch":
        sys.exit(0 if fetch(sys.argv[2]) else 1)
    print("usage: python -m agent_media_core.podcast_links fetch <video id>", file=sys.stderr)
    sys.exit(2)
