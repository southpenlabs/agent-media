"""Everyday names for shops, from open data: "woolies" is Woolworths, "maccas"
McDonald's (David, 22 Nov 2026: "when you search for woolies it doesn't find
Woolworths").

The geocoder (Nominatim) matches a name only as OpenStreetMap writes it, so
place_find puts an everyday name back to the brand's own before it asks.
The table is built from two open sources and kept in
`state_dir()/place-nicknames.json`:

- the name-suggestion-index (NSI, BSD-3, the brand list OSM's editors use;
  `dist/json/nsi.min.json` from jsDelivr): the brands sold in the countries
  in MEDIA_PLACE_COUNTRIES (ISO codes, "au,nz") and the worldwide ones, with
  each brand's OSM name and its Wikidata item;
- Wikidata's English aliases for those items (CC0; `wbgetentities`, 50 to a
  call): Woolworths Supermarkets (Q3249145) has "Woolies", McDonald's
  (Q38076) "Maccas", "Macca's" and "Mickey D's".

Open source first: NSI is the community's brand list and Wikidata the
place its nicknames are kept, so neither is ours to maintain; a missing
nickname is added on Wikidata for everyone.

Aliases are filtered before use: a company's legal name (Pty, Ltd, Inc, …),
one shorter than three letters, one inside the brand's own name ("Home" for
Home Timber & Hardware) and one that is two brands' alias are dropped, so a
common word is never rewritten. The table is rebuilt when a month old, in
the background on the next search (`refresh_if_stale`;
MEDIA_PLACE_NICKNAMES=0 for never); a failed rebuild is not tried again for
a day.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable

from . import failures

NSI_URL = "https://cdn.jsdelivr.net/npm/name-suggestion-index@latest/dist/json/nsi.min.json"
WIKIDATA_API = "https://www.wikidata.org/w/api.php"
USER_AGENT = "agent-media place nicknames (https://github.com/southpenlabs/agent-media)"
MAX_AGE_S = 30 * 86400
RETRY_S = 86400
TIMEOUT_S = 60
FILE = "place-nicknames.json"
_LEGAL = re.compile(r"\b(pty|ltd|limited|inc|incorporated|llc|plc|gmbh|s\.?a\.?|s\.?p\.?a\.?|"
                    r"b\.?v\.?|corp|corporation|company|group|holdings?)\b\.?", re.IGNORECASE)

Fetch = Callable[[str], bytes]


def _path() -> Path:
    from ._paths import state_dir

    return state_dir() / FILE


def _fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as res:
        return res.read()


def countries() -> set[str]:
    raw = os.environ.get("MEDIA_PLACE_COUNTRIES", "")
    return {c.strip().lower() for c in raw.split(",") if c.strip()}


def norm(s: str) -> str:
    """Lower case, apostrophes dropped ("Macca's" and "maccas" alike), the
    rest of the punctuation a space."""
    return " ".join(re.sub(r"[^\w]+", " ", re.sub(r"['\u2019]", "", s.lower())).split())


def brands(nsi: dict, where: set[str]) -> dict[str, str]:
    """Wikidata item -> the brand's OSM name, for brands sold in `where` or
    worldwide ("001")."""
    want = where | {"001"}
    out: dict[str, str] = {}
    for group in (nsi.get("nsi") or {}).values():
        for it in group.get("items") or []:
            inc = {str(x).lower() for x in (it.get("locationSet") or {}).get("include") or []}
            exc = {str(x).lower() for x in (it.get("locationSet") or {}).get("exclude") or []}
            tags = it.get("tags") or {}
            q, name = tags.get("brand:wikidata"), tags.get("brand") or tags.get("name")
            if q and name and inc & want and not (where and where <= exc):
                out.setdefault(q, name)
    return out


def usable(alias: str, brand: str) -> bool:
    a, b = norm(alias), norm(brand)
    if len(a.replace(" ", "")) < 3 or a == b or _LEGAL.search(alias):
        return False
    if a in b or a.replace(" ", "") in b.replace(" ", ""):
        return False
    return True


def build(*, fetch: Fetch | None = None, where: set[str] | None = None) -> dict[str, str]:
    """Nickname (normalised) -> brand name, from NSI and Wikidata."""
    fetch = fetch or _fetch
    where = countries() if where is None else where
    by_q = brands(json.loads(fetch(NSI_URL)), where)
    seen: dict[str, set[str]] = {}
    ids = sorted(by_q)
    for i in range(0, len(ids), 50):
        q = {"action": "wbgetentities", "ids": "|".join(ids[i:i + 50]), "props": "aliases",
             "languages": "en", "format": "json"}
        got = json.loads(fetch(f"{WIKIDATA_API}?{urllib.parse.urlencode(q)}"))
        for qid, ent in (got.get("entities") or {}).items():
            brand = by_q.get(qid)
            for a in ((ent.get("aliases") or {}).get("en") or []):
                alias = str(a.get("value") or "")
                if brand and usable(alias, brand):
                    seen.setdefault(norm(alias), set()).add(brand)
    names = {norm(b) for b in by_q.values()}
    # An alias two brands share, or one that is another brand's own name, is ambiguous.
    return {a: next(iter(bs)) for a, bs in sorted(seen.items()) if len(bs) == 1 and a not in names}


_lock = threading.Lock()
_cache: dict = {"mtime": None, "table": {}}
_busy = threading.Event()


def _read(path: Path) -> dict:
    try:
        d = json.loads(path.read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def table(path: Path | None = None) -> dict[str, str]:
    """The kept table (normalised nickname -> brand), {} before the first build."""
    path = path or _path()
    try:
        m = path.stat().st_mtime
    except OSError:
        return {}
    with _lock:
        if _cache["mtime"] != m:
            t = _read(path).get("nicknames")
            _cache.update(mtime=m, table=t if isinstance(t, dict) else {})
        return _cache["table"]


def refresh(*, fetch: Fetch | None = None, path: Path | None = None, now: float | None = None) -> int:
    """Rebuild and keep the table; the number of nicknames. Raises on failure
    (after noting it, so `refresh_if_stale` waits a day)."""
    path = path or _path()
    now = time.time() if now is None else now
    old = _read(path)
    try:
        t = build(fetch=fetch)
    except Exception:
        old["failed_at"] = now
        _write(path, old)
        raise
    _write(path, {"built_at": now, "countries": sorted(countries()), "nicknames": t})
    return len(t)


def _write(path: Path, d: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}")
    tmp.write_text(json.dumps(d, ensure_ascii=False))
    os.replace(tmp, path)


def stale(path: Path | None = None, now: float | None = None) -> bool:
    d = _read(path or _path())
    now = time.time() if now is None else now
    failed = d.get("failed_at")
    if failed is not None and now - float(failed) < RETRY_S:
        return False
    if sorted(countries()) != (d.get("countries") or []):
        return True
    built = d.get("built_at")
    return built is None or now - float(built) > MAX_AGE_S


def refresh_if_stale(path: Path | None = None) -> None:
    """Rebuild in a background thread when the table is old or missing; the
    search in hand uses what is kept."""
    if os.environ.get("MEDIA_PLACE_NICKNAMES", "1").strip() == "0" or _busy.is_set():
        return
    if not stale(path):
        return
    _busy.set()

    def run() -> None:
        try:
            refresh(path=path)
        except Exception as e:  # noqa: BLE001 - no network: the old table stays
            failures.swallowed(e)
        finally:
            _busy.clear()

    threading.Thread(target=run, name="place-nicknames", daemon=True).start()
