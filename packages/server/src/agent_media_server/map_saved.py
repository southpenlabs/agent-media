"""`GET /map/saved`, `POST /map/saved`: a person's Home, Work and other saved places.

The Map's Home and Work keys route there in one tap (David, 19 Nov 2026:
"let's add work and home buttons and all the other ones Google has"), on
every device of that person, and an agent can read them ("directions home").

Kept per account (auth.device_account: "" for the owner's, the account's key
for anyone else's) in `state_dir()/map-saved.json`, `{account: [place…]}`.
A place is `{id, name, lat, lon, address?, label?}`: `id` is `home`, `work`
or one of the person's own (a short slug, `saved-…` made up when not given);
`label` the key's words for one of their own ("Gym"). The host's own token
reads the owner's.

`GET /map/saved` → `{places: [...]}`, Home first, then Work, then the rest
as saved. `POST /map/saved {id?, name, lat, lon, address?, label?}` sets one
(→ `{place}`); `{id, delete: true}` takes one off (→ `{deleted: id}`, 404
when it was not there). 400 with a code: `bad_point`, `bad_name`, `bad_id`,
`too_many` (at most `MAX` saved).
"""
from __future__ import annotations

import json
import os
import re
import secrets
import threading
from pathlib import Path

#: Saved places a person keeps, at most (Home and Work among them).
MAX = 50
_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
_LOCK = threading.Lock()


def _path() -> Path:
    from agent_media_core._paths import state_dir

    return state_dir() / "map-saved.json"


def _load(path: Path | None = None) -> dict[str, list[dict]]:
    try:
        got = json.loads((path or _path()).read_text())
    except (OSError, ValueError):
        return {}
    return got if isinstance(got, dict) else {}


def _save(book: dict[str, list[dict]], path: Path | None = None) -> None:
    p = path or _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(book, ensure_ascii=False, indent=1))
    os.replace(tmp, p)


def _order(places: list[dict]) -> list[dict]:
    rank = {"home": 0, "work": 1}
    return sorted(places, key=lambda p: rank.get(p.get("id", ""), 2))


def account_of(bearer: str, host: bool = False) -> str:
    """Whose book: the device's account, the owner's ("") for the host."""
    if host:
        return ""
    from . import auth

    user, _ = auth.gate(bearer)
    return str((user or {}).get("account_key") or "")


def listing(account: str, path: Path | None = None) -> tuple[bool, dict]:
    return True, {"places": _order(list(_load(path).get(account) or []))}


def change(account: str, body: dict, path: Path | None = None) -> tuple[bool, dict]:
    pid = str(body.get("id") or "").strip().lower()
    if pid and not _ID.fullmatch(pid):
        return False, {"status": 400, "code": "bad_id", "error": "id is a short slug: home, work or your own"}
    with _LOCK:
        book = _load(path)
        mine = list(book.get(account) or [])
        if body.get("delete"):
            left = [p for p in mine if p.get("id") != pid]
            if not pid or len(left) == len(mine):
                return False, {"status": 404, "code": "not_saved", "error": "no such saved place"}
            book[account] = left
            _save(book, path)
            return True, {"deleted": pid}
        try:
            lat, lon = float(body.get("lat")), float(body.get("lon"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return False, {"status": 400, "code": "bad_point", "error": "lat and lon are numbers"}
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            return False, {"status": 400, "code": "bad_point", "error": "lat and lon are numbers"}
        name = str(body.get("name") or "").strip()[:200]
        if not name:
            return False, {"status": 400, "code": "bad_name", "error": "a saved place has a name"}
        pid = pid or f"saved-{secrets.token_hex(4)}"
        place: dict = {"id": pid, "name": name, "lat": round(lat, 6), "lon": round(lon, 6)}
        for k in ("address", "label"):
            v = str(body.get(k) or "").strip()[:200]
            if v:
                place[k] = v
        others = [p for p in mine if p.get("id") != pid]
        if len(others) >= MAX:
            return False, {"status": 400, "code": "too_many", "error": f"at most {MAX} saved places"}
        book[account] = _order([*others, place])
        _save(book, path)
        return True, {"place": place}
