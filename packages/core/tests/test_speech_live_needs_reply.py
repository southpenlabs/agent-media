"""Speech is live only when a reply is behind it, not when the player is busy.

The phone's Sasonica app has ONE player for spoken replies and music, reached
over both of the canvas's relays (speech 16624, music 16625). A song loaded in
it answers `idle-active: false` on the speech socket too, and the remote lane
read that as a reply playing: /speech/now said "speaking" with nothing named
(8 Nov 2026). The speech row is what says a reply is live.
"""

from __future__ import annotations

from agent_media_core import cli

_MUSIC = {"idle-active": False, "pause": False, "time-pos": 63.0,
          "duration": 241.0, "mute": False, "speed": 1.0, "playlist-pos": 0}


def _remote(monkeypatch, row, snap=_MUSIC):
    monkeypatch.setattr(cli, "_remote_speech", lambda: True)
    monkeypatch.setattr(cli, "_sock", lambda: "tcp://127.0.0.1:16624")
    monkeypatch.setattr(cli, "_now_speaking", lambda: row)
    cli._SNAP_CACHE["value"] = None
    calls = []

    def snapshot():
        calls.append(1)
        return dict(snap)

    monkeypatch.setattr(cli, "_remote_snapshot", snapshot)
    return calls


def test_music_in_the_shared_player_is_not_speech(monkeypatch):
    calls = _remote(monkeypatch, None)
    for kw in ({}, {"prefer_local": True}):
        idle, pos, dur, paused, muted, speed, playing = \
            cli._speech_display_state(**kw)
        assert idle is True and playing is False
        assert (pos, dur) == (None, None)
    assert not calls, "with no reply in flight there is nothing to ask"


def test_a_dead_writers_row_is_not_speech_either(monkeypatch):
    """A row whose submit process died without cleanup is the zombie the
    display already ignores; music in the player must not revive it."""
    monkeypatch.setattr("agent_media_core.procinfo.alive", lambda _pid: False)
    _remote(monkeypatch, {"extras": {"writer_pid": 999999}})
    assert cli._speech_display_state()[0] is True


def test_a_reply_over_ducked_music_still_reads_live(monkeypatch):
    """The reply's row is there, so the player's reading is the reply's."""
    row = {"extras": {"writer_pid": None, "total_duration_s": 9.0,
                      "clip_durations_s": [4.0, 5.0]}}
    _remote(monkeypatch, row,
            {**_MUSIC, "time-pos": 1.5, "duration": 5.0, "playlist-pos": 1})
    idle, pos, dur, paused, _m, _s, playing = cli._speech_display_state()
    assert (idle, playing, paused) == (False, True, False)
    assert (pos, dur) == (5.5, 9.0)


def test_a_paused_reply_still_reads_paused(monkeypatch):
    row = {"extras": {"writer_pid": None, "kind": "remote-say"}}
    _remote(monkeypatch, row, {**_MUSIC, "pause": True, "time-pos": 2.0,
                               "duration": 6.0})
    idle, _pos, _dur, paused, *_ = cli._speech_display_state()
    assert idle is False and paused is True


def test_popup_status_is_idle_while_only_music_plays(monkeypatch, capsys):
    _remote(monkeypatch, None)
    monkeypatch.setattr(cli, "_miss_alert_line", lambda: "")
    monkeypatch.setattr(cli, "_subject_label", lambda: ("", ""))
    monkeypatch.setattr(cli, "_sticky_speech_speed", lambda _s: None)

    class _Store:
        def list_mutes(self):
            return {"panes": {}, "sessions": {}}

    monkeypatch.setattr(cli, "StateStore", _Store)

    class A:
        act = None
        width = 40
        show_idle = True
        no_bar = True

    cli.cmd_popup_status(A())
    first = capsys.readouterr().out.splitlines()[0]
    assert not first.startswith("▶"), first
