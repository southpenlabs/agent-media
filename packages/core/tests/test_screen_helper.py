"""media screen: the desktop half of "Pause what's playing here" (§6.21f)."""
import json

import pytest

from agent_media_core import screen_helper as sh


class FakeBackend:
    name = "fake"

    def __init__(self, playing, own=()):
        self.playing = list(playing)
        self.own = set(own)
        self.paused, self.resumed = [], []

    def playing_players(self):
        return list(self.playing)

    def own_voice(self, name):
        return name in self.own

    def pause_players(self, names):
        self.paused.extend(names)

    def resume_players(self, names):
        self.resumed.extend(names)


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_pauses_others_not_own_voice_and_resumes_only_those():
    b = FakeBackend(["firefox", "chrome-sasonica", "spotify"], own={"chrome-sasonica"})
    d = sh.Ducker(b, Clock())
    d.step(False)
    assert b.paused == []
    d.step(True)
    assert b.paused == ["firefox", "spotify"]
    d.step(True)  # still paused: nothing asked twice
    assert b.paused == ["firefox", "spotify"]
    d.step(False)
    assert b.resumed == ["firefox", "spotify"]
    assert not d.holding


def test_fails_open_when_server_goes_quiet_while_paused():
    clock = Clock()
    b = FakeBackend(["vlc"])
    d = sh.Ducker(b, clock)
    d.step(True)
    clock.t += sh.FAIL_OPEN_S / 2
    d.step(None)
    assert b.resumed == []
    clock.t += sh.FAIL_OPEN_S
    d.step(None)
    assert b.resumed == ["vlc"]


def test_own_voice_check_failing_still_pauses():
    class Broken(FakeBackend):
        def own_voice(self, name):
            raise RuntimeError("no metadata")
    b = Broken(["mpv-video"])
    d = sh.Ducker(b, Clock())
    d.step(True)
    assert b.paused == ["mpv-video"]


def test_pair_keeps_token_private(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    calls = []

    def fake_http(url, body=None, token="", timeout=4.0, method=""):
        calls.append((url, body, token))
        if url.endswith("/pair"):
            return 200, {"token": "t0k", "device_id": "d1", "name": "box screen"}
        return 200, {"ok": True}

    monkeypatch.setattr(sh, "_http", fake_http)
    rec = sh.pair("sasonica://pair?server=https://s.example&code=ABC")
    assert rec["url"] == "https://s.example" and rec["token"] == "t0k"
    assert calls[0][1]["code"] == "ABC"
    path = sh.conf_path()
    assert json.loads(path.read_text())["token"] == "t0k"
    assert (path.stat().st_mode & 0o777) == 0o600
    assert sh.load()["device_id"] == "d1"


def test_pair_needs_a_server(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    with pytest.raises(sh.ScreenError):
        sh.pair("JUSTACODE")


def test_run_once_asks_and_follows(monkeypatch):
    b = FakeBackend(["firefox"])
    monkeypatch.setattr(sh, "ask", lambda rec: {"ok": True, "on": True, "pause": True})
    assert sh.run({"url": "https://s.example", "token": "x"}, backend=b, once=True) == 0
    assert b.paused == ["firefox"] and b.resumed == ["firefox"]


def test_dictation_on_this_computer_pauses_too(monkeypatch):
    b = FakeBackend(["firefox"])
    answers = iter([{"on": True, "pause": False}] * 3)
    mics = iter([[], ["msrdc.exe"], []])
    monkeypatch.setattr(sh, "ask", lambda rec: next(answers))
    steps = []

    class Spy(sh.Ducker):
        def step(self, pause):
            steps.append(pause)
            super().step(pause)

    monkeypatch.setattr(sh, "Ducker", Spy)
    n = {"i": 0}

    def sleep(_):
        n["i"] += 1
        if n["i"] >= 3:
            raise KeyboardInterrupt

    sh.run({"url": "u", "token": "t"}, backend=b, sleep=sleep, mic=lambda: next(mics))
    assert steps == [False, True, False]
    assert b.paused == ["firefox"] and b.resumed == ["firefox"]


def test_switch_off_ignores_the_mic(monkeypatch):
    b = FakeBackend(["firefox"])
    monkeypatch.setattr(sh, "ask", lambda rec: {"on": False, "pause": False})
    sh.run({"url": "u", "token": "t"}, backend=b, once=True, mic=lambda: ["chrome.exe"])
    assert b.paused == []


def test_mic_ignore_and_off(monkeypatch):
    monkeypatch.setattr(sh.os, "name", "posix")
    monkeypatch.setattr(sh.sys, "platform", "linux")
    monkeypatch.setattr(sh, "_linux_mic_users", lambda: ["pulseaudio.exe", "arecord"])
    monkeypatch.setenv("MEDIA_SCREEN_MIC_IGNORE", "PulseAudio")
    assert sh.mic_users() == ["arecord"]
    monkeypatch.setenv("MEDIA_SCREEN_MIC", "0")
    assert sh.mic_users() == []
