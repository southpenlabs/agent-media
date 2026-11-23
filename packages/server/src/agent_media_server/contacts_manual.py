"""People made here: added by hand (`POST /contacts`) or read from a vCard
file (`POST /contacts/import` with a .vcf). The `manual` records of the
caller's own book (`contacts.db`, contacts.py), so a person with no Google
and no CardDAV book still has a way in (contacts sweep, 24 Nov 2026, K1/K2:
"every app lets you add a contact by hand"; Google, Apple and Outlook all
take vCard).

Each is a record like a source's, kept across syncs (no sync reads it
again): in the book's rebuild (contacts._rebuild_manual) it joins the one
contact sharing its number, else its address, else stands as its own. A
.vcf's card is keyed by its UID (else its content), so the same file
imported twice is the same people, not twice as many. The vCard reading is
the CardDAV book's (contacts_carddav.card_of).
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from typing import Any

from . import contacts

KIND = "manual"

#: The most cards one .vcf may make.
VCF_MAX = 20000
#: The longest name, number or address kept.
FIELD_MAX = 300


class NotAVcard(ValueError):
    pass


def looks_like_vcard(data: bytes, name: str = "") -> bool:
    """A vCard file: `BEGIN:VCARD` first (past a BOM and blank lines), or a
    .vcf/.vcard name."""
    head = data[:64].lstrip(b"\xef\xbb\xbf \t\r\n").upper()
    return head.startswith(b"BEGIN:VCARD") or name.lower().endswith((".vcf", ".vcard"))


def _text(v: Any) -> str:
    return str(v or "").strip()[:FIELD_MAX]


def _items(xs: Any) -> list[str]:
    """A list of strings, or one string split on commas and new lines."""
    if isinstance(xs, str):
        xs = xs.replace("\n", ",").split(",")
    if not isinstance(xs, list):
        return []
    out = []
    for x in xs:
        v = _text(x.get("value") if isinstance(x, dict) else x)
        if v and v not in out:
            out.append(v)
    return out


def card_from(body: dict, region: str | None = None) -> dict:
    """The card a hand-made person keeps, from `{name, phones, emails}`
    (each a list or one string); ValueError when it names no one."""
    reg = region or contacts.region()
    name = _text(body.get("name"))
    phones = [{"value": contacts.e164(p, reg), "raw": p, "type": "Mobile"}
              for p in _items(body.get("phones"))]
    emails = [{"value": e, "type": ""} for e in _items(body.get("emails"))]
    bad = [e["value"] for e in emails if "@" not in e["value"]]
    if bad:
        raise ValueError(f"not an email address: {bad[0]}")
    if not (name or phones or emails):
        raise ValueError("give a name, a number or an email")
    given, _, family = name.partition(" ")
    shown = name or next((e["value"] for e in emails), "") or phones[0]["raw"]
    return {"name": shown,
            "names": [{"display": name, "given": given, "family": family}] if name else [],
            "phones": phones, "emails": emails, "addresses": [], "organizations": [],
            "notes": _text(body.get("notes"))}


def _put(recs: list[tuple[str, dict, dict]]) -> dict[str, str]:
    """The records into the book and the book rebuilt; each sid to the
    contact holding it."""
    with contacts._SYNC_LOCK, contacts._WRITE:
        conn = contacts._connect()
        assert conn is not None
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.executemany(
                "INSERT OR REPLACE INTO records (kind, sid, card, meta) VALUES (?, ?, ?, ?)",
                [(KIND, sid, json.dumps(card), json.dumps(meta)) for sid, card, meta in recs])
            contacts._rebuild(conn)
            contacts._set_meta(conn, f"{KIND}_synced_at", str(round(time.time(), 3)))
            mine = {sid for sid, _, _ in recs}
            held: dict[str, str] = {}
            for cid, raw in conn.execute("SELECT id, card FROM contacts"):
                for s in json.loads(raw).get("sources") or []:
                    if s.get("kind") == KIND and s.get("id") in mine:
                        held[s["id"]] = cid
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()
    contacts._fresh()
    return held


def create(body: dict) -> dict:
    """`POST /contacts {name, phones, emails, notes?}`: one person made in
    the caller's book; `{id, joined}`, `joined` when they share a number or
    address with someone already there and were made one with them."""
    if not isinstance(body, dict):
        raise ValueError("expected {name, phones, emails}")
    card = card_from(body)
    sid = f"hand:{uuid.uuid4().hex}"
    held = _put([(sid, card, {"via": "hand", "at": round(time.time(), 3)})])
    cid = held.get(sid) or contacts.contact_id(KIND, sid)
    return {"id": cid, "joined": cid != contacts.contact_id(KIND, sid)}


def _sid_of(vc: Any, card: dict) -> str:
    from . import contacts_carddav as cd

    uid = cd.uid_of(vc)
    if uid:
        return f"vcf:{uid[:200]}"
    digest = hashlib.sha256(json.dumps(card, sort_keys=True).encode()).hexdigest()[:24]
    return f"vcf:#{digest}"


def import_vcf(data: bytes, name: str = "") -> dict:
    """A .vcf of one or many cards into the caller's book (group cards
    skipped); the counts, never a name. NotAVcard when no person is in it."""
    import vobject

    from . import contacts_carddav as cd

    text = data.decode("utf-8", errors="replace").lstrip("﻿")
    reg = contacts.region()
    recs: list[tuple[str, dict, dict]] = []
    skipped = 0
    try:
        for vc in vobject.readComponents(text, ignoreUnreadable=True):
            if getattr(vc, "name", "").upper() != "VCARD" or cd.is_group(vc):
                continue
            try:
                card = cd.card_of(vc, reg)
            except Exception:  # noqa: BLE001 — one odd card is not the file
                skipped += 1
                continue
            card.pop("old_chat", None)
            if card.get("name") == "(no name)" and not (card["phones"] or card["emails"]):
                skipped += 1
                continue
            recs.append((_sid_of(vc, card), card,
                         {"via": "vcf", "file": name[-120:], "at": round(time.time(), 3)}))
            if len(recs) >= VCF_MAX:
                break
    except Exception as e:  # noqa: BLE001 — vobject raises its own on a broken file
        if not recs:
            raise NotAVcard(f"that vCard file does not read ({type(e).__name__})") from e
    if not recs:
        raise NotAVcard("no people in that vCard file")
    held = _put(recs)
    joined = sum(1 for sid, _, _ in recs if held.get(sid) not in
                 (None, contacts.contact_id(KIND, sid)))
    return {"imported": {"vcard": len({sid for sid, _, _ in recs})}, "people": len(held),
            "joined": joined, "skipped": skipped, "contacts": _count()}


def _count() -> int:
    conn = contacts._connect(create=False)
    if conn is None:
        return 0
    try:
        return conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
    finally:
        conn.close()
