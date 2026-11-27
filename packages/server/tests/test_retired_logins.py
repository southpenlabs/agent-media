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
