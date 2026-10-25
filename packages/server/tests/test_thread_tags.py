"""Thread tags (`tags.py`): read from and written to the thread's org-roam
node, over real HTTP with the contract rig. The org root is a throwaway
(conftest sets MEDIA_THREAD_TAGS_DIR), never the real ~/org.
"""

from __future__ import annotations

import os

from agent_media_server import app, tags

from test_contract import (AUTH, SID, SID2, call, server, shelf,  # noqa: F401
                           signed_in, typed)


def _node(sid: str, filetags: str, *, harness="claude", hub="home_ryer_projects_agent_media",
          sub="inbox") -> "os.PathLike":
    d = tags.root() / sub
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"20261024T000000=={sid[:8]}--a-thread__inbox.org"
    p.write_text(
        ":PROPERTIES:\n"
        f":ID:             {harness}:red5:{sid}\n"
        f":AGENT_HARNESS:  {harness}\n"
        f":AGENT_SID:      {sid}\n"
        ":END:\n"
        "#+title: a thread\n"
        f"#+filetags: {filetags}\n\n"
        f"- Project :: [[id:{harness}-proj-{hub}][agent-media]]\n\n"
        "# >>> agent-sessions export roam: auto (regenerated, do not edit) >>>\n"
        "# <<< agent-sessions export roam: auto <<<\n\n"
        "* Notes\n:tags: in the body are not tags\n")
    return p


def _row(obj: dict, sid: str) -> dict:
    return {r["session"]: r for r in obj["sessions"]}[sid]


def test_rows_carry_only_your_tags(server, shelf, signed_in):
    _node(SID, ":claude:inbox:home_ryer_projects_agent_media:work:voice:")
    _, targets = call(server, "GET", "/targets", headers=AUTH)
    assert _row(targets, SID)["tags"] == ["work", "voice"]
    assert _row(targets, SID2)["tags"] == []           # no node, no tags
    _, conv = call(server, "GET", "/conversations", headers=AUTH)
    assert _row(conv, SID)["tags"] == ["work", "voice"]


def test_add_and_remove_keep_the_exporters_tags_and_the_rest_of_the_file(
        server, shelf, signed_in):
    p = _node(SID, ":claude:inbox:home_ryer_projects_agent_media:work:")
    before = p.read_text()
    res, obj = call(server, "POST", "/session/tags",
                    {"session": SID, "add": ["Big idea", "work"], "remove": []}, AUTH)
    assert res.status == 200, obj
    assert obj == {"ok": True, "session": SID, "tags": ["work", "Big_idea"]}
    after = p.read_text()
    assert "#+filetags: :claude:inbox:home_ryer_projects_agent_media:work:Big_idea:\n" in after
    assert after.replace(":Big_idea", "") == before   # one line changed, nothing else
    res, obj = call(server, "POST", "/session/tags", {"session": SID, "remove": "work"}, AUTH)
    assert obj["tags"] == ["Big_idea"]
    res, obj = call(server, "POST", "/session/tags", {"session": SID, "tags": []}, AUTH)
    assert obj["tags"] == []
    assert "#+filetags: :claude:inbox:home_ryer_projects_agent_media:\n" in p.read_text()


def test_a_hand_edit_is_seen_on_the_next_list(server, shelf, signed_in):
    p = _node(SID, ":claude:inbox:home_ryer_projects_agent_media:")
    _, targets = call(server, "GET", "/targets", headers=AUTH)
    assert _row(targets, SID)["tags"] == []
    p.write_text(p.read_text().replace("agent_media:\n", "agent_media:emacs:\n"))
    os.utime(p, ns=(1, 1))                              # a different mtime, whatever the clock
    _, targets = call(server, "GET", "/targets", headers=AUTH)
    assert _row(targets, SID)["tags"] == ["emacs"]


def test_tag_counts(server, shelf, signed_in):
    _node(SID, ":claude:inbox:home_ryer_projects_agent_media:work:voice:")
    _node(SID2, ":pi:agent_media:work:", harness="pi", hub="agent_media", sub="agent-media")
    res, obj = call(server, "GET", "/tags", headers=AUTH)
    assert res.status == 200
    assert obj == {"ok": True, "tags": [{"tag": "work", "count": 2}, {"tag": "voice", "count": 1}]}


def test_refusals(server, shelf, signed_in):
    res, obj = call(server, "POST", "/session/tags", {"session": "nope", "add": ["x"]}, AUTH)
    assert res.status == 400 and obj["error"] == "not a session id"
    res, obj = call(server, "POST", "/session/tags", {"session": SID, "add": ["no:colons"]}, AUTH)
    assert res.status == 400 and obj["error"].startswith("not a tag")
    res, obj = call(server, "POST", "/session/tags", {"session": SID}, AUTH)
    assert res.status == 400
    # A thread the exporter has not written yet: nothing to edit.
    res, obj = call(server, "POST", "/session/tags", {"session": SID, "add": ["x"]}, AUTH)
    assert res.status == 409 and "org files" in obj["error"]


def test_tags_are_gated(server, shelf, monkeypatch):
    from agent_media_server import auth_abs

    p = _node(SID, ":claude:inbox:")
    before = p.read_text()
    monkeypatch.setattr(auth_abs, "abs_urls", lambda: ["http://abs"])
    monkeypatch.setattr(auth_abs, "_abs_get", lambda *a, **k: (None, 401))
    res, obj = call(server, "POST", "/session/tags", {"session": SID, "add": ["x"]})
    assert res.status == 401 and p.read_text() == before
    res, obj = call(server, "GET", "/tags")
    assert res.status == 401


def test_clean():
    assert tags.clean("#Deep work ") == "Deep_work"
    assert tags.clean("a-b") == "a_b"
    assert tags.clean("") is None and tags.clean("x" * 41) is None and tags.clean(3) is None


def test_open_to_the_web_client():
    assert {"/session/tags", "/tags"} <= app.CORS_PATHS


def test_older_exports_tags_are_not_yours(server, shelf, signed_in):
    # A thread moved since its export: a path tag, the hub's name, the
    # directory's name — all the exporter's.
    p = _node(SID, ":claude:inbox:home_ryer_projects_agent_media:scratch:ryer:mine:",
              hub="home_ryer_scratch")
    p.write_text(p.read_text().replace("][agent-media]]", "][ryer]]").replace(
        ":END:", ":AGENT_CWD:      /home/ryer/scratch\n:END:"))
    _, targets = call(server, "GET", "/targets", headers=AUTH)
    assert _row(targets, SID)["tags"] == ["mine"]
    res, obj = call(server, "POST", "/session/tags", {"session": SID, "add": ["home_x"]}, AUTH)
    assert res.status == 400


# --- suggestions (Auto) ---------------------------------------------------------

def _fake_model(monkeypatch, answer, seen=None):
    from agent_media_core.intake import _summary
    from agent_media_server import threads

    monkeypatch.setattr(threads, "_conversation_text", lambda s: "you: the mic cuts out\n\nassistant: fixed")

    def chat(system, user, timeout, model=None):
        if seen is not None:
            seen.append(user)
        return answer
    monkeypatch.setattr(_summary, "_chat", chat)


def test_suggest_prefers_tags_in_use_and_saves_nothing(server, shelf, signed_in, monkeypatch):
    p = _node(SID, ":claude:inbox:home_ryer_projects_agent_media:voice:")
    _node(SID2, ":claude:inbox:home_ryer:phone:", sub="claude")
    before = p.read_text()
    seen: list[str] = []
    _fake_model(monkeypatch, "Tags: Phone, voice, #Mic Bugs, claude, home_ryer, x:y, a, b, c", seen)
    res, obj = call(server, "POST", "/session/tags/suggest", {"session": SID}, AUTH)
    assert res.status == 200, obj
    # Its own (voice), the exporter's, a path tag and a bad one are left out; four at most.
    assert obj == {"ok": True, "session": SID, "tags": ["phone", "mic_bugs", "a", "b"]}
    assert "Existing tags: " in seen[0] and "phone" in seen[0] and "voice" in seen[0]
    assert p.read_text() == before                      # nothing saved


def test_suggest_refusals(server, shelf, signed_in, monkeypatch):
    res, obj = call(server, "POST", "/session/tags/suggest", {"session": "nope"}, AUTH)
    assert res.status == 400
    _fake_model(monkeypatch, None)                      # no gateway, or no answer
    res, obj = call(server, "POST", "/session/tags/suggest", {"session": SID}, AUTH)
    assert res.status == 503 and obj["error"] == "no tags to suggest"
    from agent_media_core import private

    _fake_model(monkeypatch, "phone")
    monkeypatch.setattr(private, "hidden", lambda s: True)
    res, obj = call(server, "POST", "/session/tags/suggest", {"session": SID}, AUTH)
    assert res.status == 503                            # a private thread is never read out


def test_suggest_is_gated(server, shelf, monkeypatch):
    from agent_media_server import auth_abs

    monkeypatch.setattr(auth_abs, "abs_urls", lambda: ["http://abs"])
    monkeypatch.setattr(auth_abs, "_abs_get", lambda *a, **k: (None, 401))
    res, obj = call(server, "POST", "/session/tags/suggest", {"session": SID})
    assert res.status == 401
    assert "/session/tags/suggest" in app.CORS_PATHS


def test_tags_in_use_are_matched_with_no_model(server, shelf, signed_in, monkeypatch):
    _node(SID, ":claude:inbox:home_ryer_projects_agent_media:")
    _node(SID2, ":claude:inbox:home_ryer:full_screen:mic:", sub="claude")
    _fake_model(monkeypatch, None)                      # no model answers
    res, obj = call(server, "POST", "/session/tags/suggest", {"session": SID}, AUTH)
    # "mic" is in the thread's words; "full_screen" is not, nor its title's.
    assert res.status == 200 and obj["tags"] == ["mic"]


def test_a_private_thread_never_reaches_a_hosted_model(server, shelf, signed_in, monkeypatch):
    from agent_media_core import private

    _node(SID2, ":claude:inbox:home_ryer:mic:", sub="claude")
    seen: list[str] = []
    _fake_model(monkeypatch, "hosted_guess", seen)
    monkeypatch.setattr(private, "hidden", lambda s: s == SID)
    monkeypatch.setenv("MEDIA_FAST_MODELS", "hosted")
    res, obj = call(server, "POST", "/session/tags/suggest", {"session": SID}, AUTH)
    assert obj["tags"] == ["mic"] and seen == []        # matched only, no model read it
    monkeypatch.setenv("MEDIA_PRIVATE_MODELS", "local")
    res, obj = call(server, "POST", "/session/tags/suggest", {"session": SID}, AUTH)
    assert obj["tags"] == ["mic", "hosted_guess"] and len(seen) == 1


# --- by meaning (tag_embed.py), the child process faked ----------------------------

def _fake_embedder(monkeypatch, table: dict):
    """Each text's vector from `table` by a word it holds, else far from all."""
    from agent_media_server import tag_embed

    monkeypatch.setenv("MEDIA_TAG_EMBED", "1")
    monkeypatch.setattr(tag_embed, "available", lambda: True)
    calls: list = []

    def child(texts):
        calls.append(list(texts))
        out = []
        for t in texts:
            v = next((vec for word, vec in table.items() if word in t.lower()), [0.0, 0.0, 1.0])
            out.append(v)
        return out
    monkeypatch.setattr(tag_embed, "_embed_in_child", child)
    return calls


def test_a_tag_that_fits_by_meaning(server, shelf, signed_in, monkeypatch):
    _node(SID, ":claude:inbox:home_ryer_projects_agent_media:")
    _node(SID2, ":claude:inbox:home_ryer:voice:calendar:", sub="claude")
    _fake_model(monkeypatch, None)                      # "you: the mic cuts out ..."
    # "voice" means what "mic" does; "calendar" does not.
    calls = _fake_embedder(monkeypatch, {"mic": [1.0, 0.0, 0.0], "voice": [0.9, 0.1, 0.0],
                                         "calendar": [0.0, 1.0, 0.0]})
    res, obj = call(server, "POST", "/session/tags/suggest", {"session": SID}, AUTH)
    assert res.status == 200 and obj["tags"] == ["voice"]
    # Cached: a second ask embeds nothing new.
    n = len(calls)
    call(server, "POST", "/session/tags/suggest", {"session": SID}, AUTH)
    assert len(calls) == n


def test_embeddings_failing_fall_back_to_words(server, shelf, signed_in, monkeypatch):
    from agent_media_server import tag_embed

    _node(SID, ":claude:inbox:home_ryer_projects_agent_media:")
    _node(SID2, ":claude:inbox:home_ryer:mic:", sub="claude")
    _fake_model(monkeypatch, None)
    _fake_embedder(monkeypatch, {})
    monkeypatch.setattr(tag_embed, "_embed_in_child", lambda texts: None)
    res, obj = call(server, "POST", "/session/tags/suggest", {"session": SID}, AUTH)
    assert obj["tags"] == ["mic"]                       # the word match still stands
