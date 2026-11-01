"""The sleep timer (sleep_timer.py), against fake players and a fake clock:
nothing real is turned down or stopped."""

import pytest

from agent_media_core import sleep_timer as T


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.delenv("MEDIA_SLEEP_FADE_S", raising=False)


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


class Music:
    def __init__(self, level=60):
        self.level, self.levels = level, []

    def current_volume(self, t):
        return self.level

    def duck(self, t, level):
        self.levels.append(level)


class Book:
    def __init__(self, level=None):
        self.level, self.levels = level, []

    def volume(self, t):
        return self.level

    def set_volume(self, level, t):
        self.levels.append(level)


class Srv:
    def __init__(self, music, book):
        self.m, self.b, self.stopped = music, book, []

    def _music(self):
        return self.m

    def _book(self):
        return self.b

    def _music_target(self, t):
        return t

    def _book_target(self, t):
        return t

    def music_stop(self, t):
        self.stopped.append("music")

    def book_stop(self, t):
        self.stopped.append("book")


def test_start_status_cancel():
    spawned = []
    s = T.start(30, 120, spawn=spawned.append, now=1000.0)
    assert spawned == [s["id"]]
    assert T.status(now=1000.0)["left_s"] == 1800
    assert T.cancel() and T.status() is None and not T.cancel()


def test_bad_minutes():
    with pytest.raises(ValueError):
        T.start(0, spawn=lambda i: None)


def test_fade_then_stop_then_restore():
    clock = Clock()
    s = T.start(10, 60, spawn=lambda i: None, now=clock())
    music, book = Music(60), Book(80)
    srv = Srv(music, book)
    assert T.run(s["id"], sleep=clock.sleep, clock=clock, srv=srv) == "stopped"
    assert srv.stopped == ["music", "book"]
    fade = music.levels[:-1]
    assert fade[0] == 60 and fade == sorted(fade, reverse=True) and fade[-1] <= 10
    assert music.levels[-1] == 60 and book.levels[-1] == 80   # restored after the stop
    assert T.status() is None


def test_cancelled_mid_fade_stops_nothing():
    clock = Clock()
    s = T.start(5, 120, spawn=lambda i: None, now=clock())
    srv = Srv(Music(50), Book())
    real_sleep = clock.sleep

    def sleep(sec):
        real_sleep(sec)
        if clock() > s["until"] - 60:
            T.cancel()
    assert T.run(s["id"], sleep=sleep, clock=clock, srv=srv) == "cancelled"
    assert srv.stopped == []


def test_a_replaced_timer_lets_the_old_worker_go():
    clock = Clock()
    old = T.start(5, 60, spawn=lambda i: None, now=clock())
    T.start(20, 60, spawn=lambda i: None, now=clock())
    assert T.run(old["id"], sleep=clock.sleep, clock=clock, srv=Srv(Music(), Book())) == "gone"
