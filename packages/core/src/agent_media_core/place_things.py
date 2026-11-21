"""`media place thing <kind> <name-or-id> [--here | lat,lon]`: put one of
David's devices, rooms or speakers on the Map (maps proposal step 4, "Where
things are"; the server's `GET/POST /map/things`, §6.50).

An agent told "the kitchen speaker is here" runs
`media place thing room kitchen --here`: the thing is found by its id or
name (or the label it was placed with), and placed at the phone's shared
location (§6.21b). The host's own token places the owner's things.
"""
from __future__ import annotations

KINDS = ("device", "room", "speaker")


def _norm(s: str) -> str:
    return " ".join(str(s or "").lower().replace("-", " ").replace("_", " ").split())


def match(listing: dict, kind: str, words: str) -> list[dict]:
    """The things of `kind` (placed and not) whose id, name or label is the
    words, else contains them, else contains every word; the best tier only."""
    rows = [*(listing.get("things") or []), *(listing.get("unplaced") or [])]
    rows = [r for r in rows if not kind or r.get("kind") == kind]
    want = _norm(words)
    if not want:
        return []

    def names(r: dict) -> list[str]:
        return [_norm(r.get(k) or "") for k in ("id", "name", "label") if r.get(k)]

    exact = [r for r in rows if r.get("id") == words or want in names(r)]
    if exact:
        return exact
    part = [r for r in rows if any(want in n for n in names(r))]
    if part:
        return part
    toks = want.split()
    return [r for r in rows if all(any(t in n for n in names(r)) for t in toks)]


def point(raw: str) -> tuple[float, float] | None:
    try:
        lat, lon = (float(x) for x in (raw or "").replace(" ", "").split(","))
    except ValueError:
        return None
    return (lat, lon) if -90 <= lat <= 90 and -180 <= lon <= 180 else None
