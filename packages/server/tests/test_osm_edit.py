"""Adding opening hours to OpenStreetMap under the owner's account
(osm_edit.py, §6.50). OpenStreetMap is faked throughout."""

from __future__ import annotations

import datetime as dt
import json
import urllib.parse

import pytest

from agent_media_server import osm_edit

NODE = b"""<?xml version="1.0" encoding="UTF-8"?>
<osm version="0.6"><node id="11583644270" visible="true" version="3" changeset="1" timestamp="2026-01-01T00:00:00Z" user="someone" uid="9" lat="-38.3611770" lon="144.7784380">
<tag k="amenity" v="restaurant"/><tag k="name" v="Blue Fin Blairgowrie"/></node></osm>"""


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setenv("MEDIA_OSM_CLIENT_ID", "cid")
    monkeypatch.setenv("MEDIA_PUBLIC_URL", "https://red2.example")
    monkeypatch.delenv("MEDIA_OSM_URL", raising=False)


class FakeOsm:
    def __init__(self):
        self.calls = []
        self.upload = None
        self.changeset = None
        self.node = NODE

    def __call__(self, method, url, body, headers):
        self.calls.append((method, url.replace("https://www.openstreetmap.org", ""), headers.get("Authorization")))
        path = url.replace("https://www.openstreetmap.org", "")
        if path == "/oauth2/token":
            form = urllib.parse.parse_qs(body.decode())
            assert form["code_verifier"][0] and form["redirect_uri"] == ["https://red2.example/map/osm/callback"]
            return 200, json.dumps({"access_token": "tok", "scope": "read_prefs write_api"}).encode()
        if path == "/api/0.6/user/details.json":
            return 200, json.dumps({"user": {"display_name": "davidj"}}).encode()
        if path == "/api/0.6/node/11583644270" and method == "GET":
            return 200, self.node
        if path == "/api/0.6/changeset/create":
            assert b'v="Sasonica"' in body
            self.changeset = body
            return 200, b"777"
        if path == "/api/0.6/node/11583644270" and method == "PUT":
            self.upload = body
            return 200, b"4"
        if path == "/api/0.6/changeset/777/close":
            return 200, b""
        return 404, b""


def signed_in(fake):
    url = osm_edit.begin(now=1000)
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    assert url.startswith("https://www.openstreetmap.org/oauth2/authorize?")
    assert q["scope"] == ["read_prefs write_api"] and q["code_challenge_method"] == ["S256"] and q["client_id"] == ["cid"]
    assert osm_edit.finish("the-code", q["state"][0], now=1010, http=fake) == {"user": "davidj"}


def test_sign_in_with_pkce_and_the_token_kept_private(tmp_path):
    fake = FakeOsm()
    assert osm_edit.status() == {"configured": True, "signed_in": False}
    signed_in(fake)
    assert osm_edit.status() == {"configured": True, "signed_in": True, "user": "davidj"}
    kept = next(tmp_path.rglob("osm-account.json"))
    assert oct(kept.stat().st_mode & 0o777) == "0o600" and json.loads(kept.read_text())["token"] == "tok"
    osm_edit.signout()
    assert osm_edit.status()["signed_in"] is False


def test_a_stale_or_strange_state_is_refused():
    osm_edit.begin(now=1000)
    with pytest.raises(ValueError):
        osm_edit.finish("c", "not-it", now=1001, http=FakeOsm())
    url = osm_edit.begin(now=1000)
    state = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["state"][0]
    with pytest.raises(ValueError):
        osm_edit.finish("c", state, now=1000 + osm_edit.PENDING_S + 1, http=FakeOsm())


def test_hours_set_in_a_changeset_of_their_own():
    fake = FakeOsm()
    signed_in(fake)
    ok, got = osm_edit.set_hours("node/11583644270", "Mo-Su 11:30-20:00", http=fake, today=dt.date(2026, 11, 22))
    assert ok, got
    assert got == {"changeset": "777", "version": "4", "url": "https://www.openstreetmap.org/changeset/777",
                   "opening_hours": "Mo-Su 11:30-20:00", "set": ["check_date:opening_hours", "opening_hours"], "kept": []}
    assert '<tag k="check_date:opening_hours" v="2026-11-22" />' in fake.upload.decode()
    assert b'k="comment" v="Add opening hours for Blue Fin Blairgowrie"' in fake.changeset and b'k="source"' not in fake.changeset
    up = fake.upload.decode()
    assert '<tag k="opening_hours" v="Mo-Su 11:30-20:00" />' in up and 'changeset="777"' in up and 'version="3"' in up
    assert 'user="someone"' not in up and '<tag k="name" v="Blue Fin Blairgowrie" />' in up
    assert [c[:2] for c in fake.calls[-4:]] == [("GET", "/api/0.6/node/11583644270"), ("PUT", "/api/0.6/changeset/create"),
                                                 ("PUT", "/api/0.6/node/11583644270"), ("PUT", "/api/0.6/changeset/777/close")]
    assert all(c[2] == "Bearer tok" for c in fake.calls[-4:])


def test_refusals_before_anything_is_sent():
    fake = FakeOsm()
    assert osm_edit.set_hours("node/1", "Mo-Su 11:30-20:00", http=fake)[1]["code"] == "not_signed_in"
    signed_in(fake)
    n = len(fake.calls)
    assert osm_edit.set_hours("shop/1", "Mo-Su 11:30-20:00", http=fake)[1]["code"] == "bad_id"
    assert osm_edit.set_hours("node/1", "whenever we feel like it", http=fake)[1]["code"] == "bad_hours"
    assert len(fake.calls) == n


def test_tags_from_the_website_added_but_never_over_what_osm_has():
    fake = FakeOsm()
    fake.node = NODE.replace(b'<tag k="amenity"', b'<tag k="phone" v="+61 3 5988 0000"/><tag k="amenity"')
    signed_in(fake)
    ok, got = osm_edit.set_hours("node/11583644270", "", http=fake, source="website", source_url="https://bluefin.example/",
                                 tags={"phone": "+61 3 5988 1111", "website:menu": "https://bluefin.example/menu",
                                       "cuisine": "fish_and_chips", "payment:cash": "yes"})
    assert ok, got
    assert got["set"] == ["cuisine", "payment:cash", "website:menu"] and got["kept"] == ["phone"] and "opening_hours" not in got
    up = fake.upload.decode()
    assert '<tag k="phone" v="+61 3 5988 0000" />' in up and '<tag k="website:menu" v="https://bluefin.example/menu" />' in up
    assert b'k="source" v="https://bluefin.example/"' in fake.changeset
    assert b"Add website:menu, cuisine and payment:cash for Blue Fin Blairgowrie" in fake.changeset
    # Named in overwrite, the phone is replaced.
    fake2 = FakeOsm()
    fake2.node = fake.node
    signed_in(fake2)
    ok, got = osm_edit.set_hours("node/11583644270", "", http=fake2, tags={"phone": "+61 3 5988 1111"}, overwrite=["phone"])
    assert ok and got["set"] == ["phone"] and '<tag k="phone" v="+61 3 5988 1111" />' in fake2.upload.decode()


def test_tags_and_sources_refused_before_anything_is_sent():
    fake = FakeOsm()
    signed_in(fake)
    n = len(fake.calls)
    assert osm_edit.set_hours("node/1", "Mo-Su 11:30-20:00", http=fake, source="google")[1]["code"] == "bad_source"
    assert osm_edit.set_hours("node/1", "", http=fake, tags={"name": "Something else"})[1]["code"] == "bad_tags"
    assert osm_edit.set_hours("node/1", "", http=fake, tags={"wheelchair": "maybe"})[1]["code"] == "bad_tags"
    assert osm_edit.set_hours("node/1", "", http=fake, tags={"opening_hours": "24/7"})[1]["code"] == "bad_tags"
    assert osm_edit.set_hours("node/1", "", http=fake)[1]["code"] == "bad_hours"
    assert len(fake.calls) == n


def test_nothing_to_change_opens_no_changeset():
    fake = FakeOsm()
    fake.node = NODE.replace(b'<tag k="amenity"', b'<tag k="phone" v="+61 3 5988 0000"/><tag k="amenity"')
    signed_in(fake)
    ok, got = osm_edit.set_hours("node/11583644270", "", http=fake, tags={"phone": "+61 3 5988 1111"})
    assert not ok and got["code"] == "nothing_to_change" and got["kept"] == ["phone"] and fake.changeset is None
