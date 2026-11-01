"""Peer to peer: a practice item sent to a person over Matrix, and the ones
sent to the owner, kept until they save or dismiss them (server-contract.md
§6.48; docs/proposals/2026-11-01-three-data-layers.md, step 5).

David, 1 Nov 2026: "Start now" … "Neither for now" (person to person first;
sasonica.com as a share target decided later).

A share is an ordinary `m.room.message` (`m.text`, so any Matrix app shows
its words) carrying an `org.sasonica.share` object a Sasonica server reads:

    {"v": 1, "kind": "practice", "source": "youtube" | "podcast",
     "title", "url", "key"?, "show"?, "teacher"?, "duration_s"?, "note"?}

It goes, as the owner (`MATRIX_OWNER_TOKEN`), into the owner's direct room
with that person (account data `m.direct`), or a new one. It is sent
unencrypted, so the other side's server can read it: the homeservers see it,
as they see every unencrypted message. Only a YouTube link or a podcast
episode can be shared (they have a public address); a file in the Practice
folder and the agent's own meditations never leave.

Arriving: the owner's loop (matrix._Owner) hands every timeline event of
every room the owner is in to `consider`; one carrying a share, from anyone
but the owner, is kept in `<state>/shares-incoming.json` until `save` (into
the practice list, practice_library.add / add_episode) or `dismiss`.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
from pathlib import Path

from agent_media_core._paths import state_dir

from . import auth

KEY = "org.sasonica.share"
#: A Matrix user id: @local:server.
_MXID = re.compile(r"^@[a-z0-9._=/+-]+:[A-Za-z0-9.-]+(:\d+)?$")
#: Shares kept, newest first; older dismissed or saved ones are dropped first.
KEEP = 200
MAX_NOTE = 500
_LOCK = threading.Lock()


# ---- what arrives ----------------------------------------------------------------

def _path() -> Path:
    return state_dir() / "shares-incoming.json"


def _load() -> dict:
    try:
        d = json.loads(_path().read_text())
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def _save(d: dict) -> None:
    if len(d) > KEEP:
        done = sorted((k for k, v in d.items() if v.get("status") != "new"), key=lambda k: d[k].get("at") or 0)
        for k in done[:len(d) - KEEP]:
            d.pop(k, None)
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(f".tmp.{os.getpid()}")
    tmp.write_text(json.dumps(d))
    tmp.replace(p)


def _clean(share) -> dict | None:
    """A share as sent, checked; None when it is not one this server takes."""
    if not isinstance(share, dict) or share.get("kind") != "practice":
        return None
    url = share.get("url")
    title = share.get("title")
    if share.get("source") not in ("youtube", "podcast") or not isinstance(url, str) \
            or not url.startswith(("https://", "http://")) or len(url) > 2000:
        return None
    if not isinstance(title, str) or not title.strip():
        return None
    out = {"kind": "practice", "source": share["source"], "url": url, "title": title.strip()[:300]}
    for k in ("key", "show", "teacher", "note"):
        v = share.get(k)
        if isinstance(v, str) and v.strip():
            out[k] = v.strip()[:MAX_NOTE]
    d = share.get("duration_s")
    if isinstance(d, int) and 0 < d < 48 * 3600:
        out["duration_s"] = d
    return out


def consider(room_id: str, ev: dict, owner: str) -> bool:
    """Keep `ev` if it is a share sent to the owner. True when kept."""
    if ev.get("type") != "m.room.message" or not ev.get("event_id"):
        return False
    sender = ev.get("sender") or ""
    if not sender or sender == owner:
        return False
    share = _clean((ev.get("content") or {}).get(KEY))
    if share is None:
        return False
    with _LOCK:
        d = _load()
        if ev["event_id"] in d:
            return False
        d[ev["event_id"]] = {"id": ev["event_id"], "room": room_id, "from": sender,
                             "at": (ev.get("origin_server_ts") or time.time() * 1000) / 1000,
                             "share": share, "status": "new"}
        _save(d)
    return True


def listing(bearer: str) -> tuple[bool, dict]:
    """`GET /shares`: what was sent to the owner, newest first."""
    user, err = auth.gate(bearer)
    if not user:
        return False, err
    items = sorted(_load().values(), key=lambda v: v.get("at") or 0, reverse=True)
    return True, {"shares": items, "can_send": _owner() is not None}


def control(body: dict, bearer: str) -> tuple[bool, dict]:
    """`POST /shares {"action": "save" | "dismiss", "id"}`."""
    user, err = auth.gate(bearer)
    if not user:
        return False, err
    action, sid = body.get("action"), body.get("id")
    if action not in ("save", "dismiss") or not isinstance(sid, str):
        return False, {"status": 400, "error": "expected {action: save | dismiss, id}"}
    with _LOCK:
        d = _load()
        it = d.get(sid)
        if it is None:
            return False, {"status": 404, "error": "no such share"}
        if action == "save":
            from agent_media_core import practice_library as lib

            s = it["share"]
            try:
                if s["source"] == "youtube":
                    got = lib.add(s["url"], s["title"])
                else:
                    got = lib.add_episode(s["url"], s["title"], s.get("show") or "")
            except ValueError as e:
                return False, {"status": 400, "error": str(e)}
            it["saved_as"] = got.get("id")
        it["status"] = "saved" if action == "save" else "dismissed"
        _save(d)
    return True, {"share": it}


# ---- sending ---------------------------------------------------------------------

def _owner():
    from . import matrix

    return matrix.owner_loop()


def _direct_room(owner, to: str) -> str:
    """The owner's direct room with `to`, made when there is none."""
    from . import matrix

    cfg = owner.config
    path = f"/user/{urllib.parse.quote(owner.owner)}/account_data/m.direct"
    try:
        direct = matrix._get(cfg, path)
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
        direct = {}
    for room_id in direct.get(to) or []:
        try:
            members = matrix._get(cfg, f"/rooms/{matrix._q(room_id)}/joined_members").get("joined") or {}
        except (urllib.error.URLError, OSError, ValueError):
            continue
        if owner.owner in members:
            return room_id
    room_id = matrix._post(cfg, "/createRoom", {"preset": "trusted_private_chat", "is_direct": True,
                                                "invite": [to]})["room_id"]
    direct.setdefault(to, []).append(room_id)
    matrix._put(cfg, path, direct, cfg.token)
    return room_id


def body_of(share: dict) -> str:
    """The words any Matrix app shows for a share."""
    who = f" by {share['teacher']}" if share.get("teacher") else ""
    lines = [f"Shared a practice with you: {share['title']}{who}", share["url"]]
    if share.get("note"):
        lines.insert(0, share["note"])
    return "\n".join(lines)


def share_of(item: dict, note: str = "") -> dict:
    """The share for a practice list item; ValueError for one that cannot leave."""
    from agent_media_core import central_facts

    src = item.get("source")
    uri = item.get("_uri") or ""
    if src == "youtube":
        url = uri[3:] if uri.startswith("yt:") else uri
    elif src == "podcast" and uri.startswith(("https://", "http://")):
        url = uri
    else:
        raise ValueError("only a YouTube link or a podcast episode can be shared")
    out = {"v": 1, "kind": "practice", "source": src, "title": item.get("title") or url, "url": url}
    key = central_facts.key_for(item)
    if key:
        out["key"] = key
    for k in ("show", "teacher"):
        if item.get(k):
            out[k] = item[k]
    if item.get("duration_s"):
        out["duration_s"] = int(item["duration_s"])
    if note.strip():
        out["note"] = note.strip()[:MAX_NOTE]
    return out


def send(item_id: str, to: str, note: str = "") -> tuple[bool, dict]:
    """Send practice item `item_id` to Matrix user `to`, as the owner."""
    from agent_media_core import practice_library as lib

    from . import matrix

    to = (to or "").strip()
    if not _MXID.match(to):
        return False, {"status": 400, "error": "to must be a Matrix id, like @name:sasonica.com"}
    owner = _owner()
    if owner is None:
        return False, {"status": 409, "error": "this server is not signed in to Matrix as its owner"}
    if to == owner.owner:
        return False, {"status": 400, "error": "that is this server's own Matrix account"}
    listed = {i["id"]: i for i in lib.items()}
    item = lib.find(item_id)
    if item is None:
        return False, {"status": 404, "error": "no such practice item"}
    item = {**item, **{k: v for k, v in (listed.get(item_id) or {}).items() if k in ("teacher", "duration_s")}}
    try:
        share = share_of(item, note)
    except ValueError as e:
        return False, {"status": 400, "error": str(e)}
    try:
        room_id = _direct_room(owner, to)
        txn = f"share{int(time.time() * 1000)}"
        got = matrix._put(owner.config, f"/rooms/{matrix._q(room_id)}/send/m.room.message/{txn}",
                          {"msgtype": "m.text", "body": body_of(share), KEY: share}, owner.config.token)
    except (urllib.error.URLError, OSError, ValueError, KeyError) as e:
        return False, {"status": 502, "error": f"could not send: {e}"}
    return True, {"sent": True, "to": to, "room": room_id, "event_id": got.get("event_id"), "share": share}
