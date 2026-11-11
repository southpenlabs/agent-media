"""The small HTTP endpoints hosted in the server's process (endpoints.py).

docs/proposals/2026-11-12-fewer-services.md §4c. The two things that would
be quietly wrong: an endpoint folded in while its old unit still holds the
port (two processes, one address) and the share endpoint losing its token
check on the way in (an open control port on the tailnet that starts
playback).
"""

from __future__ import annotations

import socket
import urllib.error
import urllib.request

import pytest

from agent_media_core import endpoints


@pytest.fixture(autouse=True)
def _no_inherited_choice(monkeypatch):
    monkeypatch.delenv(endpoints.ENV, raising=False)
    monkeypatch.setattr(endpoints, "_serving", {})


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# --- which ones are asked for ---------------------------------------------


def test_nothing_is_folded_in_unless_asked(monkeypatch):
    """The default must be empty. A release that moved a listener into the
    server on its own would leave two processes fighting for one port on
    every host whose unit was still running."""
    assert endpoints.wanted() == []


def test_names_are_taken_in_declaration_order_whatever_the_order_asked(monkeypatch):
    monkeypatch.setenv(endpoints.ENV, "clips, share")
    assert endpoints.wanted() == ["share", "clips"]


def test_all_means_every_one(monkeypatch):
    monkeypatch.setenv(endpoints.ENV, "all")
    assert endpoints.wanted() == [e.name for e in endpoints.ENDPOINTS]


def test_a_name_that_is_not_an_endpoint_is_reported_not_guessed(monkeypatch, caplog):
    monkeypatch.setenv(endpoints.ENV, "clips,sessiond")
    with caplog.at_level("WARNING"):
        assert endpoints.wanted() == ["clips"]
    assert "sessiond" in caplog.text


def test_sessiond_and_the_sinks_are_not_foldable_at_all():
    """Named in the module docstring and absent from the table on purpose:
    sessiond holds every app-started chat as a child, and each sink holds a
    live mpv and a playhead. A server restart must not take those with it."""
    names = {e.name for e in endpoints.ENDPOINTS}
    assert not names & {"sessiond", "sink-speech", "sink-book", "mcp"}


# --- binding --------------------------------------------------------------


def test_bind_and_port_come_from_the_same_variables_the_unit_used(monkeypatch):
    ep = {e.name: e for e in endpoints.ENDPOINTS}["clips"]
    assert ep.port() == 8780
    monkeypatch.setenv("MEDIA_CLIPS_PORT", "9999")
    monkeypatch.setenv("MEDIA_CLIPS_BIND", "10.1.2.3")
    assert ep.port() == 9999 and ep.bind() == "10.1.2.3"
    monkeypatch.setenv("MEDIA_CLIPS_PORT", "not-a-port")
    assert ep.port() == 8780, "a typo falls back, it does not crash the boot"


def test_a_port_already_held_is_reported_and_names_the_unit(monkeypatch):
    """What a still-running old unit looks like from in here. It must not
    stop the server booting, and it must say why."""
    port = _free_port()
    with socket.socket() as held:
        held.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        held.bind(("127.0.0.1", port))
        held.listen(1)
        monkeypatch.setenv("MEDIA_CLIPS_PORT", str(port))
        monkeypatch.setenv("MEDIA_CLIPS_BIND", "127.0.0.1")
        monkeypatch.setenv(endpoints.ENV, "clips")
        started, problems = endpoints.start_all()
    assert started == []
    assert len(problems) == 1
    assert "cannot bind" in problems[0]
    assert "agent-media-clips.service" in problems[0], \
        "say which unit is probably holding it"


def test_an_endpoint_whose_package_is_absent_says_so(monkeypatch):
    ep = endpoints.Endpoint(
        "ghost", "not installed anywhere",
        lambda b, p: (_ for _ in ()).throw(ImportError(name="nope")), 1)
    assert "not installed" in (endpoints.start_one(ep) or "")


def test_an_endpoint_that_needs_a_setting_is_off_rather_than_broken(monkeypatch):
    monkeypatch.delenv("MEDIA_COMPLETIONS_TARGET", raising=False)
    ep = {e.name: e for e in endpoints.ENDPOINTS}["completions-shim"]
    assert endpoints.missing_env(ep) == ["MEDIA_COMPLETIONS_TARGET"]
    assert "needs MEDIA_COMPLETIONS_TARGET" in (endpoints.start_one(ep) or "")
    assert "completions-shim" not in endpoints.serving()


# --- the share endpoint's token check survives the move -------------------


def test_share_still_refuses_a_tailnet_bind_with_no_token(monkeypatch):
    """The check moved into make_server precisely so both callers get it.
    This endpoint starts playback: bound to a routable address with no token
    it would be an open control port reporting itself healthy."""
    from agent_media_core.entrypoints import share_listener

    monkeypatch.delenv("MEDIA_SHARE_TOKEN", raising=False)
    with pytest.raises(PermissionError):
        share_listener.make_server("10.1.2.3", _free_port())
    # The failure is surfaced, not swallowed, and nothing is left serving.
    monkeypatch.setenv("MEDIA_SHARE_BIND", "10.1.2.3")
    ep = {e.name: e for e in endpoints.ENDPOINTS}["share"]
    assert "MEDIA_SHARE_TOKEN" in (endpoints.start_one(ep) or "")
    assert "share" not in endpoints.serving()


def test_share_on_loopback_needs_no_token(monkeypatch):
    from agent_media_core.entrypoints import share_listener

    monkeypatch.delenv("MEDIA_SHARE_TOKEN", raising=False)
    srv = share_listener.make_server("127.0.0.1", _free_port())
    srv.server_close()


# --- it actually serves ---------------------------------------------------


def test_clips_really_answers_from_inside_this_process(monkeypatch, tmp_path):
    """The whole claim of §4c: the process goes away, the address does not."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    (tmp_path / "agent-media").mkdir(parents=True)
    (tmp_path / "agent-media" / "clip.txt").write_text("hello")
    port = _free_port()
    monkeypatch.setenv("MEDIA_CLIPS_PORT", str(port))
    monkeypatch.setenv("MEDIA_CLIPS_BIND", "127.0.0.1")
    monkeypatch.setenv(endpoints.ENV, "clips")

    started, problems = endpoints.start_all()
    assert (started, problems) == (["clips"], [])
    try:
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/clip.txt", timeout=5) as r:
            assert r.read() == b"hello"
    finally:
        endpoints.serving()["clips"].shutdown()
        endpoints.serving()["clips"].server_close()


def test_starting_the_same_endpoint_twice_is_a_no_op(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    (tmp_path / "agent-media").mkdir(parents=True)
    port = _free_port()
    monkeypatch.setenv("MEDIA_CLIPS_PORT", str(port))
    monkeypatch.setenv("MEDIA_CLIPS_BIND", "127.0.0.1")
    ep = {e.name: e for e in endpoints.ENDPOINTS}["clips"]
    try:
        assert endpoints.start_one(ep) is None
        # Second call must not try to bind again — that would be the
        # "cannot bind" error against ourselves.
        assert endpoints.start_one(ep) is None
        assert list(endpoints.serving()) == ["clips"]
    finally:
        for srv in endpoints.serving().values():
            srv.shutdown()
            srv.server_close()
