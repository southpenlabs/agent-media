"""Project folders a device may reach, and what it may do there (folder_grants.py).

docs/proposals/2026-11-17-file-browser-and-folder-permissions.md, step 1.
Over real HTTP to an in-process canvas (the contract test's rig), with a
throwaway projects folder: two projects in a group, one on its own.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from urllib.parse import quote

import pytest

from agent_media_server import devices, folder_grants, session_files, sessions
from agent_media_core import layout

from test_contract import call, server, typed  # noqa: F401

SID = "11111111-2222-3333-4444-555555555555"


@pytest.fixture()
def tree(tmp_path, monkeypatch):
    projects = tmp_path / "projects"
    for rel in ("sasonica/app", "sasonica/site", "shop"):
        d = projects / rel
        (d / ".git").mkdir(parents=True)
        (d / "README.md").write_text(f"# {rel}\n\nWords.\n")
        (d / "main.py").write_text("print(1)\n")
    monkeypatch.setenv("MEDIA_PROJECTS_DIR", str(projects))
    monkeypatch.setenv("MEDIA_DOC_ROOTS", "")
    monkeypatch.setattr(layout, "_index_seen", (0.0, None, {}))
    folder_grants._reset_for_tests()
    yield projects
    folder_grants._reset_for_tests()


def _paired(server, name, role="member"):
    code, _ = devices.mint_code(name)
    res, obj = call(server, "POST", "/pair", {"code": code, "device": name})
    assert res.status == 200, obj
    devices.set_role(obj["device_id"], role)
    return {"Authorization": f"Bearer {obj['token']}"}, obj["device_id"]


@pytest.fixture()
def people(server, tree):
    admin, _ = _paired(server, "Desk", "admin")
    member, member_id = _paired(server, "Kid's phone", "member")
    guest, guest_id = _paired(server, "TV", "guest")
    return {"admin": admin, "member": member, "member_id": member_id,
            "guest": guest, "guest_id": guest_id}


def _grant(server, people, who, project, role):
    res, obj = call(server, "POST", "/folders/grants",
                    {"device": people[f"{who}_id"], "project": project, "role": role},
                    people["admin"])
    assert res.status == 200, obj
    return obj


def _docs(server, headers, project):
    return call(server, "GET", f"/docs?project={quote(project)}", None, headers)


def test_without_grants_nothing_changes(server, people, tree):
    for who in ("admin", "member", "guest"):
        res, me = call(server, "GET", "/me", None, people[who])
        assert res.status == 200 and me["projects"] is None
        res, _ = _docs(server, people[who], "shop")
        assert res.status == 200
    res, obj = call(server, "GET", "/project/list", None, people["member"])
    assert {r["folder"] for r in obj["projects"]} >= {"app", "site", "shop"}


def test_a_member_with_grants_reaches_only_those(server, people, tree):
    _grant(server, people, "member", "group:sasonica", "viewer")
    res, me = call(server, "GET", "/me", None, people["member"])
    assert [(p["project"], p["role"]) for p in me["projects"]] == [("group:sasonica", "viewer")]
    assert me["projects"][0]["may"] == ["browse", "open", "download"]
    assert me["projects"][0]["path"] == str((tree / "sasonica").resolve())

    assert _docs(server, people["member"], "app")[0].status == 200
    assert _docs(server, people["member"], "shop")[0].status == 404
    res, obj = call(server, "GET", "/project/list", None, people["member"])
    assert {r["folder"] for r in obj["projects"]} == {"app", "site"}
    res, _ = call(server, "GET", f"/doc/read?path={quote(str(tree / 'shop/README.md'))}",
                  None, people["member"])
    assert res.status == 404
    res, obj = call(server, "GET", f"/doc/read?path={quote(str(tree / 'sasonica/app/README.md'))}",
                    None, people["member"])
    assert res.status == 200, obj
    # The admin, and the unscoped guest, are untouched.
    assert _docs(server, people["admin"], "shop")[0].status == 200
    assert _docs(server, people["guest"], "shop")[0].status == 200


def test_the_most_specific_grant_wins(server, people, tree):
    _grant(server, people, "member", "group:sasonica", "editor")
    _grant(server, people, "member", "sasonica/site", "none")
    dev = next(d for d in devices._load() if d["id"] == people["member_id"])
    assert folder_grants.may(dev, tree / "sasonica/app/main.py", "edit")
    assert not folder_grants.may(dev, tree / "sasonica/site/main.py", "open")
    assert not folder_grants.may(dev, tree / "shop/main.py", "browse")
    res, me = call(server, "GET", "/me", None, people["member"])
    assert [p["project"] for p in me["projects"]] == ["group:sasonica"]   # "none" is not listed


def test_a_guest_never_writes(server, people, tree):
    _grant(server, people, "guest", "shop", "maintainer")
    dev = next(d for d in devices._load() if d["id"] == people["guest_id"])
    assert folder_grants.may(dev, tree / "shop/main.py", "open")
    for perm in folder_grants.WRITES:
        assert not folder_grants.may(dev, tree / "shop/main.py", perm)
    res, me = call(server, "GET", "/me", None, people["guest"])
    assert me["projects"][0]["may"] == ["browse", "open", "download"]


def test_scoped_with_nothing_and_back(server, people, tree):
    res, obj = call(server, "POST", "/folders/grants",
                    {"device": people["member_id"], "scoped": True}, people["admin"])
    assert res.status == 200 and obj["scoped"] and obj["grants"] == {}
    assert _docs(server, people["member"], "shop")[0].status == 404
    res, me = call(server, "GET", "/me", None, people["member"])
    assert me["projects"] == []
    call(server, "POST", "/folders/grants", {"device": people["member_id"], "scoped": False},
         people["admin"])
    assert _docs(server, people["member"], "shop")[0].status == 200


def test_thread_files_follow_the_grants(server, people, tree, monkeypatch):
    monkeypatch.setattr(sessions, "latest_cwd", lambda s: str(tree / "shop") if s == SID else "")
    bearer = people["member"]["Authorization"].split()[1]
    ok, got = session_files.resolve({"session": SID, "paths": ["main.py"]}, bearer)
    assert ok and "main.py" in got["paths"]
    _grant(server, people, "member", "group:sasonica", "viewer")
    ok, got = session_files.resolve({"session": SID, "paths": ["main.py"]}, bearer)
    assert ok and got["paths"] == {}
    ok, got = session_files.open_file(SID, str(tree / "shop/main.py"), bearer)
    assert not ok and got["status"] == 404
    ok, _ = session_files.open_file(SID, str(tree / "sasonica/app/main.py"), bearer)
    assert ok
    # A role that may open but not download: no download, no signed link.
    call(server, "POST", "/folders/roles", {"name": "peek", "permissions": ["browse", "open"]},
         people["admin"])
    _grant(server, people, "member", "group:sasonica", "peek")
    ok, _ = session_files.open_file(SID, str(tree / "sasonica/app/main.py"), bearer)
    assert ok
    ok, got = session_files.open_file(SID, str(tree / "sasonica/app/main.py"), bearer,
                                      permission="download")
    assert not ok
    ok, got = session_files.link({"session": SID, "path": str(tree / "sasonica/app/main.py")},
                                 bearer)
    assert not ok


def test_only_an_admin_sets_grants(server, people, tree):
    for who in ("member", "guest"):
        res, obj = call(server, "POST", "/folders/grants",
                        {"device": people["member_id"], "project": "shop", "role": "editor"},
                        people[who])
        assert res.status == 403, obj
        res, _ = call(server, "GET", "/folders/grants", None, people[who])
        assert res.status == 403


def test_refusals(server, people, tree):
    a = people["admin"]
    res, obj = call(server, "POST", "/folders/grants",
                    {"device": "nobody", "project": "shop", "role": "viewer"}, a)
    assert res.status == 404 and obj["code"] == "no_such_device"
    res, obj = call(server, "POST", "/folders/grants",
                    {"device": people["member_id"], "project": "nowhere", "role": "viewer"}, a)
    assert res.status == 404 and obj["code"] == "no_such_project"
    res, obj = call(server, "POST", "/folders/grants",
                    {"device": people["member_id"], "project": "shop", "role": "boss"}, a)
    assert res.status == 400 and obj["code"] == "no_such_role"
    res, obj = call(server, "POST", "/folders/roles", {"name": "editor", "permissions": []}, a)
    assert res.status == 400 and obj["code"] == "builtin"
    res, obj = call(server, "POST", "/folders/roles", {"name": "x", "permissions": ["fly"]}, a)
    assert res.status == 400 and obj["code"] == "bad_permissions"


def test_custom_roles_and_removing_one(server, people, tree):
    a = people["admin"]
    res, obj = call(server, "POST", "/folders/roles",
                    {"name": "uploader", "permissions": ["upload", "browse"]}, a)
    assert res.status == 200 and obj["roles"]["uploader"] == ["browse", "upload"]
    _grant(server, people, "member", "shop", "uploader")
    res, obj = call(server, "GET", "/folders/grants", None, a)
    row = next(s for s in obj["subjects"] if s["subject"] == f"device:{people['member_id']}")
    assert row["grants"] == {folder_grants.canonical("shop"): "uploader"} and row["devices"][0]["name"] == "Kid's phone"
    assert obj["permissions"][0] == "browse" and "viewer" in obj["builtin"]
    res, obj = call(server, "POST", "/folders/roles", {"name": "uploader", "permissions": None}, a)
    assert res.status == 200 and "uploader" not in obj["roles"]
    res, obj = call(server, "GET", "/folders/grants", None, a)
    row = next(s for s in obj["subjects"] if s["subject"] == f"device:{people['member_id']}")
    assert row["grants"] == {} and row["scoped"]


def test_an_account_is_granted_across_its_devices():
    dev = {"id": "d1", "iss": "https://cms.sasonica.com/", "sub": "42"}
    assert folder_grants.subject_of(dev) == "account:https://cms.sasonica.com|42"
    assert folder_grants.subject_of({"id": "d2"}) == "device:d2"


def test_the_file_is_private(server, people, tree):
    _grant(server, people, "member", "shop", "viewer")
    mode = stat.S_IMODE(os.stat(folder_grants._path()).st_mode)
    assert mode == 0o600
