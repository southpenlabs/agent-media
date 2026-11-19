"""Archive and delete in the Mail tab, at the provider (contract §6.25
"Archive and delete"; step 5 of docs/proposals/2026-10-20-mail-in-sasonica.md,
over IMAP rather than lieer; David, 19 Nov 2026: "Yeah, let's do it").

`POST /mail/move {"thread"|"id", "to": "archive"|"trash"|"inbox"}`:

- **archive** takes the inbox mail of the thread out of the Inbox: IMAP
  MOVE from INBOX to the account's \\All folder (Gmail's All Mail, which is
  how Gmail archives) or its \\Archive folder.
- **trash** moves it to the \\Trash folder (Gmail's Bin), wherever it is.
- **inbox** undoes either: back to INBOX (Gmail: a COPY from All Mail,
  which adds the Inbox label back).

The provider is changed first and the local copy only after it accepted:
the host's own sync may run with `Expunge Both`, which would delete at the
provider a message that only vanished here. Locally the file is moved to
the account's `Archive` (or `Trash`) maildir, which no sync touches, and
notmuch's tags follow (`-inbox +archived`, `+deleted`), so the thread stays readable
and an undo has it to hand. A server without MOVE gets COPY, \\Deleted and
UID EXPUNGE. Nothing is ever expunged outside the moved messages.
"""

from __future__ import annotations

import imaplib
import re
import shutil
from pathlib import Path

TARGETS = ("archive", "trash", "inbox")


class Refused(Exception):
    pass


def _special(conn) -> dict[str, str]:
    """{"all", "archive", "trash", "junk"}: the server's special-use
    folders (RFC 6154), by their flags."""
    from . import mail_remote

    typ, rows = conn.list()
    out: dict[str, str] = {}
    for raw in rows or []:
        line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
        m = re.match(r'\((?P<flags>[^)]*)\)\s+(?:"[^"]*"|NIL)\s+(?P<name>.+)$', line)
        if not m:
            continue
        flags = m.group("flags").lower()
        name = mail_remote._unquote(m.group("name"))
        for key in ("all", "archive", "trash", "junk"):
            if f"\\{key}" in flags:
                out.setdefault(key, name)
    return out


def _find(conn, folder: str, mid: str) -> list[bytes]:
    from . import mail_remote

    typ, _ = conn.select(mail_remote._imap_quote(folder))
    if typ != "OK":
        return []
    typ, data = conn.uid("SEARCH", "HEADER", "Message-ID", mail_remote._imap_quote(f"<{mid}>"))
    return data[0].split() if typ == "OK" and data and data[0] else []


def _move(conn, uids: list[bytes], dest: str, *, copy_only: bool = False) -> None:
    """MOVE (or COPY when `copy_only`, Gmail's add-a-label), else COPY +
    \\Deleted + UID EXPUNGE of just these."""
    from . import mail_remote

    s = b",".join(uids).decode()
    q = mail_remote._imap_quote(dest)
    if copy_only:
        typ, data = conn.uid("COPY", s, q)
        if typ != "OK":
            raise Refused(f"the server would not copy it to {dest}")
        return
    caps = _caps(conn)
    if "MOVE" in caps:
        typ, data = conn.uid("MOVE", s, q)
        if typ != "OK":
            raise Refused(f"the server would not move it to {dest}")
        return
    if "UIDPLUS" not in caps:
        # Without it, EXPUNGE would remove every message marked deleted.
        raise Refused("this server cannot move one message (no MOVE or UIDPLUS)")
    typ, _ = conn.uid("COPY", s, q)
    if typ != "OK":
        raise Refused(f"the server would not move it to {dest}")
    conn.uid("STORE", s, "+FLAGS.SILENT", r"(\Deleted)")
    conn.uid("EXPUNGE", s)


def _caps(conn) -> set[str]:
    """The server's capabilities after login (imaplib's own list is from
    before it, and Gmail names MOVE and UIDPLUS only after)."""
    try:
        typ, data = conn.capability()
        if typ == "OK" and data and data[0]:
            raw = data[0].decode() if isinstance(data[0], bytes) else str(data[0])
            return set(raw.upper().split())
    except (imaplib.IMAP4.error, OSError):
        pass
    return {c.upper() for c in (getattr(conn, "capabilities", ()) or ())}


def _plan(to: str, gmail: bool, sp: dict[str, str]) -> tuple[list[str], str, bool]:
    """(folders to look in, destination, copy only) for `to`."""
    if to == "archive":
        dest = sp.get("all") if gmail else (sp.get("archive") or sp.get("all"))
        if not dest:
            raise Refused("this account's server has no Archive folder")
        return ["INBOX"], dest, False
    if to == "trash":
        dest = sp.get("trash")
        if not dest:
            raise Refused("this account's server has no Trash folder")
        return ["INBOX", *([sp["archive"]] if sp.get("archive") and not gmail else []),
                *([sp["all"]] if sp.get("all") else [])], dest, False
    # Back to the inbox: from the trash first, then the archive.
    looks = [f for f in (sp.get("trash"), sp.get("archive"), sp.get("all")) if f]
    return looks, "INBOX", False


def _messages(target: str) -> list[dict]:
    """The thread's (or one message's) messages: {id, tags, files}."""
    from . import mail_list, mail_read, timeline

    if target.startswith("thread:"):
        if not mail_list.valid_thread(target[7:]):
            raise Refused("no such thread")
        q = target
    elif mail_read.valid_id(target):
        q = mail_read._query(target)
    else:
        raise Refused('expected "thread" or "id"')
    tree = mail_list._json(["show", "--format=json", "--body=false", "--entire-thread=false", q])
    out = []
    for m in timeline._messages(tree):
        if isinstance(m, dict) and m.get("id"):
            files = m.get("filename") or []
            out.append({"id": str(m["id"]), "tags": set(m.get("tags") or []),
                        "files": [str(f) for f in (files if isinstance(files, list) else [files])]})
    return out


def _local_move(root: Path, account: str, files: list[str], into: str) -> None:
    """Move the files of a message out of the synced folders into
    `<root>/<account>/<into>/cur`, which no sync touches."""
    box = root / account / into
    for sub in ("cur", "new", "tmp"):
        (box / sub).mkdir(parents=True, exist_ok=True)
    acct_root = (root / account).resolve()
    for f in files:
        p = Path(f)
        try:
            rp = p.resolve()
        except OSError:
            continue
        if acct_root not in rp.parents or rp.parent.parent == box.resolve():
            continue  # not this account's, or already there
        if not p.is_file():
            continue
        try:
            shutil.move(str(p), str(box / "cur" / p.name))
        except OSError as e:
            from agent_media_core import failures
            failures.swallowed(e, "mail_move._local_move")


def move(*, thread_id: str = "", message_id: str = "", to: str = "") -> dict:
    """`POST /mail/move`. `{"to", "moved": n, "errors": {account: why}}`."""
    from . import mail_accounts, mail_list, mail_remote, timeline

    if to not in TARGETS:
        raise Refused(f"to is one of {', '.join(TARGETS)}")
    target = f"thread:{thread_id}" if thread_id else message_id
    msgs = _messages(target)
    if to == "archive":
        msgs = [m for m in msgs if "inbox" in m["tags"]]
    elif to == "trash":
        msgs = [m for m in msgs if "deleted" not in m["tags"]]
    else:
        # Only what was moved from here: a thread's own sent mail is in
        # Gmail's All Mail too, and must not land in the Inbox.
        msgs = [m for m in msgs if m["tags"] & {"archived", "deleted"}]
    if not msgs:
        return {"to": to, "moved": 0, "errors": {}}
    names = {a["name"] for a in mail_list.accounts()}
    lg = mail_remote.logins()
    by_acct: dict[str, list[dict]] = {}
    errors: dict[str, str] = {}
    for m in msgs:
        acct = next((t for t in sorted(m["tags"]) if t in names), None)
        if acct is None or acct not in lg:
            errors[acct or "?"] = "no login for this account on this host"
            continue
        by_acct.setdefault(acct, []).append(m)
    root = mail_accounts.maildir_root()
    moved = 0
    for acct, ms in by_acct.items():
        a = lg[acct]
        try:
            conn = mail_remote._connect(a)
        except mail_remote.Refused as e:
            errors[acct] = str(e)
            continue
        try:
            sp = _special(conn)
            looks, dest, _ = _plan(to, bool(a.get("gmail")), sp)
            for m in ms:
                done = False
                for folder in looks:
                    uids = _find(conn, folder, m["id"])
                    if not uids:
                        continue
                    # Gmail: All Mail back to the Inbox is a copy (a label).
                    _move(conn, uids, dest,
                          copy_only=(to == "inbox" and bool(a.get("gmail")) and folder == sp.get("all")))
                    done = True
                    break
                if not done:
                    if to == "archive":
                        # Not in the Inbox there: it was archived elsewhere already.
                        done = True
                    else:
                        errors[acct] = "a message was not found on the server"
                        continue
                # The provider has it; now here.
                q = 'id:"' + m["id"].replace('"', '""') + '"'
                if to == "archive":
                    _local_move(root, acct, m["files"], "Archive")
                    timeline._notmuch(["tag", "-inbox", "+archived", "--", q], 60)
                elif to == "trash":
                    _local_move(root, acct, m["files"], "Trash")
                    timeline._notmuch(["tag", "+deleted", "-inbox", "-unread", "--", q], 60)
                else:
                    timeline._notmuch(["tag", "+inbox", "-deleted", "-archived", "--", q], 60)
                moved += 1
        except (Refused, imaplib.IMAP4.error, OSError) as e:
            errors[acct] = str(e) if isinstance(e, Refused) else \
                f"{a['address']}: the server said no ({e.__class__.__name__})"
        finally:
            try:
                conn.logout()
            except (OSError, imaplib.IMAP4.error):
                pass
    if moved:
        # The files moved: notmuch learns their new paths.
        timeline._notmuch(["new", "--quiet"], 300)
    return {"to": to, "moved": moved, "errors": errors}

