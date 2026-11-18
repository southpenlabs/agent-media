"""The forge (forge.py, issues.py's forge half, /project/forge): checks on the
git line, main taking PRs only, a ticket's forge issue and its PR, and the
umbrella's open items. Agent mail #1064 from sasonica-app, 19 Nov 2026.

No network: forge._open is a fake forge that answers from a table, and
records what it was asked."""

from __future__ import annotations

import json
import subprocess
from urllib.parse import urlsplit

import pytest

from agent_media_server import auth, forge, git_state, issues, sessions, threads

SID = "6ec31fd1-b48e-4fc4-8701-1e177d1cacee"
API = "https://forge.example.com/api/v1"
WEB = "https://forge.example.com/sasonica/demo"


class Fake:
    """A forge: `routes[(method, path)]` → (status, body); every ask kept."""

    def __init__(self):
        self.routes: dict[tuple[str, str], tuple[int, object]] = {}
        self.asked: list[tuple[str, str, dict, object]] = []

    def __call__(self, method, url, headers, data, timeout):
        parts = urlsplit(url)
        path = f"{parts.scheme}://{parts.netloc}{parts.path}"
        self.asked.append((method, url, headers, json.loads(data) if data else None))
        if (method, path) not in self.routes:
            return 404, None
        return self.routes[(method, path)]

    def count(self, method, path):
        return sum(1 for m, u, _h, _d in self.asked if m == method and urlsplit(u).path.endswith(path))


@pytest.fixture
def fake(monkeypatch):
    f = Fake()
    monkeypatch.setattr(forge, "_open", f)
    forge.forget()
    return f


def _run(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A checkout on forge.example.com (a Forgejo): two commits, both pushed."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("MEDIA_GIT_FORGES", "forge.example.com=forgejo")
    git_state._cache.clear()
    r = tmp_path / "demo"
    r.mkdir()
    _run(r, "init", "-q", "-b", "main")
    for msg in ("start", f"the kettle sings\n\nThread: https://x.example/app/t/{SID}"):
        _run(r, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", msg)
    _run(r, "remote", "add", "origin", WEB + ".git")
    _run(r, "update-ref", "refs/remotes/origin/main", "HEAD")
    _run(r, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    monkeypatch.setattr(auth, "gate", lambda bearer: ("david", None))
    monkeypatch.setattr(sessions, "latest_cwd", lambda s: str(r))
    return r


def _token(tmp_path, monkeypatch, host="forge.example.com"):
    f = tmp_path / f"{host}.token"
    f.write_text("s3cret\n")
    monkeypatch.setenv("MEDIA_FORGE_TOKENS", f"{host}={f}")


def test_checks_on_the_git_line_and_main_by_pr(repo, fake):
    head, first = _run(repo, "rev-parse", "HEAD"), _run(repo, "rev-parse", "HEAD~1")
    fake.routes[("GET", f"{API}/repos/sasonica/demo/commits/{head}/status")] = (200, {
        "state": "failure", "statuses": [
            {"context": "typecheck", "status": "success", "target_url": f"{WEB}/actions/runs/41/jobs/1"},
            {"context": "e2e", "status": "failure", "target_url": f"{WEB}/actions/runs/41/jobs/0"}]})
    fake.routes[("GET", f"{API}/repos/sasonica/demo/commits/{first}/status")] = (200, {
        "state": "pending", "statuses": [{"context": "e2e", "status": "pending", "target_url": None}]})
    fake.routes[("GET", f"{API}/repos/sasonica/demo/branches/main")] = (200, {"protected": True})
    ok, got = threads.git_changes(SID, "t")
    assert ok and got["needs_pr"] is True
    top, older = got["history"]
    assert top["ci"] == {"state": "failure", "url": f"{WEB}/actions/runs/41/jobs/0", "checks": [
        {"name": "typecheck", "state": "success", "url": f"{WEB}/actions/runs/41/jobs/1"},
        {"name": "e2e", "state": "failure", "url": f"{WEB}/actions/runs/41/jobs/0"}]}
    assert older["ci"]["state"] == "pending"
    # A settled state is kept for good (forge-ci.json), pending only briefly.
    forge.forget()
    asked = len(fake.asked)
    ok, got = threads.git_changes(SID, "t")
    assert got["history"][0]["ci"]["state"] == "failure"
    assert fake.count("GET", f"{head}/status") == 1 and fake.count("GET", f"{first}/status") == 2
    assert len(fake.asked) == asked + 2          # the pending one and the branch again
    # The commit's own page lists them too.
    ok, one = threads.git_changes(SID, "t", commit=head[:12])
    assert ok and one["ci"]["checks"][1]["name"] == "e2e"


def test_a_forge_that_does_not_answer_leaves_them_out(repo, fake):
    ok, got = threads.git_changes(SID, "t")
    assert ok and got["needs_pr"] is False and all("ci" not in c for c in got["history"])


def test_an_unpushed_commit_and_another_forge_are_not_asked(repo, fake):
    _run(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "local")
    ok, got = threads.git_changes(SID, "t")
    assert got["history"][0]["pushed"] is False and "ci" not in got["history"][0]
    assert not any(git_state._git(str(repo), "rev-parse", "HEAD").strip() in u for _m, u, _h, _d in fake.asked)
    _run(repo, "remote", "set-url", "origin", "https://gitlab.com/o/r.git")
    forge.forget()
    fake.asked.clear()
    ok, got = threads.git_changes(SID, "t")
    assert got["needs_pr"] is False and fake.asked == []


def test_github_reads_check_runs_and_push_permission(tmp_path, monkeypatch, fake):
    _token(tmp_path, monkeypatch, "github.com")
    gh = forge.Repo("github", "github.com", "o", "r")
    sha = "a" * 40
    api = "https://api.github.com/repos/o/r"
    fake.routes[("GET", f"{api}/commits/{sha}/status")] = (200, {"state": "pending", "statuses": [], "total_count": 0})
    fake.routes[("GET", f"{api}/commits/{sha}/check-runs")] = (200, {"check_runs": [
        {"name": "test", "status": "completed", "conclusion": "success", "html_url": "https://github.com/o/r/runs/1"},
        {"name": "build", "status": "in_progress", "conclusion": None, "html_url": "https://github.com/o/r/runs/2"}]})
    assert forge.public_ci(forge.ci_for(gh, sha)) == {"state": "pending", "url": "https://github.com/o/r/runs/2", "checks": [
        {"name": "test", "state": "success", "url": "https://github.com/o/r/runs/1"},
        {"name": "build", "state": "pending", "url": "https://github.com/o/r/runs/2"}]}
    assert all(h.get("Authorization") == "Bearer s3cret" for _m, _u, h, _d in fake.asked)
    fake.routes[("GET", f"{api}/branches/main")] = (200, {"protected": False})
    fake.routes[("GET", api)] = (200, {"permissions": {"push": False}})
    assert forge.needs_pr(gh, "main") is True
    # Nothing at all on a commit: null, not a dot.
    fake.routes[("GET", f"{api}/commits/{'b' * 40}/check-runs")] = (200, {"check_runs": []})
    fake.routes[("GET", f"{api}/commits/{'b' * 40}/status")] = (200, {"statuses": []})
    assert forge.ci_for(gh, "b" * 40) is None


def test_tokens_come_from_files_and_gh(tmp_path, monkeypatch):
    _token(tmp_path, monkeypatch)
    assert forge.token("forge.example.com") == "s3cret"
    assert forge.token("other.example.com") == ""
    monkeypatch.setattr(forge, "_gh_token", lambda: "gh-token")
    assert forge.token("github.com") == "gh-token"


def test_thread_of_a_body():
    assert forge.thread_of(f"fixes it\n\nThread: https://h/app/t/{SID}") == SID
    assert forge.thread_of(f"see https://h/app/t/{SID.upper()} for why") == SID
    assert forge.thread_of("Thread: nothing here") is None and forge.thread_of(None) is None


def _issue_routes(fake):
    fake.routes[("POST", f"{API}/repos/sasonica/demo/issues")] = (201, {
        "number": 12, "html_url": f"{WEB}/issues/12", "state": "open"})


def test_opening_the_forge_issue_by_hand(repo, fake, tmp_path, monkeypatch, server, signed_in):
    from test_contract import AUTH, call

    monkeypatch.setenv("MEDIA_PUBLIC_URL", "https://red.example.com")
    monkeypatch.setattr(sessions, "session_exists", lambda s: s == SID)
    monkeypatch.setattr(sessions, "title_of", lambda s, **k: "The kettle")
    from agent_media_server import reap

    monkeypatch.setattr(reap, "first_ask", lambda s: "Make the kettle sing.")
    _issue_routes(fake)
    # No token: tracked, and offered nothing.
    res, obj = call(server, "POST", "/session/issue", {"session": SID, "state": "open"}, headers=AUTH)
    assert res.status == 200 and "can_forge" not in obj["issue"]
    res, obj = call(server, "POST", "/session/issue", {"session": SID, "state": "open", "forge": True}, headers=AUTH)
    assert res.status == 409 and fake.count("POST", "/issues") == 0
    _token(tmp_path, monkeypatch)
    issues._can.clear()
    assert issues.add_issues([{"session": SID}])[0]["issue"]["can_forge"] is True
    res, obj = call(server, "POST", "/session/issue", {"session": SID, "state": "open", "forge": True}, headers=AUTH)
    assert res.status == 200
    assert obj["issue"]["forge"] == {"repo": "sasonica/demo", "number": 12, "url": f"{WEB}/issues/12", "state": "open"}
    assert "can_forge" not in obj["issue"]
    sent = [d for m, _u, _h, d in fake.asked if m == "POST"][0]
    assert sent["title"] == "The kettle"
    assert sent["body"].startswith("Make the kettle sing.") and sent["body"].endswith(f"Thread: https://red.example.com/app/t/{SID}")
    # Never twice: again, done, untracked and tracked again, it is the same one.
    call(server, "POST", "/session/issue", {"session": SID, "state": "done", "forge": True}, headers=AUTH)
    call(server, "POST", "/session/issue", {"session": SID, "state": None}, headers=AUTH)
    res, obj = call(server, "POST", "/session/issue", {"session": SID, "state": "open"}, headers=AUTH)
    assert obj["issue"]["forge"]["number"] == 12 and fake.count("POST", "/issues") == 1


def test_the_sweep_opens_refreshes_and_closes(repo, fake, tmp_path, monkeypatch):
    from agent_media_server import reap

    monkeypatch.setattr(sessions, "title_of", lambda s, **k: "The kettle")
    monkeypatch.setattr(reap, "first_ask", lambda s: "")
    _token(tmp_path, monkeypatch)
    _issue_routes(fake)
    issues.set_issue(SID, "open")
    other = "aaaaaaaa-0000-4000-8000-000000000001"     # tracked, but no commit of its own
    issues.set_issue(other, "open")
    fake.routes[("GET", f"{API}/repos/sasonica/demo/issues/12")] = (200, {"state": "open", "html_url": f"{WEB}/issues/12"})
    fake.routes[("GET", f"{API}/repos/sasonica/demo/pulls")] = (200, [
        {"number": 13, "html_url": f"{WEB}/pulls/13", "state": "closed", "merged": False, "body": "unrelated", "head": {"sha": "c" * 40}},
        {"number": 14, "html_url": f"{WEB}/pulls/14", "state": "open", "merged": False, "draft": True,
         "body": "Fixes #12", "head": {"sha": "d" * 40}}])
    fake.routes[("GET", f"{API}/repos/sasonica/demo/commits/{'d' * 40}/status")] = (200, {
        "statuses": [{"context": "ci", "status": "pending", "target_url": f"{WEB}/actions/runs/8"}]})
    monkeypatch.setenv("MEDIA_FORGE_AUTO_ISSUE", "0")
    assert issues.sweep() == [] and fake.count("POST", "/issues") == 0
    monkeypatch.delenv("MEDIA_FORGE_AUTO_ISSUE")
    lines = issues.sweep()
    assert lines == [f"forge: {SID[:8]} opened {WEB}/issues/12"]
    got = issues.issues()
    assert "forge" not in got[other]
    assert got[SID]["forge"]["pr"] == {"number": 14, "url": f"{WEB}/pulls/14", "state": "open", "draft": True,
                                      "ci": {"state": "pending", "url": f"{WEB}/actions/runs/8"}}
    # Merged: the ticket is done; and a second pass opens nothing new.
    fake.routes[("GET", f"{API}/repos/sasonica/demo/pulls")] = (200, [
        {"number": 14, "html_url": f"{WEB}/pulls/14", "state": "closed", "merged": True,
         "body": f"Thread: https://h/app/t/{SID}", "head": {"sha": "d" * 40}}])
    assert issues.sweep() == [f"forge: {SID[:8]} done, {WEB}/pulls/14 merged"]
    assert issues.issues()[SID]["state"] == "done" and fake.count("POST", "/issues") == 1


def test_the_umbrella_view(fake, monkeypatch):
    monkeypatch.setenv("MEDIA_GIT_FORGES", "forge.example.com=forgejo")
    demo = forge.Repo("forgejo", "forge.example.com", "sasonica", "demo")
    web = "https://forge.example.com/sasonica"
    fake.routes[("GET", f"{API}/orgs/sasonica/repos")] = (200, [
        {"full_name": "sasonica/server", "html_url": f"{web}/server"},
        {"full_name": "sasonica/demo", "html_url": f"{web}/demo"},
        {"full_name": "sasonica/old", "html_url": f"{web}/old", "archived": True}])
    fake.routes[("GET", f"{API}/repos/issues/search")] = (200, [
        {"number": 3, "title": "the server forgets the kettle", "html_url": f"{web}/server/issues/3",
         "user": {"login": "david"}, "updated_at": "2026-11-19T08:00:00Z", "labels": [{"name": "bug"}],
         "repository": {"full_name": "sasonica/server"}, "body": "it forgets"},
        {"number": 14, "title": "the kettle sings in tune", "html_url": f"{web}/demo/pulls/14",
         "user": {"login": "sasonica-app"}, "updated_at": "2026-11-19T10:00:00+00:00", "labels": [],
         "repository": {"full_name": "sasonica/demo"}, "pull_request": {"draft": False},
         "body": f"Thread: https://h/app/t/{SID}"}])
    fake.routes[("GET", f"{API}/repos/sasonica/demo/pulls/14")] = (200, {"head": {"sha": "e" * 40}})
    fake.routes[("GET", f"{API}/repos/sasonica/demo/commits/{'e' * 40}/status")] = (200, {
        "statuses": [{"context": "ci", "status": "success", "target_url": f"{web}/demo/actions/runs/9"}]})
    got = forge.project_view(demo)
    assert got["repo"] == {"name": "sasonica/demo", "web": f"{web}/demo", "kind": "forgejo"}
    assert got["umbrella"] == {"name": "sasonica", "web": web, "repos": [
        {"name": "sasonica/demo", "web": f"{web}/demo"}, {"name": "sasonica/server", "web": f"{web}/server"}]}
    assert got["new_issue"] == f"{web}/demo/issues/new"
    pr, issue = got["items"]
    assert pr == {"kind": "pr", "repo": "sasonica/demo", "number": 14, "title": "the kettle sings in tune",
                  "url": f"{web}/demo/pulls/14", "author": "sasonica-app", "at": 1795082400, "labels": [],
                  "draft": False, "thread": SID, "ci": {"state": "success", "url": f"{web}/demo/actions/runs/9"}}
    assert issue["kind"] == "issue" and issue["labels"] == ["bug"] and issue["thread"] is None
    asked = len(fake.asked)
    assert forge.project_view(demo) is got and len(fake.asked) == asked     # kept VIEW_S


from test_contract import server, signed_in, typed  # noqa: E402,F401  (fixtures)


def test_the_project_pages_last_commit_has_its_checks(repo, fake):
    from agent_media_server import project_page

    head = _run(repo, "rev-parse", "HEAD")
    fake.routes[("GET", f"{API}/repos/sasonica/demo/commits/{head}/status")] = (200, {
        "statuses": [{"context": "ci", "status": "success", "target_url": f"{WEB}/actions/runs/7"}]})
    last = project_page.git_summary(str(repo))["last"]
    assert last["sha"] == head[:len(last["sha"])] and last["ci"]["state"] == "success"
