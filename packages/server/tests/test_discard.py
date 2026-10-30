"""Taking back a new chat just started (discard.py): only one /ask started
here moments ago, and it is stopped, closed and deleted."""
from agent_media_server import auth, discard, forget, send, stop

S = "01a0d3d4-9c5f-7ea0-a7d6-060e7d7fcc2f"


def _fakes(monkeypatch):
    done = []
    monkeypatch.setattr(auth, "gate", lambda bearer: ("me", None) if bearer == "ok" else (None, {"error": "unpaired", "status": 401}))
    monkeypatch.setattr(stop, "session_stop", lambda s, mode, b: done.append(("stop", s, mode)) or (True, {}))
    monkeypatch.setattr(send, "session_close", lambda s, b: done.append(("close", s)) or (True, {}))
    monkeypatch.setattr(forget, "apply", lambda ss: done.append(("delete", *ss)) or {})
    monkeypatch.setattr(discard, "_started", {})
    return done


def test_a_chat_just_started_is_stopped_closed_and_deleted(monkeypatch):
    done = _fakes(monkeypatch)
    discard.note_started(S)
    ok, detail = discard.discard(S, "ok")
    assert ok and detail == {"session": S, "discarded": True}
    assert done == [("stop", S, "silence"), ("close", S), ("delete", S)]
    # Once only.
    ok, detail = discard.discard(S, "ok")
    assert not ok and detail["status"] == 409


def test_any_other_thread_is_refused(monkeypatch):
    done = _fakes(monkeypatch)
    ok, detail = discard.discard(S, "ok")
    assert not ok and detail["status"] == 409 and done == []


def test_too_old_is_refused(monkeypatch):
    done = _fakes(monkeypatch)
    discard.note_started(S)
    discard._started[S] -= discard.WINDOW + 1
    ok, detail = discard.discard(S, "ok")
    assert not ok and detail["status"] == 409 and done == []


def test_unpaired_and_bad_ids(monkeypatch):
    done = _fakes(monkeypatch)
    discard.note_started(S)
    assert discard.discard(S, "no")[1]["status"] == 401
    assert discard.discard("x", "ok")[1]["status"] == 400
    assert done == [] and S in discard._started
