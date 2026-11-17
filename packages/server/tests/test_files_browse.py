"""The read-only file browser (files_browse.py, §6.18b).

Over real HTTP to an in-process canvas (the contract test's rig), with a
throwaway home: a projects folder with a group, secrets that must never
list, and a folder outside every root.
"""

from __future__ import annotations

from urllib.parse import quote, urlsplit

import pytest

from agent_media_server import devices, folder_grants
from agent_media_core import layout

from test_contract import call, server, typed  # noqa: F401


@pytest.fixture()
def tree(tmp_path, monkeypatch):
    home = tmp_path / "home"
    projects = home / "projects"
    for rel in ("sasonica/app", "sasonica/site", "shop"):
        d = projects / rel
        (d / ".git").mkdir(parents=True)
        (d / "README.md").write_text(f"# {rel}\n")
        (d / "src").mkdir()
        (d / "src" / "main.py").write_text("print(1)\n")
    app = projects / "sasonica/app"
    (app / ".env").write_text("TOKEN=no")
    (app / "deploy.pem").write_text("no")
    (app / "secrets").mkdir()
    (app / "secrets" / "x.txt").write_text("no")
    (app / ".claude").mkdir()
    (app / "logo.png").write_bytes(b"\x89PNG\r\n")
    (app / "page.html").write_text("<script>1</script>")
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "x.py").write_text("no")
    (app / "out").symlink_to(outside)
    (home / "notes.txt").write_text("home")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("MEDIA_PROJECTS_DIR", str(projects))
    monkeypatch.setenv("MEDIA_DOC_ROOTS", "")
    monkeypatch.setenv("MEDIA_UPLOAD_DIR", str(home / "shared"))
    monkeypatch.setattr(layout, "_index_seen", (0.0, None, {}))
    folder_grants._reset_for_tests()
    yield {"home": home, "projects": projects, "app": app, "outside": outside}
    folder_grants._reset_for_tests()


def _paired(server, name, role):
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


def _ls(server, headers, **q):
    qs = "&".join(f"{k}={quote(str(v))}" for k, v in q.items())
    return call(server, "GET", f"/files?{qs}", None, headers)


def test_a_project_folder(server, people, tree):
    res, obj = _ls(server, people["admin"], dir=str(tree["app"]))
    assert res.status == 200, obj
    names = [e["name"] for e in obj["entries"]]
    # Folders first, then files; nothing hidden, secret, or leading outside.
    assert names == ["src", "logo.png", "page.html", "README.md"]
    kinds = {e["name"]: e["kind"] for e in obj["entries"]}
    assert kinds == {"src": "dir", "logo.png": "image", "page.html": "text", "README.md": "doc"}
    assert obj["project"] == folder_grants.canonical("sasonica/app")
    assert obj["root"] == str(tree["app"].resolve())
    assert obj["base"] == str(tree["projects"].resolve())
    assert [c["name"] for c in obj["crumbs"]] == ["projects", "sasonica", "app"]
    assert obj["parent"] == str((tree["projects"] / "sasonica").resolve())
    assert "browse" in obj["may"] and obj["truncated"] is False


def test_by_project_and_path(server, people, tree):
    res, obj = _ls(server, people["admin"], project="sasonica/app", path="src")
    assert res.status == 200, obj
    assert [e["name"] for e in obj["entries"]] == ["main.py"]
    assert obj["entries"][0]["kind"] == "text" and obj["entries"][0]["size"] == 9
    for bad in ("../shop", "/etc"):
        res, _ = _ls(server, people["admin"], project="sasonica/app", path=bad)
        assert res.status == 404


def test_what_is_never_browsed(server, people, tree):
    a = people["admin"]
    for d in (tree["home"], tree["outside"], tree["app"] / "secrets", tree["app"] / ".claude",
              tree["app"] / "out", "/etc", "/proc"):
        res, _ = _ls(server, a, dir=str(d))
        assert res.status == 404, d
    res, _ = call(server, "GET", "/files", None, a)
    assert res.status == 400
    res, _ = call(server, "GET", f"/files?dir={quote(str(tree['app']))}", None,
                  {"Authorization": "Bearer nobody"})
    assert res.status == 401


def test_the_projects_root_lists(server, people, tree):
    res, obj = _ls(server, people["admin"], dir=str(tree["projects"]))
    assert [e["name"] for e in obj["entries"]] == ["sasonica", "shop"]
    assert obj["parent"] is None


def test_opening_a_file(server, people, tree):
    a = people["admin"]
    main = tree["app"] / "src" / "main.py"
    res, obj = call(server, "GET", f"/files/read?path={quote(str(main))}&text=1", None, a)
    assert res.status == 200 and obj["text"] == "print(1)\n" and obj["kind"] == "text"
    res, raw = call(server, "GET", f"/files/read?path={quote(str(main))}", None, a)
    assert res.status == 200
    res, _ = call(server, "GET", f"/files/read?path={quote(str(tree['app'] / 'page.html'))}", None, a)
    assert res.status == 200 and "attachment" in res.getheader("Content-Disposition")
    assert res.getheader("Content-Type") == "application/octet-stream"
    for bad in (tree["app"] / ".env", tree["app"] / "deploy.pem", tree["app"] / "secrets/x.txt",
                tree["app"] / "out/x.py", tree["home"] / "notes.txt"):
        res, _ = call(server, "GET", f"/files/read?path={quote(str(bad))}", None, a)
        assert res.status == 404, bad


def test_a_signed_link(server, people, tree):
    main = tree["app"] / "src" / "main.py"
    res, obj = call(server, "POST", "/files/link", {"path": str(main)}, people["admin"])
    assert res.status == 200, obj
    res, _ = call(server, "GET", obj["url"], None, None)
    assert res.status == 200
    bent = obj["url"].replace("main.py", "other.py")
    res, _ = call(server, "GET", bent, None, None)
    assert res.status == 403
    assert urlsplit(obj["url"]).path == "/files/read"


def test_an_unscoped_guest_is_refused(server, people, tree):
    res, obj = _ls(server, people["guest"], dir=str(tree["app"]))
    assert res.status == 403 and obj["code"] == "guest"


def test_a_scoped_member_sees_the_way_down(server, people, tree):
    call(server, "POST", "/folders/grants",
         {"device": people["member_id"], "project": "sasonica/app", "role": "viewer"},
         people["admin"])
    m = people["member"]
    # Above the grant: only the way down.
    res, obj = _ls(server, m, dir=str(tree["projects"]))
    assert res.status == 200 and [e["name"] for e in obj["entries"]] == ["sasonica"]
    assert obj["may"] == []
    res, obj = _ls(server, m, dir=str(tree["projects"] / "sasonica"))
    assert [e["name"] for e in obj["entries"]] == ["app"]
    res, obj = _ls(server, m, dir=str(tree["app"]))
    assert res.status == 200 and obj["may"] == ["browse", "open", "download"]
    assert _ls(server, m, dir=str(tree["projects"] / "shop"))[0].status == 404
    main = quote(str(tree["app"] / "src/main.py"))
    assert call(server, "GET", f"/files/read?path={main}&text=1", None, m)[0].status == 200
    shop = quote(str(tree["projects"] / "shop/src/main.py"))
    assert call(server, "GET", f"/files/read?path={shop}&text=1", None, m)[0].status == 404


def test_a_scoped_guest_reads_its_folders(server, people, tree):
    call(server, "POST", "/folders/grants",
         {"device": people["guest_id"], "project": "shop", "role": "editor"}, people["admin"])
    g = people["guest"]
    res, obj = _ls(server, g, dir=str(tree["projects"] / "shop"))
    assert res.status == 200 and obj["may"] == ["browse", "open", "download"]
    assert _ls(server, g, dir=str(tree["app"]))[0].status == 404


def test_open_without_download(server, people, tree):
    a = people["admin"]
    call(server, "POST", "/folders/roles", {"name": "peek", "permissions": ["browse", "open"]}, a)
    call(server, "POST", "/folders/grants",
         {"device": people["member_id"], "project": "shop", "role": "peek"}, a)
    m = people["member"]
    main = quote(str(tree["projects"] / "shop/src/main.py"))
    assert call(server, "GET", f"/files/read?path={main}", None, m)[0].status == 200
    assert call(server, "GET", f"/files/read?path={main}&download=1", None, m)[0].status == 404
    res, _ = call(server, "POST", "/files/link",
                  {"path": str(tree["projects"] / "shop/src/main.py")}, m)
    assert res.status == 404
