"""Every harness's reply in a conversation goes through Claude Code's Stop
path (`_hook_stdin.run` → `hook_claude_code._handle_stop`): its markers act,
with the harness's own voice and engine."""

import pytest

from agent_media_core.intake import _hook_stdin, hook_claude_code, toast
from agent_media_core.types import Source


@pytest.fixture
def played(tmp_path, monkeypatch):
    import os

    for var in list(os.environ):
        if var.startswith(("MEDIA_", "RELAY_", "CODEX_", "PI_", "GEMINI_", "GROK_")):
            monkeypatch.delenv(var)
    for var, sub in (("HOME", "home"), ("XDG_STATE_HOME", "state"),
                     ("XDG_CACHE_HOME", "cache"), ("XDG_CONFIG_HOME", "config")):
        monkeypatch.setenv(var, str(tmp_path / sub))
    out = []
    monkeypatch.setattr(hook_claude_code, "_play_detached", out.append)
    monkeypatch.setattr(toast, "should_hold", lambda s: False)
    return out


SID = "019a5f2e-1111-7222-8333-444455556666"


def test_a_codex_reply_gets_chips_and_keeps_its_voice(played, monkeypatch):
    monkeypatch.setenv("CODEX_TTS_VOICE", "en-GB-RyanNeural")
    monkeypatch.setenv("CODEX_TTS_ENGINE", "edge")
    assert _hook_stdin.run(Source.CODEX, "CODEX", text="Done.\n\n[[replies: Ship | Wait]]",
                           metadata={"session": SID, "turn_id": "t1", "cwd": "/w"}) == 0
    (ev,) = played
    assert ev.text == "Done." and ev.source == Source.CODEX
    assert ev.voice == "en-GB-RyanNeural" and ev.engine == "edge"
    assert ev.metadata["followup_replies"] == ["Ship", "Wait"]
    assert ev.metadata["turn_id"] == "t1" and ev.metadata["cwd"] == "/w"
    assert ev.metadata["session"] == SID


def test_no_voice_set_is_the_default_not_a_session_voice(played):
    _hook_stdin.run(Source.PI, "PI", text="Hi.", metadata={"session": SID})
    (ev,) = played
    assert ev.voice is None


def test_a_reply_with_no_conversation_is_submitted_as_before(played, monkeypatch):
    sent = []
    monkeypatch.setattr(_hook_stdin, "submit_event", sent.append)
    _hook_stdin.run(Source.PI, "PI", text="Hi there.")
    assert played == [] and sent[0].text == "Hi there."
