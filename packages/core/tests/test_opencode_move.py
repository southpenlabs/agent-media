"""Moving an opencode chat (opencode_move): exported, its folder and project
changed, imported under the same id. A stand-in for the opencode program does
export and import on the test's database as opencode 1.19.5 does."""

import json
import os
import sqlite3
import stat
import subprocess
import sys

import pytest

from agent_media_core import opencode_move

from test_harnesses_opencode import OC, add, make_db

FAKE = '''#!{py}
import json, sqlite3, sys
db = sqlite3.connect({db!r})
if sys.argv[1] == "export":
    sid, d, t = db.execute("select id, directory, title from session where id = ?", (sys.argv[2],)).fetchone()
    print("Exporting session: " + sid)
    print(json.dumps({{"info": {{"id": sid, "directory": d, "title": t, "projectID": "old"}}, "messages": []}}))
elif sys.argv[1] == "import":
    info = json.load(open(sys.argv[2]))["info"]
    db.execute("update session set directory = ? where id = ?", (info["directory"], info["id"]))
    db.commit()
    open({seen!r}, "w").write(json.dumps(info))
    print("Imported session: " + info["id"])
'''


@pytest.fixture
def oc(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    db = make_db(tmp_path / "data" / "opencode" / "opencode.db")
    add(db, OC, directory=str(tmp_path / "a"))
    exe = tmp_path / "opencode"
    exe.write_text(FAKE.format(py=sys.executable, db=str(db), seen=str(tmp_path / "seen.json")))
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    (tmp_path / "b").mkdir()
    return tmp_path, str(exe)


def test_a_chat_is_filed_under_the_new_folder_with_its_id(oc):
    tmp, exe = oc
    assert opencode_move.move(OC, str(tmp / "b"), program=exe) == ""
    seen = json.loads((tmp / "seen.json").read_text())
    assert seen["id"] == OC and seen["directory"] == str(tmp / "b")
    assert seen["projectID"] == "global"           # no git there
    # A second ask, or one sessiond already made, does nothing.
    (tmp / "seen.json").unlink()
    assert opencode_move.move(OC, str(tmp / "b"), program=exe) == ""
    assert not (tmp / "seen.json").exists()


def test_a_git_folder_gets_its_root_commit_as_its_project(oc):
    tmp, exe = oc
    b = tmp / "b"
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "init", "-q", str(b)], check=True)
    subprocess.run(["git", "-C", str(b), "commit", "-q", "--allow-empty", "-m", "x"], check=True, env=env)
    root = subprocess.run(["git", "-C", str(b), "rev-list", "--max-parents=0", "HEAD"],
                          capture_output=True, text=True).stdout.strip()
    assert opencode_move.project_id(str(b)) == root


def test_refusals_say_why(oc):
    tmp, exe = oc
    assert "not an opencode session" in opencode_move.move("not-one", str(tmp / "b"), program=exe)
    assert "no such directory" in opencode_move.move(OC, str(tmp / "nowhere"), program=exe)
    assert "export" in opencode_move.move(OC, str(tmp / "b"), program=str(tmp / "missing"))
