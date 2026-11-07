"""GET /speech/recent: the full player's Recent replies (8 Nov 2026)."""

from __future__ import annotations

import json
import time

from agent_media_server import auth_abs, sessions, speech

A = "6c73498c-02c1-4846-8350-a82006973571"
B = "7d84509d-13d2-4957-9c9b-b93117084682"
ROOT = {"username": "david", "type": "root"}


def _as(monkeypatch, user=ROOT, status=200):
    monkeypatch.setattr(auth_abs, "abs_identity", lambda bearer: (user, status))
    monkeypatch.setattr(sessions, "title_of", lambda s, **k: {A: "Thread A", B: "Thread B"}.get(s, ""))


def _add(text, session=A, at=None, source="claude-code", **extras):
    from agent_media_core.state import StateStore

    ex = {"source_session": session, "session": session, **extras}
    return StateStore().add_history(sink="speech", uri=f"/tmp/{text[:8]}.mp3",
                                    started_at=at or time.time(), source=source,
                                    text=text, extras=ex)


def test_replies_newest_first_with_their_state(monkeypatch):
    _as(monkeypatch)
    t = time.time() - 100
    heard = _add("The first reply.", at=t, kind="stop", heard=True,
                 clip_durations_s=[1.0, 2.0])
    held = _add("A held reply.", session=B, at=t + 1, kind="stop", held=True)
    stopped = _add("Stopped part-way.", at=t + 2, kind="stop",
                   stopped_at={"sentence": 1, "at": t + 3}, clip_durations_s=[1.5, 2.5])
    ok, out = speech.speech_recent("tok")
    assert ok
    ids = [i["id"] for i in out["items"]]
    assert ids == [stopped, held, heard]
    s, h, d = out["items"]
    assert d["heard"] and "unheard" not in d and d["dur_s"] == 3.0 and d["title"] == "Thread A"
    assert h["unheard"] and not h["heard"] and h["title"] == "Thread B" and h["kind"] == "reply"
    assert s["resume"] == {"sentence": 1, "at_s": 1.5, "dur_s": 4.0} and not s["heard"]
    json.dumps(out)


def test_what_is_not_a_reply_is_left_out(monkeypatch):
    _as(monkeypatch)
    _add("You: hello", source="listener", listener=True)
    _add("Claude is waiting.", kind="notif")
    _add("Heads up.", source="cli")
    _add("While you were away.", session="", source="watcher", kind="catchup")
    q = _add("Which one?", kind="notif", ask=[{"question": "Which one?"}])
    m = _add("Sam: hi there", session=B, source="matrix", held=True)
    ok, out = speech.speech_recent("tok")
    assert [(i["id"], i["kind"]) for i in out["items"]] == [(m, "message"), (q, "question")]


def test_private_threads_and_copies_are_left_out(monkeypatch, tmp_path):
    _as(monkeypatch)
    import os

    d = tmp_path / "private.d"
    d.mkdir(exist_ok=True)
    (d / B).write_text("{}")
    assert os.environ["AGENT_PRIVATE_DIR"] == str(d)
    t = time.time() - 50
    _add("Secret words.", session=B, at=t, kind="stop", heard=True)
    _add("Same reply.", at=t + 1, kind="stop", held=True, dedup_key="k1")
    spoken = _add("Same reply.", at=t + 2, kind="stop", heard=True, dedup_key="k1")
    ok, out = speech.speech_recent("tok")
    assert [i["id"] for i in out["items"]] == [spoken]
    assert out["items"][0]["heard"]


def test_limit_and_refusals(monkeypatch):
    _as(monkeypatch)
    t = time.time() - 50
    for k in range(5):
        _add(f"Reply {k}.", at=t + k, kind="stop", heard=True)
    assert len(speech.speech_recent("tok", "2")[1]["items"]) == 2
    assert len(speech.speech_recent("tok", "999")[1]["items"]) == 5
    ok, out = speech.speech_recent("tok", "many")
    assert not ok and out["status"] == 400
    _as(monkeypatch, user=None, status=401)
    ok, out = speech.speech_recent("")
    assert not ok and out.get("status") in (401, 403)
