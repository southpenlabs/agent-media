"""Public facts every install shares: the central layer
(docs/proposals/2026-11-01-three-data-layers.md; the service is deploy/facts,
https://sasonica.com/facts/v1).

David, 1 Nov 2026: "central metadata vs pier to pier vs private ... a
combination of all three" — then "Build now". Only facts that are the same
for everyone go here, never anything about a person: today a YouTube video's
or an audio URL's length, so a second install does not ask YouTube all over
again, and one with no phone (YouTube refuses servers) still gets lengths;
and where a teacher is from (their own public profile's place), so the
Teachers list's Where filter is there without looking each one up.

- `key_for(item)` — the shared key of a practice item: `yt:<video id>`, or
  `audio:<sha256(url)[:32]>` for a podcast episode. None for a file in the
  Practice folder and for the agent's own meditations: those never leave.
- `lookup(keys)` — `{key: seconds}` the service knows. Unsigned, so a lookup
  is not tied to this install.
- `place_key(kind, name)` — `teacher:<name in letters and digits>` (also
  artist, author, show); `lookup_places(keys)` — `{key: {country, state,
  city, region, photo}}` the service knows, unsigned as `lookup`.
- `report(lengths, seed, pub, places=None)` — what this install found
  itself, signed with its install key (the server's tunnel.identity) as
  /feedback is. A fact once two installs agree (a place on country, state
  and city), or a trusted one (red5) reports it.

`MEDIA_FACTS_URL` moves the service; `-` turns both off.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from typing import Optional

DEFAULT_URL = "https://sasonica.com/facts/v1"
UA = "sasonica-facts/1"
#: The most keys one request carries (the service's limit), and the most
#: places: a place with its photo's URL is ~450 bytes, and a request at most 32 KB.
BATCH = 200
PLACE_BATCH = 50


def base() -> str:
    from .intake._env import switched_off

    if switched_off("MEDIA_FACTS_URL"):
        return ""
    return (os.environ.get("MEDIA_FACTS_URL") or DEFAULT_URL).rstrip("/")


def key_for(item: dict) -> Optional[str]:
    """The shared key for a practice item, or None for one that is private."""
    iid = item.get("id") or ""
    uri = item.get("_uri") or ""
    if iid.startswith("yt:") and len(iid) == 14:
        return iid
    if item.get("source") == "podcast" and uri.startswith(("http://", "https://")):
        return "audio:" + hashlib.sha256(uri.encode()).hexdigest()[:32]
    return None


PLACE_KINDS = ("teacher", "artist", "author", "show")
PLACE_FIELDS = ("country", "state", "city", "region", "photo")


def place_key(kind: str, name: str) -> Optional[str]:
    norm = re.sub(r"[^a-z0-9]", "", (name or "").lower())[:80]
    return f"{kind}:{norm}" if kind in PLACE_KINDS and norm else None


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _post(path: str, data: bytes, headers: dict | None = None, timeout: float = 15.0) -> tuple[bool, dict]:
    req = urllib.request.Request(f"{base()}{path}", data=data, method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": UA,
                                          **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return True, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            got = json.loads(e.read(2000) or b"{}")
        except ValueError:
            got = {}
        return False, {"status": e.code, "error": got.get("error") or f"HTTP {e.code}"}
    except (OSError, ValueError) as e:
        return False, {"status": 0, "error": str(e)}


def _ask(path: str, field: str, keys: list[str]) -> dict:
    if not base():
        return {}
    out: dict = {}
    keys = list(dict.fromkeys(k for k in keys if k))
    for i in range(0, len(keys), BATCH):
        ok, got = _post(path, json.dumps({"keys": keys[i:i + BATCH]}).encode())
        if not ok:
            break
        out.update(got.get(field) or {})
    return out


def lookup(keys: list[str]) -> dict:
    """`{key: seconds}` for those the service knows; {} when off or out of reach."""
    return {k: v for k, v in _ask("/durations", "durations", keys).items() if isinstance(v, int) and v > 0}


def lookup_places(keys: list[str]) -> dict:
    """`{key: {country, state, city, region, photo}}` for those the service
    knows; {} when off or out of reach."""
    return {k: {f: v.get(f) for f in PLACE_FIELDS} for k, v in _ask("/places", "places", keys).items()
            if isinstance(v, dict)}


def report(lengths: dict, seed: bytes, pub: bytes, places: dict | None = None) -> tuple[bool, dict]:
    """Send `{key: seconds}` and `{key: place}` this install found itself,
    signed. Returns `(ok, {"accepted": n} | {"error"})`; stops at the first
    refusal."""
    from ._ed25519 import sign

    if not base():
        return False, {"error": "off"}
    items = [("durations", k, int(v)) for k, v in lengths.items() if k and isinstance(v, (int, float)) and v > 0]
    items += [("places", k, {f: v.get(f) for f in PLACE_FIELDS if v.get(f)})
              for k, v in (places or {}).items() if k and isinstance(v, dict) and (v.get("country") or v.get("region"))]
    lens = [it for it in items if it[0] == "durations"]
    plcs = [it for it in items if it[0] == "places"]
    batches = [lens[i:i + BATCH] for i in range(0, len(lens), BATCH)]
    batches += [plcs[i:i + PLACE_BATCH] for i in range(0, len(plcs), PLACE_BATCH)]
    accepted = 0
    for batch in batches:
        body: dict = {}
        for field, k, v in batch:
            body.setdefault(field, {})[k] = v
        body.update(pub=_b64url(pub), ts=round(time.time(), 3))
        data = json.dumps(body, separators=(",", ":")).encode()
        ok, got = _post("/report", data, {"X-Sasonica-Signature": _b64url(sign(seed, data))})
        if not ok:
            return False, got
        accepted += int(got.get("accepted") or 0)
        time.sleep(0.01)   # each request's ts must be newer than the last
    return True, {"accepted": accepted}
