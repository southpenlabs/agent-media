"""`media find` asks the other servers this one searches, and merges their
hits (history_search.ask_peers / merge, 17 Nov 2026)."""

from __future__ import annotations

from agent_media_core import history_search as hs, peer_link


def _hit(sid, at, text="x"):
    return {"session": sid, "message": f"m-{sid}-{at}", "at": at, "role": "assistant",
            "snippet": {"text": text}, "thread": {"title": f"t {sid}"}}


def test_merge_labels_their_hits_and_drops_chats_held_here(monkeypatch):
    from agent_media_server import search

    monkeypatch.setattr(search, "known_sessions", lambda ids: {"held"} & set(ids))
    out = {"messages": [_hit("mine", 50)], "threads": []}
    answers = [
        {"server": "red5", "ok": True, "answer": {"messages": [
            _hit("mine", 40), _hit("held", 45), _hit("theirs", 60)], "threads": [
            {"session": "theirs", "title": "Their chat"}]}},
        {"server": "red4", "ok": False, "error": "timed out"}]
    hs.merge(out, answers, limit=10, sort="newest")
    assert [(h["session"], h.get("server")) for h in out["messages"]] == [
        ("theirs", "red5"), ("mine", None)]
    assert out["threads"] == [{"session": "theirs", "title": "Their chat", "server": "red5"}]
    assert out["peers"] == [{"server": "red5", "ok": True},
                            {"server": "red4", "ok": False, "error": "timed out"}]
    shown = hs.text(out, now=100)
    assert "· on red5" in shown and "server red4 did not answer: timed out" in shown


def test_ask_peers_asks_each_linked_search_server(monkeypatch):
    links = [{"alias": "red5", "url": "https://red5.example", "token": "t5", "search": True,
              "server_name": "red5"},
             {"alias": "down", "url": "https://down.example", "token": "td", "search": True}]
    monkeypatch.setattr(hs, "search_peers", lambda: links)
    seen = []

    def fake_http(url, body, token="", timeout=30.0, method="POST"):
        seen.append((url, token, method))
        if "down" in url:
            raise OSError("connection refused")
        return 200, {"ok": True, "messages": [_hit("theirs", 1)], "threads": []}

    monkeypatch.setattr(peer_link, "_http", fake_http)
    got = hs.ask_peers("heron after:7d", limit=5, sort="oldest")
    assert [(g["server"], g["ok"]) for g in got] == [("red5", True), ("down", False)]
    url, token, method = seen[0]
    assert method == "GET" and token == "t5" and url.startswith("https://red5.example/search?")
    assert "q=heron+after%3A7d" in url and "memory=0" in url and "sort=oldest" in url


def test_search_peers_are_only_links_paired_for_search(monkeypatch, tmp_path):
    monkeypatch.setattr(peer_link, "store_dir", lambda: tmp_path)
    monkeypatch.setattr(peer_link.config, "peers", lambda *a, **k: {})
    (tmp_path / "red5.json").write_text(
        '{"alias": "red5", "url": "https://r5", "token": "a", "search": true}')
    (tmp_path / "phone.json").write_text(
        '{"alias": "phone", "url": "http://p8a:8781", "token": "b"}')
    assert [p["alias"] for p in hs.search_peers()] == ["red5"]


def test_run_asks_peers_on_the_first_page_only_and_obeys_the_switch(monkeypatch):
    from agent_media_server import search

    monkeypatch.setattr(search, "answer", lambda q, **kw: (True, {"q": q, "messages": [],
                                                                  "threads": []}))
    calls = []
    monkeypatch.setattr(hs, "ask_peers", lambda q, **kw: calls.append(q) or [])
    hs.run("heron")                                # the conftest switch: off
    assert calls == []
    monkeypatch.setenv("MEDIA_FIND_PEERS", "1")
    hs.run("heron")
    hs.run("heron", cursor="123")                  # a later page: here only
    assert calls == ["heron"]
