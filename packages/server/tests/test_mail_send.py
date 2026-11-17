"""Replying to an email from Sasonica (mail_send.py, §6.25 "Replying"):
who it goes to and from, threading and quoting, and the send through
msmtp. notmuch is test_mail_read's fake over made-up .eml files; msmtp is a
fake script that records its arguments and the message, so nothing is ever
sent; HOME and the state dirs are throwaway and no MEDIA_* setting of this
host leaks in (test_mail_read's `isolated`, imported so it runs here)."""

from __future__ import annotations

import base64
import email
import email.policy
import json
import sys

import pytest

from agent_media_server import contacts, mail_send
from test_contract import AUTH, call, server, signed_in, typed  # noqa: F401
from test_mail_read import PLAIN, Mail, isolated, mail  # noqa: F401

RC = """# msmtp
defaults
auth on
tls on

account personal
host smtp.gmail.com
from david@example.com
user david@example.com

account work
host smtp.gmail.com
from david@work.example

account alias : personal
from other@example.com

account default : personal
"""

FROM_ME = """From: David <david@example.com>
To: Lionel Smith <lionel@example.com>, ana@example.com
Cc: bo@example.com
Subject: Re: Lunch on Friday?
Date: Mon, 19 Oct 2026 11:00:00 +1100
Message-ID: <mine-1@example.com>
References: <plain-1@example.com>
In-Reply-To: <plain-1@example.com>
Content-Type: text/plain; charset=utf-8

Yes, see you there.
"""

TO_WORK = """From: Boss <boss@work.example>
To: Team <team@work.example>, david@work.example
Reply-To: desk@work.example
Subject: Roster
Date: Tue, 20 Oct 2026 09:00:00 +1100
Message-ID: <work-1@work.example>
Content-Type: text/html; charset=utf-8

<p>The <b>roster</b> is up.</p>
"""


@pytest.fixture
def smtp(tmp_path, monkeypatch):
    """msmtp's config and a fake msmtp; `.sent` lists (args, message)."""
    rc = tmp_path / "home" / ".msmtprc"
    rc.write_text(RC)
    log = tmp_path / "sent.jsonl"
    script = tmp_path / "msmtp"
    script.write_text(f"""#!{sys.executable}
import base64, json, os, sys
data = base64.b64encode(sys.stdin.buffer.read()).decode()
if os.environ.get("FAKE_MSMTP_FAIL"):
    print("msmtp: server message: 535 bad credentials", file=sys.stderr)
    sys.exit(77)
with open({str(log)!r}, "a") as f:
    f.write(json.dumps([sys.argv[1:], data]) + "\\n")
""")
    script.chmod(0o755)
    monkeypatch.setenv("MEDIA_MAIL_SENDMAIL", str(script))

    class S:
        @property
        def sent(self):
            if not log.exists():
                return []
            return [(a, email.message_from_bytes(base64.b64decode(m), policy=email.policy.default))
                    for a, m in (json.loads(l) for l in log.read_text().splitlines())]
    return S()


def test_accounts_from_msmtprc(smtp):
    assert mail_send.accounts() == {"david@example.com": "personal",
                                    "david@work.example": "work",
                                    "other@example.com": "alias"}


def test_reply_goes_to_the_sender_from_the_account_it_came_to(mail, smtp):
    mid = mail.add("plain-1@example.com", PLAIN)
    d = mail_send.draft(mid)
    assert d["from"] == {"name": "David", "address": "david@example.com"}
    assert d["to"] == [{"name": "Lionel Smith", "address": "lionel@example.com"}]
    assert d["cc"] == [] and d["subject"] == "Re: Lunch on Friday?"
    every = mail_send.draft(mid, everyone=True)
    assert [c["address"] for c in every["cc"]] == ["ana@example.com"]


def test_reply_to_and_another_account(mail, smtp):
    mid = mail.add("work-1@work.example", TO_WORK)
    d = mail_send.draft(mid)
    assert d["from"]["address"] == "david@work.example"
    assert [t["address"] for t in d["to"]] == ["desk@work.example"]
    every = mail_send.draft(mid, everyone=True)
    # His own address never comes back to him.
    assert [c["address"] for c in every["cc"]] == ["team@work.example"]
    other = mail_send.draft(mid, want_from="other@example.com")
    assert other["from"]["address"] == "other@example.com"
    with pytest.raises(mail_send.Refused):
        mail_send.draft(mid, want_from="nobody@example.com")


def test_reply_to_his_own_mail_goes_where_it_went(mail, smtp):
    mid = mail.add("mine-1@example.com", FROM_ME)
    d = mail_send.draft(mid)
    assert d["from"]["address"] == "david@example.com"
    assert [t["address"] for t in d["to"]] == ["lionel@example.com"]
    assert d["subject"] == "Re: Lunch on Friday?"  # no "Re: Re:"
    every = mail_send.draft(mid, everyone=True)
    assert [t["address"] for t in every["to"]] == ["lionel@example.com", "ana@example.com"]
    assert [c["address"] for c in every["cc"]] == ["bo@example.com"]


def test_send_threads_quotes_and_uses_the_account(mail, smtp):
    mid = mail.add("plain-1@example.com", PLAIN)
    out = mail_send.send(mid, "Friday works.\r\nSee you.", everyone=True)
    assert out["to"][0]["address"] == "lionel@example.com" and out["message_id"]
    (args, m), = smtp.sent
    assert args == ["-a", "personal", "-t"]
    assert m["In-Reply-To"] == "<plain-1@example.com>"
    assert m["References"] == "<plain-1@example.com>"
    assert m["To"] == "Lionel Smith <lionel@example.com>"
    assert m["Cc"] == "ana@example.com"
    assert m["Message-ID"].endswith("@example.com>")
    body = m.get_content().replace("\r\n", "\n")
    assert body.startswith("Friday works.\nSee you.\n\nOn Mon, 19 Oct 2026")
    assert "> Friday at the café?" in body


def test_send_references_chain_and_html_quote(mail, smtp):
    mail.add("mine-1@example.com", FROM_ME)
    mail_send.send("mine-1@example.com", "One more thing")
    mail.add("work-1@work.example", TO_WORK)
    mail_send.send("work-1@work.example", "Thanks")
    (_, a), (args, b) = smtp.sent
    assert a["References"] == "<plain-1@example.com> <mine-1@example.com>"
    assert args[:2] == ["-a", "work"]
    assert "> The roster is up." in b.get_content().replace("\r\n", "\n")


def test_refusals(mail, smtp, monkeypatch, tmp_path):
    mid = mail.add("plain-1@example.com", PLAIN)
    with pytest.raises(mail_send.Refused):
        mail_send.send(mid, "   ")
    with pytest.raises(mail_send.Refused):
        mail_send.send(mid, "x" * (mail_send.TEXT_MAX + 1))
    monkeypatch.setenv("FAKE_MSMTP_FAIL", "1")
    with pytest.raises(RuntimeError, match="535 bad credentials"):
        mail_send.send(mid, "hello")
    (tmp_path / "home" / ".msmtprc").unlink()
    with pytest.raises(mail_send.Refused, match="no mail account"):
        mail_send.draft(mid)
    assert smtp.sent == []


def test_the_routes(server, signed_in, mail, smtp, monkeypatch):
    mail.add("plain-1@example.com", PLAIN)
    res, obj = call(server, "GET", "/mail/reply?id=plain-1%40example.com&all=1", headers=AUTH)
    assert res.status == 200 and obj["subject"] == "Re: Lunch on Friday?"
    assert obj["accounts"] == ["david@example.com", "david@work.example", "other@example.com"]
    assert smtp.sent == []
    res, obj = call(server, "POST", "/mail/reply",
                    {"id": "plain-1@example.com", "text": "Yes!"}, headers=AUTH)
    assert res.status == 200 and obj["sent"] and obj["text"] == "Yes!"
    assert len(smtp.sent) == 1
    res, _ = call(server, "POST", "/mail/reply", {"id": "plain-1@example.com"}, headers=AUTH)
    assert res.status == 400
    res, _ = call(server, "POST", "/mail/reply", {"id": "nobody@x", "text": "hi"}, headers=AUTH)
    assert res.status == 404
    res, _ = call(server, "POST", "/mail/reply",
                  {"id": "plain-1@example.com", "text": " "}, headers=AUTH)
    assert res.status == 422
    monkeypatch.setenv("FAKE_MSMTP_FAIL", "1")
    res, obj = call(server, "POST", "/mail/reply",
                    {"id": "plain-1@example.com", "text": "hi"}, headers=AUTH)
    assert res.status == 502 and "535" in obj["error"]
    res, _ = call(server, "POST", "/mail/reply", {"id": "plain-1@example.com", "text": "hi"})
    assert res.status in (401, 403)
    monkeypatch.setattr(contacts, "is_owner", lambda: False)
    res, obj = call(server, "POST", "/mail/reply",
                    {"id": "plain-1@example.com", "text": "hi"}, headers=AUTH)
    assert res.status == 403 and "owner" in obj["error"]
    assert len(smtp.sent) == 1
