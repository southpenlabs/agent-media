"""A replay on the phone counts as heard only once the phone has spoken up.

The frames hub answers for the phone from its model, whose playhead runs on by
the clock: a reply the phone never played read as played to the end and was
marked heard (David, 14 Nov 2026). `_phone_confirmed` asks the hub when the
phone last reported; a player without that property is believed, as before.
"""

from __future__ import annotations

from agent_media_core import cli
from agent_media_core.sinks import _mpv_ipc as ipc


def _says(monkeypatch, value=None, fails=False):
    def get(sock, name, *a, **k):
        assert name == "user-data/agent-media/reported-at"
        if fails:
            raise ipc.MpvIpcError("property unavailable")
        return value
    monkeypatch.setattr(cli, "_sock", lambda: "tcp://127.0.0.1:1")
    monkeypatch.setattr(ipc, "get_property", get)


def test_a_report_since_the_replay_began_confirms(monkeypatch):
    _says(monkeypatch, 1000.5)
    assert cli._phone_confirmed(1000.0) is True


def test_no_report_since_it_began_does_not(monkeypatch):
    _says(monkeypatch, 990.0)
    assert cli._phone_confirmed(1000.0) is False
    _says(monkeypatch, 0.0)
    assert cli._phone_confirmed(1000.0) is False


def test_a_player_without_the_property_is_believed(monkeypatch):
    _says(monkeypatch, fails=True)
    assert cli._phone_confirmed(1000.0) is True
    _says(monkeypatch, None)
    assert cli._phone_confirmed(1000.0) is True
