"""The Mail tab's lists (contract §6.25 "The Mail tab", step 4 of
docs/proposals/2026-10-20-mail-in-sasonica.md; David, 18 Nov 2026: a top
level Mail item, its list like the chats list).

`GET /mail/threads` lists notmuch threads, newest first, for a folder
(inbox, unread, starred, sent, spam, all), an account and a search, each row
with what a chats-list row shows: who, the subject, a preview line of the
newest message, its time, how many, unread, starred, the accounts it is in
and the sender's contact when they are in the book. `GET /mail/threads/<id>`
gives a thread's messages, each as `GET /mail/message` reads one. `POST
/mail/tag` marks read or unread and starred: notmuch writes those as maildir
flags, which mbsync carries to Gmail (read, and Gmail's star).

An account is one of msmtp's accounts (mail_send.accounts), whose name is
also its notmuch tag and maildir folder (mbsync's channel), as on red2.

The owner's book only, as reading one; nothing is kept or logged.
"""

from __future__ import annotations

import email.parser
import email.policy
import email.utils
import json
import os
import re
import subprocess
import time
from agent_media_core import failures as _failures

#: The most threads a page may ask for, and a thread's most messages.
PAGE_MAX = 100
THREAD_MAX = 50
PREVIEW_CHARS = 160

#: Each folder as a notmuch query; deleted mail is never listed.
FOLDERS = {
    "inbox": "tag:inbox and not tag:spam",
    "unread": "tag:unread and not tag:spam",
    "starred": "tag:flagged and not tag:spam",
    "sent": "tag:sent",
    "spam": "tag:spam",
    "all": "not tag:spam",
}
#: What `POST /mail/tag` may change: only what round-trips to Gmail today
#: (archive and labels wait for step 5's lieer).
TAGS = {"unread", "flagged"}

_THREAD_ID = re.compile(r"[0-9a-f]{1,32}")


class BadRequest(Exception):
    pass


def valid_thread(tid: str) -> bool:
    return bool(_THREAD_ID.fullmatch(tid or ""))


def _nm(args: list[str], timeout: float = 60) -> str:
    from . import timeline

    got = timeline._notmuch(args, timeout)
    if got is None:
        raise RuntimeError("notmuch did not answer")
    return got


def _json(args: list[str], timeout: float = 60):
    try:
        return json.loads(_nm(args, timeout) or "[]")
    except ValueError:
        raise RuntimeError("notmuch answered something else") from None


def accounts() -> list[dict]:
    """`[{name, address}]`: msmtp's accounts, in its order."""
    from . import mail_send

    return [{"name": n, "address": a} for a, n in mail_send.accounts().items()]


def _count_batch(queries: list[str]) -> list[str] | None:
    """`notmuch count --batch`'s counts for `queries`, or None."""
    from . import timeline

    try:
        run = subprocess.run(timeline._notmuch_cmd() + ["count", "--batch"],
                             input="\n".join(queries) + "\n", capture_output=True,
                             text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return run.stdout.split() if run.returncode == 0 else None


def _unread_counts(names: list[str]) -> dict[str, int]:
    """Each account's unread inbox mail, in one `notmuch count --batch`."""
    if not names:
        return {}
    got = _count_batch([f"tag:inbox and tag:unread and not tag:spam and tag:{n}"
                        for n in names])
    if not got or len(got) != len(names):
        return {}
    return {n: int(c) for n, c in zip(names, got) if c.isdigit()}


def _query(folder: str, account: str, q: str, names: set[str]) -> str:
    if folder not in FOLDERS:
        raise BadRequest(f"folder is one of {', '.join(FOLDERS)}")
    parts = [f"({FOLDERS[folder]})", "not tag:deleted"]
    if account:
        if account not in names:
            raise BadRequest("no such account")
        parts.append(f"tag:{account}")
    q = (q or "").strip()
    if q:
        if len(q) > 500:
            raise BadRequest("the search is too long")
        parts.append(f"({q})")
    return " and ".join(parts)


def _addr(value: str) -> tuple[str, str]:
    got = email.utils.getaddresses([value or ""])
    return (got[0][0].strip(), got[0][1].strip().lower()) if got else ("", "")


def _preview(path: str) -> str:
    """The newest message's first words: its plain part (else its HTML's
    words), quoted lines and the quote's "On … wrote:" left out."""
    from . import mail_read

    try:
        with open(path, "rb") as f:
            msg = email.parser.BytesParser(policy=email.policy.default).parse(f)
        plain, rich = mail_read._bodies(msg)
    except (OSError, LookupError, ValueError):
        return ""
    body = plain if plain and plain.strip() else mail_read.html_text(rich or "")
    out = []
    for line in body.splitlines():
        s = line.strip()
        if s.startswith(">"):
            continue
        if re.match(r"^On .{4,200} wrote:$", s):
            break
        if s:
            out.append(s)
        if sum(len(x) for x in out) > PREVIEW_CHARS:
            break
    text = re.sub(r"\s+", " ", " ".join(out)).strip()
    return text[:PREVIEW_CHARS]


def _messages(tree) -> list[dict]:
    from . import timeline

    return timeline._messages(tree)


def threads(*, folder: str = "inbox", account: str = "", q: str = "",
            offset: int = 0, limit: int = 50, counts: bool = True) -> dict:
    """`GET /mail/threads`'s answer. BadRequest, RuntimeError (notmuch)."""
    from . import timeline

    accts = accounts()
    names = {a["name"] for a in accts}
    query = _query(folder, account, q, names)
    limit = max(1, min(int(limit), PAGE_MAX))
    offset = max(0, int(offset))
    rows = _json(["search", "--format=json", "--sort=newest-first",
                  f"--offset={offset}", f"--limit={limit + 1}", query])
    more = len(rows) > limit
    rows = rows[:limit]
    # Each thread's newest matching message: who sent it, its file for the
    # preview. One `show` for the page.
    newest: dict[str, dict] = {}
    if rows:
        ids = " or ".join(f"thread:{r['thread']}" for r in rows if valid_thread(str(r.get("thread"))))
        if ids:
            tree = _json(["show", "--format=json", "--body=false", "--entire-thread=false",
                          f"({query}) and ({ids})"])
            by_id = {}
            for r in rows:
                # "id:a id:b" (or "… or …"); an id with a space is quoted.
                matched = str((r.get("query") or [""])[0] or "")
                for mid in re.findall(r'id:(?:"((?:[^"]|"")*)"|(\S+))', matched):
                    mid = mid[0].replace('""', '"') if mid[0] else mid[1]
                    by_id[mid] = r["thread"]
            for m in _messages(tree):
                if not isinstance(m, dict) or m.get("excluded"):
                    continue
                tid = by_id.get(str(m.get("id") or ""))
                if tid and (m.get("timestamp") or 0) >= (newest.get(tid, {}).get("timestamp") or -1):
                    newest[tid] = m
    mine = timeline.mail_self()
    try:
        book = timeline._mail_index()
    except Exception as _exc:  # the book is a nicety here, never a failure
        _failures.swallowed(_exc)
        book = {}
    out = []
    for r in rows:
        tid = str(r.get("thread") or "")
        tags = [str(t) for t in r.get("tags") or []]
        m = newest.get(tid) or {}
        h = m.get("headers") or {}
        name, addr = _addr(str(h.get("From") or ""))
        files = m.get("filename") or []
        files = files if isinstance(files, list) else [files]
        authors = str(r.get("authors") or "").replace("|", ",")
        out.append({
            "id": tid,
            "subject": str(r.get("subject") or "").strip(),
            "authors": [a.strip() for a in authors.split(",") if a.strip()],
            "from": {"name": name, "address": addr},
            "mine": addr in mine,
            "contact": book.get(addr),
            "preview": _preview(str(files[0])) if files else "",
            "date": int(r.get("timestamp") or 0),
            "count": int(r.get("total") or 0),
            "matched": int(r.get("matched") or 0),
            "unread": "unread" in tags,
            "starred": "flagged" in tags,
            "attachment": "attachment" in tags,
            "accounts": [t for t in tags if t in names],
            "message": str(m.get("id") or "") or None,
        })
    answer = {"folder": folder, "account": account or None, "q": q or None,
              "offset": offset, "threads": out, "more": more,
              "folders": list(FOLDERS)}
    if counts:
        unread = _unread_counts([a["name"] for a in accts])
        answer["accounts"] = [{**a, "unread": unread.get(a["name"])} for a in accts]
    return answer


def thread(tid: str, *, images: bool = False) -> dict:
    """`GET /mail/threads/<id>`: its messages oldest first (the newest
    THREAD_MAX), each as `mail_read.message` reads it plus its tags."""
    from . import mail_read

    if not valid_thread(tid):
        raise mail_read.NotFound("no such thread")
    tree = _json(["show", "--format=json", "--body=false", "--entire-thread=true",
                  f"thread:{tid} and not tag:deleted"])
    ms = [m for m in _messages(tree) if isinstance(m, dict) and m.get("id")]
    if not ms:
        raise mail_read.NotFound("no such thread")
    ms.sort(key=lambda m: m.get("timestamp") or 0)
    cut = len(ms) > THREAD_MAX
    ms = ms[-THREAD_MAX:]
    names = {a["name"] for a in accounts()}
    out = []
    for m in ms:
        tags = [str(t) for t in m.get("tags") or []]
        files = m.get("filename") or []
        files = [str(f) for f in (files if isinstance(files, list) else [files])]
        path = next((f for f in files if os.path.isfile(f)), "")
        try:
            got = mail_read.message(str(m["id"]), images=images, path=path)
        except (mail_read.NotFound, OSError):
            continue
        got.update(unread="unread" in tags, starred="flagged" in tags,
                   spam="spam" in tags, accounts=[t for t in tags if t in names])
        out.append(got)
    subject = next((x["subject"] for x in out if x.get("subject")), "")
    return {"id": tid, "subject": subject, "messages": out, "cut": cut,
            "unread": any(x["unread"] for x in out),
            "starred": any(x["starred"] for x in out)}


def tag(*, thread_id: str = "", message_id: str = "", add=(), remove=()) -> dict:
    """`POST /mail/tag`: add or remove `unread` / `flagged` on a thread or one
    message. BadRequest for anything else."""
    from . import mail_read

    add, remove = list(add or []), list(remove or [])
    if not add and not remove:
        raise BadRequest("nothing to change")
    bad = [t for t in add + remove if t not in TAGS]
    if bad:
        raise BadRequest(f"only {', '.join(sorted(TAGS))} can be changed")
    if thread_id and valid_thread(thread_id):
        target = f"thread:{thread_id}"
    elif message_id and mail_read.valid_id(message_id):
        target = mail_read._query(message_id)
    else:
        raise BadRequest('expected "thread" or "id"')
    ops = [f"+{t}" for t in add] + [f"-{t}" for t in remove]
    # notmuch writes the maildir flags too (synchronize_flags), so mbsync
    # carries read and starred to Gmail on its next run.
    try:
        _nm(["tag"] + ops + ["--", target], 60)
    except RuntimeError:
        # The ten-minute sync holds notmuch's write lock while it indexes.
        time.sleep(3)
        _nm(["tag"] + ops + ["--", target], 60)
    return {"changed": True, "add": add, "remove": remove}
