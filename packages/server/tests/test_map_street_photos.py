"""GET /map/street_photos and /map/street_photos/along (map_street_photos.py,
§6.50): Panoramax and Mapillary photos near a point and at a route's turns.
Every outside service is faked (the conftest refuses the real ones)."""
from __future__ import annotations

import json
import urllib.parse

from agent_media_server import devices, map_street_photos as sp

from test_contract import call, server, shelf, signed_in, typed  # noqa: F401, F811
from test_mic import _device

# Flinders Street Station's corner; photos a few metres apart.
HERE = (-37.8183, 144.9671)


def _pnx(pid, lat, lon, az=None, fov=70, lic="CC-BY-SA-4.0", status="ready", who=("mapcomplete", "legallyahc")):
    props = {"datetime": "2026-06-13T04:15:22+00:00", "license": lic, "geovisio:status": status,
             "pers:interior_orientation": {"field_of_view": fov}}
    if az is not None:
        props["view:azimuth"] = az
    return {"id": pid, "geometry": {"type": "Point", "coordinates": [lon, lat]}, "properties": props,
            "providers": [{"name": n, "roles": ["producer"]} for n in who],
            "links": [{"rel": "license", "href": "https://spdx.org/licenses/" + lic + ".html"}],
            "assets": {"thumb": {"href": f"https://pnx.example/{pid}/thumb.jpg"}, "sd": {"href": f"https://pnx.example/{pid}/sd.jpg"}}}


# North of the point by about 20 m looking south (at it), 40 m looking north (away),
# a 360° photo 60 m away, one too far, one not ready, one with an http thumb.
PNX = [
    _pnx("near-away", HERE[0] + 0.00018, HERE[1], az=0),
    _pnx("near-at", HERE[0] + 0.00036, HERE[1], az=180),
    _pnx("pano", HERE[0] - 0.00054, HERE[1], fov=360, lic="etalab-2.0"),
    _pnx("far", HERE[0] + 0.01, HERE[1], az=180),
    _pnx("hidden", HERE[0], HERE[1], status="waiting-for-process"),
    {**_pnx("plain", HERE[0], HERE[1]), "assets": {"thumb": {"href": "http://pnx.example/x.jpg"}}},
]
MLY = [{"id": "123456", "geometry": {"type": "Point", "coordinates": [HERE[1], HERE[0] - 0.0001]},
        "thumb_256_url": "https://scontent.example/t256.jpg", "thumb_1024_url": "https://scontent.example/t1024.jpg",
        "captured_at": 1717200000000, "compass_angle": 10, "computed_compass_angle": 350.6,
        "creator": {"username": "melb_walker", "id": "9"}, "is_pano": False}]


def _fake(asked: list, pnx=PNX, mly=MLY, status=200):
    def get(url, headers):
        asked.append((url, headers))
        u = urllib.parse.urlsplit(url)
        if status != 200:
            return status, b""
        if u.netloc == "api.panoramax.xyz":
            assert u.path == "/api/search" and "bbox" in urllib.parse.parse_qs(u.query)
            return 200, json.dumps({"features": pnx, "links": []}).encode()
        if u.netloc == "graph.mapillary.com":
            assert headers.get("Authorization", "").startswith("OAuth ")
            return 200, json.dumps({"data": mly}).encode()
        raise AssertionError(url)
    return get


def test_panoramax_photos_facing_the_place_first_then_nearest_credited_and_kept():
    asked: list = []
    ok, got = sp.street_photos("-37.8183,144.9671", get=_fake(asked), now=0.0)
    assert ok and got["sources"] == {"panoramax": "ok", "mapillary": "off"}
    ids = [p["id"] for p in got["photos"]]
    # Looking at the place (and the 360°) before the nearer one looking away; nothing past 80 m, not ready, or not https.
    assert ids == ["near-at", "pano", "near-away"]
    at = got["photos"][0]
    assert at == {"source": "panoramax", "id": "near-at", "thumb": "https://pnx.example/near-at/thumb.jpg",
                  "image": "https://pnx.example/near-at/sd.jpg", "link": "https://api.panoramax.xyz/#focus=pic&pic=near-at",
                  "license": "CC BY-SA 4.0", "license_url": "https://creativecommons.org/licenses/by-sa/4.0/",
                  "credit": "Panoramax", "author": "legallyahc", "heading": 180, "pano": False, "date": "2026-06-13",
                  "lat": HERE[0] + 0.00036, "lon": HERE[1], "distance_m": 40, "facing": True}
    assert got["photos"][1]["license"] == "Licence Ouverte 2.0" and got["photos"][1]["pano"] is True
    assert got["photos"][2]["facing"] is False
    # Kept: a second card asks nobody; a heading reorders without asking.
    n = len(asked)
    ok, again = sp.street_photos("-37.8183,144.9671", heading="0", get=_fake(asked), now=3600.0)
    assert len(asked) == n and [p["id"] for p in again["photos"]] == ["near-away", "pano", "near-at"]
    ok, few = sp.street_photos("-37.8183,144.9671", limit="1", get=_fake(asked), now=3600.0)
    assert [p["id"] for p in few["photos"]] == ["near-at"]


def test_mapillary_only_with_a_token(monkeypatch):
    monkeypatch.setenv("MEDIA_MAPILLARY_TOKEN", "MLY|1|abc")
    asked: list = []
    ok, got = sp.street_photos("-37.8183,144.9671", get=_fake(asked), now=0.0)
    assert got["sources"] == {"panoramax": "ok", "mapillary": "ok"}
    m = next(p for p in got["photos"] if p["source"] == "mapillary")
    assert m["link"] == "https://www.mapillary.com/app/?pKey=123456&focus=photo"
    assert m["thumb"].endswith("t256.jpg") and m["image"].endswith("t1024.jpg")
    assert m["author"] == "melb_walker" and m["heading"] == 351 and m["date"] == "2024-06-01" and m["credit"] == "Mapillary"
    assert ("Authorization", "OAuth MLY|1|abc") in asked[-1][1].items()
    assert "MLY" not in asked[-1][0]  # the token is a header, never in the address


def test_a_source_down_says_so_and_is_asked_again():
    ok, got = sp.street_photos("-37.8183,144.9671", get=_fake([], status=503), now=0.0)
    assert ok and got == {"photos": [], "sources": {"panoramax": "failed", "mapillary": "off"}}
    asked: list = []
    ok, got = sp.street_photos("-37.8183,144.9671", get=_fake(asked), now=1.0)
    assert asked and got["photos"]
    # An empty answer is kept for MISS_S only.
    sp.forget()
    asked = []
    sp.street_photos("-37.0,145.0", get=_fake(asked, pnx=[]), now=0.0)
    sp.street_photos("-37.0,145.0", get=_fake(asked, pnx=[]), now=sp.MISS_S - 1)
    assert len(asked) == 1
    sp.street_photos("-37.0,145.0", get=_fake(asked, pnx=[]), now=sp.MISS_S + 1)
    assert len(asked) == 2


def test_along_a_route_one_photo_per_turn_looking_its_way_panoramax_only(monkeypatch):
    monkeypatch.setenv("MEDIA_MAPILLARY_TOKEN", "MLY|1|abc")
    asked: list = []
    pts = f"{HERE[0]},{HERE[1]},0;{HERE[0]},{HERE[1]},90;-37.9,145.1"
    ok, got = sp.along(pts, get=_fake(asked), now=0.0)
    assert ok
    first, second, third = got["points"]
    assert first["id"] == "near-away"  # heading north: the photo looking north
    assert second is None  # nothing looks east
    assert third is None  # no photo there in 40 m
    assert all(urllib.parse.urlsplit(u).netloc == "api.panoramax.xyz" for u, _ in asked)


def test_bad_asks():
    assert sp.street_photos("")[1]["code"] == "bad_point"
    assert sp.street_photos("91,0")[1]["code"] == "bad_point"
    assert sp.street_photos("-37,145", radius_m="9999")[1]["code"] == "bad_ask"
    assert sp.street_photos("-37,145", heading="400")[1]["code"] == "bad_ask"
    assert sp.along("")[1]["code"] == "bad_points"
    assert sp.along(";".join(["-37,145"] * (sp.ALONG_MAX + 1)))[1]["code"] == "bad_points"
    assert sp.along("-37,145,x")[1]["code"] == "bad_points"


def test_routes_gated_not_a_guest(server, monkeypatch):  # noqa: F811
    monkeypatch.setattr(sp, "_get", _fake([]))
    pixel, _ = _device("Pixel 8a")
    res, obj = call(server, "GET", "/map/street_photos?near=-37.8183,144.9671", headers=pixel)
    assert res.status == 200 and obj["ok"] and obj["photos"][0]["id"] == "near-at"
    res, obj = call(server, "GET", "/map/street_photos/along?points=-37.8183,144.9671,0", headers=pixel)
    assert res.status == 200 and obj["points"][0]["id"] == "near-away"
    assert call(server, "GET", "/map/street_photos", headers=pixel)[0].status == 400
    hdrs, dev_id = _device("Visitor tablet")
    assert devices.set_role(dev_id, "guest")
    assert call(server, "GET", "/map/street_photos?near=-37,145", headers=hdrs)[0].status == 403
    assert call(server, "GET", "/map/street_photos?near=-37,145")[0].status == 401


# --- through sasonica.com/street (deploy/street) --------------------------------

def _unb64(s: str) -> bytes:
    import base64

    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _central(asked: list, status=200, mly=MLY):
    """A stand-in for the Worker: it checks the signature as the Worker does."""
    from agent_media_core._ed25519 import verify

    from agent_media_server import tunnel

    def get(url, headers):
        asked.append((url, headers))
        u = urllib.parse.urlsplit(url)
        if u.netloc == "api.panoramax.xyz":
            return 200, json.dumps({"features": [], "links": []}).encode()
        assert u.netloc == "street.example" and u.path == "/street/v1/near", url
        q = urllib.parse.parse_qs(u.query)
        pub = _unb64(q["pub"][0])
        assert verify(pub, f"{u.path}?{u.query}".encode(), _unb64(headers["X-Sasonica-Signature"]))
        assert tunnel.install_id_of(pub) == tunnel.identity()[2]
        assert q["lat"] == ["-37.81830"] and q["radius_m"] == ["80"] and "Authorization" not in headers
        if status != 200:
            return status, json.dumps({"error": "not_configured" if status == 503 else "not_allowed"}).encode()
        return 200, json.dumps({"data": mly}).encode()
    return get


def _on_central(monkeypatch):
    monkeypatch.setenv("MEDIA_MAPILLARY_CENTRAL", "1")
    monkeypatch.setenv("MEDIA_MAPILLARY_CENTRAL_URL", "https://street.example/street/v1")


def test_without_a_token_mapillary_comes_signed_through_sasonica_com(monkeypatch):
    _on_central(monkeypatch)
    asked: list = []
    ok, got = sp.street_photos("-37.8183,144.9671", get=_central(asked), now=0.0)
    assert got["sources"] == {"panoramax": "ok", "mapillary": "ok"}
    m = got["photos"][0]
    assert m["source"] == "mapillary" and m["author"] == "melb_walker" and m["credit"] == "Mapillary"
    # Kept as a direct answer is: a second card asks nobody.
    n = len(asked)
    sp.street_photos("-37.8183,144.9671", get=_central(asked), now=60.0)
    assert len(asked) == n


def test_an_owners_token_wins_over_central(monkeypatch):
    _on_central(monkeypatch)
    monkeypatch.setenv("MEDIA_MAPILLARY_TOKEN", "MLY|1|abc")
    asked: list = []
    ok, got = sp.street_photos("-37.8183,144.9671", get=_fake(asked), now=0.0)
    assert got["sources"]["mapillary"] == "ok"
    assert not any(urllib.parse.urlsplit(u).netloc == "street.example" for u, _ in asked)


def test_central_refusing_or_not_configured_reads_as_off_and_waits(monkeypatch):
    _on_central(monkeypatch)
    for status in (503, 403):
        sp.forget()
        asked: list = []
        ok, got = sp.street_photos("-37.8183,144.9671", get=_central(asked, status=status), now=0.0)
        assert ok and got["sources"] == {"panoramax": "ok", "mapillary": "off"}
        n = len(asked)
        # Not asked again for a while (nor is the refusal kept on disk as an answer).
        sp.street_photos("-37.9,145.0", get=_central(asked, status=status), now=1.0)
        assert not any(urllib.parse.urlsplit(u).netloc == "street.example" for u, _ in asked[n:])
    sp.forget()
    ok, got = sp.street_photos("-37.8183,144.9671", get=_central([], status=502), now=0.0)
    assert got["sources"]["mapillary"] == "failed"


def test_central_can_be_turned_off_and_never_serves_a_route(monkeypatch):
    _on_central(monkeypatch)
    asked: list = []
    sp.along(f"{HERE[0]},{HERE[1]},0", get=_central(asked), now=0.0)
    assert asked and all(urllib.parse.urlsplit(u).netloc == "api.panoramax.xyz" for u, _ in asked)
    monkeypatch.setenv("MEDIA_MAPILLARY_CENTRAL", "0")
    ok, got = sp.street_photos("-37.8183,144.9671", get=_central(asked), now=0.0)
    assert got["sources"]["mapillary"] == "off"
    assert sp.central_base() == ""
