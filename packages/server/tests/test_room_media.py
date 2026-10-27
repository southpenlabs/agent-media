"""A Matrix room's photos, videos, voice notes and files (§6.29, matrix.py
`_media_part`, room_media.py): media parts on the messages, and
`GET /rooms/<thread>/media/<event>` fetching them from the homeserver only
when asked. A canned homeserver and a fake web; no network."""

import stat
import urllib.error

import pytest

from agent_media_core import matrix as core_matrix
from agent_media_server import devices, matrix, room_media
from test_contract import server, typed  # noqa: F401
from test_matrix_avatars import HS, ME, ROOM, ZED, Homeserver, Web, _image, isolated  # noqa: F401


def _media(i, msgtype, url, **content):
    return {"type": "m.room.message", "event_id": f"$m{i}", "sender": ZED,
            "origin_server_ts": 1_790_000_000_000 + i * 1000,
            "content": {"msgtype": msgtype, "url": url, **content}}


HISTORY = [
    _media(1, "m.image", "mxc://ryer.org/photo", body="photo.jpg",
           info={"mimetype": "image/jpeg", "size": 1234, "w": 800, "h": 600}),
    _media(2, "m.video", "mxc://whatsapp.ryer.org/clip", body="look at this", filename="clip.mp4",
           info={"mimetype": "video/mp4", "size": 2_000_000, "duration": 25834,
                 "thumbnail_url": "mxc://ryer.org/poster"}),
    _media(3, "m.audio", "mxc://ryer.org/voice", body="voice.ogg",
           info={"mimetype": "audio/ogg", "duration": 5000},
           **{"org.matrix.msc3245.voice": {}}),
    _media(4, "m.file", "mxc://ryer.org/doc", body="SPA COMMUNICATIONS.docx",
           info={"mimetype": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}),
    # An encrypted file has no url: still the words, never a broken card.
    {"type": "m.room.message", "event_id": "$m5", "sender": ZED, "origin_server_ts": 1,
     "content": {"msgtype": "m.video", "body": "x.mp4", "file": {"url": "mxc://x/y"}}},
]


def _room():
    cfg = core_matrix.Config(homeserver=HS, token="t", rooms={ROOM})
    r = matrix.Room(ROOM, cfg, ME, get=Homeserver(HISTORY))
    matrix._ROOMS[r.thread] = r
    r.load()
    return r


def _by_id(r):
    return {m["id"]: m for m in r.page(30)[0]}


def test_media_messages_are_media_parts():
    r = _room()
    msgs = _by_id(r)
    (img,) = msgs["$m1"]["parts"]
    assert img == {"type": "media", "kind": "image", "name": "photo.jpg",
                   "url": f"/rooms/{r.thread}/media/%24m1",
                   "thumb": f"/rooms/{r.thread}/media/%24m1?thumb=1",
                   "text": "sent a photo", "mimetype": "image/jpeg", "size": 1234,
                   "w": 800, "h": 600}
    vid, caption = msgs["$m2"]["parts"]
    assert vid["kind"] == "video" and vid["name"] == "clip.mp4" and vid["duration"] == 25834
    assert vid["thumb"].endswith("?thumb=1")
    assert caption == {"type": "text", "text": "look at this"}
    (voice,) = msgs["$m3"]["parts"]
    assert voice["kind"] == "voice" and "thumb" not in voice and voice["text"] == "sent a voice note"
    (doc,) = msgs["$m4"]["parts"]
    assert doc["kind"] == "file" and doc["name"] == "SPA COMMUNICATIONS.docx"
    assert msgs["$m5"]["parts"] == [{"type": "text", "text": "_sent a video_"}]


class Downloads:
    def __init__(self):
        self.files: dict[str, bytes | Exception] = {}
        self.got: list[str] = []

    def __call__(self, mxc, dest):
        self.got.append(mxc)
        v = self.files.get(mxc, OSError("404"))
        if isinstance(v, Exception):
            raise v
        dest.write_bytes(v)
        dest.chmod(0o600)       # as the real one writes it


@pytest.fixture
def dl(monkeypatch):
    d = Downloads()
    monkeypatch.setattr(room_media, "_download", d)
    return d


@pytest.fixture
def web(monkeypatch):
    from agent_media_server import contacts_photos

    w = Web()
    monkeypatch.setattr(contacts_photos, "_http", w)
    return w


def _get(addr, path, headers=None):
    import http.client

    conn = http.client.HTTPConnection(*addr, timeout=10)
    conn.request("GET", path, headers=headers or {})
    res = conn.getresponse()
    body = res.read()
    conn.close()
    return res, body


def test_the_route_fetches_once_when_asked(server, web, dl):
    import json

    r = _room()
    code, _ = devices.mint_code("desk")
    tok = devices.redeem(code, "desk")["token"]
    A = {"Authorization": f"Bearer {tok}"}
    base = f"/rooms/{r.thread}/media"
    res, _ = _get(server, f"{base}/%24m1?thumb=1")
    assert res.status in (401, 403)
    # Nothing is fetched until it is asked for.
    assert dl.got == [] and web.got == []
    # A photo's thumbnail, through the homeserver's thumbnails; kept.
    web.pages[f"{HS}/_matrix/client/v1/media/thumbnail/ryer.org/photo"] = _image()
    res, body = _get(server, f"{base}/%24m1?thumb=1&access_token={tok}")
    assert res.status == 200 and res.getheader("Content-Type") == "image/jpeg" and body
    n = len(web.got)
    assert _get(server, f"{base}/%24m1?thumb=1", A)[0].status == 200 and len(web.got) == n
    # A video: its poster is the thumbnail; ready=1 fetches the whole, then it streams with Range.
    dl.files["mxc://whatsapp.ryer.org/clip"] = b"0123456789" * 100
    res, body = _get(server, f"{base}/%24m2?ready=1", A)
    assert res.status == 200 and json.loads(body) == {"ok": True, "size": 1000, "type": "video/mp4"}
    res, body = _get(server, f"{base}/%24m2", {**A, "Range": "bytes=10-19"})
    assert res.status == 206 and body == b"0123456789" and res.getheader("Content-Range") == "bytes 10-19/1000"
    assert dl.got == ["mxc://whatsapp.ryer.org/clip"]
    # A file that is not a picture, video or sound is a download.
    dl.files["mxc://ryer.org/doc"] = b"PK\x03\x04"
    res, _ = _get(server, f"{base}/%24m4", A)
    assert res.status == 200 and res.getheader("Content-Type") == "application/octet-stream"
    # Gone from the network: 502, and not asked again straight away.
    res, _ = _get(server, f"{base}/%24m3?ready=1", A)
    assert res.status == 502
    n = len(dl.got)
    assert _get(server, f"{base}/%24m3?ready=1", A)[0].status == 502 and len(dl.got) == n
    # Not a media message of this room: 404.
    assert _get(server, f"{base}/%24nope", A)[0].status == 404
    assert _get(server, f"/rooms/00000000-0000-0000-0000-000000000000/media/%24m1", A)[0].status == 404
    d = room_media.media_dir()
    assert stat.S_IMODE(d.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in d.iterdir())


def test_the_oldest_go_past_the_cap(monkeypatch, dl):
    monkeypatch.setattr(room_media, "KEEP_BYTES", 25)
    for i in range(3):
        dl.files[f"mxc://ryer.org/f{i}"] = b"x" * 10
        assert room_media.fetch({"mxc": f"mxc://ryer.org/f{i}"}, thumb=False) is not None
    left = [p for p in room_media.media_dir().iterdir() if not p.name.endswith(".none")]
    assert len(left) == 2


def test_download_streams_and_refuses_too_large(monkeypatch, tmp_path):
    import io

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setenv("MATRIX_OWNER_TOKEN", "owner")
    seen = []

    def urlopen(req, timeout=None):
        seen.append((req.full_url, req.get_header("Authorization")))
        return Resp(b"y" * 100)

    monkeypatch.setattr(room_media.urllib.request, "urlopen", urlopen)
    dest = tmp_path / "f"
    room_media._download("mxc://ryer.org/abc", dest)
    assert dest.read_bytes() == b"y" * 100
    assert seen[0] == (f"{HS}/_matrix/client/v1/media/download/ryer.org/abc", "Bearer owner")
    monkeypatch.setattr(room_media, "MAX_BYTES", 50)
    with pytest.raises((ValueError, OSError, urllib.error.URLError)):
        room_media._download("mxc://ryer.org/abc", tmp_path / "g")
    assert not (tmp_path / "g").exists()


def test_a_placeholder_edited_into_the_file_or_a_failure_is_shown_as_edited():
    # Going back, the edit (newer) comes before the "Waiting for attachment" it edits.
    edit_ok = {"type": "m.room.message", "event_id": "$e1", "sender": ZED, "origin_server_ts": 3,
               "content": {"msgtype": "m.image", "body": "* a.png",
                           "m.relates_to": {"rel_type": "m.replace", "event_id": "$w1"},
                           "m.new_content": {"msgtype": "m.image", "body": "a.png",
                                             "url": "mxc://ryer.org/a", "info": {"mimetype": "image/png"}}}}
    edit_bad = {"type": "m.room.message", "event_id": "$e2", "sender": ZED, "origin_server_ts": 5,
                "content": {"msgtype": "m.notice", "body": "* Failed to transfer attachment b",
                            "m.relates_to": {"rel_type": "m.replace", "event_id": "$w2"},
                            "m.new_content": {"msgtype": "m.notice", "body": "Failed to transfer attachment b"}}}
    waits = [{"type": "m.room.message", "event_id": f"$w{i}", "sender": ZED, "origin_server_ts": i,
              "content": {"msgtype": "m.notice", "body": "Waiting for attachment x"}} for i in (1, 2)]
    sticker = {"type": "m.sticker", "event_id": "$s1", "sender": ZED, "origin_server_ts": 6,
               "content": {"body": "sticker.png", "url": "mxc://ryer.org/st", "info": {"mimetype": "image/png"}}}
    cfg = core_matrix.Config(homeserver=HS, token="t", rooms={ROOM})
    r = matrix.Room(ROOM, cfg, ME, get=Homeserver([*waits, edit_ok, edit_bad, sticker]))
    matrix._ROOMS[r.thread] = r
    r.load()
    msgs = _by_id(r)
    assert msgs["$w1"]["parts"][0]["kind"] == "image" and msgs["$w1"]["edited"] is True
    assert msgs["$w2"]["parts"] == [{"type": "text", "text": "Failed to transfer attachment b"}]
    assert msgs["$s1"]["parts"][0]["kind"] == "image" and "$e1" not in msgs
    assert r.media["$w1"]["mxc"] == "mxc://ryer.org/a" and r.media["$s1"]["mxc"] == "mxc://ryer.org/st"
