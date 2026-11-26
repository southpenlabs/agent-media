"""Sharing a place on the AT Protocol (place_share.py, §6.50a), against a
fake PDS (and plc directory) in this process: no test reaches a real one
(conftest refuses any address but 127.0.0.1)."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import shutil
import stat
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

from agent_media_server import devices, place_share as ps

from test_contract import call, server, typed  # noqa: F401
from test_mic import _device

DID = "did:plc:" + "a" * 24
HANDLE = "alice.test"
PASSWORD = "abcd-efgh-ijkl-mnop"


class FakePDS:
    def __init__(self):
        self.records: dict[tuple[str, str], dict] = {}
        self.blobs: dict[str, bytes] = {}
        self.calls: list[tuple[str, str]] = []
        self.expire_next = False
        self.blob_limit = 5_000_000
        self.tokens = {"access": "acc-1", "refresh": "ref-1"}
        self.aka = [f"at://{HANDLE}"]
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj):
                b = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def _body(self):
                n = int(self.headers.get("Content-Length") or 0)
                return self.rfile.read(n) if n else b""

            def _authed(self):
                if self.headers.get("Authorization") != f"Bearer {fake.tokens['access']}":
                    self._send(401, {"error": "AuthMissing", "message": "who are you"})
                    return False
                if fake.expire_next:
                    fake.expire_next = False
                    self._send(400, {"error": "ExpiredToken", "message": "Token has expired"})
                    return False
                return True

            def do_GET(self):
                u = urlsplit(self.path)
                q = {k: v[0] for k, v in parse_qs(u.query).items()}
                fake.calls.append(("GET", u.path))
                if u.path == f"/{DID}":
                    return self._send(200, {"id": DID, "alsoKnownAs": fake.aka,
                                            "service": [{"id": "#atproto_pds", "type": "AtprotoPersonalDataServer",
                                                         "serviceEndpoint": fake.base}]})
                if u.path == "/xrpc/com.atproto.identity.resolveHandle":
                    return self._send(200, {"did": DID}) if q.get("handle") == HANDLE else \
                        self._send(400, {"error": "InvalidRequest"})
                if u.path == "/xrpc/com.atproto.server.describeServer":
                    return self._send(200, {"did": "did:web:pds.test", "blobUploadLimit": fake.blob_limit})
                if u.path == "/xrpc/com.atproto.repo.listRecords":
                    recs = [{"uri": f"at://{DID}/{c}/{k}", "cid": "bafyx", "value": v}
                            for (c, k), v in sorted(fake.records.items(), reverse=True) if c == q.get("collection")]
                    return self._send(200, {"records": recs})
                if u.path == "/xrpc/com.atproto.repo.getRecord":
                    v = fake.records.get((q.get("collection", ""), q.get("rkey", "")))
                    return self._send(200, {"uri": "x", "value": v}) if v else \
                        self._send(400, {"error": "RecordNotFound"})
                self._send(404, {"error": "nope"})

            def do_POST(self):
                u = urlsplit(self.path)
                fake.calls.append(("POST", u.path))
                body = self._body()
                if u.path == "/xrpc/com.atproto.server.createSession":
                    got = json.loads(body)
                    if got.get("identifier") != DID or got.get("password") != PASSWORD:
                        return self._send(401, {"error": "AuthenticationRequired", "message": "Invalid identifier or password"})
                    return self._send(200, {"did": DID, "handle": HANDLE, "accessJwt": fake.tokens["access"],
                                            "refreshJwt": fake.tokens["refresh"]})
                if u.path == "/xrpc/com.atproto.server.refreshSession":
                    if self.headers.get("Authorization") != f"Bearer {fake.tokens['refresh']}":
                        return self._send(400, {"error": "ExpiredToken"})
                    fake.tokens = {"access": "acc-2", "refresh": "ref-2"}
                    return self._send(200, {"did": DID, "handle": HANDLE, "accessJwt": "acc-2", "refreshJwt": "ref-2"})
                if not self._authed():
                    return None
                if u.path == "/xrpc/com.atproto.repo.uploadBlob":
                    if len(body) > fake.blob_limit:
                        return self._send(400, {"error": "BlobTooLarge"})
                    raw = bytes([1, 0x55, 0x12, 0x20]) + hashlib.sha256(body).digest()
                    cid = "b" + base64.b32encode(raw).decode().lower().rstrip("=")
                    fake.blobs[cid] = body
                    return self._send(200, {"blob": {"$type": "blob", "ref": {"$link": cid},
                                                     "mimeType": self.headers.get("Content-Type"), "size": len(body)}})
                if u.path == "/xrpc/com.atproto.repo.applyWrites":
                    got = json.loads(body)
                    results = []
                    for w in got["writes"]:
                        key = (w["collection"], w["rkey"])
                        if w["$type"].endswith("#create"):
                            if key in fake.records:
                                return self._send(400, {"error": "InvalidRequest", "message": "exists"})
                            fake.records[key] = w["value"]
                            results.append({"uri": f"at://{DID}/{key[0]}/{key[1]}", "cid": "bafyrec"})
                        else:
                            fake.records.pop(key, None)
                    return self._send(200, {"results": results})
                self._send(404, {"error": "nope"})

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.srv.daemon_threads = True
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    def writes(self):
        return [c for c in self.calls if c[0] == "POST" and c[1].endswith(("applyWrites", "uploadBlob"))]


@pytest.fixture()
def pds(monkeypatch, tmp_path):
    f = FakePDS()
    monkeypatch.setenv("MEDIA_UPLOAD_DIR", str(tmp_path / "shared"))
    (tmp_path / "shared").mkdir()
    yield f
    f.srv.shutdown()
    f.srv.server_close()


def _deps(tmp_path, pds, saved=(), things=(), fixes=(), clock=None):
    return ps.Deps(accounts=tmp_path / "acc.json", stage=tmp_path / "stage", plc=pds.base, appview=pds.base,
                   saved_places=lambda b: list(saved), things=lambda b: list(things),
                   phone_fixes=lambda: list(fixes), **({"clock": clock} if clock else {}))


def _signed_up(tmp_path, pds, **kw):
    d = _deps(tmp_path, pds, **kw)
    ps.add_account("", HANDLE, PASSWORD, pds.base, d)
    return d


PLACE = {"name": "Flinders Pier", "id": "way/123456", "lat": -38.4741234, "lon": 145.0215678,
         "address": {"country": "au", "region": "VIC", "locality": "Flinders", "street": "Cook St"}}


# --- identifiers -------------------------------------------------------------

def test_blob_cid_and_tid():
    # The CID of no bytes as a PDS (or IPFS) makes a raw block's.
    assert ps.blob_cid(b"") == "bafkreihdwdcefgh4dqkjv67uzcmw7ojee6xedzdetojuzjevtenxquvyku"
    a, b = ps.tid(1_700_000_000.0), ps.tid(1_700_000_001.0)
    assert len(a) == 13 and a < b and set(a) <= set(ps._S32)


# --- accounts ----------------------------------------------------------------

def test_resolve_checks_the_handle_both_ways(tmp_path, pds):
    d = _deps(tmp_path, pds)
    assert ps.resolve("@Alice.test", pds.base, d) == {"handle": HANDLE, "did": DID, "pds": pds.base}
    assert ps.resolve(DID, "", d)["handle"] == HANDLE
    pds.aka = ["at://someone.else"]
    with pytest.raises(ps.ShareError) as e:
        ps.resolve(HANDLE, pds.base, d)
    assert e.value.code == "handle_mismatch"
    with pytest.raises(ps.ShareError) as e:
        ps.resolve("not a handle", "", d)
    assert e.value.code == "bad_handle"


def test_add_list_remove_keeps_the_password_private(tmp_path, pds):
    d = _deps(tmp_path, pds)
    with pytest.raises(ps.ShareError) as e:
        ps.add_account("", HANDLE, "my-main-password", pds.base, d)
    assert e.value.code == "not_app_password"
    with pytest.raises(ps.ShareError) as e:
        ps.add_account("", HANDLE, "zzzz-zzzz-zzzz-zzzz", pds.base, d)
    assert e.value.code == "sign_in_refused" and not d.accounts_path().exists()
    got = ps.add_account("", HANDLE, PASSWORD.upper(), pds.base, d)
    assert got == {"handle": HANDLE, "did": DID, "pds": pds.base, "added": got["added"], "default": True,
                   "signed_in": True}
    assert stat.S_IMODE(d.accounts_path().stat().st_mode) == 0o600
    listed = ps.accounts("", d)
    assert listed == [got] and PASSWORD not in json.dumps(listed)
    assert ps.accounts("someone-else", d) == []
    assert ps.remove_account("", "@alice.test", d) == {"handle": HANDLE, "did": DID}
    assert ps.accounts("", d) == []


# --- preview and confirm -----------------------------------------------------

def test_preview_writes_nothing_and_confirm_writes_exactly_it(tmp_path, pds):
    t = [1_790_000_000.0]
    d = _signed_up(tmp_path, pds, clock=lambda: t[0])
    pv = ps.preview("", {"place": PLACE, "text": "Good low tide this morning", "tags": ["Low tide", "#swim"]}, d)
    assert pds.writes() == []
    place, post = pv["records"][0]["record"], pv["records"][1]["record"]
    assert place["$type"] == "com.sasonica.place.post"
    assert place["place"] == {"name": "Flinders Pier", "osm": {"type": "way", "id": 123456},
                              "location": {"$type": "community.lexicon.location.geo",
                                           "latitude": "-38.474123", "longitude": "145.021568"},
                              "address": {"$type": "community.lexicon.location.address", "country": "AU",
                                          "region": "VIC", "locality": "Flinders", "street": "Cook St"}}
    assert place["tags"] == ["low-tide", "swim"] and place["bskyPost"] == pv["uris"]["bluesky"]
    assert post["text"] == "Good low tide this morning\n\n📍 Flinders Pier #low-tide #swim"
    b = post["text"].encode()
    link, tag1, tag2 = post["facets"]
    assert b[link["index"]["byteStart"]:link["index"]["byteEnd"]] == "Flinders Pier".encode()
    assert link["features"][0]["uri"] == "https://www.openstreetmap.org/way/123456"
    assert b[tag1["index"]["byteStart"]:tag1["index"]["byteEnd"]] == b"#low-tide"
    assert tag2["features"][0] == {"$type": "app.bsky.richtext.facet#tag", "tag": "swim"}
    assert post["embed"]["$type"] == "app.bsky.embed.external" and post["langs"] == ["en"]
    t[0] += 60
    done = ps.confirm("", pv["token"], d)
    assert done["uri"] == pv["uris"]["place"] and done["bsky_uri"] == pv["uris"]["bluesky"]
    assert done["bsky_link"] == f"https://bsky.app/profile/{HANDLE}/post/{pv['records'][1]['rkey']}"
    kept = pds.records[("com.sasonica.place.post", pv["records"][0]["rkey"])]
    assert {k: v for k, v in kept.items() if k != "createdAt"} == {k: v for k, v in place.items() if k != "createdAt"}
    assert kept["createdAt"] != place["createdAt"] and kept["createdAt"].endswith("Z")
    with pytest.raises(ps.ShareError) as e:
        ps.confirm("", pv["token"], d)
    assert e.value.code == "no_preview"
    with pytest.raises(ps.ShareError) as e:
        ps.confirm("", "0" * 40, d)
    assert e.value.code == "no_preview"


def test_confirm_needs_the_same_book_and_a_fresh_untouched_preview(tmp_path, pds):
    t = [1_790_000_000.0]
    d = _signed_up(tmp_path, pds, clock=lambda: t[0])
    pv = ps.preview("", {"place": PLACE, "bluesky_post": False}, d)
    assert len(pv["records"]) == 1 and "bskyPost" not in pv["records"][0]["record"]
    with pytest.raises(ps.ShareError):
        ps.confirm("a-member", pv["token"], d)
    meta = d.stage_dir() / pv["token"] / "preview.json"
    good = meta.read_text()
    meta.write_text(good.replace("Flinders Pier", "Somewhere else"))
    with pytest.raises(ps.ShareError) as e:
        ps.confirm("", pv["token"], d)
    assert e.value.code == "changed"
    meta.write_text(good)
    t[0] += ps.STAGE_S + 1
    with pytest.raises(ps.ShareError) as e:
        ps.confirm("", pv["token"], d)
    assert e.value.code == "no_preview" and pds.writes() == []


def test_blur_drops_the_osm_id_and_street(tmp_path, pds):
    d = _signed_up(tmp_path, pds)
    pv = ps.preview("", {"place": PLACE, "blur": True}, d)
    rec = pv["records"][0]["record"]
    assert rec["blurred"] is True and "osm" not in rec["place"]
    assert rec["place"]["location"]["latitude"] == "-38.474" and rec["place"]["location"]["longitude"] == "145.022"
    assert "street" not in rec["place"]["address"]
    assert pv["link"].startswith("https://www.openstreetmap.org/?mlat=-38.474&mlon=145.022")


def test_private_places_are_refused(tmp_path, pds):
    home = {"id": "home", "lat": -38.0, "lon": 145.0}
    d = _signed_up(tmp_path, pds, saved=[home], things=[{"lat": -37.5, "lon": 144.5}],
                   fixes=[{"lat": -37.9, "lon": 144.9}])

    def code(place):
        with pytest.raises(ps.ShareError) as e:
            ps.preview("", {"place": place}, d)
        return e.value.code

    assert code({**PLACE, "layer": "things"}) == "private_place"
    assert code({**PLACE, "layer": "phone"}) == "private_place"
    assert code({**PLACE, "saved": "home"}) == "private_place"
    assert code({"name": "Next door", "lat": -38.0005, "lon": 145.0}) == "private_place"
    assert code({"name": "Speaker", "lat": -37.5, "lon": 144.5001}) == "private_place"
    assert code({"name": "Dropped pin", "lat": -37.90001, "lon": 144.9}) == "phone_position"
    assert code({**PLACE, "from_phone": True}) == "phone_position"
    assert code({"name": "", "lat": 1, "lon": 2}) == "bad_place"
    # The café the phone is in is a place: it has an OSM id.
    assert ps.preview("", {"place": {"name": "Café", "id": "node/9", "lat": -37.90001, "lon": 144.9}}, d)["token"]
    assert pds.writes() == []


def test_no_account_and_unknown_account(tmp_path, pds):
    d = _deps(tmp_path, pds)
    with pytest.raises(ps.ShareError) as e:
        ps.preview("", {"place": PLACE}, d)
    assert e.value.code == "no_account"
    ps.add_account("", HANDLE, PASSWORD, pds.base, d)
    with pytest.raises(ps.ShareError) as e:
        ps.preview("", {"place": PLACE, "account": "sasonica.com"}, d)
    assert e.value.code == "unknown_account"


# --- photos ------------------------------------------------------------------

def _seg(marker: int, body: bytes) -> bytes:
    return bytes([0xFF, marker]) + (len(body) + 2).to_bytes(2, "big") + body


def _jpeg(orientation=6, trailer=b"MotionPhoto_Data....video", gps=b"GPS -38.4741 145.0215"):
    tiff = b"MM\x00\x2a" + (8).to_bytes(4, "big") + (1).to_bytes(2, "big") + (0x0112).to_bytes(2, "big") \
        + (3).to_bytes(2, "big") + (1).to_bytes(4, "big") + orientation.to_bytes(2, "big") + b"\x00\x00" \
        + (0).to_bytes(4, "big") + gps
    return (b"\xff\xd8" + _seg(0xE0, b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00")
            + _seg(0xE1, b"Exif\x00\x00" + tiff)
            + _seg(0xE1, b"http://ns.adobe.com/xap/1.0/\x00<x:xmpmeta>GPSLatitude</x:xmpmeta>")
            + _seg(0xE2, b"ICC_PROFILE\x00\x01\x01profile")
            + _seg(0xE2, b"MPF\x00multi-picture")
            + _seg(0xFE, b"a comment")
            + _seg(0xDB, b"\x00" + bytes(64))
            + _seg(0xC0, b"\x08" + (300).to_bytes(2, "big") + (400).to_bytes(2, "big") + b"\x01\x01\x11\x00")
            + _seg(0xDA, b"\x01\x01\x00\x00\x3f\x00")
            + b"\x12\x34\xff\x00\x56\xff\xd0\x78"
            + b"\xff\xd9" + trailer)


def test_jpeg_metadata_and_trailer_go_orientation_stays():
    clean, orient, size = ps.jpeg_clean(_jpeg())
    assert orient == 6 and size == (400, 300)
    assert b"GPS" not in clean and b"xmpmeta" not in clean and b"MotionPhoto" not in clean
    assert b"a comment" not in clean and b"MPF" not in clean
    assert b"ICC_PROFILE" in clean and b"JFIF" in clean and clean.endswith(b"\xff\xd9")
    assert b"\x12\x34\xff\x00\x56\xff\xd0\x78" in clean
    assert ps._exif_orientation(clean[clean.index(b"Exif"):]) == 6
    got = ps.prepare_photo(_jpeg(), 1_000_000)
    # Turned a quarter: drawn 300 wide, 400 high.
    assert got["mime"] == "image/jpeg" and (got["width"], got["height"]) == (300, 400) and not got["resized"]


def test_png_text_goes():
    def chunk(kind, data):
        return len(data).to_bytes(4, "big") + kind + data + b"\x00\x00\x00\x00"

    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", (5).to_bytes(4, "big") + (7).to_bytes(4, "big") + b"\x08\x02\x00\x00\x00")
           + chunk(b"tEXt", b"Comment\x00where I live") + chunk(b"eXIf", b"MM\x00*GPS")
           + chunk(b"IDAT", b"xx") + chunk(b"IEND", b"") + b"trailing")
    clean, size = ps.png_clean(png)
    assert size == (5, 7) and b"where I live" not in clean and b"GPS" not in clean and clean.endswith(b"IEND\x00\x00\x00\x00")


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg to make a real photo")
def test_a_big_photo_is_made_smaller_and_upright(tmp_path, pds):
    import subprocess

    big = tmp_path / "shared" / "big.jpg"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=3000x2000,noise=alls=60:allf=t", "-frames:v", "1",
                    "-q:v", "1", str(big)], check=True)
    raw = big.read_bytes()
    # A phone's EXIF, turned a quarter, with where it was taken.
    tiff = b"MM\x00\x2a" + (8).to_bytes(4, "big") + (1).to_bytes(2, "big") + (0x0112).to_bytes(2, "big") \
        + (3).to_bytes(2, "big") + (1).to_bytes(4, "big") + (6).to_bytes(2, "big") + b"\x00\x00" \
        + (0).to_bytes(4, "big") + b"GPS -38.47 145.02"
    raw = raw[:2] + _seg(0xE1, b"Exif\x00\x00" + tiff) + raw[2:]
    big.write_bytes(raw)
    d = _signed_up(tmp_path, pds)
    pv = ps.preview("", {"place": PLACE, "photos": [{"upload": str(big), "alt": "The pier at low tide"}]}, d)
    ph = pv["photos"][0]
    assert ph["resized"] and ph["size"] <= 1_000_000 < len(raw) and ph["height"] > ph["width"]
    img = pv["records"][1]["record"]["embed"]["images"][0]
    assert img["alt"] == "The pier at low tide" and img["image"]["ref"]["$link"] == ph["cid"]
    assert pv["records"][0]["record"]["images"][0]["image"] == img["image"]
    ps.confirm("", pv["token"], d)
    sent = pds.blobs[ph["cid"]]
    assert b"GPS" not in sent and len(sent) <= 1_000_000


def test_photos_only_from_the_upload_folder(tmp_path, pds):
    d = _signed_up(tmp_path, pds)
    outside = tmp_path / "secret.jpg"
    outside.write_bytes(_jpeg())
    with pytest.raises(ps.ShareError) as e:
        ps.preview("", {"place": PLACE, "photos": [str(outside)]}, d)
    assert e.value.code == "bad_photo"
    with pytest.raises(ps.ShareError) as e:
        ps.preview("", {"place": PLACE, "photos": ["a"] * 5}, d)
    assert e.value.code == "too_many_photos"
    inside = tmp_path / "shared" / "p.jpg"
    inside.write_bytes(_jpeg())
    pv = ps.preview("", {"place": PLACE, "photos": [str(inside)]}, d)
    done = ps.confirm("", pv["token"], d)
    assert done["uri"] and len(pds.blobs) == 1
    assert b"GPS" not in next(iter(pds.blobs.values()))


# --- sessions, own posts, delete ---------------------------------------------

def test_an_expired_session_is_refreshed(tmp_path, pds):
    d = _signed_up(tmp_path, pds)
    pv = ps.preview("", {"place": PLACE}, d)
    pds.expire_next = True
    assert ps.confirm("", pv["token"], d)["uri"]
    assert ("POST", "/xrpc/com.atproto.server.refreshSession") in pds.calls
    assert json.loads(d.accounts_path().read_text())[""][0]["session"]["access"] == "acc-2"


def test_posts_listed_and_deleted_together(tmp_path, pds):
    d = _signed_up(tmp_path, pds)
    pv = ps.preview("", {"place": PLACE, "text": "Swim here"}, d)
    ps.confirm("", pv["token"], d)
    got = ps.posts("", d=d)
    assert len(got["posts"]) == 1
    p = got["posts"][0]
    assert p["name"] == "Flinders Pier" and p["text"] == "Swim here" and p["lat"] == -38.474123
    assert p["bsky_uri"] == pv["uris"]["bluesky"] and p["bsky_link"].startswith("https://bsky.app/profile/alice.test/")
    gone = ps.delete("", p["uri"], "", d)
    assert set(gone["deleted"]) == {pv["uris"]["place"], pv["uris"]["bluesky"]} and pds.records == {}
    with pytest.raises(ps.ShareError) as e:
        ps.delete("", p["uri"], "", d)
    assert e.value.code == "not_found"
    with pytest.raises(ps.ShareError) as e:
        ps.delete("", f"at://{DID}/app.bsky.feed.post/xyz", "", d)
    assert e.value.code == "bad_uri"


# --- the routes and the CLI ---------------------------------------------------

def test_routes_gate_and_answer(server, monkeypatch, tmp_path, pds):  # noqa: F811
    monkeypatch.setattr(ps, "_DEPS", _deps(tmp_path, pds))
    admin, _ = _device("Pixel 8a")
    res, obj = call(server, "GET", "/map/share/accounts", headers=admin)
    assert res.status == 200 and obj == {"ok": True, "accounts": []}
    res, obj = call(server, "POST", "/map/share/preview", {"place": PLACE}, headers=admin)
    assert res.status == 404 and obj["code"] == "no_account"
    res, obj = call(server, "POST", "/map/share/accounts", {"handle": HANDLE, "app_password": PASSWORD,
                                                            "pds": pds.base}, headers=admin)
    assert res.status == 200 and obj["account"]["did"] == DID and "password" not in json.dumps(obj)
    res, pv = call(server, "POST", "/map/share/preview", {"place": PLACE, "text": "hi"}, headers=admin)
    assert res.status == 200 and pv["token"] and pds.writes() == []
    member, mid = _device("Kid's tablet")
    assert devices.set_role(mid, "member")
    assert call(server, "GET", "/map/share/accounts", headers=member)[0].status == 200
    assert call(server, "POST", "/map/share/confirm", {"token": pv["token"]}, headers=member)[0].status == 403
    assert call(server, "POST", "/map/share/accounts", {"handle": HANDLE}, headers=member)[0].status == 403
    guest, gid = _device("Visitor tablet")
    assert devices.set_role(gid, "guest")
    assert call(server, "GET", "/map/share/accounts", headers=guest)[0].status == 403
    assert call(server, "GET", "/map/share/accounts")[0].status == 401
    res, obj = call(server, "POST", "/map/share/confirm", {"token": pv["token"]}, headers=admin)
    assert res.status == 200 and obj["uri"] == pv["uris"]["place"]
    res, obj = call(server, "GET", "/map/share/posts", headers=admin)
    assert res.status == 200 and obj["posts"][0]["uri"] == pv["uris"]["place"]
    res, obj = call(server, "POST", "/map/share/delete", {"uri": pv["uris"]["place"]}, headers=admin)
    assert res.status == 200 and len(obj["deleted"]) == 2
    res, obj = call(server, "POST", "/map/share/accounts/remove", {"account": HANDLE}, headers=admin)
    assert res.status == 200 and obj["removed"]["did"] == DID


def test_cli_previews_then_posts_with_the_token(tmp_path, pds, monkeypatch, capsys):
    d = _deps(tmp_path, pds)
    monkeypatch.setattr("sys.stdin", io.StringIO(PASSWORD + "\n"))
    assert ps.main(["accounts", "add", HANDLE, "--pds", pds.base], d) == 0
    capsys.readouterr()
    assert ps.main(["share", "Flinders", "Pier", "-38.4741,145.0215", "--osm", "way/123456", "--text", "Low tide",
                    "--tag", "swim", "--json"], d) == 0
    pv = json.loads(capsys.readouterr().out)
    assert pds.writes() == [] and pv["records"][1]["record"]["text"].startswith("Low tide\n\n📍 Flinders Pier")
    assert ps.main(["share", "--go", pv["token"]], d) == 0
    assert json.loads(capsys.readouterr().out)["uri"] == pv["uris"]["place"]
    assert ps.main(["posts"], d) == 0 and "Flinders Pier" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        ps.main(["share", "Nowhere"], d)


# --- the default account, and a country from the point (27 Nov 2026) ----------

def test_the_default_account_can_be_switched(tmp_path, pds):
    d = _deps(tmp_path, pds)
    rows = [{"handle": h, "did": f"did:plc:{h.split('.')[0]}", "pds": pds.base, "password": "zzqq-secret", "added": 1}
            for h in ("david.example.com", "project.example.com", "third.example.com")]
    d.accounts_path().write_text(json.dumps({"": rows}))
    got = ps.set_default("", "@project.example.com", d)
    assert [(r["handle"], r["default"]) for r in got] == [("project.example.com", True),
                                                         ("david.example.com", False), ("third.example.com", False)]
    assert ps.set_default("", "did:plc:third", d)[0]["handle"] == "third.example.com"
    assert [r["handle"] for r in ps.accounts("", d)][1:] == ["project.example.com", "david.example.com"]
    assert "zzqq-secret" not in json.dumps(ps.accounts("", d))
    with pytest.raises(ps.ShareError) as e:
        ps.set_default("", "nobody.example.com", d)
    assert e.value.code == "unknown_account"


def test_a_card_without_a_country_gets_it_from_the_point(tmp_path, pds):
    d = _signed_up(tmp_path, pds)
    asked = []
    d.country_at = lambda la, lo: asked.append((la, lo)) or "AU"
    place = {"name": "Flinders Pier", "id": "way/123456", "lat": -38.4741234, "lon": 145.0215678,
             "address": "Cook St, Flinders"}
    addr = ps.preview("", {"place": place}, d)["records"][0]["record"]["place"]["address"]
    assert addr["country"] == "AU" and addr["locality"] == "Flinders" and asked
    asked.clear()
    ps.preview("", {"place": PLACE}, d)          # a card with a country is not asked about
    assert asked == []
    d.country_at = lambda la, lo: ""
    rec = ps.preview("", {"place": place}, d)["records"][0]["record"]
    assert "address" not in rec["place"]          # no country, no address record, as before


def test_a_history_visit_posts_as_its_named_place_never_its_own_point(tmp_path, pds):
    # David, 27 Nov 2026: "Yes, do that" (sasonica-app mail #1376).
    home = {"id": "home", "lat": -38.0, "lon": 145.0}
    d = _signed_up(tmp_path, pds, saved=[home])
    own = (-38.4741234, 145.0215678)                       # the pier's own point
    d.element_at = lambda osm: own if osm == {"type": "way", "id": 123456} else None
    visit = {**PLACE, "lat": -38.4745, "lon": 145.0219, "layer": "history"}   # where the phone was
    rec = ps.preview("", {"place": visit}, d)["records"][0]["record"]
    loc = rec["place"]["location"]
    assert (loc["latitude"], loc["longitude"]) == ("-38.474123", "145.021568")   # the element's, not the visit's

    def code(place):
        with pytest.raises(ps.ShareError) as e:
            ps.preview("", {"place": place}, d)
        return e.value.code

    no_id = {k: v for k, v in visit.items() if k != "id"}
    assert code(no_id) == "private_place"                                   # a bare visit stays private
    assert code({**visit, "id": "node/77"}) == "private_place"              # its place could not be looked up
    assert code({**visit, "lat": -38.49, "lon": 145.0215678}) == "private_place"   # too far from the place
    d.element_at = lambda osm: (-38.0004, 145.0)                            # a place right by Home
    assert code({**visit, "lat": -38.0004, "lon": 145.0}) == "private_place"
    assert code({**visit, "layer": "trip"}) == "private_place"              # trips are still never posted
    assert pds.writes() == []


def test_where_i_am_now_only_when_chosen(tmp_path, pds):
    # David, 27 Nov 2026: "Or use phone location manually?" → "Allow, blurred by default".
    home = {"id": "home", "lat": -38.0, "lon": 145.0}
    d = _signed_up(tmp_path, pds, saved=[home], fixes=[{"lat": -37.9, "lon": 144.9}])
    spot = {"name": "Secret swim spot", "lat": -37.90001, "lon": 144.9, "layer": "pin"}

    def code(body):
        with pytest.raises(ps.ShareError) as e:
            ps.preview("", body, d)
        return e.value.code

    assert code({"place": spot}) == "phone_position"                          # not chosen: refused as before
    assert code({"place": {**spot, "from_phone": True}}) == "phone_position"
    pv = ps.preview("", {"place": {**spot, "from_phone": True}, "here": True, "blur": True}, d)
    assert pv["here"] is True and "right now, to about 100 m" in pv["here_warning"]
    assert pv["records"][0]["record"]["place"]["location"]["latitude"] == "-37.9"
    exact = ps.preview("", {"place": {**spot, "here": True}}, d)
    assert exact["here_warning"].endswith("exactly. Anyone can see it as soon as it is posted.")
    # Home is still never shared, chosen or not.
    assert code({"place": {"name": "Here", "lat": -38.0003, "lon": 145.0}, "here": True}) == "private_place"
    assert pds.writes() == []
