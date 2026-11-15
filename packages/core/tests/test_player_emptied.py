"""A live reply whose player is emptied from outside (sasonica-app #560, David,
15 Nov 2026): an app update restarts the app mid-reply, and the restarted app
reports an empty, idle player. Cut before its last sentence, the reply's row
keeps where it was (`extras.stopped_at`), so it keeps a Resume; cut on its
last sentence it counts as heard, as before. An ordinary end — played out, or
another reply taking over — is never stamped as a stop.

No real player: the sink is a fake, and the env is scrubbed of every route.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from agent_media_core.intake import submit as S
from agent_media_core.state import StateStore
from agent_media_core.types import Event, Source, Target

PHONE = Target(name="phone")
A = "sess-emptied"
# Three sentences the splitter keeps apart.
THREE = ("The first sentence of this reply is long enough to stand on its own. "
         "The second sentence of this reply is also long enough to stand alone. "
         "The third sentence of this reply is likewise long enough to stand alone.")


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    for k in list(os.environ):
        if k.startswith(("MEDIA_", "RELAY_")):
            monkeypatch.delenv(k, raising=False)
    for d in ("HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME", "XDG_CONFIG_HOME"):
        monkeypatch.setenv(d, str(tmp_path / d.lower()))
    monkeypatch.setenv("MEDIA_SPEECH_SOCKET_PHONE", "tcp://127.0.0.1:6602")
    monkeypatch.setenv("MEDIA_STREAM_CLIPS", "1")
    monkeypatch.setenv("MEDIA_RENDER_ENGINE", "edge")
    monkeypatch.setenv("MEDIA_RENDER_VOICE", "en-US-AriaNeural")
    monkeypatch.setattr(S.time, "sleep", lambda *_a, **_k: None)
    monkeypatch.setattr(S, "_clip_duration", lambda *_a, **_k: 2.0)
    monkeypatch.setattr(S, "render_text", _render)


def _render(text, outfile, **_):
    Path(outfile).write_bytes(b"\x00")
    return True, ""


class _Coord:
    def cancel_pre_pause(self): pass
    def pre_pause_remote(self): pass
    def before_speech(self, *a, **k): pass
    def speaking_line(self, text=""): pass
    def after_speech(self): pass
    def release_music_duck(self): pass
    def reapply_music_duck(self): pass


class _Phone:
    """Plays clip 0, 1, 2 a few ticks each. `empty_on`: the clip on which the
    player comes back empty (count 0, idle), as a restarted app reports it;
    None plays to the end and goes idle with its list intact."""

    TICKS = 4

    def __init__(self, empty_on=None, n=3):
        self.empty_on = empty_on
        self.n = n
        self.handed = 0
        self.ticks = 0
        self.stops = 0

    def prefetch(self, paths, target=None): pass

    def play_playlist(self, uris, target=None, gapless=True):
        self.handed = len(list(uris))

    def append_clips(self, uris, target=None):
        self.handed += len(list(uris))
        return True

    def set_playlist_pos(self, pos, target=None): pass
    def stop(self, target=None): self.stops += 1
    def muted(self, target): return False
    def idle(self, target): return True
    def active_other_owner(self, target): return None
    def claim_broker(self, target): return True
    def refresh_broker(self, target): pass
    def release_broker(self, target): pass

    def snapshot(self, target=None):
        self.ticks += 1
        pos = min(self.ticks // self.TICKS, self.n)
        if self.empty_on is not None and pos >= self.empty_on and self.ticks % self.TICKS == 2:
            return {"idle-active": True, "pause": False, "playlist-count": 0}
        if pos >= self.n:
            # Played out: idle on the last clip, the list still there.
            return {"idle-active": True, "pause": False,
                    "playlist-pos": self.n - 1, "playlist-count": self.handed}
        return {"idle-active": False, "pause": False, "playlist-pos": pos,
                "time-pos": 0.5, "playlist-count": self.handed, "mute": False}


def _speak(sink) -> dict:
    state = StateStore()
    S.submit_event(Event(text=THREE, source=Source.CLAUDE_CODE, target=PHONE,
                         metadata={"session": A}),
                   state=state, sink=sink, coordinator=_Coord())
    rows = [r for r in state.recent_history(sink="speech", limit=5)
            if not (r.get("extras") or {}).get("listener")]
    assert rows, "no row was written"
    ex = rows[0]["extras"]
    assert len(ex.get("clip_uris") or []) == 3, ex.get("clip_uris")
    return ex


def test_emptied_before_the_last_sentence_keeps_a_resume():
    ex = _speak(_Phone(empty_on=1))
    assert ex.get("stopped_at", {}).get("sentence") == 1, ex
    assert not ex.get("heard")


def test_emptied_on_the_first_sentence_is_unheard_from_the_top():
    from agent_media_core import reply_position

    ex = _speak(_Phone(empty_on=0))
    assert ex.get("stopped_at", {}).get("sentence") == 0, ex
    assert not ex.get("heard")
    assert reply_position.heard_to(ex) == 0


def test_emptied_on_the_last_sentence_counts_as_heard():
    ex = _speak(_Phone(empty_on=2))
    assert ex.get("heard") is True, ex
    assert "stopped_at" not in ex


def test_played_out_is_heard_not_stopped():
    sink = _Phone()
    ex = _speak(sink)
    assert ex.get("heard") is True, ex
    assert "stopped_at" not in ex


def test_superseded_by_the_next_reply_is_not_stamped(monkeypatch):
    """Another reply taking over: the loop's own `superseded` exit, and even
    when the list is seen empty first (the race), no stop is stamped."""
    sink = _Phone(empty_on=1)
    real = sink.snapshot

    def snap(target=None):
        got = real(target)
        if got.get("playlist-count") == 0:
            monkeypatch.setattr(S._SpeechPlaybackLock, "should_abort", lambda self: True)
        return got

    sink.snapshot = snap
    ex = _speak(sink)
    assert "stopped_at" not in ex, ex
