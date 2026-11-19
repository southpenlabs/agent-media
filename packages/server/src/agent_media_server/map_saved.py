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

`GET /map/saved` → `{places: [...], lists: [...]}`, Home first, then Work,
then the rest as saved. `POST /map/saved {id?, name, lat, lon, address?,
label?, lists?}` sets one (→ `{place}`); `{id, delete: true}` takes one off
(→ `{deleted: id}`, 404 when it was not there). 400 with a code:
`bad_point`, `bad_name`, `bad_id`, `bad_list`, `too_many` (at most `MAX`).

Lists (David, 19 Nov 2026: saved lists and stars, as Google Maps has them):
a place's `lists` names the lists it is in. Three are always there —
`starred`, `favourites`, `want` (Want to go) — and a person makes more:
`POST /map/saved/lists {id?, name}` makes or renames one (→ `{list}`),
`{id, delete: true}` takes it off (→ `{deleted}`; the built-in three
cannot be, 400 `builtin`), and a place left in no list that is neither Home
nor Work goes with it. `lists` in the answer: `[{id, name, builtin?}]`, the
built-in three first. Kept beside the places, under `lists:<account>`.
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


#: The lists everyone has, first, by id: their English names (the app shows its own words).
BUILTIN = [{"id": "starred", "name": "Starred", "builtin": True},
           {"id": "favourites", "name": "Favourites", "builtin": True},
           {"id": "want", "name": "Want to go", "builtin": True}]
#: Lists of a person's own, at most.
MAX_LISTS = 30


def _lists_of(book: dict, account: str) -> list[dict]:
    return [*BUILTIN, *(book.get(f"lists:{account}") or [])]


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
    book = _load(path)
    return True, {"places": _order(list(book.get(account) or [])), "lists": _lists_of(book, account)}


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
        if body.get("lists") is not None:
            want = body.get("lists")
            known = {x["id"] for x in _lists_of(book, account)}
            if not isinstance(want, list) or any(str(x) not in known for x in want):
                return False, {"status": 400, "code": "bad_list", "error": "lists names lists you have"}
            if want:
                place["lists"] = list(dict.fromkeys(str(x) for x in want))
        else:
            # Moved or renamed without saying: the lists it was in stay.
            was = next((p for p in mine if p.get("id") == pid), None)
            if was and was.get("lists"):
                place["lists"] = was["lists"]
        others = [p for p in mine if p.get("id") != pid]
        if len(others) >= MAX:
            return False, {"status": 400, "code": "too_many", "error": f"at most {MAX} saved places"}
        book[account] = _order([*others, place])
        _save(book, path)
        return True, {"place": place}


def change_list(account: str, body: dict, path: Path | None = None) -> tuple[bool, dict]:
    """`POST /map/saved/lists`: make, rename or take off a list of one's own."""
    lid = str(body.get("id") or "").strip().lower()
    if lid and not _ID.fullmatch(lid):
        return False, {"status": 400, "code": "bad_id", "error": "id is a short slug"}
    if any(b["id"] == lid for b in BUILTIN):
        return False, {"status": 400, "code": "builtin", "error": "the built-in lists stay"}
    with _LOCK:
        book = _load(path)
        key = f"lists:{account}"
        own = list(book.get(key) or [])
        if body.get("delete"):
            left = [x for x in own if x.get("id") != lid]
            if not lid or len(left) == len(own):
                return False, {"status": 404, "code": "not_saved", "error": "no such list"}
            book[key] = left
            kept = []
            for p in book.get(account) or []:
                lists = [x for x in p.get("lists") or [] if x != lid]
                if lists or p.get("id") in ("home", "work"):
                    q = {k: v for k, v in p.items() if k != "lists"}
                    if lists:
                        q["lists"] = lists
                    kept.append(q)
            book[account] = kept
            _save(book, path)
            return True, {"deleted": lid}
        name = str(body.get("name") or "").strip()[:80]
        if not name:
            return False, {"status": 400, "code": "bad_name", "error": "a list has a name"}
        lid = lid or f"list-{secrets.token_hex(4)}"
        others = [x for x in own if x.get("id") != lid]
        if len(others) >= MAX_LISTS:
            return False, {"status": 400, "code": "too_many", "error": f"at most {MAX_LISTS} lists"}
        lst = {"id": lid, "name": name}
        book[key] = [*others, lst] if len(others) == len(own) else [lst if x.get("id") == lid else x for x in own]
        _save(book, path)
        return True, {"list": lst}
