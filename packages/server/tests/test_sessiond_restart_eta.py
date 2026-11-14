"""A window for quick work while a restart waits on a long job (14 Nov 2026;
David: "sessions working through a restart notice might be requested to give
an ETA of critical task completion ... that way other sessions can keep
working on quicker tasks that will complete before the longest ETA").

While the longest estimate of a session the restart waits on has at least
`window_min_s()` left, sessiond lets messages to the other sessions and new
chats through, and delivers what it held for them; the session that gave
the estimate is still held. Against the fake `claude` (test_headless.py's
`host`); nothing here reaches a real session.
"""

from __future__ import annotations

import time

from agent_media_core import restart_services as rs
from agent_media_server import driver, sessiond

from test_headless import host, start, wait_for  # noqa: F401
from test_sessiond_restart import _fresh, _pend, _said


def _window(long: str, left: float) -> None:
    rs._update_pending(lambda r: {**r, "waiting_on": [{"session": long, "kind": "turn",
                                                        "label": "long", "why": "w"}],
                                  "etas": {long: {"until": time.time() + left, "at": time.time(),
                                                  "what": "a migration"}}})


def test_an_open_window_lets_quick_work_in_and_holds_the_long_job(host):
    sid = start(host, "reply: a")
    long = start(host, "reply: b")
    wait_for(lambda: _said(host.sup, sid) == "a" and _said(host.sup, long) == "b")
    _pend()
    _window(long, 600)
    _fresh(host.sup)
    assert not host.sup.send(sid, "reply: quick").get("held")
    wait_for(lambda: _said(host.sup, sid) == "quick")
    assert host.sup.send(long, "reply: more")["held"] is True
    ok, d = driver.headless_driver().start(agent="claude", cwd=str(host.work), text="reply: new")
    assert ok and not d.get("held")
    wait_for(lambda: _said(host.sup, d["session"]) == "new")
    view = sessiond.restart_view()
    assert view["window"] is True and view["eta"] > time.time() + 500


def test_held_messages_go_when_a_window_opens_and_are_held_again_as_it_closes(host):
    sid = start(host, "reply: a")
    long = start(host, "reply: b")
    wait_for(lambda: _said(host.sup, sid) == "a" and _said(host.sup, long) == "b")
    _pend()
    _fresh(host.sup)
    assert host.sup.send(sid, "reply: waited")["held"] is True
    assert host.sup.deliver_due() == []
    _window(long, 600)
    _fresh(host.sup)
    assert host.sup.deliver_due() == [sid]
    wait_for(lambda: _said(host.sup, sid) == "waited")
    assert sessiond.read_record(sid)["held"] == []
    # Under the window's tail: held again, and the view says it is closed.
    _window(long, 60)
    _fresh(host.sup)
    assert host.sup.send(sid, "reply: late")["held"] is True
    assert sessiond.restart_view()["window"] is False


def test_a_session_cut_off_for_the_drain_waits_for_the_restart(host):
    sid = start(host, "reply: a")
    long = start(host, "reply: b")
    wait_for(lambda: _said(host.sup, sid) == "a" and _said(host.sup, long) == "b")
    _pend()
    _fresh(host.sup)
    assert host.sup.send(sid, "reply: kept")["held"] is True
    with host.sup.lock:
        host.sup.sessions[sid].cut_off = {"why": "drain", "due": True, "restart": "r1"}
    _window(long, 600)
    _fresh(host.sup)
    assert host.sup.deliver_due() == []
    assert [m["text"] for m in sessiond.read_record(sid)["held"]] == ["reply: kept"]
