"""A space suggested for an Inbox chat (space_suggest.py; one-tap answers part 4)."""

from __future__ import annotations

import pytest

from agent_media_server import contacts, space_suggest, spaces, tags, threads

LABELS = {"labels": [{"name": "Book club", "count": 4}]}
ROWS = [{"session": "p1", "harness": "claude", "project": "spl/sasonica-app", "tags": [], "at": 1},
        {"session": "q1", "harness": "quick", "tags": [], "title": "Sasonica app colours", "at": 5},
        {"session": "q2", "harness": "quick", "tags": [], "title": "What to read", "at": 6}]


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setenv("MEDIA_SPACE_SUGGEST", "1")
    monkeypatch.setattr(contacts, "labels", lambda: LABELS)
    monkeypatch.setattr(space_suggest, "_private", lambda t: False)
    space_suggest._reset_for_tests()
    return [dict(r) for r in ROWS]


def _model(monkeypatch, answer):
    from agent_media_core.intake import fast

    seen = []
    def chat(system, user, timeout, **kw):
        seen.append((user, kw))
        return answer
    monkeypatch.setattr(fast, "chat", chat)
    monkeypatch.setattr(fast, "models", lambda *a, **k: ["m"])
    return seen


def test_a_name_said_needs_no_model_and_a_model_picks_only_from_the_list(on, monkeypatch):
    monkeypatch.setattr(threads, "_conversation_text", lambda s: {
        "q1": "Can the sasonica-app header be green?", "q2": "Ideas for the next read?"}[s])
    seen = _model(monkeypatch, "Book club")
    rows = spaces.add_spaces(on)
    space_suggest.run(rows)
    assert len(seen) == 1 and "q2" not in seen[0][0] and "Ideas" in seen[0][0]
    space_suggest.add_suggestions(rows)
    by = {r["session"]: r.get("space_suggestion") for r in rows}
    assert by["p1"] is None
    assert by["q1"] == {"space": "project:spl/sasonica-app", "name": "spl/sasonica-app",
                        "tag": "sasonica_app"}
    assert by["q2"] == {"space": "group:book_club", "name": "Book club", "tag": "Book club"}
    # Nothing new in a chat: not asked again.
    space_suggest.run(rows)
    assert len(seen) == 1


def test_a_made_up_space_is_none(on, monkeypatch):
    monkeypatch.setattr(threads, "_conversation_text", lambda s: "hello there")
    _model(monkeypatch, "Gardening")
    rows = spaces.add_spaces(on)
    space_suggest.run(rows)
    space_suggest.add_suggestions(rows)
    # q1's title names the project ("Sasonica app"); q2's model said a space
    # that is not there.
    assert [r["session"] for r in rows if r.get("space_suggestion")] == ["q1"]


def test_a_private_chat_with_no_private_model_gets_the_word_match_only(on, monkeypatch):
    from agent_media_core.intake import fast

    monkeypatch.setattr(space_suggest, "_private", lambda t: True)
    monkeypatch.setattr(fast, "models", lambda *a, private=False, **k: [] if private else ["m"])
    monkeypatch.setattr(fast, "chat", lambda *a, **k: pytest.fail("a private chat went to a model"))
    monkeypatch.setattr(threads, "_conversation_text", lambda s: "nothing named")
    rows = spaces.add_spaces(on)
    space_suggest.run(rows)


def test_accept_files_it_and_dismiss_is_remembered(on, monkeypatch):
    from agent_media_server import auth, sessions

    monkeypatch.setattr(auth, "gate", lambda b: ("me", None))
    monkeypatch.setattr(sessions, "sessions_index", lambda: [dict(r) for r in ROWS])
    space_suggest._jsonmaps()[0].put("q1", {"space": "project:spl/sasonica-app", "seen": 5})
    space_suggest._jsonmaps()[0].put("q2", {"space": "group:book_club", "seen": 6})
    sent = {}
    def session_tags(session, body, bearer):
        sent.update(session=session, body=body)
        return True, {"tags": body["add"]}
    monkeypatch.setattr(tags, "session_tags", session_tags)
    ok, out = space_suggest.post({"session": "q1", "accept": True}, "tok")
    assert ok and out["filed"] == "project:spl/sasonica-app"
    assert sent["body"] == {"session": "q1", "add": ["sasonica_app"], "hold": True}
    ok, out = space_suggest.post({"session": "q2", "dismiss": True}, "tok")
    assert ok and space_suggest.dismissed("q2") == ["group:book_club"]
    rows = spaces.add_spaces([dict(r) for r in ROWS])
    space_suggest.add_suggestions(rows)
    assert not any(r.get("space_suggestion") for r in rows)
    assert space_suggest.post({"session": "q2", "dismiss": True}, "tok")[1]["status"] == 404
    assert space_suggest.post({"session": "q1"}, "tok")[1]["status"] == 400


def test_off_adds_nothing_and_starts_nothing(monkeypatch):
    monkeypatch.setattr(space_suggest, "kick", lambda rows: pytest.fail("started"))
    rows = spaces.add_spaces([dict(r) for r in ROWS])
    assert space_suggest.add_suggestions(rows) == rows
