"""`GET /search` and the index behind it (search.py, server-contract.md §6.14),
and the log's `around=` jump.

Every transcript is a synthetic file under the throwaway CLAUDE_CONFIG_DIR /
CODEX_HOME / PI_CODING_AGENT_DIR / HERMES_HOME the fixture points at; the
index lives under the throwaway XDG_STATE_HOME (conftest). The memory store is
never called: `org._memory_call` and `org._memories` are fakes.
"""

from __future__ import annotations

import json
import os
import sqlite3

import pytest

from agent_media_server import org, search, sessions, transcript
from test_contract import AUTH, SID, SID2, call, server, signed_in, typed  # noqa: F401
from test_transcript import Script, iso

SID3 = "11111111-2222-4333-8444-555555555555"
PI_SID = "019f87ca-4bad-7fc9-9983-4c95d7fb896a"
CODEX_SID = "019c8037-ed03-7d02-afd5-a3dcdd62ce60"


class Sess(Script):
    """A transcript for any session id, in any folder."""

    def __init__(self, path, sid, cwd="/w", entrypoint="cli"):
        super().__init__(path)
        self.sid, self.cwd, self.entry = sid, cwd, entrypoint

    def base(self, kind, **kw):
        rec = super().base(kind, **kw)
        rec.update(sessionId=self.sid, cwd=self.cwd, entrypoint=self.entry)
        return rec


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """Throwaway harness homes, no memory store, no live thread list."""
    for var, sub in (("CODEX_HOME", "codex"), ("PI_CODING_AGENT_DIR", "pi"),
                     ("HERMES_HOME", "hermes")):
        monkeypatch.setenv(var, str(tmp_path / sub))
    monkeypatch.setattr(sessions, "sessions_index", lambda: [])
    monkeypatch.setattr(org, "_memory_call", lambda *a, **k: None)
    monkeypatch.setattr(org, "_memories", lambda q, n: [])
    monkeypatch.setattr(search, "memory_available", lambda: False)
    return tmp_path


def claude(home, sid, cwd="/w", entrypoint="cli", project="-w"):
    return Sess(home / "claude" / "projects" / project / f"{sid}.jsonl", sid, cwd, entrypoint)


def ask(q, **kw):
    ok, out = search.search(q, "tok", **kw)
    assert ok, out
    return out


@pytest.fixture()
def gate(monkeypatch):
    from agent_media_server import auth

    monkeypatch.setattr(auth, "gate", lambda bearer: ({"username": "david"}, None)
                        if bearer == "tok" else (None, {"error": "no", "status": 401}))


# --- the index ------------------------------------------------------------------


def test_words_both_sides_and_titles_are_found(home, gate):
    s = claude(home, SID)
    s.write({"type": "custom-title", "customTitle": "Drone batteries", "sessionId": SID})
    s.prompt("How long do the LiPo packs last?")
    s.thinking("Checking the discharge curve first.")
    s.tool("Bash", {"command": "grep -r cycles notes/"}, "t1")
    s.result("t1", "cycles: 300")
    s.text("About three hundred cycles before the capacity fades.")
    s.end_turn()
    out = ask("capacity")
    [hit] = out["messages"]
    assert hit["session"] == SID and hit["role"] == "assistant" and hit["kind"] == "text"
    assert hit["thread"]["title"] == "Drone batteries"
    text, [(a, b)] = hit["snippet"]["text"], hit["snippet"]["match"]
    assert text[a:b].lower() == "capacity"
    # The listener's words, the narration too.
    assert [m["role"] for m in ask("lipo")["messages"]] == ["user"]
    assert ask("discharge")["messages"][0]["role"] == "assistant"
    # The title is a thread hit.
    [t] = ask("batteries")["threads"]
    assert t["session"] == SID and t["match"]["title"] == [[6, 15]]
    # The message id is the log's.
    ids = [m["id"] for m in transcript.messages(SID)[0]]
    assert hit["message"] in ids


def test_tool_steps_only_with_tools(home, gate):
    s = claude(home, SID)
    s.prompt("look around")
    s.tool("Bash", {"command": "ls /srv/zebra-config"}, "t1")
    s.result("t1", "a b c")
    s.text("Done.")
    s.end_turn()
    assert ask("zebra")["messages"] == []
    [hit] = ask("zebra", tools=True)["messages"]
    assert hit["kind"] == "tool" and "zebra" in hit["snippet"]["text"]


def test_prefix_phrases_and_nothing_to_search(home, gate):
    s = claude(home, SID)
    s.prompt("the follow-along clock drifts")
    s.text("Fixed the clock.")
    s.end_turn()
    assert len(ask("follow-al")["messages"]) == 1          # last word is a prefix
    assert len(ask('"along clock"')["messages"]) == 1
    assert ask('"clock along"')["messages"] == []
    ok, err = search.search("  ?! ", "tok")
    assert not ok and err["status"] == 400


def test_growth_is_indexed_incrementally_and_the_open_turn_is_rebuilt(home, gate):
    s = claude(home, SID)
    s.prompt("first question about kiwis")
    s.text("kiwi answer", stop="tool_use")
    assert len(ask("kiwi")["messages"]) == 2
    conn = search._connect()
    resume1 = conn.execute("SELECT resume FROM files").fetchone()[0]
    assert resume1 == 0                                  # the first prompt
    # The open turn grows: rebuilt from the prompt, not duplicated.
    s.text("more about the kiwi")
    s.end_turn()
    s.prompt("second question about emus")
    s.text("emu answer")
    s.end_turn()
    hits = ask("kiwi")["messages"]
    assert len(hits) == 2 and len({h["message"] for h in hits}) == 2
    assert len(ask("emu")["messages"]) == 2
    assert conn.execute("SELECT resume FROM files").fetchone()[0] > resume1
    # An unchanged file is not read again.
    assert search.refresh()["changed"] == 0


def test_a_rewritten_file_starts_over(home, gate):
    s = claude(home, SID)
    s.prompt("apples")
    s.text("apple reply")
    s.end_turn()
    assert ask("apple")["messages"]
    s.path.write_bytes(b"")
    s.prompt("pears only now")
    s.end_turn()
    assert ask("apple")["messages"] == []
    assert len(ask("pears")["messages"]) == 1


def test_excluded_folders_and_machinery_are_not_answered(home, gate, monkeypatch):
    monkeypatch.setenv("MEDIA_SESSIONS_EXCLUDE_CWD", str(home / "meridian"))
    m = claude(home, SID, cwd=str(home / "meridian"), project="-meridian")
    m.prompt("gateway walrus")
    m.end_turn()
    p = claude(home, SID2, entrypoint="sdk-cli", project="-pipe")
    p.prompt("pipeline walrus")
    p.end_turn()
    t = claude(home, SID3)
    t.prompt("chat walrus")
    t.end_turn()
    assert [h["session"] for h in ask("walrus")["messages"]] == [SID3]
    conn = search._connect()
    assert conn.execute("SELECT count(*) FROM docs WHERE session=?", (SID,)).fetchone()[0] == 0
    # A headless thread sessiond holds is not machinery.
    monkeypatch.setattr(search, "_owned_headless", lambda: {SID2})
    assert sorted(h["session"] for h in ask("walrus")["messages"]) == sorted([SID2, SID3])


def test_recency_order_and_paging(home, gate):
    s = claude(home, SID)
    for i in range(5):
        s.prompt(f"otter number {i}")
        s.end_turn()
    page = ask("otter", limit=2)
    assert [h["snippet"]["text"] for h in page["messages"]] == ["otter number 4", "otter number 3"]
    assert page["next"] == page["messages"][-1]["at"]
    page2 = ask("otter", limit=2, before=page["next"])
    assert [h["snippet"]["text"] for h in page2["messages"]] == ["otter number 2", "otter number 1"]
    assert page2["threads"] == []
    ok, err = search.search("otter", "tok", before="soon")
    assert not ok and err["status"] == 400


def test_oldest_first_pages_with_after(home, gate):
    s = claude(home, SID)
    for i in range(5):
        s.prompt(f"otter number {i}")
        s.end_turn()
    page = ask("otter", limit=2, sort="oldest")
    assert page["sort"] == "oldest"
    assert [h["snippet"]["text"] for h in page["messages"]] == ["otter number 0", "otter number 1"]
    assert page["next"] == page["messages"][-1]["at"]
    page2 = ask("otter", limit=2, sort="oldest", after=page["next"])
    assert [h["snippet"]["text"] for h in page2["messages"]] == ["otter number 2", "otter number 3"]
    assert page2["threads"] == [] and "memory" not in page2
    # `before` is newest's cursor: under oldest it is not a later page.
    assert ask("otter", limit=2, sort="oldest", before=page["next"])["messages"] == page["messages"]
    last = ask("otter", limit=2, sort="oldest", after=page2["next"])
    assert [h["snippet"]["text"] for h in last["messages"]] == ["otter number 4"]
    assert last["next"] is None


def test_best_match_ranks_and_pages_with_offset(home, gate):
    s = claude(home, SID)
    s.prompt("heron heron heron heron")
    s.end_turn()
    s.prompt("a long note that mentions one heron among very many other words of no interest")
    s.end_turn()
    s.prompt("heron heron")
    s.end_turn()
    page = ask("heron", limit=2, sort="best")
    assert [h["snippet"]["text"] for h in page["messages"]] == ["heron heron heron heron", "heron heron"]
    assert page["next"] == 2
    page2 = ask("heron", limit=2, sort="best", offset=page["next"])
    assert [h["snippet"]["text"][:6] for h in page2["messages"]] == ["a long"]
    assert page2["next"] is None and page2["threads"] == []


def test_since_and_who_narrow_on_the_server(home, gate):
    s = claude(home, SID)
    for i in range(4):
        s.prompt(f"badger question {i}")
        s.text(f"badger answer {i}")
        s.end_turn()
    every = ask("badger")["messages"]
    assert len(every) == 8
    cut = sorted(h["at"] for h in every)[4]
    recent = ask("badger", since=cut)["messages"]
    assert len(recent) == 4 and all(h["at"] >= cut for h in recent)
    mine = ask("badger", who="me", limit=3)
    assert [h["role"] for h in mine["messages"]] == ["user"] * 3
    # The page is full of the owner's words, so paging still works.
    rest = ask("badger", who="me", limit=3, before=mine["next"])["messages"]
    assert [h["snippet"]["text"] for h in rest] == ["badger question 0"]
    assert {h["role"] for h in ask("badger", who="agent")["messages"]} == {"assistant"}


def test_a_bad_sort_or_narrowing_is_400(home, gate):
    claude(home, SID).prompt("otter")
    for kw in ({"sort": "loudest"}, {"who": "them"}, {"rooms": "some"}, {"since": "today"},
               {"after": "x", "sort": "oldest"}, {"offset": "two", "sort": "best"}):
        ok, err = search.search("otter", "tok", **kw)
        assert not ok and err["status"] == 400, kw


def test_recaps_and_projects_are_thread_hits(home, gate, monkeypatch):
    s = claude(home, SID, cwd="/home/x/projects/agent-media")
    s.prompt("hello")
    s.system("away_summary", content="Wired the speech bar to the gauge. (disable recaps in /config)")
    s.end_turn()
    monkeypatch.setattr(sessions, "project_of", lambda cwd, folder="": "p-agent-media")
    search.rebuild()
    [t] = ask("gauge")["threads"]
    assert t["recap"] == "Wired the speech bar to the gauge."
    assert "recap" in t["match"]
    assert ask("agent-media")["threads"][0]["project"] == "p-agent-media"
    # The thread list's own name wins over the index's.
    monkeypatch.setattr(sessions, "sessions_index", lambda: [
        {"session": SID, "title": "Speech gauge", "live": True, "recap": None,
         "archived": False, "project": "p-agent-media"}])
    search._LISTED[0] = (0.0, {})
    [t] = ask("gauge")["threads"]
    assert t["title"] == "Speech gauge" and t["live"] is True


def test_pi_codex_and_hermes(home, gate):
    pi = home / "pi" / "sessions" / "--w--"
    pi.mkdir(parents=True)
    (pi / f"2026-07-22T03-06-49-645Z_{PI_SID}.jsonl").write_text("\n".join(json.dumps(r) for r in [
        {"type": "session", "id": PI_SID, "timestamp": iso(1790000000), "cwd": "/w"},
        {"type": "session_info", "id": "i1", "timestamp": iso(1790000001), "name": "Pi chat"},
        {"type": "message", "id": "m1", "timestamp": iso(1790000002),
         "message": {"role": "user", "content": [{"type": "text", "text": "pi about narwhals"}]}},
        {"type": "message", "id": "m2", "timestamp": iso(1790000003),
         "message": {"role": "assistant", "content": [
             {"type": "toolCall", "name": "bash", "arguments": {"command": "ls narwhal-dir"}}]}},
    ]) + "\n")
    cx = home / "codex" / "sessions" / "2026" / "02" / "21"
    cx.mkdir(parents=True)
    (cx / f"rollout-2026-02-21T12-41-14-{CODEX_SID}.jsonl").write_text("\n".join(json.dumps(r) for r in [
        {"timestamp": iso(1790000010), "type": "session_meta", "payload": {"id": CODEX_SID, "cwd": "/w"}},
        {"timestamp": iso(1790000011), "type": "response_item", "payload": {
            "type": "message", "role": "user", "content": [{"type": "input_text", "text": "# AGENTS.md narwhal"}]}},
        {"timestamp": iso(1790000012), "type": "response_item", "payload": {
            "type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "codex narwhal reply"}]}},
    ]) + "\n")
    hdir = home / "hermes"
    hdir.mkdir()
    db = sqlite3.connect(hdir / "state.db")
    db.executescript("CREATE TABLE sessions (id TEXT PRIMARY KEY, cwd TEXT, title TEXT, started_at REAL);"
                     "CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT,"
                     " content TEXT, tool_name TEXT, timestamp REAL);")
    db.execute("INSERT INTO sessions VALUES ('20260921_102508_f74b02', '/w', 'Hermes chat', 1790000020)")
    db.execute("INSERT INTO messages VALUES (1, '20260921_102508_f74b02', 'user', 'hermes narwhal', NULL, 1790000021)")
    db.commit()
    got = ask("narwhal")["messages"]
    by = {h["session"]: h for h in got}
    assert set(by) == {PI_SID, CODEX_SID, "20260921_102508_f74b02"}
    assert by[PI_SID]["message"] == "m1" and by[PI_SID]["thread"]["title"] == "Pi chat"
    assert by[CODEX_SID]["role"] == "assistant"          # the preamble is not a message
    assert by["20260921_102508_f74b02"]["thread"]["harness"] == "hermes"
    # Hermes resumes from the last id it saw.
    db.execute("INSERT INTO messages VALUES (2, '20260921_102508_f74b02', 'assistant', 'second narwhal', NULL, 1790000022)")
    db.commit()
    db.close()
    assert sum(h["session"] == "20260921_102508_f74b02" for h in ask("narwhal")["messages"]) == 2
    assert any(h["session"] == PI_SID for h in ask("narwhal", tools=True)["messages"]
               if h["kind"] == "tool")


def test_rebuild_and_schema_bump(home, gate, monkeypatch):
    s = claude(home, SID)
    s.prompt("heron")
    s.end_turn()
    assert ask("heron")["messages"]
    got = search.rebuild()
    assert got["changed"] == 1 and search.stats()["messages"] == 1
    monkeypatch.setattr(search, "SCHEMA_VERSION", 99)
    search._reset_for_tests()
    assert search.stats()["messages"] == 0            # wiped; the next query reads again
    monkeypatch.setattr(search, "memory_available", lambda: False)
    assert ask("heron")["messages"]


def test_the_index_lives_under_the_state_dir(home, gate, tmp_path):
    claude(home, SID).prompt("x")
    ask("x")
    assert search.db_path() == tmp_path / "state" / "agent-media" / "search.db"
    assert search.db_path().exists()


# --- memory ---------------------------------------------------------------------


def test_memory_section_only_when_available(home, gate, monkeypatch):
    claude(home, SID).prompt("tea")
    assert ask("tea")["memory"] == {"available": False}
    monkeypatch.setattr(search, "memory_available", lambda: True)
    monkeypatch.setattr(org, "_memories", lambda q, n: [
        {"id": "m1", "user": "ryer", "score": 0.9, "text": "David drinks tea"}])
    assert ask("tea")["memory"] == {"available": True, "items": [
        {"id": "m1", "user": "ryer", "score": 0.9, "text": "David drinks tea"}]}
    assert "memory" not in ask("tea", memory=False)


def test_memory_detection(monkeypatch, tmp_path):
    # The real memory_available, with a fake store under it.
    monkeypatch.setenv("HOME", str(tmp_path / "nohome"))
    monkeypatch.setenv("PATH", str(tmp_path / "nobin"))
    for k in ("AGENT_MEMORY_HIPPOCAMPUS_URL", "HIPPOCAMPUS_URL"):
        monkeypatch.delenv(k, raising=False)
    calls = []
    monkeypatch.setattr(org, "_memory_call", lambda *a, **k: calls.append(a) or {"status": "ok"})
    search._MEMORY[0] = (0.0, False)
    assert search.memory_available() is False and calls == []   # not installed: never asked
    monkeypatch.setenv("HIPPOCAMPUS_URL", "http://127.0.0.1:9")
    search._MEMORY[0] = (0.0, False)
    assert search.memory_available() is True and calls == [("GET", "/health")]
    search._MEMORY[0] = (0.0, False)
    monkeypatch.setattr(org, "_memory_call", lambda *a, **k: None)
    assert search.memory_available() is False


# --- the route ------------------------------------------------------------------


def test_route_shape_and_auth(server, signed_in, home):
    s = claude(home, SID)
    s.prompt("route kestrel")
    s.end_turn()
    res, obj = call(server, "GET", "/search?q=kestrel", headers=AUTH)
    assert res.status == 200, obj
    assert set(obj) == {"ok", "q", "terms", "query", "tools", "sort", "threads", "messages",
                        "next", "indexing", "memory", "docs"}
    assert obj["query"] == {"groups": [[["kestrel"]]], "not": [], "filters": {}}
    [m] = obj["messages"]
    assert set(m) == {"session", "message", "role", "at", "kind", "snippet", "thread"}
    assert set(m["thread"]) == {"title", "project", "harness", "live", "archived"}
    assert res.getheader("Access-Control-Allow-Origin") == "*"
    res, obj = call(server, "GET", "/search?q=", headers=AUTH)
    assert res.status == 400 and obj["ok"] is False
    # The sort and the narrowing ride in the query.
    res, obj = call(server, "GET", "/search?q=kestrel&sort=oldest&who=agent", headers=AUTH)
    assert res.status == 200 and obj["sort"] == "oldest" and obj["messages"] == []
    res, obj = call(server, "GET", "/search?q=kestrel&sort=best&who=me&rooms=none&since=1",
                    headers=AUTH)
    assert res.status == 200 and [m["role"] for m in obj["messages"]] == ["user"]


# --- the jump -------------------------------------------------------------------


def _thread(home, n):
    s = claude(home, SID)
    for i in range(n):
        s.prompt(f"question {i}")
        s.text(f"answer {i}")
        s.end_turn()
    return [m["id"] for m in transcript.messages(SID)[0]] if n <= 30 else None


def test_window_around():
    msgs = [{"id": str(i)} for i in range(20)]
    win, older, newer = transcript.window_around(msgs, "10", limit=4, most=50)
    assert [m["id"] for m in win] == [str(i) for i in range(5, 20)] and older and not newer
    win, older, newer = transcript.window_around(msgs, "10", limit=4, most=8)
    assert [m["id"] for m in win] == ["5", "6", "7", "8"] and older and newer
    win, older, newer = transcript.window_around(msgs, "2", limit=4, most=50)
    assert win[0]["id"] == "0" and not older
    assert transcript.window_around(msgs, "nope", limit=4, most=50) is None


def test_log_around_a_message(server, signed_in, home, monkeypatch):
    monkeypatch.setattr(sessions, "live_sessions", lambda: {})
    ids = _thread(home, 30)
    target = ids[10]
    res, obj = call(server, "GET", f"/conversation/log?session={SID}&around={target}&limit=4",
                    headers=AUTH)
    assert res.status == 200, obj
    got = [m["id"] for m in obj["messages"]]
    assert got == ids[5:] and obj["older"] is True
    assert obj["around"] == {"id": target, "found": True, "newer": False}
    assert obj["newer"] is False
    # Not there: the newest page, and says so.
    res, obj = call(server, "GET", f"/conversation/log?session={SID}&around=gone&limit=4",
                    headers=AUTH)
    assert [m["id"] for m in obj["messages"]] == ids[-4:]
    assert obj["around"] == {"id": "gone", "found": False, "newer": False}
    # Without `around`, the envelope is as it was.
    res, obj = call(server, "GET", f"/conversation/log?session={SID}&messages=1", headers=AUTH)
    assert "around" not in obj and "newer" not in obj


def test_log_around_far_back_is_a_window(server, signed_in, home, monkeypatch):
    from agent_media_server import threads

    monkeypatch.setattr(sessions, "live_sessions", lambda: {})
    monkeypatch.setattr(threads, "MESSAGES_MAX", 10)
    _thread(home, 30)
    ids = [m["id"] for m in transcript.messages(SID)[0]]
    res, obj = call(server, "GET", f"/conversation/log?session={SID}&around={ids[20]}&limit=4",
                    headers=AUTH)
    assert [m["id"] for m in obj["messages"]] == ids[15:19]
    assert obj["newer"] is True and obj["older"] is True


def test_start_honours_the_switch(monkeypatch):
    monkeypatch.setenv("MEDIA_SEARCH_INDEX", "0")
    started = []
    monkeypatch.setattr(search.threading, "Thread", lambda *a, **k: started.append(1))
    search._STARTED.clear()
    search.start()
    assert started == [] and not search._STARTED.is_set()


def test_bodies_strip_markers_and_keep_asks():
    text, tools = search.bodies({"parts": [
        {"type": "text", "text": "See this [[visual: a box]] figure."},
        {"type": "reasoning", "text": "", "redacted": True},
        {"type": "ask", "ask": [{"question": "Which colour?"}], "answer": "Blue"},
        {"type": "tool", "name": "Read", "title": "Read a file", "input_summary": "/x.py",
         "result_summary": "3 lines"}]})
    assert "[[" not in text and "figure" in text and "Which colour?" in text and "Blue" in text
    assert tools == "Read\nRead a file\n/x.py\n3 lines"


def test_nothing_real_is_read(home):
    # The harness homes are the throwaway ones.
    for f, _s in search._claude_files() + search._pi_files() + search._codex_files():
        assert f.startswith(str(home)), f
    assert not os.environ.get("CLAUDE_CONFIG_DIR", "").startswith(os.path.expanduser("~/.claude"))


def test_one_thread_find_bar(home, gate):
    a, b = claude(home, SID), claude(home, SID2)
    for i in range(3):
        a.prompt(f"marten here {i}")
        a.end_turn()
    b.prompt("marten elsewhere")
    b.end_turn()
    page = ask("marten", session=SID, sort="oldest", limit=2)
    assert [h["snippet"]["text"] for h in page["messages"]] == ["marten here 0", "marten here 1"]
    assert {h["session"] for h in page["messages"]} == {SID}
    assert page["total"] == 3 and page["session"] == SID
    assert page["threads"] == [] and "memory" not in page and "docs" not in page
    page2 = ask("marten", session=SID, sort="oldest", limit=2, after=page["next"])
    assert [h["snippet"]["text"] for h in page2["messages"]] == ["marten here 2"]
    assert "total" not in page2
    assert ask("marten", session="no-such-thread")["total"] == 0
    assert len(ask("marten")["messages"]) == 4


def test_one_thread_total_counts_messages_not_hits(home, gate):
    s = claude(home, SID)
    s.prompt("grebe in words")
    s.end_turn()
    s.tool("Bash", {"command": "grep grebe notes"}, "t1")
    s.result("t1", "grebe found")
    s.text("grebe said")
    s.end_turn()
    page = ask("grebe", session=SID, tools=True, sort="oldest")
    assert len(page["messages"]) > len({h["message"] for h in page["messages"]})
    assert page["total"] == len({h["message"] for h in page["messages"]})


def test_a_stale_thread_list_answers_while_it_is_read_again(home, gate, monkeypatch):
    """The list takes seconds to build: a search after the cache runs out
    answers with the old one and reads it again behind the query."""
    import threading
    import time

    gate_open = threading.Event()
    calls = []

    def slow():
        calls.append(1)
        gate_open.wait(5)
        return [{"session": SID, "title": "fresh"}]

    monkeypatch.setattr(sessions, "sessions_index", slow)
    monkeypatch.setattr(search, "_LISTED", [(time.monotonic() - 60, {SID: {"session": SID, "title": "old"}})])
    t0 = time.monotonic()
    assert search._listed()[SID]["title"] == "old"
    assert time.monotonic() - t0 < 1
    gate_open.set()
    for _ in range(50):
        if search._LISTED[0][1].get(SID, {}).get("title") == "fresh":
            break
        time.sleep(0.05)
    assert search._listed()[SID]["title"] == "fresh" and len(calls) == 1


def test_one_thread_skips_the_thread_list(home, gate, monkeypatch):
    claude(home, SID).prompt("osprey here")
    monkeypatch.setattr(search, "_listed", lambda: (_ for _ in ()).throw(AssertionError("listed")))
    page = ask("osprey", session=SID)
    assert page["total"] == 1 and len(page["messages"]) == 1


# --- the operators --------------------------------------------------------------


def test_a_minus_term_and_or_and_a_phrase(home, gate):
    s = claude(home, SID)
    s.prompt("the heron waits by the pond")
    s.text("the heron left and a kingfisher came")
    s.end_turn()
    s2 = claude(home, SID2)
    s2.prompt("the heron waits by the gate")
    s2.end_turn()
    # -word: the messages that do not have it.
    assert [h["session"] for h in ask("heron -kingfisher")["messages"]] == [SID2, SID]
    assert [h["snippet"]["text"] for h in ask("heron -pond -gate")["messages"]] \
        == ["the heron left and a kingfisher came"]
    assert [h["role"] for h in ask('heron -"by the pond" -gate')["messages"]] == ["assistant"]
    assert ask('heron -"by the" -kingfisher')["messages"] == []
    # OR, and its `|`.
    assert len(ask("kingfisher OR pond")["messages"]) == 2
    assert len(ask("kingfisher | pond")["messages"]) == 2
    assert len(ask("heron AND pond")["messages"]) == 1
    # A -word takes the thread hit away too.
    s2.write({"type": "custom-title", "customTitle": "Heron gate", "sessionId": SID2})
    search.rebuild()
    assert {t["session"] for t in ask("heron")["threads"]} == {SID, SID2}
    assert [t["session"] for t in ask("heron -gate")["threads"]] == [SID]
    assert ask("heron -heron")["threads"] == []
    assert {t["session"] for t in ask("gate OR pond")["threads"]} == {SID, SID2}


def test_from_narrows_to_one_side(home, gate):
    s = claude(home, SID)
    s.prompt("who left the quail out")
    s.text("the quail is in")
    s.end_turn()
    assert [h["role"] for h in ask("quail from:me")["messages"]] == ["user"]
    assert [h["role"] for h in ask("quail from:agent")["messages"]] == ["assistant"]
    # It wins over who=, and from: takes the same words the route does.
    assert [h["role"] for h in ask("quail from:me", who="agent")["messages"]] == ["user"]
    ok, err = search.search("quail from:nobody", "tok")
    assert not ok and err["status"] == 400 and "me or agent" in err["error"]


def test_in_thread_and_harness_narrow_by_the_thread(home, gate, monkeypatch):
    a = claude(home, SID, cwd="/home/x/projects/agent-media")
    a.write({"type": "custom-title", "customTitle": "Speech gauge", "sessionId": SID})
    a.prompt("the marmot problem")
    a.end_turn()
    b = claude(home, SID2, cwd="/home/x/projects/zebra", project="-zebra")
    b.write({"type": "custom-title", "customTitle": "Stripes", "sessionId": SID2})
    b.prompt("the marmot problem")
    b.end_turn()
    monkeypatch.setattr(sessions, "project_of",
                        lambda cwd, folder="": "p-" + os.path.basename(cwd))
    search.rebuild()
    assert [h["session"] for h in ask("marmot in:zebra")["messages"]] == [SID2]
    assert [h["session"] for h in ask("marmot in:p-agent-media")["messages"]] == [SID]
    assert [h["session"] for h in ask("marmot -in:zebra")["messages"]] == [SID]
    assert [h["session"] for h in ask("marmot thread:stripes")["messages"]] == [SID2]
    assert [h["session"] for h in ask('marmot thread:"speech gauge"')["messages"]] == [SID]
    # A thread hit (its title, recap or project) is narrowed the same way.
    assert [t["session"] for t in ask("stripes in:zebra")["threads"]] == [SID2]
    assert ask("stripes in:agent-media")["threads"] == []
    assert [t["session"] for t in ask("stripes -in:agent-media")["threads"]] == [SID2]
    assert ask("stripes -thread:stripes")["threads"] == []
    assert len(ask("marmot harness:claude")["messages"]) == 2
    assert ask("marmot harness:codex")["messages"] == []
    assert ask("marmot harness:codex")["threads"] == []


def test_kind_picks_the_words_or_the_tool_steps(home, gate):
    s = claude(home, SID)
    s.prompt("look for the ocelot")
    s.tool("Bash", {"command": "grep -r ocelot notes/"}, "t1")
    s.result("t1", "none")
    s.text("No ocelot there.")
    s.end_turn()
    # kind:tool searches the steps without the Advanced setting, and nothing else.
    out = ask("ocelot kind:tool")
    assert [h["kind"] for h in out["messages"]] == ["tool"] and out["tools"] is True
    assert out["threads"] == []
    assert {h["kind"] for h in ask("ocelot kind:text", tools=True)["messages"]} == {"text"}
    ok, err = search.search("ocelot kind:picture", "tok")
    assert not ok and err["status"] == 400 and "text or tool" in err["error"]


def test_after_and_before_take_dates(home, gate):
    import datetime as dt

    s = claude(home, SID)
    s.write({"type": "custom-title", "customTitle": "The gecko", "sessionId": SID})
    s.prompt("the gecko now")
    s.end_turn()
    at = ask("gecko")["messages"][0]["at"]
    day = dt.datetime.fromtimestamp(at)
    before, after = (day - dt.timedelta(days=1)).date(), (day + dt.timedelta(days=1)).date()
    assert len(ask(f"gecko after:{before}")["messages"]) == 1
    assert ask(f"gecko after:{after}")["messages"] == []
    assert len(ask(f"gecko before:{after}")["messages"]) == 1
    assert ask(f"gecko before:{before}")["messages"] == []
    assert len(ask(f"gecko after:{day.year} before:{day.year + 1}")["messages"]) == 1
    # A thread hit follows before: and after: too.
    assert [h["session"] for h in ask(f"gecko before:{after}")["threads"]] == [SID]
    assert ask(f"gecko before:{before}")["threads"] == []
    # The words a date takes, read without the index.
    now = dt.datetime.now()
    assert search._when("today") == now.replace(hour=0, minute=0, second=0,
                                                microsecond=0).timestamp()
    assert now.timestamp() - search._when("7d") == pytest.approx(7 * 86400, abs=2)
    assert search._when("2026-11") == dt.datetime(2026, 11, 1).timestamp()
    for bad in ("soonish", "2026-13-01", "11-2026"):
        ok, err = search.search(f"gecko after:{bad}", "tok")
        assert not ok and err["status"] == 400 and "not a date" in err["error"], bad


def test_an_unknown_operator_is_read_as_words(home, gate):
    s = claude(home, SID)
    s.prompt("see notes/vole.md for the vole")
    s.end_turn()
    out = ask("notes:vole")
    assert len(out["messages"]) == 1
    assert out["terms"] == ["notes", "vole"]
    assert out["query"]["filters"] == {}


def test_the_answer_says_how_the_query_was_read(home, gate):
    claude(home, SID).prompt("quokka")
    out = ask('quokka OR "tree kangaroo" -wombat from:me in:zoo kind:text')
    assert out["terms"] == ["quokka", "tree kangaroo"]
    assert out["query"]["groups"] == [[["quokka"], ["tree kangaroo"]]]
    assert out["query"]["not"] == ["wombat"]
    assert out["query"]["filters"] == {"from": "me", "kind": "text",
                                       "in": [{"value": "zoo", "not": False}]}


def test_the_find_bar_counts_under_the_operators(home, gate):
    s = claude(home, SID)
    for i in range(3):
        s.prompt(f"tapir {i}")
        s.text(f"tapir answer {i}")
        s.end_turn()
    out = ask("tapir", session=SID, sort="oldest")
    assert out["total"] == 6
    out = ask("tapir from:me", session=SID, sort="oldest")
    assert out["total"] == 3 and {h["role"] for h in out["messages"]} == {"user"}
