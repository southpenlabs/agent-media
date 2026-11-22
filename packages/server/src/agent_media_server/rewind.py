"""`POST /session/rewind` — take a chat back to one of its replies
(server-contract.md §6.64; docs/proposals/2026-11-23-rewind-a-chat.md).

David, 23 Nov 2026: "how do we give you permission to rewind a chat", then
"And other permission so you can rewind too", and for that permission he
picked "Ask each time".

A rewind is a Branch with its history (§6.34) that stays in the thread. The
chat goes back to a reply: it remembers everything up to and including that
reply and nothing after it. The link, title and project stay as they were.
Claude Code does the cut itself: `claude --resume <id> --resume-session-at
<record>` keeps the same id and writes the next turn as a new branch off
that record. The turns after it stay in the file as a dead branch, which the
thread shows folded (`rewound` on each message; transcript.py `Builder`).
Files on disk are not touched: a rewind changes what the chat remembers, not
what it did.

Who can rewind:

* **A person** with a paired device (the app's hold on a reply, then Rewind
  to here). Carried out at once.
* **The agent**, from inside the chat with the host's own token (`media
  session rewind`). That is only a request. It is kept here and shown in
  the thread (`rewind` on the thread stream), and nothing happens until a
  person approves it (`POST /session/rewind/answer`). An agent cannot
  approve its own request, because the answer needs a paired device.

Either way the cut waits for a running turn to end (sessiond `rewind`):
nothing the chat is doing is interrupted. The next message to the chat opens
with a note saying it was rewound, and when an agent asked for it, the
agent's reason too. A declined request is told the same way.

Kept in `<state_dir>/rewinds.json` as `{"<session>": {"id", "to", "record",
"n", "why", "by", "state", "asked_at", "answered_at"?, "note"?}}`, one row
per chat (a newer request replaces an older one). `state` is `asked`,
`waiting` (approved, the turn not over yet), `done`, `declined` or `failed`.
An unanswered request lapses after `ASK_TTL_S`. Headless Claude Code chats
only: a pane's chat would have to be restarted in its pane (moves.py does
that) and is refused `not_headless` for now.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import time

from . import auth, sessions
from ._jsonmap import JsonMap

STORE = JsonMap("rewinds.json")
#: How long an agent's request waits for an answer.
ASK_TTL_S = 24 * 3600
WHY_MAX = 300
NOTE_PREFIX = "(This chat was rewound"
DECLINED_PREFIX = "(The rewind you asked for"


def _bad(error: str, status: int = 400, code: str = "bad_request", **extra) -> tuple[bool, dict]:
    return False, {"error": error, "status": status, "code": code, **extra}


def _replies(msgs: list[dict]) -> list[dict]:
    return [m for m in msgs if m.get("role") == "assistant" and not m.get("rewound")
            and not m.get("compacted")]


def resolve(session: str, to: str = "", back: int = 0) -> tuple[dict | None, dict]:
    """The reply to go back to, by its message id (`to`) or by counting
    replies back from the newest (`back`: 1 is the reply before the newest,
    so the newest exchange is set aside). `(found, {})` with `found`
    `{"id", "record", "n", "text", "at"}`, or `(None, error)`."""
    from . import branch, transcript

    got = transcript.messages(session, limit=1_000_000)   # the whole chat
    if got is None:
        return None, {"error": "no transcript for that chat", "status": 404, "code": "not_found"}
    msgs = got[0]
    replies = _replies(msgs)
    if to:
        target = next((m for m in replies if m.get("id") == to), None)
        if target is None:
            return None, {"error": "that is not one of the chat's replies", "status": 404,
                          "code": "no_such_reply"}
    else:
        if back < 1:
            return None, {"error": "say which reply: to (its id) or back (how many replies back)",
                          "status": 400, "code": "bad_request"}
        if len(replies) <= back:
            return None, {"error": f"the chat has only {len(replies)} replies", "status": 409,
                          "code": "too_far"}
        target = replies[-1 - back]
    record = branch.fork_point(session, str(target.get("id") or ""))
    if not record:
        return None, {"error": "could not find that reply in the transcript", "status": 409,
                      "code": "no_record"}
    i = msgs.index(target)
    after = [m for m in msgs[i + 1:] if not m.get("rewound")]
    text = " ".join(" ".join(str(p.get("text") or "") for p in target.get("parts") or []
                             if p.get("type") == "text").split())
    return {"id": target.get("id"), "record": record, "n": len(after),
            "text": text[:200], "at": target.get("at")}, {}


def _headless(session: str) -> bool:
    from . import driver

    return driver.headless_state(session) is not None


def view(session: str, now: float | None = None) -> dict | None:
    """The chat's rewind as the thread shows it, or None: a request waiting
    for an answer, an approved one waiting for the turn to end, or the last
    one's outcome for a day."""
    row = STORE.get(session)
    if not isinstance(row, dict):
        return None
    now = time.time() if now is None else now
    if row.get("state") == "asked" and now - float(row.get("asked_at") or 0) > ASK_TTL_S:
        return None
    if row.get("state") in ("done", "declined", "failed") \
            and now - float(row.get("answered_at") or 0) > 86400:
        return None
    return {k: row.get(k) for k in ("id", "to", "n", "text", "why", "by", "state",
                                    "asked_at", "answered_at", "error") if row.get(k) is not None}


def _rewound_note(row: dict) -> str:
    when = time.strftime("%-d %b %H:%M", time.localtime(float(row.get("to_at") or 0))) \
        if row.get("to_at") else ""
    why = f" Why: {row['why']}" if row.get("why") else ""
    return (f"{NOTE_PREFIX} to your reply{' of ' + when if when else ''}; what came after it was "
            f"set aside, and you do not remember it. Files on disk were not changed, so any work "
            f"done after that reply is still there.{why})")


def _carry_out(session: str, row: dict, now: float) -> tuple[bool, dict]:
    from . import driver

    r = driver.headless_driver().rewind(session, str(row["record"]))
    if not r.get("ok"):
        row = {**row, "state": "failed", "answered_at": round(now, 3),
               "error": str(r.get("error") or r.get("code") or "sessiond refused")}
        STORE.put(session, row)
        return _bad(row["error"], 409, str(r.get("code") or "refused"))
    row = {**row, "state": "waiting" if r.get("waiting") else "done",
           "answered_at": round(now, 3), "note": _rewound_note(row)}
    STORE.put(session, row)
    return True, {"session": session, "rewind": view(session, now)}


def request(body, bearer: str, host: bool = False,
            now: float | None = None) -> tuple[bool, dict]:
    """`POST /session/rewind {"session", "to" | "back", "why"?, "dry_run"?}`.
    `dry_run` answers `{to, n, text}`, what it would set aside, and does
    nothing. From a
    paired device it is carried out (`state` `done`, or `waiting` for the turn
    to end). With the host's own token and no bearer (an agent, `media
    session rewind`) it is kept as a request for a person to approve (`state`
    `asked`)."""
    now = time.time() if now is None else now
    if not isinstance(body, dict):
        return _bad("the body must be an object")
    by_agent = host and not bearer
    if not by_agent:
        user, err = auth.gate(bearer)
        if not user:
            return False, err
    session = str(body.get("session") or "").strip()
    if not sessions._SESSION.fullmatch(session):
        return _bad("not a session id")
    if not _headless(session):
        return _bad("only a chat the session host runs can be rewound for now", 409,
                    "not_headless")
    try:
        back = int(body.get("back") or 0)
    except (TypeError, ValueError):
        return _bad("back is a whole number of replies")
    why = " ".join(str(body.get("why") or "").split())[:WHY_MAX]
    found, err = resolve(session, str(body.get("to") or "").strip(), back)
    if found is None:
        return False, err
    if found["n"] == 0:
        return _bad("nothing comes after that reply", 409, "nothing_to_rewind")
    if body.get("dry_run"):
        # What a rewind there would set aside, for the confirm step; nothing
        # is kept or done.
        return True, {"session": session, "dry_run": True, "to": found["id"], "n": found["n"],
                      "text": found["text"]}
    row = {"id": secrets.token_hex(6), "to": found["id"], "record": found["record"],
           "n": found["n"], "text": found["text"], "to_at": found["at"], "why": why,
           "by": "agent" if by_agent else "you", "asked_at": round(now, 3)}
    if by_agent:
        STORE.put(session, {**row, "state": "asked"})
        return True, {"session": session, "pending": True, "rewind": view(session, now)}
    return _carry_out(session, row, now)


def answer(body, bearer: str, now: float | None = None) -> tuple[bool, dict]:
    """`POST /session/rewind/answer {"session", "id", "approve"}`: a person's
    answer to an agent's request. Needs a paired device: the host's own token
    (the agent) cannot approve its own request."""
    now = time.time() if now is None else now
    user, err = auth.gate(bearer)
    if not user:
        return False, err
    if not isinstance(body, dict):
        return _bad("the body must be an object")
    session = str(body.get("session") or "").strip()
    row = STORE.get(session)
    if not isinstance(row, dict) or row.get("id") != body.get("id") \
            or row.get("state") != "asked" \
            or now - float(row.get("asked_at") or 0) > ASK_TTL_S:
        return _bad("no such request waiting", 404, "not_pending")
    if not body.get("approve"):
        STORE.put(session, {**row, "state": "declined", "answered_at": round(now, 3),
                            "note": f"{DECLINED_PREFIX} was declined; the chat goes on as it was.)"})
        return True, {"session": session, "rewind": view(session, now)}
    return _carry_out(session, row, now)


def cancel(body, bearer: str, host: bool = False) -> tuple[bool, dict]:
    """`POST /session/rewind/cancel {"session"}`: drop a request still asked,
    or an approved rewind still waiting for its turn to end."""
    from . import driver

    if not (host and not bearer):
        user, err = auth.gate(bearer)
        if not user:
            return False, err
    session = str((body or {}).get("session") or "").strip() if isinstance(body, dict) else ""
    row = STORE.get(session)
    if not isinstance(row, dict) or row.get("state") not in ("asked", "waiting"):
        return _bad("no rewind waiting", 404, "not_pending")
    if row.get("state") == "waiting":
        driver.headless_driver().cancel_rewind(session)
    STORE.drop(session)
    return True, {"session": session, "cancelled": True}


def take_note(session: str) -> str:
    """The note owed to the chat's next message, once ("" when none)."""
    row = STORE.get(session)
    if not isinstance(row, dict) or not row.get("note") or row.get("state") == "waiting":
        return ""
    owed = str(row["note"])

    def fn(rows: dict) -> bool:
        r = rows.get(session)
        if not isinstance(r, dict) or not r.get("note"):
            return False
        r.pop("note", None)
        return True
    STORE.update(fn)
    return owed


def with_note(session: str, text: str) -> str:
    owed = take_note(session)
    return f"{owed}\n\n{text}" if owed else text


# --- media session rewind ---------------------------------------------------


def main(argv: list[str]) -> int:
    """`media session rewind --back N | --to <message id> [--why "<words>"]
    [--session <id>]`: ask for this chat (or `--session`) to be taken back
    to a reply. Through the server with the host's own token, so it is a
    request: a card in the thread asks a person to approve it. `--cancel`
    drops a request still waiting; `--status` shows it."""
    ap = argparse.ArgumentParser(prog="media session rewind")
    ap.add_argument("--session", default=os.environ.get("CLAUDE_CODE_SESSION_ID", ""),
                    help="the chat (default: the one this runs in)")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--back", type=int, help="replies back from the newest (1: set aside the "
                   "newest exchange)")
    g.add_argument("--to", help="the id of the reply to go back to")
    g.add_argument("--cancel", action="store_true", help="drop the request still waiting")
    g.add_argument("--status", action="store_true", help="show the chat's rewind, if any")
    ap.add_argument("--why", default="", help="why, shown on the card and told to the chat")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if not a.session:
        print("media session rewind: no chat (--session, or run it inside one)", file=sys.stderr)
        return 2
    from agent_media_core.phone_ask import _call

    if a.status:
        code, obj = _call("GET", f"/session/rewind?session={a.session}", None, timeout=15.0)
    elif a.cancel:
        code, obj = _call("POST", "/session/rewind/cancel", {"session": a.session}, timeout=15.0)
    else:
        body: dict = {"session": a.session, "why": a.why}
        body.update({"to": a.to} if a.to else {"back": a.back})
        code, obj = _call("POST", "/session/rewind", body, timeout=30.0)
    if code != 200 or not obj.get("ok"):
        print(f"media session rewind: {obj.get('error') or f'HTTP {code}'}", file=sys.stderr)
        return 1
    if a.json:
        print(json.dumps(obj))
        return 0
    rw = obj.get("rewind") or {}
    if a.cancel:
        print("cancelled")
    elif not rw:
        print("no rewind")
    elif rw.get("state") == "asked":
        print(f"asked: a card in the thread asks for approval to go back {rw.get('n')} "
              f"message(s), to the reply “{(rw.get('text') or '')[:60]}”. Nothing "
              "happens until it is approved; end your turn as usual.")
    else:
        print(f"{rw.get('state')}: {rw.get('n')} message(s) set aside")
    return 0
