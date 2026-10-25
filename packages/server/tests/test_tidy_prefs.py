"""The register last sent with, per person (tidy_prefs.py; David, 25 Oct 2026):
GET /voice/tidy/intents?session= lists it first with `last`; POST
/voice/tidy/sent records it. Key and time only; nothing for a private thread."""

from __future__ import annotations

import json
import os

import pytest

from agent_media_server import tidy_prefs, tidy_intents
from test_contract import AUTH, call, server, signed_in, typed  # noqa: F401 — fixtures

S1 = "0a1b2c3d-0000-4000-8000-000000000001"
S2 = "0a1b2c3d-0000-4000-8000-000000000002"
S3 = "0a1b2c3d-0000-4000-8000-000000000003"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    for k in list(os.environ):
        if k.startswith(("MEDIA_", "RELAY_")):
            monkeypatch.delenv(k, raising=False)
    for d in ("HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME", "XDG_CONFIG_HOME"):
        monkeypatch.setenv(d, str(tmp_path / d.lower()))
    monkeypatch.setenv("AGENT_PRIVATE_DIR", str(tmp_path / "private.d"))


def keys(listing):
    return [i["key"] for i in listing]


def test_nothing_remembered_is_the_servers_order_and_no_last():
    listing, last = tidy_prefs.rank(S1)
    assert last == "" and keys(listing) == keys(tidy_intents.listing())
    assert tidy_prefs.rank("")[1] == ""


def test_the_last_sent_register_comes_first_and_the_rest_keep_their_order():
    assert tidy_prefs.record(S1, "firm") is True
    listing, last = tidy_prefs.rank(S1)
    assert last == "firm" and keys(listing)[0] == "firm"
    assert keys(listing)[1:] == [k for k in keys(tidy_intents.listing()) if k != "firm"]
    # another room is not touched; a newer send replaces it
    assert tidy_prefs.rank(S2)[1] == ""
    tidy_prefs.record(S1, "friendly")
    assert tidy_prefs.rank(S1)[1] == "friendly"


def test_only_the_key_and_the_time_are_kept(tmp_path):
    tidy_prefs.record(S1, "brief")
    raw = tidy_prefs._path().read_text()
    data = json.loads(raw)
    assert data[f"room:{S1}"]["intent"] == "brief" and set(data[f"room:{S1}"]) == {"intent", "at"}


def test_a_contact_is_remembered_across_its_rooms(monkeypatch):
    from agent_media_server import matrix

    class R: pass
    monkeypatch.setattr(matrix, "room", lambda s: R() if s in (S1, S2) else None)
    monkeypatch.setattr(matrix, "_contact_of", lambda r: {"id": "c-mum", "name": "Mum"})
    tidy_prefs.record(S1, "apologetic")
    assert tidy_prefs.rank(S2)[1] == "apologetic"          # another room of the same person
    assert tidy_prefs.rank(S3)[1] == ""                    # not a room: nothing
    tidy_prefs.record(S2, "brief")                          # each room's own choice wins in that room
    assert tidy_prefs.rank(S2)[1] == "brief" and tidy_prefs.rank(S1)[1] == "apologetic"


def test_a_private_or_sealed_thread_remembers_and_ranks_nothing(tmp_path):
    tidy_prefs.record(S1, "firm")
    d = tmp_path / "private.d"
    d.mkdir()
    (d / S1).write_text("")
    assert tidy_prefs.rank(S1)[1] == ""                     # ranked nothing now
    assert tidy_prefs.record(S1, "brief") is False          # and nothing new kept
    (d / S1).unlink()
    assert tidy_prefs.rank(S1)[1] == "firm"                 # the old note was never changed


def test_an_unknown_intent_is_not_recorded():
    assert tidy_prefs.record(S1, "sarcastic") is False
    assert tidy_prefs.rank(S1)[1] == ""


def test_the_routes(server, signed_in):
    res, body = call(server, "GET", f"/voice/tidy/intents?session={S1}", None, AUTH)
    assert res.status == 200 and body["last"] == "" and body["intents"][0]["key"] == "plain"
    res, body = call(server, "POST", "/voice/tidy/sent", {"session": S1, "intent": "firm"}, AUTH)
    assert res.status == 200 and body["recorded"] is True
    res, body = call(server, "GET", f"/voice/tidy/intents?session={S1}", None, AUTH)
    assert body["last"] == "firm" and body["intents"][0]["key"] == "firm" and body["default"] == "plain"
    # no session: the plain list
    assert call(server, "GET", "/voice/tidy/intents", None, AUTH)[1]["last"] == ""
    assert call(server, "GET", "/voice/tidy/intents?session=../x", None, AUTH)[0].status == 400
    assert call(server, "POST", "/voice/tidy/sent", {"session": S1, "intent": "nope"}, AUTH)[0].status == 400
    assert call(server, "POST", "/voice/tidy/sent", {"session": "../x", "intent": "firm"}, AUTH)[0].status == 400


def test_the_routes_need_a_signed_in_caller(server):
    assert call(server, "POST", "/voice/tidy/sent", {"session": S1, "intent": "firm"}, {})[0].status in (401, 403)
    assert call(server, "GET", f"/voice/tidy/intents?session={S1}", None, {})[0].status in (401, 403)
