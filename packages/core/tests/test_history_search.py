"""`media find` and the `search_history` tool: the agent's way into the chat
index (history_search.py; David, 12 Nov 2026).

The index itself is the canvas's (agent_media_server.search), and reading the
real one here would read this machine's own transcripts, so every test puts a
stub module in its place and checks what is asked of it and how its answer is
read back.
"""

from __future__ import annotations

import json
import sys
import time
import types

import pytest

from agent_media_core import history_search


def _answer(**over) -> dict:
    out = {
        "q": "gauge from:me",
        "terms": ["gauge"],
        "sort": "newest",
        "tools": False,
        "threads": [],
        "messages": [
            {"session": "5f8c1b2e-aaaa", "message": "8a1f0c44-bbbb", "role": "user",
             "at": time.time(), "kind": "text",
             "snippet": {"text": "…the gauge was\nreading high…", "match": [[5, 10]]},
             "thread": {"title": "Sasonica rebuild", "project": "p-agent-media",
                        "harness": "claude", "live": True, "archived": False}},
        ],
        "next": None,
        "indexing": False,
    }
    out.update(over)
    return out


@pytest.fixture
def stub(monkeypatch):
    """A stand-in for the canvas's search module: records the call, answers
    what the test says."""
    calls: list[dict] = []
    mod = types.ModuleType("agent_media_server.search")
    mod.OPERATORS = (("from:me", "whose words", "from:me kiwi"),)
    state = {"ok": True, "out": _answer()}

    def answer(q, **kw):
        calls.append({"q": q, **kw})
        return state["ok"], state["out"]

    mod.answer = answer
    pkg = types.ModuleType("agent_media_server")
    pkg.search = mod
    monkeypatch.setitem(sys.modules, "agent_media_server", pkg)
    monkeypatch.setitem(sys.modules, "agent_media_server.search", mod)
    return types.SimpleNamespace(calls=calls, state=state)


def test_run_passes_the_query_and_its_narrowing(stub):
    history_search.run("gauge from:me", limit=4, sort="best", tools=True,
                       session="abc", memory=True, ask=True)
    assert stub.calls == [{"q": "gauge from:me", "limit": 4, "sort": "best",
                           "tools": True, "memory": True, "session": "abc",
                           "translate_nl": True}]


def test_each_sort_pages_by_its_own_parameter(stub):
    """§6.14: `before` for newest, `after` for oldest, `offset` for best. The
    caller passes the last answer's `next` and does not have to know which."""
    for sort, key in (("newest", "before"), ("oldest", "after"), ("best", "offset")):
        stub.calls.clear()
        history_search.run("gauge", sort=sort, cursor="123")
        assert stub.calls[0][key] == "123", sort
        assert stub.calls[0]["sort"] == sort


def test_a_refused_query_is_its_own_words(stub):
    stub.state["ok"] = False
    stub.state["out"] = {"error": "from: is me or agent", "status": 400}
    with pytest.raises(ValueError, match="from: is me or agent"):
        history_search.run("from:nobody kiwi")


def test_without_the_canvas_package_it_says_so(monkeypatch):
    """Core never depends on the server package: a host without it is told,
    not handed an ImportError."""
    monkeypatch.setitem(sys.modules, "agent_media_server", None)
    with pytest.raises(history_search.NotHere, match="agent-media-server"):
        history_search.run("gauge")


def test_text_is_three_lines_a_hit_with_the_ids_to_open_it():
    out = _answer()
    lines = history_search.text(out).splitlines()
    assert lines[0] == "1 hit, newest first:"
    assert " · you · Sasonica rebuild · p-agent-media" in lines[1]
    # A snippet that ran over two lines in the transcript is read back as one.
    assert lines[2].strip() == "…the gauge was reading high…"
    assert lines[3].strip() == "session 5f8c1b2e-aaaa  message 8a1f0c44-bbbb"
    assert "session" not in history_search.text(out, ids=False)


def test_text_says_what_a_question_became_and_when_it_did_not():
    out = _answer(translated={"from": "what did I say about the gauge", "to": "gauge from:me"})
    assert history_search.text(out).startswith("read “what did I say about the gauge” as: gauge from:me")
    # Asked for and nothing came back: said, so an empty answer is not read
    # as an empty history.
    plain = history_search.text(_answer(messages=[]), asked=True)
    assert "searched as typed" in plain and "no hits" in plain


def test_text_names_the_cursor_threads_memory_and_a_catching_up_index():
    out = _answer(next=1794432161.023, indexing=True,
                  threads=[{"title": "Gauge notes"}],
                  memory={"available": True, "items": [{"text": "David prefers\nthe gauge in rems"}]})
    said = history_search.text(out)
    assert "more hits: ask again with --page 1794432161.023" in said
    assert "threads matching too: Gauge notes" in said
    assert "memory · David prefers the gauge in rems" in said
    assert "catching up" in said


def test_when_a_hit_is_placed_by_the_clock_today_and_the_date_before_that():
    now = time.mktime((2026, 11, 12, 18, 30, 0, 0, 0, -1))
    assert history_search._when(now - 3600, now) == "17:30"
    assert history_search._when(now - 3 * 86400, now).startswith("Mon 09 Nov")
    assert "2025" in history_search._when(now - 400 * 86400, now)


# --- the command ----------------------------------------------------------------

def _find(capsys, *argv) -> tuple[int, str]:
    from agent_media_core import cli

    code = cli.main(["find", *argv])
    return code, capsys.readouterr()


def test_media_find_prints_the_hits_and_json_prints_the_answer(stub, capsys):
    code, cap = _find(capsys, "gauge", "from:me")
    assert code == 0 and "Sasonica rebuild" in cap.out
    assert stub.calls[0]["limit"] == history_search.LIMIT

    code, cap = _find(capsys, "--json", "gauge")
    assert code == 0 and json.loads(cap.out)["terms"] == ["gauge"]


def test_media_find_answers_2_for_no_words_and_a_refused_query(stub, capsys):
    code, cap = _find(capsys)
    assert code == 2 and "what should I search for" in cap.err

    stub.state["ok"] = False
    stub.state["out"] = {"error": "from: is me or agent", "status": 400}
    code, cap = _find(capsys, "from:nobody", "kiwi")
    assert code == 2 and "from: is me or agent" in cap.err


def test_media_find_answers_1_for_nothing_found(stub, capsys):
    """A shell can tell "nothing matched" from "here are the hits" without
    reading the words."""
    stub.state["out"] = _answer(messages=[], threads=[])
    code, cap = _find(capsys, "zzqqxx")
    assert code == 1 and "no hits" in cap.out


def test_media_find_operators_lists_the_query_language(stub, capsys):
    code, cap = _find(capsys, "--operators")
    assert code == 0 and "from:me" in cap.out and "whose words" in cap.out
    assert not stub.calls, "the listing asks the index nothing"
