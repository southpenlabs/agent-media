"""`POST /session/branch` — a new chat from a reply (server-contract.md §6.34).

David, 29 Oct 2026: "I think I'd like to Branch off from a reply". He
chose to be offered both, each time, from the long press on a reply:

* **with its history** (`mode: "history"`): a new chat that remembers the
  conversation up to and including that reply and nothing after it. Claude
  Code does it itself — `--resume <session> --fork-session
  --resume-session-at <record>` — so the transcript is never cut by hand;
  sessiond starts it under a new id (sessiond.py `fork_from`), headless,
  in the folder the thread ran in. Claude Code chats only.
* **from its words** (`mode: "quote"`): a fresh chat in the same folder,
  the reply quoted in front of the first message — what `POST /reply`
  `mode: "branch"` has always done, for any agent.

Either way the thread it came from is left as it was. The first message is
required: a branch starts with something to say.
"""

from __future__ import annotations

import json

from . import auth, driver, sessions, transcript


def _prompt(rec: dict) -> bool:
    """A record that starts a user message: what the person (or a peer) said,
    not a tool's result or the harness's own note."""
    if rec.get("type") != "user" or rec.get("isMeta") or rec.get("isSidechain"):
        return False
    content = (rec.get("message") or {}).get("content")
    if isinstance(content, str):
        return True
    return isinstance(content, list) and any(
        isinstance(b, dict) and b.get("type") != "tool_result" for b in content)


def fork_point(session: str, mid: str) -> str:
    """The record a branch from message `mid` resumes at: the last assistant
    record of that reply (its words after its steps), so all of it is kept.
    "" when the transcript or the message is not there."""
    path = transcript.transcript_path(session)
    if not path or not mid:
        return ""
    found, last = False, ""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(rec, dict) or rec.get("isSidechain"):
                    continue
                if not found:
                    if rec.get("uuid") == mid:
                        found = True
                        if rec.get("type") == "assistant":
                            last = mid
                    continue
                if _prompt(rec):
                    break
                if rec.get("type") == "assistant" and rec.get("uuid"):
                    last = rec["uuid"]
    except OSError:
        return ""
    return last


def session_branch(session: str, mid: str, text: str, bearer: str, *,
                   mode: str = "history", quote: str = "") -> tuple[bool, dict]:
    """`{"session", "id", "text", "mode", "quote"?}` → `{"session": <the new
    chat>, "branched": true, ...}`; `quote` is the reply's words, for
    `mode: "quote"`."""
    from . import send

    session = (session or "").strip()
    if not sessions._SESSION.fullmatch(session):
        return False, {"error": "not a session id", "status": 400}
    if not (text or "").strip():
        return False, {"error": "a branch needs a first message", "status": 400}
    if mode == "quote":
        return send.reply("", text, bearer, quote=quote, mode="branch", session=session)
    if mode != "history":
        return False, {"error": "mode is history or quote", "status": 400}
    user, err = auth.gate(bearer)
    if not user:
        return False, err
    if not transcript.transcript_path(session):
        return False, {"error": "only a Claude Code chat can be branched with its history",
                       "status": 409}
    at = fork_point(session, (mid or "").strip())
    if not at:
        return False, {"error": "that reply is not in this chat's transcript", "status": 404}
    cwd = sessions.transcript_cwd(session)
    if not cwd:
        return False, {"error": "this chat's folder is not known", "status": 409}
    ok, detail = driver.headless_driver().start(agent="claude", cwd=cwd, text=text,
                                                 fork_from=session, fork_at=at)
    if ok:
        detail["branched"] = True
        detail["from"] = {"session": session, "id": mid}
    return ok, detail
