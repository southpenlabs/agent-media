"""Practices that play in their maker's own embedded player, listed beside the
rest (David, 1 Nov 2026: saved Insight Timer tracks, "can we still index
them"). The index is the person's own data, so it is never in this package:
MEDIA_PRACTICE_EMBED_INDEX names a JSON list of
``{"title", "teacher", "length"?, "folders"?, "embed", "page"?, "kind"?}``
(an Insight Timer export: ``insight-timer/saved-index.json``). Unset, there
are none.

Each becomes an item ``it:<hash>`` with source ``embed``, its ``embed`` and
``page`` links (public pages, unlike the rest of the list's addresses, since
the app plays them itself) and ``folders``. Playing one plays nothing here:
the play is counted and the app is told the embed to open (practice_library
.play, server-contract.md §6.45).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Optional


def index_path() -> Optional[Path]:
    raw = (os.environ.get("MEDIA_PRACTICE_EMBED_INDEX") or "").strip()
    return Path(raw).expanduser() if raw else None


def _seconds(length: str) -> Optional[int]:
    """"12 min", "1 hr 1 min" → seconds."""
    m = re.fullmatch(r"\s*(?:(\d+)\s*hr?s?)?\s*(?:(\d+)\s*min)?\s*", length or "")
    if not m or not (m.group(1) or m.group(2)):
        return None
    return (int(m.group(1) or 0) * 60 + int(m.group(2) or 0)) * 60 or None


def items() -> list[dict]:
    p = index_path()
    if p is None:
        return []
    try:
        rows = json.loads(p.read_text())
    except (OSError, ValueError):
        return []
    out = []
    for r in rows if isinstance(rows, list) else []:
        embed = r.get("embed") if isinstance(r, dict) else None
        if not (isinstance(embed, str) and embed.startswith("https://")):
            continue
        title = (r.get("title") or "").strip() or embed
        teacher = (r.get("teacher") or "").strip()
        secs = _seconds(r.get("length") or "")
        out.append({"id": "it:" + hashlib.sha256(embed.encode()).hexdigest()[:12],
                    "title": f"{title} — {teacher}" if teacher else title,
                    "source": "embed", "embed": embed, "page": r.get("page") or None,
                    "folders": list(r.get("folders") or []),
                    "duration": str(secs) if secs else "", "_uri": embed})
    return out
