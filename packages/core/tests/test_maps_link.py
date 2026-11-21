"""maps_link: shared map links read into places, no network (expand and the geocoder stubbed)."""

import pytest

from agent_media_core import maps_link as m

ROUTE = ("https://www.google.com/maps/dir/-38.3698927,144.7819936/216-220+Dundas+St/"
         "data=!4m12!4m11!1m1!4e1!1m5!1m4!1s0x6ad5b56de0480383:0xf83ea62190e65d9b!8m2"
         "!3d-38.390484!4d144.81688699999998!2m1!11b1!3e0?utm_source=mstt_0")


def no_net(monkeypatch):
    monkeypatch.setattr(m, "expand", lambda url, hops=5: ROUTE if "goo.gl" in url else url)


def test_google_route_from_here_with_blob_points(monkeypatch):
    no_net(monkeypatch)
    info = m.read("Shared route\nhttps://maps.app.goo.gl/3DaX9Qjkcwwo91Tj8", geocode=lambda w, n: [])
    assert info["kind"] == "route" and info["mode"] == "drive"
    start, end = info["points"]
    assert start["here"] and (start["lat"], start["lon"]) == (-38.369893, 144.781994)
    assert end == {"name": "216-220 Dundas St", "lat": -38.390484, "lon": 144.816887}
    assert m.marker(info) == "[[route: Start @ -38.369893,144.781994 | 216-220 Dundas St @ -38.390484,144.816887]]"


def test_place_takes_the_blob_point_over_the_view():
    info = m.parse("https://www.google.com/maps/place/Flinders+Street+Station/@-37.8,144.9,17z/"
                   "data=!3m1!4b1!4m6!3m5!1s0x0:0x0!8m2!3d-37.8182711!4d144.9670618")
    assert info == {"kind": "place", "points": [{"name": "Flinders Street Station", "lat": -37.8182711, "lon": 144.9670618}]}


def test_named_stops_are_looked_up_near_the_placed_ones(monkeypatch):
    no_net(monkeypatch)
    asked = []

    def geocode(words, near):
        asked.append((words, near))
        return [{"lat": -37.807, "lon": 144.956, "address": "Queen St, Melbourne"}]

    info = m.read("https://www.google.com/maps/dir/?api=1&origin=-37.8,144.9"
                  "&destination=Queen+Victoria+Market&travelmode=walking", geocode=geocode)
    assert info["mode"] == "walk"
    assert asked == [("Queen Victoria Market", (-37.8, 144.9))]
    assert info["points"][1]["address"] == "Queen St, Melbourne"


def test_unfound_stop_is_kept_by_name_and_all_unfound_is_an_error(monkeypatch):
    no_net(monkeypatch)
    with pytest.raises(m.LinkError):
        m.read("https://www.google.com/maps/search/nowhere+at+all", geocode=lambda w, n: [])


@pytest.mark.parametrize("url,want", [
    ("geo:0,0?q=-37.81,144.96(Home)", {"name": "Home", "lat": -37.81, "lon": 144.96}),
    ("geo:-37.81,144.96", {"name": "", "lat": -37.81, "lon": 144.96}),
    ("https://maps.apple.com/?ll=-37.81,144.96&q=Cafe", {"name": "Cafe", "lat": -37.81, "lon": 144.96}),
    ("https://www.openstreetmap.org/?mlat=-37.81&mlon=144.96#map=17/-37.81/144.96", {"name": "", "lat": -37.81, "lon": 144.96}),
    ("https://www.google.com/maps/@-37.81,144.96,15z", {"name": "", "lat": -37.81, "lon": 144.96}),
])
def test_other_places(url, want):
    assert m.parse(url)["points"] == [want]


def test_find_url_skips_other_links():
    assert m.find_url("see https://example.com and https://maps.app.goo.gl/abc.") == "https://maps.app.goo.gl/abc"
    assert m.find_url("https://goo.gl/xyz") is None
    assert not m.is_map_url("https://www.google.com/search?q=x")


def test_expand_refuses_a_redirect_off_google(monkeypatch):
    class Res:
        headers = {"Location": "https://evil.example/x"}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class Opener:
        def open(self, req, timeout=0):
            return Res()

    monkeypatch.setattr(m.urllib.request, "build_opener", lambda *a: Opener())
    with pytest.raises(m.LinkError):
        m.expand("https://maps.app.goo.gl/abc")
