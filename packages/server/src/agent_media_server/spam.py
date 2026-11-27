"""Likely spam in group chats: folded in the app, never read out or told.

David, 28 Nov 2026, after three Telegram "venues" in a group turned out to
be Chinese ads with made-up Arctic coordinates: "let's keep the fix and add
spam filtering" — auto-flag plus marks, and the public CAS list of known
Telegram spammers.

A message from someone else is likely spam when, in order:

1. David marked its sender as spam (`POST /session/spam`); a sender marked
   "not spam" is never flagged, whatever follows. Marks are by sender, in
   every chat, kept in `<state_dir>/spam.json` (0600) as
   `{"<mxid>": {"spam": bool, "name", "set": epoch}}`.

and, in a group only (more than one person besides David, the agent and the
bridges' bots; a one-to-one chat is someone David knows or has answered):

2. `location`: a shared location whose place name carries a web address or
   an @handle, or runs past `LOCATION_NAME_MAX` — an ad in a venue's title;
3. `bot_pings`: two or more @…bot mentions and next to nothing else;
4. `cas`: a Telegram sender (`@telegram_<id>:…`) on CAS (cas.chat), asked
   once a sender with `CAS_API_KEY` (env or secrets.env; the Combot API key
   of a Telegram account; "-" turns it off) — only the numeric id leaves
   the server. Kept in
   `<state_dir>/spam-cas.json`: a listed id for good, a clear one for
   `CAS_CLEAR_TTL_S`. Asked off the sync thread; the speech intake waits up
   to `CAS_WAIT_S` for the answer, the rest read what is known;
5. `first_post`: a web address or @handle in the first message held from
   someone who joined the room less than `NEWCOMER_S` before it.

A flagged message carries `spam: {"reason", "marked"?: true}` on the log
(§6.71) and the app folds it; its sender can be marked from there. It is
not spoken (intake skip), not told as a notice, does not wake an archived
chat, is not handed to a delegated agent, and the chat list's preview line
says "Likely spam" instead of its words.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from ._jsonmap import JsonMap

log = logging.getLogger(__name__)

MARKS = JsonMap("spam.json", 0o600)
CAS = JsonMap("spam-cas.json", 0o600)

NEWCOMER_S = 3 * 86400
LOCATION_NAME_MAX = 80
CAS_CLEAR_TTL_S = 7 * 86400
CAS_WAIT_S = 3.0
CAS_URL = "https://api.cas.chat/check"

PREVIEW = "Likely spam"

_URL = re.compile(
    r"https?://|\bwww\.|\bt\.me/|\b[\w-]+\.(?:com|net|org|io|me|cc|co|xyz|top|vip|shop|site"
    r"|info|biz|ru|cn|club|live|app|link|online|store|pro)\b", re.I)
_HANDLE = re.compile(r"(?<![\w.])@[A-Za-z]\w{3,}")
_BOT = re.compile(r"(?<![\w.])@\w*bot\b", re.I)
_TG = re.compile(r"^@telegram_(\d+):")

_inflight: set[str] = set()
_done = threading.Condition()


# --- what a message says ---------------------------------------------------

def signals(content: dict) -> tuple[str | None, bool]:
    """`(reason, links)` from a message's content alone: a reason when the
    words are spam's whoever sent them (rules 2 and 3), and whether they
    carry a web address or an @handle (for rule 5)."""
    content = content if isinstance(content, dict) else {}
    body = str(content.get("body") or "")
    if content.get("msgtype") == "m.location":
        from .matrix import _location_name

        name = _location_name(body)
        if _URL.search(name) or _HANDLE.search(name) or len(name) > LOCATION_NAME_MAX:
            return "location", True
    bots = _BOT.findall(body)
    if len(bots) >= 2 and len(_BOT.sub("", body).strip()) < 20:
        return "bot_pings", True
    return None, bool(_URL.search(body) or _HANDLE.search(body))


# --- David's marks ---------------------------------------------------------

def marked(sender: str) -> bool | None:
    row = MARKS.get(sender)
    return row.get("spam") if isinstance(row, dict) and isinstance(row.get("spam"), bool) else None


def marks() -> dict[str, dict]:
    return {k: v for k, v in MARKS.all().items() if isinstance(v, dict)}


def set_mark(sender: str, spam: bool | None, name: str = "") -> bool:
    if spam is None:
        return MARKS.drop(sender)

    def fn(rows: dict) -> bool:
        rows[sender] = {"spam": spam, "name": name[:200], "set": round(time.time(), 3)}
        return True
    return MARKS.update(fn)


# --- CAS -------------------------------------------------------------------

def _cas_key() -> str:
    key = (os.environ.get("CAS_API_KEY") or "").strip()
    if key:
        return "" if key == "-" else key      # "-": off, whatever secrets.env says
    from agent_media_core.jev import secrets_path

    try:
        for line in secrets_path().read_text().splitlines():
            name, sep, value = line.strip().partition("=")
            if sep and name.strip() == "CAS_API_KEY":
                return value.strip().strip("'\"")
    except OSError:
        pass
    return ""


def telegram_id(sender: str) -> str:
    m = _TG.match(sender or "")
    return m[1] if m else ""


def _cas_known(tg: str, now: float) -> bool | None:
    row = CAS.get(tg)
    if not isinstance(row, dict) or not isinstance(row.get("listed"), bool):
        return None
    if row["listed"] or now - float(row.get("at") or 0) < CAS_CLEAR_TTL_S:
        return row["listed"]
    return None


def _cas_ask(tg: str, key: str) -> None:
    try:
        req = urllib.request.Request(f"{CAS_URL}?{urllib.parse.urlencode({'user_id': tg})}",
                                     headers={"Authorization": f"Bearer {key}",
                                              "User-Agent": "agent-media"})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            data = json.loads(e.read() or b"{}")
        if data.get("ok") is True:
            listed = True
        elif data.get("error_code") in (None, 404) or "not found" in str(data.get("description", "")).lower():
            listed = False
        else:
            log.warning("spam: CAS refused (%s)", data.get("description") or data.get("error_code"))
            return
        def put(rows: dict) -> bool:
            rows[tg] = {"listed": listed, "at": round(time.time(), 3)}
            return True
        CAS.update(put)
        if listed:
            log.info("spam: telegram %s is on CAS", tg)
    except (OSError, ValueError) as e:
        log.warning("spam: CAS unreachable: %s", e)
    finally:
        with _done:
            _inflight.discard(tg)
            _done.notify_all()


def cas_listed(sender: str, wait: float = 0.0) -> bool | None:
    """Whether a Telegram sender is on CAS: True, False, or None (not a
    Telegram sender, no key, or not known yet — asked in the background)."""
    tg = telegram_id(sender)
    if not tg:
        return None
    now = time.time()
    known = _cas_known(tg, now)
    if known is not None:
        return known
    key = _cas_key()
    if not key:
        return None
    with _done:
        if tg not in _inflight:
            _inflight.add(tg)
            threading.Thread(target=_cas_ask, args=(tg, key), name="spam-cas",
                             daemon=True).start()
        if wait > 0:
            _done.wait_for(lambda: tg not in _inflight, timeout=wait)
    return _cas_known(tg, time.time())


# --- the verdict -----------------------------------------------------------

def _others(r) -> set[str]:
    from .matrix import _bridges

    me = {r.owner, os.environ.get("MATRIX_SAM_ID") or ""} | _bridges()
    return {u for u in r.members if u not in me}


def is_group(r) -> bool:
    return len(_others(r)) > 1


def verdict(r, sender: str, sig: tuple[str | None, bool], at: float,
            first_at: float | None = None, cas_wait: float = 0.0) -> dict | None:
    """`{"reason", "marked"?}` when a message from `sender` in room `r` is
    likely spam, else None. `first_at`: when the first message held from
    the sender was (None: none before this one)."""
    from .matrix import _bridges

    if not sender or sender == r.owner or sender == (os.environ.get("MATRIX_SAM_ID") or "") \
            or sender in _bridges():
        return None
    mk = marked(sender)
    if mk is not None:
        return {"reason": "marked", "marked": True} if mk else None
    if not is_group(r):
        return None
    reason, links = sig
    if reason:
        return {"reason": reason}
    if cas_listed(sender, cas_wait):
        return {"reason": "cas"}
    joined = r.joined_at.get(sender)
    if links and joined is not None and at - joined < NEWCOMER_S \
            and (first_at is None or first_at >= at):
        return {"reason": "first_post"}
    return None


def of_event(r, ev: dict, cas_wait: float = 0.0) -> dict | None:
    """The verdict on a sync event as it comes (notices, speech, archive
    wake): a message only, never raises."""
    try:
        if ev.get("type") not in ("m.room.message", "m.sticker"):
            return None
        sender = str(ev.get("sender") or "")
        at = (ev.get("origin_server_ts") or 0) / 1000
        with r.lock:
            first = min((m["at"] for m in r.messages
                         if m["sender"]["id"] == sender and m["id"] != ev.get("event_id")),
                        default=None)
        return verdict(r, sender, signals(ev.get("content") or {}), at, first, cas_wait)
    except Exception as e:  # noqa: BLE001 — a missed flag, not a dead sync
        from agent_media_core import failures

        failures.swallowed(e)
        return None


# --- the route -------------------------------------------------------------

def session_spam(session: str, body: dict, bearer: str) -> tuple[bool, dict]:
    """`POST /session/spam {"session", "sender", "spam": true | false | null}`:
    mark a chat's member as spam, as not spam, or neither (back to the
    rules). 400 for a bad id or value, 404 for a chat or member unknown."""
    from . import auth, matrix, sessions

    session = (session or "").strip()
    if not sessions._SESSION.fullmatch(session):
        return False, {"error": "not a session id", "status": 400}
    sender = str(body.get("sender") or "").strip()
    spam = body.get("spam")
    if "spam" not in body or not (spam is None or isinstance(spam, bool)):
        return False, {"error": "spam must be true, false or null", "status": 400}
    user, err = auth.gate(bearer)
    if not user:
        return False, err
    r = matrix.room(session)
    if r is None:
        return False, {"error": "not a chat with people", "status": 404}
    with r.lock:
        known = sender in r.members or any(m["sender"]["id"] == sender for m in r.messages)
        name = r.members.get(sender) or sender
    if not sender or not known or sender == r.owner:
        return False, {"error": "no such person in this chat", "status": 404}
    try:
        set_mark(sender, spam, name)
    except OSError as e:
        return False, {"error": f"could not save the mark ({e})", "status": 500}
    with r.lock:
        r.rev += 1
    return True, {"session": session, "sender": sender, "spam": marked(sender)}
