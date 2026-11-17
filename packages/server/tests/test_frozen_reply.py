"""A reply to another machine's chat, frozen here, is passed on to that
machine's own server when a peer link may reply (archived chats step 3,
docs/proposals/2026-11-18-archived-chats.md)."""

from __future__ import annotations

import pytest

from agent_media_core import peer_link
from agent_media_server import auth, devices, frozen, send

SID = "aaaaaaaa-1111-4111-8111-111111111111"
NO = {"error": "This chat ran on red5 and is read-only here.", "code": "frozen",
      "host": "red5", "status": 409}


@pytest.fixture
def frozen_here(monkeypatch):
    monkeypatch.setattr(frozen, "refusal", lambda s: dict(NO) if s == SID else None)
    monkeypatch.setattr(auth, "gate", lambda b: ({"username": "david"}, {}) if b == "tok"
                        else (None, {"status": 401, "error": "no"}))


def test_no_reply_link_is_the_refusal(frozen_here, monkeypatch):
    monkeypatch.setattr(peer_link, "reply_link", lambda host: None)
    ok, d = send.reply("hello", "tok", session=SID)
    assert not ok and d["code"] == "frozen" and "owner_error" not in d


def test_a_reply_link_passes_it_on_and_answers_with_where(frozen_here, monkeypatch):
    link = {"url": "https://red5.example", "token": "rt", "reply": True, "server_id": "S5"}
    monkeypatch.setattr(peer_link, "reply_link", lambda host: link if host == "red5" else None)
    sent = []

    def fake_http(url, body, token="", timeout=30.0, method="POST"):
        sent.append((url, body, token, method))
        return 200, {"ok": True, "session": SID, "queued": False}

    monkeypatch.setattr(peer_link, "_http", fake_http)
    ok, d = send.reply("hello", "tok", session=SID, quote="earlier", reply_to="m1")
    assert ok and d["forwarded"] == {"host": "red5", "server": "https://red5.example",
                                     "server_id": "S5"}
    (url, body, token, method), = sent
    assert url == "https://red5.example/reply" and token == "rt" and method == "POST"
    assert body == {"session": SID, "text": "hello", "mode": "continue", "quote": "earlier",
                    "reply_to": "m1"}


def test_the_owner_refusing_or_down_is_the_refusal_with_why(frozen_here, monkeypatch):
    monkeypatch.setattr(peer_link, "reply_link",
                        lambda host: {"url": "https://red5.example", "token": "rt"})
    monkeypatch.setattr(peer_link, "_http",
                        lambda *a, **k: (409, {"ok": False, "error": "a restart is pending"}))
    ok, d = send.reply("hello", "tok", session=SID)
    assert not ok and d["code"] == "frozen" and d["owner_error"] == "a restart is pending"
    monkeypatch.setattr(peer_link, "_http", lambda *a, **k: (_ for _ in ()).throw(OSError("down")))
    ok, d = send.reply("hello", "tok", session=SID)
    assert not ok and d["owner_error"] == "down"


def test_a_reply_peer_may_reply_but_never_passes_one_on(frozen_here, monkeypatch):
    code, _ = devices.mint_code("red2 reply", reply=True, search=True)
    got = devices.redeem(code, "x")
    assert got["reply"] and got["search"]
    tok = got["token"]
    monkeypatch.setattr(auth, "gate", lambda b: (None, {"status": 401, "error": "no"}))
    user, _ = auth.reply_peer(tok)
    assert user and user["peer"] == "red2 reply"
    assert auth._device(tok) is None                     # no other route
    called = []
    monkeypatch.setattr(peer_link, "reply_link", lambda host: called.append(host) or {})
    ok, d = send.reply("hello", tok, session=SID)        # frozen here too
    assert not ok and d["code"] == "frozen" and called == []
    ok, d = send.reply("hello", tok, session="bbbbbbbb-2222-4222-8222-222222222222",
                       mode="branch")
    assert not ok and d["code"] == "peer_reply"
    assert not auth.reply_peer("nonsense")[0]


def test_reply_link_finds_the_hosts_server_by_alias_host_or_name(monkeypatch, tmp_path):
    monkeypatch.setattr(peer_link, "store_dir", lambda: tmp_path)
    monkeypatch.setattr(peer_link.config, "peers", lambda *a, **k: {})
    (tmp_path / "desk.json").write_text(
        '{"alias": "desk", "url": "https://r5", "token": "a", "reply": true, '
        '"server_name": "red5"}')
    (tmp_path / "red4.json").write_text(
        '{"alias": "red4", "url": "https://r4", "token": "b", "search": true}')
    assert peer_link.reply_link("red5")["url"] == "https://r5"
    assert peer_link.reply_link("red5.lan")["url"] == "https://r5"
    assert peer_link.reply_link("red4") is None            # search only
    assert peer_link.reply_link("") is None
