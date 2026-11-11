"""Releases: the code a host's services run, apart from any checkout being edited.

A dev install runs every package editable from a git checkout, so a service
restart loads whatever is in that folder at that moment: committed or not,
finished or not. That stays, and is how tests and contributors run. A host
that serves people runs a *release* instead (docs/proposals/
2026-11-11-where-things-run.md, step 1; docs/reference/releases.md):

    <root>/                       MEDIA_RELEASE_ROOT, default
      repo.git/                   $XDG_DATA_HOME/agent-media: a bare mirror
      releases/<sha12>/           of the remote; one folder per commit: the
        packages/ deploy/ …       commit's files (git archive, no .git),
        .venv/                    its own venv with the packages installed
        RELEASE.json              normally (not editable), and what it is
      current -> releases/<sha>   the release in use, switched atomically
      previous -> releases/<sha>  the one before, for stepping back
      release-mode.json           what `media-setup release-mode` changed

A release folder has the checkout's shape (`.venv` beside `packages/`), so a
unit that names the checkout is pointed at the release by replacing one
prefix with `<root>/current`. That is what `release_mode()` does, in a
systemd drop-in per unit; the unit files themselves are left alone, and
removing the drop-in is the way back.

A console script under `current/.venv/bin` starts the interpreter of the
release `current` named at that moment (its shebang is the real path), so a
running service stays on the release it started from until it is restarted.
`unit_runs()` reads that back, which is how `media version` can say that one
unit still runs the release before.

`update()` is `media update`: fetch, build the new release beside the old,
switch `current`, restart the units whose code changed (`units_to_restart`)
through restart_services (its busy checks, drain and health check; never a
bare systemctl restart), and step back when one does not come up. Every
step is a line in the deploy log (`deploy_log()`).

Nothing here names a host: the remote, the branch, the packages and any
extra requirements are the host's settings (`config()`).
"""

from __future__ import annotations

import ast
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

#: The drop-in `release_mode()` writes. Named to sort after a host's own
#: drop-ins (`50-…conf`, `local.conf`, `override.conf`), so it has the last
#: word on the lines it rewrites.
DROPIN = "zz-agent-media-release.conf"
MARK = "RELEASE.json"
EXEC_KEYS = ("ExecStart", "ExecStartPre", "ExecStartPost", "ExecCondition",
             "ExecReload", "ExecStop", "ExecStopPost")
UNIT_SUFFIXES = (".service",)

Emit = Callable[[str], None]


class ReleaseError(Exception):
    """A step that could not be done; nothing after it was tried."""


# --- running things (a hook for tests) -------------------------------------------


def _run(argv: "list[str]", *, cwd: "Optional[Path]" = None, timeout: float = 600,
         env: "Optional[dict]" = None) -> subprocess.CompletedProcess:
    return subprocess.run(argv, cwd=str(cwd) if cwd else None, capture_output=True,
                          text=True, timeout=timeout, env=env)


def _which(name: str) -> "Optional[str]":
    return shutil.which(name)


def _ok(argv: "list[str]", **kw) -> str:
    """Run, or raise ReleaseError with the tail of what it said."""
    try:
        r = _run(argv, **kw)
    except (OSError, subprocess.SubprocessError) as e:
        raise ReleaseError(f"{argv[0]}: {e}") from e
    if r.returncode != 0:
        tail = "\n".join(((r.stderr or "") + (r.stdout or "")).strip().splitlines()[-12:])
        raise ReleaseError(f"{' '.join(argv[:4])}… failed ({r.returncode}):\n{tail}")
    return r.stdout or ""


# --- where things are ----------------------------------------------------------------


def root() -> Path:
    explicit = os.environ.get("MEDIA_RELEASE_ROOT")
    if explicit:
        return Path(explicit).expanduser()
    base = os.environ.get("XDG_DATA_HOME")
    return (Path(base) if base else Path.home() / ".local" / "share") / "agent-media"


def releases_dir() -> Path:
    return root() / "releases"


def current_link() -> Path:
    return root() / "current"


def previous_link() -> Path:
    return root() / "previous"


def mirror() -> Path:
    return root() / "repo.git"


def manifest_path() -> Path:
    return root() / "release-mode.json"


def deploy_log() -> Path:
    from ._paths import state_dir

    return state_dir() / "deploys.jsonl"


def log(event: str, **fields) -> dict:
    """Append one line to the deploy log; the record."""
    now = time.time()
    rec = {"t": round(now, 3),
           "at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
           "event": event, **fields}
    path = deploy_log()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as fh:
            fh.write(json.dumps(rec) + "\n")
    except OSError as e:
        print(f"release: deploy log: {e}", file=sys.stderr)
    return rec


def read_log(limit: int = 0) -> "list[dict]":
    try:
        lines = deploy_log().read_text().splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out[-limit:] if limit else out


# --- settings ------------------------------------------------------------------------


@dataclass
class Config:
    remote: str = ""          # MEDIA_RELEASE_REMOTE: what the mirror fetches
    branch: str = "main"      # MEDIA_RELEASE_BRANCH
    packages: "list[str]" = field(default_factory=list)  # MEDIA_RELEASE_PACKAGES
    requirements: str = ""    # MEDIA_RELEASE_REQUIREMENTS: a pip requirements file
    constraints: str = ""     # MEDIA_RELEASE_CONSTRAINTS: a pip constraints file
    keep: int = 3             # MEDIA_RELEASE_KEEP: releases kept on disk
    python: str = ""          # MEDIA_RELEASE_PYTHON: the interpreter venvs are made from


def config() -> Config:
    """The host's release settings, from the environment (the env file).

    `MEDIA_RELEASE_PACKAGES` names package folders (`core server visual`, an
    extra as `server[covers]`); unset means the packages of this repo that
    the install running the update has. `MEDIA_RELEASE_REQUIREMENTS` is a
    pip requirements file installed into every release beside them: what
    this host needs that no package declares."""
    env = os.environ
    try:
        keep = max(2, int(env.get("MEDIA_RELEASE_KEEP") or 3))
    except ValueError:
        keep = 3
    return Config(
        remote=(env.get("MEDIA_RELEASE_REMOTE") or "").strip(),
        branch=(env.get("MEDIA_RELEASE_BRANCH") or "main").strip(),
        packages=(env.get("MEDIA_RELEASE_PACKAGES") or "").replace(",", " ").split(),
        requirements=(env.get("MEDIA_RELEASE_REQUIREMENTS") or "").strip(),
        constraints=(env.get("MEDIA_RELEASE_CONSTRAINTS") or "").strip(),
        keep=keep,
        python=(env.get("MEDIA_RELEASE_PYTHON") or "").strip())


# --- what is here --------------------------------------------------------------------


def read_mark(d: Path) -> "Optional[dict]":
    try:
        rec = json.loads((d / MARK).read_text())
    except (OSError, ValueError):
        return None
    return {**rec, "path": str(d)} if isinstance(rec, dict) and rec.get("sha") else None


def _target(link: Path) -> "Optional[Path]":
    try:
        return Path(os.path.realpath(link)) if link.is_symlink() else None
    except OSError:
        return None


def current() -> "Optional[dict]":
    """The release `current` names (its RELEASE.json and `path`), or None."""
    t = _target(current_link())
    return read_mark(t) if t else None


def previous() -> "Optional[dict]":
    t = _target(previous_link())
    return read_mark(t) if t else None


def releases() -> "list[dict]":
    """Every finished release on disk, newest built first."""
    try:
        dirs = [d for d in releases_dir().iterdir() if d.is_dir()]
    except OSError:
        return []
    out = [m for m in (read_mark(d) for d in dirs) if m]
    return sorted(out, key=lambda m: float(m.get("built") or 0), reverse=True)


def running() -> "Optional[dict]":
    """The release this process's code was loaded from, or None (a checkout,
    or some other install)."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / MARK).is_file() and (parent / ".venv").is_dir():
            return read_mark(parent)
    return None


def _git_head(checkout: Path) -> str:
    """HEAD of a checkout or worktree, read from its files (no subprocess)."""
    git = checkout / ".git"
    try:
        if git.is_file():  # a worktree: "gitdir: <path>"
            git = Path(git.read_text().split(":", 1)[1].strip())
        head = (git / "HEAD").read_text().strip()
        if not head.startswith("ref: "):
            return head
        ref = head[5:].strip()
        common = git
        if (git / "commondir").is_file():
            common = (git / (git / "commondir").read_text().strip()).resolve()
        for base in (git, common):
            if (base / ref).is_file():
                return (base / ref).read_text().strip()
        for line in (common / "packed-refs").read_text().splitlines():
            if line.endswith(" " + ref):
                return line.split(" ", 1)[0]
    except (OSError, IndexError):
        pass
    return ""


def deployed_at(sha: str) -> float:
    """When `current` last moved to `sha`, from the deploy log (0: unknown)."""
    for rec in reversed(read_log()):
        if rec.get("event") in ("switch", "rollback") and rec.get("sha") == sha:
            return float(rec.get("t") or 0)
    return 0.0


def info() -> dict:
    """What code this process runs: `{"mode": "release" | "checkout" |
    "installed", "sha", "short", "path", "built", "deployed"}`. A server
    answers this (`GET /features` `version`), so the app and a chat can ask
    what is live."""
    rel = running()
    if rel:
        sha = str(rel["sha"])
        return {"mode": "release", "sha": sha, "short": sha[:8], "path": rel["path"],
                "ref": rel.get("ref") or "", "built": float(rel.get("built") or 0),
                "deployed": deployed_at(sha)}
    from . import cli

    co = cli._checkout_root()
    if co:
        sha = _git_head(co)
        return {"mode": "checkout", "sha": sha, "short": sha[:8], "path": str(co),
                "ref": "", "built": 0.0, "deployed": 0.0}
    return {"mode": "installed", "sha": "", "short": "", "path": "", "ref": "",
            "built": 0.0, "deployed": 0.0}


# --- fetching ------------------------------------------------------------------------


def _git(*args: str, timeout: float = 300) -> str:
    return _ok(["git", "-C", str(mirror()), *args], timeout=timeout)


def fetch(remote: str = "") -> str:
    """Bring the mirror up to date with its remote; the remote's URL. The
    first call needs `remote` (kept as the mirror's `origin` from then on)."""
    m = mirror()
    if not (m / "HEAD").is_file():
        if not remote:
            raise ReleaseError(
                "no remote to fetch releases from: set MEDIA_RELEASE_REMOTE in the env "
                "file (a git URL, or the path of a repository), or pass --remote once")
        m.parent.mkdir(parents=True, exist_ok=True)
        _ok(["git", "init", "--bare", "--quiet", str(m)])
        _git("remote", "add", "origin", remote)
    else:
        have = _git("remote", "get-url", "origin").strip()
        if remote and remote != have:
            _git("remote", "set-url", "origin", remote)
        remote = remote or have
    _git("fetch", "--prune", "--quiet", "origin",
         "+refs/heads/*:refs/heads/*", "+refs/tags/*:refs/tags/*")
    return remote


def resolve_ref(ref: str) -> str:
    """The full sha of a branch, tag or (short) sha in the mirror."""
    try:
        return _git("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}").strip()
    except ReleaseError as e:
        raise ReleaseError(f"{ref!r} is not a commit the remote has") from e


def changed_files(old: str, new: str) -> "Optional[list[str]]":
    """Paths that differ between two commits; None when it cannot be told
    (a commit the mirror no longer has), which callers treat as everything."""
    if old == new:
        return []
    try:
        return [p for p in _git("diff", "--name-only", old, new).splitlines() if p]
    except ReleaseError:
        return None


# --- building ------------------------------------------------------------------------


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def package_names(tree: Path) -> "dict[str, str]":
    """Package folder → distribution name, for `tree/packages/*`."""
    import tomllib

    out = {}
    for py in sorted((tree / "packages").glob("*/pyproject.toml")):
        try:
            name = tomllib.loads(py.read_text()).get("project", {}).get("name")
        except (OSError, ValueError):
            continue
        if name:
            out[py.parent.name] = str(name)
    return out


def installed_here(names: "Iterable[str]") -> "set[str]":
    """Which of these distributions the running install has."""
    from importlib import metadata

    out = set()
    for n in names:
        try:
            metadata.version(n)
        except metadata.PackageNotFoundError:
            continue
        out.add(n)
    return out


def pick_packages(tree: Path, wanted: "list[str]") -> "list[str]":
    """Install arguments for `tree`'s packages: `wanted` (folder names, an
    extra as `server[covers]`), else the ones this install already has, else
    `core` alone. Paths, so one resolve sees them all and a package's
    dependency on another is met from the tree, not an index."""
    have = package_names(tree)
    if wanted:
        out = []
        for w in wanted:
            folder, br, extra = w.partition("[")
            if folder not in have:
                raise ReleaseError(f"MEDIA_RELEASE_PACKAGES names {folder!r}, which this "
                                   f"commit has no packages/{folder} for")
            out.append(f"{tree / 'packages' / folder}{br}{extra}")
        return out
    here = installed_here(have.values())
    folders = [f for f, n in have.items() if n in here] or (["core"] if "core" in have else [])
    if not folders:
        raise ReleaseError("this commit has no packages to install")
    return [str(tree / "packages" / f) for f in folders]


def _base_python(cfg: Config) -> str:
    return cfg.python or getattr(sys, "_base_executable", "") or sys.executable


def _modules(tree: Path, args: "list[str]") -> "list[str]":
    """Top-level modules of the installed packages, for the import check."""
    out = []
    for a in args:
        src = Path(a.partition("[")[0]) / "src"
        if not str(src).startswith(str(tree)) or not src.is_dir():
            continue
        out += [d.name for d in sorted(src.iterdir()) if (d / "__init__.py").is_file()]
    return out


def build(sha: str, cfg: "Optional[Config]" = None, *, ref: str = "",
          emit: Emit = print) -> Path:
    """The release folder for `sha`, built if it is not there: the commit's
    files, a venv, the packages installed normally, and an import check.
    `RELEASE.json` is written last, so a folder without one is a build that
    did not finish and is started again."""
    cfg = cfg or config()
    d = releases_dir() / sha[:12]
    if read_mark(d):
        emit(f"release {sha[:12]}: already built")
        return d
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    try:
        tar = d / ".src.tar"
        _git("archive", "--format=tar", "-o", str(tar), sha)
        _ok(["tar", "-xf", str(tar), "-C", str(d)])
        tar.unlink()
        args = pick_packages(d, cfg.packages)
        venv = d / ".venv"
        py = venv / ("Scripts" if os.name == "nt" else "bin") / "python"
        uv = _which("uv")
        emit(f"release {sha[:12]}: installing {len(args)} package(s) "
             f"({'uv' if uv else 'pip'})")
        extra = []
        if cfg.constraints:
            extra += ["--constraint", cfg.constraints]
        if cfg.requirements:
            extra += ["-r", cfg.requirements]
        if uv:
            _ok([uv, "venv", "--quiet", "--python", _base_python(cfg), str(venv)])
            _ok([uv, "pip", "install", "--quiet", "--python", str(py), *extra, *args],
                timeout=1800)
        else:
            _ok([_base_python(cfg), "-m", "venv", str(venv)])
            _ok([str(py), "-m", "pip", "install", "--quiet", *extra, *args], timeout=1800)
        mods = _modules(d, args)
        if mods:
            _ok([str(py), "-c", "import " + ", ".join(mods)], timeout=300)
        version = _ok([str(py), "-c", "import platform; print(platform.python_version())"])
        mark = {"sha": sha, "ref": ref, "built": round(time.time(), 3),
                "packages": [Path(a.partition('[')[0]).name + ("[" + a.partition("[")[2]
                                                               if "[" in a else "")
                             for a in args],
                "python": version.strip()}
        (d / MARK).write_text(json.dumps(mark, indent=1) + "\n")
    except BaseException:
        shutil.rmtree(d, ignore_errors=True)
        raise
    emit(f"release {sha[:12]}: built in {d}")
    return d


# --- switching -----------------------------------------------------------------------


def _point(link: Path, target: Path) -> None:
    """Make `link` name `target` in one rename: nothing ever sees it missing."""
    link.parent.mkdir(parents=True, exist_ok=True)
    tmp = link.with_name(f".{link.name}.{os.getpid()}")
    try:
        tmp.unlink()
    except OSError:
        pass
    os.symlink(str(target), str(tmp))
    os.replace(tmp, link)


def switch(d: Path) -> "Optional[Path]":
    """Point `current` at release folder `d`; what it named before (now
    `previous`), or None."""
    if not read_mark(d):
        raise ReleaseError(f"{d} is not a finished release")
    old = _target(current_link())
    if old == Path(os.path.realpath(d)):
        return old
    _point(current_link(), d)
    if old and read_mark(old):
        _point(previous_link(), old)
    return old


def prune(keep: int = 3) -> "list[str]":
    """Delete all but the `keep` newest releases; never `current`, `previous`
    or one a unit still runs. The names removed."""
    hold = {str(t) for t in (_target(current_link()), _target(previous_link())) if t}
    for u in mode_units():
        r = unit_runs(u)
        if r.get("path"):
            hold.add(r["path"])
    gone = []
    for m in releases()[keep:]:
        if os.path.realpath(m["path"]) in hold:
            continue
        shutil.rmtree(m["path"], ignore_errors=True)
        gone.append(Path(m["path"]).name)
    return gone


# --- release mode: units that start from `current` -----------------------------------


def systemd_dir() -> Path:
    from . import setup

    return setup.systemd_user_dir()


def default_checkout() -> "Optional[Path]":
    """The dev checkout a host's units name: `MEDIA_RELEASE_CHECKOUT`, else
    the one this code runs from, else `~/projects/agent-media` when it is one."""
    explicit = os.environ.get("MEDIA_RELEASE_CHECKOUT")
    if explicit:
        return Path(explicit).expanduser()
    from . import cli

    co = cli._checkout_root()
    if co:
        return co
    guess = Path.home() / "projects" / "agent-media"
    return guess if (guess / "packages" / "core").is_dir() else None


def _spellings(path: Path) -> "list[str]":
    """How a unit may write `path`: in full, or from the home folder."""
    out = [str(path)]
    try:
        rel = path.relative_to(Path.home())
    except ValueError:
        return out
    return out + [f"%h/{rel}", f"${{HOME}}/{rel}", f"$HOME/{rel}"]


def _rewriter(checkout: Path, to: Path) -> "Callable[[str], str]":
    # Not a prefix of a longer name: `agent-media` must not match
    # `agent-media-anywhere` (a sibling worktree).
    pat = re.compile("(?:" + "|".join(re.escape(s) for s in _spellings(checkout))
                     + r")(?![A-Za-z0-9_.-])")
    return lambda text: pat.sub(lambda _m: str(to), text)


def _unit_lines(path: Path) -> "list[tuple[str, str, str]]":
    """(section, key, value) for each assignment in a unit file, with
    continuation lines joined."""
    try:
        raw = path.read_text()
    except OSError:
        return []
    out, section, pending = [], "", ""
    for line in raw.splitlines():
        if pending:
            line = pending + " " + line.strip()
            pending = ""
        s = line.strip()
        if not s or s[0] in "#;":
            continue
        if s.endswith("\\"):
            pending = s[:-1].rstrip()
            continue
        if s.startswith("[") and s.endswith("]"):
            section = s[1:-1]
            continue
        key, eq, value = s.partition("=")
        if eq:
            out.append((section, key.strip(), value.strip()))
    return out


def _env_pairs(value: str) -> "list[tuple[str, str]]":
    """`Environment=` assignments: `A=b "C=d e"`."""
    try:
        words = shlex.split(value)
    except ValueError:
        words = value.split()
    return [(w.partition("=")[0], w.partition("=")[2]) for w in words if "=" in w]


@dataclass
class UnitPlan:
    unit: str
    lines: "list[str]"        # the drop-in's [Service] lines; [] = nothing names the checkout
    missing: "list[str]"      # paths the rewritten lines name that the release lacks
    command: str = ""         # the rewritten ExecStart, for entry-point lookups


def plan_unit(unit: str, checkout: Path, *, udir: "Optional[Path]" = None,
              to: "Optional[Path]" = None) -> UnitPlan:
    """What the drop-in for `unit` would say: every [Service] command,
    `Environment=` variable and `WorkingDirectory=` whose value in force
    names the checkout, with `to` (default `current`) in its place.

    "In force" means after the unit file and the host's own drop-ins, read
    in systemd's order: a command list a drop-in reset is the reset one, and
    a variable set twice has its last value."""
    udir = udir or systemd_dir()
    to = to or current_link()
    files = [udir / unit] + sorted(p for p in (udir / f"{unit}.d").glob("*.conf")
                                   if p.name != DROPIN)
    execs: "dict[str, list[str]]" = {}
    env: "dict[str, str]" = {}
    workdir = ""
    for f in files:
        for section, key, value in _unit_lines(f):
            if section != "Service":
                continue
            if key in EXEC_KEYS:
                if value:
                    execs.setdefault(key, []).append(value)
                else:
                    execs[key] = []
            elif key == "Environment":
                env.update(_env_pairs(value))
            elif key == "WorkingDirectory":
                workdir = value
    sub = _rewriter(checkout, to)
    lines: "list[str]" = []
    for name, value in env.items():
        new = sub(value)
        if new != value:
            lines.append('Environment="{}={}"'.format(
                name, new.replace("\\", "\\\\").replace('"', '\\"')))
    if workdir and sub(workdir) != workdir:
        lines.append(f"WorkingDirectory={sub(workdir)}")
    command = ""
    for key in EXEC_KEYS:
        values = execs.get(key) or []
        if not any(sub(v) != v for v in values):
            if key == "ExecStart" and values:
                command = values[-1]
            continue
        lines.append(f"{key}=")
        lines += [f"{key}={sub(v)}" for v in values]
        if key == "ExecStart":
            command = sub(values[-1])
    missing = []
    real = Path(os.path.realpath(to))
    for line in lines:
        for word in re.split(r"[\s=:\"']+", line):
            if word.startswith(str(to) + "/"):
                p = real / word[len(str(to)) + 1:]
                if not p.exists() and str(p) not in missing:
                    missing.append(word)
    return UnitPlan(unit, lines, missing, command)


def units_naming(checkout: Path, udir: "Optional[Path]" = None) -> "list[str]":
    """Service units here with a [Service] line that names the checkout."""
    udir = udir or systemd_dir()
    try:
        names = sorted(p.name for p in udir.iterdir()
                       if p.name.endswith(UNIT_SUFFIXES) and (p.is_file() or p.is_symlink()))
    except OSError:
        return []
    return [u for u in names if plan_unit(u, checkout, udir=udir).lines]


def read_manifest() -> dict:
    try:
        rec = json.loads(manifest_path().read_text())
    except (OSError, ValueError):
        rec = {}
    rec = rec if isinstance(rec, dict) else {}
    rec.setdefault("units", {})
    rec.setdefault("links", {})
    return rec


def _write_manifest(rec: dict) -> None:
    path = manifest_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}")
    tmp.write_text(json.dumps(rec, indent=1) + "\n")
    os.replace(tmp, path)


def mode_units() -> "list[str]":
    """The units in release mode (they have our drop-in), by unit file name."""
    return sorted(read_manifest()["units"])


def _dropin_text(plan: UnitPlan, checkout: Path) -> str:
    return ("# Written by `media-setup release-mode`: this unit starts from the release\n"
            f"# in use ({current_link()}), not from the checkout\n"
            f"# {checkout}. Remove with `media-setup release-mode --undo --only "
            f"{plan.unit.removesuffix('.service')}`.\n"
            "[Service]\n" + "\n".join(plan.lines) + "\n")


def _daemon_reload() -> None:
    _ok(["systemctl", "--user", "daemon-reload"], timeout=60)


def _match(names: "list[str]", only: "Optional[list[str]]") -> "tuple[list[str], list[str]]":
    """(`names` that `only` selects, the `only` entries that match none).
    Short names work as for restart-services (`sessiond`, `canvas`)."""
    if not only:
        return list(names), []
    from . import restart_services

    picked, unknown = [], []
    for raw in only:
        n = raw.strip().removesuffix(".service")
        hits = [u for u in names
                if n in (u.removesuffix(".service"),
                         restart_services.short_name(u.removesuffix(".service")))]
        if not hits:
            unknown.append(raw)
        picked += [h for h in hits if h not in picked]
    return picked, unknown


def release_mode(only: "Optional[list[str]]" = None, *, checkout: "Optional[Path]" = None,
                 undo: bool = False, dry_run: bool = False, emit: Emit = print) -> int:
    """Point units at `current` (or, `undo`, back at the checkout) by writing
    (removing) a drop-in each, then `daemon-reload`. Restarts nothing: a
    running service keeps the code it started with until it is restarted,
    which is the caller's to do, safely (`media restart-services`).

    A unit whose rewritten lines name a file the release does not have is
    left as it is and said so. Exit 0, 1 when a unit was skipped that way, 2
    for an unknown `only` name."""
    udir = systemd_dir()
    man = read_manifest()
    if undo:
        names, unknown = _match(sorted(man["units"]), only)
        if unknown:
            emit(f"not in release mode: {', '.join(unknown)}")
            return 2
        for u in names:
            path = udir / f"{u}.d" / DROPIN
            emit(f"{u}: {'would remove' if dry_run else 'removed'} {path}")
            if dry_run:
                continue
            path.unlink(missing_ok=True)
            try:
                path.parent.rmdir()   # only when ours was the only drop-in
            except OSError:
                pass
            man["units"].pop(u, None)
        if names and not dry_run:
            _write_manifest(man)
            _daemon_reload()
            log("release-mode", undo=True, units=names)
            emit("back on the checkout at their next start. Restart the running ones: "
                 "media restart-services --only "
                 + " ".join(n.removesuffix(".service") for n in names) + " --after 10")
        return 0
    if not current():
        emit(f"no release yet ({current_link()} names none): run `media update` first")
        return 1
    checkout = checkout or default_checkout()
    if not checkout:
        emit("which checkout do the units name? Pass --checkout PATH "
             "(or set MEDIA_RELEASE_CHECKOUT)")
        return 1
    names, unknown = _match(sorted(set(units_naming(checkout, udir)) | set(man["units"])),
                            only)
    if unknown:
        emit(f"no unit naming {checkout} matches: {', '.join(unknown)}")
        return 2
    rc, done = 0, []
    for u in names:
        plan = plan_unit(u, checkout, udir=udir)
        path = udir / f"{u}.d" / DROPIN
        if not plan.lines:
            emit(f"{u}: names the checkout nowhere now; left alone")
            continue
        if plan.missing:
            emit(f"{u}: SKIPPED, the release has no {', '.join(plan.missing)}")
            rc = 1
            continue
        text = _dropin_text(plan, checkout)
        same = path.is_file() and path.read_text() == text
        emit(f"{u}: " + ("already in release mode" if same else
                         f"{'would write' if dry_run else 'wrote'} {path}"))
        if dry_run:
            for line in plan.lines:
                emit(f"    {line}")
            continue
        if not same:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
            done.append(u)
        man["units"][u] = {"dropin": str(path), "checkout": str(checkout)}
    if not dry_run:
        _write_manifest(man)
        if done:
            _daemon_reload()
            log("release-mode", units=done)
            emit("they start from the release at their next start. Restart the running "
                 "ones: media restart-services --only "
                 + " ".join(n.removesuffix(".service") for n in done) + " --after 10")
    return rc


def refresh_mode(emit: Emit = print) -> "list[str]":
    """After a switch: rewrite the drop-ins of the units in release mode (a
    unit file in the new release may start something else) and reload when
    one changed. The units whose drop-in changed."""
    man = read_manifest()
    udir = systemd_dir()
    changed = []
    for u, rec in sorted(man["units"].items()):
        plan = plan_unit(u, Path(rec.get("checkout") or ""), udir=udir)
        path = udir / f"{u}.d" / DROPIN
        if not plan.lines or plan.missing:
            if plan.missing:
                emit(f"{u}: the release has no {', '.join(plan.missing)}; drop-in kept as is")
            continue
        text = _dropin_text(plan, Path(rec.get("checkout") or ""))
        try:
            if path.read_text() == text:
                continue
        except OSError:
            pass
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        changed.append(u)
    if changed:
        _daemon_reload()
    return changed


# --- release mode: commands on PATH ----------------------------------------------------


def link_mode(*, checkout: "Optional[Path]" = None, bindir: "Optional[Path]" = None,
              undo: bool = False, dry_run: bool = False, emit: Emit = print) -> int:
    """Repoint the symlinks in `~/.local/bin` that lead into the checkout
    (`media`, the hooks, the tmux helpers) at the same file under `current`,
    so what a shell or a hook runs is the release. `undo` puts back what the
    manifest remembers. A link whose file the release lacks is left."""
    from . import setup

    bindir = bindir or setup.local_bin()
    man = read_manifest()
    if undo:
        for path, old in sorted(man["links"].items()):
            emit(f"{path}: {'would point' if dry_run else 'points'} back at {old}")
            if not dry_run:
                _point(Path(path), Path(old))
        if not dry_run and man["links"]:
            log("release-links", undo=True, links=sorted(man["links"]))
            man["links"] = {}
            _write_manifest(man)
        return 0
    if not current():
        emit(f"no release yet ({current_link()} names none): run `media update` first")
        return 1
    checkout = checkout or default_checkout()
    if not checkout:
        emit("which checkout do the links lead into? Pass --checkout PATH")
        return 1
    done = []
    try:
        entries = sorted(bindir.iterdir())
    except OSError:
        entries = []
    for p in entries:
        if not p.is_symlink():
            continue
        old = os.readlink(p)
        try:
            rel = Path(old).relative_to(checkout)
        except ValueError:
            continue
        new = current_link() / rel
        if not new.exists():
            emit(f"{p.name}: left on the checkout (the release has no {rel})")
            continue
        emit(f"{p.name}: {'would point' if dry_run else 'points'} at {new}")
        if not dry_run:
            _point(p, new)
            man["links"][str(p)] = old
            done.append(str(p))
    if done:
        _write_manifest(man)
        log("release-links", links=done)
    return 0


# --- which units a change touches ------------------------------------------------------

#: Paths whose change no running service can notice.
NO_CODE = ("docs/", "handover/", "spike/", "spikes/", "examples/", "android/",
           ".github/", ".githooks/", ".claude/", ".pi/")


def module_map(tree: Path) -> "dict[str, Path]":
    """Dotted module name → file, for every package source in `tree`."""
    out: "dict[str, Path]" = {}
    for src in sorted((tree / "packages").glob("*/src")):
        for py in src.rglob("*.py"):
            parts = list(py.relative_to(src).with_suffix("").parts)
            if parts[-1] == "__init__":
                parts.pop()
            if parts:
                out[".".join(parts)] = py
    return out


def _imports(path: Path, name: str, mods: "dict[str, Path]") -> "set[str]":
    """The local modules `path` (module `name`) imports, anywhere in it:
    inside functions too, and `import_module("…")` with a literal name."""
    try:
        tree = ast.parse(path.read_text(errors="replace"))
    except (OSError, SyntaxError, ValueError):
        return set()
    pkg = name.split(".") if path.name == "__init__.py" else name.split(".")[:-1]
    found: "set[str]" = set()

    def add(dotted: str) -> None:
        parts = dotted.split(".")
        for i in range(1, len(parts) + 1):
            m = ".".join(parts[:i])
            if m in mods:
                found.add(m)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                add(a.name)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                up = pkg[:len(pkg) - (node.level - 1)] if node.level > 1 else pkg
                base = ".".join([*up, base] if base else up)
            if base:
                add(base)
                for a in node.names:
                    add(f"{base}.{a.name}")
        elif (isinstance(node, ast.Call) and node.args
              and isinstance(node.args[0], ast.Constant)
              and isinstance(node.args[0].value, str)
              and getattr(node.func, "attr", getattr(node.func, "id", "")) == "import_module"):
            add(node.args[0].value)
    return found


def closure(entries: "Iterable[str]", mods: "dict[str, Path]",
            cache: "Optional[dict[str, set[str]]]" = None) -> "set[str]":
    """Every local module reachable from `entries` by import."""
    cache = {} if cache is None else cache
    seen: "set[str]" = set()
    todo = [e for e in entries if e in mods]
    while todo:
        m = todo.pop()
        if m in seen:
            continue
        seen.add(m)
        if m not in cache:
            cache[m] = _imports(mods[m], m, mods)
        todo += [d for d in cache[m] if d not in seen]
    return seen


def script_map(tree: Path) -> "dict[str, str]":
    """Console script name → the module it runs, across `tree`'s packages."""
    import tomllib

    out = {}
    for py in sorted((tree / "packages").glob("*/pyproject.toml")):
        try:
            scripts = tomllib.loads(py.read_text()).get("project", {}).get("scripts", {})
        except (OSError, ValueError):
            continue
        for name, target in scripts.items():
            out[name] = str(target).partition(":")[0]
    return out


def unit_entries(command: str, tree: Path, scripts: "dict[str, str]",
                 mods: "dict[str, Path]") -> "tuple[set[str], set[str]]":
    """(modules, tree-relative files) a unit's command starts: a console
    script, `python -m module`, a `.py` or script file in the tree, and,
    through a `run` script, the commands that names."""
    entries: "set[str]" = set()
    files: "set[str]" = set()
    by_file = {str(p): m for m, p in mods.items()}
    texts = [command]
    roots = {str(tree), str(current_link()), os.path.realpath(tree)}
    words = command.split()
    for w in words:
        for r in roots:
            if w.startswith(r + "/"):
                rel = w[len(r) + 1:]
                p = tree / rel
                if p.is_file():
                    files.add(rel)
                    if str(p) in by_file:
                        entries.add(by_file[str(p)])
                    elif p.suffix != ".py":
                        try:
                            texts.append(p.read_text(errors="replace"))
                        except OSError:
                            pass
    for text in texts:
        toks = re.split(r"[\s;|&()\"']+", text)
        for i, t in enumerate(toks):
            base = t.rsplit("/", 1)[-1]
            if base in scripts:
                entries.add(scripts[base])
            if t == "-m" and i + 1 < len(toks) and toks[i + 1] in mods:
                entries.add(toks[i + 1])
    return entries, files


def units_to_restart(changed: "Optional[list[str]]", units: "dict[str, str]",
                     tree: Path) -> "tuple[list[str], dict[str, str]]":
    """Which of `units` (unit → its command, as `plan_unit` rewrote it) a
    change to the files `changed` touches, and why, per unit.

    A unit is touched when a changed file is one it loads: a module in the
    import closure of what it starts, a data file in a package it imports
    from, its `run` script or the script it runs. A change nobody can be
    shown to load (a plug-in found through entry points, a package's
    metadata, a file no rule knows) touches every unit: restarting one too
    many is the safe mistake. Docs and tests touch none. `changed=None`
    (unknown) is everything."""
    if changed is None:
        return sorted(units), {u: "the change cannot be told" for u in units}
    mods = module_map(tree)
    scripts = script_map(tree)
    by_file = {str(p.relative_to(tree)): m for m, p in mods.items()}
    cache: "dict[str, set[str]]" = {}
    loaded: "dict[str, set[str]]" = {}
    files: "dict[str, set[str]]" = {}
    unknown: "set[str]" = set()
    for u, command in units.items():
        entries, fs = unit_entries(command, tree, scripts, mods)
        loaded[u] = closure(entries, mods, cache) if entries else set()
        files[u] = fs
        if not entries and not fs:
            unknown.add(u)   # what it starts cannot be read: any change may be its
    everything = set().union(*loaded.values()) if loaded else set()
    why: "dict[str, str]" = {}

    def touch(us: "Iterable[str]", reason: str) -> None:
        for u in us:
            why.setdefault(u, reason)

    for f in changed:
        parts = f.split("/")
        if f.startswith(NO_CODE) or "/tests/" in f or (len(parts) == 1):
            continue
        direct = [u for u in units if f in files[u]]
        if direct:
            touch(direct, f)
            continue
        if parts[0] != "packages" or len(parts) < 3:
            continue  # deploy/ and the like: only a unit that runs the file itself
        if f in by_file:
            m = by_file[f]
            hit = [u for u in units if m in loaded[u] or u in unknown]
            touch(hit if m in everything else units, f)
            continue
        if f.endswith(".py") and parts[2] == "src":
            continue  # a deleted module: whoever imported it changed too
        if parts[2] == "src" and len(parts) > 4:
            top = parts[3]   # a data file inside a package: its importers
            hit = [u for u in units
                   if u in unknown or any(m == top or m.startswith(top + ".")
                                           for m in loaded[u])]
            touch(hit or units, f)
            continue
        if parts[2] == "services" and len(parts) > 4 and not parts[3].startswith("_"):
            hit = [u for u in units if f"/services/{parts[3]}/" in units[u]]
            touch(hit, f)
            continue
        if parts[2] in ("systemd", "tmux", "scripts", "bin", "deploy", "opencode", "pi"):
            continue  # run by path: `direct` above caught the units that do
        if f.endswith(".md"):
            continue
        touch(units, f)   # pyproject.toml, a build hook, services/_common, the unknown
    return sorted(why), why


# --- what a unit runs ------------------------------------------------------------------


def _unit_show(unit: str) -> "dict[str, str]":
    try:
        r = _run(["systemctl", "--user", "show", unit, "-p", "MainPID", "-p", "ActiveState",
                  "-p", "ExecMainStartTimestampMonotonic", "-p", "ActiveEnterTimestamp"],
                 timeout=10)
    except (OSError, subprocess.SubprocessError):
        return {}
    return dict(line.split("=", 1) for line in (r.stdout or "").splitlines() if "=" in line)


def _cmdline(pid: int) -> "list[str]":
    try:
        return [a.decode(errors="replace")
                for a in Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0") if a]
    except OSError:
        return []


def _started(pid: int) -> float:
    """When process `pid` started (epoch seconds), 0 when it cannot be told."""
    try:
        return Path(f"/proc/{pid}").stat().st_mtime
    except OSError:
        return 0.0


def release_at(t: float) -> str:
    """The release `current` named at time `t`, from the deploy log ('' when
    the log does not reach back that far)."""
    sha = ""
    for rec in read_log():
        if rec.get("event") in ("switch", "rollback") and float(rec.get("t") or 0) <= t:
            sha = str(rec.get("sha") or "")
    return sha


def unit_runs(unit: str) -> dict:
    """What a unit's main process runs: `{"state", "pid", "source": "release"
    | "checkout" | "other" | "", "sha", "path"}`. The release is read from
    the process's command line (its interpreter is the release's own); a
    command that names `current` without one (a bare script) is dated
    against the deploy log instead. `source` is '' when nothing runs."""
    show = _unit_show(unit)
    out = {"state": show.get("ActiveState", ""), "pid": 0, "source": "", "sha": "",
           "path": ""}
    try:
        pid = int(show.get("MainPID") or 0)
    except ValueError:
        pid = 0
    if pid <= 1:
        return out
    out["pid"] = pid
    args = _cmdline(pid)
    rdir = os.path.realpath(releases_dir())
    for a in args:
        ra = a if a.startswith(rdir + "/") else ""
        if ra:
            d = Path(rdir) / ra[len(rdir) + 1:].split("/", 1)[0]
            m = read_mark(d)
            out.update(source="release", sha=(m or {}).get("sha", ""), path=str(d))
            return out
    cur = str(current_link())
    if any(a.startswith(cur + "/") for a in args):
        out.update(source="release", sha=release_at(_started(pid)))
        return out
    co = default_checkout()
    if co and any(a.startswith(str(co) + "/") for a in args):
        out.update(source="checkout", path=str(co))
        return out
    out["source"] = "other"
    return out


def status() -> dict:
    """`media version`: the release in use, the one before, and each unit
    that does not run the release in use."""
    cur, prev = current(), previous()
    man = read_manifest()
    units = {}
    co = default_checkout()
    names = set(man["units"])
    if co:
        names |= set(units_naming(co))
    for u in sorted(names):
        r = unit_runs(u)
        r["mode"] = "release" if u in man["units"] else "checkout"
        units[u] = r
    return {"current": cur, "previous": prev, "running": info(), "units": units,
            "deployed": deployed_at(cur["sha"]) if cur else 0.0,
            "links": sorted(man["links"])}


def _when(t: float) -> str:
    return time.strftime("%-d %b %H:%M", time.localtime(t)) if t else "?"


def status_lines(st: "Optional[dict]" = None) -> "list[str]":
    st = st or status()
    cur, prev = st["current"], st["previous"]
    lines = []
    if cur:
        lines.append(f"release {cur['sha'][:12]}"
                     + (f" ({cur['ref']})" if cur.get("ref") else "")
                     + f", in use since {_when(st['deployed'])}, built "
                     f"{_when(float(cur.get('built') or 0))}")
        if prev:
            lines.append(f"previous {prev['sha'][:12]} (media update --rollback)")
    else:
        lines.append("no release here: this host runs from a checkout or a plain install")
    me = st["running"]
    if me["mode"] != "release" or not cur or me["sha"] != cur["sha"]:
        lines.append(f"this command runs from {me['mode']}"
                     + (f" {me['short']}" if me["short"] else "")
                     + (f" ({me['path']})" if me["path"] else ""))
    units = st["units"]
    if not units:
        return lines
    same = [u for u, r in units.items()
            if r["source"] == "release" and cur and r["sha"] == cur["sha"]]
    idle = [u for u, r in units.items() if r["mode"] == "release" and not r["source"]]
    lines.append(f"units: {len(same)} run this release"
                 + (f", {len(idle)} start from it when next run (timers)" if idle else ""))
    for u, r in units.items():
        name = u.removesuffix(".service")
        if u in same or u in idle:
            continue
        if r["source"] == "release":
            lines.append(f"  {name}: runs {r['sha'][:12] or 'an earlier release'} "
                         "(not restarted since the switch)")
        elif r["mode"] == "release":
            lines.append(f"  {name}: set to the release, still runs from "
                         f"{r['source'] or 'nothing'} (not restarted since)")
        elif r["source"]:
            lines.append(f"  {name}: runs from the checkout")
        else:
            lines.append(f"  {name}: starts from the checkout (not running)")
    return lines


# --- update, rollback ------------------------------------------------------------------


def _mode_commands() -> "dict[str, str]":
    """Unit → command, for the units in release mode."""
    man = read_manifest()
    udir = systemd_dir()
    out = {}
    for u, rec in man["units"].items():
        out[u] = plan_unit(u, Path(rec.get("checkout") or ""), udir=udir).command
    return out


def _restart(units: "list[str]", emit: Emit, *, force: bool = False,
             after: float = 10.0) -> "tuple[int, list[str], list[str]]":
    """Restart `units` through restart_services, waiting out a busy host:
    (rc, the units restarted, the units it does not manage). The session
    holder goes last, drained (`restart_services.drain`), so no chat is cut
    off mid-turn; `force` (stepping back from a release that failed) skips
    the waits."""
    from . import restart_services as rs

    svcs = rs.services()
    stems = [u.removesuffix(".service") for u in units]
    known = {s.name: s for s in svcs}
    managed = [n for n in stems if n in known]
    unmanaged = [n for n in stems if n not in known]
    idle = [n for n in managed if known[n].state not in ("up", "failed")]
    live = [n for n in managed if n not in idle]
    holder = [n for n in live if rs.is_sessiond(known[n])]
    rest = [n for n in live if n not in holder]
    rc, done = rs.OK, []
    if rest:
        res = rs.run(rest, fire=True, force=force, echo=emit)
        rc = int(res["rc"])
        if rc == rs.OK:
            done += rest
    if holder and rc == rs.OK:
        res = (rs.run(holder, fire=True, force=True, echo=emit) if force
               else rs.drain(holder, after=after, echo=emit))
        rc = int(res["rc"])
        if rc == rs.OK:
            done += holder
    return rc, done, unmanaged


def _alert(level: str, title: str = "", detail: str = "", fix: str = "") -> None:
    from . import restart_services as rs

    rs.alert(level, title, detail, fix, name="release")


def _inside_session_holder() -> bool:
    return bool(os.environ.get("MEDIA_SESSIOND_SESSION"))


def _detach(argv: "list[str]") -> dict:
    """Run `argv` outside the caller (a transient systemd user unit; else a
    detached child), so it outlives a chat whose session holder it restarts."""
    from . import restart_services as rs

    if _which("systemd-run"):
        unit = f"media-update-{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}"
        cmd = ["systemd-run", "--user", f"--unit={unit}", "--collect",
               "--description=media update: restart the changed services"]
        cmd += [f"--setenv={k}={os.environ[k]}" for k in (*rs.PASS_ENV, "MEDIA_RELEASE_ROOT",
                                                          "XDG_DATA_HOME")
                if os.environ.get(k)]
        _ok([*cmd, "--", *argv], timeout=30)
        return {"how": "systemd-run", "unit": unit, "log": f"journalctl --user -u {unit}"}
    env = {k: v for k, v in os.environ.items() if k != "MEDIA_SESSIOND_SESSION"}
    p = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, env=env, start_new_session=True)
    return {"how": "detached", "unit": f"pid {p.pid}", "log": str(deploy_log())}


def finish(units: "list[str]", new: str, old: str, *, emit: Emit = print,
           rollback: bool = True) -> int:
    """The second half of an update: restart `units` on release `new`, and
    when one fails its health check, step back to `old` and restart them
    again. A host that stayed busy (rc 3) is not a failure of the release:
    it is left switched, the units named, to be restarted later."""
    from . import restart_services as rs

    rc, done, unmanaged = _restart(units, emit)
    if unmanaged:
        emit("not restarted (not services restart-services manages; they take the "
             "release at their next start): " + " ".join(unmanaged))
    if rc == rs.OK:
        log("restarted", sha=new, units=done)
        _alert("ok")
        emit(f"release {new[:12]} is live" + (f": {len(done)} unit(s) restarted" if done
                                             else ""))
        return 0
    if rc == rs.BUSY:
        log("busy", sha=new, units=[u.removesuffix(".service") for u in units], done=done)
        emit(f"release {new[:12]} is in place, but the host stayed busy: restart the rest "
             "with `media restart-services --only … --after 10` (`media version` names them)")
        return rc
    prev = releases_dir() / old[:12] if old else None
    if not rollback or not prev or not read_mark(prev):
        log("failed", sha=new, units=[u.removesuffix(".service") for u in units])
        _alert("warn", f"release {new[:12]} failed on {rs.host()} and there is none to "
               "step back to", "", "media version")
        emit(f"release {new[:12]} FAILED and there is no release to step back to")
        return 1
    emit(f"release {new[:12]} FAILED its health check: stepping back to {old[:12]}")
    switch(prev)
    refresh_mode(emit)
    log("rollback", sha=old, **{"from": new}, auto=True)
    rc2, _done, _ = _restart(units, emit, force=True)
    _alert("warn", f"release {new[:12]} failed on {rs.host()}; stepped back to {old[:12]}",
           "The services it restarted did not come up; the previous release runs again"
           + ("" if rc2 == rs.OK else ", but a service is still down"),
           "media version; journalctl --user -e")
    emit(f"stepped back to {old[:12]}" + ("" if rc2 == rs.OK
                                          else ", and a service is STILL down"))
    return 1


def _go(units: "list[str]", new: str, old: str, emit: Emit, *, rollback: bool = True) -> int:
    """`finish`, here or, when this runs inside a chat of the session holder
    it is about to restart, detached so the chat can end its turn."""
    from . import restart_services as rs

    stems = [u.removesuffix(".service") for u in units]
    if _inside_session_holder() and any(rs.short_name(n) == rs.SESSIOND for n in stems):
        argv = [sys.executable, "-m", "agent_media_core.release", "--finish", new,
                "--from", old or "-", "--units", *units]
        if not rollback:
            argv.append("--no-rollback")
        try:
            sch = _detach(argv)
        except ReleaseError as e:
            emit(f"could not hand the restarts over: {e}")
            return 1
        emit(f"the restarts run outside this chat ({sch['how']} {sch['unit']}; log: "
             f"{sch['log']}): {' '.join(stems)}. The session host is drained, so end "
             "your turn; the result is in the deploy log (`media version --log`)")
        return 0
    return finish(units, new, old, emit=emit, rollback=rollback)


def update(to: str = "", *, remote: str = "", only: "Optional[list[str]]" = None,
           restart: bool = True, every: bool = False, dry_run: bool = False,
           emit: Emit = print) -> int:
    """`media update`: fetch, build the release for `to` (default the
    configured branch) beside the one in use, switch `current` to it, and
    restart the units in release mode whose code changed (`every`: all of
    them; `only`: just these; `restart=False`: none). A unit that does not
    come up sends the host back to the release before."""
    cfg = config()
    try:
        url = fetch(remote or cfg.remote)
        ref = to or cfg.branch
        sha = resolve_ref(ref)
        cur = current()
        old = str(cur["sha"]) if cur else ""
        if old == sha and not every and not only:
            emit(f"already on {sha[:12]} ({ref}): nothing to do")
            return 0
        changed = changed_files(old, sha) if old else None
        if dry_run:
            emit(f"would build {sha[:12]} ({ref} of {url}) and switch from "
                 f"{old[:12] or 'nothing'}"
                 + (f": {len(changed)} file(s) differ" if changed is not None else ""))
            if cur and changed is not None:
                units, why = units_to_restart(changed, _mode_commands(), Path(cur["path"]))
                for u in units:
                    emit(f"would restart {u.removesuffix('.service')} ({why[u]})")
            return 0
        d = build(sha, cfg, ref=ref if ref != sha else "", emit=emit)
    except ReleaseError as e:
        emit(f"update: {e}")
        log("failed", ref=to or cfg.branch, error=str(e)[:400])
        return 1
    if old != sha:
        switch(d)
        log("switch", sha=sha, **{"from": old}, ref=ref)
        emit(f"current is {sha[:12]}" + (f" (was {old[:12]})" if old else ""))
    refreshed = refresh_mode(emit)
    commands = _mode_commands()
    if every:
        units = sorted(commands)
    else:
        units, why = units_to_restart(changed, commands, d)
        units = sorted(set(units) | set(refreshed))
        for u in units:
            emit(f"{u.removesuffix('.service')}: changed ({why.get(u, 'its unit')})")
    if only:
        units, unknown = _match(units if not every else sorted(commands), only)
        if unknown:
            emit(f"not among the units to restart: {', '.join(unknown)}")
    for gone in prune(cfg.keep):
        emit(f"removed old release {gone}")
    if not commands:
        emit("no unit is in release mode yet (`media-setup release-mode`): nothing "
             "to restart")
        return 0
    if not restart or not units:
        emit("nothing restarted" + ("" if units else ": no running unit loads what changed"))
        return 0
    return _go(units, sha, old, emit)


def rollback(*, restart: bool = True, dry_run: bool = False, emit: Emit = print) -> int:
    """`media update --rollback`: point `current` back at `previous` and
    restart the units whose code differs between the two. Doing it twice
    returns to where it started."""
    cur, prev = current(), previous()
    if not prev:
        emit("no previous release to step back to")
        return 1
    new, old = str(prev["sha"]), str(cur["sha"]) if cur else ""
    if new == old:
        emit(f"already on {new[:12]}")
        return 0
    try:
        changed = changed_files(old, new) if old else None
    except ReleaseError:
        changed = None
    if dry_run:
        emit(f"would step back from {old[:12]} to {new[:12]}")
        return 0
    switch(Path(prev["path"]))
    log("rollback", sha=new, **{"from": old})
    emit(f"current is {new[:12]} (was {old[:12]})")
    refreshed = refresh_mode(emit)
    commands = _mode_commands()
    units, _why = units_to_restart(changed, commands, Path(prev["path"]))
    units = sorted(set(units) | set(refreshed))
    if not restart or not units:
        emit("nothing restarted")
        return 0
    return _go(units, new, old, emit, rollback=False)


def main(argv: "Optional[list[str]]" = None) -> int:
    """The detached half of an update (`_go`); people use `media update`."""
    import argparse

    ap = argparse.ArgumentParser(prog="release")
    ap.add_argument("--finish", required=True, metavar="SHA")
    ap.add_argument("--from", dest="old", default="-")
    ap.add_argument("--units", nargs="+", default=[])
    ap.add_argument("--no-rollback", action="store_true")
    a = ap.parse_args(argv)
    from .intake._env import load_env_file

    load_env_file("release")
    return finish(a.units, a.finish, "" if a.old == "-" else a.old,
                  emit=lambda line: print(line, flush=True), rollback=not a.no_rollback)


if __name__ == "__main__":
    sys.exit(main())
