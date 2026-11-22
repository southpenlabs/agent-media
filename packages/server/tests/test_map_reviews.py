"""GET/POST /map/reviews (map_reviews.py, §6.50): a place's reviews from
Mangrove and posting the account's own. Mangrove is faked throughout (the
conftest refuses the real one)."""
from __future__ import annotations

import base64
import json
import stat
import urllib.parse

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

from agent_media_server import devices, map_reviews

from test_contract import call, server, shelf, signed_in, typed  # noqa: F401
from test_mic import _device

SUB = "geo:-37.8185324,144.9631942?q=City%20Basement%20Books&u=30"


def _raw(sig: str, rating=75, opinion="Big selection.", iat=1760832653, **meta) -> dict:
    return {"signature": sig, "kid": "-----BEGIN PUBLIC KEY-----X-----END PUBLIC KEY-----",
            "payload": {"iat": iat, "sub": SUB, "rating": rating, "opinion": opinion,
                        "metadata": {"client_id": "https://mangrove.reviews", **meta}}}


def _b(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def test_reviews_summarised_newest_first_and_kept_a_day(tmp_path):
    asked = []

    def get(url):
        asked.append(url)
        return 200, json.dumps({"reviews": [
            _raw("old", rating=50, iat=1700000000),
            _raw("new", rating=100, iat=1790000000, nickname="ianrbuck"),
            _raw("stars-only", rating=75, opinion="", iat=1750000000),
            # An action on another review, not one of this place's.
            {"signature": "act", "payload": {"iat": 1, "sub": "urn:maresi:x", "action": "delete"}},
            _raw("bad", rating=300, opinion=""),
        ]}).encode()

    ok, got = map_reviews.reviews("City Basement Books", "-37.81853", "144.96319", "node/1", get=get, now=1000.0)
    assert ok and got["found"] and got["count"] == 3 and got["rated"] == 3
    assert got["average"] == 4.0  # 3, 5 and 4 stars
    assert [r["link"].rsplit("=", 1)[1] for r in got["reviews"]] == ["new", "stars-only", "old"]
    assert got["reviews"][0] == {"date": "2026-09-21", "link": "https://mangrove.reviews/list?signature=new",
                                 "stars": 5.0, "rating": 100, "opinion": "Big selection.", "author": "ianrbuck"}
    assert "opinion" not in got["reviews"][1]
    assert got["credit"] == "Mangrove" and got["license"] == "CC BY 4.0" and got["signed_up"] is False
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(asked[0]).query)
    assert asked[0].startswith("https://api.mangrove.reviews/reviews?")
    assert q["sub"] == ["geo:-37.81853,144.96319?q=City%20Basement%20Books&u=100"]
    # Kept a day: the second card asks nobody.
    map_reviews.reviews("City Basement Books", "-37.81853", "144.96319", get=get, now=1000.0 + 3600)
    assert len(asked) == 1
    map_reviews.reviews("City Basement Books", "-37.81853", "144.96319", get=get, now=1000.0 + 90000)
    assert len(asked) == 2


def test_none_yet_failure_and_bad_place():
    ok, got = map_reviews.reviews("Corner Cafe", 1, 2, get=lambda u: (200, b'{"reviews": []}'))
    assert ok and got["found"] is False and got["count"] == 0 and got["average"] is None
    assert got["link"].startswith("https://mangrove.reviews/search?sub=geo%3A1%2C2%3Fq%3DCorner")
    ok, got = map_reviews.reviews("Other Cafe", 1, 2, get=lambda u: (503, b"down"))
    assert ok and got == {"found": False, "reason": "failed", "credit": "Mangrove", "link": got["link"]}
    assert map_reviews.reviews("", 1, 2)[1]["code"] == "bad_place"
    assert map_reviews.reviews("X", 95, 2)[1]["code"] == "bad_place"


def test_posting_signs_with_the_accounts_own_key_made_once(tmp_path):
    keys = tmp_path / "keys.json"
    sent = []

    def send(url, body):
        sent.append((url, body.decode()))
        return 200, b"true"

    review = {"name": "City  Basement Books", "lat": -37.8185324, "lon": 144.9631942, "osm_id": "node/7",
              "stars": 4, "opinion": "Great old shop.", "confirm": True}
    ok, got = map_reviews.post("acct-1", review, send=send, keys=keys, now=1795000000)
    assert ok and got["link"] == f"https://mangrove.reviews/list?signature={got['signature']}"
    assert stat.S_IMODE(keys.stat().st_mode) == 0o600
    url, jwt = sent[0]
    assert url == "https://api.mangrove.reviews/submit"
    h, p, s = jwt.split(".")
    header, payload = json.loads(_b(h)), json.loads(_b(p))
    assert payload == {"sub": SUB, "rating": 75, "iat": 1795000000, "opinion": "Great old shop.",
                       "metadata": {"client_id": "https://sasonica.com", "is_personal_experience": True,
                                    "osm_id": "node/7"}}
    assert header["alg"] == "ES256" and header["typ"] == "JWT" and json.loads(header["jwk"])["crv"] == "P-256"
    # The signature verifies with the public key in the header, as Mangrove checks it.
    pem = header["kid"].replace("KEY-----", "KEY-----\n", 1).replace("-----END", "\n-----END")
    raw = _b(s)
    serialization.load_pem_public_key(pem.encode()).verify(
        encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")),
        f"{h}.{p}".encode(), ec.ECDSA(hashes.SHA256()))
    # The private key never leaves: nothing sent carries it.
    private = json.loads(keys.read_text())["acct-1"]["private"]
    assert "PRIVATE" in private and private.split("\n")[1] not in jwt
    # The same key next time; another account gets its own.
    map_reviews.post("acct-1", {**review, "stars": 5}, send=send, keys=keys)
    map_reviews.post("acct-2", review, send=send, keys=keys)
    kids = [json.loads(_b(j.split(".")[0]))["kid"] for _, j in sent]
    assert kids[0] == kids[1] != kids[2]
    # Its own reviews are marked when read back.
    mine = {"signature": "m", "kid": kids[0], "payload": {"iat": 1, "sub": SUB, "rating": 100}}
    got = map_reviews.reviews("City Basement Books", -37.8185, 144.9632, account="acct-1", keys=keys,
                              get=lambda u: (200, json.dumps({"reviews": [mine, _raw("x")]}).encode()))[1]
    assert got["signed_up"] and [r.get("mine") for r in got["reviews"]] == [None, True]


def test_posting_refused_unless_confirmed_and_well_formed(tmp_path):
    keys = tmp_path / "keys.json"

    def send(url, body):
        raise AssertionError("sent")

    base = {"name": "Cafe", "lat": 1, "lon": 2, "stars": 3, "confirm": True}
    for change, code in (({"confirm": None}, "not_confirmed"), ({"confirm": "yes"}, "not_confirmed"),
                         ({"stars": 0}, "bad_stars"), ({"stars": 6}, "bad_stars"), ({"stars": 3.5}, "bad_stars"),
                         ({"stars": True}, "bad_stars"), ({"name": ""}, "bad_place"), ({"lat": "x"}, "bad_place"),
                         ({"opinion": "x" * 1001}, "too_long")):
        ok, got = map_reviews.post("a", {**base, **change}, send=send, keys=keys)
        assert not ok and got["code"] == code, change
    assert not keys.exists()  # no key made for a review never sent
    ok, got = map_reviews.post("a", base, send=lambda u, b: (400, b"iat too far"), keys=keys)
    assert not ok and got["status"] == 502 and got["code"] == "refused" and "iat too far" in got["error"]


def test_routes_gate_and_answer(server, monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setattr(map_reviews, "_get", lambda url: (200, json.dumps({"reviews": [_raw("r1")]}).encode()))
    sent = []
    monkeypatch.setattr(map_reviews, "_post", lambda url, body: sent.append(body) or (200, b"true"))
    pixel, _ = _device("Pixel 8a")
    res, obj = call(server, "GET", "/map/reviews?name=City+Basement+Books&lat=-37.8185&lon=144.9632", headers=pixel)
    assert res.status == 200 and obj["ok"] and obj["count"] == 1 and obj["average"] == 4.0
    res, obj = call(server, "POST", "/map/reviews", {"name": "City Basement Books", "lat": -37.8185, "lon": 144.9632,
                                                     "stars": 5}, headers=pixel)
    assert res.status == 400 and obj["code"] == "not_confirmed" and not sent
    res, obj = call(server, "POST", "/map/reviews", {"name": "City Basement Books", "lat": -37.8185, "lon": 144.9632,
                                                     "stars": 5, "confirm": True}, headers=pixel)
    assert res.status == 200 and obj["ok"] and len(sent) == 1
    assert stat.S_IMODE((tmp_path / "mangrove-keys.json").stat().st_mode) == 0o600
    hdrs, dev_id = _device("Visitor tablet")
    assert devices.set_role(dev_id, "guest")
    assert call(server, "GET", "/map/reviews?name=X&lat=1&lon=2", headers=hdrs)[0].status == 403
    assert call(server, "POST", "/map/reviews", {"name": "X", "lat": 1, "lon": 2, "stars": 5, "confirm": True},
                headers=hdrs)[0].status == 403
    assert call(server, "GET", "/map/reviews?name=X&lat=1&lon=2")[0].status == 401
