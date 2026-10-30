"""Editing a contact from its card, written back to Google (contacts.py's
step 4, its second part).

David, 20 Oct 2026: an Edit button on the card for name, phones, emails,
notes and labels, written back to Google, with "Open in Google Contacts"
beside it. The write goes through the phone, as the read and the Join's
links do (contacts_writeback.py): `content` updates, inserts and deletes on
the person's `com.google` raw contact, as shell uid, which marks it dirty,
and Android syncs it to Google. No Cloud project; the People API sign-in
stays read-only.

* **What changes.** `name` updates the structured-name row's display name
  (the provider splits it into given and family); `phones` and `emails` are
  the whole list wanted: a value no longer there has its row deleted, a new
  one is inserted (a mobile number, an "other" address), the rest are left
  as they are, labels and all; `notes` updates the note row or adds one;
  `labels` adds and removes Google group memberships, by the label's name
  among the phone's `com.google` groups (a label Google has no group for is
  not made: said back as `skipped`).
* **Conflicts.** The app sends what it showed (`base`) beside what it wants
  (`set`). A field whose value on the card now is not what the app showed
  was changed elsewhere since (in Google Contacts, by a sync): 409 with
  both, `{field: {"yours": …, "theirs": …}}`, and nothing is written. (The
  People API's own etag guards a People API write; this write goes through
  the phone, so the card is the guard.)
* **After.** The phone is read again in a thread, so the card shows what
  Google now holds within moments.
* The owner's book only, and only a person with a Google entry on the phone
  (`editable`); off with `MEDIA_CONTACTS_WRITEBACK=0`, as the Join's links.
"""

from __future__ import annotations

import json
import re
import shlex
import threading

from . import contacts, contacts_android, contacts_writeback as wb

URI = contacts_android.URI
NAME = "vnd.android.cursor.item/name"
PHONE = "vnd.android.cursor.item/phone_v2"
EMAIL = "vnd.android.cursor.item/email_v2"
NOTE = "vnd.android.cursor.item/note"
GROUP = "vnd.android.cursor.item/group_membership"
POSTAL = "vnd.android.cursor.item/postal-address_v2"
ORG = "vnd.android.cursor.item/organization"
EVENT = "vnd.android.cursor.item/contact_event"
WEBSITE = "vnd.android.cursor.item/website"
#: Phone.TYPE_MOBILE, Email.TYPE_OTHER.
PHONE_MOBILE = 2
EMAIL_OTHER = 3
FIELDS = ("name", "phones", "emails", "notes", "labels", "addresses", "organization",
          "birthday", "websites")
#: The typed lists (David, 30 Oct 2026: "where do the labels for each number
#: or email go?"): each item `{value, type}`, `type` one of the kind's names
#: below or any other words (Android's custom label); a plain string, as older
#: apps send, is a value whose label is left as it is (a new one: the default).
#: The value's key on the card, and the kind's types and default.
TYPED = {
    "phones": ("raw", contacts_android._PHONE_TYPES, PHONE_MOBILE, PHONE),
    "emails": ("value", contacts_android._EMAIL_TYPES, EMAIL_OTHER, EMAIL),
    "addresses": ("formatted", contacts_android._POSTAL_TYPES, 1, POSTAL),
    "websites": ("url", contacts_android._WEBSITE_TYPES, 7, WEBSITE),
}


class Conflict(Exception):
    def __init__(self, fields: dict):
        super().__init__("changed since")
        self.fields = fields


class NotEditable(Exception):
    pass


def editable(card: dict) -> bool:
    """Whether a card can be edited here: the owner's book, write-back on,
    and a Google entry on the phone behind it."""
    return contacts.is_owner() and wb.enabled() and bool(wb._raw_ids(card))


def google_url(card: dict) -> str:
    """The person in Google Contacts, from a Google source's resource name
    (`people/c123…`), or ""."""
    for s in card.get("sources") or []:
        if s.get("kind") == "google":
            m = re.fullmatch(r"people/(c\d+)", s.get("id") or "")
            if m:
                return f"https://contacts.google.com/person/{m.group(1)}"
    return ""


def phone_lookup(card: dict) -> str:
    """The person's lookup key in the phone's address book (its Google
    entry's contact), for the app's "Edit in Google Contacts": Android opens
    its own editor on `content://com.android.contacts/contacts/lookup/<key>`
    (David, 30 Oct 2026: "even if it linked to the Google contacts app would
    be quite handy"). "" where the card cannot be edited here."""
    if not editable(card):
        return ""
    for s in card.get("sources") or []:
        if s.get("kind") == "android" and wb.GOOGLE in (s.get("accounts") or []) and s.get("id"):
            return str(s["id"])
    return ""


def reread(cid: str) -> dict:
    """`POST /contacts/<id>/reread`: read the phone's address book again now,
    so a change made in Google's editor shows when the app comes back to the
    card. `{ok}`; NotEditable for no such person, the phone's Unreachable."""
    conn = contacts._connect(create=False)
    if conn is None:
        raise NotEditable("no such contact")
    try:
        row = conn.execute("SELECT 1 FROM contacts WHERE id=?", (cid,)).fetchone()
    finally:
        conn.close()
    if row is None:
        raise NotEditable("no such contact")
    contacts.sync_android()
    return {"ok": True}


def _items(card: dict, field: str) -> list[dict]:
    """A typed list as the card shows it: `[{value, type}]`."""
    key = TYPED[field][0]
    rows = card.get(field) or []
    if field == "emails":
        rows = [e for e in rows if not e.get("derived")]
    out = []
    for r in rows:
        v = r.get(key) or (r.get("value") if field == "phones" else "") or ""
        if field == "emails":
            v = v or r.get("address") or ""
        if v:
            out.append({"value": v, "type": r.get("type") or ""})
    return out


def _now(card: dict, field: str):
    """A field as the card shows it, in the form `set` takes."""
    if field == "name":
        return card.get("name") or ""
    if field in TYPED:
        return _items(card, field)
    if field == "notes":
        return card.get("notes") or ""
    if field == "organization":
        o = (card.get("organizations") or [{}])[0]
        return {"company": o.get("company") or "", "title": o.get("title") or ""}
    if field == "birthday":
        return card.get("birthday") or ""
    return list(card.get("labels") or [])


def _key(field: str, value: str) -> str:
    """What makes two values one: a number's E.164 (else its digits), an
    address's words, the rest folded."""
    if field == "phones":
        return contacts.e164(value, contacts.region()) or re.sub(r"\D", "", value)
    if field == "addresses":
        return " ".join(value.split()).lower()
    if field == "websites":
        return wb._norm(value)
    return value.strip().lower()


def _as_items(field: str, xs) -> list[dict]:
    return [x if isinstance(x, dict) else {"value": x, "type": None} for x in xs or []]


def _same(field: str, a, b) -> bool:
    if field in TYPED:
        # A label counts only where both sides say one (an older app's plain
        # strings say none).
        def norm(xs, other):
            typed = all(x.get("type") is not None for x in _as_items(field, other))
            return sorted((_key(field, x["value"]),
                           (x.get("type") or "").casefold() if typed and x.get("type") is not None else "")
                          for x in _as_items(field, xs))
        return norm(a, b) == norm(b, a)
    if field == "labels":
        return sorted(x.strip().lower() for x in a or []) == \
            sorted(x.strip().lower() for x in b or [])
    if field == "organization":
        a, b = a or {}, b or {}
        return all((a.get(k) or "").strip() == (b.get(k) or "").strip() for k in ("company", "title"))
    return (a or "").strip() == (b or "").strip()


def _clean(field: str, v):
    if field in TYPED:
        if not isinstance(v, list):
            raise ValueError(f"{field} must be a list")
        out = []
        for x in v[:50]:
            if isinstance(x, str):
                x = {"value": x, "type": None}
            if not isinstance(x, dict) or not isinstance(x.get("value"), str) \
                    or not isinstance(x.get("type"), (str, type(None))):
                raise ValueError(f"{field} items are strings or {{value, type}}")
            val = x["value"].strip()[:1000]
            if val:
                t = x["type"].strip()[:60] if isinstance(x["type"], str) else None
                out.append({"value": val, "type": t})
        return out
    if field == "labels":
        if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
            raise ValueError(f"{field} must be a list of strings")
        return [x.strip() for x in v if x.strip()][:50]
    if field == "organization":
        if not isinstance(v, dict):
            raise ValueError("organization must be {company, title}")
        return {k: (v.get(k) or "").strip()[:300] if isinstance(v.get(k) or "", str) else ""
                for k in ("company", "title")}
    if not isinstance(v, str):
        raise ValueError(f"{field} must be a string")
    if field == "birthday" and v.strip() and not re.fullmatch(r"(\d{4}|-)-\d{2}-\d{2}", v.strip()):
        raise ValueError("birthday is YYYY-MM-DD or --MM-DD")
    return v.strip()[:4000 if field == "notes" else 300]


def _type_binds(field: str, label: str | None) -> list[str]:
    """A typed row's data2 and data3: the kind's number for a name it knows,
    else Android's custom label (TYPE_CUSTOM 0 with the words in data3)."""
    _, types, default, _ = TYPED[field]
    if not label:
        return [f"data2:i:{default}", "data3:n:"]
    for n, name in types.items():
        if name.casefold() == label.casefold():
            return [f"data2:i:{n}", "data3:n:"]
    return ["data2:i:0", f"data3:s:{label}"]


def _col(raw: str, mimetype: str, col: str) -> dict[str, str]:
    """One column of a raw contact's rows of a kind, by row id. Queried on
    its own, as the last column: its value can hold ", data3=" itself."""
    where = f"raw_contact_id={raw} AND mimetype='{mimetype}'"
    out = wb._shell(f"content query --uri {URI}/data --projection _id:{col} --where \"{where}\"")
    got: dict[str, str] = {}
    last = None
    for line in out.splitlines():
        m = re.match(rf"^Row: \d+ _id=(\d+), {col}=(.*)$", line)
        if m:
            last = m.group(1)
            got[last] = "" if m.group(2) == "NULL" else m.group(2)
        elif last is not None:
            got[last] += "\n" + line
    return got


def _rows(raw: str, mimetype: str, cols: str = "_id:data1") -> list[dict]:
    where = f"raw_contact_id={raw} AND mimetype='{mimetype}'"
    out = wb._shell(f"content query --uri {URI}/data --projection {cols} --where \"{where}\"")
    rows: list[dict] = []
    for line in out.splitlines():
        m = re.match(r"^Row: \d+ _id=(\d+)(?:, data1=(.*))?$", line)
        if m:
            rows.append({"id": m.group(1), "data1": m.group(2) or ""})
        elif rows:
            # A value with a line break runs on (a number saved as "\n+614…"
            # read as empty, and so was deleted as one not wanted).
            rows[-1]["data1"] += "\n" + line
    for r in rows:
        if r["data1"] == "NULL":
            r["data1"] = ""
    return rows


def _binds(binds: list[str]) -> str:
    return " ".join(f"--bind {shlex.quote(b)}" for b in binds)


def _insert(raw: str, mimetype: str, binds: list[str]) -> None:
    wb._shell(f"content insert --uri {URI}/data "
              + _binds([f"raw_contact_id:i:{raw}", f"mimetype:s:{mimetype}", *binds]))


def _update(row_id: str, binds: list[str]) -> None:
    wb._shell(f"content update --uri {URI}/data {_binds(binds)} --where \"_id={row_id}\"")


def _delete(row_id: str) -> None:
    wb._shell(f"content delete --uri {URI}/data --where \"_id={row_id}\"")


def _groups() -> dict[str, str]:
    """The phone's Google groups by folded title: `{title: _id}` ("My
    Contacts" is the system group `Contacts`)."""
    where = "deleted=0 AND account_type='com.google'"
    out = wb._shell(f"content query --uri {URI}/groups --projection _id:system_id:title "
                    f"--where \"{where}\"")
    got: dict[str, str] = {}
    for line in out.splitlines():
        m = re.match(r"^Row: \d+ _id=(\d+), system_id=(.*?), title=(.*)$", line)
        if not m:
            continue
        gid, system, title = m.group(1), m.group(2), m.group(3)
        name = "My Contacts" if system == "Contacts" else title
        if name and name != "NULL":
            got.setdefault(contacts.fold(name), gid)
    return got


def _new_group(raw: str, title: str) -> str | None:
    """A new Google group (label) named `title` in the account of raw contact
    `raw`, which Android syncs to Google: its `_id`, or None when the account
    cannot be told or the phone made none."""
    out = wb._shell(f"content query --uri {URI}/raw_contacts --projection _id:account_name "
                    f"--where \"_id={raw} AND account_type='com.google'\"")
    m = re.search(r"^Row: \d+ _id=\d+, account_name=(.+)$", out, re.M)
    if not m or m.group(1) == "NULL":
        return None
    wb._shell(f"content insert --uri {URI}/groups " + _binds([
        "account_type:s:com.google", f"account_name:s:{m.group(1)}", f"title:s:{title}",
        "group_visible:i:1"]))
    return _groups().get(contacts.fold(title))


def _write(raws: list[str], card: dict, want: dict) -> dict:
    """`raws`: every live Google entry behind the person, oldest first (David,
    30 Oct 2026: "I deleted a phone number and it was still there after I
    saved" — it was in the second of two joined entries). A value gone, a
    relabel and a label taken off reach every entry; a new value, the name
    and the other single fields go to the first."""
    raw = raws[0]
    done, skipped = [], []
    if "name" in want and want["name"]:
        rows = _rows(raw, NAME)
        b = [f"data1:s:{want['name']}", "data2:n:", "data3:n:", "data4:n:", "data5:n:",
             "data6:n:"]
        if rows:
            _update(rows[0]["id"], b)
        else:
            _insert(raw, NAME, [f"data1:s:{want['name']}"])
        done.append("name")
    for field in ("phones", "emails", "addresses", "websites"):
        if field not in want:
            continue
        mime, types = TYPED[field][3], TYPED[field][1]
        wanted = {_key(field, x["value"]): x for x in want[field]}
        have: dict[str, list[dict]] = {}
        for r_ in raws:
            for r in _rows(r_, mime):
                have.setdefault(_key(field, r["data1"]), []).append({**r, "raw": r_})
        typed = [x for k, x in wanted.items() if k in have and x.get("type") is not None]
        d2: dict[str, str] = {}
        d3: dict[str, str] = {}
        for r_ in {r["raw"] for k in have for r in have[k]} if typed else ():
            d2.update(_col(r_, mime, "data2"))
            d3.update(_col(r_, mime, "data3"))
        for k, rs in have.items():
            if k not in wanted:
                for r in rs:
                    _delete(r["id"])
        for k, x in wanted.items():
            if k not in have:
                _insert(raw, mime, [f"data1:s:{x['value']}", *_type_binds(field, x.get("type"))])
            elif x.get("type") is not None:
                for r in have[k]:
                    was = contacts_android._label(types, d2.get(r["id"]), d3.get(r["id"]))
                    if was.casefold() != x["type"].casefold():
                        _update(r["id"], _type_binds(field, x["type"]))
        done.append(field)
    if "organization" in want:
        rows = _rows(raw, ORG)
        o = want["organization"]
        b = [f"data1:s:{o['company']}" if o["company"] else "data1:n:",
             f"data4:s:{o['title']}" if o["title"] else "data4:n:"]
        if not (o["company"] or o["title"]):
            for r in rows:
                _delete(r["id"])
        elif rows:
            _update(rows[0]["id"], b)
        else:
            _insert(raw, ORG, [x for x in b if not x.endswith(":n:")])
        done.append("organization")
    if "birthday" in want:
        mine = [rid for r_ in raws for rid, t in _col(r_, EVENT, "data2").items()
                if t == contacts_android.BIRTHDAY]
        if want["birthday"]:
            if mine:
                _update(mine[0], [f"data1:s:{want['birthday']}"])
                for rid in mine[1:]:
                    _delete(rid)
            else:
                _insert(raw, EVENT, [f"data1:s:{want['birthday']}",
                                     f"data2:i:{contacts_android.BIRTHDAY}"])
        else:
            for rid in mine:
                _delete(rid)
        done.append("birthday")
    if "notes" in want:
        # The card shows every entry's note joined: the edited whole goes in
        # the first entry, and the others' notes (part of what was edited) go.
        for r_ in raws[1:]:
            for r in _rows(r_, NOTE):
                _delete(r["id"])
        rows = _rows(raw, NOTE)
        if rows:
            if want["notes"]:
                _update(rows[0]["id"], [f"data1:s:{want['notes']}"])
            else:
                _delete(rows[0]["id"])
        elif want["notes"]:
            _insert(raw, NOTE, [f"data1:s:{want['notes']}"])
        done.append("notes")
    if "labels" in want:
        groups = _groups()
        member: dict[str, list[dict]] = {}
        for r_ in raws:
            for r in _rows(r_, GROUP):
                member.setdefault(r["data1"], []).append(r)
        wanted: set[str] = set()
        for label in want["labels"]:
            gid = groups.get(contacts.fold(label))
            if gid is None:
                # A label Google has no group for yet is made, in the
                # person's own Google account (David, 30 Oct 2026: "a way
                # there adding people to groups"); skipped if it cannot be.
                gid = _new_group(raw, label)
                if gid is None:
                    skipped.append(label)
                    continue
                groups[contacts.fold(label)] = gid
            wanted.add(gid)
        mine = {contacts.fold(x) for x in _now(card, "labels")}
        for gid, rs in member.items():
            # Only a membership the card shows as a label is taken away.
            title = next((t for t, g in groups.items() if g == gid), None)
            if gid not in wanted and title in mine:
                for r in rs:
                    _delete(r["id"])
        for gid in wanted - set(member):
            _insert(raw, GROUP, [f"data1:i:{gid}"])
        done.append("labels")
    return {"done": done, "skipped": skipped}


def edit(cid: str, body: dict) -> dict:
    """`PATCH /contacts/<id>` `{base: {field: as shown}, set: {field: wanted}}`:
    `{ok, done, skipped}`. Raises NotEditable, Conflict, ValueError, or the
    phone's Unreachable / WriteError."""
    if not isinstance(body, dict) or not isinstance(body.get("set"), dict) or not body["set"]:
        raise ValueError("set the fields to change")
    unknown = set(body["set"]) - set(FIELDS)
    if unknown:
        raise ValueError(f"cannot edit {', '.join(sorted(unknown))}")
    base = body.get("base") if isinstance(body.get("base"), dict) else {}
    conn = contacts._connect(create=False)
    if conn is None:
        raise NotEditable("no such contact")
    try:
        row = conn.execute("SELECT card, name FROM contacts WHERE id=?", (cid,)).fetchone()
    finally:
        conn.close()
    if row is None:
        raise NotEditable("no such contact")
    card = json.loads(row[0])
    card.setdefault("name", row[1])
    if not editable(card):
        raise NotEditable("only a person with a Google entry on the phone can be edited here")
    want = {f: _clean(f, v) for f, v in body["set"].items()}
    clash = {}
    for f in want:
        if f in base and not _same(f, base[f], _now(card, f)):
            clash[f] = {"yours": base[f], "theirs": _now(card, f)}
    if clash:
        raise Conflict(clash)
    want = {f: v for f, v in want.items() if not _same(f, v, _now(card, f))}
    if not want:
        return {"ok": True, "done": [], "skipped": []}
    raws = wb._google_raws(wb._raw_ids(card))
    if not raws:
        raise NotEditable("no Google entry on the phone any more")
    got = _write(raws, card, want)
    _later(_reread, contacts.account())
    return {"ok": True, **got}


def _later(fn, *args) -> None:
    threading.Thread(target=fn, args=args, name="contacts-edit-reread", daemon=True).start()


def _reread(key: str) -> None:
    try:
        with contacts.as_account(key):
            contacts.sync_android()
    except Exception as e:  # noqa: BLE001 — the next sync loop reads it anyway
        contacts.log.info("contacts: the read after an edit failed: %s", e)
