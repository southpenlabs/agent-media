"""Full copy's media bytes (server-contract.md §6.61 "Media bytes",
local_media.py): sent once by the device that bridges the chat, then served
in the chat's log like any attachment; what is still missing is listed; the
files go with their messages. Over real HTTP with conftest's state dir."""

from __future__ import annotations

import http.client
import json
import os
import stat
from urllib.parse import quote

from agent_media_server import local_media
from test_contract import AUTH, call, server, shelf, signed_in, typed  # noqa: F401 — fixtures
from test_local_messages import WA, _auth, _paired, dial, log, msg, phone, send  # noqa: F401

PHOTO = {"event_id": "$p1", "ts": 1763300010, "from": "Ana", "kind": "image",
         "media": {"name": "beach.jpg", "mime": "image/jpeg", "size": 5}}
FILE = {"event_id": "$f1", "ts": 1763300020, "from": "Ana", "kind": "file",
        "media": {"name": "notes.pdf", "mime": "application/pdf", "size": 3}}


def put(server, token, event, data, chat=WA, ctype="image/jpeg", length=None):
    conn = http.client.HTTPConnection(*server, timeout=10)
    hdrs = {**_auth(token), "Content-Type": ctype,
            "Content-Length": str(len(data) if length is None else length)}
    conn.putrequest("POST", f"/local/media?chat={quote(chat, safe='')}&event={quote(event, safe='')}")
    for k, v in hdrs.items():
        conn.putheader(k, v)
    conn.endheaders()
    conn.send(data)
    res = conn.getresponse()
    raw = res.read()
    conn.close()
    return res.status, json.loads(raw)


def get(server, path, headers=None):
    conn = http.client.HTTPConnection(*server, timeout=10)
    conn.request("GET", path, headers=headers or {})
    res = conn.getresponse()
    raw = res.read()
    conn.close()
    return res, raw


def test_media_bytes_sent_once_then_served_in_the_log(server, phone):
    t = phone["token"]
    assert dial(server, t, "full")[0] == 200
    assert send(server, t, WA, [msg(1, "hi"), PHOTO, FILE])[0] == 200
    # Before: on the phone, and both listed as missing, newest first.
    _, got = log(server, t, WA)
    part = got["messages"][1]["parts"][0]
    assert part["on_phone"] is True and "url" not in part
    res, miss = call(server, "GET", f"/local/media/missing?chat={quote(WA, safe='')}", headers=_auth(t))
    assert [m["event"] for m in miss["missing"]] == ["$f1", "$p1"]
    assert miss["limit"] == local_media.max_bytes()
    # Sent.
    st, out = put(server, t, "$p1", b"\xff\xd8abc")
    assert st == 200 and out["size"] == 5 and out["type"] == "image/jpeg"
    p = local_media.path_of(WA, "$p1")
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600
    # After: a url in the log, the bytes there (Range too), one left missing.
    _, got = log(server, t, WA)
    part = got["messages"][1]["parts"][0]
    assert part["url"] == out["url"] and "on_phone" not in part and part["name"] == "beach.jpg"
    res, raw = get(server, part["url"], _auth(t))
    assert res.status == 200 and raw == b"\xff\xd8abc" and res.getheader("Content-Type") == "image/jpeg"
    res, raw = get(server, part["url"] + f"&access_token={t}", {"Range": "bytes=2-"})
    assert res.status == 206 and raw == b"abc"
    _, miss = call(server, "GET", f"/local/media/missing?chat={quote(WA, safe='')}", headers=_auth(t))
    assert [m["event"] for m in miss["missing"]] == ["$f1"]
    # A file that is not a picture, sound or video is served as bytes only.
    assert put(server, t, "$f1", b"pdf", ctype="application/pdf")[0] == 200
    res, _ = get(server, local_media.url(WA, "$f1"), _auth(t))
    assert res.getheader("Content-Type") == "application/octet-stream"
    # The dial off full takes the files with the messages.
    assert dial(server, t, "settings")[0] == 200
    assert not p.exists()


def test_who_and_what_may_be_sent(server, phone, monkeypatch):
    t = phone["token"]
    assert dial(server, t, "full")[0] == 200
    assert send(server, t, WA, [msg(1, "words"), PHOTO])[0] == 200
    other = _paired(server, name="Tablet")
    st, out = put(server, other["token"], "$p1", b"x")
    assert st == 403 and out["code"] == "not_bridging"
    assert put(server, t, "$nope", b"x")[0] == 404
    assert put(server, t, "$e1", b"x")[1]["code"] == "no_media"
    monkeypatch.setenv("MEDIA_LOCAL_MEDIA_MAX_MB", "1")
    st, out = put(server, t, "$p1", b"x", length=(1 << 20) + 1)
    assert st == 413 and out["code"] == "too_big" and out["limit"] == 1 << 20
    res, _ = get(server, local_media.url(WA, "$p1"), _auth(t))
    assert res.status == 404
