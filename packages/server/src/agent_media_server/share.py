"""A link shared to the app, played by agent-media (`/share`).

Moved out of the canvas's reply.py.
"""

from __future__ import annotations

import threading

from . import auth


def share_from_app(text: str, channel: str, bearer: str) -> tuple[bool, dict]:
    """A link shared to the app, played by agent-media. Gated like `/reply`.

    The same pipeline as media-share's `/share` (share.share, then the
    listener's dispatch on a thread): the verdict comes back for the toast
    while the fetching happens behind it.
    """
    import threading

    from agent_media_core import share as sharemod
    from agent_media_core.entrypoints import share_listener

    user, err = auth.gate(bearer)
    if not user:
        return False, err
    if not (text or "").strip():
        return False, {"error": "nothing shared"}
    channel = channel if channel in ("music", "book") else ""
    # A Spotify album, playlist or song: saved to the Music tab's Albums and
    # played from YouTube (music_links; David, 2 Nov 2026: "Is there a way
    # to share a link to an album from Spotify?").
    from agent_media_core import music_links

    url = sharemod.extract_url(text) or ""
    if music_links.is_music_link(url):
        try:
            got = music_links.add(url)
        except ValueError as e:
            return False, {"error": str(e), "status": 422}
        r = music_links.play(got["id"])
        if not r.get("ok"):
            if r.get("held"):      # the play gate: a treatment is under way
                return False, {"error": r.get("error"), "held": True, "status": 409}
            return False, {"error": r.get("error") or "couldn't play it", "status": 502}
        return True, {"url": url, "channel": "music", "title": got["title"],
                      "line": f"Playing {got['title']} ({got['tracks']} tracks, from YouTube)"}
    # A podcast app's episode link: its audio, on the book channel, not a
    # page handed to the music player (David, 1 Nov 2026: "I tried to share
    # a podcast with sasonica but it came through as music").
    ep = _podcast_episode(text) if channel != "music" else None
    if ep:
        verdict = sharemod.Verdict(channel="book", content_type="podcast",
                                   reason=f"a podcast episode{' of ' + ep['show'] if ep.get('show') else ''}",
                                   title=ep.get("title") or "")
        threading.Thread(target=share_listener._play, args=(ep["audio"], verdict, ""),
                         daemon=True, name="share-dispatch").start()
        return True, {"url": ep["audio"], "channel": "book", "title": verdict.title,
                      "line": verdict.line()}
    try:
        url, verdict = sharemod.share(text, channel=channel,
                                      probe_timeout=share_listener.PROBE_TIMEOUT_S)
    except sharemod.ShareError as e:
        return False, {"error": str(e), "status": 422}
    threading.Thread(target=share_listener._play, args=(url, verdict, ""),
                     daemon=True, name="share-dispatch").start()
    return True, {"url": url, "channel": verdict.channel, "title": verdict.title,
                  "line": verdict.line()}


def _podcast_episode(text: str) -> dict | None:
    """The podcast episode a shared link is (agent_media_core.podcast_resolve),
    for any link but YouTube's and the music sites': those keep the share
    pipeline's own probe."""
    from agent_media_core import podcast_resolve
    from agent_media_core import share as sharemod

    url = sharemod.extract_url(text)
    if not url:
        return None
    host = (url.split("/")[2] if "//" in url else "").lower()
    if sharemod._host_matches(host, ("youtube.com", "youtu.be", *sharemod.MUSIC_HOSTS)) \
            or (host.endswith("spotify.com") and "/episode/" not in url):
        return None
    try:
        return podcast_resolve.episode(url, text, search=podcast_resolve.looks_like_podcast(url))
    except Exception:  # noqa: BLE001 — the share pipeline decides as before
        return None
