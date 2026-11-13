"""The follow-up: a one-line next prompt filed per session (intake/_followup.py)."""

from agent_media_core.intake import _followup as fu
from agent_media_core.intake import _summary


def _state(monkeypatch, tmp_path):
    monkeypatch.setattr(fu, "state_dir", lambda: tmp_path)


def test_suggest_files_the_cleaned_line(monkeypatch, tmp_path):
    _state(monkeypatch, tmp_path)
    seen = {}
    def chat(p, t, timeout, model=None):
        seen["model"] = model
        return '"Next: go with projects, do the relabel."\n'
    monkeypatch.setattr(_summary, "_chat", chat)
    monkeypatch.setenv("MEDIA_FOLLOWUP_MODEL", "tiny")
    out = fu.suggest_followup("A long reply.", "sess-1", "k1")
    assert out == "go with projects, do the relabel."
    assert seen["model"] == "tiny"
    got = fu.load_followup("sess-1")
    assert got == {"text": out, "replies": [out], "source": "model", "key": "k1",
                   "at": got["at"]}


def test_a_failed_call_files_nothing_and_keeps_the_last(monkeypatch, tmp_path):
    _state(monkeypatch, tmp_path)
    fu.save_followup("sess-1", "earlier", "k0")
    monkeypatch.setattr(_summary, "_chat", lambda p, t, timeout, model=None: None)
    assert fu.suggest_followup("A reply.", "sess-1", "k1") is None
    assert fu.load_followup("sess-1")["key"] == "k0"


def test_an_essay_is_not_a_prompt(monkeypatch, tmp_path):
    _state(monkeypatch, tmp_path)
    monkeypatch.setattr(_summary, "_chat", lambda p, t, timeout, model=None: "x" * 300)
    assert fu.suggest_followup("A reply.", "sess-1", "k1") is None


def test_switched_off_spawns_nothing(monkeypatch):
    monkeypatch.setenv("MEDIA_FOLLOWUP", "1")
    assert fu.followup_enabled() is True
    monkeypatch.setenv("MEDIA_FOLLOWUP", "0")
    called = []
    monkeypatch.setattr(fu.threading, "Thread", lambda *a, **k: called.append(1))
    fu.spawn_followup("reply", "sess-1", "k1")
    assert not called
    assert fu.followup_enabled() is False


def test_no_session_no_file(monkeypatch, tmp_path):
    _state(monkeypatch, tmp_path)
    assert fu.load_followup("") is None
    fu.save_followup("", "text", "k")
    assert not list(tmp_path.glob("**/*.json"))


def test_up_to_three_lines_become_the_chips(monkeypatch, tmp_path):
    _state(monkeypatch, tmp_path)
    monkeypatch.setattr(_summary, "_chat", lambda p, t, timeout, model=None:
                        "1. commit it\n2) run the tests first\n- commit it\n\n"
                        "* leave it for now\nfourth one")
    assert fu.suggest_followup("A reply.", "sess-1", "k1") == "commit it"
    got = fu.load_followup("sess-1")
    assert got["replies"] == ["commit it", "run the tests first", "leave it for now"]
    assert got["text"] == "commit it"


def test_the_agents_replies_are_filed_as_they_are(monkeypatch, tmp_path):
    _state(monkeypatch, tmp_path)
    fu.save_followup("sess-1", "Yes", "k2", replies=["Yes", "No"], source="agent")
    got = fu.load_followup("sess-1")
    assert (got["replies"], got["source"], got["key"]) == (["Yes", "No"], "agent", "k2")


def test_a_file_from_before_the_chips_reads_as_one(tmp_path, monkeypatch):
    _state(monkeypatch, tmp_path)
    p = fu._path("sess-1")
    p.parent.mkdir(parents=True)
    p.write_text('{"text": "old line", "key": "k0", "at": 1}')
    got = fu.load_followup("sess-1")
    assert got["replies"] == ["old line"] and got["source"] == "model"


def test_the_agents_actions_are_filed_beside_the_follow_up(monkeypatch, tmp_path):
    _state(monkeypatch, tmp_path)
    acts = [{"label": "Listen", "verb": "listen", "target": "docs/x.md"}]
    fu.save_actions("sess-1", acts, "k3")
    # the model's follow-up written after it leaves the actions alone
    fu.save_followup("sess-1", "next", "k3")
    got = fu.load_actions("sess-1")
    assert (got["actions"], got["key"]) == (acts, "k3")
    assert fu.load_actions("other") is None
    fu.save_actions("", acts, "k")
    assert fu.load_actions("") is None


def test_a_short_reply_is_framed_and_the_models_meta_talk_dropped(monkeypatch, tmp_path):
    """14 Nov 2026: "You're welcome. Enjoy the bigger text." came back as a
    chip asking for the conversation — the reply is now framed, and a line
    longer than a chip can be is not filed."""
    _state(monkeypatch, tmp_path)
    seen = {}
    def chat(p, t, timeout, model=None):
        seen["text"] = t
        return ("I need to see the coding assistant's latest reply to predict what "
                "the person would say next. Please share the conversation you'd "
                "like me to continue from.\nthanks")
    monkeypatch.setattr(_summary, "_chat", chat)
    assert fu.suggest_followup("You're welcome. Enjoy the bigger text.", "sess-1", "k1") == "thanks"
    assert seen["text"] == "<reply>\nYou're welcome. Enjoy the bigger text.\n</reply>"
    assert fu.load_followup("sess-1")["replies"] == ["thanks"]
