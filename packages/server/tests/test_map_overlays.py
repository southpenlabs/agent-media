"""Pictures laid over the Map (map_overlays.py): listed as image layers, the picture as a data: URL."""
from __future__ import annotations

import json

from test_contract import call, server, shelf, signed_in, typed  # noqa: F401
from test_mic import _device

CORNERS = [[144.8382, -38.406265], [144.844385, -38.405693], [144.8434, -38.408883], [144.837215, -38.409455]]


def _put(tmp_path, oid="phs", meta=None, picture=b"\xff\xd8\xffjpeg"):
    d = tmp_path / "agent-media" / "map-overlays"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{oid}.jpg").write_bytes(picture)
    (d / f"{oid}.json").write_text(json.dumps(meta or {"name": "Peninsula Hot Springs", "image": f"{oid}.jpg",
                                                       "coordinates": CORNERS, "credit": "Peninsula Hot Springs"}))


def test_an_overlay_is_an_image_layer_and_its_picture_a_data_url(server, monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    _put(tmp_path)
    _put(tmp_path, "broken", {"name": "x", "image": "../../etc/passwd", "coordinates": CORNERS})
    pixel, _ = _device("Pixel 8a")
    lays = call(server, "GET", "/map/layers", headers=pixel)[1]["layers"]
    (lay,) = [x for x in lays if x["id"].startswith("overlay:")]
    assert lay == {"id": "overlay:phs", "name": "Peninsula Hot Springs", "kind": "image", "default_on": False,
                   "image": {"coordinates": CORNERS, "opacity": 0.85, "credit": "Peninsula Hot Springs"}}
    assert call(server, "GET", "/map/layer?id=overlay:phs", headers=pixel)[1]["features"] == []
    res, obj = call(server, "GET", "/map/overlay?id=phs", headers=pixel)
    assert res.status == 200 and obj["data_url"] == "data:image/jpeg;base64,/9j/anBlZw=="
    assert call(server, "GET", "/map/overlay?id=broken", headers=pixel)[0].status == 404
    assert call(server, "GET", "/map/overlay?id=../x", headers=pixel)[0].status == 404
    assert call(server, "GET", "/map/overlay?id=phs")[0].status == 401
