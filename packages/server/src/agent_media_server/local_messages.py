"""Full copy: Local chats' messages, sent by the phone, read here
(server-contract.md §6.61 "Full copy").

Step 5 of docs/proposals/2026-11-17-phone-bridges-server-integration.md
(David, 18 Nov 2026: "Full copy"). With the dial "What leaves the phone" at
`full` (local_settings.py), Sasonica posts, on the companion's behalf, the
messages of the Local chats its phone bridges; agents, search and the desk
can then read those chats like any thread. Media bytes stay on the phone:
a photo is its name, type and size ("photo — on the phone").

**Why a store of its own.** A Matrix room's messages live on its homeserver
and matrix.py holds pages of them in memory; the search index (search.py)
is a cache it rebuilds from its sources whenever its schema moves. A Local
chat's words have no other source on this server, so they are kept here,
in `<state_dir>/local-messages.db` (SQLite, 0600), and fed into the same
search index the rooms are (search.py `_index_local`), by the same
`docs`/`threads` rows and the same `rooms` signature table. The thread
routes read them through `envelope` in the §6.2.2 message shape a room's
log answers, so the app has nothing new to draw.

**Rows.** One per event the phone sent, by `(chat, event id)`: a repeat is
ignored (the phone may send a batch again). Messages (`text`, `image`,
`video`, `audio`, `file`, `notice`) show; `reaction`, `edit` and
`redaction` rows are kept beside them and applied: the newest edit's words
replace a message's (an edit arriving first waits for its message), a
redaction blanks what it names — its words, its media, the edits of it —
and a redacted reaction is gone. `changed` is a counter over the whole
store, bumped by every row written or changed, which is what the search
index reads from (`changed_since`).

**Senders' pictures** (22 Nov 2026, sasonica-app mail #1189). The phone
sends each sender's profile picture once per version (`POST /local/avatar`),
kept beside the messages in the `avatars` table as a 256 px thumbnail and
answered as `sender.avatar` in the chat's log, as a room member's picture
is. Its `etag` is the hash of the bytes the phone sent, so the phone can
tell an unchanged picture without sending it (`GET /local/avatars`). Only
the picture: the sender's name already rides with each message, and the
rest of the companion's card (phone number, service, is_bot) stays on the
phone. They go with the messages (`_delete`).

**When it goes.** Switching the dial off `full` deletes every message here
and its rows in the search index (`purge`, from local_settings.post); a
chat its phone has not listed for `GONE_DAYS` (30), or no longer known at
all, is deleted the next time the store is written (`prune`).
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
from pathlib import Path

from . import auth, local_chats
from agent_media_core import failures as _failures

#: Messages one `POST /local/messages` may carry.
MAX_BATCH = 500
#: Characters of a message's text kept.
MAX_TEXT = 32768
#: The largest body: MAX_BATCH messages of MAX_TEXT, with room for JSON escapes.
MAX_BODY = 24 * 1024 * 1024
#: A chat gone from its phone's list this long loses its messages here.
GONE_DAYS = 30

SHOWN = ("text", "image", "video", "audio", "file", "notice")
KINDS = SHOWN + ("reaction", "edit", "redaction")
MEDIA_KINDS = ("image", "video", "audio", "file")
#: What a message carries, and nothing else.
FIELDS = frozenset({"event_id", "ts", "from", "sender_id", "text", "kind", "reply_to",
                    "edits", "redacts", "media"})
MEDIA_FIELDS = frozenset({"name", "mime", "size"})
BODY_FIELDS = frozenset({"chat", "messages"})
#: The word for a medium, in "photo — on the phone".
MEDIA_WORD = {"image": "photo", "video": "video", "audio": "audio clip", "file": "file"}
ON_PHONE = "on the phone"
DELETED = "_message deleted_"

_EVENT = re.compile(r"[^\x00-\x1f\x7f]{1,255}")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v INTEGER);
CREATE TABLE IF NOT EXISTS msgs (
  chat TEXT NOT NULL, id TEXT NOT NULL, at REAL NOT NULL, seq INTEGER NOT NULL,
  kind TEXT NOT NULL, mine INTEGER NOT NULL, sender TEXT, sender_id TEXT,
  text TEXT, reply_to TEXT, target TEXT, media TEXT,
  edited INTEGER NOT NULL DEFAULT 0, redacted INTEGER NOT NULL DEFAULT 0,
  changed INTEGER NOT NULL, received REAL NOT NULL,
  PRIMARY KEY (chat, id));
CREATE INDEX IF NOT EXISTS msgs_order ON msgs(chat, at, seq);
CREATE INDEX IF NOT EXISTS msgs_target ON msgs(chat, target);
CREATE INDEX IF NOT EXISTS msgs_changed ON msgs(chat, changed);
CREATE TABLE IF NOT EXISTS avatars (
  chat TEXT NOT NULL, sender TEXT NOT NULL, etag TEXT NOT NULL, type TEXT NOT NULL,
  data BLOB NOT NULL, at REAL NOT NULL, PRIMARY KEY (chat, sender));
"""

_LOCAL = threading.local()
_WRITE = threading.Lock()


def db_path() -> Path:
    from agent_media_core._paths import state_dir

    return state_dir() / "local-messages.db"


def _connect() -> sqlite3.Connection:
    """This thread's connection (made again when the state dir moves: tests)."""
    path = db_path()
    conn = getattr(_LOCAL, "conn", None)
    if conn is not None and getattr(_LOCAL, "path", None) == str(path):
        return conn
    path.parent.mkdir(parents=True, exist_ok=True)
    old = os.umask(0o077)
    try:
        conn = sqlite3.connect(str(path), timeout=10.0, isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.executescript(_SCHEMA)
    finally:
        os.umask(old)
    for p in (path, Path(str(path) + "-wal"), Path(str(path) + "-shm")):
        try:
            os.chmod(p, 0o600)
        except OSError:
            pass
    _LOCAL.conn, _LOCAL.path = conn, str(path)
    return conn


def _exists() -> bool:
    return db_path().exists()


def _tick(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT v FROM meta WHERE k='clock'").fetchone()
    n = (row[0] if row else 0) + 1
    conn.execute("INSERT OR REPLACE INTO meta VALUES('clock', ?)", (n,))
    return n


# --- the dial -------------------------------------------------------------------


def enabled() -> bool:
    """Whether the dial is at `full` and `full` may be chosen here."""
    from . import local_settings

    return local_settings.view()["leaves_phone"] == "full" and local_settings.full_available()


NOT_ENABLED = {"code": "not_enabled", "status": 409,
               "error": "What leaves the phone is not at Full copy: messages stay on the phone"}


# --- what is posted -------------------------------------------------------------


def _bad(error: str, **extra) -> tuple[bool, dict]:
    return False, {"error": error, "status": 400, **extra}


def _clean_text(raw) -> str:
    s = str(raw).replace("\x00", "")
    return s[:MAX_TEXT]


def _ref(m: dict, key: str, i: int):
    v = m.get(key)
    if v is None:
        return None, None
    if not isinstance(v, str) or not _EVENT.fullmatch(v):
        return None, f"messages[{i}].{key} is an event id"
    return v, None


def _check(m, i: int, now: float) -> tuple[dict | None, tuple | None]:
    """One posted message as a row, or the refusal."""
    if not isinstance(m, dict):
        return None, _bad(f"messages[{i}] must be an object")
    extra = sorted(set(m) - FIELDS)
    if extra:
        return None, _bad(f"not a field of a message: {extra[0]}", code="field_not_accepted",
                          field=extra[0])
    eid = m.get("event_id")
    if not isinstance(eid, str) or not _EVENT.fullmatch(eid):
        return None, _bad(f"messages[{i}].event_id is the event's id: 1-255 characters")
    kind = m.get("kind", "text")
    if kind not in KINDS:
        return None, _bad(f"messages[{i}].kind is one of {', '.join(KINDS)}")
    ts = m.get("ts")
    if isinstance(ts, bool) or not isinstance(ts, (int, float)) or ts <= 0:
        return None, _bad(f"messages[{i}].ts is a time in epoch seconds")
    if ts > 1e11:                      # milliseconds, as most bridges keep them
        ts = ts / 1000.0
    if ts > now + 86400:
        return None, _bad(f"messages[{i}].ts is in the future")
    who = m.get("from")
    if not isinstance(who, str) or not who.strip():
        return None, _bad(f'messages[{i}].from is "me" or the sender\'s name')
    sid = m.get("sender_id")
    if sid is not None and (not isinstance(sid, str) or len(sid) > 255):
        return None, _bad(f"messages[{i}].sender_id is text")
    text = m.get("text")
    if text is not None and not isinstance(text, str):
        return None, _bad(f"messages[{i}].text is text")
    reply_to, err = _ref(m, "reply_to", i)
    edits, err2 = _ref(m, "edits", i)
    redacts, err3 = _ref(m, "redacts", i)
    for e in (err, err2, err3):
        if e:
            return None, _bad(e)
    target = None
    if kind == "edit":
        if not edits:
            return None, _bad(f"messages[{i}]: an edit names the message it edits in edits")
        target = edits
    elif kind == "redaction":
        if not redacts:
            return None, _bad(f"messages[{i}]: a redaction names what it takes back in redacts")
        target = redacts
    elif kind == "reaction":
        if not reply_to or not (text or "").strip():
            return None, _bad(f"messages[{i}]: a reaction is its emoji in text, "
                              "on the message named in reply_to")
        target, reply_to = reply_to, None
    media = m.get("media")
    if media is not None:
        if kind not in MEDIA_KINDS or not isinstance(media, dict):
            return None, _bad(f"messages[{i}].media is {{name, mime, size}} on a photo, video, "
                              "audio or file")
        extra = sorted(set(media) - MEDIA_FIELDS)
        if extra:
            return None, _bad(f"not a field of media: {extra[0]}", code="field_not_accepted",
                              field=f"media.{extra[0]}")
        size = media.get("size")
        if size is not None and (isinstance(size, bool) or not isinstance(size, int) or size < 0):
            return None, _bad(f"messages[{i}].media.size is bytes")
        for k in ("name", "mime"):
            if media.get(k) is not None and not isinstance(media[k], str):
                return None, _bad(f"messages[{i}].media.{k} is text")
        media = {"name": _clean_text(media.get("name") or "")[:200],
                 "mime": _clean_text(media.get("mime") or "")[:100], "size": size}
    mine = who.strip() == "me"
    return {"id": eid, "at": round(float(ts), 3), "kind": kind, "mine": int(mine),
            "sender": None if mine else _clean_text(who.strip())[:200],
            "sender_id": sid, "text": _clean_text(text) if text is not None else None,
            "reply_to": reply_to, "target": target,
            "media": json.dumps(media) if media else None}, None


# --- applying edits and redactions -------------------------------------------------


def _settle(conn: sqlite3.Connection, chat: str, mid: str, clock: int) -> None:
    """Bring message `mid` in line with the edits and redactions held for
    it: a redaction blanks it and its edits; else the newest edit's words."""
    row = conn.execute("SELECT kind, text, edited, redacted FROM msgs WHERE chat=? AND id=?",
                       (chat, mid)).fetchone()
    if row is None:
        return
    kind, text, edited, redacted = row
    gone = conn.execute("SELECT 1 FROM msgs WHERE chat=? AND target=? AND kind='redaction'",
                        (chat, mid)).fetchone()
    if gone:
        conn.execute("UPDATE msgs SET text=NULL, media=NULL, redacted=1, changed=? "
                     "WHERE chat=? AND id=? AND (redacted=0 OR text IS NOT NULL "
                     "OR media IS NOT NULL)", (clock, chat, mid))
        conn.execute("UPDATE msgs SET text=NULL, redacted=1, changed=? WHERE chat=? AND "
                     "target=? AND kind='edit' AND (redacted=0 OR text IS NOT NULL)",
                     (clock, chat, mid))
        return
    if kind not in SHOWN or redacted:
        return
    edit = conn.execute("SELECT text FROM msgs WHERE chat=? AND target=? AND kind='edit' "
                        "AND redacted=0 ORDER BY at DESC, seq DESC LIMIT 1",
                        (chat, mid)).fetchone()
    if edit is not None and (edit[0] != text or not edited):
        conn.execute("UPDATE msgs SET text=?, edited=1, changed=? WHERE chat=? AND id=?",
                     (edit[0], clock, chat, mid))


# --- POST /local/messages --------------------------------------------------------


def post(body, bearer: str) -> tuple[bool, dict]:
    """`POST /local/messages {"chat": "local:…", "messages": [...]}` from the
    device that bridges the chat. Answers `{chat, stored, duplicates, held}`."""
    user, err = auth.gate(bearer)
    if not user:
        return False, err
    if auth.is_guest(bearer):
        return False, {"code": "guest", "status": 403,
                       "error": "a guest does not see the phone's chats"}
    device = auth.device_id(bearer)
    if not device:
        return False, {"error": "only a paired device bridges chats", "status": 403}
    if not enabled():
        return False, dict(NOT_ENABLED)
    if not isinstance(body, dict):
        return _bad("the body must be an object")
    extra = sorted(set(body) - BODY_FIELDS)
    if extra:
        return _bad(f"not a field of a message batch: {extra[0]}", code="field_not_accepted",
                    field=extra[0])
    chat = body.get("chat")
    if not isinstance(chat, str) or not local_chats.is_local(chat):
        return _bad("chat is a Local chat's id, local:<network>:<id>")
    msgs = body.get("messages")
    if not isinstance(msgs, list):
        return _bad("messages must be a list")
    if len(msgs) > MAX_BATCH:
        return _bad(f"at most {MAX_BATCH} messages at once", code="too_many", limit=MAX_BATCH)
    rec = local_chats.record(chat)
    if rec is None:
        return False, {"error": "no such chat: a phone has not listed it", "status": 404,
                       "code": "not_found"}
    if rec.get("device") != device:
        return False, {"error": "only the device that bridges this chat sends its messages",
                       "status": 403, "code": "not_bridging"}
    now = time.time()
    rows = []
    for i, m in enumerate(msgs):
        row, refusal = _check(m, i, now)
        if refusal:
            return refusal
        rows.append(row)
    stored = dup = 0
    with _WRITE:
        conn = _connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            clock = _tick(conn)
            touched: set[str] = set()
            for r in rows:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO msgs(chat, id, at, seq, kind, mine, sender, sender_id, "
                    "text, reply_to, target, media, changed, received) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (chat, r["id"], r["at"], clock * 1000 + stored, r["kind"], r["mine"],
                     r["sender"], r["sender_id"], r["text"], r["reply_to"], r["target"],
                     r["media"], clock, now))
                if not cur.rowcount:
                    dup += 1
                    continue
                stored += 1
                touched.add(r["target"] if r["kind"] in ("edit", "redaction") else r["id"])
            for mid in touched:
                if mid:
                    _settle(conn, chat, mid, clock)
            held = conn.execute(f"SELECT count(*) FROM msgs WHERE chat=? AND kind IN "
                                f"({','.join('?' * len(SHOWN))})", (chat, *SHOWN)).fetchone()[0]
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    prune()
    return True, {"chat": chat, "stored": stored, "duplicates": dup, "held": held}


# --- reading ---------------------------------------------------------------------


def _media_parts(kind: str, media: dict | None, text: str | None) -> list[dict]:
    media = media or {}
    word = MEDIA_WORD[kind]
    part = {"type": "media", "kind": kind, "name": media.get("name") or "",
            "text": f"{word} — {ON_PHONE}", "on_phone": True}
    if media.get("mime"):
        part["mimetype"] = media["mime"]
    if media.get("size") is not None:
        part["size"] = media["size"]
    out = [part]
    if text and text.strip():
        out.append({"type": "text", "text": text})
    return out


_COLS = ("id", "at", "seq", "kind", "mine", "sender", "sender_id", "text", "reply_to",
         "media", "edited", "redacted")


def _rows(conn, chat: str, ids: list[str]) -> list[dict]:
    if not ids:
        return []
    out = []
    for i in range(0, len(ids), 500):
        part = ids[i:i + 500]
        out += [dict(zip(_COLS, r)) for r in conn.execute(
            f"SELECT {', '.join(_COLS)} FROM msgs WHERE chat=? AND id IN "
            f"({','.join('?' * len(part))})", (chat, *part))]
    out.sort(key=lambda r: (r["at"], r["seq"]))
    return out


def _message(r: dict) -> dict:
    kind = r["kind"]
    if r["redacted"]:
        parts = [{"type": "text", "text": DELETED}]
    elif kind in MEDIA_KINDS:
        parts = _media_parts(kind, json.loads(r["media"]) if r["media"] else None, r["text"])
    elif kind == "notice":
        parts = [{"type": "text", "text": f"_{(r['text'] or '').strip()}_"}]
    else:
        parts = [{"type": "text", "text": r["text"] or ""}]
    mine = bool(r["mine"])
    name = "You" if mine else (r["sender"] or "")
    m = {"id": r["id"], "role": "user" if mine else "assistant", "at": r["at"],
         "parts": parts, "spoken": None, "turn": {"running": False},
         "sender": {"id": r["sender_id"] or ("me" if mine else name), "name": name},
         "local": True}
    if not mine:
        m["peer"] = {"name": name}
    if r["edited"] and not r["redacted"]:
        m["edited"] = True
    if r["redacted"]:
        m["redacted"] = True
    if kind == "notice":
        m["notice"] = True
    if r["reply_to"]:
        m["reply_to"] = {"id": r["reply_to"]}
    return m


def _view(conn, chat: str, rows: list[dict]) -> list[dict]:
    from . import replies

    msgs = [_message(r) for r in rows]
    pics = _avatar_tags(conn, chat)
    if pics:
        for r, m in zip(rows, msgs):
            tag = None if r["mine"] or not r["sender_id"] else pics.get(r["sender_id"])
            if tag:
                m["sender"]["avatar"] = {"url": avatar_url(chat, r["sender_id"]), "etag": tag}
    ids = [m["id"] for m in msgs]
    reacts: dict[str, dict[str, list]] = {}
    for i in range(0, len(ids), 500):
        part = ids[i:i + 500]
        for target, key, mine, sender in conn.execute(
                "SELECT target, text, mine, sender FROM msgs WHERE chat=? AND kind='reaction' "
                f"AND redacted=0 AND target IN ({','.join('?' * len(part))}) ORDER BY at, seq",
                (chat, *part)):
            who = "You" if mine else (sender or "")
            by = reacts.setdefault(target, {}).setdefault(key, [])
            if who not in by:
                by.append(who)
    want = [m["reply_to"]["id"] for m in msgs if m.get("reply_to")]
    quoted = {r["id"]: r for r in _rows(conn, chat, want)}
    for m in msgs:
        rs = reacts.get(m["id"])
        if rs:
            m["reactions"] = [{"key": k, "count": len(by), "mine": "You" in by, "by": by}
                              for k, by in rs.items()]
        if m.get("reply_to"):
            q = quoted.get(m["reply_to"]["id"])
            if q is not None:
                words = " ".join(p.get("text") or "" for p in _message(q)["parts"]).strip("_ ")
                m["reply_to"].update(text=replies.short(words),
                                     name="You" if q["mine"] else (q["sender"] or ""))
                if q["mine"]:
                    m["reply_to"]["mine"] = True
    return msgs


def _order(conn, chat: str) -> list[str]:
    return [r[0] for r in conn.execute(
        f"SELECT id FROM msgs WHERE chat=? AND kind IN ({','.join('?' * len(SHOWN))}) "
        "ORDER BY at, seq", (chat, *SHOWN))]


def held(chat: str) -> int:
    if not _exists():
        return 0
    return _connect().execute(
        f"SELECT count(*) FROM msgs WHERE chat=? AND kind IN ({','.join('?' * len(SHOWN))})",
        (chat, *SHOWN)).fetchone()[0]


def envelope(chat: str, *, limit: int, before: str = "", around: str = "",
             most: int = 500) -> dict:
    """The `/conversation/log?session=local:…` answer: the chat's messages
    held here (none unless the dial is at `full`), and nothing a session has."""
    from . import transcript

    rec = local_chats.record(chat) or {}
    net = str(rec.get("network") or "")
    out = {"session": chat, "lines": [], "messages": [], "older": False,
           "pending": False, "working": None, "approval": None, "suggestion": "",
           "suggestions": [], "recap": None, "context": None,
           "local": {"network": {"id": net, "name": local_chats.NETWORKS.get(net, net)},
                     "device": rec.get("device"), "full": enabled(), "held": 0}}
    if not _exists():
        if around:
            out["around"], out["newer"] = {"id": around, "found": False, "newer": False}, False
        return out
    conn = _connect()
    order = _order(conn, chat)
    out["local"]["held"] = len(order)
    jump = None
    win = transcript.window_around([{"id": i} for i in order], around, limit, most) \
        if around else None
    if win is not None:
        picked, older, newer = win
        ids = [m["id"] for m in picked]
        jump = {"id": around, "found": True, "newer": newer}
    else:
        end = len(order)
        if before:
            end = order.index(before) if before in order else 0
        start = max(0, end - limit)
        ids, older = order[start:end], start > 0
        if around:
            jump = {"id": around, "found": False, "newer": False}
    out["messages"] = _view(conn, chat, _rows(conn, chat, ids))
    out["older"] = older
    if jump is not None:
        out["around"], out["newer"] = jump, jump["newer"]
    return out


def may_read(bearer: str) -> tuple[bool, dict]:
    """A Local chat's words, to whom: any paired device but a guest or one
    limited to its folders (a Local chat is in no folder)."""
    user, err = auth.gate(bearer)
    if not user:
        return False, err
    if auth.is_guest(bearer) or _scoped(bearer):
        return False, {"code": "guest", "status": 403,
                       "error": "this device does not see the phone's chats"}
    return True, {}


def _scoped(bearer: str) -> bool:
    from . import folder_grants

    try:
        return folder_grants.scoped(auth._device(bearer))
    except Exception as _exc:  # noqa: BLE001 — no grants file: nobody is scoped
        _failures.swallowed(_exc)
        return False


def hidden_from(bearer: str) -> bool:
    """Whether Local chats are kept from this caller in lists and search."""
    if not bearer:
        return False
    return auth.is_guest(bearer) or _scoped(bearer)


# --- the search index's feed ------------------------------------------------------


def chats_clock() -> dict[str, int]:
    """`{chat: the newest change}` for every chat with rows here."""
    if not _exists():
        return {}
    return {c: n for c, n in _connect().execute(
        "SELECT chat, max(changed) FROM msgs GROUP BY chat")}


def changed_since(chat: str, after: int) -> list[tuple[str, str, float, str, bool]]:
    """`(id, role, at, words, shown)` of every message of `chat` changed
    after `after`: `shown` False for one taken back (and rows that are not
    messages are left out)."""
    out = []
    for mid, at, kind, mine, text, media, redacted in _connect().execute(
            "SELECT id, at, kind, mine, text, media, redacted FROM msgs WHERE chat=? AND "
            f"changed>? AND kind IN ({','.join('?' * len(SHOWN))})", (chat, after, *SHOWN)):
        words = text or ""
        if kind in MEDIA_KINDS and media:
            name = (json.loads(media) or {}).get("name") or ""
            words = "\n".join(s for s in (MEDIA_WORD[kind], name, words) if s)
        out.append((mid, "user" if mine else "assistant", at, words, not redacted))
    return out


def span(chat: str) -> tuple[float | None, float | None]:
    row = _connect().execute(
        f"SELECT min(at), max(at) FROM msgs WHERE chat=? AND kind IN ({','.join('?' * len(SHOWN))})",
        (chat, *SHOWN)).fetchone()
    return (row[0], row[1]) if row else (None, None)


# --- deleting ----------------------------------------------------------------------


def _delete(chats: list[str] | None) -> dict:
    """Delete the messages of `chats` (every chat with None), here and in the
    search index. `{"messages": n, "chats": n}` — messages counted as shown."""
    if not _exists():
        return {"messages": 0, "chats": 0}
    with _WRITE:
        conn = _connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            where, args = ("", ()) if chats is None else (
                f" AND chat IN ({','.join('?' * len(chats))})", tuple(chats))
            n = conn.execute(f"SELECT count(*) FROM msgs WHERE kind IN "
                             f"({','.join('?' * len(SHOWN))}){where}", (*SHOWN, *args)).fetchone()[0]
            gone = [r[0] for r in conn.execute(f"SELECT DISTINCT chat FROM msgs WHERE 1=1{where}",
                                               args)]
            gone += [r[0] for r in conn.execute(
                f"SELECT DISTINCT chat FROM avatars WHERE 1=1{where}", args) if r[0] not in gone]
            conn.execute(f"DELETE FROM msgs WHERE 1=1{where}", args)
            conn.execute(f"DELETE FROM avatars WHERE 1=1{where}", args)
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        if chats is None:
            conn.execute("VACUUM")
    from . import search

    for c in gone:
        try:
            search.forget(c)
        except Exception as _exc:  # noqa: BLE001 — the index is rebuilt from here anyway
            _failures.swallowed(_exc)
    return {"messages": int(n), "chats": len(gone)}


def purge() -> dict:
    """Every Local message gone: the dial left `full`."""
    return _delete(None)


def prune(now: float | None = None) -> dict:
    """Delete the messages of chats gone from their phone's list for
    `GONE_DAYS`, or no longer known at all."""
    if not _exists():
        return {"messages": 0, "chats": 0}
    now = time.time() if now is None else now
    stale = []
    for (chat,) in _connect().execute("SELECT chat FROM msgs UNION SELECT chat FROM avatars"):
        rec = local_chats.record(chat)
        if rec is None or (rec.get("gone") and now - float(rec["gone"]) > GONE_DAYS * 86400):
            stale.append(chat)
    return _delete(stale) if stale else {"messages": 0, "chats": 0}


# --- senders' pictures -------------------------------------------------------------

#: The largest picture the phone may send; kept as a thumbnail of AVATAR_PX.
MAX_AVATAR_BYTES = 2 * 1024 * 1024
AVATAR_PX = 256


def avatar_etag(data: bytes) -> str:
    """A picture's version: the first 16 hex of the sha256 of the bytes as sent."""
    import hashlib

    return hashlib.sha256(data).hexdigest()[:16]


def avatar_url(chat: str, sender: str) -> str:
    from urllib.parse import quote

    return f"/local/avatar?chat={quote(chat, safe='')}&sender={quote(sender, safe='')}"


def _avatar_tags(conn, chat: str) -> dict[str, str]:
    return {s: t for s, t in conn.execute("SELECT sender, etag FROM avatars WHERE chat=?",
                                          (chat,))}


def _bridging(chat, sender, bearer: str) -> tuple[bool, dict]:
    """The checks a picture's sending or taking off shares with a message batch."""
    user, err = auth.gate(bearer)
    if not user:
        return False, err
    if auth.is_guest(bearer):
        return False, {"code": "guest", "status": 403,
                       "error": "a guest does not see the phone's chats"}
    device = auth.device_id(bearer)
    if not device:
        return False, {"error": "only a paired device bridges chats", "status": 403}
    if not enabled():
        return False, dict(NOT_ENABLED)
    if not isinstance(chat, str) or not local_chats.is_local(chat):
        return _bad("chat is a Local chat's id, local:<network>:<id>")
    if not isinstance(sender, str) or not _EVENT.fullmatch(sender):
        return _bad("sender is the sender_id its messages carry: 1-255 characters")
    rec = local_chats.record(chat)
    if rec is None:
        return False, {"error": "no such chat: a phone has not listed it", "status": 404,
                       "code": "not_found"}
    if rec.get("device") != device:
        return False, {"error": "only the device that bridges this chat sends its pictures",
                       "status": 403, "code": "not_bridging"}
    return True, {}


def put_avatar(chat, sender, data: bytes, bearer: str,
               now: float | None = None) -> tuple[bool, dict]:
    """`POST /local/avatar?chat=&sender=`, the picture's bytes: kept as a
    thumbnail, replacing the one before. `{chat, sender, etag, changed}`."""
    from . import contacts_photos

    ok, err = _bridging(chat, sender, bearer)
    if not ok:
        return ok, err
    if len(data) > MAX_AVATAR_BYTES:
        return False, {"error": f"a picture is at most {MAX_AVATAR_BYTES >> 20} MB",
                       "status": 413, "code": "too_big", "limit": MAX_AVATAR_BYTES}
    got = contacts_photos.thumbnail(data, AVATAR_PX, MAX_AVATAR_BYTES) if data else None
    if got is None:
        return _bad("the body is the picture: JPEG, PNG, GIF or WebP", code="not_an_image")
    thumb, ctype = got
    tag = avatar_etag(data)
    now = time.time() if now is None else now
    with _WRITE:
        conn = _connect()
        row = conn.execute("SELECT etag FROM avatars WHERE chat=? AND sender=?",
                           (chat, sender)).fetchone()
        changed = row is None or row[0] != tag
        if changed:
            conn.execute("INSERT OR REPLACE INTO avatars VALUES(?,?,?,?,?,?)",
                         (chat, sender, tag, ctype, thumb, now))
    return True, {"chat": chat, "sender": sender, "etag": tag, "changed": changed}


def delete_avatar(chat, sender, bearer: str) -> tuple[bool, dict]:
    """`DELETE /local/avatar?chat=&sender=`: the sender took their picture off."""
    ok, err = _bridging(chat, sender, bearer)
    if not ok:
        return ok, err
    if not _exists():
        return True, {"chat": chat, "sender": sender, "deleted": False}
    with _WRITE:
        n = _connect().execute("DELETE FROM avatars WHERE chat=? AND sender=?",
                               (chat, sender)).rowcount
    return True, {"chat": chat, "sender": sender, "deleted": bool(n)}


def avatars(chat, bearer: str) -> tuple[bool, dict]:
    """`GET /local/avatars?chat=`: `{sender id: etag}` of the pictures held,
    so the phone sends only a new or changed one."""
    ok, err = may_read(bearer)
    if not ok:
        return ok, err
    if not isinstance(chat, str) or not local_chats.is_local(chat):
        return _bad("chat is a Local chat's id, local:<network>:<id>")
    if local_chats.record(chat) is None:
        return False, {"error": "no such chat: a phone has not listed it", "status": 404,
                       "code": "not_found"}
    tags = _avatar_tags(_connect(), chat) if _exists() else {}
    return True, {"chat": chat, "avatars": tags}


def avatar(chat: str, sender: str, bearer: str) -> tuple[bool, dict]:
    """`GET /local/avatar?chat=&sender=`: `{data, type, etag}`, or the refusal
    (404 when none is held)."""
    ok, err = may_read(bearer)
    if not ok:
        return ok, err
    row = _connect().execute("SELECT data, type, etag FROM avatars WHERE chat=? AND sender=?",
                             (chat, sender)).fetchone() if _exists() else None
    if row is None:
        return False, {"error": "no picture", "status": 404, "code": "not_found"}
    return True, {"data": bytes(row[0]), "type": row[1], "etag": row[2]}


def _reset_for_tests() -> None:
    conn = getattr(_LOCAL, "conn", None)
    if conn is not None:
        conn.close()
    _LOCAL.conn = _LOCAL.path = None
