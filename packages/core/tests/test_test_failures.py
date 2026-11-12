"""A failing test told to the thread whose commit broke it (test_failures.py)."""

from __future__ import annotations

import subprocess

import pytest

from agent_media_core import test_failures as T

THREAD = "45628bf4-a304-47e8-bbc2-83ebd1ceadff"


@pytest.fixture
def repo(tmp_path):
    d = tmp_path / "r"
    d.mkdir()
    def git(*a):
        subprocess.run(["git", "-C", str(d), *a], check=True, capture_output=True)
    git("init", "-q")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    (d / "f").write_text("1")
    git("add", "f")
    git("commit", "-qm", f"speech: keep-reading\n\nbody\n\nThread: {THREAD}")
    (d / "f").write_text("2")
    git("commit", "-qam", "no thread here")
    return str(d)


def test_the_commits_thread_is_told_once(repo):
    sent = []
    send = lambda thread, sender, text: (sent.append((thread, sender, text)), (True, ""))[1]  # noqa: E731
    r = T.notify("tests/test_x.py::test_y", "HEAD~1", why="keep-reading missing from ctl_argv",
                 sender="agent-media", repo=repo, send=send)
    assert r == {"sent": True, "thread": THREAD}
    thread, sender, text = sent[0]
    assert thread == THREAD and sender == "agent-media"
    assert "tests/test_x.py::test_y" in text and "keep-reading missing" in text and "speech: keep-reading" in text
    again = T.notify("tests/test_x.py::test_y", "HEAD~1", sender="other", repo=repo, send=send)
    assert again["sent"] is False and again["reason"] == "already told" and len(sent) == 1


def test_a_commit_without_a_thread_says_so(repo):
    r = T.notify("t", "HEAD", repo=repo, send=lambda *a: (True, ""))
    assert r["sent"] is False and r["thread"] is None and "no Thread" in r["reason"]


def test_a_refused_send_is_not_kept(repo):
    r = T.notify("t", "HEAD~1", repo=repo, send=lambda *a: (False, "host down"))
    assert r == {"sent": False, "thread": THREAD, "reason": "host down"}
    assert T.notify("t", "HEAD~1", repo=repo, send=lambda *a: (True, ""))["sent"] is True


# --- the form commits are actually written in -------------------------------


def test_thread_line_is_read_as_a_link_which_is_how_commits_write_it():
    """Every commit in these repos writes its thread as a link, not a bare id.
    The pattern demanded a bare id and so matched none of them: a red test
    could not be routed to the session that caused it even when the line was
    there. Found 13 Nov 2026, when a backup test's failure could not be routed
    and "no Thread: line" turned out not to be the whole story.
    """
    link = ("speech: a change\n\nbody\n\n"
            "Thread: https://red5.sasonica.com/app/t/45628bf4-a304-47e8-bbc2-83ebd1ceadff\n")
    m = T._THREAD.search(link)
    assert m and m.group(1) == "45628bf4-a304-47e8-bbc2-83ebd1ceadff"


def test_thread_line_still_reads_a_bare_id():
    bare = f"x\n\nThread: {THREAD}\n"
    m = T._THREAD.search(bare)
    assert m and m.group(1) == THREAD


def test_a_line_that_merely_starts_with_thread_is_not_one():
    assert T._THREAD.search("Threaded: 45628bf4-a304-47e8-bbc2-83ebd1ceadff\n") is None
    assert T._THREAD.search("Thread: none yet\n") is None
