"""His music accounts as taste: YouTube through yt-profile's sign-ins.

David, 1 Nov 2026: "I'd like to also link my YouTube and Spotify accounts
for preferences" ... "can we reuse that?" (docs/proposals/
2026-11-01-music-accounts.md). The favourites gather
(music_bed_favourites.gather) counts what these return as it counts the
state store's likes and plays.

**YouTube.** The accounts are yt-profile profile names (Settings → Music →
About you, `youtube.profiles`, else MEDIA_YOUTUBE_PROFILES; comma
separated). Each is read by `ytmusic_reader.py`, run as its own process
under a Python that has ytmusicapi (MEDIA_YTMUSIC_PYTHON, else Mopidy's
pipx venv), from yt-profile's dir (MEDIA_YT_PROFILE_DIR, else
~/.config/yt-dlp/cookies): YouTube Music history and likes where the
profile's headers work, and liked music videos and mostly-music playlists
through the YouTube Data API where it has an OAuth token. Read only. The
last read is kept (`<state_dir>/music-accounts-youtube.json`) for
Settings to say how many songs each account gave, and reused for a day.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

TTL_S = 24 * 3600.0
READ_TIMEOUT_S = 600


def youtube_profiles() -> list[str]:
    from . import personal_prefs
    raw = personal_prefs.get("youtube.profiles") or ""
    return [p.strip() for p in str(raw).split(",") if p.strip()]


def youtube_python() -> Optional[str]:
    v = (os.environ.get("MEDIA_YTMUSIC_PYTHON") or "").strip()
    if v:
        return v
    p = Path.home() / ".local/share/pipx/venvs/mopidy/bin/python"
    return str(p) if p.exists() else None


def profile_dir() -> Path:
    v = (os.environ.get("MEDIA_YT_PROFILE_DIR") or "").strip()
    return Path(v).expanduser() if v else Path.home() / ".config/yt-dlp/cookies"


def _cache() -> Path:
    from ._paths import state_dir
    return state_dir() / "music-accounts-youtube.json"


def _run(argv: list[str]) -> str:
    return subprocess.run(argv, capture_output=True, text=True, timeout=READ_TIMEOUT_S,
                          stdin=subprocess.DEVNULL).stdout


def last() -> dict:
    try:
        d = json.loads(_cache().read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def youtube_accounts(now: Optional[float] = None, fresh: bool = False) -> list[dict]:
    """`[{"profile", "auth", "songs": [...], "error"}]` for the profiles
    set; the kept read when it is under a day old and for the same
    profiles. [] when none are set or the reader cannot run."""
    profiles = youtube_profiles()
    if not profiles:
        return []
    now = time.time() if now is None else now
    kept = last()
    if (not fresh and kept.get("profiles") == profiles
            and now - float(kept.get("at", 0)) < TTL_S):
        return kept.get("accounts") or []
    py = youtube_python()
    if not py:
        log.info("music accounts: no Python with ytmusicapi (MEDIA_YTMUSIC_PYTHON)")
        return kept.get("accounts") or []
    script = Path(__file__).with_name("ytmusic_reader.py")
    try:
        got = json.loads(_run([py, "-I", str(script), str(profile_dir()), *profiles]) or "{}")
        accounts = got.get("accounts") or []
    except Exception as e:  # noqa: BLE001 — the last read stands
        log.info("music accounts: YouTube read failed: %s", e)
        return kept.get("accounts") or []
    p = _cache()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{os.getpid()}")
    tmp.write_text(json.dumps({"at": now, "profiles": profiles, "accounts": accounts},
                              ensure_ascii=False))
    os.replace(tmp, p)
    return accounts


def youtube_songs(now: Optional[float] = None) -> dict[str, dict]:
    """{video id: {id, title, artist, dur, liked, plays}} across the
    accounts: liked if any account liked it, plays added."""
    out: dict[str, dict] = {}
    for a in youtube_accounts(now):
        for s in a.get("songs") or []:
            if not s.get("id"):
                continue
            cur = out.setdefault(s["id"], {**s, "liked": False, "plays": 0})
            cur["liked"] = cur["liked"] or bool(s.get("liked"))
            cur["plays"] += int(s.get("plays") or 0)
    return out


def youtube_why() -> Optional[str]:
    """None when every profile set gave songs at the last read."""
    profiles = youtube_profiles()
    if not profiles:
        return "Not set: name your yt-profile accounts (MEDIA_YOUTUBE_PROFILES on the server)."
    if not youtube_python():
        return "Not available: no Python with ytmusicapi on this server (MEDIA_YTMUSIC_PYTHON)."
    kept = last()
    if not kept:
        return None
    bad = [a["profile"] for a in kept.get("accounts") or [] if not a.get("songs")]
    return ("Nothing read from " + ", ".join(bad) + ": sign in again with yt-profile.") if bad else None


def youtube_summary() -> str:
    kept = last()
    rows = [f"{a['profile']}: {len(a.get('songs') or [])} songs"
            for a in kept.get("accounts") or []]
    return "; ".join(rows)
