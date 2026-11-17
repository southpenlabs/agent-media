"""`/sessions/events?unheard=1`: the list's Play keys pushed as soon as a
reply is saved (unheard_watch.py, 18 Nov 2026)."""

from __future__ import annotations

import time

from agent_media_server import session_events, speech, unheard_watch

from test_contract import AUTH, call, server, shelf, signed_in, typed  # noqa: F401
from test_session_events import screen  # noqa: F401
from test_thread_events import Stream

A = "6c73498c-02c1-4846-8350-a82006973571"


def test_a_saved_reply_is_pushed_at_once(server, screen, monkeypatch):
    from agent_media_core.state import StateStore

    monkeypatch.setattr(session_events, "POLL_S", 30.0)   # the watcher's tick is not what tells
    monkeypatch.setattr(unheard_watch, "_COUNTS", None)
    counts = {}
    monkeypatch.setattr(speech, "unheard_counts", lambda: dict(counts))
    st = Stream(server, "/sessions/events?unheard=1&ping=0.3", AUTH)
    try:
        assert st.next("unheard") == {"unheard": {}}
        counts[A] = 1
        t = time.monotonic()
        StateStore().add_history(sink="speech", uri="/tmp/x.mp3", started_at=time.time(),
                                 source="claude-code", text="Held.",
                                 extras={"session": A, "kind": "stop", "held": True})
        assert st.next("unheard") == {"unheard": {A: 1}}
        assert time.monotonic() - t < 2.0
    finally:
        st.close()
    deadline = time.monotonic() + 3
    while (unheard_watch._WANTED or unheard_watch._THREAD) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert unheard_watch._WANTED == 0 and unheard_watch._THREAD is None


def test_no_frame_unless_asked(server, screen, monkeypatch):
    monkeypatch.setattr(speech, "unheard_counts", lambda: {A: 2})
    st = Stream(server, "/sessions/events?ping=0.3", AUTH)
    try:
        st.next("sessions")
        assert st.next("ping") == {}
        assert unheard_watch._WANTED == 0
    finally:
        st.close()
