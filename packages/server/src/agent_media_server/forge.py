"""The folder's forge: checks on a commit, whether main takes pull requests
only, issues and pull requests, and the open ones across an umbrella.

Agent mail #1064 from sasonica-app, 19 Nov 2026 (David: "somewhat
automated"; docs/proposals/2026-11-19-forgejo-workflow.md §4, the app's side
in sasonica-app acd0dbb8). One adapter, one interface (`Backend`), two
backends: Forgejo and Gitea (the same API, `/api/v1`) and GitHub (REST,
api.github.com, or `/api/v3` on an Enterprise host). GitLab, Bitbucket and
sourcehut are known as forges (git_state.web_of) but have no backend yet, so
every answer for them is None.

Which host is which kind is git_state's (`MEDIA_GIT_FORGES` for a self-hosted
one). Reads of a public repo need no token; a private one, and every write,
do. `MEDIA_FORGE_TOKENS="host=/path/to/token-file,host2=/path"` names a file
holding each host's token (never the token itself); for github.com, with no
entry, the `gh` login's (`gh auth token`, read once and kept in memory,
never logged).

Every call is stdlib urllib with a short timeout (TIMEOUT_S), and any failure
is "not known": the caller leaves the field out or null, never an error on the
git line. What is cached:

- a commit's checks once settled (success, failure, error) for good, in
  `<state_dir>/forge-ci.json`; pending, none or a failed lookup for a short
  while in memory;
- whether a branch needs a pull request, for NEEDS_PR_S in memory;
- the umbrella's open issues and pull requests, for VIEW_S in memory.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, TypedDict

from . import git_state
from ._jsonmap import JsonMap
from agent_media_core import failures as _failures

TIMEOUT_S = 2.0
#: How long a request waits on the forge before it answers without.
WAIT_S = 2.5
PENDING_S = 30.0
NONE_S = 60.0
FAILED_S = 30.0
NEEDS_PR_S = 600.0
VIEW_S = 60.0
REPO_S = 300.0
GH_S = 3600.0
#: A /session/git answer looks up this many uncached pushed commits itself,
#: and queues this many more for the next one.
SYNC_COMMITS = 3
QUEUE_COMMITS = 10
MAX_ITEMS = 50
MAX_ITEM_CI = 8
MAX_SETTLED = 3000

KINDS = ("forgejo", "gitea", "github")
SETTLED = ("success", "failure", "error")

CiState = Literal["pending", "success", "failure", "error"]


class Check(TypedDict):
    name: str
    state: CiState
    url: str | None


class _CiBase(TypedDict):
    state: CiState
    url: str | None


class Ci(_CiBase, total=False):
    checks: list[Check]


class ForgeError(Exception):
    """The forge could not be asked, or said no."""


_SESSION = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_THREAD_LINE = re.compile(rf"^\s*Thread:.*?({_SESSION})", re.M | re.I)
_THREAD_LINK = re.compile(rf"/app/t/({_SESSION})", re.I)


def thread_of(text: str | None) -> str | None:
    """The chat a body names: its `Thread:` line, else an `/app/t/<id>` link."""
    text = text or ""
    m = _THREAD_LINE.search(text) or _THREAD_LINK.search(text)
    return m.group(1).lower() if m else None


# ── The repo ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Repo:
    kind: str       # forgejo | gitea | github
    host: str       # host[:port], as on the web
    owner: str
    name: str

    @property
    def full(self) -> str:
        return f"{self.owner}/{self.name}"

    @property
    def web(self) -> str:
        return f"https://{self.host}/{self.full}"

    @property
    def key(self) -> str:
        return f"{self.host}/{self.full}"

    @property
    def api(self) -> str:
        if self.kind == "github":
            return "https://api.github.com" if self.host == "github.com" else f"https://{self.host}/api/v3"
        return f"https://{self.host}/api/v1"


def repo_of(remote: dict | None) -> Repo | None:
    """git_state's `{"kind", "host", "web"}` → a Repo, for a forge with a backend."""
    if not remote or remote.get("kind") not in KINDS:
        return None
    m = re.fullmatch(r"https://([^/]+)/([^/]+)/([^/]+)", str(remote.get("web") or ""))
    if not m:
        return None
    host, owner, name = m.groups()
    return Repo(str(remote["kind"]), host.lower(), owner, name)


def repo_from_url(url: str) -> Repo | None:
    """An issue's or pull request's page → its repo."""
    m = re.match(r"https://([^/]+)/([^/]+)/([^/]+)/", url or "")
    if not m:
        return None
    host, owner, name = m.groups()
    forges = git_state._forges()
    kind = forges.get(host.lower()) or forges.get(host.split(":")[0].lower())
    return repo_of({"kind": kind, "web": f"https://{host}/{owner}/{name}"}) if kind else None


_repo_cache: dict[str, tuple[float, Repo | None]] = {}
_repo_lock = threading.Lock()


def repo_for(cwd: str | None) -> Repo | None:
    """The folder's repo on a forge with a backend (git only, no network),
    kept REPO_S per folder."""
    if not cwd or not os.path.isdir(cwd):
        return None
    now = time.monotonic()
    with _repo_lock:
        hit = _repo_cache.get(cwd)
        if hit is not None and now - hit[0] < REPO_S:
            return hit[1]
    got = repo_of(git_state._remote(cwd)[0])
    with _repo_lock:
        _repo_cache[cwd] = (now, got)
    return got


# ── Tokens ─────────────────────────────────────────────────────────────────

_gh: dict[str, Any] = {"at": None, "token": ""}
_gh_lock = threading.Lock()


def _gh_token() -> str:
    """The `gh` login's token for github.com, "" without one."""
    now = time.monotonic()
    with _gh_lock:
        if _gh["at"] is not None and now - _gh["at"] < GH_S:
            return str(_gh["token"])
    token = ""
    if shutil.which("gh"):
        try:
            p = subprocess.run(["gh", "auth", "token", "--hostname", "github.com"],
                               capture_output=True, text=True, timeout=3)
            if p.returncode == 0:
                token = p.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            token = ""
    with _gh_lock:
        _gh["at"], _gh["token"] = now, token
    return token


def _token_files() -> dict[str, str]:
    out = {}
    for pair in os.environ.get("MEDIA_FORGE_TOKENS", "").split(","):
        host, _, path = pair.strip().partition("=")
        if host.strip() and path.strip():
            out[host.strip().lower()] = os.path.expanduser(path.strip())
    return out


def token(host: str) -> str:
    """The host's token: its MEDIA_FORGE_TOKENS file, else (github.com) gh's."""
    files = _token_files()
    path = files.get(host.lower()) or files.get(host.split(":")[0].lower())
    if path:
        try:
            with open(path, encoding="utf-8") as fh:
                return (fh.readline() or "").strip()
        except OSError:
            return ""
    if host.lower() == "github.com":
        return _gh_token()
    return ""


def can_write(repo: Repo | None) -> bool:
    return repo is not None and backend(repo) is not None and bool(token(repo.host))


# ── HTTP ───────────────────────────────────────────────────────────────────

def _open(method: str, url: str, headers: dict[str, str], data: bytes | None,
          timeout: float) -> tuple[int, Any]:
    """One request → (status, parsed JSON or None). Tests replace this."""
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 — https only (Repo.api)
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        return e.code, None


def _call(repo: Repo, method: str, path: str, params: dict | None = None,
          body: dict | None = None, ok: tuple[int, ...] = (200,)) -> Any:
    url = repo.api + path + ("?" + urllib.parse.urlencode(params) if params else "")
    headers = {"Accept": "application/json", "User-Agent": "agent-media"}
    if repo.kind == "github":
        headers["Accept"] = "application/vnd.github+json"
        headers["X-GitHub-Api-Version"] = "2022-11-28"
    tok = token(repo.host)
    if tok:
        headers["Authorization"] = ("Bearer " if repo.kind == "github" else "token ") + tok
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    try:
        status, got = _open(method, url, headers, data, TIMEOUT_S)
    except Exception as e:  # noqa: BLE001 — any failure is "not known"
        raise ForgeError(f"{repo.host}: {type(e).__name__}") from None
    if status not in ok:
        raise ForgeError(f"{repo.host}: {method} {path} answered {status}")
    return got


def _epoch(stamp: Any) -> int:
    try:
        return int(datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).timestamp())
    except (TypeError, ValueError):
        return 0


def _combine(checks: list[Check]) -> Ci | None:
    """The checks → one state (a failure first, then an error, then pending)
    with the url of the run that decided it."""
    if not checks:
        return None
    for state in ("failure", "error", "pending"):
        hit = [c for c in checks if c["state"] == state]
        if hit:
            url = next((c["url"] for c in hit if c["url"]), None)
            return {"state": hit[0]["state"], "url": url, "checks": checks}
    return {"state": "success", "url": next((c["url"] for c in checks if c["url"]), None),
            "checks": checks}


# ── The backends ───────────────────────────────────────────────────────────

class Backend:
    """What a forge answers. Every method raises ForgeError when it cannot."""

    def ci(self, repo: Repo, sha: str) -> Ci | None:
        raise NotImplementedError

    def needs_pr(self, repo: Repo, branch: str) -> bool:
        raise NotImplementedError

    def create_issue(self, repo: Repo, title: str, body: str) -> dict:
        """→ {number, url, state}"""
        raise NotImplementedError

    def issue(self, repo: Repo, number: int) -> dict:
        """→ {number, url, state}"""
        raise NotImplementedError

    def pulls(self, repo: Repo) -> list[dict]:
        """The newest pull requests, any state: [{number, url, state, draft,
        title, body, head}]."""
        raise NotImplementedError

    def pull_head(self, repo: Repo, number: int) -> str:
        raise NotImplementedError

    def owner_repos(self, repo: Repo) -> list[dict]:
        """The owner's repos: [{name "owner/name", web}]."""
        raise NotImplementedError

    def open_items(self, repo: Repo) -> list[dict]:
        """The owner's open issues and pull requests, as /project/forge items
        (no `ci` yet)."""
        raise NotImplementedError


def _state(s: Any) -> CiState:
    s = str(s or "").lower()
    if s in ("success", "skipped"):
        return "success"
    if s in ("failure", "warning"):
        return "failure"
    if s == "error":
        return "error"
    return "pending"


def _pr_state(raw: dict, merged: bool) -> str:
    if merged:
        return "merged"
    return "open" if raw.get("state") == "open" else "closed"


class Forgejo(Backend):
    """Forgejo and Gitea, `/api/v1`."""

    def ci(self, repo: Repo, sha: str) -> Ci | None:
        got = _call(repo, "GET", f"/repos/{repo.full}/commits/{sha}/status") or {}
        seen: set[str] = set()
        checks: list[Check] = []
        for s in got.get("statuses") or []:
            name = str(s.get("context") or "")
            if name in seen:
                continue
            seen.add(name)
            checks.append({"name": name, "state": _state(s.get("status") or s.get("state")),
                           "url": s.get("target_url") or None})
        return _combine(checks)

    def needs_pr(self, repo: Repo, branch: str) -> bool:
        b = _call(repo, "GET", f"/repos/{repo.full}/branches/{urllib.parse.quote(branch, safe='')}") or {}
        if b.get("protected"):
            return True
        # user_can_push speaks for the token; without one it is always false.
        return bool(token(repo.host)) and b.get("user_can_push") is False

    def create_issue(self, repo: Repo, title: str, body: str) -> dict:
        got = _call(repo, "POST", f"/repos/{repo.full}/issues", body={"title": title, "body": body},
                    ok=(200, 201)) or {}
        return {"number": int(got["number"]), "url": str(got["html_url"]),
                "state": "closed" if got.get("state") == "closed" else "open"}

    def issue(self, repo: Repo, number: int) -> dict:
        got = _call(repo, "GET", f"/repos/{repo.full}/issues/{number}") or {}
        return {"number": number, "url": str(got.get("html_url") or ""),
                "state": "closed" if got.get("state") == "closed" else "open"}

    def pulls(self, repo: Repo) -> list[dict]:
        got = _call(repo, "GET", f"/repos/{repo.full}/pulls",
                    {"state": "all", "sort": "newest", "limit": 50}) or []
        return [{"number": int(p["number"]), "url": str(p.get("html_url") or ""),
                 "state": _pr_state(p, bool(p.get("merged"))), "draft": bool(p.get("draft")),
                 "title": str(p.get("title") or ""), "body": str(p.get("body") or ""),
                 "head": str((p.get("head") or {}).get("sha") or "")} for p in got]

    def pull_head(self, repo: Repo, number: int) -> str:
        got = _call(repo, "GET", f"/repos/{repo.full}/pulls/{number}") or {}
        return str((got.get("head") or {}).get("sha") or "")

    def owner_repos(self, repo: Repo) -> list[dict]:
        try:
            got = _call(repo, "GET", f"/orgs/{repo.owner}/repos", {"limit": 50})
        except ForgeError:
            got = _call(repo, "GET", f"/users/{repo.owner}/repos", {"limit": 50})
        return [{"name": str(r.get("full_name")), "web": str(r.get("html_url"))}
                for r in got or [] if not r.get("archived")]

    def open_items(self, repo: Repo) -> list[dict]:
        got = _call(repo, "GET", "/repos/issues/search",
                    {"owner": repo.owner, "state": "open", "limit": MAX_ITEMS}) or []
        out = []
        for i in got:
            pr = i.get("pull_request")
            item = {"kind": "pr" if pr else "issue",
                    "repo": str((i.get("repository") or {}).get("full_name") or repo.full),
                    "number": int(i["number"]), "title": str(i.get("title") or ""),
                    "url": str(i.get("html_url") or ""), "author": str((i.get("user") or {}).get("login") or ""),
                    "at": _epoch(i.get("updated_at")),
                    "labels": [str(lb.get("name")) for lb in i.get("labels") or []],
                    "thread": thread_of(i.get("body"))}
            if pr:
                item["draft"] = bool(pr.get("draft"))
            out.append(item)
        return out


def _gh_check(state: Any, conclusion: Any) -> CiState:
    if state != "completed":
        return "pending"
    c = str(conclusion or "")
    if c in ("success", "neutral", "skipped"):
        return "success"
    if c in ("failure", "timed_out"):
        return "failure"
    return "error"  # cancelled, action_required, stale


class GitHub(Backend):
    """GitHub's REST API: commit statuses and check runs (Actions reports
    through check runs)."""

    def ci(self, repo: Repo, sha: str) -> Ci | None:
        checks: list[Check] = []
        got = _call(repo, "GET", f"/repos/{repo.full}/commits/{sha}/status") or {}
        for s in got.get("statuses") or []:
            checks.append({"name": str(s.get("context") or ""), "state": _state(s.get("state")),
                           "url": s.get("target_url") or None})
        runs = _call(repo, "GET", f"/repos/{repo.full}/commits/{sha}/check-runs", {"per_page": 100}) or {}
        for r in runs.get("check_runs") or []:
            checks.append({"name": str(r.get("name") or ""),
                           "state": _gh_check(r.get("status"), r.get("conclusion")),
                           "url": r.get("html_url") or r.get("details_url") or None})
        return _combine(checks)

    def needs_pr(self, repo: Repo, branch: str) -> bool:
        b = _call(repo, "GET", f"/repos/{repo.full}/branches/{urllib.parse.quote(branch, safe='')}") or {}
        if b.get("protected"):
            return True
        if not token(repo.host):
            return False
        r = _call(repo, "GET", f"/repos/{repo.full}") or {}
        return (r.get("permissions") or {}).get("push") is False

    def create_issue(self, repo: Repo, title: str, body: str) -> dict:
        got = _call(repo, "POST", f"/repos/{repo.full}/issues", body={"title": title, "body": body},
                    ok=(200, 201)) or {}
        return {"number": int(got["number"]), "url": str(got["html_url"]),
                "state": "closed" if got.get("state") == "closed" else "open"}

    def issue(self, repo: Repo, number: int) -> dict:
        got = _call(repo, "GET", f"/repos/{repo.full}/issues/{number}") or {}
        return {"number": number, "url": str(got.get("html_url") or ""),
                "state": "closed" if got.get("state") == "closed" else "open"}

    def pulls(self, repo: Repo) -> list[dict]:
        got = _call(repo, "GET", f"/repos/{repo.full}/pulls",
                    {"state": "all", "sort": "created", "direction": "desc", "per_page": 50}) or []
        return [{"number": int(p["number"]), "url": str(p.get("html_url") or ""),
                 "state": _pr_state(p, bool(p.get("merged_at"))), "draft": bool(p.get("draft")),
                 "title": str(p.get("title") or ""), "body": str(p.get("body") or ""),
                 "head": str((p.get("head") or {}).get("sha") or "")} for p in got]

    def pull_head(self, repo: Repo, number: int) -> str:
        got = _call(repo, "GET", f"/repos/{repo.full}/pulls/{number}") or {}
        return str((got.get("head") or {}).get("sha") or "")

    def _is_org(self, repo: Repo) -> bool:
        u = _call(repo, "GET", f"/users/{repo.owner}") or {}
        return u.get("type") == "Organization"

    def owner_repos(self, repo: Repo) -> list[dict]:
        where = "orgs" if self._is_org(repo) else "users"
        got = _call(repo, "GET", f"/{where}/{repo.owner}/repos", {"per_page": 100, "sort": "pushed"}) or []
        return [{"name": str(r.get("full_name")), "web": str(r.get("html_url"))}
                for r in got if not r.get("archived")]

    def open_items(self, repo: Repo) -> list[dict]:
        got = _call(repo, "GET", "/search/issues",
                    {"q": f"user:{repo.owner} is:open", "sort": "updated", "order": "desc",
                     "per_page": MAX_ITEMS}) or {}
        out = []
        for i in got.get("items") or []:
            name = "/".join(str(i.get("repository_url") or "").rstrip("/").split("/")[-2:])
            pr = "pull_request" in i
            item = {"kind": "pr" if pr else "issue", "repo": name or repo.full,
                    "number": int(i["number"]), "title": str(i.get("title") or ""),
                    "url": str(i.get("html_url") or ""), "author": str((i.get("user") or {}).get("login") or ""),
                    "at": _epoch(i.get("updated_at")),
                    "labels": [str(lb.get("name")) for lb in i.get("labels") or []],
                    "thread": thread_of(i.get("body"))}
            if pr:
                item["draft"] = bool(i.get("draft"))
            out.append(item)
        return out


_BACKENDS: dict[str, Backend] = {"forgejo": Forgejo(), "gitea": Forgejo(), "github": GitHub()}


def backend(repo: Repo | None) -> Backend | None:
    return _BACKENDS.get(repo.kind) if repo else None


# ── A commit's checks, cached ──────────────────────────────────────────────

STORE = JsonMap("forge-ci.json")
_UNKNOWN = object()
_mem: dict[str, tuple[float, float, Any]] = {}   # key → (at, ttl, value)
_mem_lock = threading.Lock()
_inflight: dict[str, Future] = {}
_pool = ThreadPoolExecutor(max_workers=6, thread_name_prefix="forge")


def _ci_key(repo: Repo, sha: str) -> str:
    return f"{repo.key}@{sha}"


def cached_ci(repo: Repo, sha: str) -> Any:
    """A commit's checks if known (a Ci, or None for none), else _UNKNOWN."""
    key = _ci_key(repo, sha)
    with _mem_lock:
        hit = _mem.get(key)
        if hit is not None and time.monotonic() - hit[0] < hit[1]:
            return hit[2]
    row = STORE.get(key)
    if isinstance(row, dict) and row.get("state") in SETTLED:
        row.pop("at", None)
        return row
    return _UNKNOWN


def _remember(key: str, value: Any, ttl: float) -> None:
    with _mem_lock:
        _mem[key] = (time.monotonic(), ttl, value)
        if len(_mem) > 4000:
            for k in sorted(_mem, key=lambda k: _mem[k][0])[:1000]:
                _mem.pop(k, None)


def fetch_ci(repo: Repo, sha: str) -> Any:
    """Ask the forge now; cache the answer. → a Ci, None, or _UNKNOWN."""
    be = backend(repo)
    key = _ci_key(repo, sha)
    if be is None:
        return _UNKNOWN
    try:
        got = be.ci(repo, sha)
    except Exception as _exc:  # noqa: BLE001 — ForgeError, or an answer not shaped as expected
        _failures.swallowed(_exc)
        _remember(key, _UNKNOWN, FAILED_S)
        return _UNKNOWN
    if got is not None and got["state"] in SETTLED:
        row = {**got, "at": round(time.time())}

        def fn(rows: dict) -> bool:
            rows[key] = row
            if len(rows) > MAX_SETTLED:
                for k in sorted(rows, key=lambda k: (rows[k] or {}).get("at", 0))[:len(rows) - MAX_SETTLED]:
                    rows.pop(k, None)
            return True
        try:
            STORE.update(fn)
        except OSError:
            pass
        _remember(key, got, NEEDS_PR_S)
    else:
        _remember(key, got, PENDING_S if got else NONE_S)
    return got


def _submit(key: str, fn, *args) -> Future:
    with _mem_lock:
        fut = _inflight.get(key)
        if fut is not None and not fut.done():
            return fut
        fut = _pool.submit(fn, *args)
        _inflight[key] = fut
    fut.add_done_callback(lambda _f: _inflight.pop(key, None))
    return fut


def ci_for(repo: Repo, sha: str, wait_s: float = WAIT_S) -> Any:
    """Cached, else asked (waiting at most `wait_s`). A Ci, None or _UNKNOWN."""
    got = cached_ci(repo, sha)
    if got is not _UNKNOWN:
        return got
    fut = _submit("ci:" + _ci_key(repo, sha), fetch_ci, repo, sha)
    wait([fut], timeout=wait_s)
    return cached_ci(repo, sha)


def public_ci(got: Any, checks: bool = True) -> Ci | None:
    if not isinstance(got, dict):
        return None
    out: Ci = {"state": got["state"], "url": got.get("url")}
    if checks and got.get("checks"):
        out["checks"] = [dict(c) for c in got["checks"]]  # type: ignore[misc]
    return out


# ── Does the default branch take pull requests only ────────────────────────

def _needs_pr_now(repo: Repo, branch: str) -> bool:
    be = backend(repo)
    key = f"pr:{repo.key}#{branch}"
    try:
        got = bool(be.needs_pr(repo, branch)) if be else False
        _remember(key, got, NEEDS_PR_S)
    except Exception as _exc:  # noqa: BLE001 — not known
        _failures.swallowed(_exc)
        got = False
        _remember(key, got, FAILED_S * 2)
    return got


def _needs_pr_cached(repo: Repo, branch: str) -> bool | None:
    with _mem_lock:
        hit = _mem.get(f"pr:{repo.key}#{branch}")
        if hit is not None and time.monotonic() - hit[0] < hit[1]:
            return bool(hit[2])
    return None


def needs_pr(repo: Repo, branch: str, wait_s: float = WAIT_S) -> bool:
    """True when the forge protects `branch` or this host's token cannot
    push to it; False when not known."""
    if not branch:
        return False
    hit = _needs_pr_cached(repo, branch)
    if hit is not None:
        return hit
    fut = _submit(f"pr:{repo.key}#{branch}", _needs_pr_now, repo, branch)
    wait([fut], timeout=wait_s)
    return bool(fut.result()) if fut.done() else False


# ── GET /session/git and /project/info ─────────────────────────────────────

def add_to_changes(changes: dict, wait_s: float = WAIT_S) -> dict:
    """`ci` on each pushed commit of `history` (left out when not known) and
    `needs_pr` (False when not known), for §6.49."""
    changes["needs_pr"] = False
    repo = repo_of(changes.get("remote"))
    if repo is None:
        return changes
    pushed = [c for c in changes.get("history") or [] if c.get("pushed")]
    missing = [c for c in pushed if cached_ci(repo, c["full"]) is _UNKNOWN]
    futs = [_submit("ci:" + _ci_key(repo, c["full"]), fetch_ci, repo, c["full"])
            for c in missing[:SYNC_COMMITS]]
    for c in missing[SYNC_COMMITS:SYNC_COMMITS + QUEUE_COMMITS]:
        _submit("ci:" + _ci_key(repo, c["full"]), fetch_ci, repo, c["full"])
    branch = changes.get("default_branch") or ""
    if branch and _needs_pr_cached(repo, branch) is None:
        futs.append(_submit(f"pr:{repo.key}#{branch}", _needs_pr_now, repo, branch))
    wait(futs, timeout=wait_s)
    for c in pushed:
        got = cached_ci(repo, c["full"])
        if got is not _UNKNOWN:
            c["ci"] = public_ci(got)
    if branch:
        changes["needs_pr"] = bool(_needs_pr_cached(repo, branch))
    return changes


def commit_ci(cwd: str | None, full: str, pushed: bool | None, wait_s: float = WAIT_S) -> Any:
    """One commit's checks for a page (a Ci or None), _UNKNOWN when not known."""
    if not pushed or not full:
        return _UNKNOWN
    repo = repo_for(cwd)
    if repo is None:
        return _UNKNOWN
    got = ci_for(repo, full, wait_s)
    return public_ci(got) if got is not _UNKNOWN else _UNKNOWN


# ── GET /project/forge ─────────────────────────────────────────────────────

_views: dict[str, tuple[float, dict]] = {}


def _item_ci(repo: Repo, be: Backend, item: dict) -> None:
    owner, _, name = item["repo"].partition("/")
    there = Repo(repo.kind, repo.host, owner, name)
    try:
        head = be.pull_head(there, item["number"])
    except Exception as _exc:  # noqa: BLE001 — no dot, then
        _failures.swallowed(_exc)
        return
    if head:
        got = ci_for(there, head, wait_s=TIMEOUT_S)
        if got is not _UNKNOWN:
            item["ci"] = public_ci(got, checks=False)


def project_view(repo: Repo) -> dict:
    """`{repo, umbrella, items, new_issue}` for /project/forge (cached VIEW_S)."""
    now = time.monotonic()
    with _mem_lock:
        hit = _views.get(repo.key)
        if hit is not None and now - hit[0] < VIEW_S:
            return hit[1]
    be = backend(repo)
    out: dict = {"repo": {"name": repo.full, "web": repo.web, "kind": repo.kind},
                 "umbrella": None, "items": [], "new_issue": f"{repo.web}/issues/new"}
    if be is None:
        return out
    repos_f = _pool.submit(be.owner_repos, repo)
    items_f = _pool.submit(be.open_items, repo)
    wait([repos_f, items_f], timeout=WAIT_S)
    try:
        repos = repos_f.result(timeout=0) if repos_f.done() else []
    except Exception as _exc:  # noqa: BLE001 — not known
        _failures.swallowed(_exc)
        repos = []
    if len(repos) > 1:
        out["umbrella"] = {"name": repo.owner, "web": f"https://{repo.host}/{repo.owner}",
                           "repos": sorted(repos, key=lambda r: r["name"].lower())}
    try:
        items = items_f.result(timeout=0) if items_f.done() else []
    except Exception as _exc:  # noqa: BLE001 — not known
        _failures.swallowed(_exc)
        items = []
    items.sort(key=lambda i: -i["at"])
    items = items[:MAX_ITEMS]
    prs = [i for i in items if i["kind"] == "pr"][:MAX_ITEM_CI]
    wait([_pool.submit(_item_ci, repo, be, i) for i in prs], timeout=WAIT_S)
    out["items"] = items
    if repos_f.done() and items_f.done():
        with _mem_lock:
            _views[repo.key] = (now, out)
    return out


def forget() -> None:
    """Drop the in-memory caches (tests)."""
    with _mem_lock:
        _mem.clear()
        _views.clear()
    with _repo_lock:
        _repo_cache.clear()
    with _gh_lock:
        _gh["at"], _gh["token"] = None, ""
