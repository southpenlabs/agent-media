"""Which chats hold a checkout's changes, for the git sheet's "N changed"
and "N to push" (David, 12 Nov 2026: "click on the indicator that specifies
how it differs from the branch or worktree to know which threads hold the
changes").

Git does not know which session wrote a line, so this reads the Claude Code
transcripts filed under the checkout's folder (and folders inside it, a
worktree under `.worktrees/` among them) and matches what they did against
what git lists:

- a changed file is a chat's when the chat wrote it with Edit, Write,
  MultiEdit or NotebookEdit (`how: "edit"`), or ran a shell command naming
  it alongside something that writes (`sed -i`, a `>` redirect, `tee`,
  `open(..., 'w')`, `git apply`, `mv`/`cp`/`rm`…: `how: "shell"`, a guess),
  after the file's last commit — an edit before that is already committed;
- an unpushed commit is a chat's when its hash shows up in the output of a
  command of that chat's that committed (`git commit`, `cherry-pick`,
  `merge`, `am`, `revert`).

What no transcript accounts for (another harness, a person at a terminal,
an edit made from a folder outside the checkout) is listed as unclaimed.

`add_changes(rows)` puts the same answer on the chat list's rows, counted
rather than listed (`changes: {"files", "commits", "at"}`), from a snapshot
per checkout that a list refreshes behind itself.

Each transcript is read once and then only from where it was read up to,
so asking again costs only what was appended since.
"""

from __future__ import annotations

import glob
import json
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path

from . import git_state

# Transcripts untouched for longer than this are not read.
MAX_AGE_S = 14 * 86400
MAX_SHELL = 20000
SNIPPET = 160
MAX_THREADS = 20

_EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
# A command that rewrites whatever files it names: one naming a changed
# file is taken to have written it.
_WRITES = re.compile(
    r"\bsed\s+(-[a-zA-Z]*\s+)*-[a-zA-Z]*i|\bperl\s+-[a-zA-Z]*i|"
    r"open\([^)]*['\"][wa]b?\+?['\"]|write_text|write_bytes|\bgit\s+(apply|checkout|restore|mv|rm)\b|"
    r"\bpatch\s|(^|[;&|(]\s*)(mv|cp|rm|touch|truncate|install)\s")
# A redirect or tee: only the file it is aimed at was written.
_AIMED = re.compile(r">|\btee\b")
_COMMITS = re.compile(r"\bgit\b[^\n|;&]*\b(commit|cherry-pick|merge|am|revert|rebase)\b")
_HEX = re.compile(rb"\b[0-9a-f]{7,40}\b")

_lock = threading.Lock()
# transcript path -> (bytes read, its events, the folders it ran in, the
# committing commands whose output has not come yet)
_seen: dict[str, tuple[int, list[tuple], set[str], dict[str, str]]] = {}


def _slug(path: str) -> str:
    """Claude Code's folder name for a working directory."""
    return re.sub(r"[^A-Za-z0-9]", "-", path)


def _projects_dir() -> Path:
    from . import recaps

    return recaps._claude_dir() / "projects"


def _transcripts(top: str) -> list[str]:
    """Transcripts of sessions that ran in `top` or a folder inside it."""
    out: set[str] = set()
    base = _projects_dir()
    for root in {top, os.path.realpath(top)}:
        slug = _slug(root)
        for d in glob.glob(str(base / glob.escape(slug))) + glob.glob(str(base / (glob.escape(slug) + "-*"))):
            out.update(glob.glob(os.path.join(d, "*.jsonl")))
    return sorted(out)


def _at(stamp) -> float:
    try:
        return datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _text_of(content) -> bytes:
    if isinstance(content, str):
        return content.encode("utf-8", "replace")
    if isinstance(content, list):
        return b"\n".join(_text_of(c.get("text") if isinstance(c, dict) else c) for c in content)
    return b""


def _scan(path: str) -> tuple[list[tuple], set[str]]:
    """The transcript's events, read on from where the last call stopped:
    ("edit", at, abs path), ("shell", at, command, cwd), ("commit", at,
    {hashes}); and the folders it ran in."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return [], set()
    with _lock:
        offset, events, cwds, pending = _seen.get(path, (0, [], set(), {}))
    if size < offset:  # rewritten: read it again
        offset, events, cwds, pending = 0, [], set(), {}
    if size > offset:
        events, cwds, pending = list(events), set(cwds), dict(pending)
        try:
            with open(path, "rb") as fh:
                fh.seek(offset)
                data = fh.read(size - offset)
        except OSError:
            return events, cwds
        end = data.rfind(b"\n") + 1  # a line still being written waits
        for line in data[:end].splitlines():
            if b'"tool_use"' not in line and b'"tool_result"' not in line:
                i = line.find(b'"cwd":"')
                if i >= 0 and len(cwds) < 50:
                    j = line.find(b'"', i + 7)
                    if j > 0:
                        cwds.add(line[i + 7:j].decode("utf-8", "replace"))
                continue
            if b'"tool_use"' in line:
                if not (b'"name":"Bash"' in line or any(b'"name":"%s"' % n.encode() in line for n in _EDIT_TOOLS)):
                    continue
            elif not (pending and any(i.encode() in line for i in pending)):
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            at = _at(rec.get("timestamp"))
            cwd = str(rec.get("cwd") or "")
            if cwd:
                cwds.add(cwd)
            content = (rec.get("message") or {}).get("content")
            if not isinstance(content, list):
                continue
            for c in content:
                if not isinstance(c, dict):
                    continue
                if c.get("type") == "tool_use":
                    name, inp = c.get("name"), c.get("input") or {}
                    if name in _EDIT_TOOLS:
                        fp = str(inp.get("file_path") or inp.get("notebook_path") or "")
                        if fp:
                            events.append(("edit", at, os.path.join(cwd, fp) if cwd else fp, _snippet(name, inp)))
                    elif name == "Bash":
                        cmd = str(inp.get("command") or "")[:MAX_SHELL]
                        if _COMMITS.search(cmd):
                            pending[str(c.get("id") or "")] = cmd
                        if _WRITES.search(cmd) or _AIMED.search(cmd):
                            events.append(("shell", at, cmd, cwd))
                elif c.get("type") == "tool_result":
                    if pending.pop(str(c.get("tool_use_id") or ""), None) is not None:
                        hexes = {h.decode() for h in _HEX.findall(_text_of(c.get("content")))[:200]}
                        if hexes:
                            events.append(("commit", at, hexes))
        offset += end
        with _lock:
            _seen[path] = (offset, events, cwds, pending)
    return events, cwds


def _wrote(cmd: str, names: set[str]) -> bool:
    """Whether shell command `cmd` wrote a file known by any of `names`: it
    names one and rewrites what it names (`_WRITES`), or redirects or tees
    into one."""
    named = [n for n in names if n and n in cmd]
    if not named:
        return False
    if _WRITES.search(cmd):
        return True
    return any(re.search(r"(>>?|\btee\s+(-a\s+)?)\s*['\"]?" + re.escape(n) + r"(['\"\s;&|)]|$)", cmd) for n in named)


def _snippet(name: str, inp: dict) -> str:
    """A line the edit wrote, to tell an edit still in the file from one
    since undone: the longest line of its new text, cut to SNIPPET."""
    if name == "MultiEdit":
        text = "\n".join(str(e.get("new_string") or "") for e in inp.get("edits") or [] if isinstance(e, dict))
    else:
        text = str(inp.get("new_string") or inp.get("content") or inp.get("new_source") or "")
    lines = [l.strip() for l in text.splitlines() if len(l.strip()) >= 12]
    return max(lines, key=len)[:SNIPPET] if lines else ""


def _inside(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip("/") + "/")


def _since(top: str, path: str) -> float:
    """When `path` was last committed, 0 when never."""
    out = git_state._git(top, "log", "-1", "--format=%ct", "--", path)
    try:
        return float((out or "").strip() or 0)
    except ValueError:
        return 0.0


def who(cwd: str | None, now: float | None = None) -> dict | None:
    """`{"threads": [{"session", "files": [{"path", "how"}], "commits":
    [<sha>], "at"}], "unclaimed": {"files": [...], "commits": [...]}}` for
    the checkout `cwd` is in, newest chat first; None outside a checkout."""
    got = git_state.changes(cwd)
    if got is None:
        return None
    top = (git_state._git(cwd, "rev-parse", "--show-toplevel") or "").strip()
    if not top:
        return None
    now = time.time() if now is None else now
    tops = {top, os.path.realpath(top)}
    files = [f["path"] for f in got["files"]]
    froms = {f["path"]: f.get("from") for f in got["files"] if f.get("from")}
    untracked_paths = {f["path"] for f in got["files"] if f.get("untracked")}
    since = {p: (0.0 if f.get("untracked") else _since(top, p)) for f in got["files"] for p in [f["path"]]}
    commits = [c["full"] for c in got["history"] if c["pushed"] is False]
    floor = min([s for s in since.values() if s] + [now - MAX_AGE_S]) if (files or commits) else now
    for c in got["history"]:
        if c["pushed"] is False:
            floor = min(floor, float(c["at"]) - 3600)
    floor = max(floor, now - MAX_AGE_S)  # older chats are not read at all

    def matches(path: str, abs_path: str) -> bool:
        for t in tops:
            for p in (path, froms.get(path)):
                if not p:
                    continue
                full = os.path.join(t, p)
                if abs_path == full or (p.endswith("/") and abs_path.startswith(full)):
                    return True
        return False

    texts: dict[str, str | None] = {}

    def still_there(path: str, snippet: str) -> bool:
        """The edit's line is in the file now (True when it has none to check)."""
        if not snippet or path.endswith("/"):
            return True
        if path not in texts:
            try:
                with open(os.path.join(top, path), "rb") as fh:
                    texts[path] = fh.read(4 << 20).decode("utf-8", "replace")
            except OSError:
                texts[path] = None
        text = texts[path]
        return text is None or snippet in text

    added: dict[str, set[str]] = {}

    def heredoc_landed(path: str, cmd: str) -> bool:
        """A heredoc script carries the lines it wrote: one of them must be
        among the file's uncommitted added lines, or the write was since
        undone or committed elsewhere. Other commands (`sed -i`) pass."""
        if "<<" not in cmd or path.endswith("/"):
            return True
        if path not in added:
            untracked = since[path] == 0.0 and path in untracked_paths
            if untracked:
                try:
                    with open(os.path.join(top, path), "rb") as fh:
                        body = fh.read(4 << 20).decode("utf-8", "replace").splitlines()
                except OSError:
                    body = []
            else:
                out = git_state._git(top, "diff", "--no-ext-diff", "--no-color", "HEAD", "--", path) or ""
                body = [l[1:] for l in out.splitlines() if l.startswith("+") and not l.startswith("+++")]
            added[path] = {l.strip() for l in body if len(l.strip()) >= 20}
        return not added[path] or any(line in cmd for line in added[path])

    threads: dict[str, dict] = {}
    for tpath in _transcripts(top):
        try:
            if os.path.getmtime(tpath) < floor:
                continue
        except OSError:
            continue
        events, cwds = _scan(tpath)
        if not any(_inside(c, t) for c in cwds for t in tops):
            continue
        sid = Path(tpath).stem
        mine: dict[str, str] = {}
        made: list[str] = []
        last = 0.0
        for ev in events:
            kind, at = ev[0], ev[1]
            if kind == "edit":
                for p in files:
                    if at > since[p] and matches(p, os.path.normpath(ev[2])) and still_there(p, ev[3]):
                        mine[p] = "edit"
                        last = max(last, at)
            elif kind == "shell":
                cmd, where = ev[2], ev[3]
                if not any(_inside(where, t) for t in tops):
                    continue
                for p in files:
                    if p in mine or at <= since[p]:
                        continue
                    rel = os.path.relpath(os.path.join(top, p), where) if where else p
                    if (_wrote(cmd, {p.rstrip("/"), rel.rstrip("/"), os.path.join(top, p).rstrip("/")})
                            and heredoc_landed(p, cmd)):
                        mine[p] = "shell"
                        last = max(last, at)
            elif kind == "commit":
                for full in commits:
                    if full not in made and any(full.startswith(h) for h in ev[2]):
                        made.append(full)
                        last = max(last, at)
        if mine or made:
            threads[sid] = {"session": sid, "files": [{"path": p, "how": h} for p, h in mine.items()],
                            "commits": [c[:12] for c in made], "at": last}
    claimed_files = {f["path"] for t in threads.values() for f in t["files"]}
    claimed_commits = {c for t in threads.values() for c in t["commits"]}
    rows = sorted(threads.values(), key=lambda t: -t["at"])[:MAX_THREADS]
    return {"threads": rows,
            "unclaimed": {"files": [p for p in files if p not in claimed_files],
                          "commits": [c[:12] for c in commits if c[:12] not in claimed_commits]}}


_warming: set[str] = set()


def warm(cwd: str | None) -> None:
    """Read the checkout's transcripts in the background, so the sheet's
    later "who" asks find them read (a cold read of two weeks' chats takes
    seconds). Called when the git sheet opens; one at a time per folder."""
    if not cwd:
        return
    with _lock:
        if cwd in _warming:
            return
        _warming.add(cwd)

    def run():
        try:
            who(cwd)
        except Exception:  # a warm-up is best effort
            pass
        finally:
            with _lock:
                _warming.discard(cwd)

    threading.Thread(target=run, name="git-who-warm", daemon=True).start()


# ── The list's mark: which chats hold changes, by session ────────────────
#
# David, 12 Nov 2026: "add an indicator on the chat list when there are
# changes to the repository made by that thread and add a status to the
# filter to list only them". The list polls every few seconds and a who()
# over a fortnight of transcripts is not a thing to do in that request, so
# the rows are served from the last snapshot per checkout and a stale one is
# refreshed in the background: a chat's mark appears a few seconds after the
# first list, and follows it from then on.

#: How old a checkout's snapshot may be before the next list refreshes it.
SNAPSHOT_S = 90.0
#: How long a folder's checkout top is remembered (a `git rev-parse`).
TOP_S = 600.0
#: Checkouts refreshed at once; the rest wait for a later list.
MAX_REFRESH = 2

#: `{top: (at, {session: {"files", "commits", "at"}})}`
_snapshots: dict[str, tuple[float, dict[str, dict]]] = {}
#: `{cwd: (at, top | None)}` — None for a folder outside a checkout.
_tops: dict[str, tuple[float, str | None]] = {}
#: "not looked up yet", which None (outside a checkout) is not.
_UNKNOWN = object()
_refreshing: set[str] = set()


def top_of(cwd: str | None, now: float | None = None) -> str | None:
    """The checkout `cwd` is in, remembered for TOP_S; None outside one. Runs
    `git rev-parse` when it does not know yet, so only a background read or a
    test calls it: a list reads `_cached_top`."""
    if not cwd:
        return None
    now = time.time() if now is None else now
    got = _cached_top(cwd, now)
    if got is not _UNKNOWN:
        return got
    top = None
    if os.path.isdir(cwd):
        top = (git_state._git(cwd, "rev-parse", "--show-toplevel") or "").strip() or None
    with _lock:
        _tops[cwd] = (now, top)
    return top


def _cached_top(cwd: str, now: float):
    """The remembered checkout for `cwd` (None outside one), `_UNKNOWN` when
    it has not been looked up, or has not been for TOP_S."""
    with _lock:
        got = _tops.get(cwd)
    return got[1] if got and now - got[0] < TOP_S else _UNKNOWN


def _snapshot(top: str) -> dict[str, dict]:
    """who(top) as `{session: {"files", "commits", "at"}}`."""
    got = who(top)
    rows: dict[str, dict] = {}
    for row in (got or {}).get("threads", []):
        rows[row["session"]] = {"files": len(row["files"]), "commits": len(row["commits"]),
                                "at": row["at"]}
    return rows


def refresh(top: str) -> dict[str, dict]:
    """Read the checkout now and keep the answer (the tests' way in)."""
    rows = _snapshot(top)
    with _lock:
        _snapshots[top] = (time.time(), rows)
    return rows


def _read_soon(cwd: str, top: str | None) -> None:
    """Read it behind the list: find the checkout first when `top` is not
    known yet, then its chats. One go per folder, MAX_REFRESH at once."""
    key = top or cwd
    with _lock:
        if key in _refreshing or len(_refreshing) >= MAX_REFRESH:
            return
        _refreshing.add(key)

    def run():
        try:
            found = top or top_of(cwd)
            if found:
                refresh(found)
        except Exception:  # a mark missing from one list, not a broken list
            pass
        finally:
            with _lock:
                _refreshing.discard(key)

    threading.Thread(target=run, name="git-who-list", daemon=True).start()


def add_changes(rows: list[dict], now: float | None = None) -> list[dict]:
    """`changes` on every row: `{"files", "commits", "at"}` for a thread
    holding changes in its folder's checkout, else None (§6.1). Never runs
    git or reads a transcript in the caller's time: what is known is served,
    and a folder whose checkout or chats are unknown or stale is read behind
    the answer, for the next list."""
    now = time.time() if now is None else now
    tops: dict[str, str] = {}
    later: list[tuple[str, str | None]] = []
    for row in rows:
        cwd = str(row.get("cwd") or "")
        if not cwd or cwd in tops:
            continue
        top = _cached_top(cwd, now)
        if top is _UNKNOWN:
            if not any(c == cwd for c, _ in later):
                later.append((cwd, None))
        elif top:
            tops[cwd] = top
    with _lock:
        held = {top: _snapshots.get(top) for top in set(tops.values())}
    for top, got in held.items():
        if got is None or now - got[0] >= SNAPSHOT_S:
            later.append(("", top))
    for row in rows:
        top = tops.get(str(row.get("cwd") or ""))
        got = held.get(top) if top else None
        row["changes"] = (got[1].get(str(row.get("session") or "")) if got else None) or None
    for cwd, top in later:
        _read_soon(cwd, top)
    return rows
