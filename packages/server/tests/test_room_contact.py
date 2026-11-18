"""A room's phone number, and a room with no contact linked to a person or
added (room_contact.py, §6.1, §6.25). The fake Google and rooms of
test_contacts; the phone is a stand-in `_shell`, never the real one; no
homeserver: a profile is a patched `matrix._get`."""

from __future__ import annotations

import json
import os
import re
import time

import pytest

from agent_media_server import contacts, contacts_writeback, matrix, room_contact
from test_contacts import BOT, _room, google, rooms  # noqa: F401
from test_contract import AUTH, call, server, signed_in, typed  # noqa: F401


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    for k in list(os.environ):
        if k.startswith(("MATRIX_", "MEDIA_", "RELAY_")):
            monkeypatch.delenv(k, raising=False)
    for k in ("HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
        monkeypatch.setenv(k, str(tmp_path / k.lower()))
    monkeypatch.setenv("MATRIX_SAM_ID", "@sam:ryer.org")
    monkeypatch.setenv("MEDIA_CONTACTS_ADB", str(tmp_path / "no-such-adb") + " shell")
    monkeypatch.setattr(matrix, "_ROOMS", {})
    contacts._reset_for_tests()
    room_contact._reset_for_tests()

    def nowhere(*a, **k):
        raise AssertionError(f"a real request: {a[:2]}")
    monkeypatch.setattr(contacts, "_request", nowhere)
    # No write-back to a phone, ever.
    monkeypatch.setattr(contacts_writeback, "queue", lambda cid: None)
    yield
    contacts._reset_for_tests()
    room_contact._reset_for_tests()


MEL = contacts.contact_id("google", "people/c2")
ZOE = contacts.contact_id("google", "people/c3")


def _row(room_id):
    return next(r for r in matrix.rows() if r["room"] == room_id)


def _wait_profiles(uid="@gmessages_1.46:ryer.org"):
    deadline = time.time() + 5
    while time.time() < deadline:
        with room_contact._LOCK:
            if uid in room_contact._PROFILES and not room_contact._QUEUED:
                return
        time.sleep(0.02)
    raise AssertionError("the profile was never read")


def test_rows_carry_the_number_and_whether_they_can_be_linked(rooms, monkeypatch):
    monkeypatch.setattr(matrix, "_get", lambda *a, **k: {})
    stranger = _row("!d:ryer.org")                       # "0499 999 999", no contact
    assert stranger["phone"] == "+61499999999" and stranger["link"] is True
    mel = _row("!b:ryer.org")                            # matched by her number
    assert mel["phone"] == "+61400111222" and "link" not in mel
    group = _row("!e:ryer.org")
    assert "phone" not in group and "link" not in group
    email = _row("!f:ryer.org")                          # Zoë, by her address
    assert "phone" not in email and "link" not in email
    # David's own book only: another account's sees no link.
    monkeypatch.setattr(contacts, "is_owner", lambda: False)
    assert "link" not in _row("!d:ryer.org")


def test_the_number_from_the_ghosts_profile(rooms, monkeypatch):
    asked = []

    def get(cfg, path, *a, **k):
        asked.append(path)
        assert path.startswith("/profile/")
        return {"displayname": "Pat Doe",
                "com.beeper.bridge.identifiers": ["tel:+61 488 000 111", "mailto:x@y"]}
    monkeypatch.setattr(matrix, "_get", get)
    _room("!g:ryer.org", {"@gmessages_1.46:ryer.org": "Pat Doe"})
    # Never waits on the homeserver: the first row has none, the next has it.
    assert "phone" not in _row("!g:ryer.org")
    _wait_profiles()
    assert _row("!g:ryer.org")["phone"] == "+61488000111"
    # Each 1:1 room's ghost once, kept, not asked again.
    assert asked.count("/profile/%40gmessages_1.46%3Aryer.org") == 1


def test_a_profile_that_cannot_be_read_waits_an_hour(rooms, monkeypatch):
    def down(*a, **k):
        raise OSError("down")
    monkeypatch.setattr(matrix, "_get", down)
    _room("!g:ryer.org", {"@gmessages_1.46:ryer.org": "Pat Doe"})
    _row("!g:ryer.org")
    _wait_profiles()
    when, nums = room_contact._PROFILES["@gmessages_1.46:ryer.org"]
    assert nums == frozenset() and when - time.time() > 3000


def test_numbers_of_a_profile():
    assert room_contact.numbers_of_profile(
        {"com.beeper.bridge.identifiers": ["tel:0412 345 678", "tel:bad", 7]}) == {"+61412345678"}
    assert room_contact.numbers_of_profile({"com.beeper.bridge.identifiers": "tel:+61412345678"}) \
        == set()
    assert room_contact.numbers_of_profile(None) == set()


def test_link_a_room_to_someone(server, signed_in, rooms, monkeypatch):
    monkeypatch.setattr(matrix, "_get", lambda *a, **k: {})
    thread = matrix.thread_of("!d:ryer.org")
    path = f"/rooms/{thread}/contact"
    r, body = call(server, "POST", path, {"id": MEL}, AUTH)
    assert r.status == 200, body
    assert body == {"ok": True, "contact": {"id": MEL, "name": "Mel Jones"}}
    row = _row("!d:ryer.org")
    assert row["contact"]["id"] == MEL and "link" not in row
    # Kept as the room's and as the ghost's: in a group, that member is Mel.
    conn = contacts._connect()
    try:
        assert conn.execute("SELECT contact FROM overrides WHERE room=? AND kind='join'",
                            ("!d:ryer.org",)).fetchone()[0] == MEL
        assert conn.execute("SELECT contact FROM merges WHERE source=? AND kind='join'",
                            ("@gmessages_1.44:ryer.org",)).fetchone()[0] == MEL
    finally:
        conn.close()
    # Linked already: 409.
    assert call(server, "POST", path, {"id": ZOE}, AUTH)[0].status == 409


def test_link_refusals(server, signed_in, rooms, monkeypatch):
    monkeypatch.setattr(matrix, "_get", lambda *a, **k: {})
    stranger = f"/rooms/{matrix.thread_of('!d:ryer.org')}/contact"
    assert call(server, "POST", stranger, {"id": MEL})[0].status in (401, 403)
    unknown = "0" * 8 + "-0000-0000-0000-" + "0" * 12
    assert call(server, "POST", stranger, {"id": unknown}, AUTH)[0].status == 404
    assert call(server, "POST", stranger, {}, AUTH)[0].status == 400
    assert call(server, "POST", f"/rooms/{unknown}/contact", {"id": MEL}, AUTH)[0].status == 404
    group = f"/rooms/{matrix.thread_of('!e:ryer.org')}/contact"
    assert call(server, "POST", group, {"id": MEL}, AUTH)[0].status == 409
    nothing_linked = _row("!d:ryer.org")
    assert "contact" not in nothing_linked and nothing_linked["link"] is True


class Phone:
    """A stand-in for the phone's `content`: raw contacts in one Google
    account, every script logged."""

    def __init__(self, account="david@example.com", raws=(40, 41)):
        self.account = account
        self.raws = list(raws)
        self.log: list[str] = []

    def __call__(self, script: str) -> str:
        self.log.append(script)
        if script.startswith("content insert --uri content://com.android.contacts/raw_contacts"):
            self.raws.append(max(self.raws) + 1)
            return ""
        if "raw_contacts --projection account_name" in script:
            return "".join(f"Row: {i} account_name={self.account}\n"
                           for i, _ in enumerate(self.raws))
        if "raw_contacts --projection _id" in script:
            return "".join(f"Row: {i} _id={r}\n"
                           for i, r in enumerate(sorted(self.raws, reverse=True)))
        if script.startswith("content insert --uri content://com.android.contacts/data"):
            return ""
        raise AssertionError(f"unexpected: {script}")


def test_add_to_contacts_makes_one_on_the_phone_and_links_it(server, signed_in, rooms,
                                                             monkeypatch):
    monkeypatch.setattr(matrix, "_get", lambda *a, **k: {})
    phone = Phone()
    monkeypatch.setattr(contacts_writeback, "_shell", phone)
    monkeypatch.setattr(contacts, "sync_android", lambda: {})
    monkeypatch.setattr(room_contact, "_contact_of_raw", lambda raw: ZOE if raw == "42" else None)
    path = f"/rooms/{matrix.thread_of('!d:ryer.org')}/contact"
    r, body = call(server, "POST", path, {"new": {"name": "  Sam   Stranger "}}, AUTH)
    assert r.status == 200, body
    assert body == {"ok": True, "contact": {"id": ZOE, "name": "Zoë Brown"}}
    inserts = [s for s in phone.log if s.startswith("content insert")]
    assert "account_type:s:com.google" in inserts[0] and "david@example.com" in inserts[0]
    assert "raw_contact_id:i:42" in inserts[1] and "'data1:s:Sam Stranger'" in inserts[1]
    assert "data1:s:+61499999999" in inserts[2] and "data2:i:2" in inserts[2]
    assert len(inserts) == 3


def test_add_not_yet_on_the_phone_is_pending(server, signed_in, rooms, monkeypatch):
    monkeypatch.setattr(matrix, "_get", lambda *a, **k: {})
    monkeypatch.setattr(contacts_writeback, "_shell", Phone())
    monkeypatch.setattr(contacts, "sync_android", lambda: {})
    monkeypatch.setattr(room_contact, "_contact_of_raw", lambda raw: None)
    path = f"/rooms/{matrix.thread_of('!d:ryer.org')}/contact"
    r, body = call(server, "POST", path, {"new": {"name": "Sam"}}, AUTH)
    assert r.status == 200 and body == {"ok": True, "pending": True, "phone": "+61499999999"}


def test_add_refusals(server, signed_in, rooms, monkeypatch):
    monkeypatch.setattr(matrix, "_get", lambda *a, **k: {})
    path = f"/rooms/{matrix.thread_of('!d:ryer.org')}/contact"
    assert call(server, "POST", path, {"new": {"name": " "}}, AUTH)[0].status == 400
    # No phone (the adb command does not exist): 503 with why.
    r, body = call(server, "POST", path, {"new": {"name": "Sam"}}, AUTH)
    assert r.status == 503 and "phone" in body["error"]
    monkeypatch.setenv("MEDIA_CONTACTS_WRITEBACK", "0")
    r, body = call(server, "POST", path, {"new": {"name": "Sam"}}, AUTH)
    assert r.status == 503 and "off" in body["error"]
    # A phone with no Google account.
    monkeypatch.delenv("MEDIA_CONTACTS_WRITEBACK")
    monkeypatch.setattr(contacts_writeback, "_shell", lambda s: "No result found.\n")
    r, body = call(server, "POST", path, {"new": {"name": "Sam"}}, AUTH)
    assert r.status == 503 and "Google account" in body["error"]
