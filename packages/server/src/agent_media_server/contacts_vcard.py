"""The caller's book as one vCard file (`GET /contacts/export.vcf`, contract
§6.25 "Export"). Every app people come from or go to (Google, Apple,
Outlook) takes a vCard file, and a person's own contacts are theirs to take
away (contacts parity sweep, 24 Nov 2026, K8).

Each merged person is one vCard 3.0 card, written with vobject (the parser
contacts_carddav.py already uses, Apache-2.0): FN and N, TEL, EMAIL, ADR
(the address as one line in the street part, the form the book keeps it
in), ORG and TITLE, URL, BDAY (a birthday with no year as Apple writes it,
1604 with X-APPLE-OMIT-YEAR, which contacts_carddav reads back), NOTE,
labels as CATEGORIES, the contact id as UID, and with `photos` the
thumbnail as an inline PHOTO. Labels on phones and emails use the TYPE
words contacts_carddav writes, so a file exported here reads back into the
same card."""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

from . import contacts_carddav as carddav
from agent_media_core import failures as _failures

#: The file's media type (RFC 6350 §10.1).
TYPE = "text/vcard; charset=utf-8"


def _add(vc: Any, name: str, value: Any, types: list[str] | None = None) -> None:
    prop = vc.add(name)
    prop.value = value
    if types:
        prop.type_paramlist = types


def _birthday(vc: Any, value: str) -> None:
    v = (value or "").strip()
    if not v:
        return
    if v.startswith("--") and len(v) == 7:          # --MM-DD: no year
        prop = vc.add("bday")
        prop.value = f"1604{v[1:]}"
        prop.x_apple_omit_year_param = "1604"
        return
    _add(vc, "bday", v)


def vcard_of(cid: str, c: dict, name: str, photo: tuple[bytes, str] | None = None) -> Any:
    """One person's card as a vobject vCard."""
    import vobject

    vc = vobject.vCard()
    _add(vc, "uid", cid)
    _add(vc, "fn", name or "(no name)")
    n = (c.get("names") or [{}])[0] or {}
    given, family = str(n.get("given") or ""), str(n.get("family") or "")
    if not (given or family) and name and name != "(no name)":
        given = name
    _add(vc, "n", vobject.vcard.Name(family=family, given=given))
    for p in c.get("phones") or []:
        v = str(p.get("raw") or p.get("value") or "").strip()
        if v:
            _add(vc, "tel", v, carddav.types_for("phones", p.get("type")))
    for e in c.get("emails") or []:
        v = str(e.get("value") or "").strip()
        if v:
            _add(vc, "email", v, carddav.types_for("emails", e.get("type")))
    for a in c.get("addresses") or []:
        v = str(a.get("formatted") or "").strip()
        if v:
            _add(vc, "adr", vobject.vcard.Address(street=v),
                 carddav.types_for("addresses", a.get("type")))
    for o in c.get("organizations") or []:
        company, title = str(o.get("company") or "").strip(), str(o.get("title") or "").strip()
        if company:
            _add(vc, "org", [company])
        if title:
            _add(vc, "title", title)
    for w in c.get("websites") or []:
        v = str(w.get("url") or "").strip()
        if v:
            _add(vc, "url", v, carddav.types_for("websites", w.get("type")))
    _birthday(vc, str(c.get("birthday") or ""))
    if str(c.get("notes") or "").strip():
        _add(vc, "note", str(c["notes"]).strip())
    labels = [str(x) for x in c.get("labels") or [] if str(x).strip()]
    if labels:
        _add(vc, "categories", labels)
    if photo is not None:
        data, ctype = photo
        prop = vc.add("photo")
        prop.value = data
        prop.encoding_param = "b"
        sub = (ctype.split("/", 1)[1] if "/" in ctype else "jpeg").split(";")[0].upper()
        prop.type_param = sub
    return vc


def export(photos: bool = False) -> str:
    """The caller's whole book, by name, as vCard text ("" for no book)."""
    from . import contacts, contacts_photos

    conn = contacts._connect(create=False)
    if conn is None:
        return ""
    try:
        rows = conn.execute("SELECT id, name, card FROM contacts").fetchall()
    finally:
        conn.close()
    out = []
    for cid, name, raw in sorted(rows, key=lambda r: contacts.fold(r[1] or "")):
        pic = None
        if photos:
            got = contacts_photos.photo(cid)
            if got is not None:
                pic = (got[0], got[1])
        try:
            out.append(vcard_of(cid, json.loads(raw), name, pic).serialize())
        except Exception as e:  # noqa: BLE001 — one odd card does not lose the book
            _failures.swallowed(e)
    return "".join(out)


def filename(today: dt.date | None = None) -> str:
    return f"contacts-{(today or dt.date.today()).isoformat()}.vcf"
