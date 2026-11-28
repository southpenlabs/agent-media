"""The pause helper's launcher (screen_launch.py, agent-mail #1449): a
damaged install or a start that fails at once is reinstalled from the
server and tried once more, and the launcher itself imports nothing of
agent-media's."""

import ast
import base64
import hashlib
from pathlib import Path

from agent_media_core import screen_helper, screen_launch


def test_the_launcher_imports_only_the_standard_library():
    tree = ast.parse(Path(screen_launch.__file__).read_text())
    names = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    names |= {n.module.split(".")[0] for n in ast.walk(tree)
              if isinstance(n, ast.ImportFrom) and n.module and not n.level}
    assert not any(n.startswith("agent_media") for n in names)
    assert not [n for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.level]


def _run(tmp_path, results, *, bad=(), fix_says=""):
    calls = {"fix": 0, "start": 0}

    def start(folder, argv):
        calls["start"] += 1
        return results[calls["start"] - 1]

    def fix(folder):
        calls["fix"] += 1
        return fix_says

    rc = screen_launch.main([], folder=tmp_path, check=lambda: list(bad), fix=fix,
                            start=start, now=lambda: 1000.0, is_editable=lambda: False)
    return rc, calls, (tmp_path / "screen.log").read_text() if (tmp_path / "screen.log").exists() else ""


def test_a_quick_failure_is_reinstalled_and_tried_once_more(tmp_path):
    rc, calls, logged = _run(tmp_path, [(1, 2.0), (0, 500.0)])
    assert rc == 0 and calls == {"fix": 1, "start": 2}
    assert "start or import failure" in logged and "starting it once more" in logged


def test_a_long_run_that_ends_is_not_a_broken_install(tmp_path):
    rc, calls, _ = _run(tmp_path, [(1, 4000.0)])
    assert rc == 1 and calls == {"fix": 0, "start": 1}


def test_damage_found_on_start_is_reinstalled_first(tmp_path):
    rc, calls, logged = _run(tmp_path, [(0, 900.0)], bad=["agent_media_core/audio_backend.py"])
    assert rc == 0 and calls == {"fix": 1, "start": 1}
    assert "damaged: agent_media_core/audio_backend.py" in logged


def test_at_most_one_reinstall_an_hour(tmp_path):
    (tmp_path / "screen-reinstalled").write_text("900")        # 100 s ago
    rc, calls, logged = _run(tmp_path, [(1, 1.0)])
    assert rc == 1 and calls == {"fix": 0, "start": 1} and "not again" in logged


def test_record_hashes_catch_a_zero_filled_file(tmp_path, monkeypatch):
    """What the crash left on hpo: a module of the right size, all zeros."""
    from importlib import metadata

    site = tmp_path / "site"
    pkg = site / "demo_pkg"
    pkg.mkdir(parents=True)
    body = b"x = 1\n"
    (pkg / "__init__.py").write_bytes(body)
    h = base64.urlsafe_b64encode(hashlib.sha256(body).digest()).rstrip(b"=").decode()
    info = site / "demo_pkg-1.0.dist-info"
    info.mkdir()
    (info / "METADATA").write_text("Name: demo-pkg\nVersion: 1.0\n")
    (info / "RECORD").write_text(f"demo_pkg/__init__.py,sha256={h},{len(body)}\n"
                                 "demo_pkg-1.0.dist-info/RECORD,,\n")
    monkeypatch.syspath_prepend(str(site))
    metadata.distribution("demo-pkg")   # found
    assert screen_launch.damaged("demo-pkg") == []
    (pkg / "__init__.py").write_bytes(b"\0" * len(body))
    assert screen_launch.damaged("demo-pkg") == ["demo_pkg/__init__.py"]
    assert "cannot be read" in screen_launch.damaged("no-such-dist-here")[0]


def test_install_runs_the_launcher_and_an_old_unit_moves_onto_it(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setattr(screen_helper.sys, "platform", "linux")
    text = screen_helper.install(dry_run=True)
    assert "screen-launch.py\n" in text and " run\n" not in text
    unit = tmp_path / "cfg" / "systemd" / "user" / screen_helper.UNIT
    unit.parent.mkdir(parents=True)
    unit.write_text(screen_helper._linux_unit(screen_helper._self_cmd()))   # an older install
    assert "screen_helper run" in unit.read_text()
    ran = []
    done = screen_helper.migrate_to_launcher(run_cmd=lambda c, **kw: ran.append(c))
    assert "now starts through screen-launch.py" in done
    assert "screen-launch.py" in unit.read_text()
    assert screen_helper.launcher_path().read_text() == Path(screen_launch.__file__).read_text()
    assert screen_helper.migrate_to_launcher(run_cmd=lambda c, **kw: ran.append(c)) == ""
    assert ran == [["systemctl", "--user", "daemon-reload"]]
