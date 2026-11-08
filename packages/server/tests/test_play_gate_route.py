"""The play gate on the Media tab's routes (§6.9a, agent_media_core.play_gate):
a play held during a treatment answers 409 `held`, `GET /music` lists what
is held, and `{"action": "held", "play": true}` plays it on purpose.

The gate is a stub script and the `media` runner is faked: nothing plays.
"""

from __future__ import annotations

import stat

import pytest

from agent_media_core import play_gate
from agent_media_server import features, music, reply_actions

from test_contract import AUTH, audio_host, call, keys, server, signed_in, typed  # noqa: F401
from test_music_route import NOW, _no_phone  # noqa: F401

YT = "yt:https://www.youtube.com/watch?v=abcdefghijk"


@pytest.fixture()
def gate(monkeypatch, tmp_path):
    def put(body: str) -> None:
        p = tmp_path / "gate"
        p.write_text("#!/bin/sh\n" + body + "\n")
        p.chmod(p.stat().st_mode | stat.S_IEXEC)
        monkeypatch.setenv("MEDIA_PLAY_GATE_CMD", str(p))
        play_gate.forget_verdict()
    return put


@pytest.fixture()
def runner(monkeypatch):
    """`media <argv>` faked: a play is held by the gate the way the CLI does."""
    ran: list = []
    real = music.subprocess.run

    class R:
        def __init__(self, rc, err=""):
            self.returncode, self.stdout, self.stderr = rc, "", err

    def run(argv, **kw):
        if argv[1:3] != ["-m", "agent_media_core.cli"]:
            return real(argv, **kw)            # the gate command itself
        ran.append((argv[3:], kw.get("env") or {}))
        if argv[3:5] == ["music", "play"] and "--in-treatment" not in argv:
            try:
                play_gate.check(argv[-1])
            except play_gate.PlayHeld as e:
                return R(play_gate.EXIT_HELD, f"media: {e}")
        return R(0)
    monkeypatch.setattr(music.subprocess, "run", run)
    monkeypatch.setattr(music, "_status", lambda: dict(NOW))
    music._reset_cache(forget=True)
    yield ran
    music._reset_cache(forget=True)


def test_a_play_in_a_treatment_is_409_held_and_listed(server, signed_in, audio_host, runner, gate):
    gate('echo "2:00pm-3:15pm"; exit 0')
    res, obj = call(server, "POST", "/music", {"action": "play", "uri": YT}, AUTH)
    assert res.status == 409 and obj["held"] is True and obj["code"] == "held"
    assert "treatment is under way (2:00pm-3:15pm)" in obj["error"]
    (h,) = obj["on_hold"]
    assert h["uri"] == YT and h["id"]


def test_the_play_carries_when_it_was_asked_for(server, signed_in, audio_host, runner, gate):
    gate("exit 1")
    res, _ = call(server, "POST", "/music", {"action": "play", "uri": YT}, AUTH)
    assert res.status == 200
    argv, env = runner[-1]
    assert argv == ["music", "play", YT] and float(env["MEDIA_PLAY_REQUESTED_AT"]) > 0


def test_force_goes_through_and_held_play_now(server, signed_in, audio_host, runner, gate):
    gate("exit 0")
    res, _ = call(server, "POST", "/music", {"action": "play", "uri": YT, "force": True}, AUTH)
    assert res.status == 200 and runner[-1][0] == ["music", "play", YT, "--in-treatment"]
    call(server, "POST", "/music", {"action": "play", "uri": YT}, AUTH)
    (h,) = call(server, "GET", "/music", None, AUTH)[1]["on_hold"]
    res, obj = call(server, "POST", "/music", {"action": "held", "id": h["id"], "play": True}, AUTH)
    assert res.status == 200 and obj["on_hold"] == []
    argv, env = runner[-1]
    assert "--in-treatment" in argv and argv[-1] == YT and env["MEDIA_PLAY_FORCE"] == "1"


def test_a_held_play_can_be_dismissed(server, signed_in, audio_host, runner, gate):
    gate("exit 0")
    call(server, "POST", "/music", {"action": "play", "uri": YT}, AUTH)
    (h,) = call(server, "GET", "/music", None, AUTH)[1]["on_hold"]
    res, obj = call(server, "POST", "/music", {"action": "held", "id": h["id"], "dismiss": True}, AUTH)
    assert res.status == 200 and obj["on_hold"] == []


def test_no_gate_no_held_key(server, signed_in, audio_host, runner):
    res, obj = call(server, "GET", "/music", None, AUTH)
    assert res.status == 200 and "on_hold" not in obj
    assert features.detect()["play_gate"] is False


def test_play_now_is_a_reply_action_where_there_is_a_gate(gate):
    reply_actions.reset()
    assert reply_actions.task(play_gate.TASK_NAME) is None
    gate("exit 1")
    reply_actions.reset()
    t = reply_actions.task(play_gate.TASK_NAME)
    assert t and t["label"] == "Play now" and t["confirm"] is False
    assert features.detect()["play_gate"] is True
    reply_actions.reset()
