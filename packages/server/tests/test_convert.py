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


# --- a cut at a message, the origin, the server's call, Branch's agent -----------------

def _times_fake(tmp_path, stamps: list[str], cont: str = CODEX_OUT) -> str:
    """A txcript whose `export` lists messages at `stamps` and whose
    `continue` prints `cont`; argv of the last call kept."""
    log = tmp_path / "argv.json"
    exe = tmp_path / "txcript"
    export = json.dumps({"messages": [{"role": "user", "timestamp": t, "content": "x"}
                                      for t in stamps]})
    exe.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        f"json.dump({{'argv': sys.argv[1:], 'cwd': os.getcwd()}}, open({str(log)!r}, 'w'))\n"
        f"sys.stdout.write({export!r} if sys.argv[1] == 'export' else {cont!r})\n")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return str(exe)


# Our view of a chat: you, a reply in two of txcript's messages, you, a reply.
OURS = [{"id": "u1", "role": "user", "at": 1000.0},
        {"id": "a1", "role": "assistant", "at": 1001.0},
        {"id": "u2", "role": "user", "at": 1010.0},
        {"id": "a2", "role": "assistant", "at": 1011.0}]
STAMPS = ["1970-01-01T00:16:40.000Z", "1970-01-01T00:16:41.000Z", "1970-01-01T00:16:42.000Z",
          "1970-01-01T00:16:50.000Z", "1970-01-01T00:16:51.000Z"]


@pytest.fixture
def ours(monkeypatch):
    from agent_media_server import transcript

    monkeypatch.setattr(transcript, "messages", lambda s, **k: ([dict(m) for m in OURS], False))


def test_cut_count(ours):
    times = [convert._epoch(t) for t in STAMPS]
    assert convert.cut_count(SID, "a1", "history", times) == 3    # up to the reply's end
    assert convert.cut_count(SID, "a2", "history", times) == 5    # the last reply: all
    assert convert.cut_count(SID, "u2", "before", times) == 3     # before your message
    assert convert.cut_count(SID, "u1", "before", times) == 0     # your first: nothing
    assert convert.cut_count(SID, "a1", "before", times) is None  # not one of yours
    assert convert.cut_count(SID, "zz", "history", times) is None


@pytest.fixture
def gate(monkeypatch, tmp_path):
    from agent_media_server import auth, default_agent

    monkeypatch.setattr(auth, "gate", lambda b: ("someone", {}))
    monkeypatch.setattr(default_agent, "_installed", lambda a: a in ("claude", "codex"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    sent = []
    from agent_media_server import send
    monkeypatch.setattr(send, "reply", lambda text, bearer, **kw: sent.append(
        (text, kw.get("session"))) or (True, {}))
    return sent


def test_session_convert_cuts_records_and_sends(tmp_path, place, ours, gate, monkeypatch):
    monkeypatch.setenv("MEDIA_TXCRIPT", _times_fake(tmp_path, STAMPS))
    ok, out = convert.session_convert(SID, "codex", "tok", text="go on", mid="a1")
    new = "01a1dd55-8715-7933-bffa-cfacf8115e8b"
    assert ok and out["session"] == new and out["converted"] and out["agent"] == "codex"
    assert out["from"] == {"session": SID, "agent": "claude", "id": "a1"} and out["sent"]
    ran = json.loads((tmp_path / "argv.json").read_text())
    assert ran["argv"][:2] == ["continue", f"{SID}#1-3"]
    assert gate == [("go on", new)]
    assert convert.origin(new)["session"] == SID and convert.origin(SID) is None


def test_session_convert_refusals(tmp_path, place, ours, gate, monkeypatch):
    monkeypatch.setenv("MEDIA_TXCRIPT", _times_fake(tmp_path, STAMPS))
    assert convert.session_convert("nope", "codex", "tok")[1]["status"] == 400
    assert convert.session_convert(SID, "pi", "tok")[1]["status"] == 409   # pi not here
    assert convert.session_convert(SID, "codex", "tok", mid="zz")[1]["status"] == 404
    assert convert.session_convert(SID, "codex", "tok", mid="u1",
                                   mode="before")[1]["status"] == 409
    monkeypatch.setenv("MEDIA_TXCRIPT", str(tmp_path / "gone"))
    assert convert.session_convert(SID, "codex", "tok")[1]["status"] == 409  # no txcript


def test_targets_need_txcript_and_the_agent(tmp_path, gate, monkeypatch):
    monkeypatch.setenv("MEDIA_TXCRIPT", str(tmp_path / "gone"))
    assert convert.targets() == []
    monkeypatch.setenv("MEDIA_TXCRIPT", _fake(tmp_path, CODEX_OUT))
    assert convert.targets() == ["claude", "codex"]


def test_branch_into_another_agent_converts(tmp_path, place, ours, gate, monkeypatch):
    from agent_media_server import branch

    monkeypatch.setenv("MEDIA_TXCRIPT", _times_fake(tmp_path, STAMPS))
    ok, out = branch.session_branch(SID, "u2", "try it this way", "tok", mode="before",
                                    agent="codex")
    assert ok and out["branched"] and out["converted"] and out["from"]["id"] == "u2"
    assert json.loads((tmp_path / "argv.json").read_text())["argv"][1] == f"{SID}#1-3"


def test_branch_with_its_own_agent_forks_as_before(place, ours, gate, monkeypatch):
    from agent_media_server import branch, convert as cv

    monkeypatch.setattr(cv, "session_convert", lambda *a, **k: pytest.fail("converted"))
    monkeypatch.setattr(branch.transcript, "transcript_path", lambda s: "")
    ok, out = branch.session_branch(SID, "a1", "hi", "tok", agent="claude")
    assert not ok and out["status"] == 409  # the old path's own answer, no transcript here
