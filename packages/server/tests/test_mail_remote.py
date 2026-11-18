"""Older mail at the provider (mail_remote.py, §6.25 "Older mail"): logins
from the accounts added here and the host's own ~/.mbsyncrc, the search
(Gmail's X-GM-RAW in All Mail, TEXT elsewhere), mail already here left out,
an account's failure beside the others' results, and fetching one message
into its Archive maildir. The IMAP server is a stand-in object; no network.
HOME and the state dirs are throwaway (test_mail_read's `isolated`)."""

from __future__ import annotations

import pytest

from agent_media_server import contacts, mail_remote, timeline
from test_contract import AUTH, call, server, signed_in, typed  # noqa: F401
from test_mail_read import isolated  # noqa: F401

MBSYNCRC = """IMAPAccount gmail-personal
Host imap.gmail.com
User david@example.com
PassCmd "printf 's3cret\\n'"
TLSType IMAPS

IMAPStore personal-remote
Account gmail-personal

IMAPAccount other
Host mail.example.org
Port 1993
User me@example.org
Pass plain
"""
MSMTPRC = """account personal
from david@example.com
account other
from me@example.org
"""

RAW = (b"From: Lionel <lionel@example.com>\r\nSubject: Old lunch\r\n"
       b"Date: Mon, 05 Jan 2015 10:00:00 +1100\r\nMessage-ID: <old-1@x>\r\n\r\nback then\r\n")
HERE = (b"From: Ana <ana@example.com>\r\nSubject: Recent\r\n"
        b"Date: Mon, 16 Nov 2026 10:00:00 +1100\r\nMessage-ID: <here-1@x>\r\n\r\nnow\r\n")


class FakeIMAP:
    def __init__(self, folders, store, fail=False):
        self.folders, self.store, self.fail, self.cmds = folders, store, fail, []
        self.literal = None

    def list(self):
        return "OK", [f'({flags}) "/" "{name}"'.encode() for name, flags in self.folders]

    def select(self, folder, readonly=False):
        self.cmds.append(("select", folder, readonly))
        return "OK", [b"2"]

    def uid(self, cmd, *args):
        self.cmds.append((cmd, args, self.literal))
        if cmd == "SEARCH":
            if self.fail:
                raise mail_remote.imaplib.IMAP4.error("BAD")
            return "OK", [b" ".join(str(u).encode() for u in self.store)]
        if cmd == "FETCH" and "BODY.PEEK[]" in args[1] and "HEADER" not in args[1]:
            u = int(args[0])
            return "OK", [(f"1 (UID {u} BODY[] {{9}}".encode(), self.store[u]), b")"]
        rows = []
        for u in args[0].split(","):
            rows += [(f"1 (UID {u} INTERNALDATE \"x\" BODY[HEADER] {{9}}".encode(),
                      self.store[int(u)].split(b"\r\n\r\n")[0] + b"\r\n\r\n"), b")"]
        return "OK", rows

    def logout(self):
        pass


@pytest.fixture
def setup(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".mbsyncrc").write_text(MBSYNCRC)
    (home / ".msmtprc").write_text(MSMTPRC)
    local = {"here-1@x"}
    calls = []

    def nm(args, timeout):
        calls.append(args)
        if args[:3] == ["config", "get", "database.path"]:
            return str(tmp_path / "Mail") + "\n"
        if args[:2] == ["search", "--output=messages"]:
            return "".join(f"id:{i}\n" for i in local if f'"{i}"' in args[-1])
        if args[:1] == ["new"]:
            local.add("old-1@x")
        return ""
    monkeypatch.setattr(timeline, "_notmuch", nm)
    servers = {}

    def connect(a):
        mail_remote._password(a)  # the login's password is read as mbsync reads it
        return servers[a["address"]]
    monkeypatch.setattr(mail_remote, "_connect", connect)
    servers["david@example.com"] = FakeIMAP(
        [("INBOX", "\\HasNoChildren"), ("[Gmail]/All Mail", "\\All \\HasNoChildren")],
        {7: RAW, 9: HERE})
    servers["me@example.org"] = FakeIMAP([("INBOX", ""), ("Archive", "\\Archive")], {3: RAW})
    return {"servers": servers, "calls": calls, "tmp": tmp_path}


def test_logins_from_the_hosts_mbsyncrc(setup):
    lg = mail_remote.logins()
    assert lg["personal"]["gmail"] is True and lg["personal"]["host"] == "imap.gmail.com"
    assert lg["other"]["port"] == 1993 and lg["other"]["gmail"] is False
    assert mail_remote._password(lg["personal"]) == "s3cret"
    assert mail_remote._password(lg["other"]) == "plain"


def test_search_gmail_and_other_leaving_out_what_is_here(setup):
    got = mail_remote.search("lunch")
    # One per account (the same message in two folders counts once), newest first.
    assert sorted((r["account"], r["uid"], r["subject"]) for r in got["results"]) == [
        ("other", 3, "Old lunch"), ("personal", 7, "Old lunch")]
    assert all(r["message_id"] != "here-1@x" for r in got["results"])
    g = setup["servers"]["david@example.com"].cmds
    assert ("select", '"[Gmail]/All Mail"', True) in g
    assert any(c[0] == "SEARCH" and c[1] == ("X-GM-RAW", '"lunch"') for c in g)
    o = setup["servers"]["me@example.org"].cmds
    assert [c[1] for c in o if c[0] == "select"] == ['"Archive"', '"INBOX"']
    assert any(c[0] == "SEARCH" and c[1] == ("CHARSET", "UTF-8", "TEXT", '"lunch"') for c in o)
    assert got["errors"] == {}


def test_non_ascii_goes_as_a_literal(setup):
    mail_remote.search("café", account="personal")
    s = [c for c in setup["servers"]["david@example.com"].cmds if c[0] == "SEARCH"][0]
    assert s[1] == ("X-GM-RAW",) and s[2] == "café".encode()


def test_one_accounts_failure_beside_the_others(setup):
    setup["servers"]["me@example.org"].fail = True
    got = mail_remote.search("lunch")
    assert [r["account"] for r in got["results"]] == ["personal"]
    assert "other" in got["errors"] and "search failed" in got["errors"]["other"]


def test_refusals(setup):
    for q in ("", "x" * 301, "a\r\nb"):
        with pytest.raises(mail_remote.Refused):
            mail_remote.search(q)
    with pytest.raises(mail_remote.Refused):
        mail_remote.search("x", account="nobody")
    with pytest.raises(mail_remote.Refused):
        mail_remote.fetch("personal", "", 7)


def test_fetch_copies_one_message_into_archive(setup):
    got = mail_remote.fetch("personal", "[Gmail]/All Mail", 7)
    assert got == {"id": "old-1@x", "fetched": True}
    files = list((setup["tmp"] / "Mail" / "personal" / "Archive" / "cur").iterdir())
    assert len(files) == 1 and files[0].read_bytes() == RAW and files[0].name.endswith(":2,S")
    assert ["tag", "+personal", "-inbox", "-unread", "--", 'id:"old-1@x"'] in setup["calls"]
    # Fetched again: already here, nothing copied twice.
    assert mail_remote.fetch("personal", "[Gmail]/All Mail", 7) == {"id": "old-1@x", "fetched": False}


def test_the_routes(server, signed_in, setup, monkeypatch):
    res, obj = call(server, "GET", "/mail/remote/search?q=lunch&account=personal", headers=AUTH)
    assert res.status == 200 and obj["results"][0]["uid"] == 7
    res, obj = call(server, "GET", "/mail/remote/search?q=", headers=AUTH)
    assert res.status == 400
    res, obj = call(server, "POST", "/mail/remote/fetch",
                    {"account": "personal", "folder": "[Gmail]/All Mail", "uid": 7}, headers=AUTH)
    assert res.status == 200 and obj["id"] == "old-1@x"
    res, obj = call(server, "POST", "/mail/remote/fetch", {"account": "nobody"}, headers=AUTH)
    assert res.status == 422
    res, _ = call(server, "GET", "/mail/remote/search?q=x")
    assert res.status in (401, 403)
    monkeypatch.setattr(contacts, "is_owner", lambda: False)
    res, _ = call(server, "GET", "/mail/remote/search?q=x", headers=AUTH)
    assert res.status == 403
