"""`POST /speech/say {"text"}` (server-contract.md, `POST /speech/say`): the app's own words,
read aloud on a tap — What's new's Play. `media say` is recorded, never run."""

from __future__ import annotations

import subprocess

from agent_media_server import app as canvas_app

from test_contract import AUTH, call, server, signed_in, typed  # noqa: F401


def test_words_are_said_privately_and_capped(server, signed_in, monkeypatch):
    said: list = []

    class Child:
        def __init__(self, args, **kw):
            said.append(list(args))
            self.stdin = self

        def write(self, b):
            said.append(b.decode())

        def close(self):
            pass

    monkeypatch.setattr(subprocess, "Popen", Child)
    res, obj = call(server, "POST", "/speech/say", {"text": "What's new. Music comes back."}, headers=AUTH)
    assert res.status == 200 and obj["chars"] == len("What's new. Music comes back.")
    # Played, never kept in speech history.
    assert said[0][-2:] == ["say", "--private"] and said[1] == "What's new. Music comes back."
    said.clear()
    res, obj = call(server, "POST", "/speech/say", {"text": "x" * (canvas_app.SAY_MAX + 50)}, headers=AUTH)
    assert res.status == 200 and obj["chars"] == canvas_app.SAY_MAX and len(said[1]) == canvas_app.SAY_MAX
    res, _ = call(server, "POST", "/speech/say", {"text": "  "}, headers=AUTH)
    assert res.status == 400


def test_needs_a_credential(server, monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("spoke")))
    res, _ = call(server, "POST", "/speech/say", {"text": "hi"})
    assert res.status in (401, 403)
