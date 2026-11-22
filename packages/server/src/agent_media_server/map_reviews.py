"""A place's reviews from Mangrove (mangrove.reviews), the open review
platform keyed to OpenStreetMap places, and posting the person's own
(maps proposal step 8; David, 22 Nov 2026: "fuck yeah!", to adding reviews
after learning OpenStreetMap keeps none).

  GET  /map/reviews?name=&lat=&lon=[&osm_id=node/1]
       → {ok, found, count, rated, average (stars, 1–5, one decimal) | null,
          reviews: [{stars, rating (0–100), opinion?, date, author?, mine?,
          link}] (newest first, at most SHOW), link (the place on
          mangrove.reviews), credit: "Mangrove", license: "CC BY 4.0",
          signed_up (whether this account has a key yet)}
       or {ok, found: false, reason: "failed"} when Mangrove did not answer.
  POST /map/reviews {name, lat, lon, osm_id?, stars (1–5), opinion?,
       nickname?, confirm: true}
       → {ok, signature, link}: signed with this account's key and sent to
          Mangrove, where it is public at once and for good (CC BY 4.0).

The licence and terms (read 22 Nov 2026): reviews are CC BY 4.0 unless a
review's `license` says another open licence (CC BY-SA); re-users credit the
authors, e.g. by linking the review on mangrove.reviews. The Terms of
Service (mangrove.reviews/terms) allow commercial use, forbid only abusive
or disruptive automated use, and ask that reviews be genuine experiences.
Hence: one request per card opened, kept a day; a credit and a link on the
card; and a review is posted only when the person has written it and
confirmed (`confirm: true`), never for them.

A place is a `geo:` subject, `geo:<lat>,<lon>?q=<name>&u=<metres>` (the Open
Reviews schema guide). Mangrove's `sub=` filter matches reviews within their
uncertainty of the point whose name matches ignoring case, punctuation and
accents, so the card asks with its own name and point; one posted here says
u=30 as MapComplete's do, and carries `metadata.osm_id`.

Posting (the Mangrove standard): a JWT signed ES256 with a P-256 key, header
`{alg: ES256, typ: JWT, kid: <public key PEM on one line>, jwk: <the public
JWK as a JSON string>}`, payload `{sub, rating 0–100, opinion ≤1000, iat,
metadata: {client_id, osm_id?, nickname?, is_personal_experience}}`, sent as
the body of `POST https://api.mangrove.reviews/submit`. The key is made on
first use, one per account, kept 0600 in `state_dir()/mangrove-keys.json`
and never sent anywhere: only the signature and public key go to Mangrove.
Lose it and the reviews stay but can no longer be edited or deleted.

`MEDIA_MANGROVE_URL` points at another Mangrove API (its tests use none:
conftest refuses the real one).
"""
from __future__ import annotations

import base64
import json
import math
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable

from agent_media_core import failures as _failures

API = "https://api.mangrove.reviews"
SITE = "https://mangrove.reviews"
CLIENT_ID = "https://sasonica.com"
USER_AGENT = "agent-media (Sasonica; +https://sasonica.com)"
TIMEOUT_S = 8
#: How long an answer is used before Mangrove is asked again.
CACHE_S = 86400
#: The point's uncertainty when asking (metres): an OSM point and the one a
#: reviewer's app used can differ by a building's width.
ASK_U = 100
#: The uncertainty a posted review states, as MapComplete's do.
POST_U = 30
SHOW = 5
MAX_OPINION = 1000
MAX_NAME = 100
MAX_NICK = 50
LICENSE = "CC BY 4.0"

_lock = threading.Lock()
_cache: dict[str, tuple[float, dict]] = {}

#: (url) → (status, body). Tests pass their own.
Get = Callable[[str], tuple[int, bytes]]
#: (url, body) → (status, body).
Post = Callable[[str, bytes], tuple[int, bytes]]


def _api() -> str:
    return (os.environ.get("MEDIA_MANGROVE_URL") or API).strip().rstrip("/")


def _get(url: str) -> tuple[int, bytes]:
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            return r.status, r.read(4_000_000)
    except urllib.error.HTTPError as e:
        return e.code, e.read(10_000)


def _post(url: str, body: bytes) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "text/plain", "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            return r.status, r.read(100_000)
    except urllib.error.HTTPError as e:
        return e.code, e.read(10_000)


def forget() -> None:
    with _lock:
        _cache.clear()


# --- the place -------------------------------------------------------------

def _point(lat, lon) -> tuple[float, float] | None:
    try:
        la, lo = float(lat), float(lon)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(la) and math.isfinite(lo) and -90 <= la <= 90 and -180 <= lo <= 180):
        return None
    return la, lo


def subject(name: str, la: float, lo: float, u: int) -> str:
    """The place's `geo:` URI, its name escaped as Mangrove's own client does."""
    q = urllib.parse.quote(" ".join(name.split())[:MAX_NAME], safe="")
    return f"geo:{_num(la)},{_num(lo)}?q={q}&u={u}"


def _num(v: float) -> str:
    return f"{v:.7f}".rstrip("0").rstrip(".")


def place_link(sub: str) -> str:
    return f"{SITE}/search?{urllib.parse.urlencode({'sub': sub})}"


def review_link(signature: str) -> str:
    return f"{SITE}/list?{urllib.parse.urlencode({'signature': signature})}"


def _stars(rating) -> float | None:
    """Mangrove's 0–100 as stars, 1–5 (its client's `rating / 25 + 1`)."""
    if not isinstance(rating, int) or isinstance(rating, bool) or not 0 <= rating <= 100:
        return None
    return rating / 25 + 1


def _date(iat) -> str:
    try:
        return time.strftime("%Y-%m-%d", time.gmtime(int(iat)))
    except (TypeError, ValueError, OverflowError):
        return ""


def _shape(raw: dict, my_kid: str) -> dict | None:
    p = raw.get("payload") if isinstance(raw, dict) else None
    if not isinstance(p, dict) or p.get("action"):
        return None
    stars = _stars(p.get("rating"))
    opinion = str(p.get("opinion") or "").strip()[:MAX_OPINION]
    if stars is None and not opinion:
        return None
    m = p.get("metadata")
    meta: dict = m if isinstance(m, dict) else {}
    author = str(meta.get("nickname") or meta.get("preferred_username") or meta.get("given_name") or "").strip()[:MAX_NICK]
    sig = str(raw.get("signature") or "")
    out: dict = {"date": _date(p.get("iat")), "iat": int(p.get("iat") or 0), "link": review_link(sig)}
    if stars is not None:
        out["stars"] = stars
        out["rating"] = p.get("rating")
    if opinion:
        out["opinion"] = opinion
    if author:
        out["author"] = author
    lic = str(meta.get("license") or "")
    if lic and lic != "CC-BY-4.0":
        out["license"] = lic
    if my_kid and _one_line(str(raw.get("kid") or "")) == my_kid:
        out["mine"] = True
    return out


def reviews(name: str, lat, lon, osm_id: str = "", account: str = "", *,
            get: Get | None = None, now: float | None = None, keys: Path | None = None) -> tuple[bool, dict]:
    name = " ".join(str(name or "").split())
    pt = _point(lat, lon)
    if not name or pt is None:
        return False, {"status": 400, "code": "bad_place", "error": "name, lat and lon are needed"}
    la, lo = pt
    now_s = time.time() if now is None else now
    sub = subject(name, la, lo, ASK_U)
    key = f"{name.lower()}|{la:.4f}|{lo:.4f}"
    with _lock:
        hit = _cache.get(key)
    if hit and now_s - hit[0] < CACHE_S:
        raw_list = hit[1]["reviews"]
    else:
        url = f"{_api()}/reviews?{urllib.parse.urlencode({'sub': sub, 'limit': 100})}"
        try:
            status, body = (get or _get)(url)
            data = json.loads(body) if status == 200 else None
        except (OSError, ValueError) as e:
            _failures.swallowed(e)
            data = None
        if not isinstance(data, dict) or not isinstance(data.get("reviews"), list):
            return True, {"found": False, "reason": "failed", "credit": "Mangrove", "link": place_link(sub)}
        raw_list = [r for r in data["reviews"] if isinstance(r, dict)]
        with _lock:
            _cache[key] = (now_s, {"reviews": raw_list})
    mine = _my_kid(account, keys)
    shaped = [s for s in (_shape(r, mine) for r in raw_list) if s]
    shaped.sort(key=lambda s: s["iat"], reverse=True)
    rated = [s["stars"] for s in shaped if "stars" in s]
    avg = round(sum(rated) / len(rated), 1) if rated else None
    for s in shaped:
        s.pop("iat", None)
    return True, {"found": bool(shaped), "count": len(shaped), "rated": len(rated), "average": avg,
                  "reviews": shaped[:SHOW], "link": place_link(subject(name, la, lo, POST_U)),
                  "credit": "Mangrove", "license": LICENSE, "signed_up": bool(mine),
                  **({"osm_id": osm_id} if osm_id else {})}


# --- the account's key ------------------------------------------------------

def _keys_path() -> Path:
    from agent_media_core._paths import state_dir

    return state_dir() / "mangrove-keys.json"


def _load_keys(path: Path | None) -> dict:
    try:
        d = json.loads((path or _keys_path()).read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_keys(d: dict, path: Path | None) -> None:
    p = path or _keys_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{os.getpid()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(d, f)
    os.chmod(tmp, 0o600)
    os.replace(tmp, p)


def _one_line(pem: str) -> str:
    return "".join(pem.split()).replace("-----BEGINPUBLICKEY-----", "-----BEGIN PUBLIC KEY-----") \
        .replace("-----ENDPUBLICKEY-----", "-----END PUBLIC KEY-----")


def _private(account: str, path: Path | None, make: bool):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    with _lock:
        d = _load_keys(path)
        pem = (d.get(account) or {}).get("private") if isinstance(d.get(account), dict) else None
        if pem:
            return serialization.load_pem_private_key(pem.encode(), password=None)
        if not make:
            return None
        k = ec.generate_private_key(ec.SECP256R1())
        d[account] = {"private": k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                  serialization.NoEncryption()).decode(),
                      "made": int(time.time())}
        _save_keys(d, path)
        return k


def _public_pem(k) -> str:
    from cryptography.hazmat.primitives import serialization

    der = k.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return f"-----BEGIN PUBLIC KEY-----{base64.b64encode(der).decode()}-----END PUBLIC KEY-----"


def _my_kid(account: str, path: Path | None) -> str:
    try:
        k = _private(account, path, make=False)
    except Exception as e:  # noqa: BLE001 - a bad key file means "no key", and is logged
        _failures.swallowed(e)
        return ""
    return _public_pem(k) if k is not None else ""


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def sign(k, payload: dict) -> str:
    """The review as a Mangrove JWT, ES256 over `header.payload`."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

    nums = k.public_key().public_numbers()
    jwk = {"crv": "P-256", "ext": True, "key_ops": ["verify"], "kty": "EC",
           "x": _b64u(nums.x.to_bytes(32, "big")), "y": _b64u(nums.y.to_bytes(32, "big"))}
    header = {"alg": "ES256", "typ": "JWT", "kid": _public_pem(k), "jwk": json.dumps(jwk, separators=(",", ":"))}
    signing = (_b64u(json.dumps(header, separators=(",", ":")).encode()) + "."
               + _b64u(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()))
    r, s = decode_dss_signature(k.sign(signing.encode(), ec.ECDSA(hashes.SHA256())))
    return f"{signing}.{_b64u(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"


def post(account: str, body: dict, *, send: Post | None = None, keys: Path | None = None,
         now: float | None = None) -> tuple[bool, dict]:
    if body.get("confirm") is not True:
        return False, {"status": 400, "code": "not_confirmed",
                       "error": "a review is public for good: send confirm: true once the person has agreed"}
    name = " ".join(str(body.get("name") or "").split())[:MAX_NAME]
    pt = _point(body.get("lat"), body.get("lon"))
    if not name or pt is None:
        return False, {"status": 400, "code": "bad_place", "error": "name, lat and lon are needed"}
    stars = body.get("stars")
    if not isinstance(stars, int) or isinstance(stars, bool) or not 1 <= stars <= 5:
        return False, {"status": 400, "code": "bad_stars", "error": "stars is a whole number from 1 to 5"}
    opinion = str(body.get("opinion") or "").strip()
    if len(opinion) > MAX_OPINION:
        return False, {"status": 400, "code": "too_long", "error": f"at most {MAX_OPINION} characters"}
    osm_id = str(body.get("osm_id") or "").strip()
    nick = " ".join(str(body.get("nickname") or "").split())[:MAX_NICK]
    la, lo = pt
    meta: dict = {"client_id": CLIENT_ID, "is_personal_experience": True}
    if osm_id:
        meta["osm_id"] = osm_id[:40]
    if nick:
        meta["nickname"] = nick
    payload: dict = {"sub": subject(name, la, lo, POST_U), "rating": (stars - 1) * 25,
                     "iat": int(time.time() if now is None else now), "metadata": meta}
    if opinion:
        payload["opinion"] = opinion
    try:
        k = _private(account, keys, make=True)
        jwt = sign(k, payload)
    except Exception as e:  # noqa: BLE001 - reported to the app, and logged
        _failures.swallowed(e)
        return False, {"status": 500, "code": "key", "error": "could not sign the review"}
    try:
        status, resp = (send or _post)(f"{_api()}/submit", jwt.encode())
    except OSError as e:
        _failures.swallowed(e)
        return False, {"status": 502, "code": "unreachable", "error": "Mangrove did not answer"}
    if not 200 <= status < 300:
        msg = resp.decode("utf-8", "replace")[:300] if resp else ""
        return False, {"status": 502, "code": "refused", "error": f"Mangrove refused it ({status}) {msg}".strip()}
    with _lock:
        _cache.pop(f"{name.lower()}|{la:.4f}|{lo:.4f}", None)
    sig = jwt.rsplit(".", 1)[1]
    return True, {"signature": sig, "link": review_link(sig), "license": LICENSE}
