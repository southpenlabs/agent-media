"""The Mail tab's lists (mail_list.py, §6.25 "The Mail tab"): folders,
accounts, search and paging, rows as the chats list's (who, subject,
preview, time, unread, starred), a thread's messages, and read/starred.
notmuch is a Python stand-in for `timeline._notmuch` over made-up .eml files
with a small query evaluator; msmtp's config is a throwaway file; HOME and
the state dirs are throwaway and no MEDIA_* setting of this host leaks in
(test_mail_read's `isolated`, imported so it runs here)."""

from __future__ import annotations

import json
import re

import pytest

from agent_media_server import contacts, mail_list, timeline
from test_contract import AUTH, call, server, signed_in, typed  # noqa: F401
from test_mail_read import isolated  # noqa: F401

RC = """account personal
from david@example.com
account work
from david@work.example
"""


def eml(frm, to, subject, body, mid, date="Mon, 19 Oct 2026 10:00:00 +1100", extra=""):
    return (f"From: {frm}\nTo: {to}\nSubject: {subject}\nDate: {date}\n"
            f"Message-ID: <{mid}>\n{extra}Content-Type: text/plain; charset=utf-8\n\n{body}\n")


class FakeNotmuch:
    """search (json, sort, offset, limit), show (json), tag and count --batch
    over messages {id, thread, tags, ts, file}."""

    def __init__(self, tmp_path):
        self.dir = tmp_path / "nm"
        self.dir.mkdir()
        self.msgs: list[dict] = []
        self.calls: list[list[str]] = []

    def add(self, thread, mid, raw, tags, ts):
        p = self.dir / f"m{len(self.msgs)}.eml"
        p.write_text(raw)
        self.msgs.append({"id": mid, "thread": thread, "tags": set(tags), "ts": ts,
                          "file": str(p), "raw": raw})

    # A tiny evaluator: tag:, thread:, id:"…", and/or/not, parentheses, and a
    # bare word matched against the subject and body.
    def match(self, m, q):
        toks = re.findall(r'\(|\)|id:"(?:[^"]|"")*"|\S+?(?=\)|\s|$)', q)
        expr = []
        for t in toks:
            low = t.lower()
            if t in ("(", ")"):
                expr.append(t)
            elif low in ("and", "or", "not"):
                expr.append(low)
            elif t.startswith("tag:"):
                expr.append(repr(t[4:] in m["tags"]))
            elif t.startswith("thread:"):
                expr.append(repr(t[7:] == m["thread"]))
            elif t.startswith("id:"):
                v = t[3:]
                v = v[1:-1].replace('""', '"') if v.startswith('"') else v
                expr.append(repr(v == m["id"]))
            else:
                expr.append(repr(t.lower() in m["raw"].lower()))
        # Juxtaposed terms are "and", as in notmuch.
        out = []
        for e in expr:
            if out and out[-1] not in ("(", "and", "or", "not") and e not in (")", "and", "or"):
                out.append("and")
            out.append(e)
        return eval(" ".join(out) or "True")  # noqa: S307 — test-only, our own tokens

    def headers(self, m):
        import email
        msg = email.message_from_string(m["raw"])
        return {k: msg.get(k, "") for k in ("From", "To", "Cc", "Subject", "Date")}

    def __call__(self, args, timeout):
        self.calls.append(args)
        if args[:1] == ["count"]:
            return str(len(self.msgs))
        if args[:2] == ["config", "get"]:
            return "david@example.com" if args[2] == "user.primary_email" else ""
        q = args[-1]
        hits = [m for m in self.msgs if self.match(m, q)]
        if args[0] == "search" and "--output=files" in args:
            return json.dumps([m["file"] for m in hits])
        if args[0] == "search":
            by: dict = {}
            for m in hits:
                by.setdefault(m["thread"], []).append(m)
            rows = []
            for tid, ms in by.items():
                allm = [x for x in self.msgs if x["thread"] == tid]
                tags = set().union(*(x["tags"] for x in allm))
                rows.append({"thread": tid, "timestamp": max(x["ts"] for x in ms),
                             "matched": len(ms), "total": len(allm),
                             "authors": ", ".join(self.headers(x)["From"].split("<")[0].strip().strip('"')
                                                  for x in allm),
                             "subject": self.headers(allm[0])["Subject"],
                             "query": [" ".join(f"id:{x['id']}" for x in ms), None],
                             "tags": sorted(tags)})
            rows.sort(key=lambda r: -r["timestamp"])
            off = int(next(a.split("=")[1] for a in args if a.startswith("--offset=")))
            lim = int(next(a.split("=")[1] for a in args if a.startswith("--limit=")))
            return json.dumps(rows[off:off + lim])
        if args[0] == "show":
            if "--entire-thread=true" in args:
                tids = {m["thread"] for m in hits}
                hits = [m for m in self.msgs if m["thread"] in tids and "deleted" not in m["tags"]]
            return json.dumps([[[{"id": m["id"], "timestamp": m["ts"], "tags": sorted(m["tags"]),
                                  "filename": [m["file"]], "headers": self.headers(m),
                                  "excluded": False, "match": True}, []]] for m in hits])
        if args[0] == "tag":
            ops = args[1:args.index("--")]
            for m in hits:
                for op in ops:
                    (m["tags"].add if op[0] == "+" else m["tags"].discard)(op[1:])
            return ""
        return None


@pytest.fixture
def nm(tmp_path, monkeypatch):
    (tmp_path / "home" / ".msmtprc").write_text(RC)
    f = FakeNotmuch(tmp_path)
    monkeypatch.setattr(timeline, "_notmuch", f)
    monkeypatch.setattr(timeline, "_notmuch_ok", lambda: True)

    def batch(qs):
        return [str(sum(1 for m in f.msgs if f.match(m, q))) for q in qs]
    monkeypatch.setattr(mail_list, "_count_batch", batch)
    f.add("a1", "lunch-1@x", eml('"Lionel Smith" <lionel@example.com>', "david@example.com",
                                 "Lunch on Friday?", "Hi David,\nFriday at the café?", "lunch-1@x"),
          {"personal", "inbox"}, 100)
    f.add("a1", "lunch-2@x", eml("David <david@example.com>", "lionel@example.com",
                                 "Re: Lunch on Friday?",
                                 "Yes, see you there.\n\nOn Mon, Lionel wrote:\n> Friday at the café?",
                                 "lunch-2@x"), {"personal", "sent"}, 200)
    f.add("b2", "roster@w", eml("Boss <boss@work.example>", "david@work.example", "Roster",
                                "The roster is up.", "roster@w"),
          {"work", "inbox", "unread", "flagged", "attachment"}, 300)
    f.add("c3", "spam@s", eml("Prize <win@spam.example>", "david@example.com", "You won",
                              "Click here", "spam@s"), {"personal", "spam", "unread"}, 400)
    f.add("d4", "gone@x", eml("Old <old@example.com>", "david@example.com", "Deleted one",
                              "bye", "gone@x"), {"personal", "inbox", "deleted"}, 500)
    return f


def test_inbox_rows_are_like_chat_rows(nm):
    got = mail_list.threads()
    assert [t["id"] for t in got["threads"]] == ["b2", "a1"]  # newest first, no spam, no deleted
    b2, a1 = got["threads"]
    assert b2 == {**b2, "subject": "Roster", "unread": True, "starred": True, "attachment": True,
                  "accounts": ["work"], "preview": "The roster is up.", "date": 300, "count": 1,
                  "mine": False, "from": {"name": "Boss", "address": "boss@work.example"}}
    # The newest message in the folder speaks for the thread; the quote is left out.
    assert a1["from"]["address"] == "lionel@example.com" and a1["count"] == 2
    assert a1["preview"] == "Hi David, Friday at the café?" and a1["unread"] is False
    assert got["accounts"] == [{"name": "personal", "address": "david@example.com", "unread": 0},
                               {"name": "work", "address": "david@work.example", "unread": 1}]
    assert got["more"] is False and got["folders"][0] == "inbox"


def test_folders_accounts_search_and_paging(nm):
    assert [t["id"] for t in mail_list.threads(folder="spam")["threads"]] == ["c3"]
    sent = mail_list.threads(folder="sent")["threads"]
    assert [t["id"] for t in sent] == ["a1"] and sent[0]["mine"] is True
    assert sent[0]["preview"] == "Yes, see you there."
    assert [t["id"] for t in mail_list.threads(folder="starred")["threads"]] == ["b2"]
    assert [t["id"] for t in mail_list.threads(folder="unread")["threads"]] == ["b2"]
    assert [t["id"] for t in mail_list.threads(account="personal")["threads"]] == ["a1"]
    assert [t["id"] for t in mail_list.threads(folder="all", q="café")["threads"]] == ["a1"]
    page = mail_list.threads(folder="all", limit=1)
    assert [t["id"] for t in page["threads"]] == ["b2"] and page["more"] is True
    page = mail_list.threads(folder="all", limit=1, offset=1)
    assert [t["id"] for t in page["threads"]] == ["a1"] and page["more"] is False
    with pytest.raises(mail_list.BadRequest):
        mail_list.threads(folder="burn")
    with pytest.raises(mail_list.BadRequest):
        mail_list.threads(account="nobody")
    assert "accounts" not in mail_list.threads(counts=False)


def test_a_thread_oldest_first(nm):
    got = mail_list.thread("a1")
    assert [m["id"] for m in got["messages"]] == ["lunch-1@x", "lunch-2@x"]
    assert got["subject"] == "Lunch on Friday?" and got["messages"][1]["mine"] is True
    assert got["messages"][0]["text"].startswith("Hi David")
    assert got["messages"][0]["accounts"] == ["personal"]
    from agent_media_server import mail_read
    with pytest.raises(mail_read.NotFound):
        mail_list.thread("ffff")
    with pytest.raises(mail_read.NotFound):
        mail_list.thread("../x")


def test_tag_read_and_starred_only(nm):
    mail_list.tag(thread_id="b2", remove=["unread"])
    assert "unread" not in nm.msgs[2]["tags"]
    mail_list.tag(message_id="lunch-1@x", add=["flagged"])
    assert "flagged" in nm.msgs[0]["tags"] and "flagged" not in nm.msgs[1]["tags"]
    for bad in ({"add": ["inbox"]}, {"remove": ["spam"]}, {}):
        with pytest.raises(mail_list.BadRequest):
            mail_list.tag(thread_id="b2", **bad)
    with pytest.raises(mail_list.BadRequest):
        mail_list.tag(thread_id="not a thread", add=["unread"])
    tag_calls = [c for c in nm.calls if c[0] == "tag"]
    assert tag_calls == [["tag", "-unread", "--", "thread:b2"],
                         ["tag", "+flagged", "--", 'id:"lunch-1@x"']]


def test_the_routes(server, signed_in, nm, monkeypatch):
    res, obj = call(server, "GET", "/mail/threads?folder=inbox&limit=10", headers=AUTH)
    assert res.status == 200 and [t["id"] for t in obj["threads"]] == ["b2", "a1"]
    res, obj = call(server, "GET", "/mail/threads?folder=nope", headers=AUTH)
    assert res.status == 400
    res, obj = call(server, "GET", "/mail/threads?limit=x", headers=AUTH)
    assert res.status == 400
    res, obj = call(server, "GET", "/mail/threads/a1", headers=AUTH)
    assert res.status == 200 and len(obj["messages"]) == 2
    res, _ = call(server, "GET", "/mail/threads/ffff", headers=AUTH)
    assert res.status == 404
    res, obj = call(server, "POST", "/mail/tag", {"thread": "b2", "remove": ["unread"]}, headers=AUTH)
    assert res.status == 200 and obj["changed"]
    res, _ = call(server, "POST", "/mail/tag", {"thread": "b2", "add": ["inbox"]}, headers=AUTH)
    assert res.status == 400
    res, _ = call(server, "GET", "/mail/threads")
    assert res.status in (401, 403)
    monkeypatch.setattr(contacts, "is_owner", lambda: False)
    res, obj = call(server, "GET", "/mail/threads", headers=AUTH)
    assert res.status == 403 and "owner" in obj["error"]


def test_features_says_mail_for_the_owner_with_notmuch(nm, monkeypatch):
    from agent_media_server import features
    assert features.detect()["mail"] is True
    monkeypatch.setattr(contacts, "is_owner", lambda: False)
    assert features.detect()["mail"] is False
    monkeypatch.setattr(contacts, "is_owner", lambda: True)
    monkeypatch.setattr(timeline, "_notmuch_ok", lambda: False)
    assert features.detect()["mail"] is False
