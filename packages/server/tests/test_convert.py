"""convert.py: a chat copied into another agent through txcript (faked here)."""

from __future__ import annotations

import json
import os
import stat

import pytest

from agent_media_server import convert, headless_mail, sessions

SID = "3a9d22fb-7d49-470f-8c8e-437a92b41098"


def _fake(tmp_path, body: str, rc: int = 0) -> str:
    """A txcript that records its argv and cwd, prints `body`, exits `rc`."""
    log = tmp_path / "argv.json"
    exe = tmp_path / "txcript"
    exe.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        f"json.dump({{'argv': sys.argv[1:], 'cwd': os.getcwd()}}, open({str(log)!r}, 'w'))\n"
        f"sys.stdout.write({body!r})\n"
        f"sys.exit({rc})\n")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return str(exe)


@pytest.fixture
def place(tmp_path, monkeypatch):
    work = tmp_path / "proj"
    work.mkdir()
    monkeypatch.setattr(sessions, "agent_of", lambda s: "claude")
    monkeypatch.setattr(sessions, "transcript_cwd", lambda s: str(work))
    return work


CODEX_OUT = ("claude_code → codex  /home/x/.codex/sessions/2026/11/15/"
             "rollout-2026-11-15T03-52-12-01a1dd55-8715-7933-bffa-cfacf8115e8b.jsonl\n"
             "  resume with: codex resume 01a1dd55-8715-7933-bffa-cfacf8115e8b\n")


def test_writes_a_copy_and_reads_the_new_id(tmp_path, place, monkeypatch):
    monkeypatch.setenv("MEDIA_TXCRIPT", _fake(tmp_path, CODEX_OUT))
    got = convert.convert(SID, "codex")
    assert got["session"] == "01a1dd55-8715-7933-bffa-cfacf8115e8b"
    assert got["resume"] == "codex resume 01a1dd55-8715-7933-bffa-cfacf8115e8b"
    assert got["path"].endswith(".jsonl")
    assert (got["from"], got["to"], got["loss"]) == ("claude", "codex", "")
    ran = json.loads((tmp_path / "argv.json").read_text())
    assert ran["argv"] == ["continue", SID, "--from", "claude_code", "--with", "codex",
                           "--no-resume"]
    assert os.path.realpath(ran["cwd"]) == os.path.realpath(place)


def test_opencode_has_no_path(tmp_path, place, monkeypatch):
    out = ("claude_code → opencode  imported via `opencode import`\n"
           "  resume with: opencode --session ses_2a3d07675f474eeda9bbc61233c40798\n")
    monkeypatch.setenv("MEDIA_TXCRIPT", _fake(tmp_path, out))
    got = convert.convert(SID, "opencode")
    assert got["session"] == "ses_2a3d07675f474eeda9bbc61233c40798"
    assert got["path"] == ""


def test_a_known_loss_is_said(tmp_path, place, monkeypatch):
    out = "claude_code → pi  /p/x.jsonl\n  resume with: pi --session abc\n"
    monkeypatch.setenv("MEDIA_TXCRIPT", _fake(tmp_path, out))
    assert "txcript#105" in convert.convert(SID, "pi")["loss"]


def test_txcript_failing_is_its_words(tmp_path, place, monkeypatch):
    out = "error: cannot convert to opencode: \x1b[91m`opencode import` failed\x1b[0m\n"
    monkeypatch.setenv("MEDIA_TXCRIPT", _fake(tmp_path, out, rc=1))
    with pytest.raises(convert.ConvertError, match="opencode import` failed"):
        convert.convert(SID, "opencode")


@pytest.mark.parametrize("to,why", [("claude", "already"), ("gemini", "cannot write"),
                                    ("", "cannot write")])
def test_refused_before_running(tmp_path, place, monkeypatch, to, why):
    monkeypatch.setenv("MEDIA_TXCRIPT", _fake(tmp_path, CODEX_OUT))
    with pytest.raises(convert.ConvertError, match=why):
        convert.convert(SID, to)
    assert not (tmp_path / "argv.json").exists()


def test_no_txcript(place, monkeypatch):
    monkeypatch.delenv("MEDIA_TXCRIPT", raising=False)
    monkeypatch.setattr(convert.shutil, "which", lambda name: None)
    with pytest.raises(convert.ConvertError, match="not installed"):
        convert.convert(SID, "codex")


def test_a_gone_folder_runs_from_home(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, "agent_of", lambda s: "claude")
    monkeypatch.setattr(sessions, "transcript_cwd", lambda s: str(tmp_path / "gone"))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MEDIA_TXCRIPT", _fake(tmp_path, CODEX_OUT))
    convert.convert(SID, "codex")
    ran = json.loads((tmp_path / "argv.json").read_text())
    assert os.path.realpath(ran["cwd"]) == os.path.realpath(tmp_path)


def test_cli_prints_the_link(tmp_path, place, monkeypatch, capsys):
    monkeypatch.setenv("MEDIA_TXCRIPT", _fake(tmp_path, CODEX_OUT))
    import agent_media_core.intake.context as ctx
    monkeypatch.setattr(ctx, "thread_link_base", lambda: "https://x.example/app/t/")
    assert headless_mail.main(["convert", SID, "--to", "codex", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] and out["link"] == ("https://x.example/app/t/"
                                         "01a1dd55-8715-7933-bffa-cfacf8115e8b")


def test_cli_failure(tmp_path, place, monkeypatch, capsys):
    monkeypatch.setenv("MEDIA_TXCRIPT", _fake(tmp_path, "boom\n", rc=2))
    assert headless_mail.main(["convert", SID, "--to", "codex"]) == headless_mail.RC_FAILED
    assert "boom" in capsys.readouterr().err


def test_a_txcript_opencode_id_is_an_opencode_id():
    """opencode's own ids have 26 base62 characters; a copy txcript imports
    has 32 hex ones, and is still an opencode thread (harnesses, sessions)."""
    from agent_media_core import harnesses

    for sid in ("ses_f2fa343dcffeKPfrR1z5h6u4NN", "ses_7350717840704786b9504e9831755976"):
        assert harnesses.is_opencode(sid)
        assert sessions._SESSION.fullmatch(sid)
    assert not harnesses.is_opencode("ses_" + "a" * 33)
