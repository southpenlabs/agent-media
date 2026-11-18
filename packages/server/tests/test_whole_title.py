"""A list's titles stop at 60 characters; an open thread's header gets the
whole one (David, 12 Nov 2026: "the title probably shouldn't be truncated in
the extended view"): `sessions._cut` keeps it, `title_of(whole=True)` gives it."""
from agent_media_server import sessions

SID = "18136e77-b9ac-43ba-878f-ece77a68846c"
LONG = "[From Sam] Fewer services: look at agent-media's unit count and say which ones can go"


def test_a_long_title_is_cut_for_the_list_and_whole_for_the_header(monkeypatch):
    short = sessions._cut(SID, LONG)
    assert len(short) <= 60 and short.endswith("…") and LONG.startswith(short[:-1])
    assert short == "[From Sam] Fewer services: look at agent-media's unit count…"  # at a word
    monkeypatch.setattr(sessions, "cached_states", lambda: ([{"session": SID, "title": short}], {}))
    assert sessions.title_of(SID) == short
    assert sessions.title_of(SID, whole=True) == LONG
    assert sessions.title_of(SID, live_only=True, whole=True) == LONG


def test_a_short_title_or_a_rename_is_left_as_it_is(monkeypatch):
    sessions._cut(SID, LONG)
    # Renamed since to something short: the old whole one is forgotten.
    assert sessions._cut(SID, "Fewer services") == "Fewer services"
    monkeypatch.setattr(sessions, "cached_states", lambda: ([{"session": SID, "title": "Fewer services"}], {}))
    assert sessions.title_of(SID, whole=True) == "Fewer services"


def test_a_whole_title_that_no_longer_matches_is_not_used(monkeypatch):
    sessions._cut(SID, LONG)
    other = "Something else entirely, long enough to have been cut short by…"
    monkeypatch.setattr(sessions, "cached_states", lambda: ([{"session": SID, "title": other}], {}))
    assert sessions.title_of(SID, whole=True) == other


def test_a_first_message_the_length_of_a_mail_stops_at_300():
    sessions._cut(SID, "word " * 200)
    assert len(sessions.whole_title(SID, ("word " * 200)[:59] + "…")) == sessions.TITLE_WHOLE


def test_targets_carries_the_whole_title_beside_a_cut_one(monkeypatch):
    """The rename box and Full titles start from the whole name (David,
    14 Nov 2026: "This chat title is truncated in the rename field")."""
    from agent_media_server import auth, mailbox

    short = sessions._cut(SID, LONG)
    rows = [{"session": SID, "title": short}, {"session": "other", "title": "Short one"}]
    monkeypatch.setattr(auth, "may_control_speech", lambda bearer: (True, {}))
    monkeypatch.setattr(sessions, "sessions_index", lambda days: rows)
    monkeypatch.setattr(sessions, "places", lambda limit=6: [])
    monkeypatch.setattr(mailbox, "with_mail", lambda where: where)
    monkeypatch.setattr(mailbox, "by_project", lambda pairs: None)
    monkeypatch.setattr(sessions, "project_groups", lambda: {})
    ok, out = sessions.targets("token")
    assert ok
    assert out["sessions"][0]["title_whole"] == LONG
    assert "title_whole" not in out["sessions"][1]


def test_a_headless_chat_named_by_its_first_message_keeps_all_of_it(monkeypatch, tmp_path):
    """The whole first message, not session_feed's 64-character label: the
    Rename sheet opened on "…being…" (David, 18 Nov 2026: "the renaming card
    gives a truncated title instead of the full one")."""
    import json
    from agent_media_core import conversation

    said = ("I didn't get any follow along for the current message being read out "
            "in this thread, which is the one about auto update")
    path = tmp_path / f"{SID}.jsonl"
    path.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": said}}) + "\n")
    monkeypatch.setattr(conversation, "transcript", lambda session: path)
    monkeypatch.setattr(sessions, "_manifest_of", lambda session: {})
    sessions._HEADLESS_TITLES.pop(SID, None)
    short = sessions._headless_title(SID, {})
    assert short == "I didn't get any follow along for the current message being…"
    assert sessions.whole_title(SID, short) == said
