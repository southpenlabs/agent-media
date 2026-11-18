"""One file per chat, with no folder links (docs/proposals/2026-11-18-no-folder-links.md).

Claude Code files a chat under `~/.claude/projects/<encoded cwd>/` and
`claude --resume <id>` only finds it from that folder. A repo that moves
leaves its chats under the old folder's name; a link from the old name to the
new one made them resumable again (agent-media, 17 Nov; websites and
somahz-home, 18 Nov 2026). This module makes the link unnecessary, and keeps
the ones that stay accountable:

- **`move-folder <old path> <new path>`** moves every chat filed under the old
  path's name into the new one's, each with its side folder (subagents, tool
  results), records the new cwd in `moved.json` (as moves.py does for one
  thread), merges Claude's `memory` notes, leaves a chat written in the last
  `LIVE_S` alone (it is live), backs up whatever it would overwrite (larger,
  then newer, wins: what the 18 Nov cleanup did), and then removes the old
  name, or with `--link` leaves a link there and records it.
- **The link record**, `<state>/claude-folder-links.json`:
  `{"<old folder name>": {"to", "old_path", "why", "at"}}` (`to` is the new
  path; `deprecated` once marked). David, 18 Nov 2026: links are fine where
  they make sense, "but have a plan to remove them in the case of
  deprecation".
- **`folder-links check`** reports a chat with files in two folders and a link
  nobody recorded, and retires a recorded link once it is deprecated (nothing
  at `old_path`, or marked) and quiet (no chat written through the old name for
  `QUIET_S`): anything still filed under the old name is moved in, and the link
  and its record go. A deprecated link still written through raises the alert
  naming the chats that use it. One alert, `claude-folders.<host>`.

Everything is a dry run unless `apply` is given. Nothing here deletes a chat:
what loses goes to `~/backups/claude-duplicates/<when>/<folder name>/`.
"""

from __future__ import annotations

import argparse
import filecmp
import json
import os
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

from ._jsonmap import JsonMap
from agent_media_core import failures as _failures

LINKS = JsonMap("claude-folder-links.json")
#: A chat written this recently is live: its process would go on writing
#: where it was, so it is not moved.
LIVE_S = 15 * 60
#: A deprecated link nothing has written through for this long is retired.
QUIET_S = 14 * 86400
ALERT = "claude-folders"


def root() -> Path:
    from . import moves

    return moves.claude_root()


def encoded(path: str) -> str:
    from . import moves

    return moves.encoded_dir(path)


def backup_root() -> Path:
    base = os.environ.get("MEDIA_CLAUDE_BACKUPS")
    return Path(base) if base else Path.home() / "backups" / "claude-duplicates"


def _norm(path: str) -> str:
    return os.path.normpath(os.path.abspath(os.path.expanduser(path)))


def _newest(p: Path) -> float:
    """The newest mtime in a file or under a folder (0 when unreadable)."""
    try:
        if p.is_symlink() or not p.is_dir():
            return p.lstat().st_mtime
        best = p.stat().st_mtime
        for dirpath, _dirs, files in os.walk(p):
            for f in files:
                try:
                    best = max(best, os.lstat(os.path.join(dirpath, f)).st_mtime)
                except OSError:
                    pass
        return best
    except OSError:
        return 0.0


def _weight(p: Path) -> tuple[int, float]:
    """Larger, then newer: the copy that wins."""
    try:
        if p.is_dir():
            size = sum(os.path.getsize(os.path.join(d, f))
                       for d, _s, fs in os.walk(p) for f in fs)
        else:
            size = p.stat().st_size
    except OSError:
        size = 0
    return size, _newest(p)


def _first_cwd(f: Path) -> str:
    """The first `cwd` a Claude transcript records ("" when none)."""
    try:
        with f.open("rb") as fh:
            for _ in range(50):
                line = fh.readline()
                if not line:
                    break
                if b'"cwd"' not in line:
                    continue
                try:
                    cwd = json.loads(line).get("cwd")
                except ValueError:
                    continue
                if cwd:
                    return str(cwd)
    except OSError:
        pass
    return ""


def _last_cwd(f: Path, tail: int = 256 * 1024) -> str:
    """The last `cwd` a transcript records: the folder it was last opened in."""
    try:
        with f.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - tail))
            lines = fh.read().splitlines()
    except OSError:
        return ""
    for line in reversed(lines):
        if b'"cwd"' not in line:
            continue
        try:
            cwd = json.loads(line).get("cwd")
        except ValueError:
            continue
        if cwd:
            return str(cwd)
    return ""


# --- the plan -----------------------------------------------------------------

class Plan:
    """What a move would do (dry run) or did (apply), step by step."""

    def __init__(self, apply: bool, stamp: str = ""):
        self.apply = apply
        self.stamp = stamp or datetime.now().strftime("%Y%m%d-%H%M%S")
        self.steps: list[dict] = []
        self.moved: list[str] = []            # chat ids now filed under the new name
        self.live: list[str] = []
        self.errors: list[str] = []

    def add(self, op: str, **kw) -> None:
        self.steps.append({"op": op, **kw})

    def backup_dir(self, folder: str) -> Path:
        return backup_root() / self.stamp / folder

    def as_dict(self) -> dict:
        ops: dict[str, int] = {}
        for s in self.steps:
            ops[s["op"]] = ops.get(s["op"], 0) + 1
        return {"apply": self.apply, "steps": self.steps, "counts": ops,
                "moved": len(self.moved), "live": self.live, "errors": self.errors,
                "backup": str(backup_root() / self.stamp)}


def _back_up(plan: Plan, src: Path, folder: str, rel: str) -> None:
    """Move `src` aside into the backup, as `<folder>/<rel>`."""
    dest = plan.backup_dir(folder) / rel
    plan.add("back-up", path=str(src), to=str(dest))
    if not plan.apply:
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest = dest.with_name(f"{dest.name}.{int(time.time() * 1000)}")
    shutil.move(str(src), str(dest))


def _merge_tree(plan: Plan, src: Path, dest: Path, src_wins: bool,
                src_folder: str, dest_folder: str, rel: str) -> None:
    """Merge folder `src` into `dest`: what only `src` has moves in; where both
    have a file, identical ones are dropped from `src`, otherwise the loser
    (`src_wins` says which, for a whole chat's side folder) is backed up."""
    for child in sorted(src.iterdir()):
        target = dest / child.name
        sub = f"{rel}/{child.name}"
        if not target.exists() and not target.is_symlink():
            plan.add("move", path=str(child), to=str(target))
            if plan.apply:
                shutil.move(str(child), str(target))
        elif child.is_dir() and not child.is_symlink() and target.is_dir():
            _merge_tree(plan, child, target, src_wins, src_folder, dest_folder, sub)
        elif child.is_file() and target.is_file() and filecmp.cmp(child, target, shallow=False):
            plan.add("same", path=str(child), at=str(target))
            if plan.apply:
                child.unlink()
        elif src_wins:
            _back_up(plan, target, dest_folder, sub)
            plan.add("move", path=str(child), to=str(target))
            if plan.apply:
                shutil.move(str(child), str(target))
        else:
            _back_up(plan, child, src_folder, sub)
    if plan.apply:
        try:
            src.rmdir()
        except OSError:
            pass


def _merge_memory_index(plan: Plan, src: Path, dest: Path) -> None:
    """MEMORY.md: the old index's lines the new one lacks go on its end."""
    try:
        have = dest.read_text().splitlines()
        theirs = src.read_text().splitlines()
    except OSError as e:
        plan.errors.append(f"memory index: {e}")
        return
    seen = {ln.strip() for ln in have}
    extra = [ln for ln in theirs if ln.strip() and ln.strip() not in seen]
    plan.add("merge-index", path=str(src), into=str(dest), lines=len(extra))
    if not plan.apply:
        return
    if extra:
        text = "\n".join(have).rstrip("\n") + "\n\n" + "\n".join(extra) + "\n"
        dest.write_text(text)
    src.unlink()


def _merge_memory(plan: Plan, src: Path, dest: Path, old_name: str, new_name: str) -> None:
    if not dest.exists():
        plan.add("move", path=str(src), to=str(dest))
        if plan.apply:
            shutil.move(str(src), str(dest))
        return
    idx_src, idx_dest = src / "MEMORY.md", dest / "MEMORY.md"
    merged = idx_src.is_file() and idx_dest.is_file() \
        and not filecmp.cmp(idx_src, idx_dest, shallow=False)
    if merged:
        _merge_memory_index(plan, idx_src, idx_dest)
    for child in sorted(src.iterdir()):
        if merged and child.name == "MEMORY.md":
            continue                    # merged above
        target = dest / child.name
        if not target.exists():
            plan.add("move", path=str(child), to=str(target))
            if plan.apply:
                shutil.move(str(child), str(target))
        elif child.is_dir() and target.is_dir():
            _merge_tree(plan, child, target, _weight(child) > _weight(target),
                        old_name, new_name, f"memory/{child.name}")
        elif child.is_file() and target.is_file() and filecmp.cmp(child, target, shallow=False):
            plan.add("same", path=str(child), at=str(target))
            if plan.apply:
                child.unlink()
        elif _weight(child) > _weight(target):
            _back_up(plan, target, new_name, f"memory/{child.name}")
            plan.add("move", path=str(child), to=str(target))
            if plan.apply:
                shutil.move(str(child), str(target))
        else:
            _back_up(plan, child, old_name, f"memory/{child.name}")
    if plan.apply:
        try:
            src.rmdir()
        except OSError:
            pass


def _record_moved(plan: Plan, session: str, new_path: str, old_path: str) -> None:
    """`moved.json` says where the chat is to be resumed: the new path."""
    from . import moves, sessions

    if not sessions._SESSION.fullmatch(session):
        return
    row = moves.moved(session)
    if row and _norm(str(row.get("cwd") or "")) not in (_norm(old_path), _norm(new_path)):
        plan.add("keep-move", session=session, cwd=row.get("cwd"))
        return
    if row and _norm(str(row.get("cwd") or "")) == _norm(new_path):
        return
    try:
        project = sessions.project_of(new_path) or ""
    except Exception as _exc:  # noqa: BLE001 — a project name is a nicety here
        _failures.swallowed(_exc)
        project = ""
    plan.add("record-move", session=session, cwd=new_path)
    if plan.apply:
        moves.MOVES.put(session, {"project": project, "cwd": new_path,
                                  "at": round(time.time(), 3)})
        sessions._CWDS.pop(session, None)


def _move_contents(plan: Plan, src: Path, dest: Path, old_path: str, new_path: str,
                   now: float) -> None:
    """Every chat (and anything else) in folder `src` into folder `dest`."""
    old_name, new_name = src.name, dest.name
    if plan.apply:
        dest.mkdir(parents=True, exist_ok=True)
    entries = sorted(src.iterdir())
    chats = {e.stem for e in entries if e.suffix == ".jsonl" and e.is_file()}
    for e in entries:
        if e.name == "memory" and e.is_dir():
            _merge_memory(plan, e, dest / "memory", old_name, new_name)
            continue
        if e.is_dir() and e.name in chats:
            continue                    # a chat's side folder goes with it
        if e.suffix == ".jsonl" and e.stem in chats:
            sid = e.stem
            side = src / sid
            if now - max(_newest(e), _newest(side) if side.is_dir() else 0.0) < LIVE_S:
                plan.live.append(sid)
                plan.add("live", path=str(e))
                continue
            target = dest / e.name
            src_wins = True
            if target.exists():
                tside = dest / sid
                mine = (_weight(e)[0] + (_weight(side)[0] if side.is_dir() else 0), _newest(e))
                theirs = (_weight(target)[0] + (_weight(tside)[0] if tside.is_dir() else 0),
                          _newest(target))
                if now - _newest(target) < LIVE_S:
                    src_wins = False    # the one being written stays
                else:
                    src_wins = mine > theirs
                if src_wins:
                    _back_up(plan, target, new_name, e.name)
                    plan.add("move", path=str(e), to=str(target))
                    if plan.apply:
                        shutil.move(str(e), str(target))
                else:
                    _back_up(plan, e, old_name, e.name)
            else:
                plan.add("move", path=str(e), to=str(target))
                if plan.apply:
                    shutil.move(str(e), str(target))
            if side.is_dir():
                tside = dest / sid
                if not tside.exists():
                    plan.add("move", path=str(side), to=str(tside))
                    if plan.apply:
                        shutil.move(str(side), str(tside))
                else:
                    _merge_tree(plan, side, tside, src_wins, old_name, new_name, sid)
            plan.moved.append(sid)
            _record_moved(plan, sid, new_path, old_path)
            continue
        # Anything else: in if the new folder lacks it, else larger-then-newer.
        target = dest / e.name
        if not target.exists():
            plan.add("move", path=str(e), to=str(target))
            if plan.apply:
                shutil.move(str(e), str(target))
        elif e.is_dir() and target.is_dir():
            _merge_tree(plan, e, target, _weight(e) > _weight(target),
                        old_name, new_name, e.name)
        elif e.is_file() and target.is_file() and filecmp.cmp(e, target, shallow=False):
            plan.add("same", path=str(e), at=str(target))
            if plan.apply:
                e.unlink()
        elif _weight(e) > _weight(target):
            _back_up(plan, target, new_name, e.name)
            plan.add("move", path=str(e), to=str(target))
            if plan.apply:
                shutil.move(str(e), str(target))
        else:
            _back_up(plan, e, old_name, e.name)


def _resumed_by_old_path(plan: Plan, folder: Path, old_path: str, new_path: str) -> None:
    """A link's chats are already in the new folder, but the ones that started
    at the old path are resumed from there: say where they are now."""
    for f in sorted(folder.glob("*.jsonl")):
        if _norm(_first_cwd(f) or "/") == _norm(old_path):
            _record_moved(plan, f.stem, new_path, old_path)


def move_folder(old_path: str, new_path: str, *, link: bool = False, why: str = "",
                apply: bool = False, now: float | None = None, stamp: str = "") -> dict:
    """Move every chat filed under `old_path`'s Claude folder into `new_path`'s.

    `{"old", "new", "plan", "result", "error"}`; a dry run unless `apply`.
    """
    old_path, new_path = _norm(old_path), _norm(new_path)
    now = time.time() if now is None else now
    base = root()
    old_name, new_name = encoded(old_path), encoded(new_path)
    src, dest = base / old_name, base / new_name
    plan = Plan(apply, stamp)
    out = {"old": str(src), "new": str(dest), "old_path": old_path, "new_path": new_path}
    if old_name == new_name:
        return {**out, "error": "the two paths share one Claude folder", "plan": plan.as_dict()}
    if not os.path.isdir(new_path):
        return {**out, "error": f"{new_path} is not a directory on this host",
                "plan": plan.as_dict()}
    if dest.is_symlink():
        return {**out, "error": f"{dest} is itself a link", "plan": plan.as_dict()}
    if src.is_symlink():
        target = Path(os.path.realpath(src))
        if target != Path(os.path.realpath(dest)):
            return {**out, "error": f"{src} is a link to {os.readlink(src)}, not to {dest}",
                    "plan": plan.as_dict()}
        # Its chats are in the new folder already.
        _resumed_by_old_path(plan, dest, old_path, new_path)
    elif src.is_dir():
        _move_contents(plan, src, dest, old_path, new_path, now)
    elif not src.exists():
        plan.add("nothing", path=str(src))

    result = ""
    if plan.live:
        result = (f"{len(plan.live)} live chat(s) left under {old_name}; "
                  "run it again once they have been quiet 15 minutes")
    elif link:
        if not src.is_symlink():
            plan.add("link", path=str(src), to=str(dest))
            if apply:
                try:
                    src.rmdir()
                except FileNotFoundError:
                    pass
                except OSError as e:
                    plan.errors.append(f"{src} is not empty: {e}")
                if not src.exists():
                    src.symlink_to(dest)
        plan.add("record-link", name=old_name, to=new_path)
        if apply and src.is_symlink():
            record(old_path, new_path, why=why or f"repo moved {old_path} -> {new_path}")
        result = f"{old_name} left as a recorded link to {new_name}"
    else:
        if src.is_symlink():
            plan.add("unlink", path=str(src))
            if apply:
                src.unlink()
        elif src.exists():
            plan.add("remove-folder", path=str(src))
            if apply:
                try:
                    src.rmdir()
                except OSError as e:
                    plan.errors.append(f"{src} is not empty: {e}")
        if LINKS.get(old_name):
            plan.add("drop-record", name=old_name)
            if apply:
                LINKS.drop(old_name)
        result = f"{old_name} removed; its chats are under {new_name}"
    return {**out, "plan": plan.as_dict(), "result": result, "error": ""}


# --- the record ---------------------------------------------------------------

def links() -> dict:
    return {k: v for k, v in LINKS.all().items() if isinstance(v, dict)}


def record(old_path: str, new_path: str, why: str, at: float | None = None) -> dict:
    old_path, new_path = _norm(old_path), _norm(new_path)
    row = {"to": new_path, "old_path": old_path, "why": why,
           "at": round(time.time() if at is None else at, 3)}
    LINKS.put(encoded(old_path), row)
    return row


def deprecate(name_or_path: str, on: bool = True) -> bool:
    name = name_or_path if name_or_path in LINKS.all() else encoded(_norm(name_or_path))

    def fn(rows: dict) -> bool:
        row = rows.get(name)
        if not isinstance(row, dict):
            return False
        if on:
            row["deprecated"] = round(time.time(), 3)
        else:
            row.pop("deprecated", None)
        return True

    return LINKS.update(fn)


# --- the check ----------------------------------------------------------------

def _writes_through(folder: Path, old_path: str, since: float) -> list[tuple[str, float]]:
    """Chats in `folder` written since `since` whose last cwd is `old_path`:
    something opened them at the old path, so through the old folder name."""
    old = _norm(old_path)
    hits = []
    try:
        files = list(folder.glob("*.jsonl"))
    except OSError:
        return hits
    for f in files:
        try:
            m = f.stat().st_mtime
        except OSError:
            continue
        if m < since:
            continue
        cwd = _last_cwd(f)
        if cwd and _norm(cwd) == old:
            hits.append((f.stem, m))
    return sorted(hits, key=lambda h: -h[1])


def check(*, apply: bool = False, now: float | None = None, alert: bool = False) -> dict:
    """Copies in two folders, unrecorded links, and the recorded links'
    state; with `apply`, the deprecated and quiet ones retired."""
    now = time.time() if now is None else now
    base = root()
    rows = links()
    where: dict[str, list[str]] = {}
    found_links: dict[str, str] = {}
    try:
        entries = sorted(base.iterdir())
    except OSError:
        entries = []
    for d in entries:
        if d.is_symlink():
            found_links[d.name] = os.readlink(d)
            continue
        if not d.is_dir():
            continue
        try:
            for f in os.scandir(d):
                if f.name.endswith(".jsonl") and f.is_file(follow_symlinks=False):
                    where.setdefault(f.name[:-6], []).append(d.name)
        except OSError:
            continue
    copies = {sid: sorted(ds) for sid, ds in where.items() if len(ds) > 1}
    unrecorded = {n: t for n, t in found_links.items() if n not in rows}

    report_links = []
    retired = []
    problems = []
    for name, row in sorted(rows.items()):
        old_path, to = str(row.get("old_path") or ""), str(row.get("to") or "")
        entry = base / name
        folder = base / encoded(to) if to else None
        deprecated = bool(row.get("deprecated")) or not (old_path and os.path.lexists(old_path))
        writes = _writes_through(folder, old_path, now - QUIET_S) if folder else []
        if entry.is_dir() and not entry.is_symlink():
            # Something was filed under the old name itself.
            newest = max((_newest(f) for f in entry.iterdir()), default=0.0)
            if newest >= now - QUIET_S:
                writes.append((f"(a chat filed under {name})", newest))
        state = {"name": name, "to": to, "old_path": old_path, "why": row.get("why"),
                 "at": row.get("at"), "deprecated": deprecated,
                 "exists": entry.is_symlink() or entry.exists(),
                 "is_link": entry.is_symlink(),
                 "last_write_through": max((w[1] for w in writes), default=None),
                 "writing_through": [w[0] for w in writes[:5]]}
        if deprecated and writes:
            problems.append(f"{name} is deprecated ({old_path} gone or marked) but "
                            f"{len(writes)} chat(s) still open it: "
                            + ", ".join(w[0][:8] for w in writes[:5]))
            state["status"] = "deprecated, still used"
        elif deprecated:
            state["status"] = "retire"
            if apply:
                why = _retire(name, row, now)
                state["status"] = "retired" if not why else f"not retired: {why}"
                if why:
                    problems.append(f"{name}: {why}")
                else:
                    retired.append(name)
        else:
            state["status"] = "kept"
        report_links.append(state)

    for n, t in sorted(unrecorded.items()):
        problems.append(f"{n} is a link to {t} that nobody recorded")
    if copies:
        problems.append(f"{len(copies)} chat(s) have files in two folders: "
                        + ", ".join(f"{s[:8]} ({' + '.join(ds)})"
                                    for s, ds in sorted(copies.items())[:5]))
    out = {"copies": copies, "unrecorded_links": unrecorded, "links": report_links,
           "retired": retired, "problems": problems, "apply": apply}
    if alert:
        _alert(problems)
    return out


def _retire(name: str, row: dict, now: float) -> str:
    """Retire a deprecated, quiet link: stragglers in, link and record gone.
    "" or why not."""
    to, old_path = str(row.get("to") or ""), str(row.get("old_path") or "")
    if not to or not os.path.isdir(to):
        return f"its new path {to or '(none)'} is not a directory here"
    base = root()
    entry = base / name
    if entry.is_symlink() or entry.exists():
        r = move_folder(old_path, to, apply=True, now=now)
        if r.get("error"):
            return r["error"]
        if r["plan"]["live"]:
            return r["result"]
    LINKS.drop(name)
    print(f"claude-folders: retired {name} (link to {to}; {old_path} deprecated, "
          f"nothing written through it for {QUIET_S // 86400} days)", file=sys.stderr)
    return ""


def _alert(problems: list[str]) -> None:
    from agent_media_core import restart_services

    if problems:
        restart_services.alert(
            "warn", f"Claude chat folders: {len(problems)} to look at",
            "\n".join(problems),
            "media session folder-links check (and move-folder for a moved repo)",
            name=ALERT)
    else:
        restart_services.alert("ok", name=ALERT)


# --- the commands ---------------------------------------------------------------

def _print_plan(r: dict) -> None:
    p = r["plan"]
    mode = "" if p["apply"] else "(dry run) "
    print(f"{mode}{r['old']} -> {r['new']}")
    if r.get("error"):
        print(f"  error: {r['error']}")
        return
    counts = ", ".join(f"{k} {v}" for k, v in sorted(p["counts"].items()))
    print(f"  chats: {p['moved']} moved, {len(p['live'])} live left; steps: {counts or 'none'}")
    for s in p["steps"]:
        if s["op"] in ("back-up", "record-link", "link", "unlink", "remove-folder",
                       "drop-record", "merge-index", "keep-move", "live"):
            print("  " + " ".join(f"{k}={v}" for k, v in s.items()))
    if any(s["op"] == "back-up" for s in p["steps"]):
        print(f"  backups go to {p['backup']}")
    for e in p["errors"]:
        print(f"  error: {e}")
    print(f"  {r.get('result') or ''}")


def main(argv: list[str]) -> int:
    """`media session move-folder …` and `media session folder-links …`."""
    if not argv:
        argv = ["--help"]
    cmd, rest = argv[0], argv[1:]
    if cmd == "move-folder":
        ap = argparse.ArgumentParser(prog="media session move-folder",
                                     description="Move every Claude chat filed under OLD's "
                                     "folder into NEW's (a dry run without --apply).")
        ap.add_argument("old")
        ap.add_argument("new")
        ap.add_argument("--link", action="store_true",
                        help="leave a recorded link at the old name instead of removing it")
        ap.add_argument("--why", default="", help="why the link stays (with --link)")
        ap.add_argument("--apply", action="store_true")
        ap.add_argument("--json", action="store_true")
        a = ap.parse_args(rest)
        r = move_folder(a.old, a.new, link=a.link, why=a.why, apply=a.apply)
        if a.json:
            print(json.dumps(r, indent=1))
        else:
            _print_plan(r)
        return 1 if r.get("error") or r["plan"]["errors"] else 0
    if cmd == "folder-links":
        ap = argparse.ArgumentParser(prog="media session folder-links")
        sub = ap.add_subparsers(dest="what")
        sub.add_parser("list")
        c = sub.add_parser("check", help="copies, unrecorded links, links to retire")
        c.add_argument("--apply", action="store_true", help="retire the deprecated, quiet ones")
        c.add_argument("--no-alert", action="store_true")
        c.add_argument("--json", action="store_true")
        rec = sub.add_parser("record", help="record a link that is there already")
        rec.add_argument("old_path")
        rec.add_argument("new_path")
        rec.add_argument("--why", required=True)
        rec.add_argument("--at", default="", help="when it was made (YYYY-MM-DD[THH:MM])")
        dep = sub.add_parser("deprecate", help="mark a recorded link deprecated")
        dep.add_argument("name_or_path")
        dep.add_argument("--undo", action="store_true")
        a = ap.parse_args(rest)
        if a.what == "record":
            at = datetime.fromisoformat(a.at).timestamp() if a.at else None
            print(json.dumps({encoded(_norm(a.old_path)): record(a.old_path, a.new_path,
                                                                 a.why, at)}, indent=1))
            return 0
        if a.what == "deprecate":
            ok = deprecate(a.name_or_path, on=not a.undo)
            print("marked" if ok else "no such recorded link", file=sys.stderr)
            return 0 if ok else 1
        if a.what == "check":
            r = check(apply=a.apply, alert=not a.no_alert)
            if a.json:
                print(json.dumps(r, indent=1))
                return 0
            for s in r["links"]:
                print(f"{s['name']} -> {s['to']}: {s['status']}"
                      + (f" (last opened at the old path "
                         f"{datetime.fromtimestamp(s['last_write_through']):%d %b %H:%M})"
                         if s["last_write_through"] else ""))
            for p in r["problems"]:
                print(f"problem: {p}")
            if not r["problems"]:
                print("no copies, no unrecorded links")
            return 0
        print(json.dumps(links(), indent=1))
        return 0
    print("media session move-folder OLD NEW [--link] [--apply] | "
          "folder-links [list|check|record|deprecate]", file=sys.stderr)
    return 2
