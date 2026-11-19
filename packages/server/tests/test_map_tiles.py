"""Aerial photos through this server (map_tiles.py): the owner's key stays here, tiles kept a day."""
from __future__ import annotations

from agent_media_server import map_tiles

from test_contract import call, server, shelf, signed_in, typed  # noqa: F401
from test_mic import _device


def test_no_key_no_layer_and_no_tiles(server, monkeypatch):
    monkeypatch.delenv("MEDIA_MAP_AERIAL_KEY", raising=False)
    pixel, _ = _device("Pixel 8a")
    lays = call(server, "GET", "/map/layers", headers=pixel)[1]["layers"]
    assert not [x for x in lays if x["id"] == "aerial"]
    assert call(server, "GET", "/map/tiles/aerial/17/118270/80703", headers=pixel)[0].status == 404


def test_tiles_asked_with_the_key_kept_and_gated(server, monkeypatch):
    monkeypatch.setenv("MEDIA_MAP_AERIAL_KEY", "k3y/+")
    map_tiles._reset_for_tests()
    asked = []

    def fetch(url):
        asked.append(url)
        return 200, b"\\xff\\xd8jpeg", "image/jpeg"
    real = map_tiles.tile
    monkeypatch.setattr(map_tiles, "tile", lambda z, x, y, **k: real(z, x, y, fetch=fetch))
    pixel, _ = _device("Pixel 8a")
    (lay,) = [x for x in call(server, "GET", "/map/layers", headers=pixel)[1]["layers"] if x["id"] == "aerial"]
    assert lay["kind"] == "tiles" and lay["default_on"] is False and lay["tiles"]["url"] == "/map/tiles/aerial/{z}/{x}/{y}"
    assert "Esri" in lay["tiles"]["credit"] and "k3y" not in str(lay)
    import http.client
    conn = http.client.HTTPConnection(*server, timeout=10)
    conn.request("GET", "/map/tiles/aerial/17/118270/80703", headers=pixel)
    res = conn.getresponse()
    assert res.status == 200 and res.getheader("Content-Type") == "image/jpeg" and res.read() == b"\\xff\\xd8jpeg"
    assert asked == ["https://ibasemaps-api.arcgis.com/arcgis/rest/services/World_Imagery/MapServer/tile/17/80703/118270?token=k3y/%2B"]
    # Kept: asked again, Esri is not.
    call(server, "GET", "/map/tiles/aerial/17/118270/80703", headers=pixel)
    assert len(asked) == 1
    assert call(server, "GET", "/map/tiles/aerial/17/118270/80703")[0].status == 401
    assert call(server, "GET", "/map/tiles/aerial/3/9/9", headers=pixel)[0].status == 404
