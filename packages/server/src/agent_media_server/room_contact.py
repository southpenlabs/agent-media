"""A room's phone number, and a room with no contact linked to a person
(server-contract.md §6.1 "Matrix rows", §6.25 "A room linked to someone").

David, 18 Nov 2026 ("Start it, all three"): the thread header of a Messages
room gets the person's picture, a call button, and, where no contact
matches the room, "Add to contacts" / "Link to someone" in place of Rename.

* **`phone`** on a row: the one number of the one person a 1:1 room is with,
  E.164. From what the room's member says about itself (`contacts.
  identities`: a WhatsApp ghost's id, a displayname that is a number) and
  from the ghost's own profile, where mautrix bridgev2 lists
  `com.beeper.bridge.identifiers` (`tel:+61…`). The bridges only put a
  number there with `bridge.phone_numbers_in_profile: true` and a
  homeserver that keeps extra profile fields; without them a Google
  Messages or Signal room has no number here. Profiles are read in the
  background and kept (a day; one with no number or a failure, an hour),
  so a row never waits on the homeserver. Absent in a group or where no one number is known.
* **`link: true`** on a row: the owner's book, a 1:1 room, no contact
  matched — the room can be linked.
* **`POST /rooms/<session>/contact {"id"}`**: this room's other member is
  that person from now on. Kept twice: a room join (`contacts.
  set_override`), so this room is theirs whatever the matcher thinks, and a
  join of the member's ghost (`merges`, as a Find on pick or "Same person"
  keeps it), so the ghost lands on them in every rebuild and in their other
  rooms. The card's chat links go to Google as a Join's do.
* **`POST /rooms/<session>/contact {"new": {"name"}}`**: a new Google
  contact made on the phone (`content insert`, as the card's edits are
  written, contacts_edit.py) with that name and the room's number, the
  phone read again, and the new person linked as above. Where the phone has
  not shown it yet the answer says `pending` and has no `contact`; a number
  it was made with matches the room by itself at the next read.
"""

from __future__ import annotations

import json
import logging
import queue
import re
import shlex
import threading
import time

from . import auth, contacts, sessions

log = logging.getLogger(__name__)

#: How long a ghost's profile numbers are kept; a failed read waits this.
PROFILE_TTL_S = 24 * 3600
PROFILE_RETRY_S = 3600
NAME_MAX = 200

_PROFILES: dict[str, tuple[float, frozenset]] = {}   # uid → (when, numbers)
_WANTED: "queue.Queue[tuple]" = queue.Queue()
_QUEUED: set[str] = set()
_LOCK = threading.Lock()
_WORKER: threading.Thread | None = None


class Refused(Exception):
    """The request names a room or person it cannot be: `status` and why."""

    def __init__(self, status: int, why: str) -> None:
        super().__init__(why)
        self.status = status


# --- the row's fields ------------------------------------------------------------


def _other(r) -> tuple[str, str] | None:
    """`(uid, displayname)` of the one person a room is with besides the
    owner, the agent and the bridges' bots; None in a group or an empty room."""
    skip = contacts._skip_of(r)
    with r.lock:
        others = [(u, dn) for u, dn in r.members.items() if u not in skip]
    return others[0] if len(others) == 1 else None


def _profile_numbers(r, uid: str) -> frozenset:
    """The `tel:` numbers on the ghost's profile, as kept; an unknown or
    stale one is asked for in the background."""
    now = time.time()
    with _LOCK:
        got = _PROFILES.get(uid)
        if got is not None and now < got[0]:
            return got[1]
        if uid not in _QUEUED:
            _QUEUED.add(uid)
            _WANTED.put((r.config, uid))
            _start()
    return got[1] if got else frozenset()


def _start() -> None:
    global _WORKER
    if _WORKER is None or not _WORKER.is_alive():
        _WORKER = threading.Thread(target=_work, name="room-contact-profiles", daemon=True)
        _WORKER.start()


def _work() -> None:
    while True:
        try:
            config, uid = _WANTED.get(timeout=60)
        except queue.Empty:
            return
        nums, ok = _read_profile(config, uid)
        with _LOCK:
            _QUEUED.discard(uid)
            if ok:
                # None yet is asked again within the hour: the bridge fills a
                # ghost's profile when it next updates that person.
                _PROFILES[uid] = (time.time() + (PROFILE_TTL_S if nums else PROFILE_RETRY_S),
                                  nums)
            else:
                old = _PROFILES.get(uid)
                _PROFILES[uid] = (time.time() + PROFILE_RETRY_S, old[1] if old else frozenset())


def _read_profile(config, uid: str) -> tuple[frozenset, bool]:
    from . import matrix

    try:
        prof = matrix._get(config, f"/profile/{matrix._q(uid)}")
    except Exception as e:  # noqa: BLE001 — no number shown, not no row
        log.info("room-contact: profile of %s unreadable: %s", uid, e)
        return frozenset(), False
    return frozenset(numbers_of_profile(prof)), True


def numbers_of_profile(prof) -> set[str]:
    """The E.164 numbers in a profile's `com.beeper.bridge.identifiers`."""
    out: set[str] = set()
    ids = prof.get("com.beeper.bridge.identifiers") if isinstance(prof, dict) else None
    for x in ids if isinstance(ids, list) else []:
        if isinstance(x, str) and x.startswith("tel:"):
            num = contacts.e164(x[4:], contacts.region())
            if num:
                out.add(num)
    return out


def phone_of(r) -> str | None:
    """The row's `phone`: one number, or None."""
    other = _other(r)
    if other is None:
        return None
    uid, dn = other
    nums = set(contacts.identities(uid, dn, contacts.region())["phones"])
    if contacts._is_ghost(uid):
        nums |= _profile_numbers(r, uid)
    nums -= contacts._self_ids()[0]
    return next(iter(nums)) if len(nums) == 1 else None


def linkable(r, contact: dict | None) -> bool:
    """The row's `link`: the owner's book, a 1:1 room, no contact."""
    return contact is None and contacts.is_owner() and _other(r) is not None


def row_fields(r, contact: dict | None) -> dict:
    """What a Matrix row adds (matrix.rows): `phone` and `link`, each only
    when it has something to say. Never fails the row."""
    out: dict = {}
    try:
        phone = phone_of(r)
        if phone:
            out["phone"] = phone
        if linkable(r, contact):
            out["link"] = True
    except Exception as e:  # noqa: BLE001 — a row without a call button, not no row
        log.warning("room-contact %s: %s", getattr(r, "id", "?"), e)
    return out


# --- linking ---------------------------------------------------------------------


def _room_for(session: str):
    from . import matrix

    session = (session or "").strip()
    if not sessions._SESSION.fullmatch(session):
        raise Refused(400, "not a session id")
    r = matrix.room(session)
    if r is None:
        raise Refused(404, "no such room")
    if not contacts.is_owner():
        raise Refused(403, "the rooms are the owner's")
    other = _other(r)
    if other is None:
        raise Refused(409, "only a chat with one person can be linked to someone")
    if contacts.for_room(r) is not None:
        raise Refused(409, "this room already has a contact")
    return r, other[0]


def _link(r, uid: str, cid: str) -> dict:
    """Keep the room and its member's ghost as `cid`'s; `{id, name}`."""
    with contacts._WRITE:
        conn = contacts._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT name FROM contacts WHERE id=?", (cid,)).fetchone()
            if row is None:
                raise Refused(404, "no such person")
            now = time.time()
            conn.execute("DELETE FROM overrides WHERE room=? AND kind='join'", (r.id,))
            conn.execute("DELETE FROM overrides WHERE room=? AND contact=?", (r.id, cid))
            conn.execute("INSERT OR REPLACE INTO overrides (room, contact, kind, at) "
                         "VALUES (?, ?, 'join', ?)", (r.id, cid, now))
            if contacts._is_ghost(uid):
                conn.execute("DELETE FROM merges WHERE source=? AND kind='join'", (uid,))
                conn.execute("DELETE FROM merges WHERE source=? AND contact=? AND kind='dismiss'",
                             (uid, cid))
                conn.execute("INSERT OR REPLACE INTO merges (source, contact, kind, at) "
                             "VALUES (?, ?, 'join', ?)", (uid, cid, now))
            contacts._rebuild(conn)
            name = (conn.execute("SELECT name FROM contacts WHERE id=?", (cid,)).fetchone()
                    or row)[0]
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()
    contacts._fresh()
    try:
        from . import contacts_writeback

        contacts_writeback.queue(cid)
    except Exception:  # noqa: BLE001 — a write-back is never the link's failure
        pass
    log.info("room-contact: room %s linked to %s", r.thread[:8], cid[:8])
    return {"id": cid, "name": name or ""}


def link(session: str, cid: str) -> dict:
    """`POST /rooms/<session>/contact {"id"}`."""
    r, uid = _room_for(session)
    if not re.fullmatch(r"[0-9a-f-]{36}", cid or ""):
        raise Refused(404, "no such person")
    return {"contact": _link(r, uid, cid)}


# --- a new contact on the phone ----------------------------------------------------


def _google_account(wb, URI: str) -> str | None:
    """The Google account a new contact goes in: one of the book's signed-in
    Google accounts that the phone holds, else the phone's busiest one."""
    out = wb._shell(f"content query --uri {URI}/raw_contacts --projection account_name "
                    f"--where \"deleted=0 AND account_type='com.google'\"")
    counts: dict[str, int] = {}
    for m in re.finditer(r"^Row: \d+ account_name=(.+)$", out, re.M):
        name = m.group(1).strip()
        if name and name != "NULL":
            counts[name] = counts.get(name, 0) + 1
    if not counts:
        return None
    try:
        signed = [a.get("email") or "" for a in contacts.google_accounts()]
    except Exception:  # noqa: BLE001 — the phone's own count decides
        signed = []
    for email in signed:
        if email in counts:
            return email
    return max(counts, key=counts.get)


def create_on_phone(name: str, phone: str | None) -> str:
    """A new Google contact on the phone: its raw contact id. Raises the
    phone's Unreachable / WriteError, or Refused."""
    from . import contacts_android, contacts_edit, contacts_writeback as wb

    URI = contacts_android.URI
    account = _google_account(wb, URI)
    if not account:
        raise Refused(503, "the phone has no Google account to keep a contact in")
    acct = f"account_type='com.google' AND account_name={_sql(account)}"
    before = _newest_raw(wb, URI, acct)
    wb._shell(f"content insert --uri {URI}/raw_contacts "
              + contacts_edit._binds(["account_type:s:com.google", f"account_name:s:{account}"]))
    raw = _newest_raw(wb, URI, acct)
    if raw is None or (before is not None and int(raw) <= int(before)):
        raise Refused(503, "the phone made no contact")
    contacts_edit._insert(raw, contacts_edit.NAME, [f"data1:s:{name}"])
    if phone:
        contacts_edit._insert(raw, contacts_edit.PHONE,
                              [f"data1:s:{phone}", f"data2:i:{contacts_edit.PHONE_MOBILE}"])
    return raw


def _sql(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def _newest_raw(wb, URI: str, where: str) -> str | None:
    out = wb._shell(f"content query --uri {URI}/raw_contacts --projection _id "
                    f"--where {shlex.quote(where)} --sort \"_id DESC\"")
    m = re.search(r"^Row: \d+ _id=(\d+)\s*$", out, re.M)
    return m.group(1) if m else None


def _contact_of_raw(raw: str) -> str | None:
    """The book's person whose phone entry is raw contact `raw`."""
    conn = contacts._connect(create=False)
    if conn is None:
        return None
    try:
        for cid, card in conn.execute("SELECT id, card FROM contacts"):
            for s in json.loads(card).get("sources") or []:
                if s.get("kind") == "android" and raw in [str(x) for x in s.get("raw") or []]:
                    return cid
    finally:
        conn.close()
    return None


def add(session: str, name: str) -> dict:
    """`POST /rooms/<session>/contact {"new": {"name"}}`."""
    from . import contacts_android, contacts_writeback as wb

    name = " ".join((name or "").split())
    if not name or len(name) > NAME_MAX:
        raise Refused(400, f"a name is 1 to {NAME_MAX} characters")
    r, uid = _room_for(session)
    if not wb.enabled():
        raise Refused(503, "writing to the phone's contacts is off here "
                           "(MEDIA_CONTACTS_WRITEBACK=0)")
    phone = phone_of(r)
    try:
        raw = create_on_phone(name, phone)
    except contacts_android.Unreachable as e:
        raise Refused(503, f"the phone cannot be reached now ({e})") from None
    except wb.WriteError as e:
        raise Refused(503, f"the phone refused it ({e})") from None
    log.info("room-contact: room %s: new contact made on the phone (raw %s)",
             r.thread[:8], raw)
    try:
        contacts.sync_android()
    except Exception as e:  # noqa: BLE001 — made; the next read shows it
        log.info("room-contact: the read after adding failed: %s", e)
    cid = _contact_of_raw(raw)
    if cid is None:
        return {"pending": True, "phone": phone}
    return {"contact": _link(r, uid, cid)}


# --- the endpoint ------------------------------------------------------------------


def handle(session: str, body, bearer: str) -> tuple[int, dict]:
    """`POST /rooms/<session>/contact`: (status, answer)."""
    user, err = auth.gate(bearer)
    if not user:
        return err.pop("status", 403), {"ok": False, **err}
    body = body if isinstance(body, dict) else {}
    try:
        if isinstance(body.get("new"), dict):
            got = add(session, str(body["new"].get("name") or ""))
        elif body.get("id"):
            got = link(session, str(body["id"]))
        else:
            return 400, {"ok": False, "error": 'send {"id"} or {"new": {"name"}}'}
    except Refused as e:
        return e.status, {"ok": False, "error": str(e)}
    return 200, {"ok": True, **got}


def _reset_for_tests() -> None:
    with _LOCK:
        _PROFILES.clear()
        _QUEUED.clear()
