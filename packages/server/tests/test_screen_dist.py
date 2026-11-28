"""The desktop pause helper's updates, from the paired server (screen_dist.py)."""
import json
import subprocess

from agent_media_server import auth, screen_dist


def _release(tmp_path, sha="abcdef1234567890"):
    root = tmp_path / "releases" / sha[:12]
    (root / "packages" / "core").mkdir(parents=True)
    (root / "RELEASE.json").write_text(json.dumps({"sha": sha}))
    return root


def _builds(calls):
    def run(cmd, **kw):
        calls.append(cmd)
        out = cmd[cmd.index("--out-dir") + 1] if "--out-dir" in cmd else cmd[cmd.index("-w") + 1]
        from pathlib import Path
        (Path(out) / "agent_media_core-0.0.0-py3-none-any.whl").write_bytes(b"wheel " + str(len(calls)).encode())
        return subprocess.CompletedProcess(cmd, 0, "", "")
    return run


def test_built_once_per_release_and_old_ones_go(tmp_path):
    calls = []
    cache = tmp_path / "cache"
    root = _release(tmp_path)
    w = screen_dist.wheel(root, cache, run=_builds(calls))
    assert w["version"] == "abcdef123456" and w["size"] == len(b"wheel 1")
    assert screen_dist.wheel(root, cache, run=_builds(calls))["sha256"] == w["sha256"]
    assert len(calls) == 1
    root2 = _release(tmp_path, "1234567890ffff")
    w2 = screen_dist.wheel(root2, cache, run=_builds(calls))
    assert w2["version"] == "1234567890ff" and len(calls) == 2
    assert sorted(p.name for p in cache.iterdir()) == ["1234567890ff"]


def test_no_release_hands_out_nothing(tmp_path):
    assert screen_dist.wheel(tmp_path, tmp_path / "c", run=_builds([])) is None


def test_a_failed_build_is_none_and_leaves_nothing(tmp_path):
    def fails(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 1, "", "boom")
    cache = tmp_path / "cache"
    assert screen_dist.wheel(_release(tmp_path), cache, run=fails) is None
    assert not any(cache.iterdir())


def test_info_needs_a_paired_device(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "gate", lambda b: (None, {"status": 401, "error": "no"}))
    ok, out = screen_dist.info("x", root=_release(tmp_path), cache=tmp_path / "c", run=_builds([]))
    assert not ok and out["status"] == 401
    monkeypatch.setattr(auth, "gate", lambda b: ({"id": "u"}, {}))
    ok, out = screen_dist.info("x", root=_release(tmp_path, "ffff00001111aa"), cache=tmp_path / "c",
                               run=_builds([]))
    assert ok and out["version"] == "ffff00001111"
