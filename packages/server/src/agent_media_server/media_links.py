"""A link shared or pasted into a Media tab (server-contract.md §6.46).

David, 1 Nov 2026: "a share option would be even better... so that we can
share from YouTube and select which media tab it goes to". The app asks
which tabs take a link and shows one chip each (the app stays generic: the
server knows what each tab does with it).

  GET  /media/links → {"ok", "to": [{"id", "label", "takes"}]}
  POST /media/links {"url", "to", "title"?}       → {"ok", "added", "title", …}
                    {"action": "remove", "id", "keep_link"?}   (a saved podcast link)

- podcast — a YouTube video, saved and downloaded small
  (agent_media_core.podcast_links); the Podcasts tab's `saved` list.
- meditation — a YouTube video or a podcast feed (meditation_library.add).
- book — a YouTube video or playlist, imported into Audiobookshelf as
  `media book import-youtube` does.
"""

from __future__ import annotations

from . import auth

TABS = [
    {"id": "podcast", "label": "Podcasts", "takes": "a YouTube video"},
    {"id": "meditation", "label": "Meditations", "takes": "a YouTube video or a podcast feed"},
    {"id": "book", "label": "Books", "takes": "a YouTube video or playlist"},
]


def listing(bearer: str) -> tuple[bool, dict]:
    user, err = auth.gate(bearer)
    if not user:
        return False, err
    return True, {"to": TABS}


def _book(url: str) -> dict:
    from agent_media_core import library

    if not library.is_youtube(url):
        raise ValueError("a book link must be a YouTube video or playlist")
    urls = library.expand_youtube_playlist(url) or [url]
    if not library.start_fetch_many(urls):
        raise RuntimeError("the YouTube download helper is not installed here")
    return {"added": "book", "title": url, "items": len(urls)}


def control(body: dict, bearer: str) -> tuple[bool, dict]:
    user, err = auth.gate(bearer)
    if not user:
        return False, err
    from agent_media_core import meditation_library, podcast_links

    if body.get("action") == "remove":
        if not podcast_links.remove(str(body.get("id") or ""), keep_link=bool(body.get("keep_link"))):
            return False, {"error": "no saved podcast link under that id", "status": 404}
        return True, {"removed": str(body.get("id"))}
    url = str(body.get("url") or "").strip()
    to = str(body.get("to") or "")
    title = str(body.get("title") or "")
    if not url.startswith(("http://", "https://")):
        return False, {"error": "url must be an http(s) link"}
    try:
        if to == "podcast":
            return True, podcast_links.add(url, title=title)
        if to == "meditation":
            return True, meditation_library.add(url, title=title)
        if to == "book":
            return True, _book(url)
    except ValueError as e:
        return False, {"error": str(e)}
    except RuntimeError as e:
        return False, {"error": str(e), "status": 503}
    return False, {"error": "to must be one of " + ", ".join(t["id"] for t in TABS)}
