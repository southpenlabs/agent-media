"""Quiet and Mute passed on to Telegram (bridge_mute.py): which command is
sent and when, and that the command stays out of the room's log. No network."""

import time

import pytest

from agent_media_server import bridge_mute, matrix, mute, quiet


class FakeRoom:
    def __init__(self, network="telegram"):
        self.network = {"id": network, "name": network}
        self.sent = []

    def send_content(self, content, reply_to=""):
        self.sent.append(content["body"])
        return True, {"event_id": f"$e{len(self.sent)}"}


@pytest.fixture
def room(monkeypatch):
    r = FakeRoom()
    monkeypatch.setattr(matrix, "room", lambda thread: r if thread == T else None)
    return r


T = "77a0a2ee-c374-56dd-aa42-b251004eb4d1"


def test_quiet_until_turned_off_mutes_for_good_and_ending_it_unmutes(room):
    quiet.set_quiet(T, None)
    assert bridge_mute.sync(T) == "!tg mute"
    assert bridge_mute.sync(T) == ""                      # nothing changed: not sent twice
    quiet.set_quiet(T, False)
    assert bridge_mute.sync(T) == "!tg unmute"
    assert bridge_mute.sync(T) == ""
    assert room.sent == ["!tg mute", "!tg unmute"]


def test_a_timed_mute_goes_for_its_time_and_the_later_end_wins(room):
    now = time.time()
    mute.set_mute(T, True, now + 3600)
    assert bridge_mute.sync(T, now) == "!tg mute 3600s"
    quiet.set_quiet(T, now + 7200)
    assert bridge_mute.sync(T, now) == "!tg mute 7200s"
    mute.set_mute(T, True)                                # until turned off
    assert bridge_mute.sync(T, now) == "!tg mute"
    mute.set_mute(T, False)
    assert bridge_mute.sync(T, now) == "!tg mute 7200s"


def test_a_mute_that_ran_out_here_is_not_undone_twice(room):
    now = time.time()
    mute.set_mute(T, True, now + 600)
    assert bridge_mute.sync(T, now) == "!tg mute 600s"
    later = now + 700                                     # ran out on both sides by itself
    assert bridge_mute.sync(T, later) == ""


def test_nothing_is_unmuted_that_this_server_did_not_mute(room):
    assert bridge_mute.sync(T) == ""
    assert room.sent == []


def test_a_shifts_rule_and_other_networks_are_left_alone(room, monkeypatch):
    monkeypatch.setattr(quiet, "rule_of", lambda s: {"rule": "shifts", "source": "x"})
    monkeypatch.setattr(quiet, "quiet_of", lambda s, now=None: {"until": None, "at": 1})
    assert bridge_mute.sync(T) == ""
    monkeypatch.undo()
    room.network = {"id": "whatsapp", "name": "WhatsApp"}
    mute.set_mute(T, True)
    assert bridge_mute.sync(T) == ""


def test_the_command_stays_out_of_the_log():
    from test_matrix_rooms import ME, _ev, _room

    r, _ = _room([_ev(1, ME, "!tg mute 3600s"), _ev(2, ME, "!tg unmute"),
                  _ev(3, ME, "!tg is how you talk to the bridge")])
    r.load()
    assert [m["parts"][0]["text"] for m in r.messages] == ["!tg is how you talk to the bridge"]


def test_the_routes_pass_it_on(room, monkeypatch):
    from agent_media_server import auth

    called = []
    monkeypatch.setattr(bridge_mute, "soon", called.append)
    monkeypatch.setattr(auth, "gate", lambda bearer: ("david", None))
    assert mute.session_mute(T, {"muted": True}, "b")[0]
    assert quiet.session_quiet(T, {"until": None}, "b")[0]
    assert called == [T, T]
