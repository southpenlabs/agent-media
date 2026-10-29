"""A room's voice notes get their words (server-contract.md §6.37,
voice_notes.py): in the background, on the part as `transcript`, kept by
event id, and searchable. The speech-to-text and the homeserver's media are
replaced: nothing is sent."""

from __future__ import annotations

import time

import pytest

from agent_media_core import transcribe
from agent_media_server import room_media, search, voice_notes
from test_matrix_rooms import SAM, _ev, _room


def _voice(i, sender=SAM):
    return _ev(i, sender, "Voice message", msgtype="m.audio", url=f"mxc://hs.test/v{i}",
               info={"mimetype": "audio/ogg", "duration": 3000},
               **{"org.matrix.msc3245.voice": {}})


@pytest.fixture
def stt(tmp_path, monkeypatch):
    monkeypatch.setattr(transcribe, "provider", lambda: {"name": "fake"})
    heard = []

    def fake(path, language=""):
        heard.append(path.name)
        return "running late, ten minutes"
    monkeypatch.setattr(transcribe, "transcribe", fake)
    clip = tmp_path / "clip.ogg"
    clip.write_bytes(b"OggS")
    monkeypatch.setattr(room_media, "fetch", lambda info, thumb: clip)
    voice_notes._ASKED.clear()
    return heard


def _wait(r, ev_id):
    for _ in range(200):
        m = r._by_id.get(ev_id)
        if m and any("transcript" in p for p in m["parts"]):
            return
        time.sleep(0.02)


def _part(r, ev_id):
    m = next(m for m in r.page(30)[0] if m["id"] == ev_id)
    return next(p for p in m["parts"] if p.get("type") == "media")


def test_a_voice_note_gets_its_words_once(stt):
    r, _ = _room([_ev(1, SAM, "hi"), _voice(2)])
    r.load()
    rev = r.rev
    # Read for the first time: not there yet, and asked for in the background.
    assert "transcript" not in _part(r, "$2")
    _wait(r, "$2")
    assert _part(r, "$2")["transcript"] == "running late, ten minutes" and r.rev > rev
    # Kept: a room read again has it at once, and nothing is asked again.
    r2, _ = _room([_ev(1, SAM, "hi"), _voice(2)])
    r2.load()
    assert _part(r2, "$2")["transcript"] == "running late, ten minutes"
    voice_notes.want(r2, "$2")
    assert stt == ["clip.ogg"]
    # Searchable: the words are the message's.
    m = next(m for m in r.page(30)[0] if m["id"] == "$2")
    assert "running late" in search.bodies(m)[0]


def test_one_arriving_is_done_in_the_background(stt):
    r, _ = _room([_ev(1, SAM, "hi")])
    r.load()
    r.on_event(_voice(3))
    _wait(r, "$3")
    assert _part(r, "$3")["transcript"] == "running late, ten minutes"


def test_a_failure_is_not_tried_again_soon(stt, monkeypatch):
    def fail(path, language=""):
        raise transcribe.SttError("refused (401)")
    monkeypatch.setattr(transcribe, "transcribe", fail)
    r, _ = _room([_voice(4)])
    r.load()
    assert voice_notes.transcribe_now(r, "$4") is None
    assert voice_notes._failed_lately("$4")
    voice_notes.want(r, "$4")
    assert voice_notes._Q.empty()


def test_nothing_set_up_nothing_asked(monkeypatch):
    voice_notes._ASKED.clear()
    r, _ = _room([_voice(5)])
    r.load()
    r.page(30)                      # MEDIA_STT=0 in every test: not enabled
    assert voice_notes._Q.empty() and not voice_notes._ASKED
