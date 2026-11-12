"""A list's titles stop at 60 characters; an open thread's header gets the
whole one (David, 12 Nov 2026: "the title probably shouldn't be truncated in
the extended view"): `sessions._cut` keeps it, `title_of(whole=True)` gives it."""
from agent_media_server import sessions

SID = "18136e77-b9ac-43ba-878f-ece77a68846c"
LONG = "[From Sam] Fewer services: look at agent-media's unit count and say which ones can go"


def test_a_long_title_is_cut_for_the_list_and_whole_for_the_header(monkeypatch):
    short = sessions._cut(SID, LONG)
    assert len(short) == 60 and short.endswith("…") and LONG.startswith(short[:-1])
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
