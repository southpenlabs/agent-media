"""Settings → Prompts, read-only (prompt_settings.py, §6.57; David, 16 Nov
2026: "Maybe they should be viewable and configurable from the settings page
somewhere")."""

from __future__ import annotations

import pytest

from agent_media_server import auth, personas, prompt_settings as ps, quick, tidy_intents


@pytest.fixture()
def who(monkeypatch):
    users = {"admin": {"username": "d"}, "guest": {"username": "g", "device": "y", "role": "guest"}}
    monkeypatch.setattr(auth, "gate", lambda b: (users[b], {}) if b in users
                        else (None, {"status": 401, "code": "not_paired", "error": "no"}))
    monkeypatch.setattr(auth, "is_guest", lambda b: b == "guest")
    monkeypatch.setattr(personas, "described", lambda: [
        {"id": "sam", "kind": "persona", "adapters": None, "projects": None, "private": True},
        {"id": "reviewer", "kind": "role", "adapters": None, "projects": None, "private": False}])
    rendered = []

    def render(persona, role, agent, mode="public"):
        rendered.append((persona, role, agent, mode))
        return True, f"render of {persona or role} for {agent}\n"

    monkeypatch.setattr(personas, "render", render)
    for name in ("MEDIA_AGENT_NOTES_FILE", "MEDIA_AGENT_NOTES", "MEDIA_SUMMARY_PROMPT",
                 "MEDIA_DESCRIBE_CODE_PROMPT", "MEDIA_DESCRIBE_TABLE_PROMPT"):
        monkeypatch.delenv(name, raising=False)
    return rendered


def _by_key(d):
    return {p["key"]: p for p in d["prompts"]}


def test_every_prompt_is_listed_in_full(who):
    ok, d = ps.settings("admin")
    assert ok
    p = _by_key(d)
    assert set(p) == {"writing", "quick", "tidy", "summary"}
    assert p["quick"]["text"] == quick.SYSTEM_PROMPT
    # The writing guide: its rules with their tags, the markers flagged.
    w = p["writing"]
    assert w["text"].startswith("How to write for this user") and w["parts"]
    assert {r["label"] for r in w["parts"]} >= {"all", "claude"}
    replies = next(r for r in w["parts"] if "[[replies:" in r["text"])
    assert "[[replies:" in replies["parsed"]
    assert any(a["label"] == "Thread link" for a in w["also"])
    # Tidy: one prompt per chip, each with the RULES and the JSON answer.
    t = p["tidy"]
    assert [x["key"] for x in t["parts"]] == [i.key for i in tidy_intents.INTENTS] + ["draft"]
    assert all('"messy"' in x["text"] for x in t["parts"] if x["key"] != "draft")
    assert '"draft"' in t["parts"][-1]["text"]
    # Summary and the describers, none overridden.
    assert [x["key"] for x in p["summary"]["parts"]] == ["summary", "code", "table"]
    assert p["summary"]["overridden"] is None
    assert d["personas"] == [{"id": "sam", "kind": "persona", "private": True},
                             {"id": "reviewer", "kind": "role", "private": False}]


def test_an_override_on_this_server_is_what_is_shown(who, monkeypatch, tmp_path):
    monkeypatch.setenv("MEDIA_SUMMARY_PROMPT", "Say it in one line.")
    notes = tmp_path / "notes.md"
    notes.write_text("# mine\n\n- [all] Be brief.\n- [tune] Sing.\nnot a rule\n")
    monkeypatch.setenv("MEDIA_AGENT_NOTES_FILE", str(notes))
    p = _by_key(ps.settings("admin")[1])
    assert p["summary"]["text"] == "Say it in one line." and p["summary"]["overridden"]
    assert p["writing"]["overridden"] == "MEDIA_AGENT_NOTES_FILE"
    assert [(r["label"], r["text"]) for r in p["writing"]["parts"]] == [("all", "Be brief."),
                                                                          ("tune", "Sing.")]


def test_a_persona_is_rendered_on_request_in_public_mode(who):
    ok, d = ps.settings("admin", {"persona": "sam", "agent": "codex"})
    assert ok and d["text"].startswith("render of sam for codex") and d["via"] == "prepend"
    ok, d = ps.settings("admin", {"persona": "reviewer"})
    assert ok and d["agent"] == "claude" and d["via"] == "system_prompt"
    assert who == [("sam", "", "codex", "public"), ("", "reviewer", "claude", "public")]
    assert ps.settings("admin", {"persona": "nobody"})[1]["status"] == 404
    assert ps.settings("admin", {"persona": "sam", "agent": "vim"})[1]["status"] == 400


def test_a_guest_sees_the_prompts_but_no_personas_and_a_stranger_nothing(who):
    ok, d = ps.settings("guest")
    assert ok and d["personas"] == [] and _by_key(d)["quick"]["text"]
    ok, d = ps.settings("guest", {"persona": "sam"})
    assert not ok and d["status"] == 403 and who == []
    ok, d = ps.settings("stranger")
    assert not ok and d["status"] == 401
