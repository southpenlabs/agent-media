"""Full copy (server-contract.md §6.61 "Full copy", local_messages.py): a
Local chat's messages, posted by the device that bridges it while the dial
is at full, read back through the thread routes and found by search; the
dial set back deletes them all.

Over real HTTP with the contract test's rig and really paired devices; the
state dir (the store and the search index) is conftest's throwaway one."""

from __future__ import annotations

import os
import stat
import time

import pytest

from agent_media_server import devices, doc_read, local_chats, local_messages, search, sessions
from test_contract import AUTH, call, server, shelf, signed_in, typed  # noqa: F401 — fixtures
from test_thread_events import Stream

WA = "local:whatsapp:61400000000@s.whatsapp.net"
GROUP = "local:whatsapp:1203630@g.us"
T0 = 1763300000


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _paired(server, role="", name="Pixel 8a"):
    code, _ = devices.mint_code(name, role=role)
    res, obj = call(server, "POST", "/pair", {"code": code, "device": name})
    assert res.status == 200, obj
    return obj


@pytest.fixture
def phone(server, monkeypatch):
    # No live thread list, no memory store, no project documents.
    monkeypatch.setattr(sessions, "sessions_index", list)
    monkeypatch.setattr(search, "memory_available", lambda: False)
    monkeypatch.setattr(doc_read, "search", lambda terms: [])
    p = _paired(server)
    res, got = call(server, "POST", "/local/chats", {"network": "whatsapp", "chats": [
        {"id": "61400000000@s.whatsapp.net", "name": "Ana", "kind": "person"},
        {"id": "1203630@g.us", "name": "Climbing", "kind": "group"}]}, _auth(p["token"]))
    assert res.status == 200, got
    return p


def dial(server, token, step):
    res, got = call(server, "POST", "/local/settings", {"leaves_phone": step}, _auth(token))
    return res.status, got


def send(server, token, chat, messages):
    res, got = call(server, "POST", "/local/messages", {"chat": chat, "messages": messages},
                    _auth(token))
    return res.status, got


def log(server, token, chat, **q):
    qs = "&".join(f"{k}={v}" for k, v in {"session": chat, "messages": 1, **q}.items())
    res, got = call(server, "GET", f"/conversation/log?{qs}", headers=_auth(token))
    return res.status, got


def msg(i, text=None, **kw):
    return {"event_id": f"$e{i}", "ts": T0 + i, "from": kw.pop("frm", "Ana"),
            "kind": "text", "text": text if text is not None else f"note {i}", **kw}


def words(m):
    return " ".join(p.get("text") or "" for p in m["parts"])


def test_not_enabled_is_409_and_nothing_is_kept(server, phone):
    st, got = send(server, phone["token"], WA, [msg(1)])
    assert st == 409 and got["code"] == "not_enabled"
    # Encrypted copy goes to the user's own Matrix rooms, never here: the
    # server still takes no readable messages.
    assert dial(server, phone["token"], "encrypted")[0] == 200
    st, got = send(server, phone["token"], WA, [msg(1)])
    assert st == 409 and got["code"] == "not_enabled"
    assert not local_messages.db_path().exists()
    # The chat reads, empty, rather than 400 "not a session id".
    st, got = log(server, phone["token"], WA)
    assert st == 200 and got["messages"] == [] and got["local"]["full"] is False


def test_store_dedupe_edit_redact_react_reply(server, phone):
    t = phone["token"]
    st, got = dial(server, t, "full")
    assert st == 200 and got["leaves_phone"] == "full"
    st, got = send(server, t, WA, [
        msg(1, "the crag at nine?"),
        msg(2, "yes, bringing the rope", frm="me", sender_id="me@s.whatsapp.net",
            reply_to="$e1"),
        {"event_id": "$e3", "ts": (T0 + 3) * 1000, "from": "Ana", "kind": "image",
         "text": "the wall", "media": {"name": "IMG_1.jpg", "mime": "image/jpeg", "size": 2048}},
        {"event_id": "$r1", "ts": T0 + 4, "from": "me", "kind": "reaction", "text": "👍",
         "reply_to": "$e1"},
        # An edit before its message: it waits for it.
        {"event_id": "$x5", "ts": T0 + 6, "from": "Ana", "kind": "edit", "edits": "$e5",
         "text": "parking is full, edited"},
        msg(5, "parking is full"),
        msg(6, "secret plan"),
        {"event_id": "$d6", "ts": T0 + 7, "from": "Ana", "kind": "redaction", "redacts": "$e6"},
        {"event_id": "$n7", "ts": T0 + 8, "from": "WhatsApp", "kind": "notice",
         "text": "Ana changed the group photo"}])
    assert st == 200, got
    assert (got["stored"], got["duplicates"], got["held"]) == (9, 0, 6)
    # The same batch again: every one a duplicate.
    st, got = send(server, t, WA, [msg(1, "changed?")])
    assert (got["stored"], got["duplicates"]) == (0, 1)
    st, got = log(server, t, WA)
    assert st == 200, got
    by = {m["id"]: m for m in got["messages"]}
    assert [m["id"] for m in got["messages"]] == ["$e1", "$e2", "$e3", "$e5", "$e6", "$n7"]
    assert words(by["$e1"]) == "the crag at nine?"
    assert by["$e1"]["role"] == "assistant" and by["$e1"]["peer"] == {"name": "Ana"}
    assert by["$e1"]["reactions"] == [{"key": "👍", "count": 1, "mine": True, "by": ["You"]}]
    assert by["$e2"]["role"] == "user" and by["$e2"]["sender"]["id"] == "me@s.whatsapp.net"
    assert by["$e2"]["reply_to"] == {"id": "$e1", "text": "the crag at nine?", "name": "Ana"}
    media, caption = by["$e3"]["parts"]
    assert media == {"type": "media", "kind": "image", "name": "IMG_1.jpg",
                     "text": "photo — on the phone", "on_phone": True,
                     "mimetype": "image/jpeg", "size": 2048}
    assert caption == {"type": "text", "text": "the wall"}
    assert by["$e3"]["at"] == T0 + 3                 # milliseconds read as such
    assert words(by["$e5"]) == "parking is full, edited" and by["$e5"]["edited"]
    assert by["$e6"]["redacted"] and "secret" not in words(by["$e6"])
    assert by["$n7"]["notice"] and words(by["$n7"]) == "_Ana changed the group photo_"
    # A later edit wins; the reaction taken back is gone.
    send(server, t, WA, [{"event_id": "$x5b", "ts": T0 + 9, "from": "Ana", "kind": "edit",
                          "edits": "$e5", "text": "parking is fine now"},
                         {"event_id": "$d1", "ts": T0 + 10, "from": "me", "kind": "redaction",
                          "redacts": "$r1"}])
    by = {m["id"]: m for m in log(server, t, WA)[1]["messages"]}
    assert words(by["$e5"]) == "parking is fine now" and "reactions" not in by["$e1"]
    # The redacted words are not in the store at all.
    import sqlite3

    conn = sqlite3.connect(local_messages.db_path())
    assert not conn.execute("SELECT 1 FROM msgs WHERE text LIKE '%secret%'").fetchall()
    assert stat.S_IMODE(os.stat(local_messages.db_path()).st_mode) == 0o600
    # Paging: the newest 2, then the 2 before.
    st, got = log(server, t, WA, limit=2)
    assert [m["id"] for m in got["messages"]] == ["$e6", "$n7"] and got["older"]
    st, got = log(server, t, WA, limit=2, before="$e6")
    assert [m["id"] for m in got["messages"]] == ["$e3", "$e5"]


def test_bad_batches_are_refused(server, phone):
    t = phone["token"]
    dial(server, t, "full")
    assert send(server, t, "local:whatsapp:999", [msg(1)])[0] == 404
    assert send(server, t, "nope", [msg(1)])[0] == 400
    st, got = send(server, t, WA, [{**msg(1), "preview": "x"}])
    assert st == 400 and got["code"] == "field_not_accepted"
    assert send(server, t, WA, [{**msg(1), "kind": "sticker"}])[0] == 400
    assert send(server, t, WA, [{**msg(1), "ts": "now"}])[0] == 400
    assert send(server, t, WA, [{"event_id": "$e", "ts": T0, "from": "me", "kind": "edit"}])[0] == 400
    assert send(server, t, WA, [{**msg(1), "media": {"name": "x"}}])[0] == 400   # media on text
    st, got = send(server, t, WA, [msg(i) for i in range(501)])
    assert st == 400 and got["code"] == "too_many"
    # One bad message refuses the batch: nothing is kept.
    assert send(server, t, WA, [msg(1), {**msg(2), "kind": "x"}])[0] == 400
    assert local_messages.held(WA) == 0


def test_only_the_bridging_device_may_post(server, phone):
    dial(server, phone["token"], "full")
    desk = _paired(server, name="Desk")
    st, got = send(server, desk["token"], WA, [msg(1)])
    assert st == 403 and got["code"] == "not_bridging"
    guest = _paired(server, role="guest", name="Visitor")
    assert send(server, guest["token"], WA, [msg(1)])[0] == 403
    res, _ = call(server, "POST", "/local/messages", {"chat": WA, "messages": []})
    assert res.status == 401


def test_read_back_on_other_devices_and_the_stream(server, phone):
    t = phone["token"]
    dial(server, t, "full")
    send(server, t, GROUP, [msg(1, "who has the quickdraws"), msg(2, "me", frm="Ben")])
    desk = _paired(server, name="Desk")
    st, got = log(server, desk["token"], GROUP)
    assert st == 200 and [words(m) for m in got["messages"]] == ["who has the quickdraws", "me"]
    assert got["local"]["network"] == {"id": "whatsapp", "name": "WhatsApp"}
    assert got["local"]["held"] == 2
    res, conv = call(server, "GET", f"/conversation?session={GROUP}", headers=_auth(desk["token"]))
    assert res.status == 200 and conv["source"] == "local"
    # A guest reads none of it; an unlisted chat is 404.
    guest = _paired(server, role="guest", name="Visitor")
    assert log(server, guest["token"], GROUP)[0] == 403
    assert log(server, desk["token"], "local:whatsapp:999")[0] == 404
    # The thread stream's snapshot is the same messages.
    st = Stream(server, f"/threads/{GROUP}/events", _auth(desk["token"]))
    try:
        assert st.status == 200
        snap = st.next("snapshot")
        assert [m["id"] for m in snap["messages"]] == ["$e1", "$e2"]
        assert snap["state"] == "ended" and snap["local"]["full"] is True
    finally:
        st.close()
    gst = Stream(server, f"/threads/{GROUP}/events", _auth(guest["token"]))
    try:
        assert gst.status == 403
    finally:
        gst.close()


def test_search_finds_it_and_keeps_it_from_guests(server, phone):
    t = phone["token"]
    dial(server, t, "full")
    send(server, t, WA, [msg(1, "the ocelot crag is wet"), msg(2, "kestrel spotted", frm="me")])
    res, got = call(server, "GET", "/search?q=ocelot&memory=0", headers=_auth(t))
    assert res.status == 200, got
    hit, = [h for h in got["messages"] if h["session"] == WA]
    assert hit["message"] == "$e1" and hit["thread"]["harness"] == "local"
    assert hit["thread"]["project"] == "WhatsApp" and hit["thread"]["title"] == "Ana"
    # harness:local and in:whatsapp narrow to it; from:me is the owner's words.
    for q in ("harness:local ocelot", "in:whatsapp ocelot", "from:me kestrel"):
        ok, out = search.answer(q, memory=False)
        assert ok and [h["session"] for h in out["messages"]] == [WA], q
    ok, out = search.answer("ocelot", memory=False, rooms="none")
    assert ok and not out["messages"]
    # A jump from the hit opens the chat at it.
    st, win = log(server, t, WA, around="$e1")
    assert st == 200 and win["around"]["found"] and win["messages"][0]["id"] == "$e1"
    # An edit re-indexes; a redaction drops the row.
    send(server, t, WA, [{"event_id": "$x1", "ts": T0 + 5, "from": "Ana", "kind": "edit",
                          "edits": "$e1", "text": "the heron crag is dry"},
                         {"event_id": "$d2", "ts": T0 + 6, "from": "me", "kind": "redaction",
                          "redacts": "$e2"}])
    assert not search.answer("ocelot", memory=False)[1]["messages"]
    assert [h["session"] for h in search.answer("heron", memory=False)[1]["messages"]] == [WA]
    assert not search.answer("kestrel", memory=False)[1]["messages"]
    guest = _paired(server, role="guest", name="Visitor")
    res, got = call(server, "GET", "/search?q=heron&memory=0", headers=_auth(guest["token"]))
    assert res.status == 200 and not got["messages"]
    assert "local" not in str(got["query"])


def test_switching_back_purges_everything(server, phone):
    t = phone["token"]
    dial(server, t, "full")
    send(server, t, WA, [msg(1, "the ocelot crag"), msg(2), {"event_id": "$r", "ts": T0 + 9,
                         "from": "me", "kind": "reaction", "text": "x", "reply_to": "$e1"}])
    send(server, t, GROUP, [msg(1)])
    assert search.answer("ocelot", memory=False)[1]["messages"]
    st, got = dial(server, t, "settings")
    assert st == 200 and got["purged"] == {"messages": 3, "chats": 2}
    assert local_messages.held(WA) == 0 and local_messages.held(GROUP) == 0
    assert not search.answer("ocelot", memory=False)[1]["messages"]
    st, got = log(server, t, WA)
    assert got["messages"] == [] and got["local"]["full"] is False
    assert send(server, t, WA, [msg(1)])[0] == 409
    # Not from full: nothing to say.
    assert "purged" not in dial(server, t, "settings")[1]


def test_a_chat_gone_thirty_days_is_deleted(server, phone):
    t = phone["token"]
    dial(server, t, "full")
    send(server, t, WA, [msg(1)])
    send(server, t, GROUP, [msg(1)])
    # The phone's full list no longer has the group.
    call(server, "POST", "/local/chats", {"network": "whatsapp", "full": True, "chats": [
        {"id": "61400000000@s.whatsapp.net", "kind": "person"}]}, _auth(t))
    assert local_messages.held(GROUP) == 1               # gone, not yet 30 days
    later = time.time() + 31 * 86400
    assert local_messages.prune(later) == {"messages": 1, "chats": 1}
    assert local_messages.held(GROUP) == 0 and local_messages.held(WA) == 1


def test_hosted_refuses_full_unless_allowed(server, phone, monkeypatch):
    monkeypatch.setenv("MEDIA_HOSTED", "1")
    t = phone["token"]
    res, got = call(server, "GET", "/local/settings", headers=_auth(t))
    full = {c["id"]: c for c in got["choosable"]["leaves_phone"]}["full"]
    assert full["available"] is False and "South Pen Labs" in full["why"]
    assert "South Pen Labs" in full["explain"]
    st, got = dial(server, t, "full")
    assert st == 400 and got["code"] == "not_available"
    monkeypatch.setenv("MEDIA_LOCAL_FULL_ON_HOSTED", "1")
    assert dial(server, t, "full")[0] == 200
    assert send(server, t, WA, [msg(1)])[0] == 200
    # Allowed no longer: the copy stops being taken.
    monkeypatch.delenv("MEDIA_LOCAL_FULL_ON_HOSTED")
    assert send(server, t, WA, [msg(2)])[0] == 409


# --- senders' pictures (22 Nov 2026, sasonica-app mail #1189) ---------------------

def _png(color=(200, 30, 30), px=600):
    """A plain PNG, made without Pillow (CI has none: the picture is then kept as sent)."""
    import struct
    import zlib

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + \
            struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)

    row = b"\x00" + bytes(color) * px
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", px, px, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(row * px)) + chunk(b"IEND", b""))


def pic(server, method, token, chat=WA, sender="61411111111@s.whatsapp.net", data=None,
        headers=None):
    import http.client
    from urllib.parse import quote

    conn = http.client.HTTPConnection(*server, timeout=10)
    path = f"/local/avatar?chat={quote(chat, safe='')}&sender={quote(sender, safe='')}"
    hdrs = {**(_auth(token) if token else {}), **(headers or {})}
    if data is not None:
        hdrs["Content-Type"] = "image/png"
    conn.request(method, path, body=data, headers=hdrs)
    res = conn.getresponse()
    raw = res.read()
    conn.close()
    return res, raw


def test_a_senders_picture_rides_with_their_messages(server, phone):
    import json

    tok = phone["token"]
    sid = "61411111111@s.whatsapp.net"
    data = _png()
    # Off Full copy nothing is taken.
    res, raw = pic(server, "POST", tok, data=data)
    assert res.status == 409 and json.loads(raw)["code"] == "not_enabled"
    assert dial(server, tok, "full")[0] == 200
    res, raw = pic(server, "POST", tok, data=data)
    got = json.loads(raw)
    assert res.status == 200 and got["changed"] is True
    assert got["etag"] == local_messages.avatar_etag(data)
    # Sent again unchanged: nothing written; the list lets the phone skip it.
    assert json.loads(pic(server, "POST", tok, data=data)[1])["changed"] is False
    from urllib.parse import quote

    res, lst = call(server, "GET", f"/local/avatars?chat={quote(WA, safe='')}", headers=_auth(tok))
    assert res.status == 200 and lst["avatars"] == {sid: got["etag"]}
    # In the log: the sender's picture, not on "me" or a sender without one.
    assert send(server, tok, WA, [msg(1, sender_id=sid), msg(2, frm="me"),
                                  msg(3, frm="Bo", sender_id="614222@s.whatsapp.net")])[0] == 200
    st, lg = log(server, tok, WA)
    a, me, bo = lg["messages"]
    assert a["sender"]["avatar"] == {"url": local_messages.avatar_url(WA, sid),
                                     "etag": got["etag"]}
    assert "avatar" not in me["sender"] and "avatar" not in bo["sender"]
    # Served as a thumbnail, by access_token for an <img>, 304 when unchanged.
    url = a["sender"]["avatar"]["url"]
    res, body = call(server, "GET", f"{url}&access_token={tok}")
    assert res.status == 200
    try:
        import io

        from PIL import Image
    except ImportError:                       # kept as sent
        assert body == data and res.getheader("Content-Type") == "image/png"
    else:                                     # a thumbnail
        assert res.getheader("Content-Type") == "image/jpeg"
        assert max(Image.open(io.BytesIO(body)).size) <= local_messages.AVATAR_PX
    res, _ = call(server, "GET", url, headers={**_auth(tok), "If-None-Match": f'"{got["etag"]}"'})
    assert res.status == 304
    # Another paired device reads it; a guest and nobody do not.
    other = _paired(server, name="Tablet")
    assert call(server, "GET", url, headers=_auth(other["token"]))[0].status == 200
    guest = _paired(server, role="guest", name="Visitor")
    assert call(server, "GET", url, headers=_auth(guest["token"]))[0].status == 403
    assert call(server, "GET", url)[0].status == 401
    # Only the bridging device sends one; no image is 400; an unlisted chat 404.
    assert pic(server, "POST", other["token"], data=data)[0].status == 403
    res, raw = pic(server, "POST", tok, data=b"hello")
    assert res.status == 400 and json.loads(raw)["code"] == "not_an_image"
    assert pic(server, "POST", tok, chat="local:whatsapp:nobody", data=data)[0].status == 404
    # A new version replaces it; DELETE takes it off.
    new = _png((10, 10, 200))
    assert json.loads(pic(server, "POST", tok, data=new)[1])["etag"] == \
        local_messages.avatar_etag(new)
    res, raw = pic(server, "DELETE", tok)
    assert res.status == 200 and json.loads(raw)["deleted"] is True
    assert call(server, "GET", url, headers=_auth(tok))[0].status == 404
    # Leaving full takes the pictures with the messages.
    assert pic(server, "POST", tok, data=data)[0].status == 200
    assert dial(server, tok, "settings")[0] == 200
    assert call(server, "GET", url, headers=_auth(tok))[0].status == 404
