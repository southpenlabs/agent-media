"""Replies refused for want of a device: an alert once per outage, cleared by
the next reply that plays. Private state; no sink, no alert store."""

from agent_media_core import speech_reach

ERR = RuntimeError("loadfile: no device plays speech: none has ?speech=frames on its stream")


class Calls:
    def __init__(self):
        self.alerts, self.errors = [], []

    def kw(self):
        return {"alert": lambda *a: self.alerts.append(a), "note_error": self.errors.append}


def test_three_unheard_replies_raise_once_and_a_played_one_clears(tmp_path, monkeypatch):
    monkeypatch.delenv("MEDIA_SPEECH_UNREACHED_REPLIES", raising=False)
    path, c = tmp_path / "run.json", Calls()
    got = [speech_reach.reply_ended(False, True, ERR, now=100.0 + i, path=path, **c.kw())
           for i in range(5)]
    assert got == [None, None, "raised", None, None]
    assert [a[0] for a in c.alerts] == ["warn"]
    assert c.alerts[0][1] == speech_reach.TITLE and "5" not in c.alerts[0][2]
    assert len(c.errors) == 1 and "3 replies in a row" in c.errors[0]
    assert speech_reach.status(path)["misses"] == 5
    assert speech_reach.reply_ended(True, False, now=200.0, path=path, **c.kw()) == "cleared"
    assert [a[0] for a in c.alerts] == ["warn", "ok"]
    assert speech_reach.status(path) == {}


def test_a_played_reply_ends_the_run_before_it_alerts(tmp_path):
    path, c = tmp_path / "run.json", Calls()
    for played in (False, False, True, False, False):
        speech_reach.reply_ended(played, not played, ERR, path=path, **c.kw())
    assert c.alerts == [] and speech_reach.status(path)["misses"] == 2


def test_other_outcomes_leave_the_run_alone(tmp_path):
    path, c = tmp_path / "run.json", Calls()
    speech_reach.reply_ended(False, True, ERR, path=path, **c.kw())
    # Cut before it began, or failed some other way: neither counts nor clears.
    assert speech_reach.reply_ended(False, False, path=path, **c.kw()) is None
    assert speech_reach.status(path)["misses"] == 1
    assert speech_reach.reply_ended(True, False, path=path, **c.kw()) is None  # Nothing raised.
    assert c.alerts == []


def test_the_count_is_a_setting(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIA_SPEECH_UNREACHED_REPLIES", "1")
    path, c = tmp_path / "run.json", Calls()
    assert speech_reach.reply_ended(False, True, ERR, path=path, **c.kw()) == "raised"


def test_an_alert_that_fails_never_reaches_the_reply(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIA_SPEECH_UNREACHED_REPLIES", "1")

    def boom(*a):
        raise OSError("store down")
    assert speech_reach.reply_ended(False, True, ERR, path=tmp_path / "r.json",
                                    alert=boom, note_error=lambda d: None) == "raised"


def test_is_no_device():
    assert speech_reach.is_no_device(ERR)
    assert not speech_reach.is_no_device(RuntimeError("mpv socket gone"))
