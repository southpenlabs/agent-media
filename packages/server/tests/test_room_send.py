"""Photos, files and voice clips sent to a room (server-contract.md §6.33,
room_send.py): each `Shared file:` line goes as the file itself, never as a
path, then the words. The homeserver is canned."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from agent_media_server import matrix, room_send, send
from test_matrix_rooms import SAM, _ev, _room


class FakeRoom:
    def __init__(self):
        self.uploads, self.sent = [], []

    def upload(self, data, name, ctype):
        self.uploads.append((name, ctype, len(data)))
        return True, {"url": f"mxc://hs.test/{len(self.uploads)}"}

    def send_content(self, content, reply_to=""):
        self.sent.append((content, reply_to))
        return True, {"event_id": f"$m{len(self.sent)}"}

    def send(self, text, reply_to=""):
        return self.send_content({"msgtype": "m.text", "body": text}, reply_to)


@pytest.fixture
def shared(tmp_path, monkeypatch):
    root = tmp_path / "shared"
    (root / "2026-10-29").mkdir(parents=True)
    monkeypatch.setenv("MEDIA_UPLOAD_DIR", str(root))
    return root / "2026-10-29"


def test_a_photo_and_words_go_as_a_photo_then_the_words(shared):
    photo = shared / "beach.png"
    photo.write_bytes(b"\x89PNG fake")
    r = FakeRoom()
    ok, d = room_send.send(r, f"look at this\nShared file: {photo}", reply_to="$1")
    assert ok, d
    assert r.uploads == [("beach.png", "image/png", 9)]
    (pic, to1), (words, to2) = r.sent
    assert pic["msgtype"] == "m.image" and pic["url"] == "mxc://hs.test/1"
    assert pic["body"] == "beach.png" and pic["info"] == {"mimetype": "image/png", "size": 9}
    assert to1 == "$1" and to2 == ""          # the reply is the first of them
    assert words == {"msgtype": "m.text", "body": "look at this"}
    # Nothing of the path reaches the person.
    assert all(str(shared) not in json_ for json_ in map(str, r.sent))


def test_a_file_alone_and_a_path_outside_the_shared_folder(shared, tmp_path):
    doc = shared / "notes.pdf"
    doc.write_bytes(b"%PDF")
    r = FakeRoom()
    ok, _ = room_send.send(r, f"Shared file: {doc}")
    assert ok and [c["msgtype"] for c, _ in r.sent] == ["m.file"]
    secret = tmp_path / "secret.txt"
    secret.write_text("no")
    r = FakeRoom()
    ok, err = room_send.send(r, f"hi\nShared file: {secret}")
    assert not ok and err["status"] == 400 and not r.sent and not r.uploads


def test_words_alone_are_sent_as_before(shared):
    r = FakeRoom()
    ok, _ = room_send.send(r, "just words", reply_to="$9")
    assert ok and r.sent == [({"msgtype": "m.text", "body": "just words"}, "$9")]


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="no ffmpeg")
def test_a_voice_clip_goes_as_a_voice_note_in_ogg(shared):
    clip = shared / "voice-clip-20261029-1801.webm"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                    "-c:a", "libopus", str(clip)], check=True, timeout=60)
    r = FakeRoom()
    ok, d = room_send.send(r, f"Shared file: {clip}")
    assert ok, d
    (name, ctype, _), = r.uploads
    assert name == "voice-clip-20261029-1801.ogg" and ctype == "audio/ogg"
    (c, _), = r.sent
    assert c["msgtype"] == "m.audio" and "org.matrix.msc3245.voice" in c
    assert 800 <= c["info"]["duration"] <= 1200
    assert c["org.matrix.msc1767.audio"]["duration"] == c["info"]["duration"]


def test_is_voice_by_its_name():
    assert room_send.is_voice("voice-clip-1.webm", "video/webm")
    assert room_send.is_voice("voice-clip-1.ogg", "audio/ogg")
    assert not room_send.is_voice("song.ogg", "audio/ogg")
    assert not room_send.is_voice("voice-clip-1.png", "image/png")


def test_through_reply_into_a_real_room(shared, monkeypatch):
    from agent_media_server import auth

    r, _ = _room([_ev(1, SAM, "send me the photo")])
    r.load()
    monkeypatch.setattr(matrix, "_ROOMS", {r.thread: r})
    put = []
    monkeypatch.setenv("MATRIX_OWNER_TOKEN", "owner-tok")
    monkeypatch.setattr(matrix, "_put", lambda config, path, body, token, timeout=15.0:
                        put.append(body) or {"event_id": f"$s{len(put)}"})
    monkeypatch.setattr(matrix, "_post", lambda *a, **k: {})
    monkeypatch.setattr(matrix.Room, "upload", lambda self, data, name, ctype:
                        (True, {"url": "mxc://hs.test/abc"}))
    monkeypatch.setattr(auth, "gate", lambda bearer: ("david", None))
    photo = shared / "cat.jpg"
    photo.write_bytes(b"jpeg")
    ok, d = send.reply("", f"here\nShared file: {photo}", "tok", session=r.thread)
    assert ok, d
    assert [b["msgtype"] for b in put] == ["m.image", "m.text"]
    msgs = r.page(30)[0]
    assert msgs[-2]["parts"][0]["type"] == "media" and msgs[-2]["parts"][0]["kind"] == "image"
    assert msgs[-1]["parts"][0]["text"] == "here"
