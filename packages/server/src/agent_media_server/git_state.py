"""A thread's git branch and how many files are changed, for the status line
under its input box (David, 1 Nov 2026: "Git branch and changes").

`git_for(cwd)` runs one `git status --porcelain=v1 --branch` in the thread's
folder and answers `{"branch", "changed", "ahead", "behind"}`, or None when
the folder is not in a git checkout (or git is missing or slow). `branch` is
None on a detached HEAD. The answer is kept for CACHE_S per folder, so a
thread polled every few seconds costs one git call in that time, and a call
is cut off after TIMEOUT_S so a slow disk never holds up the thread's log.

Read-only: `--no-optional-locks` keeps git from refreshing the index, so it
never takes the lock a session's own `git commit` needs.
"""

from __future__ import annotations

import os
import re
import subprocess
import threading
import time

CACHE_S = 15.0
TIMEOUT_S = 2.0

_lock = threading.Lock()
_cache: dict[str, tuple[float, dict | None]] = {}

_AHEAD = re.compile(r"ahead (\d+)")
_BEHIND = re.compile(r"behind (\d+)")


def parse(out: str) -> dict:
    """`git status --porcelain=v1 --branch` output → the status dict."""
    lines = out.splitlines()
    branch = None
    ahead = behind = 0
    if lines and lines[0].startswith("## "):
        head = lines.pop(0)[3:]
        if head.startswith("No commits yet on "):
            branch = head[len("No commits yet on "):].strip() or None
        elif not head.startswith("HEAD (no branch)"):
            name = head.split("...", 1)[0].split(" [", 1)[0].strip()
            branch = name or None
        if (m := _AHEAD.search(head)):
            ahead = int(m.group(1))
        if (m := _BEHIND.search(head)):
            behind = int(m.group(1))
    changed = sum(1 for l in lines if l.strip())
    return {"branch": branch, "changed": changed, "ahead": ahead, "behind": behind}


def _read(cwd: str) -> dict | None:
    try:
        p = subprocess.run(
            ["git", "--no-optional-locks", "-C", cwd, "status", "--porcelain=v1", "--branch"],
            capture_output=True, text=True, timeout=TIMEOUT_S,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"},
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if p.returncode != 0:
        return None
    return parse(p.stdout)


def git_for(cwd: str | None, now: float | None = None) -> dict | None:
    """The folder's git status (cached CACHE_S), or None outside a checkout."""
    if not cwd or not os.path.isdir(cwd):
        return None
    now = time.monotonic() if now is None else now
    with _lock:
        hit = _cache.get(cwd)
        if hit is not None and now - hit[0] < CACHE_S:
            return hit[1]
    val = _read(cwd)
    with _lock:
        _cache[cwd] = (now, val)
    return val


# ── The changes behind the line: GET /session/git (3 Nov 2026) ────────────
#
# David, 3 Nov 2026: "Can we make that Git status line clickable? maybe we
# could see diffs from it. sort of like what Paseo does" — read-only. The
# files `git status` lists with their +/- counts against HEAD, the branch's
# recent history, a commit's files, and one file's diff. A diff is only ever
# asked for a path the status (or that commit) lists, and a commit only on
# the branch's own history, so the app cannot read anything else.
#
# Later the same day: "Open on GitHub ... let's make it work for some other
# popular git repositories too... including forgejo": `remote` gives the
# web address of the folder's remote and its kind, so the app can link a
# commit, the branch, or the branch against the default one. Hosts are
# known by name; a self-hosted one (a Forgejo, a GitLab) is named in
# MEDIA_GIT_FORGES ("git.example.org=forgejo,code.example.com=gitlab").

MAX_FILES = 300
MAX_COMMITS = 50
MAX_HISTORY = 30
MAX_DIFF = 200_000
_EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
_SHA = re.compile(r"[0-9a-f]{4,40}")

FORGES = {
    "github.com": "github",
    "gitlab.com": "gitlab",
    "bitbucket.org": "bitbucket",
    "codeberg.org": "forgejo",
    "gitea.com": "gitea",
    "git.sr.ht": "sourcehut",
}
KINDS = ("github", "gitlab", "bitbucket", "forgejo", "gitea", "sourcehut")


def _git(cwd: str, *args: str, ok: tuple[int, ...] = (0,)) -> str | None:
    try:
        p = subprocess.run(
            ["git", "--no-optional-locks", "-C", cwd, *args],
            capture_output=True, timeout=TIMEOUT_S * 2,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"},
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if p.returncode not in ok:
        return None
    return p.stdout.decode("utf-8", "replace")


# ── Remotes on the web ─────────────────────────────────────────────────────

def _forges() -> dict[str, str]:
    out = dict(FORGES)
    for pair in os.environ.get("MEDIA_GIT_FORGES", "").split(","):
        host, _, kind = pair.strip().partition("=")
        if host and kind.strip().lower() in KINDS:
            out[host.strip().lower()] = kind.strip().lower()
    return out


def web_of(url: str) -> dict | None:
    """A remote URL → {"kind", "host", "web"} for a known forge, else None.

    https://host[:port]/owner/repo(.git), ssh://git@host[:port]/owner/repo.git
    and git@host:owner/repo.git all give https://host/owner/repo (an ssh
    port is not the web one, so it goes; an https one stays)."""
    url = (url or "").strip()
    m = re.fullmatch(r"(?:(https?)://|ssh://)(?:[^@/]+@)?([^/:]+)(?::(\d+))?/(.+?)(?:\.git)?/?", url)
    if m:
        scheme, host, port, path = m.groups()
        hostport = f"{host}:{port}" if port and scheme else host
    else:
        m = re.fullmatch(r"(?:[^@/]+@)?([^/:]+):(?!//)(.+?)(?:\.git)?/?", url)
        if not m:
            return None
        host, path = m.groups()
        hostport = host
    host = host.lower()
    kind = _forges().get(host) or _forges().get(hostport.lower())
    if not kind or not path or ".." in path.split("/"):
        return None
    return {"kind": kind, "host": host, "web": f"https://{hostport}/{path}"}


def commit_url(remote: dict | None, sha: str) -> str | None:
    if not remote:
        return None
    sep = {"gitlab": "/-/commit/", "bitbucket": "/commits/"}.get(remote["kind"], "/commit/")
    return remote["web"] + sep + sha


def branch_url(remote: dict | None, branch: str) -> str | None:
    if not remote or not branch:
        return None
    sep = {"gitlab": "/-/tree/", "bitbucket": "/src/", "forgejo": "/src/branch/",
           "gitea": "/src/branch/"}.get(remote["kind"], "/tree/")
    return remote["web"] + sep + branch


def compare_url(remote: dict | None, base: str, head: str) -> str | None:
    """The branch against the default one; sourcehut has no such page."""
    if not remote or not base or not head or base == head:
        return None
    kind, web = remote["kind"], remote["web"]
    if kind == "sourcehut":
        return None
    if kind == "bitbucket":
        return f"{web}/branches/compare/{head}%0D{base}"
    return f"{web}{'/-' if kind == 'gitlab' else ''}/compare/{base}...{head}"


def _remote(cwd: str) -> tuple[dict | None, str, str]:
    """The folder's remote on the web, the branch's name there, and the
    remote's default branch ("" when not known)."""
    up = (_git(cwd, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}") or "").strip()
    name, _, head = up.partition("/") if up else ("origin", "", "")
    url = (_git(cwd, "remote", "get-url", name) or "").strip()
    default = (_git(cwd, "symbolic-ref", "--short", f"refs/remotes/{name}/HEAD") or "").strip()
    default = default.partition("/")[2] if default else ""
    return web_of(url), head, default


# ── The working tree ───────────────────────────────────────────────────────

def _status_entries(cwd: str) -> list[dict] | None:
    """`git status -z` → [{"path", "from"?, "status"}]; None outside a checkout."""
    out = _git(cwd, "status", "--porcelain=v1", "-z")
    if out is None:
        return None
    parts = out.split("\0")
    entries: list[dict] = []
    i = 0
    while i < len(parts):
        rec = parts[i]
        i += 1
        if len(rec) < 4:
            continue
        xy, path = rec[:2], rec[3:]
        e = {"path": path, "status": xy.strip() or xy}
        if "R" in xy or "C" in xy:
            # -z puts the old name in the next record.
            e["from"] = parts[i] if i < len(parts) else ""
            i += 1
        entries.append(e)
    return entries


def _base(cwd: str) -> str:
    return "HEAD" if _git(cwd, "rev-parse", "--verify", "-q", "HEAD") is not None else _EMPTY_TREE


def _numstat(out: str) -> dict[str, tuple[int | None, int | None]]:
    """`git diff --numstat` → {path: (added, removed)}, (None, None) for binary;
    a rename under its new name."""
    counts: dict[str, tuple[int | None, int | None]] = {}
    for line in out.splitlines():
        bits = line.split("\t")
        if len(bits) < 3:
            continue
        name = bits[2]
        if " => " in name:
            # "a/{old => new}/b" or "old => new".
            if "{" in name:
                pre, rest = name.split("{", 1)
                mid, post = rest.split("}", 1)
                name = (pre + mid.split(" => ", 1)[1] + post).replace("//", "/")
            else:
                name = name.split(" => ", 1)[1]
        add, rem = bits[0], bits[1]
        counts[name] = (None, None) if add == "-" else (int(add), int(rem))
    return counts


def _with_counts(f: dict, counts: dict) -> dict:
    if f["path"] in counts:
        add, rem = counts[f["path"]]
        if add is None:
            f["binary"] = True
        else:
            f["added"], f["removed"] = add, rem
    return f


def _unpushed(cwd: str) -> set[str] | None:
    """Commits on HEAD that no remote branch has; None with no remote at all
    (nothing to push to, so nothing is "not pushed")."""
    if not (_git(cwd, "remote") or "").strip():
        return None
    return set((_git(cwd, "rev-list", f"--max-count={MAX_HISTORY * 4}", "HEAD", "--not", "--remotes") or "").split())


def _history(cwd: str, remote: dict | None) -> list[dict]:
    out = _git(cwd, "log", f"--max-count={MAX_HISTORY}", "--format=%H%x1f%h%x1f%an%x1f%at%x1f%s%x1e") or ""
    unpushed = _unpushed(cwd)
    commits = []
    for rec in out.split("\x1e"):
        bits = rec.strip("\n").split("\x1f")
        if len(bits) < 5:
            continue
        full, sha, author, at, subject = bits
        pushed = None if unpushed is None else full not in unpushed
        commits.append({"sha": sha, "full": full, "subject": subject, "author": author,
                        "at": int(at or 0), "pushed": pushed,
                        "url": commit_url(remote, full) if pushed else None})
    return commits


def changes(cwd: str | None) -> dict | None:
    """The status line's files, unpushed commits, recent history and the
    remote's web links, or None outside a checkout."""
    if not cwd or not os.path.isdir(cwd):
        return None
    head = git_for(cwd)
    entries = _status_entries(cwd)
    if head is None or entries is None:
        return None
    counts = _numstat(_git(cwd, "diff", "--no-ext-diff", "--numstat", "-M", _base(cwd)) or "")
    files = []
    for e in entries[:MAX_FILES]:
        f = dict(e)
        if e["status"] == "??":
            f["untracked"] = True
        else:
            _with_counts(f, counts)
        files.append(f)
    remote, upstream, default = _remote(cwd)
    history = _history(cwd, remote)
    commits = [{"sha": c["sha"], "subject": c["subject"]} for c in history if c["pushed"] is False][:MAX_COMMITS]
    links = {"branch": branch_url(remote, upstream) if upstream else None,
             "compare": compare_url(remote, default, upstream) if upstream else None}
    return {**head, "files": files, "more": max(0, len(entries) - MAX_FILES), "commits": commits,
            "history": history, "remote": remote, "default_branch": default or None, "links": links}


def file_diff(cwd: str | None, path: str) -> dict | None:
    """One listed file's diff against HEAD (an untracked one as all new), or
    None when `path` is not among the changes."""
    if not cwd or not os.path.isdir(cwd) or not path:
        return None
    entries = _status_entries(cwd)
    if not entries:
        return None
    entry = next((e for e in entries if e["path"] == path), None)
    if entry is None:
        # A file inside an untracked folder git lists as "dir/".
        if ".." in path.split("/"):
            return None
        entry = next((e for e in entries if e["status"] == "??" and e["path"].endswith("/")
                      and path.startswith(e["path"])), None)
        if entry is None:
            return None
    if path.endswith("/"):
        return None
    top = (_git(cwd, "rev-parse", "--show-toplevel") or "").strip()
    if not top:
        return None
    if entry["status"] == "??":
        full = os.path.realpath(os.path.join(top, path))
        if not full.startswith(os.path.realpath(top) + os.sep) or not os.path.isfile(full):
            return None
        out = _git(top, "diff", "--no-ext-diff", "--no-color", "--no-index", "--", "/dev/null", path, ok=(0, 1))
    else:
        paths = [path] + ([entry["from"]] if entry.get("from") else [])
        out = _git(top, "diff", "--no-ext-diff", "--no-color", "-M", _base(top), "--", *paths)
    return _diff_answer(path, entry["status"], out)


def _diff_answer(path: str, status: str, out: str | None) -> dict | None:
    if out is None:
        return None
    binary = "Binary files " in out and "@@" not in out
    return {"path": path, "status": status, "diff": "" if binary else out[:MAX_DIFF],
            "binary": binary, "truncated": len(out) > MAX_DIFF}


# ── A commit on the branch ─────────────────────────────────────────────────

def _commit_pair(cwd: str, sha: str) -> tuple[str, str] | None:
    """(parent, full sha) for a commit on HEAD's own history, else None. A
    merge is shown against its first parent, a root commit as all new."""
    if not _SHA.fullmatch(sha or ""):
        return None
    full = (_git(cwd, "rev-parse", "--verify", "-q", f"{sha}^{{commit}}") or "").strip()
    if not full or _git(cwd, "merge-base", "--is-ancestor", full, "HEAD") is None:
        return None
    parent = (_git(cwd, "rev-parse", "--verify", "-q", f"{full}^1") or "").strip()
    return (parent or _EMPTY_TREE), full


def _commit_files(cwd: str, parent: str, full: str) -> list[dict]:
    counts = _numstat(_git(cwd, "diff", "--no-ext-diff", "--numstat", "-M", parent, full) or "")
    files = []
    for line in (_git(cwd, "diff", "--no-ext-diff", "--name-status", "-M", parent, full) or "").splitlines():
        bits = line.split("\t")
        if len(bits) < 2:
            continue
        status = bits[0][:1]
        f = {"path": bits[-1], "status": status}
        if status in "RC" and len(bits) >= 3:
            f["from"] = bits[1]
        files.append(_with_counts(f, counts))
    return files


def commit(cwd: str | None, sha: str) -> dict | None:
    """A commit on the branch: its message, author, time, files and link."""
    if not cwd or not os.path.isdir(cwd):
        return None
    pair = _commit_pair(cwd, sha)
    if pair is None:
        return None
    parent, full = pair
    meta = (_git(cwd, "log", "-1", "--format=%h%x1f%an%x1f%at%x1f%s%x1f%b", full) or "").split("\x1f", 4)
    if len(meta) < 5:
        return None
    short, author, at, subject, body = meta
    files = _commit_files(cwd, parent, full)
    remote = _remote(cwd)[0]
    unpushed = _unpushed(cwd)
    pushed = None if unpushed is None else full not in unpushed
    return {"sha": short, "full": full, "subject": subject, "body": body.strip(), "author": author,
            "at": int(at or 0), "pushed": pushed, "url": commit_url(remote, full) if pushed else None,
            "files": files[:MAX_FILES], "more": max(0, len(files) - MAX_FILES)}


def commit_diff(cwd: str | None, sha: str, path: str) -> dict | None:
    """One file's diff in a commit on the branch, or None when the commit
    is not on it or did not touch `path`."""
    if not cwd or not os.path.isdir(cwd) or not path:
        return None
    pair = _commit_pair(cwd, sha)
    if pair is None:
        return None
    parent, full = pair
    f = next((f for f in _commit_files(cwd, parent, full) if f["path"] == path), None)
    if f is None:
        return None
    # The paths are from the checkout's top, whichever folder the session ran in.
    top = (_git(cwd, "rev-parse", "--show-toplevel") or "").strip()
    if not top:
        return None
    paths = [path] + ([f["from"]] if f.get("from") else [])
    out = _git(top, "diff", "--no-ext-diff", "--no-color", "-M", parent, full, "--", *paths)
    return _diff_answer(path, f["status"], out)


# ── All files: the checkout's folders and any file in them (5 Nov 2026) ───
#
# David, 5 Nov 2026: a directory listing from the thread's header, then "It
# sounds like the git label does a lot of this anyway" — so the git sheet
# gains an All files tab. Read-only, and only what git would show: the files
# `git ls-files --cached --others --exclude-standard` lists (tracked, and
# untracked but not ignored), so .env files, build output and anything
# outside the checkout are never read. A folder is listed one level at a time.

MAX_ENTRIES = 1000
MAX_TEXT = 200_000
_SNIFF = 8000


def _top(cwd: str | None) -> str:
    if not cwd or not os.path.isdir(cwd):
        return ""
    return (_git(cwd, "rev-parse", "--show-toplevel") or "").strip()


def _clean(path: str) -> str | None:
    """A path from the checkout's top, no leading or trailing "/" ("" for the
    top); None for one that climbs out or names .git."""
    path = (path or "").strip().strip("/")
    if path in ("", "."):
        return ""
    bits = path.split("/")
    if any(b in ("", ".", "..") for b in bits) or bits[0] == ".git":
        return None
    return path


def _listed(top: str, under: str) -> list[str] | None:
    spec = [f":(literal){under}/"] if under else []
    out = _git(top, "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", *spec)
    if out is None:
        return None
    return sorted({p for p in out.split("\0") if p})


def _inside(top: str, path: str) -> str | None:
    """`path`'s real place on disk when it stays inside the checkout."""
    real_top = os.path.realpath(top)
    full = os.path.realpath(os.path.join(top, path))
    return full if full == real_top or full.startswith(real_top + os.sep) else None


def _status_of(top: str):
    """path → git's status letters, a file in an untracked folder as "??"."""
    status = {e["path"]: e["status"] for e in (_status_entries(top) or [])}
    loose = tuple(s for s, st in status.items() if st == "??" and s.endswith("/"))

    def of(path: str) -> str | None:
        return status.get(path) or ("??" if loose and path.startswith(loose) else None)
    return of


def _pushed_head(cwd: str) -> str:
    """HEAD's full sha when a remote branch has it, else "" (no link)."""
    full = (_git(cwd, "rev-parse", "--verify", "-q", "HEAD") or "").strip()
    unpushed = _unpushed(cwd)
    return full if full and unpushed is not None and full not in unpushed else ""


def blob_url(remote: dict | None, sha: str, path: str, folder: bool = False) -> str | None:
    """A file (or folder) at a commit on the forge's web pages."""
    if not remote or not sha:
        return None
    kind, web = remote["kind"], remote["web"]
    if kind == "gitlab":
        sep = "/-/tree/" if folder else "/-/blob/"
    elif kind == "bitbucket":
        sep = "/src/"
    elif kind in ("forgejo", "gitea"):
        sep = "/src/commit/"
    elif kind == "sourcehut":
        return f"{web}/tree/{sha}" + (f"/item/{path}" if path else "")
    else:
        sep = "/tree/" if folder else "/blob/"
    return f"{web}{sep}{sha}" + (f"/{path}" if path else "")


def tree(cwd: str | None, path: str = "") -> dict | None:
    """One folder of the checkout: its folders, then its files, each with
    what git status says of it. None outside a checkout or for a folder git
    lists nothing in."""
    top = _top(cwd)
    under = _clean(path)
    if not top or under is None:
        return None
    listed = _listed(top, under)
    if listed is None or (under and not listed):
        return None
    status = _status_of(top)
    cut = len(under) + 1 if under else 0
    folders: dict[str, int] = {}
    files: list[dict] = []
    for p in listed:
        rest = p[cut:]
        name, sep, _ = rest.partition("/")
        if sep:
            folders.setdefault(name, 0)
            if status(p):
                folders[name] += 1
            continue
        full = os.path.join(top, p)
        if os.path.isdir(full):
            # A submodule: a folder git does not list into.
            folders.setdefault(name, 0)
            continue
        try:
            size = os.lstat(full).st_size
        except OSError:
            continue  # deleted, not yet committed: the Changes tab has it
        f = {"name": name, "path": p, "size": size}
        if status(p):
            f["status"] = status(p)
        files.append(f)
    entries = [{"name": n, "path": f"{under}/{n}" if under else n, "folder": True,
                **({"changed": c} if c else {})}
               for n, c in sorted(folders.items(), key=lambda kv: kv[0].lower())]
    entries += sorted(files, key=lambda f: f["name"].lower())
    remote = _remote(top)[0]
    return {"tree": under, "entries": entries[:MAX_ENTRIES], "more": max(0, len(entries) - MAX_ENTRIES),
            "url": blob_url(remote, _pushed_head(top), under, folder=True)}


def blob(cwd: str | None, path: str) -> dict | None:
    """One file git lists in the checkout, as text (cut at MAX_TEXT), or
    marked binary. None for anything else."""
    top = _top(cwd)
    p = _clean(path)
    if not top or not p:
        return None
    if p not in (_listed(top, p.rpartition("/")[0]) or []):
        return None
    full = _inside(top, p)
    if not full or not os.path.isfile(full):
        return None
    try:
        size = os.path.getsize(full)
        with open(full, "rb") as fh:
            raw = fh.read(MAX_TEXT + 1)
    except OSError:
        return None
    binary = b"\0" in raw[:_SNIFF]
    text = "" if binary else raw[:MAX_TEXT].decode("utf-8", "replace")
    status = _status_of(top)(p)
    tracked = status != "??"
    remote = _remote(top)[0]
    return {"path": p, "size": size, "status": status, "text": text, "binary": binary,
            "truncated": not binary and len(raw) > MAX_TEXT,
            "url": blob_url(remote, _pushed_head(top), p) if tracked else None}
