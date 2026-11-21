"""Adding opening hours to OpenStreetMap from the Map, under the owner's
own OSM account (David, 21 Nov 2026: "Can we do one and two?" — one being
"add them to OpenStreetMap ourselves"; asked how: "My OSM account").

  GET  /map/osm             → {ok, configured, signed_in, user?}
  POST /map/osm/start       admins → {ok, url}: OSM's sign-in page (OAuth 2
                            with PKCE; no client secret, so the app is
                            registered as a public client)
  GET  /map/osm/callback    no bearer: OSM sends the browser back here with
                            `code` and `state`; the state is the credential
                            (one pending, ten minutes)
  POST /map/osm/signout     admins → forgets the token
  POST /map/osm/hours       admins {id: "node/1", opening_hours?, name?,
                            tags?: {key: value}, overwrite?: [key],
                            source?: survey|website|local_knowledge,
                            source_url?} → {ok, changeset, url, version,
                            set, kept}: opening_hours set (replacing), and
                            each of `tags` (site_read's keys only) the
                            element has not got, or names in `overwrite`,
                            in a changeset of its own (22 Nov 2026)

The edit is public and under the owner's name, so the app asks before each
one. The hours are checked with the same parser the Map reads them with
(opening-hours-py) before anything is sent. One changeset per edit,
opened, used and closed at once, its comment naming the place and saying
it came from Sasonica.

Settings: `MEDIA_OSM_CLIENT_ID` (the app registered on openstreetmap.org
with redirect `<MEDIA_PUBLIC_URL>/map/osm/callback` and scopes read_prefs
and write_api); `MEDIA_OSM_URL` for another OSM server (the dev one,
https://master.apis.dev.openstreetmap.org, to try it out). The token is
kept 0600 in `state_dir()/osm-account.json`; OSM's tokens do not expire.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Callable

from agent_media_core import failures as _failures

from . import auth

PATHS = ("/map/osm", "/map/osm/start", "/map/osm/callback", "/map/osm/signout", "/map/osm/hours")
PENDING_S = 600
SCOPES = "read_prefs write_api"
CREATED_BY = "Sasonica"
_ID = re.compile(r"(node|way|relation)/(\d{1,15})")

#: (method, url, body bytes or None, headers) → (status, body bytes). Tests pass their own.
Http = Callable[[str, str, bytes | None, dict], tuple[int, bytes]]


def _base() -> str:
    return (os.environ.get("MEDIA_OSM_URL") or "https://www.openstreetmap.org").strip().rstrip("/")


def client_id() -> str:
    return (os.environ.get("MEDIA_OSM_CLIENT_ID") or "").strip()


def redirect() -> str:
    base = (os.environ.get("MEDIA_PUBLIC_URL") or "https://red5.sasonica.com").strip().rstrip("/")
    return f"{base}/map/osm/callback"


def _path() -> Path:
    from agent_media_core._paths import state_dir

    return state_dir() / "osm-account.json"


def _load() -> dict:
    try:
        d = json.loads(_path().read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(d: dict) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{os.getpid()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(d, f)
    os.replace(tmp, p)


def _http(method: str, url: str, body: bytes | None, headers: dict) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def status() -> dict:
    d = _load()
    out = {"configured": bool(client_id()), "signed_in": bool(d.get("token"))}
    if d.get("user"):
        out["user"] = d["user"]
    return out


def begin(now: float | None = None) -> str:
    cid = client_id()
    if not cid:
        raise ValueError("OpenStreetMap is not set up on this server (MEDIA_OSM_CLIENT_ID)")
    now = time.time() if now is None else now
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(24)
    d = _load()
    d["pending"] = {"state": state, "verifier": verifier, "at": now}
    _save(d)
    q = urllib.parse.urlencode({"client_id": cid, "response_type": "code", "redirect_uri": redirect(),
                                "scope": SCOPES, "state": state, "code_challenge_method": "S256",
                                "code_challenge": challenge})
    return f"{_base()}/oauth2/authorize?{q}"


def finish(code: str, state: str, now: float | None = None, http: Http = _http) -> dict:
    now = time.time() if now is None else now
    d = _load()
    p = d.get("pending") or {}
    if not (state and p.get("state") and hmac.compare_digest(str(p["state"]), str(state))):
        raise ValueError("that sign-in link is not the one this server made; start again")
    if now - float(p.get("at") or 0) > PENDING_S:
        raise ValueError("that sign-in took too long; start again")
    form = urllib.parse.urlencode({"grant_type": "authorization_code", "code": code, "redirect_uri": redirect(),
                                   "client_id": client_id(), "code_verifier": p["verifier"]}).encode()
    st, body = http("POST", f"{_base()}/oauth2/token", form, {"Content-Type": "application/x-www-form-urlencoded"})
    got = json.loads(body.decode() or "{}") if st == 200 else {}
    token = got.get("access_token")
    if not token:
        raise ValueError(f"OpenStreetMap gave no token ({st})")
    user = ""
    try:
        st, body = http("GET", f"{_base()}/api/0.6/user/details.json", None, {"Authorization": f"Bearer {token}"})
        if st == 200:
            user = str((json.loads(body.decode() or "{}").get("user") or {}).get("display_name") or "")
    except Exception as e:  # noqa: BLE001 - the name is only for Settings
        _failures.swallowed(e)
    d.pop("pending", None)
    d.update(token=token, user=user, signed_in_at=now, scope=got.get("scope") or SCOPES)
    _save(d)
    return {"user": user}


def signout() -> None:
    d = _load()
    for k in ("token", "user", "signed_in_at", "scope", "pending"):
        d.pop(k, None)
    _save(d)


def valid_hours(hours: str) -> bool:
    try:
        import opening_hours as _oh

        _oh.OpeningHours(hours)
        return True
    except Exception:  # noqa: BLE001 - not readable is the answer
        return False


#: Words a changeset's `source` may say: what OSM allows copying from.
#: Never Google's or Foursquare's (their hours cannot go to OSM).
SOURCES = ("survey", "website", "local_knowledge")
_ENUMS = {"reservation": {"yes", "no", "required", "recommended"},
          "wheelchair": {"yes", "limited", "no"}, "outdoor_seating": {"yes", "no"},
          "internet_access": {"wlan", "yes", "no"}, "internet_access:fee": {"yes", "no"}}


def _tags(raw) -> tuple[dict[str, str], str]:
    """The tags asked for, checked against site_read's keys: (tags, error)."""
    from . import site_read

    if raw in (None, {}):
        return {}, ""
    if not isinstance(raw, dict) or len(raw) > 40:
        return {}, "tags is an object of OSM keys and values"
    out = {}
    for k, v in raw.items():
        k, v = str(k), " ".join(str(v if v is not None else "").split())
        if k == "opening_hours":
            return {}, "opening_hours goes in its own field"
        if k not in site_read.TAG_KEYS and k not in site_read.PAYMENT_KEYS:
            return {}, f"{k} is not a tag this server sets"
        if not v or len(v) > 255:
            return {}, f"{k}: a value of 1 to 255 characters"
        allowed = {"yes", "no"} if k in site_read.PAYMENT_KEYS else _ENUMS.get(k)
        if allowed and v not in allowed:
            return {}, f"{k} is one of {', '.join(sorted(allowed))}"
        out[k] = v
    return out, ""


def set_hours(el_id: str, hours: str, name: str = "", http: Http = _http, *, tags=None,
              overwrite=(), source: str = "", source_url: str = "", today=None) -> tuple[bool, dict]:
    """The element's opening_hours set to `hours` (replacing any) with
    `check_date:opening_hours` today (OSM's date they were last checked),
    and any `tags` it has not got yet (one it has is replaced only when
    named in `overwrite`), in a changeset of its own."""
    m = _ID.fullmatch((el_id or "").strip())
    if not m:
        return False, {"status": 400, "code": "bad_id", "error": "id is node/<n>, way/<n> or relation/<n>"}
    hours = " ".join((hours or "").split())
    extra, err = _tags(tags)
    if err:
        return False, {"status": 400, "code": "bad_tags", "error": err}
    if not hours and not extra:
        return False, {"status": 400, "code": "bad_hours", "error": "give opening_hours or tags"}
    if hours and (len(hours) > 255 or not valid_hours(hours)):
        return False, {"status": 400, "code": "bad_hours", "error": "those hours are not in OpenStreetMap's form, e.g. Mo-Su 11:30-20:00"}
    source = (source or "").strip().lower()
    if source and source not in SOURCES:
        return False, {"status": 400, "code": "bad_source",
                       "error": f"source is one of {', '.join(SOURCES)}: OpenStreetMap cannot take what Google or Foursquare say"}
    source_url = (source_url or "").strip()
    if source_url and not (source_url.startswith(("http://", "https://")) and len(source_url) <= 255):
        return False, {"status": 400, "code": "bad_source", "error": "source_url is a web address"}
    replace = {str(k) for k in (overwrite or []) if isinstance(k, str)} if isinstance(overwrite, (list, tuple)) else set()
    token = _load().get("token")
    if not token:
        return False, {"status": 409, "code": "not_signed_in", "error": "sign in to OpenStreetMap first"}
    kind, num = m.group(1), m.group(2)
    api = f"{_base()}/api/0.6"
    auth_h = {"Authorization": f"Bearer {token}"}
    xml_h = {**auth_h, "Content-Type": "text/xml; charset=utf-8"}
    st, body = http("GET", f"{api}/{kind}/{num}", None, auth_h)
    if st == 401:
        return False, {"status": 409, "code": "not_signed_in", "error": "OpenStreetMap no longer accepts the sign-in; sign in again"}
    if st != 200:
        return False, {"status": 404 if st in (404, 410) else 502, "code": "no_element", "error": f"OpenStreetMap could not give {el_id} ({st})"}
    root = ET.fromstring(body)
    el = root.find(kind)
    if el is None:
        return False, {"status": 502, "code": "no_element", "error": f"OpenStreetMap's answer had no {kind}"}
    have = {t.get("k"): t for t in el.findall("tag")}
    kept = sorted(k for k in extra if k in have and k not in replace and have[k].get("v") != extra[k])
    sets = {k: v for k, v in extra.items() if k not in kept and (have.get(k) is None or have[k].get("v") != v)}
    if hours:
        import datetime as _dt

        sets = {"opening_hours": hours, **sets,
                "check_date:opening_hours": (today or _dt.date.today()).isoformat()}
    if not sets:
        return False, {"status": 409, "code": "nothing_to_change", "kept": kept,
                       "error": "OpenStreetMap already has all of that"}
    what = name or (have["name"].get("v") if "name" in have else "") or el_id
    said = [k.replace("_", " ") for k in sets if not k.startswith("check_date")][:4]
    words = ", ".join(said[:-1]) + (" and " if len(said) > 1 else "") + said[-1]
    cs = ET.Element("osm")
    c = ET.SubElement(cs, "changeset")
    ctags = [("created_by", CREATED_BY), ("comment", f"Add {words} for {what}"[:255])]
    if source:
        ctags.append(("source", source_url if source == "website" and source_url else source))
    for k, v in ctags:
        ET.SubElement(c, "tag", k=k, v=v)
    st, body = http("PUT", f"{api}/changeset/create", ET.tostring(cs, encoding="utf-8"), xml_h)
    if st == 401:
        return False, {"status": 409, "code": "not_signed_in", "error": "OpenStreetMap no longer accepts the sign-in; sign in again"}
    if st != 200:
        return False, {"status": 502, "code": "changeset", "error": f"OpenStreetMap would not open a changeset ({st})"}
    changeset = body.decode().strip()
    try:
        for k, v in sets.items():
            if have.get(k) is None:
                ET.SubElement(el, "tag", k=k, v=v)
            else:
                have[k].set("v", v)
        el.set("changeset", changeset)
        for a in ("user", "uid", "timestamp", "visible"):
            el.attrib.pop(a, None)
        out = ET.Element("osm")
        out.append(el)
        st, body = http("PUT", f"{api}/{kind}/{num}", ET.tostring(out, encoding="utf-8"), xml_h)
        if st == 409:
            return False, {"status": 409, "code": "conflict", "error": "someone changed that place on OpenStreetMap just now; try again"}
        if st != 200:
            return False, {"status": 502, "code": "upload", "error": f"OpenStreetMap would not take the change ({st})"}
        version = body.decode().strip()
    finally:
        try:
            http("PUT", f"{api}/changeset/{changeset}/close", None, auth_h)
        except Exception as e:  # noqa: BLE001 - OSM closes an idle changeset itself within the hour
            _failures.swallowed(e)
    # The next places asked see it once Overpass has it; drop what is kept meanwhile.
    try:
        from . import map_places

        map_places.forget()
    except Exception as e:  # noqa: BLE001
        _failures.swallowed(e)
    out_d = {"changeset": changeset, "version": version, "url": f"{_base()}/changeset/{changeset}",
             "set": sorted(sets), "kept": kept}
    if hours:
        out_d["opening_hours"] = hours
    return True, out_d


def _page(h: BaseHTTPRequestHandler, code: int, title: str, words: str) -> None:
    body = (f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>"
            f"<title>{html.escape(title)}</title><body style='font:18px system-ui;margin:2em'>"
            f"<h2>{html.escape(title)}</h2><p>{html.escape(words)}</p>").encode()
    h.send_response(code)
    h.send_header("Content-Type", "text/html; charset=utf-8")
    h.send_header("Content-Length", str(len(body)))
    h.send_header("Cache-Control", "no-store")
    h.end_headers()
    h.wfile.write(body)


def handle(h: BaseHTTPRequestHandler, method: str, path: str, bearer: str, send_json, read_json) -> bool:
    if path == "/map/osm/callback" and method == "GET":
        q = urllib.parse.parse_qs(h.path.partition("?")[2])
        if q.get("error"):
            _page(h, 400, "OpenStreetMap sign-in stopped", f"OpenStreetMap said: {q['error'][0]}. Nothing was changed.")
            return True
        try:
            who = finish((q.get("code") or [""])[0], (q.get("state") or [""])[0])
        except ValueError as e:
            _page(h, 400, "OpenStreetMap sign-in didn't work", str(e))
            return True
        except Exception as e:  # noqa: BLE001
            _failures.swallowed(e)
            _page(h, 502, "OpenStreetMap sign-in didn't work", "OpenStreetMap could not be reached. Try again.")
            return True
        _page(h, 200, "Signed in to OpenStreetMap",
              f"Signed in{' as ' + who['user'] if who.get('user') else ''}. You can close this page and go back to Sasonica.")
        return True
    if path not in PATHS:
        return False
    if path == "/map/osm" and method == "GET":
        user, err = auth.gate(bearer)
        if not user:
            send_json(h, err.pop("status", 401), {"ok": False, **err})
        else:
            send_json(h, 200, {"ok": True, **status()})
        return True
    if method != "POST":
        return False
    user, err = auth.may_admin(bearer)
    if not user:
        send_json(h, err.pop("status", 403), {"ok": False, **err})
        return True
    if path == "/map/osm/start":
        try:
            send_json(h, 200, {"ok": True, "url": begin()})
        except ValueError as e:
            send_json(h, 409, {"ok": False, "code": "not_configured", "error": str(e)})
        return True
    if path == "/map/osm/signout":
        signout()
        send_json(h, 200, {"ok": True, **status()})
        return True
    body = read_json(h) or {}
    ok, detail = set_hours(str(body.get("id") or ""), str(body.get("opening_hours") or ""), str(body.get("name") or "")[:120],
                           tags=body.get("tags"), overwrite=body.get("overwrite") or (),
                           source=str(body.get("source") or ""), source_url=str(body.get("source_url") or ""))
    code = 200 if ok else int(detail.pop("status", 400))
    send_json(h, code, {"ok": ok, **detail})
    return True
