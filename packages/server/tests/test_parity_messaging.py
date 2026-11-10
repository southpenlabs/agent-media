"""Parity sweep, messaging chat list (docs/parity/messaging.md): how a room's
row is titled and previewed, against what established clients do with the
same edge case. A pass is pinned as a plain test; a known gap as a strict
xfail, so the fix flips it to a pass and the table is updated with it.
No network: a canned homeserver."""

import pytest

from agent_media_core import matrix as core_matrix
from agent_media_server import matrix

ROOM = "!parity:ryer.org"
ME, SAM = "@david:ryer.org", "@sam:ryer.org"


def _room(monkeypatch, members, name=None, state=None, messages=()):
    """A loaded room with these `{user: display_name}` members (the owner and
    the agent added), this `m.room.name` (None: the room has none) and
    these state events."""
    monkeypatch.setenv("MATRIX_SAM_ID", SAM)
    joined = {ME: {"display_name": "David"}, SAM: {"display_name": "Sam"},
              **{u: {"display_name": n} for u, n in members.items()}}

    def get(config, path, params=None, timeout=10.0):
        if path.endswith("/joined_members"):
            return {"joined": joined}
        if path.endswith("/state/m.room.name"):
            if name is None:
                raise OSError("404")
            return {"name": name}
        if path.endswith("/state"):
            return list(state or [])
        return {"chunk": list(reversed(messages))}

    cfg = core_matrix.Config(homeserver="https://hs.test", token="t", rooms={ROOM})
    r = matrix.Room(ROOM, cfg, ME, get=get)
    r.load()
    return r


def _ev(i, sender, body):
    return {"type": "m.room.message", "event_id": f"${i}", "sender": sender,
            "origin_server_ts": 1_790_000_000_000 + i * 1000,
            "content": {"msgtype": "m.text", "body": body}}


def _row(monkeypatch, r):
    monkeypatch.setattr(matrix, "_ROOMS", {r.thread: r})
    (row,) = matrix.rows()
    return row


# -- names --

def test_an_empty_name_is_named_from_its_members(monkeypatch):
    r = _room(monkeypatch, {"@mel:x": "Mel"}, name="")
    assert r.title() == "Mel"


@pytest.mark.xfail(strict=True, reason="parity/messaging.md M3: a blank name is used as is")
def test_a_blank_name_is_named_from_its_members(monkeypatch):
    r = _room(monkeypatch, {"@mel:x": "Mel"}, name="   ")
    assert r.title() == "Mel"


def test_a_member_with_no_display_name_shows_as_their_id(monkeypatch):
    # Element and the spec fall back to the Matrix id too.
    r = _room(monkeypatch, {"@whatsapp_61400:x": ""}, name=None)
    assert r.title() == "@whatsapp_61400:x"


def test_a_long_name_reaches_the_app_whole_and_the_preview_is_capped(monkeypatch):
    # The app ellipsises the title (app.css `.thread-row .title`); the server
    # does not cut it. The preview line is cut at 300 characters.
    long = "The Extremely Long Family Group Chat Name " * 5
    r = _room(monkeypatch, {"@mel:x": "Mel"}, name=long,
              messages=[_ev(1, "@mel:x", "x" * 1000)])
    row = _row(monkeypatch, r)
    assert row["title"] == long
    assert len(row["recap"]["text"]) == 300


def test_an_rtl_name_passes_through_and_gives_its_first_name(monkeypatch):
    r = _room(monkeypatch, {"@a:x": "שרה כהן"}, name=None)
    assert r.title() == "שרה כהן"
    r = _room(monkeypatch, {"@a:x": "שרה כהן", "@b:x": "Bob Ray", "@c:x": "Cy Dee",
                            "@d:x": "Di Eve"}, name=None,
              messages=[_ev(1, "@a:x", "שלום")])
    assert r.title() == "שרה, Bob, Cy +1"


@pytest.mark.xfail(strict=True, reason="parity/messaging.md M5: an emoji first word is the name")
def test_an_emoji_led_name_in_a_big_group_gives_a_word(monkeypatch):
    r = _room(monkeypatch, {"@a:x": "🎉 Pete Rose", "@b:x": "Bob Ray", "@c:x": "Cy Dee",
                            "@d:x": "Di Eve"}, name=None,
              messages=[_ev(1, "@a:x", "party")])
    first = r.title().split(",")[0]
    assert any(ch.isalpha() for ch in first)


# -- who is in it --

@pytest.mark.xfail(strict=True, reason="parity/messaging.md M8: titled by its raw room id")
def test_a_room_with_only_the_owner_is_not_titled_by_its_id(monkeypatch):
    # Element: "Empty room" (the spec's name calculation, §13.1.1 heroes).
    r = _room(monkeypatch, {}, name=None)
    assert not r.title().startswith("!")


@pytest.mark.xfail(strict=True, reason="parity/messaging.md M9: two people of one name read as one")
def test_two_members_of_one_name_are_told_apart(monkeypatch):
    # Element disambiguates a shared display name with the Matrix id.
    r = _room(monkeypatch, {"@sam1:x": "Sam Lee", "@sam2:x": "Sam Lee"}, name=None)
    names = [n.strip() for n in r.title().split(",")]
    assert len(set(names)) == len(names)


@pytest.mark.xfail(strict=True, reason="parity/messaging.md M4: placeholders repeat in the title")
def test_a_group_of_only_placeholders_does_not_repeat_one(monkeypatch):
    # Every member a bridge cannot see: "Facebook, Facebook, Facebook +1" today.
    r = _room(monkeypatch, {f"@fb{i}:x": "Facebook user" for i in range(4)}, name=None)
    names = [n.strip() for n in r.title().split("+")[0].split(",")]
    assert len(set(names)) == len(names)


def test_a_big_group_skips_a_placeholder_for_a_real_name(monkeypatch):
    r = _room(monkeypatch, {"@fb:x": "Facebook user", "@a:x": "Anna Bell", "@c:x": "Cara Day",
                            "@d:x": "Dee Ess", "@e:x": "Eve Fox"}, name=None,
              messages=[_ev(1, "@fb:x", "hi")])
    assert r.title() == "Anna, Cara, Dee +2"


@pytest.mark.xfail(strict=True, reason="parity/messaging.md M10: the canonical alias is not read")
def test_an_unnamed_room_with_an_alias_is_titled_by_it(monkeypatch):
    # The spec's order: m.room.name, then m.room.canonical_alias, then members.
    alias = {"type": "m.room.canonical_alias", "state_key": "",
             "content": {"alias": "#family:ryer.org"}}
    r = _room(monkeypatch, {"@mel:x": "Mel"}, name=None, state=[alias])
    assert r.title() == "#family:ryer.org"


# -- the row --

def test_a_message_with_no_timestamp_gives_no_time(monkeypatch):
    # The app's `ago` shows nothing for a falsy `at` rather than "55 years".
    ev = _ev(1, "@mel:x", "hi")
    ev["origin_server_ts"] = 0
    r = _room(monkeypatch, {"@mel:x": "Mel"}, name="Mel", messages=[ev])
    assert _row(monkeypatch, r)["at"] == 0


def test_a_placeholder_senders_preview_names_them_as_the_bridge_does(monkeypatch):
    r = _room(monkeypatch, {"@fb:x": "Facebook user", "@a:x": "Anna"}, name="Group",
              messages=[_ev(1, "@fb:x", "hello")])
    assert _row(monkeypatch, r)["recap"]["text"] == "Facebook user: hello"
