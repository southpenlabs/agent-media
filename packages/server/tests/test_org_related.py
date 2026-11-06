"""Related threads from the org-roam graph (org_related.py), and /org/read
naming the thread behind a session link. Each test builds its own tree."""

from __future__ import annotations

import http.client
import json
import os
import threading
from http.server import ThreadingHTTPServer

import pytest

from agent_media_server import app, auth, org, org_related

A = "aaaaaaaa-0000-0000-0000-000000000001"
B = "bbbbbbbb-0000-0000-0000-000000000002"
C = "cccccccc-0000-0000-0000-000000000003"


def _session(sid: str, title: str) -> str:
    return (f":PROPERTIES:\n:ID:             claude:red5:{sid}\n"
            f":AGENT_SID:      {sid}\n:END:\n#+title: {title}\n"
            f"#+filetags: :claude:\n")


def _zettel(sid: str, n: int, title: str, related: list[str], *, inbox=True) -> str:
    rel = "".join(f"- [[id:{r}][{r}]]\n" for r in related)
    tags = ":zk:inbox:" if inbox else ":zk:"
    return (f":PROPERTIES:\n:ID:          zk:{sid}:{n}\n"
            f":ZK_SOURCE:   claude:red5:{sid}\n:END:\n#+title: {title}\n"
            f"#+filetags: {tags}\n\n- Source :: [[id:claude:red5:{sid}][the thread]]\n\n"
            f"Body.\n\n* Related\n# >>> agent-sessions zk: related (regenerated, do not edit) >>>\n"
            f"{rel}# <<< agent-sessions zk: related <<<\n")


@pytest.fixture()
def tree(tmp_path, monkeypatch):
    root = tmp_path / "org"
    s = root / "roam" / "sessions" / "agent-media"
    z = root / "roam" / "notes"
    s.mkdir(parents=True)
    z.mkdir(parents=True)
    (s / "a.org").write_text(_session(A, "Thread A"))
    (s / "b.org").write_text(_session(B, "Thread B"))
    (s / "c.org").write_text(_session(C, "Thread C"))
    # A's two notes both name B's note; C's note names A's first (only C
    # lists it: the back direction counts too).
    (z / "a1.org").write_text(_zettel(A, 1, "A one", [f"zk:{B}:1", f"zk:{A}:2"]))
    (z / "a2.org").write_text(_zettel(A, 2, "A two", [f"zk:{B}:1"], inbox=False))
    (z / "b1.org").write_text(_zettel(B, 1, "B one", []))
    (z / "c1.org").write_text(_zettel(C, 1, "C one", [f"zk:{A}:1"]))
    (root / "inbox.org").write_text(
        "#+title: Inbox\n\n* TODO Follow up on A\n  :PROPERTIES:\n"
        f"  :SESSION:  [[id:claude:red5:{A}]]\n  :END:\n* Plain heading\n")
    (root / "roam" / "ideas.org").write_text(
        f":PROPERTIES:\n:ID: idea-id\n:END:\n#+title: Ideas\n\nsee [[id:claude:red5:{A}][A]]"
        f" and [[id:zk:{B}:1][B one]]\n")
    monkeypatch.setenv("MEDIA_ORG_DIR", str(root))
    monkeypatch.setattr(auth, "gate", lambda bearer: (
        ({"username": "david"}, {}) if bearer == "good"
        else (None, {"error": "not allowed", "status": 401})))
    monkeypatch.setattr(org_related, "_app_title", lambda sid: "")
    monkeypatch.setattr(org, "_IDS", {"at": 0.0, "map": {}})
    org_related._reset()
    yield root
    org_related._reset()


@pytest.fixture()
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
    srv.daemon_threads = True
    t = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05},
                         daemon=True)
    t.start()
    yield srv.server_address
    srv.shutdown()
    srv.server_close()


def _get(addr, path, bearer="good"):
    conn = http.client.HTTPConnection(*addr, timeout=10)
    conn.request("GET", path, headers={"Authorization": f"Bearer {bearer}"})
    res = conn.getresponse()
    data = json.loads(res.read() or b"{}")
    conn.close()
    return res.status, data


def test_related_ranks_threads_by_paths(tree):
    got = org_related.related_for(A)
    assert got["title"] == "Thread A"
    assert [(r["session"], r["score"]) for r in got["related"]] == [(B, 2), (C, 1)]
    b = got["related"][0]
    assert b["title"] == "Thread B"
    assert b["why"] == [{"note": "B one", "path": "roam/notes/b1.org", "via": "A one"}]
    assert got["related"][1]["why"][0]["note"] == "C one"
    assert all(r["session"] != A for r in got["related"])


def test_what_came_out_of_the_thread(tree):
    got = org_related.related_for(A)
    assert got["zettels"] == [
        {"path": "roam/notes/a1.org", "title": "A one", "draft": True},
        {"path": "roam/notes/a2.org", "title": "A two"}]
    assert got["links"] == [
        {"path": "inbox.org", "at": 3, "title": "Follow up on A", "state": "TODO",
         "kind": "todo"},
        {"path": "roam/ideas.org", "at": 0, "title": "Ideas", "state": "",
         "kind": "note"}]
    # B's own zettel's Source line is not a "link" to B.
    assert org_related.related_for(B)["links"] == []


def test_an_unknown_thread_has_nothing(tree):
    got = org_related.related_for("nope")
    assert got["related"] == [] and got["zettels"] == [] and got["links"] == []


def test_cache_follows_file_changes(tree):
    assert org_related.related_for(C)["related"][0]["session"] == A
    p = tree / "roam" / "notes" / "c1.org"
    p.write_text(_zettel(C, 1, "C one", [f"zk:{B}:1"]))
    st = p.stat()
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000_000))
    # Within the sweep TTL the old answer stands; past it, the new one.
    org_related._STATE["at"] = None
    assert org_related.related_for(C)["related"][0]["session"] == B


def test_route_is_gated_and_answers(tree, server):
    assert _get(server, f"/session/related?session={A}", bearer="bad")[0] == 401
    assert _get(server, "/session/related")[0] == 400
    status, got = _get(server, f"/session/related?session={A}")
    assert status == 200 and got["ok"]
    assert got["related"][0]["session"] == B


def test_read_names_the_thread_behind_a_session_link(tree, server):
    status, note = _get(server, "/org/read?path=roam/ideas.org")
    assert status == 200
    assert note["links"] == [
        {"label": "A", "path": "roam/sessions/agent-media/a.org", "session": A},
        {"label": "B one", "path": "roam/notes/b1.org"}]
