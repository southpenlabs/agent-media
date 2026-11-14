"""One `sessions_index` reads the shelf's manifests once and looks each
folder's project up once (sessions._PASS); outside a list, a changed
manifest is seen at once (14 Nov 2026: half a million stats per list, the
new chat screen's "Could not reach" while CI loaded red2)."""
from __future__ import annotations

import json

from agent_media_server import sessions


def _shelve(tmp_path, monkeypatch, n=3):
    d = tmp_path / "book-tracks"
    d.mkdir()
    for i in range(n):
        (d / f"s{i}.json").write_text(json.dumps({"session": f"s{i}", "folder": f"/f/p-x/t{i}"}))
    monkeypatch.setattr(sessions, "_manifest_dir", lambda: d)
    return d


def test_a_list_reads_the_manifests_once(tmp_path, monkeypatch):
    _shelve(tmp_path, monkeypatch)
    reads, looks = [], []
    real_read, real_proj = sessions._read_manifests, sessions._project_of
    monkeypatch.setattr(sessions, "_read_manifests", lambda: reads.append(1) or real_read())
    monkeypatch.setattr(sessions, "_project_of",
                        lambda cwd, folder="": looks.append(cwd) or real_proj(cwd, folder))

    def index(*, days):
        for s in ("s0", "s1", "s2", "s0"):
            sessions._manifest_of(s)
            sessions.project_of("/tmp/same")
        return [sessions._shelf_folders()]

    monkeypatch.setattr(sessions, "_sessions_index", index)
    out = sessions.sessions_index(days=1)
    assert len(reads) == 1 and looks == ["/tmp/same"]
    assert out[0]["s1"] == "/f/p-x/t1"
    # Held only for the list: gone after it.
    from agent_media_core import _pass
    assert not _pass.active()
    sessions.sessions_index(days=1)
    assert len(reads) == 2


def test_a_rename_outside_a_list_is_seen_at_once(tmp_path, monkeypatch):
    d = _shelve(tmp_path, monkeypatch, n=1)
    assert sessions._manifest_of("s0")["folder"] == "/f/p-x/t0"
    (d / "s0.json").write_text(json.dumps({"session": "s0", "folder": "/f/p-x/renamed-longer"}))
    assert sessions._manifest_of("s0")["folder"] == "/f/p-x/renamed-longer"


def test_a_map_written_inside_a_list_reads_back_the_write(tmp_path, monkeypatch):
    from agent_media_core import _pass
    from agent_media_server._jsonmap import JsonMap

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    m = JsonMap("pass-test.json")
    m.put("a", {"n": 1})
    with _pass.held():
        assert m.get("a") == {"n": 1}
        m.put("a", {"n": 2})
        assert m.get("a") == {"n": 2}
        got = m.get("a")
        got["n"] = 99                      # a caller's copy, not the map's
        assert m.get("a") == {"n": 2}
        # Another process's write is not seen inside the pass, and is after.
        (tmp_path / "agent-media" / "pass-test.json").write_text('{"a": {"n": 3}}')
        assert m.get("a") == {"n": 2}
    assert m.get("a") == {"n": 3}


def test_private_and_sealed_inside_a_pass(tmp_path, monkeypatch):
    from agent_media_core import _pass, private, sealed

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setenv("AGENT_PRIVATE_DIR", str(tmp_path / "private.d"))
    with _pass.held():
        assert not private.hidden("s1")
        assert private.mark("s1")
        sealed.mark("s2", harness="claude")
        assert private.hidden("s1") and private.hidden("s2")
        assert not private.hidden("s3")
    assert private.hidden("s1") and private.hidden("s2")
