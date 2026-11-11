"""How often a song comes round: a step or a few either side of normal.

David, 11 Nov 2026: "As well as likes and dislikes, can we have an option
to increase or decrease a song's rotation?" — the song card's More often
and Less often (`POST /music {"action": "rotation", "uri", "step"}`).

One level per YouTube song, MIN_LEVEL to MAX_LEVEL, 0 (normal) not kept;
a song inside a mix is one too, by its chapter's key (mix_songs.key:
`<video id>@<start>`; David, 12 Nov 2026), its entry also keeping `vid`,
`start`, `end`, `artist` and `song` so a pick can play just that song;
`weight` is FACTOR to the level, how many times as often as normal it is
picked. Kept in `<state_dir>/music-rotation.json` as `{video id: {"level",
"title", "at"}}`; never raises into a caller for a missing or broken file.

Who honours it:
- the backing music's favourite under a reply (music_bed_favourites.
  track_for), and so the song Sing it out sings (the original of that
  favourite, music_bed_vocal), and the ranking that decides which songs
  are favourites at all (`gather`);
- a radio station's refill (radio._merge: a song turned down is left out
  of a batch that often; one turned up goes to the front of its batch),
  the song a Mix station refills from (`refill`), and the DJ's brief
  (radio_dj: songs to play more and less).
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Optional

from ._paths import state_dir

MIN_LEVEL, MAX_LEVEL = -3, 3
#: Each level up is this many times as often.
FACTOR = 1.6
#: A video, or a chapter of one (mix_songs.key).
_ID = re.compile(r"^[A-Za-z0-9_-]{11}(@\d+)?$")


def _path():
    return state_dir() / "music-rotation.json"


def load() -> dict:
    try:
        d = json.loads(_path().read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def level(vid: str, data: Optional[dict] = None) -> int:
    e = (load() if data is None else data).get(vid or "")
    try:
        return max(MIN_LEVEL, min(MAX_LEVEL, int((e or {}).get("level") or 0)))
    except (TypeError, ValueError, AttributeError):
        return 0


def weight(vid: str, data: Optional[dict] = None) -> float:
    """How many times as often as normal: 1 for a song never turned."""
    return FACTOR ** level(vid, data)


def step(vid: str, by: int, title: str = "", now: Optional[float] = None,
         extra: Optional[dict] = None) -> int:
    """Turn a song up (`by` > 0) or down, within the levels; its new level.
    `vid` a video id, or a mix's chapter's key with `extra` its `start`,
    `end`, `artist` and `song`. ValueError for a bad id."""
    if not _ID.match(vid or ""):
        raise ValueError("not a YouTube video id or a chapter of one")
    data = load()
    new = max(MIN_LEVEL, min(MAX_LEVEL, level(vid, data) + int(by)))
    if new == 0:
        data.pop(vid, None)
    else:
        old = data.get(vid) if isinstance(data.get(vid), dict) else {}
        data[vid] = {**{k: v for k, v in old.items() if k not in ("level", "title", "at")},
                     **(extra or {}),
                     "level": new, "title": title or old.get("title") or "",
                     "at": time.time() if now is None else now}
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{os.getpid()}")
    tmp.write_text(json.dumps(data, ensure_ascii=False))
    os.replace(tmp, p)
    return new


def titled(sign: int, data: Optional[dict] = None, limit: int = 15) -> list[str]:
    """The names of the songs turned up (`sign` > 0) or down, the most
    turned first: for the DJ."""
    rows = [(level(v, data), e.get("title")) for v, e in (load() if data is None else data).items()
            if isinstance(e, dict)]
    rows = [(n, t) for n, t in rows if t and n * sign > 0]
    rows.sort(key=lambda r: -abs(r[0]))
    return [t for _, t in rows[:limit]]
