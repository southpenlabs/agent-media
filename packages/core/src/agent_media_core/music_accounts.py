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


# ---- Spotify -------------------------------------------------------------------------------
#
# David, 1 Nov 2026 (a Premium account, its own Development Mode app,
# client ID in Settings → About you `spotify.client_id`, else
# MEDIA_SPOTIFY_CLIENT_ID). Sign-in is the Authorization Code flow with
# PKCE — no client secret on the server: `spotify_begin` makes the link
# (state and verifier kept ten minutes), Spotify sends the browser back to
# the public `/accounts/spotify/callback` (server spotify_auth), and
# `spotify_finish` swaps the code for a refresh token, kept 0600 in
# `<state_dir>/music-accounts-spotify.json` (Spotify gives a Development
# Mode app's refresh token 180 days; a refresh may hand back a new one,
# which is kept). Read only: top tracks (long and medium term), saved
# tracks, recently played. The read is kept a day, as YouTube's.

SPOTIFY_AUTH = "https://accounts.spotify.com/authorize"
SPOTIFY_TOKEN = "https://accounts.spotify.com/api/token"
SPOTIFY_API = "https://api.spotify.com/v1"
# Reading, and (2 Nov 2026, spotify_remote) playing on his Spotify devices.
SPOTIFY_SCOPES = ("user-top-read user-library-read user-read-recently-played "
                  "user-read-playback-state user-modify-playback-state")
PENDING_S = 600.0


def spotify_client_id() -> str:
    from . import personal_prefs
    return str(personal_prefs.get("spotify.client_id") or "")


def spotify_redirect() -> str:
    v = (os.environ.get("MEDIA_SPOTIFY_REDIRECT") or "").strip()
    if v:
        return v
    base = (os.environ.get("MEDIA_PUBLIC_URL") or "https://red5.sasonica.com").strip().rstrip("/")
    return f"{base}/accounts/spotify/callback"


def _sp_path() -> Path:
    from ._paths import state_dir
    return state_dir() / "music-accounts-spotify.json"


def _sp_load() -> dict:
    try:
        d = json.loads(_sp_path().read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _sp_save(d: dict) -> None:
    p = _sp_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{os.getpid()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(d, f)
    os.replace(tmp, p)


def _http(url: str, data: Optional[dict] = None, token: str = "", timeout: float = 20.0) -> dict:
    import urllib.parse
    import urllib.request
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    if body is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    with urllib.request.urlopen(urllib.request.Request(url, body, headers), timeout=timeout) as r:
        return json.loads(r.read().decode() or "{}")


def spotify_begin(now: Optional[float] = None) -> str:
    """The sign-in link: Spotify's authorize page, with a fresh state and
    PKCE challenge kept for PENDING_S. ValueError without a client ID."""
    import base64
    import hashlib
    import secrets
    import urllib.parse
    cid = spotify_client_id()
    if not cid:
        raise ValueError("no Spotify client ID: set it in Settings → About you")
    now = time.time() if now is None else now
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(24)
    d = _sp_load()
    d["pending"] = {"state": state, "verifier": verifier, "at": now}
    _sp_save(d)
    q = urllib.parse.urlencode({"client_id": cid, "response_type": "code",
                                "redirect_uri": spotify_redirect(), "scope": SPOTIFY_SCOPES,
                                "state": state, "code_challenge_method": "S256",
                                "code_challenge": challenge})
    return f"{SPOTIFY_AUTH}?{q}"


def spotify_finish(code: str, state: str, now: Optional[float] = None) -> dict:
    """Swap the callback's code for tokens, if `state` is the pending one.
    `{"user"}` (Spotify's display name). ValueError otherwise."""
    import hmac
    now = time.time() if now is None else now
    d = _sp_load()
    p = d.get("pending") or {}
    if not (state and p.get("state") and hmac.compare_digest(str(p["state"]), str(state))):
        raise ValueError("that sign-in link is not the one this server made; start again")
    if now - float(p.get("at") or 0) > PENDING_S:
        raise ValueError("that sign-in took too long; start again")
    got = _http(SPOTIFY_TOKEN, {"grant_type": "authorization_code", "code": code,
                                "redirect_uri": spotify_redirect(),
                                "client_id": spotify_client_id(),
                                "code_verifier": p["verifier"]})
    if not got.get("refresh_token"):
        raise ValueError("Spotify gave no refresh token")
    me = {}
    try:
        me = _http(f"{SPOTIFY_API}/me", token=got["access_token"])
    except Exception:  # noqa: BLE001 — the name is only for Settings
        pass
    d.pop("pending", None)
    d.update(refresh_token=got["refresh_token"], user=me.get("display_name") or me.get("id") or "",
             signed_in_at=now, scope=got.get("scope") or "")
    d.pop("read", None)
    _sp_save(d)
    return {"user": d["user"]}


def spotify_signed_in() -> dict:
    d = _sp_load()
    return {"signed_in": bool(d.get("refresh_token")), "user": d.get("user") or None,
            "can_play": "user-modify-playback-state" in str(d.get("scope") or "").split()}


def spotify_sign_out() -> None:
    d = _sp_load()
    for k in ("refresh_token", "user", "read", "signed_in_at", "scope"):
        d.pop(k, None)
    _sp_save(d)


def _sp_access(d: dict) -> str:
    got = _http(SPOTIFY_TOKEN, {"grant_type": "refresh_token", "refresh_token": d["refresh_token"],
                                "client_id": spotify_client_id()})
    if got.get("refresh_token"):
        d["refresh_token"] = got["refresh_token"]
    if got.get("scope"):
        d["scope"] = got["scope"]
    return got["access_token"]


def spotify_songs(now: Optional[float] = None, fresh: bool = False) -> list[dict]:
    """`[{artist, title, plays, loved, id?}]` from his Spotify: top tracks
    (points by rank, long term counted double), saved tracks (loved),
    recently played (a play each). The read kept a day; [] when not
    signed in. Never raises."""
    now = time.time() if now is None else now
    d = _sp_load()
    if not d.get("refresh_token") or not spotify_client_id():
        return []
    kept = d.get("read") or {}
    if not fresh and kept and now - float(kept.get("at", 0)) < TTL_S:
        return kept.get("songs") or []
    try:
        tok = _sp_access(d)
        songs: dict[tuple, dict] = {}

        def add(t: dict, plays: int = 0, loved: bool = False) -> None:
            if not t or not t.get("name") or not t.get("artists"):
                return
            artist = t["artists"][0].get("name") or ""
            k = (artist.lower(), t["name"].lower())
            s = songs.setdefault(k, {"artist": artist, "title": t["name"], "plays": 0,
                                     "loved": False})
            s["plays"] += plays
            s["loved"] = s["loved"] or loved

        for rng, weight in (("long_term", 2), ("medium_term", 1)):
            items = _http(f"{SPOTIFY_API}/me/top/tracks?time_range={rng}&limit=50", token=tok).get("items") or []
            for i, t in enumerate(items):
                add(t, plays=weight * max(1, (50 - i) // 10))
        url = f"{SPOTIFY_API}/me/tracks?limit=50"
        for _ in range(4):
            page = _http(url, token=tok)
            for it in page.get("items") or []:
                add(it.get("track") or {}, loved=True)
            url = page.get("next")
            if not url:
                break
        for it in _http(f"{SPOTIFY_API}/me/player/recently-played?limit=50", token=tok).get("items") or []:
            add(it.get("track") or {}, plays=1)
        out = list(songs.values())
        d["read"] = {"at": now, "songs": out}
        _sp_save(d)
        return out
    except Exception as e:  # noqa: BLE001 — the last read stands
        log.info("music accounts: Spotify read failed: %s", e)
        try:
            _sp_save(d)  # a rotated refresh token must not be lost
        except OSError:
            pass
        return kept.get("songs") or []
