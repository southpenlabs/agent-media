"""Releases (release.py): build beside the old, switch, restart what changed,
step back; and release mode, the drop-ins that start a unit from `current`.

Everything is in tmp dirs. The remote is a git repository made here; the
venv and the install are faked (`fake_install`: no index, no network);
`systemctl`, `systemd-run` and `agent-alert` are test_restart_services.py's
logging scripts on PATH. Nothing real is built into, switched or restarted.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from agent_media_core import cli, release, restart_services as rs

from test_restart_services import host  # noqa: F401 — the fixture


def _git(repo: Path, *args: str) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
           "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                          text=True, env=env).stdout.strip()


def _write(root: Path, files: "dict[str, str]") -> None:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)


TREE = {
    "packages/core/pyproject.toml":
        '[project]\nname = "agent-media-core"\n[project.scripts]\n'
        'media = "agent_media_core.cli:main"\nmedia-feed = "agent_media_core.feed:main"\n',
    "packages/core/src/agent_media_core/__init__.py": "",
    "packages/core/src/agent_media_core/cli.py":
        "from . import util\n\ndef sessiond():\n    from agent_media_server import sessiond\n",
    "packages/core/src/agent_media_core/util.py": "X = 1\n",
    "packages/core/src/agent_media_core/feed.py": "from .util import X\n",
    "packages/core/src/agent_media_core/lonely.py": "Y = 1\n",
    "packages/core/src/agent_media_core/notes.md": "notes\n",
    "packages/core/services/media-sessiond/run": "#!/bin/sh\nexec media sessiond\n",
    "packages/core/services/media-feed/run": "#!/bin/sh\nexec media-feed\n",
    "packages/core/tests/test_x.py": "",
    "packages/server/pyproject.toml":
        '[project]\nname = "agent-media-server"\ndependencies = ["agent-media-core"]\n',
    "packages/server/src/agent_media_server/__init__.py": "",
    "packages/server/src/agent_media_server/sessiond.py": "import agent_media_core.util\n",
    "packages/plugin/pyproject.toml": '[project]\nname = "agent-media-plugin"\n',
    "packages/plugin/src/agent_media_plugin/__init__.py": "",
    "deploy/clip.py": "print('clips')\n",
    "docs/x.md": "doc\n",
}


@pytest.fixture
def rel(host, tmp_path, monkeypatch):  # noqa: F811
    """A remote with one commit, a release root, a systemd dir and a fake
    install; `rel.commit(files)` adds a commit and returns its sha."""
    for k in list(os.environ):
        if k.startswith("MEDIA_RELEASE"):
            monkeypatch.delenv(k, raising=False)
    remote = tmp_path / "remote"
    remote.mkdir()
    _git(remote, "init", "-q", "-b", "main")
    monkeypatch.setenv("MEDIA_RELEASE_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("MEDIA_RELEASE_REMOTE", str(remote))
    monkeypatch.setenv("MEDIA_RELEASE_PACKAGES", "core server")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("FAKE_UNITS", "agent-media-sessiond agent-media-feed")
    monkeypatch.delenv("MEDIA_SESSIOND_SESSION", raising=False)
    monkeypatch.setattr(rs, "_sleep", lambda s: None)
    installs: "list[list[str]]" = []
    real = release._run

    def fake_install(argv, **kw):
        if argv[1:3] == ["-m", "venv"]:
            (Path(argv[3]) / "bin").mkdir(parents=True)
            for script in ("media", "media-feed"):
                (Path(argv[3]) / "bin" / script).write_text("#!/bin/sh\n")
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[1:4] == ["-m", "pip", "install"]:
            installs.append(argv[4:])
            return subprocess.CompletedProcess(argv, Rel.install_rc, "", "no index here")
        if len(argv) > 1 and argv[1] == "-c":
            return subprocess.CompletedProcess(argv, 0, "3.13.0\n", "")
        return real(argv, **kw)

    monkeypatch.setattr(release, "_run", fake_install)
    monkeypatch.setattr(release, "_which",
                        lambda name: None if name == "uv" else shutil.which(name))

    class Rel:
        install_rc = 0
        root = tmp_path / "data"
        udir = tmp_path / "config" / "systemd" / "user"
        checkout = tmp_path / "home" / "projects" / "agent-media"

        @staticmethod
        def commit(files: "dict[str, str]", msg: str = "c") -> str:
            _write(remote, files)
            _git(remote, "add", "-A")
            _git(remote, "commit", "-q", "-m", msg)
            return _git(remote, "rev-parse", "HEAD")

        @staticmethod
        def unit(name: str, text: str) -> None:
            _write(Rel.udir, {name: text})

        installs_ = installs

        @staticmethod
        def events() -> "list[str]":
            return [r["event"] for r in release.read_log()]

    Rel.host = host
    Rel.first = Rel.commit(TREE)
    Rel.udir.mkdir(parents=True)
    Rel.checkout.mkdir(parents=True)
    return Rel


def _lines() -> "tuple[list[str], callable]":
    out: "list[str]" = []
    return out, out.append


SESSIOND_UNIT = """[Unit]
Description=x
[Service]
EnvironmentFile=-%h/.config/agent-media.env
Environment=PATH={co}/.venv/bin:/usr/bin:/bin
ExecStart=/bin/sh {co}/packages/core/services/media-sessiond/run
"""
FEED_UNIT = """[Service]
ExecStart=%h/projects/agent-media/.venv/bin/media-feed --port 1
"""


def _units(rel, monkeypatch) -> None:
    """Two units naming the checkout, one in full and one as %h/…"""
    monkeypatch.setenv("HOME", str(rel.checkout.parent.parent))
    monkeypatch.setenv("MEDIA_RELEASE_CHECKOUT", str(rel.checkout))
    monkeypatch.setenv("XDG_STATE_HOME", str(rel.host.path / "state"))
    rel.unit("agent-media-sessiond.service", SESSIOND_UNIT.format(co=rel.checkout))
    rel.unit("agent-media-feed.service", FEED_UNIT)


# --- build, switch, step back ------------------------------------------------------


def test_update_builds_a_release_and_points_current_at_it(rel):
    out, emit = _lines()
    assert release.update(emit=emit) == 0
    cur = release.current()
    assert cur["sha"] == rel.first and cur["ref"] == "main"
    d = Path(cur["path"])
    assert d == rel.root / "releases" / rel.first[:12]
    assert (d / "packages/core/src/agent_media_core/cli.py").is_file()
    assert not (d / ".git").exists() and (d / ".venv/bin/media").is_file()
    assert os.readlink(rel.root / "current") == str(d)
    # One resolve, by path, so core is met from the tree and not an index.
    assert rel.installs_ == [["--quiet", str(d / "packages/core"), str(d / "packages/server")]]
    assert cur["packages"] == ["core", "server"]
    assert rel.events() == ["switch"]
    assert release.previous() is None


def test_a_second_update_keeps_the_first_as_previous_and_rollback_toggles(rel):
    assert release.update(emit=lambda s: None) == 0
    second = rel.commit({"docs/y.md": "more\n"})
    out, emit = _lines()
    assert release.update(emit=emit) == 0
    assert release.current()["sha"] == second and release.previous()["sha"] == rel.first
    assert release.update(emit=emit) == 0
    assert any("nothing to do" in line for line in out)
    assert release.rollback(emit=emit) == 0
    assert release.current()["sha"] == rel.first and release.previous()["sha"] == second
    assert release.rollback(emit=emit) == 0
    assert release.current()["sha"] == second
    assert rel.events() == ["switch", "switch", "rollback", "rollback"]
    assert release.deployed_at(second) == release.read_log()[-1]["t"]


def test_a_failed_install_changes_nothing_and_leaves_no_folder(rel):
    assert release.update(emit=lambda s: None) == 0
    second = rel.commit({"packages/core/src/agent_media_core/util.py": "X = 2\n"})
    rel.install_rc = 1
    out, emit = _lines()
    assert release.update(emit=emit) == 1
    assert "no index here" in "\n".join(out)
    assert release.current()["sha"] == rel.first
    assert not (rel.root / "releases" / second[:12]).exists()
    assert rel.events() == ["switch", "failed"]
    rel.install_rc = 0
    assert release.update(emit=emit) == 0 and release.current()["sha"] == second


def test_a_folder_without_its_mark_is_built_again(rel):
    half = rel.root / "releases" / rel.first[:12]
    (half / "junk").mkdir(parents=True)
    assert release.update(emit=lambda s: None) == 0
    assert not (half / "junk").exists() and release.read_mark(half)


def test_to_takes_a_sha_and_an_unknown_ref_fails_cleanly(rel):
    second = rel.commit({"docs/y.md": "more\n"})
    assert release.update(rel.first[:10], emit=lambda s: None) == 0
    assert release.current()["sha"] == rel.first and release.current()["ref"] == rel.first[:10]
    out, emit = _lines()
    assert release.update("no-such-branch", emit=emit) == 1
    assert "not a commit the remote has" in out[-1]
    assert release.update(emit=emit) == 0 and release.current()["sha"] == second


def test_no_remote_says_how_to_set_one(rel, monkeypatch):
    monkeypatch.delenv("MEDIA_RELEASE_REMOTE")
    out, emit = _lines()
    assert release.update(emit=emit) == 1
    assert "MEDIA_RELEASE_REMOTE" in out[-1]


def test_dry_run_builds_and_switches_nothing(rel):
    out, emit = _lines()
    assert release.update(dry_run=True, emit=emit) == 0
    assert "would build" in out[0] and release.current() is None
    assert not (rel.root / "releases").exists()


def test_prune_keeps_current_previous_and_the_newest(rel, monkeypatch):
    monkeypatch.setenv("MEDIA_RELEASE_KEEP", "2")
    shas = [rel.first]
    assert release.update(emit=lambda s: None) == 0
    for i in range(3):
        shas.append(rel.commit({"docs/y.md": f"{i}\n"}))
        assert release.update(emit=lambda s: None) == 0
    names = sorted(p.name for p in (rel.root / "releases").iterdir())
    assert names == sorted(s[:12] for s in shas[-2:])


def test_default_packages_are_the_ones_this_install_has(rel, monkeypatch):
    monkeypatch.delenv("MEDIA_RELEASE_PACKAGES")
    monkeypatch.setattr(release, "installed_here", lambda names: {"agent-media-server"})
    assert release.update(emit=lambda s: None) == 0
    assert release.current()["packages"] == ["server"]


# --- release mode: the drop-ins -------------------------------------------------------


def test_plan_rewrites_every_spelling_and_only_the_checkout(rel, monkeypatch):
    _units(rel, monkeypatch)
    co, cur = rel.checkout, rel.root / "current"
    rel.unit("x.service", f"""[Service]
Environment=PATH=%h/projects/agent-media/.venv/bin:/bin "NOTE=a b" OTHER={co}-anywhere/x
WorkingDirectory=$HOME/projects/agent-media
ExecStartPre=/bin/true
ExecStart=/usr/bin/python3 {co}/deploy/clip.py \\
    --dir {co}-anywhere
""")
    plan = release.plan_unit("x.service", co, udir=rel.udir)
    assert plan.lines == [
        f'Environment="PATH={cur}/.venv/bin:/bin"',
        f"WorkingDirectory={cur}",
        "ExecStart=",
        f"ExecStart=/usr/bin/python3 {cur}/deploy/clip.py --dir {co}-anywhere",
    ]
    assert plan.command.startswith(f"/usr/bin/python3 {cur}/deploy/clip.py")


def test_plan_reads_the_hosts_drop_ins_in_order(rel, monkeypatch):
    """A command list a drop-in reset is the reset one; a variable set twice
    has its last value; our own drop-in is not read back."""
    _units(rel, monkeypatch)
    co, cur = rel.checkout, rel.root / "current"
    d = "agent-media-sessiond.service.d"
    rel.unit(f"{d}/local.conf",
             f"[Service]\nExecStart=\nExecStart={co}/.venv/bin/media sessiond --x\n"
             "Environment=PATH=/opt/own/bin\n")
    rel.unit(f"{d}/{release.DROPIN}", "[Service]\nExecStart=\nExecStart=/stale\n")
    plan = release.plan_unit("agent-media-sessiond.service", co, udir=rel.udir)
    assert plan.lines == ["ExecStart=", f"ExecStart={cur}/.venv/bin/media sessiond --x"]


def test_release_mode_writes_drop_ins_reloads_and_undo_removes_them(rel, monkeypatch):
    _units(rel, monkeypatch)
    out, emit = _lines()
    assert release.release_mode(emit=emit) == 1
    assert "media update" in out[-1]                      # no release yet
    assert release.update(emit=lambda s: None) == 0
    assert release.release_mode(["sessiond"], dry_run=True, emit=emit) == 0
    assert release.mode_units() == []
    assert release.release_mode(["sessiond"], emit=emit) == 0
    cur = rel.root / "current"
    drop = rel.udir / "agent-media-sessiond.service.d" / release.DROPIN
    text = drop.read_text()
    assert f'Environment="PATH={cur}/.venv/bin:/usr/bin:/bin"' in text
    assert f"ExecStart=\nExecStart=/bin/sh {cur}/packages/core/services/media-sessiond/run" in text
    assert release.mode_units() == ["agent-media-sessiond.service"]
    assert rel.host.calls().count("systemctl --user daemon-reload") == 1
    assert rel.host.restarts() == []                      # never restarts
    assert rel.unit is not None and SESSIOND_UNIT.format(co=rel.checkout) == (
        rel.udir / "agent-media-sessiond.service").read_text()
    # Again: nothing to write, no reload. Then the rest.
    assert release.release_mode(["sessiond"], emit=emit) == 0
    assert rel.host.calls().count("systemctl --user daemon-reload") == 1
    assert release.release_mode(emit=emit) == 0
    assert release.mode_units() == ["agent-media-feed.service", "agent-media-sessiond.service"]
    assert release.release_mode(["nope"], emit=emit) == 2
    # Back.
    assert release.release_mode(["sessiond"], undo=True, emit=emit) == 0
    assert not drop.exists() and not drop.parent.exists()
    assert release.mode_units() == ["agent-media-feed.service"]
    assert "release-mode" in rel.events()


def test_a_unit_whose_file_the_release_lacks_is_skipped(rel, monkeypatch):
    _units(rel, monkeypatch)
    rel.unit("agent-media-gone.service",
             f"[Service]\nExecStart=/bin/sh {rel.checkout}/packages/core/services/gone/run\n")
    assert release.update(emit=lambda s: None) == 0
    out, emit = _lines()
    assert release.release_mode(emit=emit) == 1
    assert any(line.startswith("agent-media-gone.service: SKIPPED") for line in out)
    assert "agent-media-gone.service" not in release.mode_units()
    assert len(release.mode_units()) == 2


def test_link_mode_repoints_and_restores(rel, monkeypatch):
    _units(rel, monkeypatch)
    assert release.update(emit=lambda s: None) == 0
    bin_ = rel.host.path / "localbin"
    bin_.mkdir()
    os.symlink(rel.checkout / ".venv/bin/media", bin_ / "media")
    os.symlink(rel.checkout / ".venv/bin/not-in-release", bin_ / "other")
    os.symlink("/usr/bin/env", bin_ / "env")
    assert release.link_mode(checkout=rel.checkout, bindir=bin_, emit=lambda s: None) == 0
    assert os.readlink(bin_ / "media") == str(rel.root / "current/.venv/bin/media")
    assert os.readlink(bin_ / "other") == str(rel.checkout / ".venv/bin/not-in-release")
    assert os.readlink(bin_ / "env") == "/usr/bin/env"
    assert release.link_mode(undo=True, bindir=bin_, emit=lambda s: None) == 0
    assert os.readlink(bin_ / "media") == str(rel.checkout / ".venv/bin/media")
    assert release.read_manifest()["links"] == {}


def test_install_services_from_a_release_names_current(rel, monkeypatch):
    from agent_media_core import setup

    assert release.update(emit=lambda s: None) == 0
    d = Path(release.current()["path"])
    assert setup._release_stable(d / ".venv" / "bin") == rel.root / "current/.venv/bin"
    assert setup._release_stable(Path("/usr/bin")) == Path("/usr/bin")


# --- which units a change touches --------------------------------------------------


def _touched(rel, changed):
    d = Path(release.current()["path"])
    cur = rel.root / "current"
    units = {"sessiond.service": f"/bin/sh {cur}/packages/core/services/media-sessiond/run",
             "feed.service": f"{cur}/.venv/bin/media-feed --port 1",
             "clips.service": f"/usr/bin/python3 {cur}/deploy/clip.py 8780"}
    return release.units_to_restart(changed, units, d)[0]


def test_units_to_restart_follows_imports_data_and_scripts(rel):
    assert release.update(emit=lambda s: None) == 0
    core = "packages/core/src/agent_media_core/"
    assert _touched(rel, ["docs/x.md", "packages/core/tests/test_x.py", "README.md"]) == []
    # Imported only inside a function of the `media` cli, through a run script.
    assert _touched(rel, ["packages/server/src/agent_media_server/sessiond.py"]) \
        == ["sessiond.service"]
    assert _touched(rel, [core + "util.py"]) == ["feed.service", "sessiond.service"]
    assert _touched(rel, [core + "feed.py"]) == ["feed.service"]
    assert _touched(rel, ["deploy/clip.py"]) == ["clips.service"]
    assert _touched(rel, ["packages/core/services/media-sessiond/run"]) == ["sessiond.service"]
    # A data file in a package: whoever imports from that package.
    assert _touched(rel, [core + "notes.md"]) == ["feed.service", "sessiond.service"]
    # Nobody can be shown to load these: everyone restarts.
    everyone = ["clips.service", "feed.service", "sessiond.service"]
    assert _touched(rel, ["packages/plugin/src/agent_media_plugin/__init__.py"]) == everyone
    assert _touched(rel, [core + "lonely.py"]) == everyone
    assert _touched(rel, ["packages/core/pyproject.toml"]) == everyone
    assert _touched(rel, None) == everyone
    # A module that went away: its importers changed too, and are listed.
    assert _touched(rel, [core + "deleted.py"]) == []


# --- update restarts what changed, and steps back -----------------------------------


def _in_mode(rel, monkeypatch) -> None:
    _units(rel, monkeypatch)
    assert release.update(emit=lambda s: None) == 0
    assert release.release_mode(emit=lambda s: None) == 0


def test_update_restarts_only_the_units_whose_code_changed(rel, monkeypatch):
    _in_mode(rel, monkeypatch)
    rel.commit({"packages/core/src/agent_media_core/feed.py": "from .util import X\nZ = 1\n"})
    out, emit = _lines()
    assert release.update(emit=emit) == 0
    assert rel.host.restarts() == ["agent-media-feed.service"]
    assert any("is live: 1 unit(s) restarted" in line for line in out)
    assert rel.events()[-2:] == ["switch", "restarted"]
    assert rs.read_pending() is None


def test_the_session_host_is_drained_and_goes_last(rel, monkeypatch):
    _in_mode(rel, monkeypatch)
    rel.commit({"packages/core/src/agent_media_core/util.py": "X = 3\n"})
    out, emit = _lines()
    assert release.update(emit=emit) == 0
    assert rel.host.restarts() == ["agent-media-feed.service", "agent-media-sessiond.service"]
    assert any("draining until" in line for line in out)
    assert not any(c.startswith("systemd-run") for c in rel.host.calls())   # inline
    assert rs.read_pending() is None


def test_a_unit_that_does_not_come_up_sends_the_host_back(rel, monkeypatch):
    _in_mode(rel, monkeypatch)
    second = rel.commit({"packages/core/src/agent_media_core/feed.py": "broken = 1\n"})
    monkeypatch.setenv("FAKE_DOWN", "agent-media-feed")
    out, emit = _lines()
    assert release.update(emit=emit) == 1
    assert release.current()["sha"] == rel.first
    assert release.previous()["sha"] == second
    assert rel.host.restarts() == ["agent-media-feed.service"] * 2   # forward, then back
    assert any("stepping back" in line for line in out)
    assert rel.events()[-2:] == ["switch", "rollback"]
    assert release.read_log()[-1]["auto"] is True
    assert any("release." in a and "--level warn" in a for a in rel.host.alerts())


def test_a_host_that_stays_busy_is_left_switched_not_stepped_back(rel, monkeypatch):
    _in_mode(rel, monkeypatch)
    second = rel.commit({"packages/core/src/agent_media_core/feed.py": "Z = 2\n"})
    monkeypatch.setattr(release, "_restart", lambda units, emit, **kw: (rs.BUSY, [], []))
    assert release.update(emit=lambda s: None) == rs.BUSY
    assert release.current()["sha"] == second and rel.events()[-1] == "busy"


def test_inside_a_chat_the_session_hosts_restart_is_handed_over(rel, monkeypatch):
    """A chat cannot wait out the drain of the host it runs in: the restarts
    go to a transient unit, and the chat is told to end its turn."""
    _in_mode(rel, monkeypatch)
    second = rel.commit({"packages/core/src/agent_media_core/util.py": "X = 4\n"})
    monkeypatch.setenv("MEDIA_SESSIOND_SESSION", "11111111-aaaa")
    out, emit = _lines()
    assert release.update(emit=emit) == 0
    assert rel.host.restarts() == []
    run = next(c for c in rel.host.calls() if c.startswith("systemd-run"))
    assert f"agent_media_core.release --finish {second} --from {rel.first}" in run
    assert "agent-media-sessiond.service" in run and "end your turn" in out[-1]
    # …and that unit's half does the restarts.
    monkeypatch.delenv("MEDIA_SESSIOND_SESSION")
    assert release.main(["--finish", second, "--from", rel.first, "--units",
                         "agent-media-feed.service", "agent-media-sessiond.service"]) == 0
    assert rel.host.restarts() == ["agent-media-feed.service", "agent-media-sessiond.service"]


def test_no_restart_and_only(rel, monkeypatch):
    _in_mode(rel, monkeypatch)
    rel.commit({"packages/core/src/agent_media_core/util.py": "X = 5\n"})
    assert release.update(restart=False, emit=lambda s: None) == 0
    assert rel.host.restarts() == []
    rel.commit({"packages/core/src/agent_media_core/util.py": "X = 6\n"})
    assert release.update(only=["feed"], emit=lambda s: None) == 0
    assert rel.host.restarts() == ["agent-media-feed.service"]


def test_rollback_restarts_what_differs(rel, monkeypatch):
    _in_mode(rel, monkeypatch)
    rel.commit({"packages/core/src/agent_media_core/feed.py": "Z = 3\n"})
    assert release.update(restart=False, emit=lambda s: None) == 0
    assert release.rollback(emit=lambda s: None) == 0
    assert release.current()["sha"] == rel.first
    assert rel.host.restarts() == ["agent-media-feed.service"]


# --- what runs, and `media version` --------------------------------------------------


def test_unit_runs_reads_the_release_from_the_command_line(rel, monkeypatch):
    _in_mode(rel, monkeypatch)
    d = Path(release.current()["path"])
    cur = rel.root / "current"
    cmds = {"agent-media-feed.service": [f"{d}/.venv/bin/python", f"{cur}/.venv/bin/media-feed"],
            "agent-media-sessiond.service": [f"{rel.checkout}/.venv/bin/python", "x"]}
    monkeypatch.setattr(release, "_unit_show", lambda u: {"MainPID": "4242",
                                                          "ActiveState": "active"})
    monkeypatch.setattr(release, "_cmdline", lambda pid: cmds[unit["name"]])
    unit = {"name": "agent-media-feed.service"}
    r = release.unit_runs(unit["name"])
    assert (r["source"], r["sha"], r["path"]) == ("release", rel.first, str(d))
    unit["name"] = "agent-media-sessiond.service"
    assert release.unit_runs(unit["name"])["source"] == "checkout"
    # A bare script under `current` is dated against the deploy log.
    cmds[unit["name"]] = ["/usr/bin/python3", f"{cur}/deploy/clip.py"]
    monkeypatch.setattr(release, "_started", lambda pid: release.read_log()[0]["t"] + 1)
    r = release.unit_runs(unit["name"])
    assert (r["source"], r["sha"]) == ("release", rel.first)
    monkeypatch.setattr(release, "_unit_show", lambda u: {"MainPID": "0"})
    assert release.unit_runs(unit["name"])["source"] == ""


def test_media_version_names_the_units_that_differ(rel, monkeypatch, capsys):
    _in_mode(rel, monkeypatch)
    assert release.release_mode(["sessiond"], undo=True, emit=lambda s: None) == 0
    d = Path(release.current()["path"])
    monkeypatch.setattr(release, "_unit_show", lambda u: {"MainPID": "4242"})
    monkeypatch.setattr(release, "_cmdline",
                        lambda pid: [f"{rel.checkout}/.venv/bin/python", "x"])
    assert cli.main(["version"]) == 0
    out = capsys.readouterr().out
    assert out.startswith(f"release {rel.first[:12]} (main), in use since ")
    assert "this command runs from checkout" in out
    assert "agent-media-sessiond: runs from the checkout" in out
    assert "agent-media-feed: set to the release, still runs from checkout" in out
    monkeypatch.setattr(release, "_cmdline", lambda pid: [f"{d}/.venv/bin/python", "x"])
    assert cli.main(["version", "--json"]) == 0
    st = json.loads(capsys.readouterr().out)
    assert st["current"]["sha"] == rel.first
    assert st["units"]["agent-media-feed.service"]["sha"] == rel.first
    assert cli.main(["version", "--log"]) == 0
    assert "switch" in capsys.readouterr().out


def test_info_for_a_checkout_is_its_head():
    v = release.info()
    assert v["mode"] == "checkout" and len(v["sha"]) == 40 and v["deployed"] == 0.0


def test_cli_update_and_rollback(rel, monkeypatch, capsys):
    assert cli.main(["update"]) == 0
    assert cli.main(["update", "--rollback"]) == 1
    assert "no previous release" in capsys.readouterr().out
    rel.commit({"docs/y.md": "z\n"})
    assert cli.main(["update", "--dry-run"]) == 0
    assert release.current()["sha"] == rel.first
    assert cli.main(["update"]) == 0 and cli.main(["update", "--rollback"]) == 0
    assert release.current()["sha"] == rel.first


# --- restart_services.drain, the inline drained restart ---------------------------


def test_drain_restarts_in_process_and_reports_health(rel, monkeypatch):
    res = rs.drain(["sessiond"], after=0)
    assert res["rc"] == rs.OK and rel.host.restarts() == ["agent-media-sessiond.service"]
    assert rs.read_pending() is None
    monkeypatch.setenv("FAKE_DOWN", "agent-media-sessiond")
    assert rs.drain(["sessiond"], after=0)["rc"] == rs.FAILED
    assert rs.drain(["nope"], after=0)["rc"] == rs.UNKNOWN
