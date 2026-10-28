"""Text a contact (contacts_text.py, §6.25 "Texting someone new"): the
services a person can be texted on, and a chat started through a stand-in
bridge and homeserver on loopback. Never the real ones: HOME, the config and
state dirs are throwaway, every MATRIX_* and MEDIA_* setting of this host is
scrubbed, and the bridges are found only from configs written here."""

from __future__ import annotations

import json
import os
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from agent_media_core import matrix as core_matrix
from agent_media_server import auth, bridges, contacts, contacts_text, matrix
from test_contract import AUTH, call, server, signed_in, typed  # noqa: F401

ME = "@david:test"
SECRET = "s3cret-for-the-fake-bridge"
BOTS = "@gmessagesbot:test,@whatsappbot:test,@facebookbot:test"
NEW_ROOM = "!new:test"


class Fake:
    """One loopback server standing in for the bridges' provisioning APIs
    and the homeserver: `create_dm` answers `NEW_ROOM` (404 for
    `refuse`), and the owner's join is refused `join_refusals` times — the
    invitation not there yet — before it is let in."""

    def __init__(self):
        self.calls: list[tuple[str, str]] = []
        self.join_refusals = 0
        self.refuse = "+61499999999"
        self.ghost = "@gmessages_1.9:test"
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _answer(self, code, obj):
                raw = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _route(self, method):
                n = int(self.headers.get("Content-Length") or 0)
                if n:
                    self.rfile.read(n)
                u = urllib.parse.urlsplit(self.path)
                path = urllib.parse.unquote(u.path)
                q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
                bearer = (self.headers.get("Authorization") or "").removeprefix("Bearer ")
                fake.calls.append((method, path))
                if path.startswith("/_matrix/provision/v3/create_dm/"):
                    if bearer != SECRET or q.get("user_id") != ME:
                        return self._answer(401, {"errcode": "M_UNKNOWN_TOKEN"})
                    ident = path.rsplit("/", 1)[1]
                    if ident == fake.refuse:
                        return self._answer(404, {"errcode": "M_NOT_FOUND",
                                                  "error": "Identifier not found"})
                    return self._answer(201, {"id": "p9", "name": "Mel Jones",
                                              "dm_room_mxid": NEW_ROOM})
                if path.startswith("/_matrix/client/v3/"):
                    if bearer != "owner-tok":
                        return self._answer(401, {"errcode": "M_UNKNOWN_TOKEN"})
                    rest = path[len("/_matrix/client/v3"):]
                    if method == "POST" and rest == f"/rooms/{NEW_ROOM}/join":
                        if fake.join_refusals > 0:
                            fake.join_refusals -= 1
                            return self._answer(403, {"errcode": "M_FORBIDDEN"})
                        return self._answer(200, {"room_id": NEW_ROOM})
                    if rest == f"/rooms/{NEW_ROOM}/joined_members":
                        return self._answer(200, {"joined": {
                            ME: {"display_name": "David"},
                            "@gmessagesbot:test": {"display_name": "bot"},
                            fake.ghost: {"display_name": "Mel Jones"}}})
                    if rest == f"/rooms/{NEW_ROOM}/state/m.room.name":
                        return self._answer(404, {"errcode": "M_NOT_FOUND"})
                    if rest == f"/rooms/{NEW_ROOM}/state":
                        return self._answer(200, [{"type": "m.bridge", "state_key": "x",
                                                   "content": {"protocol": {
                                                       "id": "gmessages-sms",
                                                       "displayname": "Google Messages (SMS)"}}}])
                    if rest == f"/rooms/{NEW_ROOM}/messages":
                        return self._answer(200, {"chunk": []})
                return self._answer(404, {"errcode": "M_UNRECOGNIZED"})

            def do_GET(self):
                self._route("GET")

            def do_POST(self):
                self._route("POST")

            def do_PUT(self):
                self._route("PUT")

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.srv.daemon_threads = True
        self.port = self.srv.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.srv.serve_forever, kwargs={"poll_interval": 0.05},
                         daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()

    def provisioned(self):
        return [p for m, p in self.calls if p.startswith("/_matrix/provision/")]


def _config(appservice_id, port, bot):
    return f"""\
homeserver:
    address: http://127.0.0.1:{port}
    domain: test
appservice:
    hostname: 127.0.0.1
    port: {port}
    id: {appservice_id}
    bot:
        username: {bot}
    username_template: {appservice_id}_{{{{.}}}}
provisioning:
    shared_secret: "{SECRET}"
    allow_matrix_auth: true
"""


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    for k in list(os.environ):
        if k.startswith(("MATRIX_", "MEDIA_")):
            monkeypatch.delenv(k, raising=False)
    for d in ("home", "config", "cache"):
        (tmp_path / d).mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("MEDIA_CONTACTS_ADB", str(tmp_path / "no-such-adb") + " shell")
    monkeypatch.setenv("MATRIX_OWNER_ID", ME)
    monkeypatch.setenv("MATRIX_OWNER_BRIDGES", BOTS)
    monkeypatch.setenv("MATRIX_BRIDGES", str(tmp_path / "none" / "*.yaml"))
    monkeypatch.setattr(matrix, "_ROOMS", {})
    monkeypatch.setattr(matrix, "_OWNER", [None])
    monkeypatch.setattr(matrix, "ADOPT_STEP_S", 0.01)
    monkeypatch.setattr(matrix, "ADOPT_WAIT_S", 2.0)
    contacts._reset_for_tests()
    bridges._reset_for_tests()

    def nowhere(*a, **k):
        raise AssertionError(f"a real Google request: {a[:2]}")
    monkeypatch.setattr(contacts, "_request", nowhere)
    yield
    auth._REQ.__dict__.clear()
    contacts._reset_for_tests()


@pytest.fixture
def fake(monkeypatch, tmp_path):
    f = Fake()
    d = tmp_path / "share"
    for aid, bot in (("gmessages", "gmessagesbot"), ("whatsapp", "whatsappbot"),
                     ("facebook", "facebookbot"),
                     # Not in MATRIX_OWNER_BRIDGES: its rooms are not read, so
                     # no chat is started on it.
                     ("telegram", "telegrambot")):
        (d / f"mautrix-{aid}").mkdir(parents=True)
        (d / f"mautrix-{aid}" / "config.yaml").write_text(_config(aid, f.port, bot))
    monkeypatch.setenv("MATRIX_BRIDGES", str(d / "mautrix-*" / "config.yaml"))
    monkeypatch.setenv("MATRIX_OWNER_TOKEN", "owner-tok")
    cfg = core_matrix.Config(homeserver=f.url, token="t", rooms=set())
    matrix._OWNER[0] = matrix._Owner(cfg, ME, matrix._bridges(), None)
    yield f
    f.close()


def _rec(kind, sid, name, phones=(), handles=()):
    card = {"name": name, "names": [{"display": name, "given": "", "family": ""}],
            "phones": [{"value": contacts.e164(p, "AU"), "raw": p, "type": "Mobile"} for p in phones],
            "emails": [], "addresses": [], "organizations": [], "notes": "",
            "handles": [{"network": "facebookgo", "network_name": "Messenger", "id": h}
                        for h in handles]}
    return kind, sid, card, ({"etag": "e"} if kind == "google" else
                             {"network": "facebookgo", "network_name": "Messenger", "rooms": []})


LIONEL = contacts.contact_id("google", "people/c1")
MEL = contacts.contact_id("google", "people/c2")
JO = contacts.contact_id("bridges", "@facebook_1001:test")


@pytest.fixture
def book():
    conn = contacts._connect()
    try:
        for kind, sid, card, meta in (
                _rec("google", "people/c1", "Lionel Smith", ["0412 345 678"]),
                _rec("google", "people/c2", "Mel Jones", ["+61 400 111 222", "+61 499 999 999"]),
                _rec("bridges", "@facebook_1001:test", "Jo Book", handles=["@facebook_1001:test"])):
            conn.execute("INSERT OR REPLACE INTO records (kind, sid, card, meta) VALUES (?, ?, ?, ?)",
                         (kind, sid, json.dumps(card), json.dumps(meta)))
        contacts._rebuild(conn)
    finally:
        conn.close()
    contacts._INDEX["checked"] = 0.0
    # Lionel has an SMS room already; its ghost is named by participant, not
    # by number (as mautrix-gmessages names them).
    cfg = core_matrix.Config(homeserver="http://127.0.0.1:9", token="t", rooms=set())
    r = matrix.Room("!lionel:test", cfg, ME,
                    get=lambda *a, **k: (_ for _ in ()).throw(OSError("no")))
    r.members = {ME: "David", "@gmessagesbot:test": "bot", "@gmessages_1.5:test": "Lionel Smith"}
    r.network = {"id": "gmessages", "name": "Google Messages"}
    r.loaded = True
    matrix._ROOMS[r.thread] = r
    return r


def test_their_rooms_first_then_what_a_chat_can_be_started_with(fake, book):
    got = contacts_text.services(LIONEL)
    assert got["can_start"] is True and got["why"] is None
    (sms, wa) = got["services"]
    # The one SMS room is theirs, and with one number the number is its.
    assert sms == {"service": "gmessages", "name": "SMS", "address": "@gmessages_1.5:test",
                   "detail": "Lionel Smith", "network": {"id": "gmessages", "name": "Google Messages"},
                   "room": book.thread}
    assert wa["service"] == "whatsapp" and wa["name"] == "WhatsApp"
    assert wa["address"] == "+61412345678" and wa["room"] is None
    # Each number on each number service; no Telegram (its rooms are not read).
    mel = contacts_text.services(MEL)["services"]
    assert [(e["service"], e["address"]) for e in mel] == [
        ("gmessages", "+61400111222"), ("gmessages", "+61499999999"),
        ("whatsapp", "+61400111222"), ("whatsapp", "+61499999999")]
    # A Messenger person is texted by the ghost the card holds.
    (jo,) = contacts_text.services(JO)["services"]
    assert jo["service"] == "facebook" and jo["name"] == "Messenger"
    assert jo["address"] == "@facebook_1001:test"
    assert fake.provisioned() == []           # listing asks no bridge


def test_an_existing_room_is_opened_and_no_bridge_is_asked(fake, book):
    got = contacts_text.start(LIONEL, "gmessages", "@gmessages_1.5:test")
    assert got == {"session": book.thread, "room": "!lionel:test", "title": book.title(),
                   "created": False}
    assert fake.calls == []


def test_a_new_chat_is_started_joined_and_listed(fake, book):
    fake.join_refusals = 2                     # the invitation is a moment behind
    got = contacts_text.start(MEL, "gmessages", "0400 111 222")
    assert got["room"] == NEW_ROOM and got["created"] is True
    assert got["session"] == matrix.thread_of(NEW_ROOM)
    assert fake.provisioned() == ["/_matrix/provision/v3/create_dm/+61400111222"]
    # It is a Matrix row now, read as the owner, and it is Mel's.
    row = next(r for r in matrix.rows() if r["session"] == got["session"])
    assert row["network"] == {"id": "gmessages", "name": "Google Messages"}
    assert NEW_ROOM in matrix._OWNER[0].config.rooms
    # Nothing was sent to anyone: no message, no reaction, no state.
    assert not [c for c in fake.calls if c[0] == "PUT" or "/send/" in c[1]]
    # Again: the same room, not created this time.
    again = contacts_text.start(MEL, "gmessages", "+61400111222")
    assert again["session"] == got["session"] and again["created"] is False


def test_errors_say_why(fake, book, monkeypatch):
    with pytest.raises(KeyError):
        contacts_text.services("00000000-0000-0000-0000-000000000000")
    with pytest.raises(ValueError, match="not one of their addresses"):
        contacts_text.start(MEL, "gmessages", "+61400000000")
    with pytest.raises(ValueError, match="cannot be texted on telegram"):
        contacts_text.start(MEL, "telegram", "+61400111222")
    with pytest.raises(contacts_text.Refused, match="SMS would not start it: Identifier not found"):
        contacts_text.start(MEL, "gmessages", "+61499999999")
    # The chat made, its room never let in: 504, and asked again it works.
    fake.join_refusals = 10_000
    monkeypatch.setattr(matrix, "ADOPT_WAIT_S", 0.1)
    with pytest.raises(contacts_text.TimedOut):
        contacts_text.start(MEL, "whatsapp", "+61400111222")
    # No owner token: nothing is asked of a bridge.
    fake.calls.clear()
    monkeypatch.delenv("MATRIX_OWNER_TOKEN")
    got = contacts_text.services(MEL)
    assert got["can_start"] is False and "owner token" in got["why"]
    with pytest.raises(contacts_text.NotDrivable):
        contacts_text.start(MEL, "gmessages", "+61400111222")
    assert fake.calls == []
    # Another account's book: the bridges are the owner's.
    with contacts.as_account(auth.NOBODY), pytest.raises(contacts_text.NotOwner):
        contacts_text.services(MEL)


def test_the_routes(server, signed_in, fake, book, monkeypatch):
    res, obj = call(server, "GET", f"/contacts/{MEL}", headers=AUTH)
    assert res.status == 200 and obj["contact"]["text"] is True
    res, obj = call(server, "GET", f"/contacts/{MEL}/text", headers=AUTH)
    assert res.status == 200 and obj["ok"] and len(obj["services"]) == 4
    res, obj = call(server, "POST", f"/contacts/{MEL}/text",
                    {"service": "gmessages", "address": "+61400111222"}, headers=AUTH)
    assert res.status == 200 and obj["session"] == matrix.thread_of(NEW_ROOM) and obj["created"]
    res, obj = call(server, "POST", f"/contacts/{MEL}/text",
                    {"service": "gmessages", "address": "+61499999999"}, headers=AUTH)
    assert res.status == 502 and "Identifier not found" in obj["error"]
    res, obj = call(server, "POST", f"/contacts/{MEL}/text", {"service": "nope"}, headers=AUTH)
    assert res.status == 400
    res, obj = call(server, "GET", "/contacts/00000000-0000-0000-0000-000000000000/text",
                    headers=AUTH)
    assert res.status == 404
    res, _ = call(server, "GET", f"/contacts/{MEL}/text")
    assert res.status in (401, 403)
    monkeypatch.delenv("MATRIX_OWNER_TOKEN")
    res, obj = call(server, "POST", f"/contacts/{MEL}/text",
                    {"service": "whatsapp", "address": "+61400111222"}, headers=AUTH)
    assert res.status == 409 and "owner token" in obj["error"]
