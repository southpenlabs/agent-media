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
