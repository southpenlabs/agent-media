"""Mail accounts added from Sasonica (contract §6.25 "Mail accounts";
docs/proposals/2026-10-20-mail-in-sasonica.md "Setup"; David, 18 Nov 2026:
"Okay, let's do it", Gmail and any IMAP, Inbox/Sent/Spam kept, the host's
own hand-made accounts shown and left alone, the owner only for now).

`Settings → Mail → Add account` takes a provider (a preset or any IMAP
server), the address and an app password, logs in to IMAP and SMTP to test
them, finds the Sent and Spam folders (IMAP's special-use flags, else the
preset's names), and only then saves. Everything it writes is its own, under
`<config>/agent-media/mail/` (0700):

    accounts.json     the accounts: name, address, provider, servers, folders
    secrets/<name>    each app password (0600), read by mbsync and msmtp
    mbsyncrc          mbsync's config for these accounts (generated)
    msmtprc           msmtp's (generated, 0600 as msmtp requires)
    notmuch-config    only when the host has no notmuch config of its own

Hand-made config (~/.mbsyncrc, ~/.msmtprc, the host's notmuch) is never
rewritten: those accounts are listed as `managed: false`, read only.

Mail goes to `<maildir root>/<name>/`, the root being notmuch's
database.path (else `MEDIA_MAIL_DIR`, else ~/Mail), so it joins the one
index the Mail tab reads. Only Inbox, Sent and Spam are copied; nothing is
ever deleted on the server (`Expunge None`). `sync()` (the scheduler's
`mail-sync`, every 10 minutes; `media mail sync`) runs mbsync per account,
then `notmuch new`, then tags each new message with its account and moves
Sent and Spam out of the inbox.
"""

from __future__ import annotations

import imaplib
import json
import os
import re
import shlex
import shutil
import smtplib
import ssl
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Literal, Optional, TypedDict, cast


# ── The records (typed: docs/proposals/2026-11-19-typed-records-and-settings.md) ──

class Imap(TypedDict):
    host: str
    port: int


class Smtp(TypedDict):
    host: str
    port: int
    tls: Literal["tls", "starttls"]


class Folders(TypedDict, total=False):
    inbox: str
    sent: str
    spam: str


class _AccountBase(TypedDict):
    name: str            # the slug: secrets/<name>, <maildir root>/<name>/, am-<name>
    address: str
    provider: str        # a PRESETS key


class Account(_AccountBase, total=False):
    """One entry of accounts.json."""

    user: str            # the login, when it is not the address
    display_name: str
    imap: Imap
    smtp: Smtp
    folders: Folders
    added: int


class SyncState(TypedDict):
    """mail-sync.json, by account name: the last sync."""

    at: int
    ok: bool
    error: str


class Listed(TypedDict, total=False):
    """An account as Settings → Mail lists it (contract §6.25)."""

    name: str
    address: str
    provider: Optional[str]
    label: str
    managed: bool
    added: Optional[int]
    synced: Optional[int]
    ok: Optional[bool]
    error: Optional[str]


class Preset(TypedDict, total=False):
    label: str
    imap: Optional[tuple[str, int]]
    smtp: Optional[tuple[str, int, Literal["tls", "starttls"]]]
    sent: str
    spam: str
    help: str
    note: str
    user: Literal["local"]    # log in with the address's local part

#: Presets: servers and the folder names to fall back on when the server
#: does not mark its special-use folders. Outlook.com is not here: Microsoft
#: closed app-password IMAP for personal accounts in 2024 (Graph, later).
PRESETS: dict[str, Preset] = {
    "gmail": {"label": "Gmail", "imap": ("imap.gmail.com", 993),
              "smtp": ("smtp.gmail.com", 587, "starttls"),
              "sent": "[Gmail]/Sent Mail", "spam": "[Gmail]/Spam",
              "help": "https://myaccount.google.com/apppasswords",
              "note": "Needs 2-Step Verification on, then an app password."},
    "icloud": {"label": "iCloud Mail", "imap": ("imap.mail.me.com", 993),
               "smtp": ("smtp.mail.me.com", 587, "starttls"),
               "sent": "Sent Messages", "spam": "Junk",
               "help": "https://support.apple.com/en-us/102654",
               "note": "An app-specific password from your Apple Account.",
               "user": "local"},
    "fastmail": {"label": "Fastmail", "imap": ("imap.fastmail.com", 993),
                 "smtp": ("smtp.fastmail.com", 465, "tls"),
                 "sent": "Sent", "spam": "Spam",
                 "help": "https://www.fastmail.help/hc/en-us/articles/360058752854",
                 "note": "An app password with IMAP and SMTP access."},
    "yahoo": {"label": "Yahoo Mail", "imap": ("imap.mail.yahoo.com", 993),
              "smtp": ("smtp.mail.yahoo.com", 465, "tls"),
              "sent": "Sent", "spam": "Bulk",
              "help": "https://help.yahoo.com/kb/SLN15241.html",
              "note": "An app password from Account security."},
    "imap": {"label": "Other (IMAP)", "imap": None, "smtp": None,
             "sent": "Sent", "spam": "Junk", "help": "", "note":
             "Your provider's IMAP and SMTP servers and an app password."},
}

LOGIN_TIMEOUT_S = 25
SYNC_TIMEOUT_S = 900
_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,31}")
_LOCK = threading.Lock()


class Refused(Exception):
    """An account that cannot be added or changed as asked (400/422)."""


# ── Where things live ─────────────────────────────────────────────────────

def mail_dir() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "agent-media" / "mail"


def _secret(name: str) -> Path:
    return mail_dir() / "secrets" / name


def mbsyncrc() -> Path:
    return mail_dir() / "mbsyncrc"


def msmtprc() -> Path:
    return mail_dir() / "msmtprc"


def own_notmuch_config() -> Path:
    return mail_dir() / "notmuch-config"


def host_has_notmuch_config() -> bool:
    """The host's own notmuch config (NOTMUCH_CONFIG, ~/.notmuch-config or
    $XDG_CONFIG_HOME/notmuch/default/config)."""
    env = os.environ.get("NOTMUCH_CONFIG")
    if env:
        return Path(env).expanduser().is_file()
    home = Path.home()
    xdg = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config")
    return (home / ".notmuch-config").is_file() or (xdg / "notmuch" / "default" / "config").is_file()


def maildir_root() -> Path:
    """Where mail lives: notmuch's database.path, else MEDIA_MAIL_DIR, else
    ~/Mail."""
    from . import timeline

    got = timeline._notmuch(["config", "get", "database.path"], 15)
    if got and got.strip():
        return Path(got.strip()).expanduser()
    env = (os.environ.get("MEDIA_MAIL_DIR") or "").strip()
    return Path(env).expanduser() if env else Path.home() / "Mail"


def tools() -> dict[str, bool]:
    return {t: shutil.which(t) is not None for t in ("notmuch", "mbsync", "msmtp")}


# ── The list ──────────────────────────────────────────────────────────────

def load() -> list[Account]:
    try:
        got = json.loads((mail_dir() / "accounts.json").read_text())
    except (OSError, ValueError):
        return []
    return [cast(Account, a) for a in got if isinstance(a, dict) and a.get("name")] \
        if isinstance(got, list) else []


def _save(accts: list[Account]) -> None:
    d = mail_dir()
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    tmp = d / "accounts.json.tmp"
    tmp.write_text(json.dumps(accts, indent=1))
    os.chmod(tmp, 0o600)
    tmp.replace(d / "accounts.json")
    _write_configs(accts)


def _state_path() -> Path:
    base = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
    return base / "agent-media" / "mail-sync.json"


def _state() -> dict[str, SyncState]:
    try:
        got = json.loads(_state_path().read_text())
        return got if isinstance(got, dict) else {}
    except (OSError, ValueError):
        return {}


def _record(name: str, ok: bool, error: str = "") -> None:
    st = _state()
    st[name] = {"at": int(time.time()), "ok": ok, "error": error[:300]}
    p = _state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(st))


def listing() -> list[Listed]:
    """Every account the Mail tab knows: added here (`managed`) first, then
    the host's own msmtp accounts, read only."""
    from . import mail_send

    st = _state()
    out: list[Listed] = []
    mine: set[str] = set()
    for a in load():
        s: SyncState | dict = st.get(a["name"]) or {}
        out.append({"name": a["name"], "address": a["address"],
                    "provider": a.get("provider") or "imap",
                    "label": PRESETS.get(a.get("provider") or "imap", {}).get("label", "IMAP"),
                    "managed": True, "added": a.get("added"),
                    "synced": s.get("at"), "ok": s.get("ok"), "error": s.get("error") or None})
        mine.add(a["name"])
    for addr, name in mail_send.accounts(include_managed=False).items():
        if name not in mine:
            out.append({"name": name, "address": addr, "provider": None,
                        "label": "Set up on this host", "managed": False})
    return out


# ── Adding: test, then save ───────────────────────────────────────────────

def _slug(address: str, taken: set[str]) -> str:
    local, _, domain = address.partition("@")
    base = re.sub(r"[^a-z0-9-]+", "-", local.lower()).strip("-")[:24] or "mail"
    if not base[0].isalnum():
        base = "m" + base
    name, n = base, 2
    while name in taken or name in ("sent", "drafts", "inbox", "spam", "new"):
        name, n = f"{base[:28]}-{n}", n + 1
    return name


def _imap_folders(conn) -> dict[str, str]:
    """The server's special-use folders (RFC 6154): {"sent": …, "spam": …}."""
    out: dict[str, str] = {}
    typ, rows = conn.list()
    if typ != "OK":
        return out
    for raw in rows or []:
        line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
        m = re.match(r'\((?P<flags>[^)]*)\)\s+(?:"[^"]*"|NIL)\s+(?P<name>.+)$', line)
        if not m:
            continue
        flags = m.group("flags").lower()
        name = m.group("name").strip()
        if name.startswith('"') and name.endswith('"'):
            name = name[1:-1].replace('\\"', '"')
        if "\\sent" in flags:
            out.setdefault("sent", name)
        if "\\junk" in flags:
            out.setdefault("spam", name)
    return out


def _test_imap(host: str, port: int, user: str, password: str) -> dict[str, str]:
    try:
        conn = imaplib.IMAP4_SSL(host, port, timeout=LOGIN_TIMEOUT_S,
                                 ssl_context=ssl.create_default_context())
    except (OSError, ssl.SSLError) as e:
        raise Refused(f"could not reach {host}:{port} ({e.__class__.__name__})") from None
    try:
        try:
            conn.login(user, password)
        except imaplib.IMAP4.error as e:
            raise Refused("the mail server refused that address and password"
                          + (f" ({str(e)[:120]})" if str(e) else "")) from None
        return _imap_folders(conn)
    finally:
        try:
            conn.logout()
        except (OSError, imaplib.IMAP4.error):
            pass


def _test_smtp(host: str, port: int, mode: str, user: str, password: str) -> None:
    ctx = ssl.create_default_context()
    try:
        if mode == "tls":
            s: smtplib.SMTP = smtplib.SMTP_SSL(host, port, timeout=LOGIN_TIMEOUT_S, context=ctx)
        else:
            s = smtplib.SMTP(host, port, timeout=LOGIN_TIMEOUT_S)
            s.starttls(context=ctx)
    except (OSError, smtplib.SMTPException, ssl.SSLError) as e:
        raise Refused(f"could not reach {host}:{port} to send ({e.__class__.__name__})") from None
    try:
        s.login(user, password)
    except smtplib.SMTPAuthenticationError:
        raise Refused("the sending server refused that address and password") from None
    except smtplib.SMTPException as e:
        raise Refused(f"the sending server said no ({str(e)[:120]})") from None
    finally:
        try:
            s.quit()
        except (OSError, smtplib.SMTPException):
            pass


def add(body: dict, *, test: bool = True) -> dict:
    """Add an account: `{provider, address, password, display_name?,
    imap_host?, imap_port?, smtp_host?, smtp_port?, smtp_tls?, user?}`.
    Logs in to both servers first; Refused with what went wrong."""
    from . import mail_send

    provider = str(body.get("provider") or "").strip().lower()
    if provider not in PRESETS:
        raise Refused(f"provider is one of {', '.join(PRESETS)}")
    p = PRESETS[provider]
    address = str(body.get("address") or "").strip()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", address):
        raise Refused("an email address, please")
    password = str(body.get("password") or "").replace(" ", "") if provider == "gmail" \
        else str(body.get("password") or "")
    if not password or "\n" in password:
        raise Refused("the app password, please")
    if any(a["address"].lower() == address.lower() for a in load()) or \
            address.lower() in mail_send.accounts(include_managed=False):
        raise Refused("that address is already set up")
    smtp_tls: Literal["tls", "starttls"]
    preset_imap, preset_smtp = p.get("imap"), p.get("smtp")
    if preset_imap and preset_smtp:
        imap_host, imap_port = preset_imap
        smtp_host, smtp_port, smtp_tls = preset_smtp
    else:
        imap_host = str(body.get("imap_host") or "").strip()
        smtp_host = str(body.get("smtp_host") or "").strip()
        try:
            imap_port = int(body.get("imap_port") or 993)
            smtp_port = int(body.get("smtp_port") or 587)
        except (TypeError, ValueError):
            raise Refused("ports are numbers") from None
        smtp_tls = "tls" if (str(body.get("smtp_tls") or "") == "tls" or smtp_port == 465) else "starttls"
        for h in (imap_host, smtp_host):
            if not re.fullmatch(r"[A-Za-z0-9.-]{1,253}", h or ""):
                raise Refused("the IMAP and SMTP servers' names, please")
    user = str(body.get("user") or "").strip() or (
        address.partition("@")[0] if p.get("user") == "local" else address)
    found = _test_imap(imap_host, imap_port, user, password) if test else {}
    if test:
        _test_smtp(smtp_host, smtp_port, smtp_tls, user, password)
    with _LOCK:
        accts = load()
        taken = {a["name"] for a in accts} | set(mail_send.accounts(include_managed=False).values())
        name = _slug(address, taken)
        acct: Account = {"name": name, "address": address, "user": user, "provider": provider,
                "display_name": str(body.get("display_name") or "").strip()[:80],
                "imap": {"host": imap_host, "port": imap_port},
                "smtp": {"host": smtp_host, "port": smtp_port, "tls": smtp_tls},
                "folders": {"inbox": "INBOX", "sent": found.get("sent") or p["sent"],
                            "spam": found.get("spam") or p["spam"]},
                "added": int(time.time())}
        sec = _secret(name)
        sec.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(sec.parent, 0o700)
        fd = os.open(sec, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(password)
        accts.append(acct)
        _save(accts)
    return {k: v for k, v in acct.items() if k != "user"}


def remove(name: str, *, delete_mail: bool = False) -> dict:
    """Remove an account added here (a hand-made one is refused). Its mail
    stays on disk unless `delete_mail`; nothing on the provider changes."""
    from . import timeline

    with _LOCK:
        accts = load()
        acct = next((a for a in accts if a["name"] == name), None)
        if acct is None:
            raise Refused("no such account added here (one set up on this host is changed there)")
        accts = [a for a in accts if a["name"] != name]
        _save(accts)
        try:
            _secret(name).unlink()
        except OSError:
            pass
    deleted = False
    if delete_mail and _NAME.fullmatch(name):
        root = maildir_root()
        d = root / name
        if d.is_dir() and d.resolve().parent == root.resolve():
            shutil.rmtree(d, ignore_errors=True)
            timeline._notmuch(["new", "--quiet"], SYNC_TIMEOUT_S)
            deleted = True
    return {"removed": name, "mail_deleted": deleted}


# ── The generated config ──────────────────────────────────────────────────

def _q(s: str) -> str:
    """A double-quoted string for mbsync and msmtp."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _write_configs(accts: list[Account]) -> None:
    root = maildir_root()
    mb, ms = ["# Written by agent-media (mail_accounts.py); changes are overwritten.", ""], [
        "# Written by agent-media (mail_accounts.py); changes are overwritten.",
        "defaults", "auth on", "tls on", "tls_trust_file system", "logfile none", ""]
    for a in accts:
        n, sec = a["name"], str(_secret(a["name"]))
        cat = "cat " + shlex.quote(sec)
        box = root / n
        f = a.get("folders") or {}
        pats = " ".join(_q(x) for x in (f.get("inbox") or "INBOX", f.get("sent"), f.get("spam")) if x)
        mb += [f"IMAPAccount am-{n}", f"Host {a['imap']['host']}", f"Port {a['imap']['port']}",
               f"User {_q(a.get('user') or a['address'])}", f"PassCmd {_q(cat)}",
               "TLSType IMAPS", "",
               f"IMAPStore am-{n}-far", f"Account am-{n}", "",
               f"MaildirStore am-{n}-near", f"Path {_q(str(box) + '/')}",
               f"Inbox {_q(str(box / 'INBOX'))}", "SubFolders Verbatim", "",
               f"Channel am-{n}", f"Far :am-{n}-far:", f"Near :am-{n}-near:",
               f"Patterns {pats}", "Create Near", "Expunge None", "SyncState *",
               "CopyArrivalDate yes", ""]
        sm = a["smtp"]
        ms += [f"account {n}", f"host {sm['host']}", f"port {sm['port']}",
               f"tls_starttls {'off' if sm.get('tls') == 'tls' else 'on'}",
               f"from {a['address']}", f"user {a.get('user') or a['address']}",
               f"passwordeval {_q(cat)}", ""]
    d = mail_dir()
    d.mkdir(parents=True, exist_ok=True)
    for path, lines in ((mbsyncrc(), mb), (msmtprc(), ms)):
        tmp = path.with_suffix(".tmp")
        tmp.write_text("\n".join(lines))
        os.chmod(tmp, 0o600)
        tmp.replace(path)
    if accts and not host_has_notmuch_config():
        _write_notmuch_config(root, [a["address"] for a in accts])


def _write_notmuch_config(root: Path, addresses: list[str]) -> None:
    """A notmuch config of agent-media's own, for a host with none
    (timeline._notmuch_cmd passes it with --config)."""
    lines = ["# Written by agent-media (mail_accounts.py) for a host with no notmuch config.",
             "[database]", f"path={root}", "", "[user]",
             f"primary_email={addresses[0]}",
             f"other_email={';'.join(addresses[1:])}", "",
             "[new]", "tags=unread;inbox;",
             "ignore=.mbsyncstate;.uidvalidity;.mbsyncstate.journal;.mbsyncstate.new;.mbsyncstate.lock",
             "", "[search]", "exclude_tags=deleted;spam;", "",
             "[maildir]", "synchronize_flags=true", ""]
    p = own_notmuch_config()
    p.write_text("\n".join(lines))
    os.chmod(p, 0o600)


# ── Syncing ───────────────────────────────────────────────────────────────

def _run(argv: list[str], timeout: float) -> tuple[int, str]:
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                           errors="replace")
    except FileNotFoundError:
        return 127, f"{argv[0]} is not installed"
    except subprocess.TimeoutExpired:
        return 124, f"{argv[0]} took longer than {int(timeout)} s"
    tail = (r.stderr or "").strip().splitlines()
    return r.returncode, (tail[-1] if tail else "")


def _sync_cmd() -> list[str]:
    cmd = (os.environ.get("MEDIA_MAIL_MBSYNC") or "").strip()
    return shlex.split(cmd) if cmd else ["mbsync"]


def sync(only: str = "") -> dict:
    """Fetch each added account (or `only`), index, and tag. Nothing to do
    when none was added. `{"accounts": {name: ok|error}, "indexed": bool}`."""
    from . import timeline

    accts = [a for a in load() if not only or a["name"] == only]
    if not accts:
        return {"accounts": {}, "indexed": False}
    root = maildir_root()
    results = {}
    for a in accts:
        (root / a["name"]).mkdir(parents=True, exist_ok=True)
        rc, err = _run(_sync_cmd() + ["-c", str(mbsyncrc()), "-q", f"am-{a['name']}"],
                       SYNC_TIMEOUT_S)
        _record(a["name"], rc == 0, "" if rc == 0 else (err or f"mbsync exited {rc}"))
        results[a["name"]] = "ok" if rc == 0 else (err or f"mbsync exited {rc}")
    indexed = False
    for attempt in range(3):
        if timeline._notmuch(["new", "--quiet"], SYNC_TIMEOUT_S) is not None:
            indexed = True
            break
        time.sleep(20)  # another sync holds notmuch's write lock
    if indexed:
        for a in accts:
            n, f = a["name"], a.get("folders") or {}
            timeline._notmuch(["tag", f"+{n}", "--", f'path:"{n}/**" and not tag:{n}'], 300)
            if f.get("sent"):
                timeline._notmuch(["tag", "+sent", "-inbox", "-unread", "--",
                                   f'folder:"{n}/{f["sent"]}" and tag:inbox'], 300)
            if f.get("spam"):
                timeline._notmuch(["tag", "+spam", "-inbox", "--",
                                   f'folder:"{n}/{f["spam"]}" and not tag:spam'], 300)
    return {"accounts": results, "indexed": indexed}


def sync_soon(only: str = "") -> None:
    """Start a sync in the background (after an account is added)."""
    threading.Thread(target=lambda: sync(only), name="mail-sync", daemon=True).start()


# ── Settings → Mail ───────────────────────────────────────────────────────

def settings(bearer: str, body: dict | None = None) -> tuple[bool, dict]:
    """`GET`/`POST /settings/mail`, the owner's admin."""
    from . import auth, contacts

    user, err = auth.may_admin(bearer)
    if not user:
        return False, err
    if not contacts.is_owner():
        return False, {"status": 403, "code": "owner", "error": "this host's mail is its owner's"}
    if body is not None:
        action = str(body.get("action") or "")
        try:
            if action == "add":
                got = add(body)
                sync_soon(got["name"])
            elif action == "remove":
                remove(str(body.get("name") or ""), delete_mail=bool(body.get("delete_mail")))
            elif action == "sync":
                sync_soon(str(body.get("name") or ""))
            else:
                return False, {"status": 400, "code": "action",
                               "error": "action is add, remove or sync"}
        except Refused as e:
            return False, {"status": 422, "code": "refused", "error": str(e)}
    return True, {"accounts": listing(), "tools": tools(),
                  "presets": [{"id": k, "label": v["label"], "help": v["help"],
                               "note": v["note"], "servers": v["imap"] is None}
                              for k, v in PRESETS.items()]}


def main(argv: list[str] | None = None) -> int:
    """`media mail sync [name] | list`."""
    argv = list(argv if argv is not None else sys.argv[1:])
    if argv[:1] == ["sync"]:
        got = sync(argv[1] if len(argv) > 1 else "")
        bad = {k: v for k, v in got["accounts"].items() if v != "ok"}
        for k, v in bad.items():
            print(f"media mail sync: {k}: {v}", file=sys.stderr)
        return 1 if bad or (got["accounts"] and not got["indexed"]) else 0
    if argv[:1] == ["list"]:
        for a in listing():
            flag = "added here" if a["managed"] else "set up on this host"
            print(f"{a['name']:<16} {a['address']:<36} {flag}"
                  + (f"  error: {a['error']}" if a.get("error") else ""))
        return 0
    print("usage: media mail sync [name] | list", file=sys.stderr)
    return 2

