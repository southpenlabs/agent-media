"""The song that played under a reply, and playing it again in the music player.

David, 30 Oct 2026: "let's also add to the reply menu, a way of playing the
actual song through the music player". Roadmap item 39 (the music under long
readings): when submit starts a bed under a reply, `song_of(plan)` is kept on
that reply's speech history row as `extras.bed_song`, and the app's reply ⋯
menu offers "Play the song" for a row that has one (server-contract §6.9a,
`POST /music {"action": "song", "id"}`).

What is kept, by source:

- **favourites** (an instrumental of one of his favourite songs): the
  ORIGINAL, with its vocals — artist, title, its YouTube Music video id and
  the `yt:<id>` the music player plays;
- **streaming** (a song from his YouTube Music on Mopidy): the first track
  the bed put on — its video id, its Mopidy URI, the cached copy the bed
  played and `yt:<id>`;
- **library** (archive.org or ccMixter): the track itself — its page, the
  cached file and the download address the music player plays;
- **generated**: nothing; there is no song to play.

`uri` is what `media music play` is handed; a row with no `uri` has nothing
to play. Never raises.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

log = logging.getLogger(__name__)

#: A YouTube video id.
_VID = re.compile(r"^[\w-]{11}$")
#: What the app is shown of a song (Turn.song, the reply's `spoken.song`).
SHOWN = ("source", "title", "artist")


def _vid_of(url: str) -> str:
    m = re.search(r"[?&]v=([\w-]{11})", url or "")
    return m.group(1) if m else ""


def song_of(plan) -> Optional[dict]:
    """What to keep on the reply's row for bed `plan` (music_bed.Plan), or
    None (a generated bed, or nothing known enough to play)."""
    try:
        source = getattr(plan, "source", "")
        credit = getattr(plan, "credit", None) or {}
        if source == "favourites":
            vid = str(credit.get("original_id") or "") or _vid_of(credit.get("url") or "")
            if not _VID.match(vid):
                return None
            return {"source": source,
                    "title": str(credit.get("original_title") or ""),
                    "artist": str(credit.get("creator") or ""),
                    "video_id": vid, "uri": f"yt:{vid}"}
        if source == "streaming":
            tracks = getattr(plan, "tracks", ()) or ()
            first = tracks[0] if tracks else {}
            vid = str(first.get("id") or "") or _vid_of(credit.get("url") or "")
            if not _VID.match(vid):
                return None
            return {"source": source,
                    "title": str(first.get("title") or credit.get("title") or ""),
                    "artist": str(first.get("artist") or credit.get("creator") or ""),
                    "video_id": vid,
                    "mopidy_uri": f"youtube:video:{vid}",
                    # The copy the bed played (the rooms cache), not what
                    # is played again: that is the song itself, `uri`.
                    "file": str(first.get("file") or ""),
                    "uri": f"yt:{vid}"}
        if source == "library":
            download = str(credit.get("download") or "")
            if not download.startswith(("https://", "http://")):
                return None
            path = getattr(plan, "path", None)
            return {"source": source,
                    "title": str(credit.get("title") or ""),
                    "artist": str(credit.get("creator") or ""),
                    "page": str(credit.get("url") or ""),
                    "file": str(path) if path else "",
                    "site": str(credit.get("source") or ""),
                    "uri": download}
    except Exception as e:  # noqa: BLE001 — a bed's record must never cost a reply
        log.warning("music-bed song: %s", e)
    return None


def started(plan, under: bool, stop) -> Optional[dict]:
    """`song_of(plan)` when `music_bed.start` played it as planned, from its
    answer (`under`, `stop`): a clip plan plays only when its under-player
    started; a Mopidy plan only when it was left on Mopidy (an under-player
    there is the generated stand-in it fell back to)."""
    kind = getattr(plan, "kind", "")
    if kind == "under" and under:
        return song_of(plan)
    if kind == "stream" and not under and stop is not None:
        return song_of(plan)
    return None


def shown(song) -> Optional[dict]:
    """What the app is shown of a row's `bed_song`: source, title and artist,
    and only when there is something to play."""
    if not isinstance(song, dict) or not str(song.get("uri") or "").strip():
        return None
    return {k: str(song.get(k) or "") for k in SHOWN}


def label(song: dict) -> str:
    """The music player's title for it: "<title> — <artist>"."""
    title, artist = str(song.get("title") or ""), str(song.get("artist") or "")
    return f"{title} — {artist}" if title and artist else title or artist
