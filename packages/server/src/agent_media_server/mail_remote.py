"""Searching the mail that is not on this host (contract §6.25 "Older
mail"; David, 18 Nov 2026: "Is it possible to have the rest of the mail
searchable with option 1?").

Only Inbox, Sent and Spam are copied here, so a search can go on to the
provider: `GET /mail/remote/search?q=&account=` asks each account's IMAP
server (Gmail by its own search syntax, X-GM-RAW, in All Mail; any other
server by IMAP's TEXT search in its \\All folder when it has one, else its
Archive and Inbox) and lists what it finds that is not here already.
`POST /mail/remote/fetch {account, folder, uid}` copies that one message
into the account's `Archive` maildir, indexes it, and answers its notmuch
id, after which it reads, threads and replies as any other mail.

An account's login is the one its sync uses: an account added from
Sasonica (mail_accounts.py), or the host's own ~/.mbsyncrc (its User, Host,
Port and Pass or PassCmd, run as mbsync runs it). Nothing is kept but the
fetched message itself; no query or result is logged.
"""

from __future__ import annotations

import email.parser
import email.policy
import email.utils
import imaplib
import os
import re
import shlex
import socket
import ssl
import subprocess
import time
from pathlib import Path

TIMEOUT_S = 25
PER_ACCOUNT = 25
_LOCAL_FOLDER = "Archive"


class Refused(Exception):
    pass


# ── Logins ────────────────────────────────────────────────────────────────

def _mbsync_hand() -> list[dict]:
    """The host's own ~/.mbsyncrc IMAP accounts: {user, host, port, pass?,
    passcmd?, tls}."""
    home = Path.home()
    for p in (home / ".mbsyncrc", home / ".config" / "isyncrc"):
        try:
            text = p.read_text(errors="replace")
            break
        except OSError:
            continue
    else:
        return []
    out, cur = [], None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, _, val = line.partition(" ")
        val = val.strip()
        if key == "IMAPAccount":
            cur = {"host": "", "port": 993, "user": "", "tls": "IMAPS"}
            out.append(cur)
        elif cur is None:
            continue
        elif key in ("IMAPStore", "MaildirStore", "Channel", "Group"):
            cur = None
        elif key == "Host":
            cur["host"] = val
        elif key == "Port" and val.isdigit():
            cur["port"] = int(val)
        elif key == "User":
            cur["user"] = val.strip('"')
        elif key == "Pass":
            cur["pass"] = val.strip('"')
        elif key == "PassCmd":
            cur["passcmd"] = val[1:] if val.startswith("+") else val
        elif key in ("TLSType", "SSLType"):
            cur["tls"] = val
    return [a for a in out if a["host"] and a["user"]]


def _unquote(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] == '"':
        return s[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return s


def _password(a: dict) -> str:
    if a.get("pass"):
        return a["pass"]
    cmd = _unquote(a.get("passcmd") or "")
    if not cmd:
        raise Refused("no password for that account")
    try:
        r = subprocess.run(["/bin/sh", "-c", cmd], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        raise Refused("could not read that account's password") from None
    if r.returncode != 0 or not r.stdout.strip():
        raise Refused("could not read that account's password")
    return r.stdout.splitlines()[0]


def logins() -> dict[str, dict]:
    """Each account name the Mail tab knows to its IMAP login."""
    from . import mail_accounts, mail_send

    out: dict[str, dict] = {}
    for a in mail_accounts.load():
        out[a["name"]] = {"host": a["imap"]["host"], "port": a["imap"]["port"],
                          "user": a.get("user") or a["address"], "address": a["address"],
                          "passcmd": "cat " + shlex.quote(str(mail_accounts._secret(a["name"]))),
                          "tls": "IMAPS", "gmail": a.get("provider") == "gmail"}
    hand = {h["user"].lower(): h for h in _mbsync_hand()}
    for addr, name in mail_send.accounts(include_managed=False).items():
        h = hand.get(addr)
        if h and name not in out:
            out[name] = {**h, "address": addr, "gmail": "gmail" in h["host"]}
    return out


def _connect(a: dict):
    try:
        if str(a.get("tls", "IMAPS")).upper() == "IMAPS":
            conn = imaplib.IMAP4_SSL(a["host"], a["port"], timeout=TIMEOUT_S,
                                     ssl_context=ssl.create_default_context())
        else:
            conn = imaplib.IMAP4(a["host"], a["port"], timeout=TIMEOUT_S)
            conn.starttls(ssl_context=ssl.create_default_context())
        conn.login(a["user"], _password(a))
    except imaplib.IMAP4.error:
        raise Refused(f"{a['address']}: the mail server refused the login") from None
    except (OSError, ssl.SSLError, socket.timeout) as e:
        raise Refused(f"{a['address']}: could not reach the mail server ({e.__class__.__name__})") from None
    return conn


def _all_folder(conn, gmail: bool) -> list[str]:
    """Where to search: the \\All folder (Gmail's All Mail), else Archive
    and INBOX."""
    typ, rows = conn.list()
    allf, archive = None, None
    for raw in rows or []:
        line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
        m = re.match(r'\((?P<flags>[^)]*)\)\s+(?:"[^"]*"|NIL)\s+(?P<name>.+)$', line)
        if not m:
            continue
        name = _unquote(m.group("name"))
        flags = m.group("flags").lower()
        if "\\all" in flags:
            allf = name
        elif "\\archive" in flags:
            archive = name
    if allf:
        return [allf]
    return [f for f in (archive, "INBOX") if f]


def _imap_quote(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


# ── Searching ─────────────────────────────────────────────────────────────

def _local_ids(ids: list[str]) -> set[str]:
    from . import timeline

    if not ids:
        return set()
    q = " or ".join('id:"' + i.replace('"', '""') + '"' for i in ids)
    got = timeline._notmuch(["search", "--output=messages", q], 60) or ""
    return {l[3:] for l in got.splitlines() if l.startswith("id:")}


def _search_one(name: str, a: dict, q: str) -> list[dict]:
    conn = _connect(a)
    try:
        out = []
        for folder in _all_folder(conn, a.get("gmail", False)):
            typ, _ = conn.select(_imap_quote(folder), readonly=True)
            if typ != "OK":
                continue
            head = ["X-GM-RAW"] if a.get("gmail") else ["CHARSET", "UTF-8", "TEXT"]
            if q.isascii():
                typ, data = conn.uid("SEARCH", *head, _imap_quote(q))
            else:
                # imaplib sends only ASCII arguments; anything else goes as
                # a literal, which it appends after the others.
                conn.literal = q.encode("utf-8")
                typ, data = conn.uid("SEARCH", *head)
            if typ != "OK" or not data or not data[0]:
                continue
            uids = data[0].split()[-PER_ACCOUNT:]
            typ, rows = conn.uid("FETCH", b",".join(uids).decode(),
                                 "(UID INTERNALDATE BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE MESSAGE-ID)])")
            for row in rows or []:
                if not isinstance(row, tuple):
                    continue
                meta = row[0].decode("ascii", "replace")
                um = re.search(r"UID (\d+)", meta)
                h = email.parser.BytesHeaderParser(policy=email.policy.default).parsebytes(row[1])
                name_, addr = email.utils.parseaddr(str(h.get("From") or ""))
                try:
                    d = email.utils.parsedate_to_datetime(str(h.get("Date") or ""))
                    when = int(d.timestamp())
                except (TypeError, ValueError):
                    when = 0
                out.append({"account": name, "folder": folder, "uid": int(um.group(1)) if um else 0,
                            "message_id": str(h.get("Message-ID") or "").strip().strip("<>"),
                            "from": {"name": name_.strip(), "address": addr.strip().lower()},
                            "subject": str(h.get("Subject") or "").strip(), "date": when})
        return out
    finally:
        try:
            conn.logout()
        except (OSError, imaplib.IMAP4.error):
            pass


def search(q: str, account: str = "", limit: int = 50) -> dict:
    """`GET /mail/remote/search`: newest first, those already here left out
    (they are in the local results). Each account's failure is reported
    beside the results, never instead of them."""
    q = (q or "").strip()
    if not q or len(q) > 300 or "\n" in q or "\r" in q:
        raise Refused("a search of up to 300 characters, please")
    lg = logins()
    if account:
        if account not in lg:
            raise Refused("no such account, or no login for it on this host")
        lg = {account: lg[account]}
    found, errors = [], {}
    for name, a in lg.items():
        try:
            found += _search_one(name, a, q)
        except Refused as e:
            errors[name] = str(e)
        except (imaplib.IMAP4.error, OSError, ValueError) as e:
            errors[name] = f"{a['address']}: the search failed ({e.__class__.__name__})"
    here = _local_ids([f["message_id"] for f in found if f["message_id"]])
    seen: set = set()
    kept = []
    for f in found:
        key = (f["account"], f["message_id"] or f"{f['folder']}/{f['uid']}")
        if f["message_id"] in here or key in seen:
            continue
        seen.add(key)
        kept.append(f)
    found = kept
    found.sort(key=lambda f: -f["date"])
    return {"q": q, "results": found[:max(1, min(int(limit), 200))], "errors": errors,
            "accounts": sorted(lg)}


# ── Fetching one ──────────────────────────────────────────────────────────

def fetch(account: str, folder: str, uid: int) -> dict:
    """Copy one message here, into `<root>/<account>/Archive/`, index it
    and tag it with its account (not inbox, not unread). `{"id"}` for
    `GET /mail/message`."""
    from . import mail_accounts, timeline

    lg = logins()
    a = lg.get(account)
    if a is None:
        raise Refused("no such account, or no login for it on this host")
    if not folder or len(folder) > 200 or uid <= 0:
        raise Refused("expected account, folder and uid")
    conn = _connect(a)
    try:
        typ, _ = conn.select(_imap_quote(folder), readonly=True)
        if typ != "OK":
            raise Refused("no such folder")
        typ, rows = conn.uid("FETCH", str(uid), "(BODY.PEEK[])")
        raw = next((r[1] for r in rows or [] if isinstance(r, tuple)), None)
    finally:
        try:
            conn.logout()
        except (OSError, imaplib.IMAP4.error):
            pass
    if not raw:
        raise Refused("that message is gone from the server")
    h = email.parser.BytesHeaderParser(policy=email.policy.default).parsebytes(raw)
    mid = str(h.get("Message-ID") or "").strip().strip("<>")
    if mid and _local_ids([mid]):
        return {"id": mid, "fetched": False}
    box = mail_accounts.maildir_root() / account / _LOCAL_FOLDER
    for sub in ("cur", "new", "tmp"):
        (box / sub).mkdir(parents=True, exist_ok=True)
    fname = f"{int(time.time())}.{os.getpid()}_{uid}.agent-media:2,S"
    tmp = box / "tmp" / fname
    tmp.write_bytes(raw)
    tmp.replace(box / "cur" / fname)
    if timeline._notmuch(["new", "--quiet"], 300) is None:
        time.sleep(5)
        timeline._notmuch(["new", "--quiet"], 300)
    if mid:
        q = 'id:"' + mid.replace('"', '""') + '"'
        timeline._notmuch(["tag", f"+{account}", "-inbox", "-unread", "--", q], 60)
    return {"id": mid or None, "fetched": True}
