"""A bridge login now gone (the phone paired again): its rooms marked
retired, folded under the live room on a contact's card, its ghosts off the
card (David, 27 Nov 2026: "why are there two lots of Google messages here?")."""

import threading
from types import SimpleNamespace

from agent_media_server import contacts, matrix

GM = {"id": "gmessages", "name": "Google Messages"}
TG = {"id": "telegram", "name": "Telegram"}


def room(rid, net, receiver, members=()):
    return SimpleNamespace(id=rid, thread=rid, network=net, lock=threading.Lock(),
                           channel=("@bot:x", receiver, "c-" + rid) if receiver else None,
                           members={m: m for m in members})


def test_a_login_the_bridges_no_longer_list_is_retired():
    rooms = [room("a", GM, "old"), room("b", GM, "new"),
             room("c", TG, "tg-gone"),          # no live login on its network
             room("d", GM, "")]
    assert matrix.retired_logins(rooms, {"new"}) == {"old"}
    # Until the bridges have answered, nothing is.
    assert matrix.retired_logins(rooms, set()) == set()


def test_rows_and_the_card_mark_it(monkeypatch):
    monkeypatch.setattr(matrix, "_ACCOUNT_NAMES", {})
    net = matrix.with_account(GM, ("b", "old", "c"), retired={"old"})
    assert net["account"] == {"id": "old", "retired": True}
    assert "retired" not in matrix.with_account(GM, ("b", "new", "c"), retired={"old"})["account"]


def test_a_retired_room_goes_under_the_live_one():
    def row(s, net, acct, at, retired=False):
        a = {"id": acct, **({"retired": True} if retired else {})}
        return {"session": s, "room": "!" + s, "title": "Kat", "network": {**net, "account": a}, "at": at}

    rows = [row("live", GM, "new", 30), row("tg", TG, "t", 20),
            row("dead", GM, "old", 10, True), row("lone", {"id": "signal", "name": "Signal"}, "s-old", 5, True)]
    out = contacts._fold_retired(rows)
    assert [x["session"] for x in out] == ["live", "tg", "lone"]
    assert out[0]["earlier"] == [{"session": "dead", "room": "!dead", "at": 10}]
    # Signal has no live room with them: "lone" stands, still marked.
    assert out[2]["network"]["account"]["retired"]
    assert "earlier" not in out[1]


def test_ghosts_of_a_retired_login_leave_the_card(monkeypatch):
    rooms = {"a": room("a", GM, "old", ["@gmessages_1.19:x", "@david:x"]),
             "b": room("b", GM, "new", ["@gmessages_2.21:x", "@david:x"]),
             "c": room("c", GM, "old", ["@gmessages_1.7:x"]),
             "d": room("d", GM, "new", ["@gmessages_1.7:x"])}
    monkeypatch.setattr(matrix, "_ROOMS", rooms)
    monkeypatch.setattr(matrix, "_LIVE_LOGINS", {"new"})
    handles = [{"id": "@gmessages_1.19:x"}, {"id": "@gmessages_2.21:x"},
               {"id": "@gmessages_1.7:x"},            # also in a live room: kept
               {"id": "@signal_abc:x"}]               # in no room: kept
    assert [h["id"] for h in contacts._live_handles(handles)] == \
        ["@gmessages_2.21:x", "@gmessages_1.7:x", "@signal_abc:x"]
    monkeypatch.setattr(matrix, "_LIVE_LOGINS", None)
    assert contacts._live_handles(handles) == handles


def test_the_listing_keeps_unnamed_logins_live(monkeypatch):
    monkeypatch.setattr(matrix, "_LIVE_LOGINS", None)
    monkeypatch.setattr(matrix, "_ACCOUNT_NAMES", {})
    matrix._refresh_accounts(lambda: [{"id": "gm", "logins": [{"id": "9", "name": ""}]}])
    assert matrix._LIVE_LOGINS == {"9"}


# --- one transcript: the retired copy read on into the live room ------------

from agent_media_core import matrix as core_matrix  # noqa: E402

ME = "@david:ryer.org"
OLD_GHOST, NEW_GHOST = "@gmessages_1.19:ryer.org", "@gmessages_2.21:ryer.org"
OLD_ROOM, NEW_ROOM = "!gm1:ryer.org", "!gm2:ryer.org"


def _ev(eid, ts, sender, body):
    return {"type": "m.room.message", "event_id": eid, "sender": sender,
            "origin_server_ts": 1_790_000_000_000 + ts * 1000,
            "content": {"msgtype": "m.text", "body": body}}


class TwoLogins:
    """Login 1's copy holds the long history; login 2 backfilled the last of
    it again (`$o5` and `$n5` are one message) and has what came since."""

    rooms = {OLD_ROOM: ("old", OLD_GHOST, [_ev("$o1", 1, OLD_GHOST, "from long ago"),
                                           _ev("$o2", 2, ME, "my old answer"),
                                           _ev("$o5", 5, OLD_GHOST, "backfilled twice")]),
             NEW_ROOM: ("new", NEW_GHOST, [_ev("$n5", 5, NEW_GHOST, "backfilled twice"),
                                           _ev("$n6", 6, ME, "since the pairing")])}

    def __call__(self, config, path, params=None, timeout=10.0):
        rid = next(r for r in self.rooms if path.startswith(f"/rooms/{matrix._q(r)}")
                   or path.startswith(f"/rooms/{r}"))
        receiver, ghost, evs = self.rooms[rid]
        if path.endswith("/joined_members"):
            return {"joined": {ME: {"display_name": "David"}, ghost: {"display_name": "Kat"}}}
        if path.endswith("/state"):
            return [{"type": "m.bridge", "content": {
                "bridgebot": "@gmessagesbot:ryer.org",
                "channel": {"id": "c", "fi.mau.receiver": receiver},
                "protocol": {"id": "gmessages", "displayname": "Google Messages"}}}]
        if path.endswith("/state/m.room.name"):
            return {"name": "Kat"}
        return {"chunk": list(reversed(evs))}


def test_the_retired_copy_reads_on_below_the_live_one(monkeypatch):
    monkeypatch.setattr(matrix, "_ROOMS", {})
    monkeypatch.setattr(matrix, "_ALIAS", {})
    cfg = core_matrix.Config(homeserver="https://hs.test", token="t", rooms=set())
    new = matrix.Room(NEW_ROOM, cfg, ME, get=TwoLogins())
    new.load()
    new.attach(OLD_ROOM, retired=True, senders={OLD_GHOST: NEW_GHOST})
    msgs, more = new.page(30)
    assert [(m["id"], m["parts"][0]["text"]) for m in msgs] == [
        ("$o1", "from long ago"), ("$o2", "my old answer"),
        ("$n5", "backfilled twice"), ("$n6", "since the pairing")]
    assert more is False
    # Their old words read as theirs, the copy marked and read-only.
    assert msgs[0]["sender"] == {"id": NEW_GHOST, "name": "Kat"}
    assert msgs[0]["earlier"] == matrix.thread_of(OLD_ROOM)
    assert "earlier" not in msgs[2]
    assert new._closed_refusal("$o1") is not None
    # Attached once only.
    new.attach(OLD_ROOM, retired=True, senders={OLD_GHOST: NEW_GHOST})
    assert len(new.page(30)[0]) == 4
