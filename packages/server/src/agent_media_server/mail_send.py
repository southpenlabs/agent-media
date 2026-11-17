"""Replying to an email from Sasonica (contract §6.25 "Replying", step 2 of
docs/proposals/2026-10-20-mail-in-sasonica.md).

`GET /mail/reply?id=<notmuch id>[&all=1]` answers what a reply would be
(From, To, Cc, Subject) so the app can show it before anything is sent;
`POST /mail/reply {"id", "text", "all"?, "from"?}` sends it through msmtp,
which this host already has set up for its accounts (David, 20 Oct 2026).

From is the account the mail came to: one of David's own addresses among
its Delivered-To, To and Cc that msmtp has an account for, else the account
whose maildir holds it (mbsync's channel and msmtp's account share a name,
as on red5 and red2); `from` picks another of msmtp's accounts. A reply to
his own mail goes to whom it went to. Reply all adds the other To and Cc
people, never his own addresses. The reply threads by In-Reply-To and
References, its subject gets "Re: ", and the original is quoted below.

Nothing is kept or logged: the reply goes to msmtp's standard input and is
dropped. Gmail files its own copy in Sent Mail, which the next sync brings
here; `MEDIA_MAIL_AFTER_SEND` (a command, e.g. a mail sync) is started
after a send so that is soon.
"""

from __future__ import annotations

import email.message
import email.policy
import email.utils
import os
import re
import shlex
import subprocess
import time
from pathlib import Path

#: A reply's own words at most, and the quoted original's.
TEXT_MAX = 256 * 1024
QUOTE_MAX = 64 * 1024
#: How long msmtp may take.
SEND_TIMEOUT_S = 90


class Refused(Exception):
    """A reply that cannot be made as asked (422)."""


# ── msmtp's accounts ──────────────────────────────────────────────────────

def _msmtprc() -> Path | None:
    """msmtp's own config, where msmtp looks for it."""
    env = (os.environ.get("MEDIA_MAIL_MSMTPRC") or "").strip()
    if env:
        return Path(env).expanduser()
    home = Path.home()
    xdg = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config")
    for p in (home / ".msmtprc", xdg / "msmtp" / "config"):
        if p.is_file():
            return p
    return None


def accounts() -> dict[str, str]:
    """msmtp's accounts, each `from` address (lower case) to its name; an
    account inheriting another's (`account x : y`) takes its `from` when it
    has none of its own."""
    p = _msmtprc()
    if p is None:
        return {}
    try:
        lines = p.read_text(errors="replace").splitlines()
    except OSError:
        return {}
    froms: dict[str, str] = {}
    parents: dict[str, str] = {}
    order: list[str] = []
    cur = None
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, _, val = line.partition(" ")
        val = val.strip()
        if key == "account":
            name, _, parent = val.partition(":")
            cur = name.strip()
            if cur and cur != "default":
                order.append(cur)
                if parent.strip():
                    parents[cur] = parent.strip().split(",")[0].strip()
        elif key == "from" and cur:
            froms[cur] = val.strip().strip('"').lower()
    out: dict[str, str] = {}
    for name in order:
        addr, seen, n = froms.get(name), set(), name
        while not addr and n in parents and n not in seen:
            seen.add(n)
            n = parents[n]
            addr = froms.get(n)
        if addr and "@" in addr and "*" not in addr and addr not in out:
            out[addr] = name
    return out


# ── Building the reply ────────────────────────────────────────────────────

def _pairs(*values) -> list[tuple[str, str]]:
    out = []
    for name, addr in email.utils.getaddresses([str(v) for v in values if v]):
        addr = addr.strip()
        if "@" in addr:
            out.append((name.strip(), addr))
    return out


def _subject(s: str) -> str:
    s = re.sub(r"\s+", " ", s or "").strip()
    return s if re.match(r"(?i)re\s*:", s) else ("Re: " + s if s else "Re:")


def _from_account(msg, path: str, accts: dict[str, str], mine: set[str],
                  want: str = "") -> str:
    """The address the reply is sent from (see the module's note). Refused
    when none of msmtp's accounts fits."""
    if want:
        w = want.strip().lower()
        if w not in accts:
            raise Refused("that address has no account to send from")
        return w
    for _, a in _pairs(msg.get("From")):
        if a.lower() in accts and a.lower() in mine:
            # His own mail: sent again from where it was sent.
            return a.lower()
    heads = msg.get_all("Delivered-To", []) + msg.get_all("X-Original-To", [])
    for _, a in _pairs(*heads) + _pairs(msg.get("To"), msg.get("Cc")):
        if a.lower() in accts:
            return a.lower()
    parts = set(Path(path).parts)
    for addr, name in accts.items():
        if name in parts:
            return addr
    raise Refused("could not tell which account this mail came to")


def _recipients(msg, sender: str, mine: set[str], everyone: bool):
    """(to, cc) as (name, address) pairs, his own addresses left out."""
    own = mine | {sender}
    frm = _pairs(msg.get("From"))
    if frm and frm[0][1].lower() in own:
        # A reply to his own mail goes to whom it went to.
        to = _pairs(msg.get("To"))
        cc = _pairs(msg.get("Cc")) if everyone else []
        if not everyone:
            to = to[:1] if to else []
    else:
        to = _pairs(msg.get("Reply-To")) or frm
        cc = _pairs(msg.get("To"), msg.get("Cc")) if everyone else []
    seen: set[str] = set()

    def keep(pairs):
        out = []
        for n, a in pairs:
            k = a.lower()
            if k in own or k in seen:
                continue
            seen.add(k)
            out.append((n, a))
        return out

    return keep(to), keep(cc)


def _fmt(pairs) -> str:
    return ", ".join(email.utils.formataddr(p) for p in pairs)


def draft(mid: str, *, everyone: bool = False, want_from: str = "") -> dict:
    """`GET /mail/reply`'s answer: who the reply would go to and from, and
    its subject. NotFound (mail_read), Refused, RuntimeError (notmuch)."""
    _, _, out = _prepare(mid, everyone=everyone, want_from=want_from)
    return out


def _prepare(mid: str, *, everyone: bool, want_from: str):
    from . import mail_read, timeline

    path = mail_read._file(mid)
    with open(path, "rb") as f:
        import email.parser
        msg = email.parser.BytesParser(policy=email.policy.default).parse(f)
    accts = accounts()
    if not accts:
        raise Refused("this host has no mail account to send from (msmtp)")
    mine = timeline.mail_self() | set(accts)
    sender = _from_account(msg, path, accts, mine, want_from)
    to, cc = _recipients(msg, sender, mine, everyone)
    if not to and cc:
        to, cc = cc[:1], cc[1:]
    if not to:
        raise Refused("nobody to reply to")
    sender_name = ""
    for n, a in _pairs(msg.get("To"), msg.get("Cc"), msg.get("From")):
        if a.lower() == sender and n:
            sender_name = n
            break
    out = {"id": mid,
           "from": {"name": sender_name, "address": sender},
           "to": [{"name": n, "address": a} for n, a in to],
           "cc": [{"name": n, "address": a} for n, a in cc],
           "subject": _subject(str(msg.get("Subject") or "")),
           "accounts": sorted(accts)}
    return msg, (sender_name, sender, to, cc), out


def _quote(msg) -> str:
    """The original, quoted as mail clients do: "On <date>, <name> wrote:"
    then each line behind "> "."""
    from . import mail_read

    plain, rich = mail_read._bodies(msg)
    body = plain if plain and plain.strip() else mail_read.html_text(rich or "")
    body = body.strip()[:QUOTE_MAX]
    if not body:
        return ""
    who = _pairs(msg.get("From"))
    name = (who[0][0] or who[0][1]) if who else "someone"
    when = str(msg.get("Date") or "").strip()
    head = f"On {when}, {name} wrote:" if when else f"{name} wrote:"
    quoted = "\n".join(("> " + l) if l.strip() else ">" for l in body.splitlines())
    return head + "\n" + quoted


def build(mid: str, text: str, *, everyone: bool = False,
          want_from: str = "") -> tuple[email.message.EmailMessage, dict, str]:
    """The reply as a message, `draft`'s answer for it, and msmtp's account
    name. Refused for empty or oversized words."""
    text = (text or "").replace("\r\n", "\n").rstrip()
    if not text.strip():
        raise Refused("the reply has no words")
    if len(text) > TEXT_MAX:
        raise Refused("the reply is too long")
    orig, (sname, sender, to, cc), out = _prepare(mid, everyone=everyone,
                                                    want_from=want_from)
    m = email.message.EmailMessage(policy=email.policy.SMTP)
    m["From"] = email.utils.formataddr((sname, sender))
    m["To"] = _fmt(to)
    if cc:
        m["Cc"] = _fmt(cc)
    m["Subject"] = out["subject"]
    m["Date"] = email.utils.formatdate(localtime=True)
    m["Message-ID"] = email.utils.make_msgid(domain=sender.partition("@")[2] or None)
    orig_id = str(orig.get("Message-ID") or "").strip()
    if orig_id:
        m["In-Reply-To"] = orig_id
        refs = " ".join(str(orig.get("References") or "").split())
        m["References"] = (refs + " " + orig_id).strip() if orig_id not in refs else refs
    quote = _quote(orig)
    m.set_content(text + ("\n\n" + quote if quote else "") + "\n")
    out["message_id"] = str(m["Message-ID"]).strip("<>")
    out["date"] = int(time.time())
    out["text"] = text
    return m, out, accounts()[sender]


# ── Sending ───────────────────────────────────────────────────────────────

def _sendmail() -> list[str]:
    cmd = (os.environ.get("MEDIA_MAIL_SENDMAIL") or "").strip()
    return shlex.split(cmd) if cmd else ["msmtp"]


def send(mid: str, text: str, *, everyone: bool = False, want_from: str = "") -> dict:
    """Send the reply through msmtp (`-a <account> -t`, the message on
    standard input). RuntimeError with msmtp's last line when it fails."""
    m, out, account = build(mid, text, everyone=everyone, want_from=want_from)
    cmd = _sendmail() + ["-a", account, "-t"]
    try:
        p = subprocess.run(cmd, input=m.as_bytes(), capture_output=True,
                           timeout=SEND_TIMEOUT_S)
    except FileNotFoundError:
        raise RuntimeError("msmtp is not installed on this host") from None
    except subprocess.TimeoutExpired:
        raise RuntimeError("the mail server did not answer in time") from None
    if p.returncode != 0:
        last = (p.stderr or b"").decode(errors="replace").strip().splitlines()
        raise RuntimeError("not sent: " + (last[-1][:300] if last else f"msmtp exited {p.returncode}"))
    _after_send()
    return out


def _after_send() -> None:
    cmd = (os.environ.get("MEDIA_MAIL_AFTER_SEND") or "").strip()
    if not cmd:
        return
    try:
        subprocess.Popen(shlex.split(cmd), stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    except OSError:
        pass
