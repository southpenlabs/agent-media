"""Contacts from a CardDAV address book (docs/proposals/2026-11-20-contacts-carddav.md,
steps 1 and 2).

David, 20 Nov 2026: contacts should live on an open standard he hosts, not
in Google's People API. The book is Radicale on red2, loaded from Google
the same day; this module reads it as the `carddav` source and writes the
card's edits back to it.

* **Settings** (the owner's environment; another account's `contacts.json`
  `carddav_url`, `carddav_user`, `carddav_password_file`):
  `MEDIA_CARDDAV_URL`, the address book's collection
  (`https://dav.example.org/david/contacts/`); `MEDIA_CARDDAV_USER`;
  `MEDIA_CARDDAV_PASSWORD_FILE`, a file holding the password (read at each
  use, never logged). Off while the URL is unset; `carddav` in
  `MEDIA_CONTACTS_SOURCES` (or that list unset) reads it.
* **Reading.** A `sync-collection` REPORT (RFC 6578) with the sync token
  kept in the store's meta (`carddav_sync_token`): the first time, or when
  the server no longer knows the token, an empty token lists every member;
  a server without sync-collection is listed by a `PROPFIND` (Depth 1).
  Only members whose etag moved are fetched, by `addressbook-multiget`, in
  batches. Each vCard is parsed with vobject into the card the other
  sources make (names, phones, emails, addresses, organisations, websites,
  birthday, notes); a group card (Apple's `X-ADDRESSBOOKSERVER-KIND:group`
  with `-MEMBER:urn:uuid:<UID>`, as the import made and DAVx⁵ reads) is a
  label, and its members carry it. Records: `carddav` (sid the vCard's
  `UID`, meta its `href`, `etag`, the Google resource it came from) and
  `carddav-group` (sid the group's `UID`). A card's picture (`PHOTO`,
  inline) is kept by contacts_photos as the record's own.
* **Old chat addresses.** The import turned Google's MSN-gateway emails
  into `IMPP:xmpp:` entries; those are handed to the card as emails again
  (type "Old chat") so contacts.py folds them away into `old_addresses`
  exactly as Google's are.
* **Writing** (contacts_edit.py): `fetch` GETs a member, `put` PUTs it with
  `If-Match` (412 is `Changed`), or `If-None-Match: *` for a new one.

Errors: `Unreachable` (no answer, a refused sign-in, a server error) and
`Refused` (the server would not take a write). Nothing here logs a card.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_media_core import failures as _failures

log = logging.getLogger("agent-media.server.contacts_carddav")

TIMEOUT_S = 60.0
#: Members fetched per addressbook-multiget.
BATCH = 100
#: The record kinds this source writes.
KIND = "carddav"
GROUP_KIND = "carddav-group"

DAV = "DAV:"
CARD = "urn:ietf:params:xml:ns:carddav"

#: `(status, headers with lower-case names, body)`.
Response = tuple[int, dict[str, str], bytes]
Http = Callable[[str, str, "bytes | None", dict[str, str]], Response]


class Error(Exception):
    pass


class NotConfigured(Error):
    pass


class Unreachable(Error):
    """No answer, a refused sign-in, or a server error."""


class Refused(Error):
    """The server would not take a write (other than an etag mismatch)."""


class Changed(Error):
    """412: the member is not the version the write was based on."""


class _TokenGone(Error):
    pass


class _NoSync(Error):
    pass


# --- settings -------------------------------------------------------------------

@dataclass(frozen=True)
class Config:
    url: str
    user: str
    password: str = ""

    @property
    def collection(self) -> str:
        return self.url if self.url.endswith("/") else self.url + "/"


def _setting(env: str, key: str) -> str:
    from . import contacts

    return (contacts.setting(env, key) or "").strip()


def configured() -> bool:
    """The book's CardDAV collection is set."""
    return bool(_setting("MEDIA_CARDDAV_URL", "carddav_url"))


def config() -> Config | None:
    """The current book's CardDAV settings, or None when off. The password
    file is read now; one that cannot be read is Unreachable (said, not a
    crash)."""
    url = _setting("MEDIA_CARDDAV_URL", "carddav_url")
    if not url:
        return None
    user = _setting("MEDIA_CARDDAV_USER", "carddav_user")
    pfile = _setting("MEDIA_CARDDAV_PASSWORD_FILE", "carddav_password_file")
    password = ""
    if pfile:
        try:
            password = Path(pfile).expanduser().read_text().strip()
        except OSError as e:
            raise Unreachable(f"the CardDAV password file cannot be read ({e.strerror})") from None
    return Config(url=url, user=user, password=password)


# --- HTTP -----------------------------------------------------------------------

def _http(method: str, url: str, body: bytes | None, headers: dict[str, str]) -> Response:
    """One request; an HTTP error is a status, a network failure Unreachable."""
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            return resp.status, {k.lower(): v for k, v in resp.headers.items()}, resp.read()
    except urllib.error.HTTPError as e:
        try:
            data = e.read()
        except OSError:
            data = b""
        return e.code, {k.lower(): v for k, v in (e.headers or {}).items()}, data
    except (urllib.error.URLError, OSError) as e:
        reason = getattr(e, "reason", None) or e
        raise Unreachable(f"the address book cannot be reached ({reason})") from None


def _x(tag: str, ns: str = DAV) -> str:
    return f"{{{ns}}}{tag}"


class Client:
    """The address book over CardDAV: `cfg` its settings, `http` the one
    seam a test replaces."""

    def __init__(self, cfg: Config, http: Http | None = None):
        self.cfg = cfg
        self.http = http or _http

    def url(self, href: str) -> str:
        return urllib.parse.urljoin(self.cfg.collection, href)

    def href(self, url: str) -> str:
        """A member's href as the server names it (its path)."""
        return urllib.parse.urlsplit(self.url(url)).path

    def request(self, method: str, href: str, body: bytes | None = None,
                headers: dict[str, str] | None = None) -> Response:
        h = {"User-Agent": "agent-media contacts"}
        if self.cfg.user or self.cfg.password:
            tok = base64.b64encode(f"{self.cfg.user}:{self.cfg.password}".encode()).decode()
            h["Authorization"] = f"Basic {tok}"
        h.update(headers or {})
        status, hdrs, data = self.http(method, self.url(href), body, h)
        if status in (401, 403) and method not in ("REPORT",):
            raise Unreachable(f"the address book refused the sign-in ({status})")
        if status >= 500:
            raise Unreachable(f"the address book answered {status}")
        return status, hdrs, data

    def _xml(self, method: str, body: str, depth: str = "1") -> tuple[int, ET.Element | None]:
        status, _, data = self.request(method, self.cfg.collection, body.encode(), {
            "Content-Type": "application/xml; charset=utf-8", "Depth": depth})
        try:
            root = ET.fromstring(data) if data.strip() else None
        except ET.ParseError:
            root = None
        return status, root

    def sync_collection(self, token: str | None) -> tuple[dict[str, str], list[str], str | None]:
        """`({href: etag} changed or new, [href] gone, new token)`; with no
        token, every member. _TokenGone when the server no longer knows the
        token, _NoSync when it has no sync-collection."""
        body = ('<?xml version="1.0" encoding="utf-8"?>'
                '<d:sync-collection xmlns:d="DAV:">'
                f'<d:sync-token>{_esc(token or "")}</d:sync-token>'
                '<d:sync-level>1</d:sync-level><d:prop><d:getetag/></d:prop>'
                '</d:sync-collection>')
        status, root = self._xml("REPORT", body)
        if status in (403, 409) and root is not None and root.find(_x("valid-sync-token")) is not None:
            raise _TokenGone()
        if status in (400, 403, 405, 415, 501) and not token:
            raise _NoSync()
        if status == 401:
            raise Unreachable("the address book refused the sign-in (401)")
        if status != 207 or root is None:
            if token:
                raise _TokenGone()
            raise Unreachable(f"the address book's sync answered {status}")
        changed: dict[str, str] = {}
        gone: list[str] = []
        coll = self.href(self.cfg.collection)
        for r in root.findall(_x("response")):
            href = urllib.parse.unquote(r.findtext(_x("href")) or "")
            if not href or href.rstrip("/") == coll.rstrip("/"):
                continue
            top = r.findtext(_x("status")) or ""
            if " 404 " in f"{top} ":
                gone.append(href)
                continue
            etag = r.findtext(f"{_x('propstat')}/{_x('prop')}/{_x('getetag')}")
            if etag is not None:
                changed[href] = etag.strip()
        return changed, gone, (root.findtext(_x("sync-token")) or None)

    def listing(self) -> dict[str, str]:
        """Every member's etag, by a PROPFIND (Depth 1)."""
        body = ('<?xml version="1.0" encoding="utf-8"?>'
                '<d:propfind xmlns:d="DAV:"><d:prop><d:getetag/><d:resourcetype/>'
                '</d:prop></d:propfind>')
        status, root = self._xml("PROPFIND", body)
        if status != 207 or root is None:
            raise Unreachable(f"the address book's listing answered {status}")
        out: dict[str, str] = {}
        coll = self.href(self.cfg.collection)
        for r in root.findall(_x("response")):
            href = urllib.parse.unquote(r.findtext(_x("href")) or "")
            if not href or href.rstrip("/") == coll.rstrip("/"):
                continue
            etag = r.findtext(f"{_x('propstat')}/{_x('prop')}/{_x('getetag')}")
            if etag is not None:
                out[href] = etag.strip()
        return out

    def multiget(self, hrefs: list[str], batch: int = BATCH) -> dict[str, tuple[str, str]]:
        """`{href: (etag, vCard text)}` for those of `hrefs` the server has."""
        out: dict[str, tuple[str, str]] = {}
        for i in range(0, len(hrefs), batch):
            part = hrefs[i:i + batch]
            body = ('<?xml version="1.0" encoding="utf-8"?>'
                    '<c:addressbook-multiget xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:carddav">'
                    '<d:prop><d:getetag/><c:address-data/></d:prop>'
                    + "".join(f"<d:href>{_esc(urllib.parse.quote(h))}</d:href>" for h in part)
                    + '</c:addressbook-multiget>')
            status, root = self._xml("REPORT", body)
            if status != 207 or root is None:
                raise Unreachable(f"the address book's multiget answered {status}")
            for r in root.findall(_x("response")):
                href = urllib.parse.unquote(r.findtext(_x("href")) or "")
                prop = f"{_x('propstat')}/{_x('prop')}"
                text = r.findtext(f"{prop}/{_x('address-data', CARD)}")
                if href and text:
                    out[href] = ((r.findtext(f"{prop}/{_x('getetag')}") or "").strip(), text)
        return out

    def fetch(self, href: str) -> tuple[str, str] | None:
        """`(etag, vCard text)` of one member, None when it is gone."""
        status, hdrs, data = self.request("GET", href)
        if status == 404:
            return None
        if status != 200:
            raise Unreachable(f"the address book answered {status} for a card")
        return hdrs.get("etag", ""), data.decode("utf-8", "replace")

    def put(self, href: str, text: str, etag: str = "", create: bool = False) -> str:
        """Write a member: over the version `etag` names (If-Match), or, with
        `create`, only if it does not exist yet. The new etag ("" when the
        server sends none). Changed on 412, Refused on another refusal."""
        h = {"Content-Type": "text/vcard; charset=utf-8"}
        if etag:
            h["If-Match"] = etag
        elif create:
            h["If-None-Match"] = "*"
        status, hdrs, _ = self.request("PUT", href, text.encode("utf-8"), h)
        if status == 412:
            raise Changed(href)
        if status not in (200, 201, 204):
            raise Refused(f"the address book refused the change ({status})")
        return hdrs.get("etag", "")


def _store() -> Any:
    """The book's store, made if need be (contacts._connect)."""
    from . import contacts

    conn = contacts._connect()
    assert conn is not None
    return conn


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# --- vCards into cards ----------------------------------------------------------

def parse(text: str) -> Any:
    """One vCard (vobject's component)."""
    import vobject

    return vobject.readOne(text)


def _props(vc: Any, name: str) -> list:
    return list(vc.contents.get(name.lower(), []))


def _value(prop: Any) -> str:
    v = prop.value
    if isinstance(v, list):
        v = ",".join(str(x) for x in v)
    return str(v or "")


def _types(prop: Any) -> list[str]:
    out = []
    for t in prop.params.get("TYPE", []):
        out += [x.strip().upper() for x in str(t).split(",") if x.strip()]
    return out


#: vCard TYPE words to the names the card uses (contacts_android's), by kind.
_PHONE = {"CELL": "Mobile", "MOBILE": "Mobile", "HOME": "Home", "WORK": "Work",
          "MAIN": "Main", "OTHER": "Other", "PAGER": "Pager", "HOMEFAX": "Home fax",
          "WORKFAX": "Work fax", "CAR": "Car", "CALLBACK": "Callback",
          "COMPANY_MAIN": "Company main", "WORK_MOBILE": "Work mobile"}
_EMAIL = {"HOME": "Home", "WORK": "Work", "OTHER": "Other", "MOBILE": "Mobile"}
_POSTAL = {"HOME": "Home", "WORK": "Work", "OTHER": "Other"}
_URL = {"HOMEPAGE": "Homepage", "BLOG": "Blog", "PROFILE": "Profile", "HOME": "Home",
        "WORK": "Work", "FTP": "FTP", "OTHER": "Other"}
KNOWN = {"phones": _PHONE, "emails": _EMAIL, "addresses": _POSTAL, "websites": _URL}
_NOISE = {"INTERNET", "PREF", "VOICE", "X400", "MSG"}


def _custom(word: str) -> str:
    """A custom label as written: the import wrote Google's upper-cased."""
    return word.capitalize() if word.isupper() else word


def label_of(prop: Any, kind: str) -> str:
    """The label a typed property shows (`Mobile`, `Work fax`, a custom one)."""
    ts = [t for t in _types(prop) if t not in _NOISE]
    if kind == "phones":
        if "FAX" in ts:
            return "Work fax" if "WORK" in ts else "Home fax" if "HOME" in ts else "Fax"
        if "CELL" in ts and "WORK" in ts:
            return "Work mobile"
    known = KNOWN[kind]
    for t in ts:
        if t in known:
            return known[t]
    return _custom(" ".join(str(x) for x in prop.params.get("TYPE", []) if
                            str(x).strip().upper() not in _NOISE).strip()) if ts else ""


def types_for(kind: str, label: str | None) -> list[str]:
    """The TYPE words a label is written as; the kind's default for none."""
    default = {"phones": ["CELL"], "emails": ["INTERNET", "OTHER"], "addresses": ["HOME"],
               "websites": ["OTHER"]}[kind]
    if not label:
        return default
    low = label.strip().casefold()
    if kind == "phones":
        special = {"mobile": ["CELL"], "home fax": ["HOME", "FAX"], "work fax": ["WORK", "FAX"],
                   "work mobile": ["WORK", "CELL"], "fax": ["FAX"]}
        if low in special:
            return special[low]
    for word, name in KNOWN[kind].items():
        if name.casefold() == low and word not in ("MOBILE", "HOMEFAX", "WORKFAX"):
            return (["INTERNET"] if kind == "emails" else []) + [word]
    return (["INTERNET"] if kind == "emails" else []) + [label.strip()]


def is_group(vc: Any) -> bool:
    kinds = [_value(p).strip().lower() for p in
             _props(vc, "X-ADDRESSBOOKSERVER-KIND") + _props(vc, "KIND")]
    return "group" in kinds


def uid_of(vc: Any) -> str:
    got = _props(vc, "UID")
    return _value(got[0]).strip() if got else ""


def _member_uid(v: str) -> str:
    v = v.strip()
    for pre in ("urn:uuid:", "urn:uid:"):
        if v.lower().startswith(pre):
            return v[len(pre):]
    return v


def group_of(vc: Any) -> dict:
    """A group card as kept: `{name, members: [UID], resource}`."""
    fn = _props(vc, "FN")
    members = [_member_uid(_value(p)) for p in
               _props(vc, "X-ADDRESSBOOKSERVER-MEMBER") + _props(vc, "MEMBER")]
    res = _props(vc, "X-GOOGLE-RESOURCE")
    return {"name": _value(fn[0]).strip() if fn else "",
            "members": [m for m in members if m],
            "resource": _value(res[0]).strip() if res else ""}


def _birthday(prop: Any) -> str:
    v = _value(prop).strip()
    if "T" in v:
        v = v.split("T", 1)[0]
    if re.fullmatch(r"\d{8}", v):
        v = f"{v[:4]}-{v[4:6]}-{v[6:]}"
    if re.fullmatch(r"--\d{4}", v):
        v = f"--{v[2:4]}-{v[4:]}"
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", v)
    if m and (prop.params.get("X-APPLE-OMIT-YEAR") or m.group(1) == "1604"):
        return f"--{m.group(2)}-{m.group(3)}"
    return v


def address_pairs(vc: Any) -> list[tuple[Any, Any]]:
    """The card's addresses as `(ADR, LABEL)` pairs (either may be None): a
    LABEL goes with the ADR of the same TYPE at the same place among them,
    as the import wrote them one after the other."""
    adrs, labels = _props(vc, "ADR"), _props(vc, "LABEL")
    pairs: list[tuple[Any, Any]] = []
    left = list(labels)
    for a in adrs:
        t = _types(a)
        lab = next((x for x in left if _types(x) == t), None)
        if lab is not None:
            left.remove(lab)
        pairs.append((a, lab))
    pairs += [(None, x) for x in left]
    return pairs


def formatted(adr: Any, lab: Any) -> str:
    if lab is not None and _value(lab).strip():
        return _value(lab).strip()
    if adr is None:
        return ""
    a = adr.value
    parts = [getattr(a, k, "") for k in ("box", "extended", "street", "city", "region",
                                          "code", "country")]
    return ", ".join(str(x).strip() for x in parts if str(x or "").strip())


def card_of(vc: Any, default_region: str = "AU") -> dict:
    """A person's vCard as the store keeps it, the shape contacts.card_of
    makes of a Google person. `old_chat`: the old gateway addresses (IMPP
    xmpp:), which contacts.py hands on as emails to fold away."""
    from . import contacts

    fn = _props(vc, "FN")
    display = _value(fn[0]).strip() if fn else ""
    given = family = ""
    for n in _props(vc, "N")[:1]:
        given = str(getattr(n.value, "given", "") or "").strip()
        family = str(getattr(n.value, "family", "") or "").strip()
    names = [{"display": display, "given": given, "family": family}] if (
        display or given or family) else []
    phones = []
    for p in _props(vc, "TEL"):
        raw = _value(p).strip()
        if raw:
            phones.append({"value": contacts.e164(raw, default_region), "raw": raw,
                           "type": label_of(p, "phones")})
    emails = []
    for e in _props(vc, "EMAIL"):
        v = _value(e).strip()
        if v:
            emails.append({"value": v, "type": label_of(e, "emails")})
    old_chat, impp = [], []
    for i in _props(vc, "IMPP"):
        v = _value(i).strip()
        scheme, _, addr = v.partition(":")
        if scheme.lower() == "xmpp" and contacts.is_gateway(addr):
            old_chat.append(addr)
        elif v:
            impp.append(v)
    addresses = []
    for adr, lab in address_pairs(vc):
        text = formatted(adr, lab)
        if text:
            addresses.append({"formatted": text,
                              "type": label_of(adr if adr is not None else lab, "addresses")})
    orgs = []
    titles = _props(vc, "TITLE")
    for i, o in enumerate(_props(vc, "ORG")):
        parts = o.value if isinstance(o.value, list) else [o.value]
        title = _value(titles[i]).strip() if i < len(titles) else ""
        orgs.append({"company": str(parts[0] if parts else "").strip(), "title": title})
    for t in titles[len(orgs):]:
        orgs.append({"company": "", "title": _value(t).strip()})
    websites = [{"url": _value(u).strip(), "type": label_of(u, "websites")}
                for u in _props(vc, "URL") if _value(u).strip()]
    notes = "\n\n".join(_value(x).strip() for x in _props(vc, "NOTE") if _value(x).strip())
    name = display if display and display != "(no name)" else ""
    name = name or " ".join(x for x in (given, family) if x) \
        or next((e["value"] for e in emails), "") \
        or next((p["raw"] for p in phones), "") or "(no name)"
    out: dict = {"name": name, "names": names, "phones": phones, "emails": emails,
                 "addresses": addresses, "organizations": orgs, "notes": notes}
    if websites:
        out["websites"] = websites
    bday = _props(vc, "BDAY")
    if bday and _birthday(bday[0]):
        out["birthday"] = _birthday(bday[0])
    if old_chat:
        out["old_chat"] = old_chat
    if impp:
        out["impp"] = impp
    return out


def photo_of(vc: Any) -> bytes | None:
    """The card's inline picture, or None (a picture by URL is not fetched)."""
    for p in _props(vc, "PHOTO"):
        v = p.value
        if isinstance(v, bytes) and v:
            return v
        if isinstance(v, str) and v.startswith("data:") and ";base64," in v:
            try:
                return base64.b64decode(v.split(",", 1)[1])
            except ValueError:
                continue
    return None


# --- the sync -------------------------------------------------------------------

def sync(full: bool = False, cfg: Config | None = None, http: Http | None = None) -> dict:
    """Bring the `carddav` records up to date. Unreachable raises, said in
    `carddav_last_error`, and the last good copy stays."""
    from . import contacts, contacts_photos

    with contacts._SYNC_LOCK:
        try:
            cfg = cfg or config()
            if cfg is None:
                raise NotConfigured("MEDIA_CARDDAV_URL is not set")
            client = Client(cfg, http)
            conn = _store()
            try:
                token = None if full else contacts._meta(conn, "carddav_sync_token")
                if contacts._meta(conn, "carddav_url") != cfg.collection:
                    token = None              # another book: read it whole
                known: dict[str, tuple[str, str, str]] = {}
                for kind, sid, meta in conn.execute(
                        "SELECT kind, sid, meta FROM records WHERE kind IN (?, ?)",
                        (KIND, GROUP_KIND)):
                    m = json.loads(meta)
                    known[m.get("href") or ""] = (kind, sid, m.get("etag") or "")
            finally:
                conn.close()
            try:
                changed, gone, new_token = client.sync_collection(token)
            except _TokenGone:
                log.info("contacts: the CardDAV sync token is gone, reading it whole")
                token = None
                changed, gone, new_token = client.sync_collection(None)
            except _NoSync:
                token, new_token = None, None
                changed, gone = client.listing(), []
            if token is None:
                gone = [h for h in known if h not in changed]
            want = [h for h, etag in changed.items()
                    if h not in known or known[h][2] != etag or not etag]
            got = client.multiget(want) if want else {}
        except (Error, OSError) as e:
            contacts._failed(KIND, e)
            raise
        reg = contacts.region()
        rows: list[tuple[str, str, str, str]] = []
        photos: dict[str, tuple[str, str, str] | None] = {}
        unread = 0
        for href, (etag, text) in got.items():
            try:
                vc = parse(text)
            except Exception as e:  # noqa: BLE001 — one bad card is not the book
                _failures.swallowed(e)
                unread += 1
                continue
            uid = uid_of(vc) or href
            res = _props(vc, "X-GOOGLE-RESOURCE")
            meta = {"href": href, "etag": etag or changed.get(href, ""),
                    "resource": _value(res[0]).strip() if res else ""}
            if is_group(vc):
                rows.append((GROUP_KIND, uid, json.dumps(group_of(vc)), json.dumps(meta)))
                continue
            pic = photo_of(vc)
            kept = contacts_photos.keep_inline(pic) if pic else None
            photos[uid] = kept
            if kept:
                meta["photo"] = kept[2]
            rows.append((KIND, uid, json.dumps(card_of(vc, reg)), json.dumps(meta)))
        now = time.time()
        with contacts._WRITE:
            conn = _store()
            try:
                conn.execute("BEGIN IMMEDIATE")
                deleted = 0
                for href in gone:
                    if href in known:
                        kind, sid, _ = known[href]
                        deleted += conn.execute("DELETE FROM records WHERE kind=? AND sid=?",
                                                (kind, sid)).rowcount
                        conn.execute("DELETE FROM photo_src WHERE kind=? AND sid=?", (KIND, sid))
                for kind, sid, card, meta in rows:
                    m = json.loads(meta)
                    was = known.get(m["href"])
                    if was and (was[0], was[1]) != (kind, sid):
                        conn.execute("DELETE FROM records WHERE kind=? AND sid=?", was[:2])
                    conn.execute("INSERT OR REPLACE INTO records (kind, sid, card, meta) "
                                 "VALUES (?, ?, ?, ?)", (kind, sid, card, meta))
                    if kind == KIND:
                        kept = photos.get(sid)
                        if kept:
                            contacts_photos._put(conn, KIND, sid, kept[2], kept[0], kept[1],
                                                 now, 0, 0.0)
                        else:
                            conn.execute("DELETE FROM photo_src WHERE kind=? AND sid=?",
                                         (KIND, sid))
                contacts._set_meta(conn, "carddav_sync_token", new_token)
                contacts._set_meta(conn, "carddav_url", cfg.collection)
                contacts._set_meta(conn, "carddav_synced_at", str(round(now, 3)))
                contacts._set_meta(conn, "carddav_last_error", None)
                if rows or deleted:
                    contacts._rebuild(conn)
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            finally:
                total = conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
                conn.close()
    out = {"source": KIND, "full": token is None, "changed": len(rows), "deleted": deleted,
           "contacts": total}
    if unread:
        out["unreadable"] = unread
    return out


# --- writing a card -------------------------------------------------------------

def new_uid() -> str:
    return str(uuid.uuid4())


def rev() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def group_text(uid: str, name: str, members: list[str]) -> str:
    """A new group card, as the import made them."""
    import vobject

    vc = vobject.vCard()
    vc.add("uid").value = uid
    vc.add("fn").value = name
    vc.add("n").value = vobject.vcard.Name(family=name)
    vc.add("x-addressbookserver-kind").value = "group"
    for m in members:
        vc.add("x-addressbookserver-member").value = f"urn:uuid:{m}"
    vc.add("rev").value = rev()
    return vc.serialize()


def set_member(text: str, uid: str, member: bool) -> str | None:
    """A group card's text with `uid` added to or taken off its members;
    None when it already is as wanted."""
    vc = parse(text)
    props = _props(vc, "X-ADDRESSBOOKSERVER-MEMBER")
    have = [p for p in props if _member_uid(_value(p)) == uid]
    if member == bool(have):
        return None
    if member:
        vc.add("x-addressbookserver-member").value = f"urn:uuid:{uid}"
    else:
        for p in have:
            vc.remove(p)
    for r in _props(vc, "REV"):
        vc.remove(r)
    vc.add("rev").value = rev()
    return vc.serialize()


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]
