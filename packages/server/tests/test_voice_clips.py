"""A voice clip sent to an agent goes with its words (server-contract.md
§6.36, voice_clips.py). The speech-to-text is replaced: nothing is sent."""

from __future__ import annotations

import pytest

from agent_media_core import transcribe
from agent_media_server import voice_clips


@pytest.fixture
def shared(tmp_path, monkeypatch):
    root = tmp_path / "shared" / "2026-10-29"
    root.mkdir(parents=True)
    monkeypatch.setenv("MEDIA_UPLOAD_DIR", str(tmp_path / "shared"))
    monkeypatch.setattr(transcribe, "provider", lambda: {"name": "fake"})
    heard = []

    def fake(path, language=""):
        heard.append(path.name)
        return 'pick up "some" milk'
    monkeypatch.setattr(transcribe, "transcribe", fake)
    return root, heard


def test_a_clip_gets_its_words_and_other_files_do_not(shared):
    shared, heard = shared
    clip = shared / "voice-clip-20261029-1801.webm"
    clip.write_bytes(b"x")
    photo = shared / "beach.png"
    photo.write_bytes(b"x")
    text = f"listen\nShared file: {clip}\nShared file: {photo}"
    assert voice_clips.with_transcripts(text) == (
        f"listen\nShared file: {clip}\n(Voice clip, transcribed: \"pick up 'some' milk\")"
        f"\nShared file: {photo}")
    assert heard == ["voice-clip-20261029-1801.webm"]


def test_nothing_set_up_a_failure_or_no_clip_leaves_it(shared, monkeypatch, tmp_path):
    shared, _ = shared
    clip = shared / "voice-clip-1.ogg"
    clip.write_bytes(b"x")
    assert voice_clips.with_transcripts("just words") == "just words"
    outside = tmp_path / "voice-clip-2.ogg"
    outside.write_bytes(b"x")
    assert voice_clips.with_transcripts(f"Shared file: {outside}") == f"Shared file: {outside}"

    def fail(path, language=""):
        raise transcribe.SttError("refused (401)")
    monkeypatch.setattr(transcribe, "transcribe", fail)
    assert voice_clips.with_transcripts(f"Shared file: {clip}") == f"Shared file: {clip}"
    monkeypatch.setattr(transcribe, "provider", lambda: None)
    assert voice_clips.with_transcripts(f"Shared file: {clip}") == f"Shared file: {clip}"


def test_the_words_of_a_clip_before_it_is_sent(shared, monkeypatch, tmp_path):
    from agent_media_server import auth

    shared, heard = shared
    monkeypatch.setattr(auth, "gate", lambda bearer: ("david", None))
    clip = shared / "voice-clip-3.webm"
    clip.write_bytes(b"x")
    assert voice_clips.session_transcribe(str(clip), "tok") == (
        True, {"text": 'pick up "some" milk', "provider": "fake"})
    outside = tmp_path / "voice-clip-4.webm"
    outside.write_bytes(b"x")
    assert voice_clips.session_transcribe(str(outside), "tok")[1]["status"] == 404
    monkeypatch.setattr(transcribe, "provider", lambda: None)
    assert voice_clips.session_transcribe(str(clip), "tok")[1]["status"] == 409


# --- the recording itself, a stretch of dictation (POST /transcribe, audio body)

@pytest.fixture
def server(monkeypatch):
    import threading
    from http.server import ThreadingHTTPServer

    from agent_media_server import app, auth

    monkeypatch.setattr(auth, "gate", lambda bearer: ({"id": "u"}, {}) if bearer == "good"
                        else (None, {"error": "who?", "status": 401}))
    heard = []

    def fake(path, language=""):
        heard.append((path.suffix, path.read_bytes(), language))
        return "" if path.read_bytes() == b"OggS-quiet" else "the words of a stretch"
    monkeypatch.setattr(transcribe, "provider", lambda: {"name": "fake"})
    monkeypatch.setattr(transcribe, "transcribe", fake)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    yield srv.server_address, heard
    srv.shutdown()
    srv.server_close()


def _post(addr, path, body, ctype, bearer="good"):
    import http.client
    import json

    conn = http.client.HTTPConnection(*addr, timeout=5)
    conn.request("POST", path, body=body, headers={"Authorization": f"Bearer {bearer}",
                                                   "Content-Type": ctype})
    res = conn.getresponse()
    out = res.status, json.loads(res.read() or b"{}")
    conn.close()
    return out


def test_a_recording_sent_as_the_body_gets_its_words(server):
    addr, heard = server
    status, got = _post(addr, "/transcribe?language=en", b"\x1a\x45\xdf\xa3 opus",
                        "audio/webm;codecs=opus")
    assert status == 200
    assert got["ok"] and got["text"] == "the words of a stretch" and got["provider"] == "fake"
    assert isinstance(got["ms"], int) and got["ms"] >= 0
    assert heard == [(".webm", b"\x1a\x45\xdf\xa3 opus", "en")]
    # Silence is no words; octet-stream goes by its first bytes; a bad
    # language hint is dropped.
    status, got = _post(addr, "/transcribe?language=../x", b"OggS-quiet",
                        "application/octet-stream")
    assert status == 200 and got["text"] == ""
    assert heard[-1] == (".ogg", b"OggS-quiet", "")


def test_a_recording_refused(server, monkeypatch):
    from agent_media_server import app

    addr, heard = server
    assert _post(addr, "/transcribe", b"x", "audio/ogg", bearer="bad")[0] == 401
    assert _post(addr, "/transcribe", b"", "audio/ogg")[0] == 400
    assert app.body_limit("/transcribe") == 8 * 1024 * 1024
    import http.client
    # Refused on its Content-Length, before a byte of the body is sent.
    conn = http.client.HTTPConnection(*addr, timeout=5)
    conn.putrequest("POST", "/transcribe")
    conn.putheader("Content-Type", "audio/webm")
    conn.putheader("Content-Length", str(8 * 1024 * 1024 + 1))
    conn.endheaders()
    assert conn.getresponse().status == 413
    conn.close()

    def fail(path, language=""):
        raise transcribe.SttError("refused (401)")
    monkeypatch.setattr(transcribe, "transcribe", fail)
    status, got = _post(addr, "/transcribe", b"x", "audio/ogg")
    assert status == 502 and "refused (401)" in got["error"]
    monkeypatch.setattr(transcribe, "provider", lambda: None)
    status, got = _post(addr, "/transcribe", b"x", "audio/ogg")
    assert status == 503 and got["ok"] is False and "speech-to-text" in got["error"]
    assert heard == []


def test_the_path_form_is_unchanged_over_http(server, tmp_path, monkeypatch):
    addr, _ = server
    monkeypatch.setenv("MEDIA_UPLOAD_DIR", str(tmp_path / "shared"))
    (tmp_path / "shared").mkdir()
    clip = tmp_path / "shared" / "voice-clip-5.webm"
    clip.write_bytes(b"x")
    import json
    status, got = _post(addr, "/transcribe", json.dumps({"path": str(clip)}).encode(),
                        "application/json")
    assert status == 200 and got["text"] == "the words of a stretch"
