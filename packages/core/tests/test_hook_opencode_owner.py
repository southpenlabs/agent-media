"""The opencode hook stays quiet for a chat another server started here
(this machine its session worker): that server speaks it (18 Nov 2026)."""

import json

from agent_media_core.intake import hook_opencode


def test_owned_elsewhere_reads_sessiond_record(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    d = tmp_path / "agent-media" / "sessiond"
    d.mkdir(parents=True)
    (d / "ses_a.json").write_text(json.dumps({"session": "ses_a", "owner_server": "S2"}))
    (d / "ses_b.json").write_text(json.dumps({"session": "ses_b"}))
    assert hook_opencode._owned_elsewhere("ses_a")
    assert not hook_opencode._owned_elsewhere("ses_b")
    assert not hook_opencode._owned_elsewhere("ses_none")
    assert hook_opencode.main(["--session", "ses_a"]) == 0
