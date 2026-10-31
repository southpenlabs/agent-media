"""What a YouTube account likes and plays, read through yt-profile's sign-ins.

David, 1 Nov 2026: "I'd like to also link my YouTube and Spotify accounts
for preferences" ... "I think we may have already done this for
audiobookshelf or Iris. can we reuse that?" — yes: yt-profile (dotfiles,
~/.config/yt-dlp/README-yt-profile.md) keeps, per Google account, an OAuth
token (`<name>-oauth.json`, its own Google client in `oauth_client.json`)
and/or YouTube Music headers (`<name>.json`, what Iris lists his playlists
with). Read only: nothing is ever written to an account.

A standalone script, never imported by agent-media: it needs ytmusicapi,
which lives in Mopidy's venv, not here. music_accounts runs it as

    <python with ytmusicapi> ytmusic_reader.py <cookie dir> <profile>...

and reads one JSON object from stdout: `{"accounts": [{"profile", "auth",
"songs": [{"id", "title", "artist", "dur", "liked", "plays"}], "error"}]}`.
For each profile the OAuth token is tried first (it refreshes itself), then
the headers. No credential is ever printed.
"""

from __future__ import annotations

import json
import os
import sys


def _tolerant_token():
    # Google's token answer carries keys ytmusicapi's dataclass refuses (as
    # yt-oauth-setup works around); drop them.
    import dataclasses

    from ytmusicapi.auth.oauth.token import RefreshingToken

    if getattr(RefreshingToken, "_am_extra_kwargs_tolerated", False):
        return
    fields = {f.name for f in dataclasses.fields(RefreshingToken)}
    orig = RefreshingToken.__init__

    def init(self, *a, **k):
        for key in [key for key in k if key not in fields]:
            k.pop(key)
        orig(self, *a, **k)

    RefreshingToken.__init__ = init
    RefreshingToken._am_extra_kwargs_tolerated = True


def _clients(d: str, profile: str):
    """(how, YTMusic) to try, OAuth first."""
    from ytmusicapi import OAuthCredentials, YTMusic

    out = []
    tok, client = os.path.join(d, f"{profile}-oauth.json"), os.path.join(d, "oauth_client.json")
    if os.path.isfile(tok) and os.path.isfile(client):
        try:
            c = json.load(open(client))
            cid = c.get("client_id") or c.get("id")
            sec = c.get("client_secret") or c.get("secret")
            if cid and sec:
                _tolerant_token()
                out.append(("oauth", lambda: YTMusic(tok, oauth_credentials=OAuthCredentials(cid, sec))))
        except (OSError, ValueError):
            pass
    hdr = os.path.join(d, f"{profile}.json")
    if os.path.isfile(hdr):
        out.append(("headers", lambda: YTMusic(hdr)))
    return out


def _artist(t: dict) -> str:
    return ", ".join(a.get("name") or "" for a in (t.get("artists") or []) if a.get("name"))


def _dur(t: dict) -> int:
    if isinstance(t.get("duration_seconds"), (int, float)):
        return int(t["duration_seconds"])
    parts = str(t.get("duration") or "").split(":")
    try:
        n = 0
        for p in parts:
            n = n * 60 + int(p)
        return n
    except ValueError:
        return 0


DATA_API = "https://www.googleapis.com/youtube/v3"
MUSIC_CATEGORY = "10"


def _iso_dur(v: str) -> int:
    import re
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", v or "")
    if not m:
        return 0
    d, h, mi, se = (int(x or 0) for x in m.groups())
    return ((d * 24 + h) * 60 + mi) * 60 + se


def _get(url: str, access: str) -> dict:
    import urllib.request
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {access}"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def _song(v: dict, liked: bool) -> dict:
    sn = v.get("snippet") or {}
    return {"id": v["id"], "title": sn.get("title") or "",
            "artist": (sn.get("channelTitle") or "").removesuffix(" - Topic"),
            "dur": _iso_dur((v.get("contentDetails") or {}).get("duration") or ""),
            "liked": liked, "plays": 0}


def data_api_likes(d: str, profile: str, pages: int = 4,
                   playlists: int = 150, music_share: float = 0.6) -> list[dict]:
    """His liked videos in the Music category, and the songs of his own
    playlists that are mostly music (`music_share` of their first 50), each
    a play; through the official YouTube Data API with yt-profile's OAuth
    token (the YouTube Music API refuses OAuth since Google closed that
    route). The token is refreshed in memory only; the file is not
    rewritten. Read only. Raises on any failure of the likes."""
    import urllib.parse

    tok = json.load(open(os.path.join(d, f"{profile}-oauth.json")))
    c = json.load(open(os.path.join(d, "oauth_client.json")))
    body = urllib.parse.urlencode({
        "client_id": c.get("client_id") or c.get("id"),
        "client_secret": c.get("client_secret") or c.get("secret"),
        "refresh_token": tok["refresh_token"], "grant_type": "refresh_token"}).encode()
    import urllib.request
    with urllib.request.urlopen("https://oauth2.googleapis.com/token", body, timeout=20) as r:
        access = json.load(r)["access_token"]
    out: dict[str, dict] = {}
    page = ""
    for _ in range(pages):
        q = urllib.parse.urlencode({"part": "snippet,contentDetails", "myRating": "like",
                                    "maxResults": 50, **({"pageToken": page} if page else {})})
        got = _get(f"{DATA_API}/videos?{q}", access)
        for v in got.get("items") or []:
            if (v.get("snippet") or {}).get("categoryId") == MUSIC_CATEGORY:
                out[v["id"]] = _song(v, True)
        page = got.get("nextPageToken") or ""
        if not page:
            break
    try:
        page, seen = "", 0
        while seen < playlists:
            q = urllib.parse.urlencode({"part": "id", "mine": "true", "maxResults": 50,
                                        **({"pageToken": page} if page else {})})
            got = _get(f"{DATA_API}/playlists?{q}", access)
            for pl in got.get("items") or []:
                seen += 1
                q2 = urllib.parse.urlencode({"part": "contentDetails", "playlistId": pl["id"],
                                             "maxResults": 50})
                ids = [i["contentDetails"]["videoId"] for i in
                       _get(f"{DATA_API}/playlistItems?{q2}", access).get("items") or []
                       if (i.get("contentDetails") or {}).get("videoId")]
                if len(ids) < 2:
                    continue
                q3 = urllib.parse.urlencode({"part": "snippet,contentDetails", "id": ",".join(ids)})
                vids = _get(f"{DATA_API}/videos?{q3}", access).get("items") or []
                music = [v for v in vids if (v.get("snippet") or {}).get("categoryId") == MUSIC_CATEGORY]
                if len(music) < music_share * len(ids):
                    continue
                for v in music:
                    s = out.setdefault(v["id"], _song(v, False))
                    s["plays"] += 1
            page = got.get("nextPageToken") or ""
            if not page:
                break
    except Exception as e:  # noqa: BLE001 — the likes still count
        print(f"ytmusic_reader: playlists: {type(e).__name__}", file=sys.stderr)
    return list(out.values())


def read(d: str, profile: str) -> dict:
    tried = []
    if os.path.isfile(os.path.join(d, f"{profile}-oauth.json")):
        try:
            likes = data_api_likes(d, profile)
        except Exception as e:  # noqa: BLE001
            likes = None
            tried.append(f"data api: {type(e).__name__} {getattr(e, 'code', '')}".strip())
    else:
        likes = None
    for how, make in _clients(d, profile):
        try:
            yt = make()
            songs: dict[str, dict] = {}

            def add(t: dict, liked: bool) -> None:
                vid = t.get("videoId")
                if not vid or not t.get("title"):
                    return
                s = songs.setdefault(vid, {"id": vid, "title": t["title"], "artist": _artist(t),
                                           "dur": _dur(t), "liked": False, "plays": 0})
                if liked:
                    s["liked"] = True
                else:
                    s["plays"] += 1

            got_any = False
            for name, call in (("liked", lambda: (yt.get_liked_songs(limit=200) or {}).get("tracks") or []),
                               ("history", lambda: yt.get_history() or [])):
                try:
                    for t in call():
                        add(t, name == "liked")
                    got_any = True
                except Exception as e:  # noqa: BLE001 — the other list may still read
                    tried.append(f"{how} {name}: {type(e).__name__}")
            if got_any:
                for t in likes or []:
                    songs.setdefault(t["id"], t)["liked"] = True
                return {"profile": profile, "auth": how + ("+data api" if likes else ""),
                        "songs": list(songs.values()), "error": "; ".join(tried) or None}
        except Exception as e:  # noqa: BLE001
            tried.append(f"{how}: {type(e).__name__}")
    if likes is not None:
        return {"profile": profile, "auth": "data api", "songs": likes,
                "error": "; ".join(tried) or None}
    return {"profile": profile, "auth": None, "songs": [],
            "error": "; ".join(tried) or "no sign-in kept for this profile"}


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__, file=sys.stderr)
        return 2
    d = argv[1]
    print(json.dumps({"accounts": [read(d, p) for p in argv[2:]]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
