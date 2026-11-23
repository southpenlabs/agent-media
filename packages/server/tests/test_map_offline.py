"""Offline maps (map_offline.py, §6.50): an area's tiles extracted from a
PMTiles archive by byte ranges, the listing, and the files handed over a
range at a time."""

from __future__ import annotations

import gzip
import http.client
import json
import zipfile

from agent_media_server import map_offline as mo

from test_contract import call, server, shelf, signed_in, typed  # noqa: F401
from test_mic import _device


def _archive(tiles: dict[tuple[int, int, int], bytes], leaf_size: int | None = None) -> bytes:
    """A PMTiles v3 archive of `tiles`, with leaf directories when `leaf_size` is given."""
    entries, data, where = [], bytearray(), {}
    for tid, body in sorted((mo.zxy_to_id(*k), v) for k, v in tiles.items()):
        if body not in where:
            where[body] = len(data)
            data += body
        entries.append((tid, where[body], len(body), 1))
    if leaf_size:
        leaves, root_entries = bytearray(), []
        for i in range(0, len(entries), leaf_size):
            leaf = gzip.compress(mo.write_directory(entries[i:i + leaf_size]))
            root_entries.append((entries[i][0], len(leaves), len(leaf), 0))
            leaves += leaf
        root = gzip.compress(mo.write_directory(root_entries))
    else:
        leaves, root = b"", gzip.compress(mo.write_directory(entries))
    meta = gzip.compress(json.dumps({"name": "test"}).encode())
    h = {"root_offset": 127, "root_length": len(root), "metadata_offset": 127 + len(root), "metadata_length": len(meta),
         "leaf_offset": 127 + len(root) + len(meta), "leaf_length": len(leaves),
         "data_offset": 127 + len(root) + len(meta) + len(leaves), "data_length": len(data),
         "addressed_tiles": len(entries), "tile_entries": len(entries), "tile_contents": len(where),
         "clustered": 1, "internal_compression": 2, "tile_compression": 2, "tile_type": 1, "min_zoom": 0,
         "max_zoom": 6, "min_lon_e7": -1800000000, "min_lat_e7": -850000000, "max_lon_e7": 1800000000,
         "max_lat_e7": 850000000, "center_zoom": 0, "center_lon_e7": 0, "center_lat_e7": 0}
    return mo.write_header(h) + root + meta + bytes(leaves) + bytes(data)


def _ranges(blob: bytes, asked: list):
    def fetch(url, first, last):
        asked.append((first, last))
        return blob[first:last + 1]
    return fetch


def test_tile_ids_round_trip_and_directories():
    assert [mo.zxy_to_id(*t) for t in ((0, 0, 0), (1, 0, 0), (1, 0, 1), (1, 1, 1), (1, 1, 0), (2, 0, 0))] == [0, 1, 2, 3, 4, 5]
    for t in ((5, 3, 17), (12, 3699, 2514), (14, 14796, 10057)):
        assert mo.id_to_zxy(mo.zxy_to_id(*t)) == t
    e = [(5, 0, 10, 1), (6, 10, 4, 3), (20, 0, 10, 1)]
    assert mo.read_directory(mo.write_directory(e)) == e


def test_an_area_extracted(tmp_path):
    # Every tile to zoom 6, each its own bytes, and the sea (a shared tile) everywhere east of 160°.
    tiles = {}
    for z in range(7):
        for x in range(1 << z):
            for y in range(1 << z):
                lon = x / (1 << z) * 360 - 180
                tiles[(z, x, y)] = b"sea" if lon > 160 else f"{z}/{x}/{y}".encode()
    blob = _archive(tiles, leaf_size=500)
    asked: list = []
    box = (140.95, -39.25, 150.05, -33.95)
    out = tmp_path / "vic.pmtiles"
    p = mo.extract("src", out, box, max_zoom=6, fetch=_ranges(blob, asked))
    want = mo.wanted_ids(box, 6)
    assert p["tiles"] == len(want) and p["max_zoom"] == 6
    h = mo.read_header(out.read_bytes())
    assert h["max_zoom"] == 6 and h["clustered"] == 1 and h["min_lon_e7"] == 1409500000
    # Victoria's tiles are there with their own bytes; a tile far away is not.
    x, y = mo._tile(145.0, -37.8, 6)
    assert mo.tile(out, 6, x, y) == f"6/{x}/{y}".encode()
    assert mo.tile(out, 0, 0, 0) == b"0/0/0"
    assert mo.tile(out, 6, 0, 0) is None
    # The leaves were read and nearby tiles asked in one range, not one each.
    assert len(asked) < 10


def test_listing_and_files_with_ranges(server, monkeypatch, tmp_path):
    d = tmp_path / "offline"
    monkeypatch.setenv("MEDIA_MAP_OFFLINE_DIR", str(d))
    pixel, _ = _device("Pixel 8a")
    assert call(server, "GET", "/map/offline", headers=pixel)[1]["areas"] == []
    d.mkdir()
    (d / "victoria.pmtiles").write_bytes(b"P" * 1000)
    with zipfile.ZipFile(d / "victoria-assets.zip", "w") as z:
        z.writestr("fonts/Noto Sans Regular/0-255.pbf", b"glyphs")
    (d / "offline.json").write_text(json.dumps({"areas": [{
        "id": "victoria", "name": "Victoria", "version": "20261121", "bounds": [140.95, -39.25, 150.05, -33.95],
        "max_zoom": 14, "files": [{"name": "victoria.pmtiles", "bytes": 1, "kind": "tiles"},
                                  {"name": "victoria-assets.zip", "bytes": 1, "kind": "assets"}]}]}))
    (area,) = call(server, "GET", "/map/offline", headers=pixel)[1]["areas"]
    assert area["id"] == "victoria" and area["files"][0]["bytes"] == 1000
    assert area["files"][0]["path"] == "/map/offline/victoria/victoria.pmtiles"
    assert area["bytes"] == 1000 + (d / "victoria-assets.zip").stat().st_size
    conn = http.client.HTTPConnection(*server, timeout=10)
    conn.request("GET", "/map/offline/victoria/victoria.pmtiles", headers={**pixel, "Range": "bytes=10-19"})
    res = conn.getresponse()
    assert res.status == 206 and res.getheader("Content-Range") == "bytes 10-19/1000" and res.read() == b"P" * 10
    # A file the listing does not name, or no credential: nothing.
    assert call(server, "GET", "/map/offline/victoria/offline.json", headers=pixel)[0].status == 404
    assert call(server, "GET", "/map/offline/victoria/victoria.pmtiles")[0].status == 401
    # A file gone from disk: the area is not offered.
    (d / "victoria-assets.zip").unlink()
    assert call(server, "GET", "/map/offline", headers=pixel)[1]["areas"] == []


def test_assets_packed(tmp_path):
    got = []

    def get(url):
        got.append(url)
        return b"x"
    size = mo.assets(tmp_path / "a.zip", get=get)
    names = zipfile.ZipFile(tmp_path / "a.zip").namelist()
    assert size > 0 and "fonts/Noto Sans Regular/0-255.pbf" in names and "sprites/dark@2x.png" in names
    assert "NOTICE.txt" in names and any("Noto%20Sans%20Medium" in u for u in got)


def test_every_state_listed_and_built_on_request(server, monkeypatch, tmp_path):
    # David, 23 Nov 2026: offline maps of other states, built when asked for.
    d = tmp_path / "offline"
    monkeypatch.setenv("MEDIA_MAP_OFFLINE_DIR", str(d))
    started = []
    monkeypatch.setattr(mo, "start_worker", lambda unit, out: started.append((unit, out)))
    pixel, _ = _device("Pixel 8a")
    got = call(server, "GET", "/map/offline", headers=pixel)[1]
    ids = [r["id"] for r in got["regions"]]
    assert ids[0] == "victoria" and len(ids) == 8 and "tasmania" in ids and "australian-capital-territory" in ids
    assert all(r["state"] == "none" and r["approx_bytes"] > 0 for r in got["regions"])
    # Asked for: queued, and one worker started however often it is asked.
    res, body = call(server, "POST", "/map/offline/build", {"region": "tasmania"}, headers=pixel)
    assert res.status == 200 and next(r for r in body["regions"] if r["id"] == "tasmania")["state"] == "queued"
    call(server, "POST", "/map/offline/build", {"region": "tasmania"}, headers=pixel)
    call(server, "POST", "/map/offline/build", {"region": "northern-territory"}, headers=pixel)
    assert len(started) == 1 and started[0][1] == d
    assert json.loads((d / "jobs.json").read_text())["queue"] == ["tasmania", "northern-territory"]
    assert call(server, "POST", "/map/offline/build", {"region": "atlantis"}, headers=pixel)[0].status == 400
    assert call(server, "POST", "/map/offline/build", {"region": "tasmania"})[0].status == 401

    # The worker builds the queue one at a time; one failing does not stop the next.
    monkeypatch.setattr(mo, "newest_build", lambda get=None: "20261123.pmtiles")
    built = []

    def builder(out_dir, region, progress, key, assets_from):
        progress(5, 10)
        if region == "northern-territory":
            raise OSError("range read failed")
        built.append((region, key, assets_from))
        (out_dir / f"{region}.pmtiles").write_bytes(b"P" * 10)
        (out_dir / f"{region}-assets.zip").write_bytes(b"Z")
        man = {"areas": [{"id": region, "name": "Tasmania", "version": "20261123", "bounds": [1, 2, 3, 4],
                          "files": [{"name": f"{region}.pmtiles", "bytes": 10, "kind": "tiles"},
                                    {"name": f"{region}-assets.zip", "bytes": 1, "kind": "assets"}]}]}
        (out_dir / "offline.json").write_text(json.dumps(man))
        return man["areas"][0]

    assert mo.work(d, builder=builder) == 1
    assert built == [("tasmania", "20261123.pmtiles", None)]
    jobs = json.loads((d / "jobs.json").read_text())
    assert jobs["queue"] == [] and "worker" not in jobs and "running" not in jobs
    regions = {r["id"]: r for r in call(server, "GET", "/map/offline", headers=pixel)[1]["regions"]}
    assert regions["tasmania"]["state"] == "ready" and regions["tasmania"]["built"] is True
    assert regions["northern-territory"]["state"] == "failed" and "range read" in regions["northern-territory"]["error"]
    # Asked again after failing: queued again, a new worker.
    call(server, "POST", "/map/offline/build", {"region": "northern-territory"}, headers=pixel)
    assert len(started) == 2


def test_a_running_worker_shows_progress_and_a_dead_one_is_replaced(tmp_path, monkeypatch):
    d = tmp_path
    started = []
    import os
    import time

    mo._write(d / "jobs.json", {"queue": [], "running": {"region": "queensland", "done": 50, "total": 200},
                                "worker": {"pid": os.getpid(), "started": time.time()}})
    # This test's own process stands in for a live worker only when its cmdline says map_offline:
    monkeypatch.setattr(mo, "_alive", lambda pid: True)
    q = next(r for r in mo.listing(d)["regions"] if r["id"] == "queensland")
    assert q["state"] == "building" and (q["done"], q["total"]) == (50, 200)
    ok, _ = mo.request("queensland", d, starter=lambda u, o: started.append(u))
    assert ok and started == [] and json.loads((d / "jobs.json").read_text())["queue"] == []
    monkeypatch.setattr(mo, "_alive", lambda pid: False)
    assert next(r for r in mo.listing(d)["regions"] if r["id"] == "queensland")["state"] == "none"
    ok, _ = mo.request("queensland", d, starter=lambda u, o: started.append(u))
    assert ok and len(started) == 1 and json.loads((d / "jobs.json").read_text())["queue"] == ["queensland"]


def test_a_build_keeps_the_other_regions(tmp_path, monkeypatch):
    tiles = {(z, x, y): f"{z}/{x}/{y}".encode() for z in range(4) for x in range(1 << z) for y in range(1 << z)}
    blob = _archive(tiles)
    monkeypatch.setattr(mo, "_http_range", lambda url, a, b: blob[a:b + 1])
    monkeypatch.setattr(mo, "assets", lambda out, get=None: out.write_bytes(b"zip") or 3)
    mo.build(out_dir=tmp_path, region="tasmania", key="20261123.pmtiles", max_zoom=3)
    mo.build(out_dir=tmp_path, region="victoria", key="20261123.pmtiles", max_zoom=3,
             assets_from=tmp_path / "tasmania-assets.zip")
    man = json.loads((tmp_path / "offline.json").read_text())
    assert [a["id"] for a in man["areas"]] == ["victoria", "tasmania"]
    assert man["areas"][1]["bounds"] == [143.77, -43.79, 148.55, -39.15]
    assert (tmp_path / "victoria-assets.zip").read_bytes() == b"zip"
    assert mo.built_regions(tmp_path) == ["victoria", "tasmania"]
