"""Archive and delete at the provider (mail_move.py, §6.25 "Archive and
delete"): Gmail's All Mail and Bin, another server's Archive and Trash, the
undo, MOVE or COPY+EXPUNGE, the provider changed before the local copy, and
a thread's own sent mail never pulled into the Inbox. The IMAP server is a
stand-in keeping folders of Message-IDs; notmuch is a stand-in over real
files in a tmp maildir. No network. HOME and the state dirs are throwaway
(test_mail_read's `isolated`)."""

from __future__ import annotations

import json

import pytest

from agent_media_server import contacts, mail_list, mail_move, mail_remote, timeline
from test_contract import AUTH, call, server, signed_in, typed  # noqa: F401
from test_mail_read import isolated  # noqa: F401

MSMTPRC = "account personal\nfrom david@example.com\naccount other\nfrom me@example.org\n"
MBSYNCRC = """IMAPAccount a
Host imap.gmail.com
User david@example.com
Pass x

IMAPAccount b
Host mail.example.org
User me@example.org
Pass y
"""


class Server:
    """Folders of Message-IDs; MOVE, COPY, STORE \\Deleted, UID EXPUNGE."""

    def __init__(self, special, folders, caps=("IMAP4REV1", "MOVE", "UIDPLUS"), refuse=False):
        self.special, self.folders, self.capabilities = special, folders, caps
        self.sel, self.log, self.refuse = None, [], refuse
        self.deleted: set = set()

    def capability(self):
        # After login, as a real server answers (imaplib's own list is older).
        return "OK", [" ".join(self.capabilities).encode()]

    def list(self):
        rows = [f'({flag}) "/" "{name}"'.encode() for name, flag in self.special]
        return "OK", rows + [f'() "/" "{n}"'.encode() for n in self.folders
                             if n not in {s[0] for s in self.special}]

    def select(self, folder, readonly=False):
        self.sel = folder.strip('"')
        return ("OK", [b"1"]) if self.sel in self.folders else ("NO", [])

    def _uids(self, s):
        return [int(u) for u in s.split(",")]

    def uid(self, cmd, *args):
        self.log.append((cmd, self.sel, args))
        box = self.folders[self.sel]
        if cmd == "SEARCH":
            want = args[2].strip('"').strip("<>")
            return "OK", [" ".join(str(i + 1) for i, m in enumerate(box) if m == want).encode()]
        if self.refuse and cmd in ("MOVE", "COPY"):
            return "NO", [b"no"]
        ids = [box[u - 1] for u in self._uids(args[0])]
        dest = args[1].strip('"') if len(args) > 1 else None
        # A folder holds a message once (Gmail's All Mail already has it).
        if cmd == "MOVE":
            self.folders[dest] += [m for m in ids if m not in self.folders[dest]]
            self.folders[self.sel] = [m for m in box if m not in ids]
        elif cmd == "COPY":
            self.folders[dest] += [m for m in ids if m not in self.folders[dest]]
        elif cmd == "STORE":
            self.deleted |= {(self.sel, m) for m in ids}
        elif cmd == "EXPUNGE":
            self.folders[self.sel] = [m for m in box if (self.sel, m) not in self.deleted]
        return "OK", [b""]

    def logout(self):
        pass


@pytest.fixture
def world(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".msmtprc").write_text(MSMTPRC)
    (home / ".mbsyncrc").write_text(MBSYNCRC)
    root = tmp_path / "Mail"
    msgs = {}

    def put(mid, acct, folder, tags, thread="a1"):
        d = root / acct / folder / "cur"
        d.mkdir(parents=True, exist_ok=True)
        f = d / f"{mid}:2,S"
        f.write_text(f"Message-ID: <{mid}>\n\nx\n")
        msgs[mid] = {"id": mid, "thread": thread, "tags": set(tags) | {acct}, "file": f}

    calls = []

    def nm(args, timeout):
        calls.append(args)
        if args[:3] == ["config", "get", "database.path"]:
            return str(root) + "\n"
        if args[0] == "show":
            q = args[-1]
            hit = [m for m in msgs.values()
                   if (q.startswith("thread:") and m["thread"] == q[7:]) or q == f'id:"{m["id"]}"']
            return json.dumps([[[{"id": m["id"], "tags": sorted(m["tags"]), "headers": {},
                                  "filename": [str(m["file"])]}, []]] for m in hit])
        if args[0] == "tag":
            q = args[-1]
            for m in msgs.values():
                if q == f'id:"{m["id"]}"':
                    for op in args[1:args.index("--")]:
                        (m["tags"].add if op[0] == "+" else m["tags"].discard)(op[1:])
            return ""
        if args[0] == "new":
            for m in msgs.values():  # files found where they are now
                if not m["file"].exists():
                    found = list(root.rglob(m["file"].name))
                    if found:
                        m["file"] = found[0]
            return ""
        return ""
    monkeypatch.setattr(timeline, "_notmuch", nm)
    servers = {
        "david@example.com": Server([("[Gmail]/All Mail", "\\All"), ("[Gmail]/Bin", "\\Trash")],
                                    {"INBOX": ["in-1@x"], "[Gmail]/All Mail": ["in-1@x", "sent-1@x"],
                                     "[Gmail]/Bin": []}),
        "me@example.org": Server([("Archive", "\\Archive"), ("Trash", "\\Trash")],
                                 {"INBOX": ["o-1@x"], "Archive": [], "Trash": []},
                                 caps=("IMAP4REV1", "UIDPLUS")),
    }
    monkeypatch.setattr(mail_remote, "_connect", lambda a: servers[a["address"]])
    put("in-1@x", "personal", "INBOX", {"inbox", "unread"})
    put("sent-1@x", "personal", "[Gmail]/Sent Mail", {"sent"})
    put("o-1@x", "other", "INBOX", {"inbox"}, thread="b2")
    return {"msgs": msgs, "servers": servers, "root": root, "calls": calls}


def test_gmail_archive_moves_to_all_mail_then_here(world):
    got = mail_move.move(thread_id="a1", to="archive")
    assert got == {"to": "archive", "moved": 1, "errors": {}}
    g = world["servers"]["david@example.com"]
    assert g.folders["INBOX"] == [] and "in-1@x" in g.folders["[Gmail]/All Mail"]
    m = world["msgs"]["in-1@x"]
    assert "inbox" not in m["tags"] and "archived" in m["tags"]
    assert m["file"].parent.parent.name == "Archive" and m["file"].exists()
    # The thread's sent reply is left where it is.
    assert "Sent Mail" in str(world["msgs"]["sent-1@x"]["file"])
    # Undo: a copy from All Mail (Gmail's Inbox label), only what was archived.
    got = mail_move.move(thread_id="a1", to="inbox")
    assert got["moved"] == 1 and g.folders["INBOX"] == ["in-1@x"]
    assert {"inbox"} <= m["tags"] and "archived" not in m["tags"]
    assert [c[0] for c in g.log if c[0] in ("MOVE", "COPY")] == ["MOVE", "COPY"]


def test_gmail_trash_and_back(world):
    got = mail_move.move(message_id="in-1@x", to="trash")
    g = world["servers"]["david@example.com"]
    assert got["moved"] == 1 and g.folders["[Gmail]/Bin"] == ["in-1@x"]
    m = world["msgs"]["in-1@x"]
    assert "deleted" in m["tags"] and "inbox" not in m["tags"]
    assert m["file"].parent.parent.name == "Trash"
    got = mail_move.move(message_id="in-1@x", to="inbox")
    assert got["moved"] == 1 and g.folders["INBOX"] == ["in-1@x"] and g.folders["[Gmail]/Bin"] == []
    assert "deleted" not in m["tags"] and "inbox" in m["tags"]


def test_without_move_copy_delete_expunge_just_that_one(world):
    o = world["servers"]["me@example.org"]
    o.folders["INBOX"].append("keep@x")
    got = mail_move.move(thread_id="b2", to="archive")
    assert got["moved"] == 1
    assert o.folders["INBOX"] == ["keep@x"] and o.folders["Archive"] == ["o-1@x"]
    assert [c[0] for c in o.log if c[0] != "SEARCH"] == ["COPY", "STORE", "EXPUNGE"]


def test_a_server_with_neither_move_nor_uidplus_is_left_alone(world):
    o = world["servers"]["me@example.org"]
    o.capabilities = ("IMAP4REV1",)
    got = mail_move.move(thread_id="b2", to="archive")
    assert got["moved"] == 0 and "no MOVE or UIDPLUS" in got["errors"]["other"]
    assert [c[0] for c in o.log if c[0] != "SEARCH"] == [] and o.folders["INBOX"] == ["o-1@x"]


def test_capabilities_are_asked_after_login(world):
    g = world["servers"]["david@example.com"]
    g.capability = lambda: ("OK", [b"IMAP4REV1 MOVE UIDPLUS"])
    g.capabilities = ("IMAP4REV1",)  # what imaplib saw before login
    mail_move.move(thread_id="a1", to="archive")
    assert [c[0] for c in g.log if c[0] != "SEARCH"] == ["MOVE"]


def test_the_server_says_no_so_nothing_changes_here(world):
    world["servers"]["david@example.com"].refuse = True
    got = mail_move.move(thread_id="a1", to="archive")
    assert got["moved"] == 0 and "personal" in got["errors"]
    m = world["msgs"]["in-1@x"]
    assert "inbox" in m["tags"] and m["file"].parent.parent.name == "INBOX"


def test_refusals(world):
    with pytest.raises(mail_move.Refused):
        mail_move.move(thread_id="a1", to="burn")
    with pytest.raises(mail_move.Refused):
        mail_move.move(to="archive")
    with pytest.raises(mail_move.Refused, match="thread"):
        mail_move.move(thread_id="not-hex", to="archive")
    del world["servers"]["me@example.org"].special[0]
    got = mail_move.move(thread_id="b2", to="archive")
    assert got["moved"] == 0 and "no Archive" in got["errors"]["other"]


def test_trash_is_a_folder(world):
    assert "trash" in mail_list.FOLDERS
    assert mail_list._query("trash", "", "", set()) == "(tag:deleted)"
    assert "not tag:deleted" in mail_list._query("inbox", "", "", set())


def test_the_route(server, signed_in, world, monkeypatch):
    res, obj = call(server, "POST", "/mail/move", {"thread": "a1", "to": "archive"}, headers=AUTH)
    assert res.status == 200 and obj["moved"] == 1
    res, obj = call(server, "POST", "/mail/move", {"thread": "a1", "to": "x"}, headers=AUTH)
    assert res.status == 400
    world["servers"]["me@example.org"].refuse = True
    res, obj = call(server, "POST", "/mail/move", {"thread": "b2", "to": "archive"}, headers=AUTH)
    assert res.status == 502 and obj["errors"]
    res, _ = call(server, "POST", "/mail/move", {"thread": "a1", "to": "inbox"})
    assert res.status in (401, 403)
    monkeypatch.setattr(contacts, "is_owner", lambda: False)
    res, _ = call(server, "POST", "/mail/move", {"thread": "a1", "to": "inbox"}, headers=AUTH)
    assert res.status == 403
