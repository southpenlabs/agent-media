"""Sharing a place on the AT Protocol: a `com.sasonica.place.post` record in
the person's own repo, and with it, if asked, an ordinary Bluesky post
(docs/proposals/2026-11-26-places-on-atproto.md, build step 1; David, 26 Nov
2026: "places are a great start"). Contract: docs/server-contract.md §6.50a.

  GET  /map/share/accounts          → {accounts: [{handle, did, pds, added,
                                       default, signed_in}]} (never a password)
  POST /map/share/accounts {handle, app_password, pds?}  (admin)
                                    → {account}: resolved, signed in, kept
  POST /map/share/accounts/remove {account}  (admin) → {removed}
  POST /map/share/accounts/default {account}  (admin) → {accounts}: that
                                       account first, so it is the default
  POST /map/share/preview {place, text?, tags?, photos?, blur?, account?,
       bluesky_post?, langs?}  (admin)
                                    → {token, records, uris, ...}: exactly
                                       what would be written, nothing sent
  POST /map/share/confirm {token}  (admin) → {uri, bsky_uri?, bsky_link?}
  GET  /map/share/posts?account=&cursor=  → {posts, cursor?}
  POST /map/share/delete {uri, account?}  (admin) → {deleted: [uri…]}

The rules (the proposal's "Privacy (the hard rule)"):

- Nothing is written by itself. A preview stages the records and their
  photos on the server under a token, the sha-256 of what would be written;
  only `confirm` with that token writes, and then exactly what was shown
  (`createdAt` alone is set again, to the time it was posted).
- A place's own coordinates, never the phone's: a place from the phone,
  "Your things", the car, a trip or the location history layers, Home or
  Work, or a point within HOME_M of Home or Work or THING_M of a placed thing
  is refused; so is a point with no OpenStreetMap id within PHONE_M of where
  a phone last said it was (a pin dropped on oneself).
- Photos lose their metadata (EXIF, XMP, IPTC, comments, and anything after
  the image, such as a motion photo's video) before they leave, the
  orientation alone kept; one over the blob limit (the lesser of the PDS's
  and Bluesky's 1 MB for an embedded image) is made smaller as a JPEG.

Accounts: an app password per account (Bluesky Settings → App passwords),
kept with its session tokens 0600 in `state_dir()/atproto-accounts.json`,
never logged and never answered. A handle is resolved to its DID (its PDS's
resolveHandle when one is named, its `/.well-known/atproto-did`, else the
Bluesky AppView's resolveHandle), the DID to its document (plc.directory
for did:plc, `/.well-known/did.json` for did:web), which must name the handle
back, and the document to its PDS. Kept per book, as map_saved.py keeps
places: the owner's ("") for the host and the owner's devices.

`MEDIA_ATPROTO_PLC_URL` and `MEDIA_ATPROTO_APPVIEW_URL` point elsewhere (the
tests use a fake PDS in the process; conftest refuses any other address).
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from agent_media_core import failures as _failures

PLC = "https://plc.directory"
APPVIEW = "https://public.api.bsky.app"
USER_AGENT = "agent-media (Sasonica; +https://sasonica.com)"
TIMEOUT_S = 20

POST = "com.sasonica.place.post"
BSKY_POST = "app.bsky.feed.post"
GEO = "community.lexicon.location.geo"
ADDRESS = "community.lexicon.location.address"

MAX_PHOTOS = 4
#: An image embedded in a Bluesky post, at most (app.bsky.embed.images).
BSKY_IMAGE_MAX = 1_000_000
#: A photo read from the disk, at most, before it is made smaller.
PHOTO_READ_MAX = 40 << 20
MAX_TEXT = 3000
MAX_NAME = 200
MAX_TAGS = 8
MAX_ALT = 1000
#: A Bluesky post's text, at most (graphemes; counted here as code points,
#: which is never fewer) and in bytes.
BSKY_TEXT = 300
BSKY_BYTES = 3000
MAX_ACCOUNTS = 10
#: How long a preview stays good for its confirm.
STAGE_S = 3600
#: Decimals written: a place's own (about 10 cm), and blurred (about 100 m).
DECIMALS = 6
BLUR_DECIMALS = 3

#: The Map's layers whose points are the person's, not places.
PRIVATE_LAYERS = frozenset({"phone", "things", "parked", "history", "trip", "trips", "home", "work"})
PRIVATE_SAVED = frozenset({"home", "work"})
HOME_M = 150
THING_M = 30
PHONE_M = 15

_APP_PASSWORD = re.compile(r"[a-z0-9]{4}-[a-z0-9]{4}-[a-z0-9]{4}-[a-z0-9]{4}")
_HANDLE = re.compile(r"([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]([a-z0-9-]{0,61}[a-z0-9])?")
_DID = re.compile(r"did:(plc:[a-z2-7]{24}|web:[a-zA-Z0-9.%-]+)")
_TAG = re.compile(r"[^a-z0-9-]+")
_OSM_TYPES = ("node", "way", "relation")

_lock = threading.Lock()

#: (method, url, headers, body) → (status, body). Tests pass their own.
Http = Callable[[str, str, dict, "bytes | None"], tuple[int, bytes]]


class ShareError(Exception):
    """A refusal with a status and a code, answered to the app as they are."""

    def __init__(self, status: int, code: str, error: str):
        super().__init__(error)
        self.status, self.code, self.error = status, code, error

    def detail(self) -> dict:
        return {"status": self.status, "code": self.code, "error": self.error}


def _http(method: str, url: str, headers: dict, body: bytes | None) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, method=method,
                                 headers={"User-Agent": USER_AGENT, **headers})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            return r.status, r.read(20_000_000)
    except urllib.error.HTTPError as e:
        return e.code, e.read(100_000)


@dataclass
class Deps:
    """Where this module reads and writes; each `None` is the real one."""

    accounts: Path | None = None
    stage: Path | None = None
    http: Http | None = None
    clock: Callable[[], float] = field(default=time.time)
    plc: str | None = None
    appview: str | None = None
    saved_places: Callable[[str], list] | None = None
    things: Callable[[str], list] | None = None
    phone_fixes: Callable[[], list] | None = None
    photo_bytes: Callable[[Any, str], bytes] | None = None
    country_at: Callable[[float, float], str] | None = None

    def accounts_path(self) -> Path:
        if self.accounts is not None:
            return self.accounts
        from agent_media_core._paths import state_dir

        return state_dir() / "atproto-accounts.json"

    def stage_dir(self) -> Path:
        if self.stage is not None:
            return self.stage
        from agent_media_core._paths import state_dir

        return state_dir() / "place-share"

    def get(self, method: str, url: str, headers: dict | None = None,
            body: bytes | None = None) -> tuple[int, bytes]:
        return (self.http or _http)(method, url, headers or {}, body)

    def plc_url(self) -> str:
        return (self.plc or os.environ.get("MEDIA_ATPROTO_PLC_URL") or PLC).rstrip("/")

    def appview_url(self) -> str:
        return (self.appview or os.environ.get("MEDIA_ATPROTO_APPVIEW_URL") or APPVIEW).rstrip("/")

    def saved(self, book: str) -> list:
        if self.saved_places is not None:
            return self.saved_places(book)
        from . import map_saved

        return list(map_saved._load().get(book) or [])

    def placed(self, book: str) -> list:
        if self.things is not None:
            return self.things(book)
        from . import map_things

        return list(map_things._load().get(book) or [])

    def fixes(self) -> list:
        if self.phone_fixes is not None:
            return self.phone_fixes()
        from . import phone

        return phone.shared_all()

    def photo(self, ref: Any, book: str) -> bytes:
        return (self.photo_bytes or _photo_bytes)(ref, book)

    def country(self, la: float, lo: float) -> str:
        return (self.country_at or _country_at)(la, lo)



_countries: dict[str, str] = {}


def _country_at(la: float, lo: float) -> str:
    """The two-letter country at a point, from the geocoder the Map already
    uses (Nominatim, a reverse lookup at country level), kept per tenth of a
    degree for the process; "" when it cannot say. For a card that gives an
    address line but no country (sasonica-app, mail #1367, 27 Nov 2026)."""
    key = f"{la:.1f},{lo:.1f}"
    if key in _countries:
        return _countries[key]
    from . import map_wiki

    try:
        q = {"lat": f"{la:.4f}", "lon": f"{lo:.4f}", "zoom": "3", "format": "jsonv2"}
        r = map_wiki._json(f"{map_wiki._nominatim_base()}/reverse?{urllib.parse.urlencode(q)}", None, nominatim=True)
        cc = str(((r or {}).get("address") or {}).get("country_code") or "").upper() if isinstance(r, dict) else ""
    except Exception as e:  # the lookup is a nicety: no country, no address record
        _failures.swallowed(e)
        return ""
    cc = cc if re.fullmatch(r"[A-Z]{2}", cc) else ""
    _countries[key] = cc
    return cc


_DEPS = Deps()


# --- XRPC -------------------------------------------------------------------

def _xrpc(d: Deps, base: str, nsid: str, *, params: dict | None = None, body: Any = None,
          raw: bytes | None = None, mime: str = "", token: str = "", method: str = "") -> tuple[int, dict]:
    url = f"{base.rstrip('/')}/xrpc/{nsid}"
    if params:
        url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})
    headers: dict = {"Accept": "application/json"}
    data: bytes | None = None
    if raw is not None:
        data, headers["Content-Type"] = raw, mime or "application/octet-stream"
    elif body is not None:
        data, headers["Content-Type"] = json.dumps(body, ensure_ascii=False).encode(), "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        status, got = d.get(method or ("POST" if data is not None else "GET"), url, headers, data)
    except OSError as e:
        _failures.swallowed(e)
        raise ShareError(502, "unreachable", f"{urllib.parse.urlsplit(base).netloc} did not answer") from None
    try:
        out = json.loads(got) if got else {}
    except ValueError:
        out = {}
    return status, out if isinstance(out, dict) else {}


def _refused(nsid: str, status: int, got: dict) -> ShareError:
    words = str(got.get("message") or got.get("error") or "").strip()[:300]
    return ShareError(502, "refused", f"the PDS refused {nsid.rsplit('.', 1)[-1]} ({status})"
                      + (f": {words}" if words else ""))


# --- who an account is ------------------------------------------------------

def _web_base(host: str) -> str:
    bare = host.split(":", 1)[0]
    return f"http://{host}" if bare in ("localhost", "127.0.0.1") else f"https://{host}"


def did_document(did: str, d: Deps | None = None) -> dict:
    d = d or _DEPS
    if did.startswith("did:plc:"):
        url = f"{d.plc_url()}/{did}"
    elif did.startswith("did:web:"):
        if ":" in did[len("did:web:"):]:
            raise ShareError(400, "bad_did", "a did:web with a path is not supported")
        host = urllib.parse.unquote(did[len("did:web:"):])
        url = f"{_web_base(host)}/.well-known/did.json"
    else:
        raise ShareError(400, "bad_did", "only did:plc and did:web are supported")
    try:
        status, body = d.get("GET", url, {"Accept": "application/did+ld+json, application/json"})
        doc = json.loads(body) if status == 200 else None
    except (OSError, ValueError) as e:
        _failures.swallowed(e)
        doc = None
    if not isinstance(doc, dict) or doc.get("id") != did:
        raise ShareError(502, "no_did_document", f"could not read the DID document for {did}")
    return doc


def _pds_of(doc: dict) -> str:
    for s in doc.get("service") or []:
        if isinstance(s, dict) and str(s.get("id", "")).endswith("#atproto_pds") \
                and s.get("type") == "AtprotoPersonalDataServer":
            ep = str(s.get("serviceEndpoint") or "").rstrip("/")
            if ep.startswith(("https://", "http://")):
                return ep
    raise ShareError(502, "no_pds", "the DID document names no PDS")


def _handle_of(doc: dict) -> str:
    for aka in doc.get("alsoKnownAs") or []:
        if isinstance(aka, str) and aka.startswith("at://"):
            return aka[5:].lower()
    return ""


def _did_for_handle(handle: str, pds_hint: str, d: Deps) -> str:
    tries: list[tuple[str, str]] = []
    if pds_hint:
        tries.append(("xrpc", pds_hint))
    tries.append(("well-known", f"{_web_base(handle)}/.well-known/atproto-did"))
    tries.append(("xrpc", d.appview_url()))
    for kind, where in tries:
        try:
            if kind == "xrpc":
                status, got = _xrpc(d, where, "com.atproto.identity.resolveHandle", params={"handle": handle})
                did = str(got.get("did") or "") if status == 200 else ""
            else:
                status, body = d.get("GET", where, {"Accept": "text/plain"})
                did = body.decode("utf-8", "replace").strip() if status == 200 else ""
        except (ShareError, OSError) as e:
            _failures.swallowed(e)
            continue
        if _DID.fullmatch(did):
            return did
    raise ShareError(404, "unknown_handle", f"could not find {handle}: check the handle")


def resolve(who: str, pds_hint: str = "", d: Deps | None = None) -> dict:
    """`{handle, did, pds}` for a handle (`@` or not) or a DID, the handle
    checked both ways (the DID document names it back)."""
    d = d or _DEPS
    who = who.strip().lstrip("@").lower() if not who.strip().startswith("did:") else who.strip()
    if who.startswith("did:"):
        if not _DID.fullmatch(who):
            raise ShareError(400, "bad_handle", "that is not a DID this server can read")
        doc = did_document(who, d)
        return {"handle": _handle_of(doc), "did": who, "pds": _pds_of(doc)}
    if not _HANDLE.fullmatch(who) or len(who) > 253:
        raise ShareError(400, "bad_handle", "a handle looks like name.bsky.social or david.sasonica.com")
    did = _did_for_handle(who, pds_hint.strip().rstrip("/"), d)
    doc = did_document(did, d)
    if f"at://{who}" not in [str(a).lower() for a in doc.get("alsoKnownAs") or []]:
        raise ShareError(400, "handle_mismatch", f"{did} does not name {who} back: the handle is not verified")
    return {"handle": who, "did": did, "pds": _pds_of(doc)}


def describe_server(pds: str, d: Deps | None = None) -> dict:
    """The PDS's describeServer (read only), or {} when it did not answer."""
    d = d or _DEPS
    try:
        status, got = _xrpc(d, pds, "com.atproto.server.describeServer")
    except ShareError as e:
        _failures.swallowed(e)
        return {}
    return got if status == 200 else {}


# --- the accounts file ------------------------------------------------------

def _load(d: Deps) -> dict:
    try:
        got = json.loads(d.accounts_path().read_text())
    except (OSError, ValueError):
        return {}
    return got if isinstance(got, dict) else {}


def _write_private(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{os.getpid()}.{threading.get_ident()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.chmod(tmp, 0o600)
    os.replace(tmp, p)


def _save(book_all: dict, d: Deps) -> None:
    _write_private(d.accounts_path(), json.dumps(book_all, indent=1))


def _public(a: dict, i: int) -> dict:
    return {"handle": a.get("handle", ""), "did": a.get("did", ""), "pds": a.get("pds", ""),
            "added": a.get("added", 0), "default": i == 0, "signed_in": bool((a.get("session") or {}).get("access"))}


def accounts(book: str, d: Deps | None = None) -> list[dict]:
    """The book's accounts, the default first, without their secrets."""
    d = d or _DEPS
    return [_public(a, i) for i, a in enumerate(_load(d).get(book) or [])]


def _pick(book: str, who: str, d: Deps) -> dict:
    rows = list(_load(d).get(book) or [])
    if not rows:
        raise ShareError(404, "no_account", "no AT Protocol account is set up: add one in Settings → Bluesky accounts")
    if not who:
        return rows[0]
    w = who.strip().lstrip("@").lower()
    for a in rows:
        if w in (str(a.get("handle", "")).lower(), str(a.get("did", "")).lower()):
            return a
    raise ShareError(404, "unknown_account", f"{who} is not one of the accounts set up here")


def _store_session(book: str, did: str, sess: dict, d: Deps) -> None:
    with _lock:
        whole = _load(d)
        for a in whole.get(book) or []:
            if a.get("did") == did:
                a["session"] = sess
                if sess.get("handle"):
                    a["handle"] = sess["handle"]
        _save(whole, d)


def _create_session(pds: str, did: str, password: str, d: Deps) -> dict:
    status, got = _xrpc(d, pds, "com.atproto.server.createSession", body={"identifier": did, "password": password})
    if status != 200 or not got.get("accessJwt"):
        if got.get("error") == "AuthFactorTokenRequired":
            raise ShareError(400, "not_app_password", "that is the account's own password: make an app password")
        if status in (400, 401):
            raise ShareError(401, "sign_in_refused", "the PDS refused the app password: check it, or make a new one")
        raise _refused("com.atproto.server.createSession", status, got)
    return {"access": got["accessJwt"], "refresh": got.get("refreshJwt", ""), "handle": str(got.get("handle") or ""),
            "at": int(time.time())}


def add_account(book: str, handle: str, app_password: str, pds_hint: str = "", d: Deps | None = None) -> dict:
    d = d or _DEPS
    password = (app_password or "").strip().lower()
    if not _APP_PASSWORD.fullmatch(password):
        raise ShareError(400, "not_app_password",
                         "an app password looks like abcd-efgh-ijkl-mnop (Settings → Privacy and security → App passwords)")
    who = resolve(handle, pds_hint, d)
    sess = _create_session(who["pds"], who["did"], password, d)
    row = {"handle": sess.get("handle") or who["handle"], "did": who["did"], "pds": who["pds"],
           "password": password, "added": int(d.clock()), "session": sess}
    with _lock:
        whole = _load(d)
        rows = [a for a in whole.get(book) or [] if a.get("did") != who["did"]]
        old = [a for a in whole.get(book) or [] if a.get("did") == who["did"]]
        if not old and len(rows) >= MAX_ACCOUNTS:
            raise ShareError(400, "too_many", f"at most {MAX_ACCOUNTS} accounts")
        if old:
            row["added"] = old[0].get("added", row["added"])
            rows.insert([a.get("did") for a in whole.get(book) or []].index(who["did"]), row)
        else:
            rows.append(row)
        whole[book] = rows
        _save(whole, d)
    return _public(row, [a["did"] for a in rows].index(who["did"]))


def remove_account(book: str, who: str, d: Deps | None = None) -> dict:
    d = d or _DEPS
    a = _pick(book, who, d)
    with _lock:
        whole = _load(d)
        whole[book] = [r for r in whole.get(book) or [] if r.get("did") != a.get("did")]
        _save(whole, d)
    return {"handle": a.get("handle", ""), "did": a.get("did", "")}


def _authed(book: str, a: dict, nsid: str, d: Deps, **kw) -> tuple[int, dict]:
    """An XRPC call as the account: signed in if it is not, the session
    refreshed (or made again from the app password) when it has expired."""
    sess = dict(a.get("session") or {})
    if not sess.get("access"):
        sess = _create_session(a["pds"], a["did"], a.get("password", ""), d)
        _store_session(book, a["did"], sess, d)
    status, got = _xrpc(d, a["pds"], nsid, token=sess["access"], **kw)
    if status == 401 or (status == 400 and got.get("error") in ("ExpiredToken", "InvalidToken")):
        fresh = None
        if sess.get("refresh"):
            st, g = _xrpc(d, a["pds"], "com.atproto.server.refreshSession", token=sess["refresh"], method="POST")
            if st == 200 and g.get("accessJwt"):
                fresh = {"access": g["accessJwt"], "refresh": g.get("refreshJwt", sess["refresh"]),
                         "handle": str(g.get("handle") or sess.get("handle") or ""), "at": int(time.time())}
        if fresh is None:
            fresh = _create_session(a["pds"], a["did"], a.get("password", ""), d)
        _store_session(book, a["did"], fresh, d)
        a["session"] = fresh
        status, got = _xrpc(d, a["pds"], nsid, token=fresh["access"], **kw)
    return status, got


# --- identifiers ------------------------------------------------------------

_S32 = "234567abcdefghijklmnopqrstuvwxyz"


def tid(now: float | None = None) -> str:
    """A record key: microseconds since 1970 and a random clock id, as 13
    base32-sortable characters (the atproto TID)."""
    us = int((time.time() if now is None else now) * 1_000_000)
    n = ((us & ((1 << 53) - 1)) << 10) | secrets.randbelow(1024)
    return "".join(_S32[(n >> (5 * i)) & 31] for i in range(12, -1, -1))


def blob_cid(data: bytes) -> str:
    """A blob's CID as a PDS makes it: v1, raw, sha2-256, base32."""
    raw = bytes([0x01, 0x55, 0x12, 0x20]) + hashlib.sha256(data).digest()
    return "b" + base64.b32encode(raw).decode().lower().rstrip("=")


def _iso(t: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + f".{int((t % 1) * 1000):03d}Z"


def _at_uri(did: str, coll: str, rkey: str) -> str:
    return f"at://{did}/{coll}/{rkey}"


def bsky_link(handle_or_did: str, rkey: str) -> str:
    return f"https://bsky.app/profile/{handle_or_did}/post/{rkey}"


# --- photos -----------------------------------------------------------------

def _photo_bytes(ref: Any, book: str) -> bytes:
    """A photo the person sent: a file shared to the app (`POST /upload`,
    its path, under the upload root) or a chat room's (`{room, event}`,
    the owner's rooms)."""
    from agent_media_core import shared_files

    if isinstance(ref, dict) and ref.get("room"):
        if book != "":
            raise ShareError(403, "bad_photo", "a room's photos are the owner's")
        from . import matrix as mx, room_media

        r = mx.room(str(ref.get("room")))
        info = None
        if r is not None:
            with r.lock:
                info = dict(r.media.get(str(ref.get("event") or "")) or {}) or None
        p = room_media.fetch(info, thumb=False) if info else None
        if p is None:
            raise ShareError(404, "bad_photo", "that chat photo could not be had")
    else:
        raw = ref.get("upload") or ref.get("path") if isinstance(ref, dict) else ref
        p = Path(os.path.expanduser(str(raw or "")))
        top = shared_files.root().resolve()
        try:
            p = p.resolve()
        except (OSError, RuntimeError):
            raise ShareError(404, "bad_photo", "no such shared photo") from None
        if not raw or not p.is_relative_to(top) or not p.is_file() or p.name.startswith("."):
            raise ShareError(404, "bad_photo", "no such shared photo (send it with POST /upload first)")
    if p.stat().st_size > PHOTO_READ_MAX:
        raise ShareError(413, "photo_too_large", f"a photo of at most {PHOTO_READ_MAX >> 20} MB")
    return p.read_bytes()


def sniff(data: bytes) -> str:
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[4:12] in (b"ftypheic", b"ftypheix", b"ftypmif1", b"ftypavif"):
        return "image/heic"
    return ""


def _exif_orientation(seg: bytes) -> int:
    """The Orientation tag (1–8) of an APP1 Exif segment's body, else 1."""
    if seg[:6] != b"Exif\x00\x00":
        return 1
    t = seg[6:]
    if t[:2] == b"II":
        e = "little"
    elif t[:2] == b"MM":
        e = "big"
    else:
        return 1
    try:
        off = int.from_bytes(t[4:8], e)  # type: ignore[arg-type]
        n = int.from_bytes(t[off:off + 2], e)  # type: ignore[arg-type]
        for i in range(n):
            ent = t[off + 2 + 12 * i: off + 14 + 12 * i]
            if int.from_bytes(ent[:2], e) == 0x0112:  # type: ignore[arg-type]
                v = int.from_bytes(ent[8:10], e)  # type: ignore[arg-type]
                return v if 1 <= v <= 8 else 1
    except (IndexError, ValueError):
        return 1
    return 1


def _orientation_app1(o: int) -> bytes:
    """An APP1 holding only the Orientation tag: what a photo keeps."""
    tiff = b"MM\x00\x2a" + (8).to_bytes(4, "big") + (1).to_bytes(2, "big") \
        + (0x0112).to_bytes(2, "big") + (3).to_bytes(2, "big") + (1).to_bytes(4, "big") \
        + o.to_bytes(2, "big") + b"\x00\x00" + (0).to_bytes(4, "big")
    body = b"Exif\x00\x00" + tiff
    return b"\xff\xe1" + (len(body) + 2).to_bytes(2, "big") + body


def jpeg_clean(data: bytes) -> tuple[bytes, int, tuple[int, int] | None]:
    """The JPEG without its metadata (EXIF, XMP, IPTC, comments, the
    multi-picture index) or anything after its end (a motion photo's video):
    `(bytes, orientation, (width, height) as stored)`. Its colour profile
    (APP2 ICC_PROFILE), JFIF and Adobe segments are kept."""
    if data[:2] != b"\xff\xd8":
        raise ValueError("not a JPEG")
    out = bytearray(b"\xff\xd8")
    orient, size, i, n = 1, None, 2, len(data)
    keep_after_soi: list[bytes] = []
    body = bytearray()
    while i < n:
        if data[i] != 0xFF:
            raise ValueError("a JPEG marker was expected")
        while i < n and data[i] == 0xFF:
            i += 1
        if i >= n:
            break
        m = data[i]
        i += 1
        if m == 0xD9:
            body += b"\xff\xd9"
            break
        if 0xD0 <= m <= 0xD7 or m == 0x01:
            body += bytes([0xFF, m])
            continue
        ln = int.from_bytes(data[i:i + 2], "big")
        if ln < 2 or i + ln > n:
            raise ValueError("a JPEG segment runs past the end")
        seg = data[i + 2:i + ln]
        whole = bytes([0xFF, m]) + data[i:i + ln]
        i += ln
        if m == 0xE1:
            if orient == 1:
                orient = _exif_orientation(seg)
            continue
        if m == 0xE2 and not seg.startswith(b"ICC_PROFILE\x00"):
            continue
        if 0xE3 <= m <= 0xED or m == 0xEF or m == 0xFE:
            continue
        if m == 0xE0 and not body:
            keep_after_soi.append(whole)
            continue
        if 0xC0 <= m <= 0xCF and m not in (0xC4, 0xC8, 0xCC) and len(seg) >= 5:
            size = (int.from_bytes(seg[3:5], "big"), int.from_bytes(seg[1:3], "big"))
        body += whole
        if m == 0xDA:
            # The entropy-coded scan: up to the next marker that is not a
            # stuffed 0xFF00 or a restart.
            j = i
            while j < n - 1:
                if data[j] == 0xFF and data[j + 1] != 0x00 and not 0xD0 <= data[j + 1] <= 0xD7:
                    break
                j += 1
            body += data[i:j]
            i = j
    for s in keep_after_soi:
        out += s
    if orient != 1:
        out += _orientation_app1(orient)
    out += body
    if not out.endswith(b"\xff\xd9"):
        out += b"\xff\xd9"
    return bytes(out), orient, size


_PNG_KEEP = {b"IHDR", b"PLTE", b"IDAT", b"IEND", b"tRNS", b"gAMA", b"cHRM", b"sRGB", b"iCCP", b"sBIT", b"pHYs"}


def png_clean(data: bytes) -> tuple[bytes, tuple[int, int] | None]:
    """The PNG with only the chunks that draw it (no text, EXIF or time)."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    out, i, size = bytearray(data[:8]), 8, None
    while i + 8 <= len(data):
        ln = int.from_bytes(data[i:i + 4], "big")
        kind = data[i + 4:i + 8]
        chunk = data[i:i + 12 + ln]
        if len(chunk) < 12 + ln:
            raise ValueError("a PNG chunk runs past the end")
        if kind == b"IHDR":
            size = (int.from_bytes(data[i + 8:i + 12], "big"), int.from_bytes(data[i + 12:i + 16], "big"))
        if kind in _PNG_KEEP:
            out += chunk
        i += 12 + ln
        if kind == b"IEND":
            break
    return bytes(out), size


def _shrink(data: bytes, orient: int, limit: int) -> bytes | None:
    """The photo as a JPEG under `limit`, upright, smaller each try: with
    Pillow when it is installed, else ffmpeg; None when neither can."""
    try:
        import io

        from PIL import Image, ImageOps

        im = ImageOps.exif_transpose(Image.open(io.BytesIO(data)))
        im = im.convert("RGB")
        for side, q in ((2000, 85), (2000, 75), (1600, 75), (1200, 70), (1000, 60)):
            cp = im.copy()
            cp.thumbnail((side, side))
            buf = io.BytesIO()
            cp.save(buf, "JPEG", quality=q, optimize=True)
            if buf.tell() <= limit:
                return buf.getvalue()
        return None
    except ImportError:
        pass
    except Exception as e:  # noqa: BLE001 - Pillow could not read it: ffmpeg may
        _failures.swallowed(e)
    ff = shutil.which("ffmpeg")
    if not ff:
        return None
    turn = {3: "hflip,vflip", 6: "transpose=1", 8: "transpose=2", 2: "hflip", 4: "vflip",
            5: "transpose=0", 7: "transpose=3"}.get(orient, "")
    with tempfile.TemporaryDirectory(prefix="place-share-") as tmp:
        src, dst = Path(tmp) / "in", Path(tmp) / "out.jpg"
        src.write_bytes(data)
        for side, q in ((2000, 3), (2000, 5), (1600, 5), (1200, 6), (1000, 8)):
            vf = ",".join(x for x in (turn, f"scale='min({side},iw)':'min({side},ih)':force_original_aspect_ratio=decrease") if x)
            try:
                subprocess.run([ff, "-v", "error", "-y", "-noautorotate", "-i", str(src), "-vf", vf,
                                "-frames:v", "1", "-map_metadata", "-1", "-pix_fmt", "yuvj420p",
                                "-q:v", str(q), str(dst)], check=True, timeout=60,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except (OSError, subprocess.SubprocessError) as e:
                _failures.swallowed(e)
                return None
            if dst.is_file() and dst.stat().st_size <= limit:
                return dst.read_bytes()
    return None


def prepare_photo(data: bytes, limit: int) -> dict:
    """A photo made ready to post: `{bytes, mime, width?, height?, resized}`,
    its metadata gone and at most `limit` bytes."""
    mime = sniff(data)
    orient, size, resized = 1, None, False
    if mime == "image/jpeg":
        try:
            clean, orient, size = jpeg_clean(data)
        except ValueError as e:
            raise ShareError(400, "bad_photo", f"that JPEG could not be read: {e}") from None
    elif mime == "image/png":
        try:
            clean, size = png_clean(data)
        except ValueError as e:
            raise ShareError(400, "bad_photo", f"that PNG could not be read: {e}") from None
    elif mime in ("image/webp", "image/heic"):
        clean = b""
    else:
        raise ShareError(400, "bad_photo", "a photo is a JPEG or PNG (or WebP or HEIC where it can be converted)")
    if not clean or len(clean) > limit:
        small = _shrink(data, orient, limit)
        if small is None:
            raise ShareError(413, "photo_too_large",
                             f"that photo is over {limit // 1000} kB and could not be made smaller here")
        clean, orient, size = jpeg_clean(small)
        mime, resized = "image/jpeg", True
    out: dict = {"bytes": clean, "mime": mime, "resized": resized}
    if size:
        w, h = (size[1], size[0]) if orient in (5, 6, 7, 8) else size
        if w > 0 and h > 0:
            out["width"], out["height"] = w, h
    return out


# --- the place --------------------------------------------------------------

def _obj(v: Any) -> dict:
    return v if isinstance(v, dict) else {}


def _point(lat, lon) -> tuple[float, float] | None:
    try:
        la, lo = float(lat), float(lon)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(la) and math.isfinite(lo) and -90 <= la <= 90 and -180 <= lo <= 180):
        return None
    return la, lo


def _metres(a: tuple[float, float], b: tuple[float, float]) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (*a, *b))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6_371_000 * 2 * math.asin(min(1.0, math.sqrt(h)))


def _num(v: float, decimals: int) -> str:
    s = f"{v:.{decimals}f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def _osm(place: dict) -> dict | None:
    raw = place.get("osm")
    if isinstance(raw, dict):
        t, i = str(raw.get("type") or ""), raw.get("id")
    else:
        t, _, i = str(place.get("osm_id") or place.get("id") or "").partition("/")
    try:
        n = int(str(i))
    except (TypeError, ValueError):
        return None
    return {"type": t, "id": n} if t in _OSM_TYPES and n > 0 else None


def _address(place: dict, blur: bool, country_hint: str = "") -> dict | None:
    a = place.get("address")
    tags = _obj(place.get("tags"))
    parts: dict = {}
    if isinstance(a, dict):
        parts = {k: str(a.get(k) or "").strip() for k in ("country", "region", "locality", "street", "postalCode",
                                                             "postcode", "name")}
    elif isinstance(a, str) and a.strip():
        bits = [b.strip() for b in a.split(",") if b.strip()]
        if bits:
            parts["street"] = bits[0] if len(bits) > 1 else ""
            parts["locality"] = bits[-1]
    country = (parts.get("country") or str(tags.get("addr:country") or "") or country_hint).strip().upper()
    if not re.fullmatch(r"[A-Z]{2}", country):
        return None
    street = parts.get("street") or " ".join(x for x in (str(tags.get("addr:housenumber") or ""),
                                                          str(tags.get("addr:street") or "")) if x)
    out: dict = {"$type": ADDRESS, "country": country}
    for key, val in (("region", parts.get("region") or tags.get("addr:state")),
                     ("locality", parts.get("locality") or tags.get("addr:suburb") or tags.get("addr:city")),
                     ("postalCode", parts.get("postalCode") or parts.get("postcode") or tags.get("addr:postcode")),
                     ("street", "" if blur else street)):
        v = " ".join(str(val or "").split())[:200]
        if v:
            out[key] = v
    return out


def _check_private(place: dict, pt: tuple[float, float], has_osm: bool, book: str, d: Deps) -> None:
    layer = str(place.get("layer") or "").strip().lower()
    if layer in PRIVATE_LAYERS or layer.startswith(("trip", "history")):
        raise ShareError(403, "private_place", "that is one of your own places (your things, the car, a trip or "
                                               "where you have been), not a place to share")
    saved = str(place.get("saved") or "").strip().lower()
    if saved in PRIVATE_SAVED:
        raise ShareError(403, "private_place", f"{saved.title()} is never shared")
    for s in d.saved(book):
        if isinstance(s, dict) and s.get("id") in PRIVATE_SAVED:
            sp = _point(s.get("lat"), s.get("lon"))
            if sp and _metres(pt, sp) <= HOME_M:
                raise ShareError(403, "private_place", f"that is within {HOME_M} m of your "
                                                       f"{str(s.get('id')).title()}, which is never shared")
    for t in d.placed(book):
        tp = _point(t.get("lat"), t.get("lon")) if isinstance(t, dict) else None
        if tp and _metres(pt, tp) <= THING_M:
            raise ShareError(403, "private_place", "that is where one of your things is, not a place to share")
    if not has_osm:
        for f in d.fixes():
            fp = _point(f.get("lat"), f.get("lon")) if isinstance(f, dict) else None
            if fp and _metres(pt, fp) <= PHONE_M:
                raise ShareError(403, "phone_position", "that is where your phone is, not a place: share the "
                                                        "place itself from the Map")


def _tags(raw) -> list[str]:
    if isinstance(raw, str):
        raw = raw.replace(",", " ").split()
    out: list[str] = []
    for t in raw or []:
        s = _TAG.sub("-", str(t).lower().lstrip("#")).strip("-")[:64]
        if s and s not in out:
            out.append(s)
    return out[:MAX_TAGS]


def osm_link(osm: dict | None, la: float, lo: float, decimals: int) -> str:
    if osm:
        return f"https://www.openstreetmap.org/{osm['type']}/{osm['id']}"
    a, b = _num(la, decimals), _num(lo, decimals)
    return f"https://www.openstreetmap.org/?mlat={a}&mlon={b}#map={16 if decimals < DECIMALS else 18}/{a}/{b}"


def _bsky_post(text: str, name: str, link: str, tags: list[str], images: list[dict], langs: list[str],
               address: dict | None) -> tuple[dict, bool]:
    """The Bluesky post: the words, the place's name linked to its map, the
    tags as hashtags, the photos (or a link card when there are none)."""
    pin = f"📍 {name[:BSKY_TEXT - 10]}"
    tail_tags = "".join(f" #{t}" for t in tags)
    head = text.strip()
    truncated = False

    def build(h: str, tt: str) -> str:
        return (h + "\n\n" if h else "") + pin + tt

    def fits(s: str) -> bool:
        return len(s) <= BSKY_TEXT and len(s.encode()) <= BSKY_BYTES

    if not fits(build(head, tail_tags)):
        tail_tags = ""
    while head and not fits(build(head, tail_tags)):
        over = max(1, len(build(head, tail_tags)) - BSKY_TEXT)
        head, truncated = head.rstrip("…")[:-over - 1].rstrip(), True
        head = head + "…" if head else ""
    full = build(head, tail_tags)
    b = full.encode()
    shown = pin[len("📍 "):]
    start = b.rindex(pin.encode()) + len("📍 ".encode())
    facets = [{"index": {"byteStart": start, "byteEnd": start + len(shown.encode())},
               "features": [{"$type": "app.bsky.richtext.facet#link", "uri": link}]}]
    pos = start + len(shown.encode())
    for t in (tags if tail_tags else []):
        at = b.index(f"#{t}".encode(), pos)
        facets.append({"index": {"byteStart": at, "byteEnd": at + len(t.encode()) + 1},
                       "features": [{"$type": "app.bsky.richtext.facet#tag", "tag": t}]})
        pos = at + len(t.encode()) + 1
    post: dict = {"$type": BSKY_POST, "text": full, "facets": facets, "langs": langs}
    if images:
        post["embed"] = {"$type": "app.bsky.embed.images", "images": images}
    else:
        desc = ", ".join(str(address.get(k)) for k in ("street", "locality", "region") if address and address.get(k))
        post["embed"] = {"$type": "app.bsky.embed.external",
                         "external": {"uri": link, "title": name, "description": desc or "On OpenStreetMap"}}
    return post, truncated


def _canon(x: Any) -> bytes:
    return json.dumps(x, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _token_of(did: str, writes: list[dict]) -> str:
    """The preview's token: the sha-256 of what would be written, but for
    `createdAt` (set again when it is posted)."""
    bare = [{**w, "record": {k: v for k, v in w["record"].items() if k != "createdAt"}} for w in writes]
    return hashlib.sha256(_canon({"did": did, "writes": bare})).hexdigest()[:40]


def preview(book: str, body: dict, d: Deps | None = None) -> dict:
    """What sharing this place would write, staged under its token; nothing
    is sent but the read-only describeServer (for its blob limit)."""
    d = d or _DEPS
    place = _obj(body.get("place"))
    name = " ".join(str(place.get("name") or "").split())[:MAX_NAME]
    pt = _point(place.get("lat"), place.get("lon"))
    if not name or pt is None:
        raise ShareError(400, "bad_place", "a place needs its name, lat and lon")
    if body.get("from_phone") or place.get("from_phone"):
        raise ShareError(403, "phone_position", "the phone's own position is never shared")
    osm = _osm(place)
    _check_private(place, pt, osm is not None, book, d)
    text = str(body.get("text") or "").strip()
    if len(text) > MAX_TEXT:
        raise ShareError(400, "too_long", f"at most {MAX_TEXT} characters")
    tags = _tags(body.get("tags"))
    blur = body.get("blur") is True
    bluesky = body.get("bluesky_post") is not False
    langs = [str(x)[:16] for x in (body.get("langs") or ["en"]) if str(x).strip()][:3] or ["en"]
    photos = body.get("photos") or []
    if not isinstance(photos, list) or len(photos) > MAX_PHOTOS:
        raise ShareError(400, "too_many_photos", f"at most {MAX_PHOTOS} photos")
    a = _pick(book, str(body.get("account") or ""), d)
    limit = BSKY_IMAGE_MAX
    pds_limit = describe_server(a["pds"], d).get("blobUploadLimit")
    if isinstance(pds_limit, int) and 0 < pds_limit < limit:
        limit = pds_limit
    prepared: list[dict] = []
    for ref in photos:
        got = prepare_photo(d.photo(ref, book), limit)
        got["alt"] = " ".join(str(ref.get("alt") or "").split())[:MAX_ALT] if isinstance(ref, dict) else ""
        got["cid"] = blob_cid(got["bytes"])
        prepared.append(got)
    decimals = BLUR_DECIMALS if blur else DECIMALS
    la, lo = pt
    if blur:
        osm = None
    location = {"$type": GEO, "latitude": _num(la, decimals), "longitude": _num(lo, decimals)}
    address = _address(place, blur)
    if address is None and (place.get("address") or _obj(place.get("tags")).get("addr:street")):
        # The card gave an address but no country: the point says which.
        address = _address(place, blur, d.country(la, lo))
    link = osm_link(osm, la, lo, decimals)
    images = []
    for p in prepared:
        img: dict = {"image": {"$type": "blob", "ref": {"$link": p["cid"]}, "mimeType": p["mime"],
                               "size": len(p["bytes"])}, "alt": p["alt"]}
        if "width" in p:
            img["aspectRatio"] = {"width": p["width"], "height": p["height"]}
        images.append(img)
    now = d.clock()
    did = a["did"]
    rkey = tid(now)
    bkey = tid(now + 0.000002) if bluesky else ""
    rec: dict = {"$type": POST, "place": {"name": name, "location": location}, "text": text,
                 "createdAt": _iso(now)}
    if osm:
        rec["place"]["osm"] = osm
    if address:
        rec["place"]["address"] = address
    if tags:
        rec["tags"] = tags
    if images:
        rec["images"] = images
    if blur:
        rec["blurred"] = True
    rec["langs"] = langs
    writes: list[dict] = []
    truncated = False
    if bluesky:
        bpost, truncated = _bsky_post(text, name, link, tags, images, langs, address)
        bpost["createdAt"] = _iso(now)
        writes.append({"collection": BSKY_POST, "rkey": bkey, "record": bpost})
        rec["bskyPost"] = _at_uri(did, BSKY_POST, bkey)
    writes.insert(0, {"collection": POST, "rkey": rkey, "record": rec})
    token = _token_of(did, writes)
    _stage_put(token, {"book": book, "did": did, "handle": a.get("handle", ""), "writes": writes,
                       "at": now, "photos": [{"cid": p["cid"], "mime": p["mime"]} for p in prepared]},
               [p["bytes"] for p in prepared], d)
    out: dict = {"token": token, "expires": int(now + STAGE_S),
                 "account": {"handle": a.get("handle", ""), "did": did, "pds": a["pds"]},
                 "records": [{"collection": w["collection"], "rkey": w["rkey"],
                              "uri": _at_uri(did, w["collection"], w["rkey"]), "record": w["record"]}
                             for w in writes],
                 "uris": {"place": _at_uri(did, POST, rkey)}, "link": link, "blurred": blur,
                 "photos": [{"cid": p["cid"], "mime": p["mime"], "size": len(p["bytes"]), "resized": p["resized"],
                             **({"width": p["width"], "height": p["height"]} if "width" in p else {})}
                            for p in prepared],
                 "public": "Everything posted to an AT Protocol repo is public, and deleting it does not unsay it."}
    if bluesky:
        out["uris"]["bluesky"] = _at_uri(did, BSKY_POST, bkey)
        out["bluesky_link"] = bsky_link(a.get("handle") or did, bkey)
        out["bluesky_text"] = writes[1]["record"]["text"]
        out["bluesky_truncated"] = truncated
    return out


# --- staging ----------------------------------------------------------------

def _stage_put(token: str, meta: dict, blobs: list[bytes], d: Deps) -> None:
    root = d.stage_dir()
    _stage_sweep(d)
    dirp = root / token
    dirp.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(dirp, 0o700)
    for i, b in enumerate(blobs):
        fd = os.open(dirp / f"photo-{i}", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(b)
    _write_private(dirp / "preview.json", json.dumps(meta, ensure_ascii=False))


def _stage_sweep(d: Deps) -> None:
    root = d.stage_dir()
    if not root.is_dir():
        return
    for p in root.iterdir():
        try:
            if p.is_dir() and d.clock() - p.stat().st_mtime > STAGE_S:
                shutil.rmtree(p, ignore_errors=True)
        except OSError as e:
            _failures.swallowed(e)


def _stage_get(token: str, book: str, d: Deps) -> tuple[dict, list[bytes]]:
    if not re.fullmatch(r"[0-9a-f]{40}", token or ""):
        raise ShareError(400, "bad_token", "confirm carries the token its preview gave")
    dirp = d.stage_dir() / token
    try:
        meta = json.loads((dirp / "preview.json").read_text())
    except (OSError, ValueError):
        raise ShareError(404, "no_preview", "that preview is gone (an hour old, or already posted): preview again") from None
    if meta.get("book") != book:
        raise ShareError(404, "no_preview", "that preview is gone (an hour old, or already posted): preview again")
    if d.clock() - float(meta.get("at") or 0) > STAGE_S:
        shutil.rmtree(dirp, ignore_errors=True)
        raise ShareError(404, "no_preview", "that preview is over an hour old: preview again")
    if _token_of(meta["did"], meta["writes"]) != token:
        raise ShareError(409, "changed", "the staged post no longer matches its token: preview again")
    blobs = []
    for i, ph in enumerate(meta.get("photos") or []):
        b = (dirp / f"photo-{i}").read_bytes()
        if blob_cid(b) != ph["cid"]:
            raise ShareError(409, "changed", "a staged photo no longer matches: preview again")
        blobs.append(b)
    return meta, blobs


def confirm(book: str, token: str, d: Deps | None = None) -> dict:
    """Write exactly what the preview with this token showed: its photos
    uploaded, then its records in one applyWrites."""
    d = d or _DEPS
    meta, blobs = _stage_get(str(token or ""), book, d)
    a = _pick(book, meta["did"], d)
    for ph, b in zip(meta.get("photos") or [], blobs):
        status, got = _authed(book, a, "com.atproto.repo.uploadBlob", d, raw=b, mime=ph["mime"])
        if status != 200:
            raise _refused("com.atproto.repo.uploadBlob", status, got)
        ref = ((got.get("blob") or {}).get("ref") or {}).get("$link")
        if ref != ph["cid"]:
            raise ShareError(502, "blob_mismatch", "the PDS kept a photo under another id than shown: nothing was posted")
    now = d.clock()
    writes = []
    for w in meta["writes"]:
        rec = {**w["record"], "createdAt": _iso(now)}
        writes.append({"$type": "com.atproto.repo.applyWrites#create", "collection": w["collection"],
                       "rkey": w["rkey"], "value": rec})
    # The Bluesky post first, so the place post's bskyPost names one that is there.
    writes.sort(key=lambda w: 0 if w["collection"] == BSKY_POST else 1)
    status, got = _authed(book, a, "com.atproto.repo.applyWrites", d, body={"repo": meta["did"], "writes": writes})
    if status != 200:
        raise _refused("com.atproto.repo.applyWrites", status, got)
    shutil.rmtree(d.stage_dir() / token, ignore_errors=True)
    did, handle = meta["did"], a.get("handle") or meta.get("handle") or meta["did"]
    out: dict = {"account": {"handle": handle, "did": did}}
    for w in meta["writes"]:
        uri = _at_uri(did, w["collection"], w["rkey"])
        if w["collection"] == POST:
            out["uri"] = uri
        else:
            out["bsky_uri"] = uri
            out["bsky_link"] = bsky_link(handle, w["rkey"])
    cids = {r.get("uri"): r.get("cid") for r in got.get("results") or [] if isinstance(r, dict)}
    if cids.get(out.get("uri")):
        out["cid"] = cids[out["uri"]]
    return out


# --- own posts --------------------------------------------------------------

def _shape(rec: dict, did: str, handle: str, pds: str) -> dict:
    v = _obj(rec.get("value"))
    pl = _obj(v.get("place"))
    loc = _obj(pl.get("location"))
    uri = str(rec.get("uri") or "")
    out: dict = {"uri": uri, "rkey": uri.rsplit("/", 1)[-1], "cid": rec.get("cid", ""), "name": pl.get("name", ""),
                 "text": v.get("text", ""), "tags": v.get("tags") or [], "createdAt": v.get("createdAt", ""),
                 "blurred": bool(v.get("blurred"))}
    pt = _point(loc.get("latitude"), loc.get("longitude"))
    if pt:
        out["lat"], out["lon"] = pt
    if isinstance(pl.get("osm"), dict):
        out["osm"] = pl["osm"]
    if isinstance(pl.get("address"), dict):
        out["address"] = {k: val for k, val in pl["address"].items() if k != "$type"}
    bs = str(v.get("bskyPost") or "")
    if bs.startswith(f"at://{did}/{BSKY_POST}/"):
        out["bsky_uri"] = bs
        out["bsky_link"] = bsky_link(handle or did, bs.rsplit("/", 1)[-1])
    imgs = []
    for im in v.get("images") or []:
        cid = ((im.get("image") or {}).get("ref") or {}).get("$link") if isinstance(im, dict) else None
        if cid:
            imgs.append({"url": f"{pds}/xrpc/com.atproto.sync.getBlob?" + urllib.parse.urlencode({"did": did, "cid": cid}),
                         "alt": im.get("alt", "")})
    out["images"] = imgs
    return out


def posts(book: str, who: str = "", cursor: str = "", limit: int = 50, d: Deps | None = None) -> dict:
    d = d or _DEPS
    a = _pick(book, who, d)
    status, got = _xrpc(d, a["pds"], "com.atproto.repo.listRecords",
                        params={"repo": a["did"], "collection": POST, "limit": max(1, min(100, limit)),
                                "cursor": cursor})
    if status != 200:
        raise _refused("com.atproto.repo.listRecords", status, got)
    out: dict = {"account": {"handle": a.get("handle", ""), "did": a["did"]},
                 "posts": [_shape(r, a["did"], a.get("handle", ""), a["pds"])
                           for r in got.get("records") or [] if isinstance(r, dict)]}
    if got.get("cursor"):
        out["cursor"] = got["cursor"]
    return out


def delete(book: str, uri: str, who: str = "", d: Deps | None = None) -> dict:
    """The place post and the Bluesky post made with it, deleted together."""
    d = d or _DEPS
    m = re.fullmatch(r"at://(did:[^/]+)/([^/]+)/([^/]+)", str(uri or "").strip())
    if not m or m.group(2) != POST:
        raise ShareError(400, "bad_uri", f"an at:// URI of a {POST} record")
    did, _, rkey = m.groups()
    a = _pick(book, who or did, d)
    if a["did"] != did:
        raise ShareError(403, "not_yours", "that post is in another account's repo")
    status, got = _xrpc(d, a["pds"], "com.atproto.repo.getRecord", params={"repo": did, "collection": POST, "rkey": rkey})
    if status != 200:
        if got.get("error") == "RecordNotFound" or status == 404:
            raise ShareError(404, "not_found", "that post is not there (deleted already?)")
        raise _refused("com.atproto.repo.getRecord", status, got)
    deletes = [{"$type": "com.atproto.repo.applyWrites#delete", "collection": POST, "rkey": rkey}]
    bs = str(((got.get("value") or {}) if isinstance(got.get("value"), dict) else {}).get("bskyPost") or "")
    if bs.startswith(f"at://{did}/{BSKY_POST}/"):
        brkey = bs.rsplit("/", 1)[-1]
        st, _ = _xrpc(d, a["pds"], "com.atproto.repo.getRecord", params={"repo": did, "collection": BSKY_POST, "rkey": brkey})
        if st == 200:
            deletes.insert(0, {"$type": "com.atproto.repo.applyWrites#delete", "collection": BSKY_POST, "rkey": brkey})
    status, got = _authed(book, a, "com.atproto.repo.applyWrites", d, body={"repo": did, "writes": deletes})
    if status != 200:
        raise _refused("com.atproto.repo.applyWrites", status, got)
    return {"deleted": [_at_uri(did, w["collection"], w["rkey"]) for w in deletes],
            "note": "Gone from the repo; copies others made (relays, apps) may remain."}


# --- the routes -------------------------------------------------------------

def set_default(book: str, who: str, d: Deps | None = None) -> list[dict]:
    """Make an account the default: it moves to the front of the book's list,
    the others keep their order (David, 27 Nov 2026: "Can we add a way of
    switching default accounts")."""
    d = d or _DEPS
    a = _pick(book, who, d)
    with _lock:
        whole = _load(d)
        rows = list(whole.get(book) or [])
        whole[book] = [r for r in rows if r.get("did") == a.get("did")] + [r for r in rows if r.get("did") != a.get("did")]
        _save(whole, d)
    return accounts(book, d)


READS = frozenset({("GET", "/map/share/accounts"), ("GET", "/map/share/posts")})
WRITES = frozenset({("POST", "/map/share/accounts"), ("POST", "/map/share/accounts/remove"),
                    ("POST", "/map/share/accounts/default"),
                    ("POST", "/map/share/preview"), ("POST", "/map/share/confirm"), ("POST", "/map/share/delete")})
PATHS = frozenset(p for _, p in READS | WRITES)


def route(method: str, path: str, bearer: str, host: bool, body: dict, arg: Callable[[str], str],
          d: Deps | None = None) -> tuple[bool, dict]:
    """One `/map/share/…` request: reading for anyone the Map lets in, not a
    guest; adding accounts, posting and deleting for an admin (or the host)."""
    d = d or _DEPS
    from . import auth, map_layers, map_saved

    if (method, path) not in READS | WRITES:
        return False, {"status": 405, "code": "method", "error": "not this method"}
    ok, detail = map_layers._gate(bearer, host)
    if not ok:
        return False, detail
    if (method, path) in WRITES and not host:
        user, err = auth.may_admin(bearer)
        if not user:
            return False, err
    book = map_saved.account_of(bearer, host)
    try:
        if path == "/map/share/accounts" and method == "GET":
            return True, {"accounts": accounts(book, d)}
        if path == "/map/share/accounts":
            return True, {"account": add_account(book, str(body.get("handle") or ""),
                                                 str(body.get("app_password") or ""), str(body.get("pds") or ""), d)}
        if path == "/map/share/accounts/default":
            return True, {"accounts": set_default(book, str(body.get("account") or body.get("handle") or ""), d)}
        if path == "/map/share/accounts/remove":
            return True, {"removed": remove_account(book, str(body.get("account") or body.get("handle") or ""), d)}
        if path == "/map/share/preview":
            return True, preview(book, body, d)
        if path == "/map/share/confirm":
            return True, confirm(book, str(body.get("token") or ""), d)
        if path == "/map/share/posts":
            try:
                lim = int(arg("limit") or 50)
            except ValueError:
                lim = 50
            return True, posts(book, arg("account"), arg("cursor"), lim, d)
        return True, delete(book, str(body.get("uri") or ""), str(body.get("account") or ""), d)
    except ShareError as e:
        return False, e.detail()


# --- `media place share|accounts|posts|unshare` -----------------------------

_LATLON = re.compile(r"-?\d+(?:\.\d+)?,\s*-?\d+(?:\.\d+)?")


def _print_preview(p: dict) -> None:
    acc = p["account"]
    print(f"As {acc['handle'] or acc['did']} ({acc['pds']}). Nothing is posted until: "
          f"media place share --go {p['token']}")
    print(p["public"])
    for r in p["records"]:
        print(f"\n{r['collection']}  {r['uri']}")
        print(json.dumps(r["record"], ensure_ascii=False, indent=2))
    if p.get("bluesky_truncated"):
        print("\n(The Bluesky post's words were cut to fit its 300 characters.)")


def main(argv: list[str] | None = None, d: Deps | None = None) -> int:
    """`media place share …` (a preview; `--go <token>` posts that preview),
    `media place accounts [add <handle> [--pds URL] | remove <handle>]` (the
    app password on stdin), `media place posts`, `media place unshare <uri>`."""
    d = d or _DEPS
    import argparse
    import sys

    argv = list(argv or [])
    cmd, rest = (argv[0], argv[1:]) if argv else ("", [])
    book = ""
    try:
        if cmd == "accounts":
            ap = argparse.ArgumentParser(prog="media place accounts")
            ap.add_argument("action", nargs="?", default="list", choices=("list", "add", "remove", "default"))
            ap.add_argument("handle", nargs="?", default="")
            ap.add_argument("--pds", default="", help="the PDS to ask for the handle first")
            a = ap.parse_args(rest)
            if a.action == "list":
                rows = accounts(book, d)
                for r in rows:
                    print(f"{r['handle']:<32} {r['did']:<34} {r['pds']}" + ("  (default)" if r["default"] else ""))
                if not rows:
                    print("no accounts: media place accounts add <handle>  (the app password on stdin)")
                return 0
            if not a.handle:
                ap.error("say which handle")
            if a.action == "remove":
                print(json.dumps(remove_account(book, a.handle, d)))
                return 0
            if a.action == "default":
                for r in set_default(book, a.handle, d):
                    print(f"{r['handle']:<32} {r['did']:<34} {r['pds']}" + ("  (default)" if r["default"] else ""))
                return 0
            if sys.stdin.isatty():
                print("app password (abcd-efgh-ijkl-mnop): ", end="", file=sys.stderr, flush=True)
            pw = sys.stdin.readline().strip()
            print(json.dumps(add_account(book, a.handle, pw, a.pds, d)))
            return 0
        if cmd == "posts":
            ap = argparse.ArgumentParser(prog="media place posts")
            ap.add_argument("--account", default="")
            ap.add_argument("--json", action="store_true")
            a = ap.parse_args(rest)
            got = posts(book, a.account, d=d)
            if a.json:
                print(json.dumps(got, ensure_ascii=False, indent=2))
            for p in got["posts"] if not a.json else []:
                print(f"{p['createdAt'][:16]}  {p['name']}  {p['uri']}" + (f"  {p['bsky_link']}" if p.get("bsky_link") else ""))
            return 0
        if cmd == "unshare":
            ap = argparse.ArgumentParser(prog="media place unshare")
            ap.add_argument("uri")
            ap.add_argument("--account", default="")
            a = ap.parse_args(rest)
            print(json.dumps(delete(book, a.uri, a.account, d), ensure_ascii=False))
            return 0
        if cmd == "share":
            rest = [f"--at={t}" if _LATLON.fullmatch(t) else t for t in rest]
            ap = argparse.ArgumentParser(prog="media place share",
                                         description="Preview a place post (and its Bluesky post); --go TOKEN posts "
                                                     "that preview once the person has seen it and said yes.")
            ap.add_argument("name", nargs="*", help="the place's name")
            ap.add_argument("--at", default="", help="the place's own lat,lon (from media place find), never the phone's")
            ap.add_argument("--osm", default="", help="its OpenStreetMap id, node/123")
            ap.add_argument("--text", default="")
            ap.add_argument("--tag", action="append", default=[])
            ap.add_argument("--photo", action="append", default=[], help="a shared file's path (POST /upload), up to 4")
            ap.add_argument("--alt", action="append", default=[], help="a photo's description, in --photo order")
            ap.add_argument("--blur", action="store_true", help="coordinates to about 100 m, no street")
            ap.add_argument("--account", default="")
            ap.add_argument("--no-bluesky", action="store_true", help="only the place post, no Bluesky post")
            for k in ("country", "region", "locality", "street", "postcode"):
                ap.add_argument(f"--{k}", default="")
            ap.add_argument("--go", default="", metavar="TOKEN", help="post the preview this token came from")
            ap.add_argument("--json", action="store_true")
            a = ap.parse_args(rest)
            if a.go:
                got = confirm(book, a.go, d)
                print(json.dumps(got, ensure_ascii=False, indent=None if not a.json else 2))
                return 0
            if not a.name or not _LATLON.fullmatch(a.at.strip()):
                ap.error("a name and its lat,lon")
            la, lo = (x.strip() for x in a.at.split(","))
            body = {"place": {"name": " ".join(a.name), "lat": la, "lon": lo, "osm_id": a.osm,
                              "address": {"country": a.country, "region": a.region, "locality": a.locality,
                                          "street": a.street, "postcode": a.postcode}},
                    "text": a.text, "tags": a.tag, "blur": a.blur, "account": a.account,
                    "bluesky_post": not a.no_bluesky,
                    "photos": [{"upload": p, "alt": a.alt[i] if i < len(a.alt) else ""} for i, p in enumerate(a.photo)]}
            got = preview(book, body, d)
            if a.json:
                print(json.dumps(got, ensure_ascii=False, indent=2))
            else:
                _print_preview(got)
            return 0
    except ShareError as e:
        print(f"media place {cmd}: {e.error} ({e.code})", file=sys.stderr)
        return 1
    print("usage: media place share|accounts|posts|unshare …", file=sys.stderr)
    return 2
