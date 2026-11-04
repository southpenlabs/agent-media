"""`model` and `model_label` on /targets rows (session_model.py; David, 4 Nov
2026: "which chats are using which models")."""
from __future__ import annotations

import json
import os
import time
import uuid

import pytest

from agent_media_server import session_model

SID = "0f3a2c1e-9b7d-4e21-8c5a-1d2e3f4a5b6c"


@pytest.fixture(autouse=True)
def _fresh():
    session_model._reset_for_tests()
    yield
    session_model._reset_for_tests()


def _reply(model: str, t: float) -> str:
    return json.dumps({"type": "assistant", "uuid": str(uuid.uuid4()),
                       "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t)),
                       "message": {"role": "assistant", "model": model,
                                   "content": [{"type": "text", "text": "hi"}]}})


def _user(t: float) -> str:
    return json.dumps({"type": "user", "uuid": str(uuid.uuid4()),
                       "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t)),
                       "message": {"role": "user", "content": "hello"}})


def test_claude_row_names_its_last_reply_model_and_family(tmp_path):
    path = tmp_path / f"{SID}.jsonl"
    path.write_text("\n".join([_user(1), _reply("claude-opus-5-5", 2), _user(3),
                               _reply("claude-fable-5-1", 4)]) + "\n")
    row = {"session": SID, "harness": "claude"}
    assert session_model.of_row(row, {SID: ("claude", str(path))}) == ("claude-fable-5-1", "Fable")


def test_a_grown_transcript_is_read_again_an_unchanged_one_not(tmp_path, monkeypatch):
    path = tmp_path / f"{SID}.jsonl"
    path.write_text(_user(1) + "\n" + _reply("claude-sonnet-5-5", 2) + "\n")
    paths = {SID: ("claude", str(path))}
    assert session_model.of_row({"session": SID, "harness": "claude"}, paths)[1] == "Sonnet"
    reads = []
    real = session_model.claude_tail
    monkeypatch.setattr(session_model, "claude_tail", lambda p: (reads.append(p), real(p))[1])
    assert session_model.of_row({"session": SID, "harness": "claude"}, paths)[1] == "Sonnet"
    assert reads == [], "an unchanged file costs a stat, not a read"
    with open(path, "a") as f:
        f.write(_user(3) + "\n" + _reply("claude-fable-5-1", 4) + "\n")
    st = os.stat(path)
    os.utime(path, (st.st_atime, st.st_mtime + 5))
    assert session_model.of_row({"session": SID, "harness": "claude"}, paths) == ("claude-fable-5-1", "Fable")
    assert reads == [str(path)]


def test_no_reply_no_file_a_person_or_a_quick_chat_have_none(tmp_path):
    path = tmp_path / f"{SID}.jsonl"
    path.write_text(_user(1) + "\n")
    assert session_model.of_row({"session": SID, "harness": "claude"}, {SID: ("claude", str(path))}) == (None, None)
    assert session_model.of_row({"session": SID, "harness": "claude"}, {SID: ("claude", str(tmp_path / "gone.jsonl"))}) == (None, None)
    assert session_model.of_row({"session": SID, "harness": "matrix"}, {}) == (None, None)
    assert session_model.of_row({"session": SID, "harness": "quick"}, {}) == (None, None)
    assert session_model.of_row({"session": SID, "harness": "claude", "sealed": True}, {}) == (None, None)


def test_other_harnesses_read_through_their_own_readers(tmp_path, monkeypatch):
    from agent_media_core import native_models, pi_models

    path = tmp_path / "rollout.jsonl"
    path.write_text("{}\n")
    monkeypatch.setattr(native_models, "current", lambda s: ("gpt-5.3-codex", 1.0))
    assert session_model.of_row({"session": SID, "harness": "codex"}, {SID: ("codex", str(path))}) == ("gpt-5.3-codex", "gpt-5.3-codex")
    monkeypatch.setattr(pi_models, "current", lambda s: ("anthropic/claude-sonnet-4-5", 1.0))
    assert session_model.of_row({"session": "b" + SID[1:], "harness": "pi"}, {"b" + SID[1:]: ("pi", str(path))}) == ("anthropic/claude-sonnet-4-5", "Sonnet")


def test_labels():
    assert session_model.label_of("claude-fable-5-1") == "Fable"
    assert session_model.label_of("claude-haiku-4-5-20251001") == "Haiku"
    assert session_model.label_of("openai/gpt-5") == "gpt-5"
    assert session_model.label_of("") == ""
