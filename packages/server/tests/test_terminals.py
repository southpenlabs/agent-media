"""The terminal the server holds itself: install and sign-in without tmux."""

import os
import sys
import time

import pytest

from agent_media_core import harnesses
from agent_media_server import auth, panes, send, terminals
from agent_media_server import harnesses as agents

posix = pytest.mark.skipif(os.name != "posix", reason="a pty is POSIX")


@pytest.fixture(autouse=True)
def _here(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIA_AGENT_SETUP_DIR", str(tmp_path / "setup"))
    monkeypatch.delenv("MEDIA_HARNESS_TERMINAL", raising=False)
    monkeypatch.setattr(auth, "may_control_speech", lambda bearer: (True, {}))

    def no_tmux(*a, **k):
        raise AssertionError("tmux was asked for")
    monkeypatch.setattr(panes, "_tmux", no_tmux)
    monkeypatch.setattr(send, "ensure_host", no_tmux)


def _until(pred, wait=10.0):
    end = time.monotonic() + wait
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


def _py(code: str) -> list[str]:
    return [sys.executable, "-c", code]


def _recipe(monkeypatch, agent, install=None, login=None):
    monkeypatch.setattr(harnesses, "install_argv", lambda name: install if name == agent else [])
    monkeypatch.setattr(harnesses, "login_argv", lambda name: login if name == agent else [])


# --- an install: a plain subprocess, its output streamed ----------------------------

def test_an_install_runs_without_tmux_and_streams_its_output(monkeypatch):
    _recipe(monkeypatch, "codex", install=_py(
        "import sys,time\n"
        "print('fetching', flush=True)\n"
        "sys.stdout.write('10%\\r50%\\r100%\\n'); sys.stdout.flush()\n"
        "print('added 1 package', file=sys.stderr, flush=True)\n"
        "sys.exit(3)"))
    ok, detail = agents.run("codex", "install", "bearer")
    assert ok and terminals.ID.fullmatch(detail["pane"])
    pane = detail["pane"]
    term = terminals.get(pane)
    assert term is not None and term.tty is False          # no terminal needed
    assert _until(lambda: agents.screen(pane, "b")[1]["done"])
    _ok, shown = agents.screen(pane, "b")
    assert shown["exit"] == 3
    assert shown["lines"][:3] == ["fetching", "100%", "added 1 package"]
    assert shown["lines"][-1] == "[finished: 3]"
    assert "signin" not in shown                            # an install asks nothing
    # Ended: keys reach nothing, and close forgets it.
    assert agents.close(pane, "b")[0]
    assert agents.screen(pane, "b")[1]["status"] == 404


@posix
def test_a_sign_in_runs_in_a_pty_and_takes_keys(monkeypatch):
    _recipe(monkeypatch, "claude", login=_py(
        "import sys\n"
        "print('tty' if sys.stdin.isatty() else 'pipe', flush=True)\n"
        "print('Paste code here if prompted >', flush=True)\n"
        "code = input()\n"
        "print('got ' + code, flush=True)"))
    monkeypatch.setattr(agents, "SIGNIN_WAIT_S", 0.5)
    ok, detail = agents.run("claude", "login", "bearer")
    pane = detail["pane"]
    assert ok and terminals.get(pane).tty
    assert _until(lambda: "Paste code" in "\n".join(agents.screen(pane, "b")[1]["lines"]))
    assert agents.screen(pane, "b")[1]["lines"][0] == "tty"
    assert agents.keys(pane, "abc123\nnot this", "Enter", "b")[0]
    assert _until(lambda: agents.screen(pane, "b")[1]["done"])
    shown = agents.screen(pane, "b")[1]
    assert "got abc123" in shown["lines"] and shown["exit"] == 0
    agents.close(pane, "b")


@posix
def test_a_device_code_sign_in_answers_with_its_url_and_code(monkeypatch):
    _recipe(monkeypatch, "codex", login=_py(
        "import time\n"
        "print('Follow these steps to sign in:', flush=True)\n"
        "print('1. Open https://auth.openai.com/codex/device in your browser', flush=True)\n"
        "print('2. Enter this one-time code: KQ7P-2MXD', flush=True)\n"
        "time.sleep(30)"))
    ok, detail = agents.run("codex", "login", "bearer")
    assert ok
    assert detail["signin"] == {"url": "https://auth.openai.com/codex/device",
                                "code": "KQ7P-2MXD", "paste": False}
    assert agents.screen(detail["pane"], "b")[1]["signin"]["code"] == "KQ7P-2MXD"
    agents.close(detail["pane"], "b")
    assert terminals.get(detail["pane"]) is None


def test_a_gone_terminal_is_410_as_a_gone_window_was(monkeypatch):
    _recipe(monkeypatch, "codex", install=_py("print('x')"))
    pane = agents.run("codex", "install", "bearer")[1]["pane"]
    terminals.close(pane)              # as after a restart of the server
    assert agents.screen(pane, "b")[1]["status"] == 410
    assert agents.keys(pane, "y", "", "b")[1]["status"] == 404   # and forgotten


def test_a_program_that_is_not_there_is_refused_not_hung(monkeypatch):
    _recipe(monkeypatch, "codex", install=["/nonexistent/npm", "install"])
    ok, detail = agents.run("codex", "install", "bearer")
    assert not ok and detail["status"] == 503


def test_tmux_is_still_there_when_asked_for(monkeypatch):
    monkeypatch.setenv("MEDIA_HARNESS_TERMINAL", "tmux")
    seen = []
    monkeypatch.setattr(agents, "_tmux_window", lambda argv, title: (seen.append(title) or "%9", ""))
    _recipe(monkeypatch, "codex", install=["npm", "i"])
    assert agents.run("codex", "install", "bearer")[1]["pane"] == "%9"
    assert seen == ["codex-install"]


# --- reading a sign-in off the screen ------------------------------------------------

def test_signin_reads_a_wrapped_url_whole():
    url = "https://claude.ai/oauth/authorize?code=true&client_id=" + "x" * 90
    lines = ["Browser didn't open? Use the url below to sign in:", "",
             url[:80], url[80:], "", "Paste code here if prompted >"]
    assert terminals.signin(lines) == {"url": url, "code": "", "paste": True}


def test_signin_ignores_dates_versions_and_screens_without_a_url():
    assert terminals.signin(["installed 2.0.14 on 2026-11-25"]) is None
    found = terminals.signin(["see https://x.ai/cli/device", "v1.2.3-beta 2026-11-25"])
    assert found == {"url": "https://x.ai/cli/device", "code": "", "paste": False}


def test_signin_prefers_the_sign_in_url_to_a_docs_link():
    found = terminals.signin(["Docs: https://example.com/help",
                              "Visit https://accounts.x.ai/device?user_code=ABCD-EFGH",
                              "and enter ABCD-EFGH"])
    assert found["url"].startswith("https://accounts.x.ai/device")
    assert found["code"] == "ABCD-EFGH"


def test_a_piped_line_overwritten_by_carriage_returns():
    t = terminals.Terminal("t00000000", [], proc=None, tty=False)  # type: ignore[arg-type]
    t._feed(b"\x1b[32mone\x1b[0m\nprog 1\rprog 2")
    t._feed(b"\rprog 3\nend\n")
    assert t.lines() == ["one", "prog 3", "end"]


def test_a_finished_terminal_is_reaped_after_its_linger():
    t = terminals.Terminal("t0000000a", [], proc=None, tty=False)  # type: ignore[arg-type]
    t.exit, t.ended_at = 0, 1000.0
    terminals._HELD[t.id] = t
    terminals._reap(now=1000.0 + terminals.LINGER_S - 1)
    assert terminals.get(t.id) is t
    terminals._reap(now=1000.0 + terminals.LINGER_S + 1)
    assert terminals.get(t.id) is None


def test_a_gateway_sign_in_asks_for_its_callback_address_only_later():
    url = "https://accounts.google.com/o/oauth2/v2/auth?client_id=1&redirect_uri=http://localhost:51121"
    waiting = ["Visit the following URL to continue authentication:", url,
               "Waiting for antigravity authentication callback..."]
    assert terminals.signin(waiting) == {"url": url, "code": "", "paste": False}
    later = waiting + ["Paste the antigravity callback URL (or press Enter to keep waiting):"]
    assert terminals.signin(later)["paste"] is True
