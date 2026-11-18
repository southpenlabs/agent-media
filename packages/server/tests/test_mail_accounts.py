"""Mail accounts added from Sasonica (mail_accounts.py, §6.25 "Mail
accounts"): adding (logins tested first, faked here), the config written
under agent-media's own folder only, the host's hand-made accounts listed and
never rewritten, sending through the right msmtp config, removing, syncing
(a fake mbsync and a fake notmuch that record their calls), and the settings
route's gate. No network: the IMAP and SMTP logins are stand-ins. HOME and
the state dirs are throwaway; no MEDIA_* setting leaks in."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

import pytest

from agent_media_server import contacts, features, mail_accounts, mail_send, timeline
from test_contract import AUTH, call, server, signed_in, typed  # noqa: F401
from test_mail_read import isolated  # noqa: F401

HOST_RC = """account personal
host smtp.gmail.com
from david@example.com
"""


@pytest.fixture
def logins(monkeypatch):
    """IMAP and SMTP logins that succeed (or fail when told), never dialled."""
    seen = {"imap": [], "smtp": [], "imap_fail": None, "folders": {}}

    def imap(host, port, user, pw):
        seen["imap"].append((host, port, user, pw))
        if seen["imap_fail"]:
            raise mail_accounts.Refused(seen["imap_fail"])
        return dict(seen["folders"])

    def smtp(host, port, mode, user, pw):
        seen["smtp"].append((host, port, mode, user, pw))
    monkeypatch.setattr(mail_accounts, "_test_imap", imap)
    monkeypatch.setattr(mail_accounts, "_test_smtp", smtp)
    return seen


@pytest.fixture
def nm(tmp_path, monkeypatch):
    """A notmuch that records its calls; database.path is tmp/Mail."""
    calls = []

    def fake(args, timeout):
        calls.append(args)
        if args[:3] == ["config", "get", "database.path"]:
            return str(tmp_path / "Mail") + "\n"
        if args[:1] == ["count"]:
            return "0"
        return ""
    monkeypatch.setattr(timeline, "_notmuch", fake)
    return calls


def test_add_gmail_writes_only_our_own_config(tmp_path, logins, nm):
    home = tmp_path / "home"
    (home / ".msmtprc").write_text(HOST_RC)
    got = mail_accounts.add({"provider": "gmail", "address": "Ana.B@gmail.com",
                             "password": "abcd efgh ijkl mnop"})
    assert got["name"] == "ana-b" and got["folders"]["sent"] == "[Gmail]/Sent Mail"
    assert "user" not in got
    # Gmail's app password is shown in groups of four; the spaces are not part of it.
    assert logins["imap"] == [("imap.gmail.com", 993, "Ana.B@gmail.com", "abcdefghijklmnop")]
    assert logins["smtp"][0][:3] == ("smtp.gmail.com", 587, "starttls")
    d = mail_accounts.mail_dir()
    assert oct(d.stat().st_mode & 0o777) == "0o700"
    sec = d / "secrets" / "ana-b"
    assert sec.read_text() == "abcdefghijklmnop" and oct(sec.stat().st_mode & 0o777) == "0o600"
    mb = (d / "mbsyncrc").read_text()
    assert "IMAPAccount am-ana-b" in mb and "Expunge None" in mb
    assert f'Path "{tmp_path}/Mail/ana-b/"' in mb
    assert 'Patterns "INBOX" "[Gmail]/Sent Mail" "[Gmail]/Spam"' in mb
    assert "abcdefghijklmnop" not in mb
    ms = (d / "msmtprc").read_text()
    assert oct((d / "msmtprc").stat().st_mode & 0o777) == "0o600"
    assert "account ana-b" in ms and "from Ana.B@gmail.com" in ms and "tls_starttls on" in ms
    # The host's own msmtp config is untouched.
    assert (home / ".msmtprc").read_text() == HOST_RC
    # No host notmuch config here, so agent-media writes its own.
    assert (d / "notmuch-config").read_text().count(f"path={tmp_path}/Mail") == 1


def test_special_use_folders_win_and_other_imap(tmp_path, logins, nm):
    logins["folders"] = {"sent": "Sent Items", "spam": "Junk E-mail"}
    got = mail_accounts.add({"provider": "imap", "address": "me@example.org", "password": "pw",
                             "imap_host": "mail.example.org", "smtp_host": "mail.example.org",
                             "smtp_port": 465})
    assert got["folders"] == {"inbox": "INBOX", "sent": "Sent Items", "spam": "Junk E-mail"}
    assert got["smtp"] == {"host": "mail.example.org", "port": 465, "tls": "tls"}
    assert "tls_starttls off" in mail_accounts.msmtprc().read_text()
    with pytest.raises(mail_accounts.Refused, match="servers"):
        mail_accounts.add({"provider": "imap", "address": "x@example.org", "password": "pw",
                           "imap_host": "bad host;", "smtp_host": "ok.example"})


def test_icloud_logs_in_with_the_local_part(logins, nm):
    mail_accounts.add({"provider": "icloud", "address": "ana@icloud.com", "password": "pw"})
    assert logins["imap"][0][2] == "ana"


def test_refusals_and_nothing_saved(tmp_path, logins, nm):
    (tmp_path / "home" / ".msmtprc").write_text(HOST_RC)
    for body, msg in (({"provider": "aol"}, "provider"),
                      ({"provider": "gmail", "address": "nope"}, "address"),
                      ({"provider": "gmail", "address": "a@b.co"}, "password"),
                      ({"provider": "gmail", "address": "david@example.com", "password": "x"},
                       "already")):
        with pytest.raises(mail_accounts.Refused, match=msg):
            mail_accounts.add(body)
    logins["imap_fail"] = "the mail server refused that address and password"
    with pytest.raises(mail_accounts.Refused, match="refused"):
        mail_accounts.add({"provider": "gmail", "address": "new@gmail.com", "password": "x"})
    assert mail_accounts.load() == []
    assert not (mail_accounts.mail_dir() / "secrets").exists()


def test_names_never_clash_with_the_hosts(tmp_path, logins, nm):
    (tmp_path / "home" / ".msmtprc").write_text("account ana\nfrom ana@old.example\n")
    a = mail_accounts.add({"provider": "gmail", "address": "ana@gmail.com", "password": "x"})
    b = mail_accounts.add({"provider": "fastmail", "address": "ana@fastmail.com", "password": "y"})
    assert (a["name"], b["name"]) == ("ana-2", "ana-3")


def test_listing_and_sending_through_the_right_config(tmp_path, logins, nm, monkeypatch):
    (tmp_path / "home" / ".msmtprc").write_text(HOST_RC)
    mail_accounts.add({"provider": "gmail", "address": "ana@gmail.com", "password": "x"})
    rows = mail_accounts.listing()
    assert [(r["name"], r["managed"]) for r in rows] == [("ana", True), ("personal", False)]
    assert rows[1]["label"] == "Set up on this host"
    assert mail_send.accounts() == {"david@example.com": "personal", "ana@gmail.com": "ana"}
    assert mail_send.accounts(include_managed=False) == {"david@example.com": "personal"}
    assert mail_send._rc_args("personal") == []
    assert mail_send._rc_args("ana") == ["-C", str(mail_accounts.msmtprc())]


@pytest.mark.skipif(shutil.which("msmtp") is None, reason="msmtp not installed")
def test_msmtp_reads_the_written_config(tmp_path, logins, nm):
    mail_accounts.add({"provider": "fastmail", "address": "ana@fastmail.com", "password": "x"})
    r = subprocess.run(["msmtp", "-C", str(mail_accounts.msmtprc()), "-a", "ana", "--pretend"],
                       capture_output=True, text=True, timeout=10)
    assert r.returncode == 0, r.stderr
    assert "smtp.fastmail.com" in r.stdout and "465" in r.stdout


def test_remove_keeps_mail_unless_asked(tmp_path, logins, nm):
    mail_accounts.add({"provider": "gmail", "address": "ana@gmail.com", "password": "x"})
    box = tmp_path / "Mail" / "ana" / "INBOX" / "cur"
    box.mkdir(parents=True)
    (box / "1").write_text("x")
    assert mail_accounts.remove("ana") == {"removed": "ana", "mail_deleted": False}
    assert box.exists() and not (mail_accounts.mail_dir() / "secrets" / "ana").exists()
    assert "am-ana" not in mail_accounts.mbsyncrc().read_text()
    mail_accounts.add({"provider": "gmail", "address": "ana@gmail.com", "password": "x"})
    assert mail_accounts.remove("ana", delete_mail=True)["mail_deleted"] is True
    assert not (tmp_path / "Mail" / "ana").exists() and (tmp_path / "Mail").exists()
    with pytest.raises(mail_accounts.Refused):
        mail_accounts.remove("personal")


def test_sync_fetches_indexes_and_tags(tmp_path, logins, nm, monkeypatch):
    log = tmp_path / "mbsync.log"
    fake = tmp_path / "mbsync"
    fake.write_text(f"#!{sys.executable}\nimport sys\nopen({str(log)!r}, 'a').write(' '.join(sys.argv[1:]) + '\\n')\n"
                    "sys.exit(1 if sys.argv[-1] == 'am-bo' else 0)\n")
    fake.chmod(0o755)
    monkeypatch.setenv("MEDIA_MAIL_MBSYNC", str(fake))
    assert mail_accounts.sync() == {"accounts": {}, "indexed": False}
    mail_accounts.add({"provider": "gmail", "address": "ana@gmail.com", "password": "x"})
    mail_accounts.add({"provider": "fastmail", "address": "bo@fastmail.com", "password": "y"})
    got = mail_accounts.sync()
    assert got["accounts"]["ana"] == "ok" and got["accounts"]["bo"] != "ok" and got["indexed"]
    assert log.read_text().splitlines() == [
        f"-c {mail_accounts.mbsyncrc()} -q am-ana", f"-c {mail_accounts.mbsyncrc()} -q am-bo"]
    tags = [c for c in nm if c[0] in ("new", "tag")]
    assert tags[0] == ["new", "--quiet"]
    assert ["tag", "+ana", "--", 'path:"ana/**" and not tag:ana'] in tags
    assert ["tag", "+sent", "-inbox", "-unread", "--",
            'folder:"ana/[Gmail]/Sent Mail" and tag:inbox'] in tags
    rows = {r["name"]: r for r in mail_accounts.listing()}
    assert rows["ana"]["ok"] is True and rows["bo"]["ok"] is False and rows["bo"]["error"]


def test_own_notmuch_config_only_without_the_hosts(tmp_path, monkeypatch):
    monkeypatch.delenv("MEDIA_TIMELINE_NOTMUCH")
    d = mail_accounts.mail_dir()
    d.mkdir(parents=True)
    (d / "notmuch-config").write_text("[database]\npath=/x\n")
    assert timeline._notmuch_cmd() == ["notmuch", f"--config={d / 'notmuch-config'}"]
    (tmp_path / "home" / ".notmuch-config").write_text("[database]\npath=/y\n")
    assert timeline._notmuch_cmd() == ["notmuch"]


def test_the_route_and_features(server, signed_in, logins, nm, monkeypatch):
    res, obj = call(server, "GET", "/settings/mail", headers=AUTH)
    assert res.status == 200 and obj["accounts"] == []
    assert [p["id"] for p in obj["presets"]] == ["gmail", "icloud", "fastmail", "yahoo", "imap"]
    assert set(obj["tools"]) == {"notmuch", "mbsync", "msmtp"}
    monkeypatch.setattr(mail_accounts, "sync_soon", lambda only="": None)
    res, obj = call(server, "POST", "/settings/mail",
                    {"action": "add", "provider": "gmail", "address": "ana@gmail.com",
                     "password": "x"}, headers=AUTH)
    assert res.status == 200 and obj["accounts"][0]["name"] == "ana"
    assert "x" not in json.dumps(obj["accounts"])
    res, obj = call(server, "POST", "/settings/mail", {"action": "add", "provider": "gmail",
                                                       "address": "ana@gmail.com", "password": "x"},
                    headers=AUTH)
    assert res.status == 422 and "already" in obj["error"]
    res, obj = call(server, "POST", "/settings/mail", {"action": "fly"}, headers=AUTH)
    assert res.status == 400
    res, obj = call(server, "POST", "/settings/mail", {"action": "remove", "name": "ana"},
                    headers=AUTH)
    assert res.status == 200 and obj["accounts"] == []
    assert features.detect()["mail_accounts"] is True
    res, _ = call(server, "GET", "/settings/mail")
    assert res.status in (401, 403)
    monkeypatch.setattr(contacts, "is_owner", lambda: False)
    res, obj = call(server, "GET", "/settings/mail", headers=AUTH)
    assert res.status == 403
    assert features.detect()["mail_accounts"] is False
