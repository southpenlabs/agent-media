"""The phone app updating itself (server-contract.md §6.52; app_update.py):
`GET /app/update` and `GET /app/update/apk`, Sasonica's and (app=bridges)
the Bridges app's. gh is replaced in
every test: nothing here reaches GitHub."""

from __future__ import annotations

import hashlib
import threading
import time

import pytest

from agent_media_server import app, app_update, devices

from test_contract import AUTH, call, server, signed_in, typed  # noqa: F401

PERSONAL = b"personal apk bytes " * 100
RELEASE = b"release apk bytes " * 50


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _asset(name: str, data: bytes, digest: bool = True) -> dict:
    a = {"name": name, "size": len(data)}
    if digest:
        a["digest"] = "sha256:" + _sha(data)
    return a


#: Newest first, as GitHub lists them: 1963 minimal (personal only), 1962
#: full, with a debug APK, and its sha256 only in the notes; a draft and a
#: non-build release that must be skipped.
RELEASES = [
    {"tag_name": "build-1964", "draft": True, "assets": [_asset("sasonica-personal-1964-aaaaaaa.apk", PERSONAL)]},
    {"tag_name": "build-1963", "published_at": "2026-11-07T09:33:48Z",
     "target_commitish": "de8e1e7" + "0" * 33,
     "body": "versionCode: 1963\ncommit: de8e1e7" + "1" * 33 + "\nbuilt: 2026-11-07T09:21:05Z\n",
     "assets": [_asset("sasonica-personal-1963-de8e1e7.apk", PERSONAL),
                _asset("sasonica-web-1963-de8e1e7.tar.gz", b"web")]},
    {"tag_name": "build-1962", "published_at": "2026-11-07T09:28:10Z",
     "body": f"sha256:\n    {_sha(RELEASE)}  sasonica-1962-b5babc3.apk\n",
     "assets": [_asset("sasonica-personal-1962-b5babc3.apk", PERSONAL),
                _asset("sasonica-1962-b5babc3.apk", RELEASE, digest=False),
                _asset("sasonica-debug-1962-b5babc3.apk", b"debug"),
                _asset("sasonica-1962-b5babc3-debug.apk", b"debug")]},
    {"tag_name": "v1.0", "assets": [_asset("sasonica-1-abcdef0.apk", RELEASE)]},
]


BRIDGES_APK = b"bridges apk bytes " * 40

#: The bridges repo: 12 the newest, its sha256 in the notes; 11 older; a
#: Sasonica-named asset in it that must never be taken for the bridges app.
BRIDGES_RELEASES = [
    {"tag_name": "build-12", "published_at": "2026-11-16T01:00:00Z",
     "body": f"commit: 0123abc{'2' * 33}\nsha256:\n    {_sha(BRIDGES_APK)}  sasonica-bridges-12-0123abc.apk\n",
     "assets": [_asset("sasonica-bridges-12-0123abc.apk", BRIDGES_APK, digest=False),
                _asset("sasonica-bridges-debug-12-0123abc.apk", b"debug")]},
    {"tag_name": "build-11", "published_at": "2026-11-15T01:00:00Z",
     "assets": [_asset("sasonica-bridges-11-fedcba9.apk", BRIDGES_APK),
                _asset("sasonica-personal-11-fedcba9.apk", PERSONAL),
                _asset("sasonica-11-fedcba9.apk", RELEASE)]},
]


@pytest.fixture()
def gh(monkeypatch):
    """gh replaced: counts list reads and downloads, writes the asset bytes."""
    seen = {"lists": 0, "bridges_lists": 0, "downloads": [], "releases": RELEASES}
    data = {a["name"]: d for a, d in (
        (_asset("sasonica-personal-1963-de8e1e7.apk", PERSONAL), PERSONAL),
        (_asset("sasonica-personal-1962-b5babc3.apk", PERSONAL), PERSONAL),
        (_asset("sasonica-1962-b5babc3.apk", RELEASE), RELEASE))}

    data.update({"sasonica-bridges-12-0123abc.apk": BRIDGES_APK,
                 "sasonica-bridges-11-fedcba9.apk": BRIDGES_APK})

    def releases(name):
        if name == "git.southpenlabs.com/sasonica/sasonica-bridges":
            seen["bridges_lists"] += 1
            return BRIDGES_RELEASES
        assert name == "southpenlabs/sasonica-app"
        seen["lists"] += 1
        return seen["releases"]

    def download(name, tag, asset, dest):
        seen["downloads"].append((tag, asset))
        time.sleep(0.05)  # long enough for a second request to arrive
        dest.write_bytes(seen.get("corrupt") or data[asset])

    monkeypatch.setattr(app_update, "_gh_releases", releases)
    monkeypatch.setattr(app_update, "_gh_download", download)
    monkeypatch.delenv("MEDIA_APP_UPDATE_REPO", raising=False)
    monkeypatch.delenv("MEDIA_BRIDGES_UPDATE_REPO", raising=False)
    monkeypatch.delenv("MEDIA_APP_UPDATE_TTL", raising=False)
    app_update.reset()
    yield seen
    app_update.reset()


def test_the_newest_build_of_each_flavor(server, signed_in, gh):
    res, obj = call(server, "GET", "/app/update?flavor=personal&current=1900", headers=AUTH)
    assert res.status == 200, obj
    assert obj == {"ok": True, "versionCode": 1963, "commit": "de8e1e7" + "1" * 33,
                   "sha256": _sha(PERSONAL), "size": len(PERSONAL),
                   "published": "2026-11-07T09:33:48Z", "built_at": "2026-11-07T09:21:05Z",
                   "url": "/app/update/apk?build=1963&flavor=personal"}
    # The release flavor's newest is the full build, its sha256 from the notes;
    # no debug asset is ever taken for it.
    _, obj = call(server, "GET", "/app/update?flavor=release&current=1900", headers=AUTH)
    assert obj["versionCode"] == 1962 and obj["sha256"] == _sha(RELEASE)
    assert obj["size"] == len(RELEASE) and obj["commit"] == "b5babc3"
    assert obj["url"] == "/app/update/apk?build=1962&flavor=release"
    assert "/app/update" in app.CORS_PATHS and "/app/update/apk" in app.CORS_PATHS


def test_nothing_newer(server, signed_in, gh):
    for flavor, current in (("personal", "1963"), ("personal", "2000"), ("release", "1962")):
        res, obj = call(server, "GET", f"/app/update?flavor={flavor}&current={current}", headers=AUTH)
        assert res.status == 200 and obj == {"ok": True, "versionCode": None}, (flavor, current)
    # No build of that flavor at all is the same answer.
    gh["releases"] = RELEASES[:2]
    app_update.reset()
    _, obj = call(server, "GET", "/app/update?flavor=release&current=1", headers=AUTH)
    assert obj == {"ok": True, "versionCode": None}


def test_the_list_is_cached(server, signed_in, gh, monkeypatch):
    for _ in range(3):
        call(server, "GET", "/app/update?flavor=personal&current=1", headers=AUTH)
    assert gh["lists"] == 1
    # Once the TTL has passed it is read again; a failed read then answers
    # from the list in hand.
    monkeypatch.setenv("MEDIA_APP_UPDATE_TTL", "0")
    call(server, "GET", "/app/update?flavor=personal", headers=AUTH)
    assert gh["lists"] == 2

    def down(name):
        raise app_update.Unavailable("offline")
    monkeypatch.setattr(app_update, "_gh_releases", down)
    res, obj = call(server, "GET", "/app/update?flavor=personal", headers=AUTH)
    assert res.status == 200 and obj["versionCode"] == 1963
    app_update.reset()
    res, obj = call(server, "GET", "/app/update?flavor=personal", headers=AUTH)
    assert res.status == 502 and not obj["ok"]


def test_open_to_anyone(server, gh):
    """A TV whose pairing was forgotten still updates (David, 16 Nov 2026:
    "Anyone, anywhere"): no token, an unknown token, a member or a guest all
    get the answer and the newest APK."""
    hdrs = [{}, {"Authorization": "Bearer forgotten-token"}]
    for role in ("member", "guest"):
        code, _ = devices.mint_code(f"phone {role}", role=role)
        _, paired = call(server, "POST", "/pair", {"code": code, "device": f"phone {role}"})
        hdrs.append({"Authorization": f"Bearer {paired['token']}"})
    for hdr in hdrs:
        res, obj = call(server, "GET", "/app/update?flavor=personal", headers=hdr)
        assert res.status == 200 and obj["versionCode"] == 1963 and obj["sha256"], (hdr, obj)
        res, _ = call(server, "GET", "/app/update/apk?build=1963&flavor=personal", headers=hdr)
        assert res.status == 200, hdr
    # One download however many asked.
    assert gh["downloads"] == [("build-1963", "sasonica-personal-1963-de8e1e7.apk")]


def test_an_older_build_is_for_admins(server, gh, signed_in):
    """Anyone can ask, so only an admin device may fetch a build that is not
    the newest: old builds cannot be made to churn the cache."""
    res, obj = call(server, "GET", "/app/update/apk?build=1962&flavor=personal",
                    headers={"Authorization": ""})
    assert res.status == 404 and obj["code"] == "not_newest"
    res, _ = call(server, "GET", "/app/update/apk?build=1962&flavor=personal", headers=AUTH)
    assert res.status == 200


@pytest.mark.parametrize("query", [
    "build=1963&flavor=debug", "build=1963", "flavor=personal", "build=-1&flavor=personal",
    "build=19%2F63&flavor=personal", "build=..%2F1963&flavor=personal",
    "build=1963x&flavor=personal", "build=1963&flavor=personal%2F..",
    "build=1234567890&flavor=personal", "build=--help&flavor=personal",
])
def test_a_bad_parameter_is_400(server, signed_in, gh, query):
    res, obj = call(server, "GET", f"/app/update/apk?{query}", headers=AUTH)
    assert res.status == 400, obj
    assert gh["downloads"] == [] and gh["lists"] == 0


def test_bad_check_parameters(server, signed_in, gh):
    for q in ("flavor=debug", "", "flavor=personal&current=abc", "flavor=personal&current=-1"):
        res, _ = call(server, "GET", f"/app/update?{q}", headers=AUTH)
        assert res.status == 400, q


def test_the_apk_is_served_and_kept(server, signed_in, gh, tmp_path):
    res, body = call(server, "GET", "/app/update/apk?build=1963&flavor=personal", headers=AUTH)
    assert res.status == 200 and body == PERSONAL
    assert res.getheader("Content-Type") == "application/vnd.android.package-archive"
    assert res.getheader("Content-Length") == str(len(PERSONAL))
    assert (app_update.cache_dir() / "sasonica-personal-1963-de8e1e7.apk").is_file()
    assert str(app_update.cache_dir()).startswith(str(tmp_path))
    # Again: from the disk, no second download.
    res, body = call(server, "GET", "/app/update/apk?build=1963&flavor=personal", headers=AUTH)
    assert res.status == 200 and body == PERSONAL
    assert len(gh["downloads"]) == 1
    # A build with no APK of that flavor is 404; an unknown build is too.
    res, _ = call(server, "GET", "/app/update/apk?build=1963&flavor=release", headers=AUTH)
    assert res.status == 404
    res, _ = call(server, "GET", "/app/update/apk?build=1700&flavor=personal", headers=AUTH)
    assert res.status == 404


def test_the_last_two_are_kept(server, signed_in, gh):
    for build, flavor in (("1962", "personal"), ("1962", "release"), ("1963", "personal")):
        res, _ = call(server, "GET", f"/app/update/apk?build={build}&flavor={flavor}", headers=AUTH)
        assert res.status == 200
        time.sleep(0.02)
    kept = sorted(p.name for p in app_update.cache_dir().glob("*.apk"))
    assert kept == ["sasonica-1962-b5babc3.apk", "sasonica-personal-1963-de8e1e7.apk"]


def test_one_download_for_concurrent_requests(server, signed_in, gh):
    got = []

    def fetch():
        res, body = call(server, "GET", "/app/update/apk?build=1963&flavor=personal", headers=AUTH)
        got.append((res.status, body == PERSONAL))
    ts = [threading.Thread(target=fetch) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert got == [(200, True)] * 4
    assert len(gh["downloads"]) == 1


def test_a_download_that_does_not_match_is_not_kept(server, signed_in, gh):
    gh["corrupt"] = b"not the apk"
    res, obj = call(server, "GET", "/app/update/apk?build=1963&flavor=personal", headers=AUTH)
    assert res.status == 502 and not obj["ok"]
    assert list(app_update.cache_dir().iterdir()) == []


def test_features_says_so(server, signed_in):
    _, obj = call(server, "GET", "/features", headers=AUTH)
    assert obj["app_update"] is True


# --- the Sasonica Bridges app (app=bridges, 16 Nov 2026) ----------------------

def _paired(server, role: str) -> dict:
    code, _ = devices.mint_code(f"phone {role}", role=role)
    _, paired = call(server, "POST", "/pair", {"code": code, "device": f"phone {role}"})
    return {"Authorization": f"Bearer {paired['token']}"}


def test_the_asset_names_never_cross():
    """A Sasonica asset is never taken for the bridges app, nor a bridges one
    for either Sasonica flavor."""
    mixed = [{"tag_name": "build-5", "assets": [
        _asset("sasonica-bridges-5-abcdef0.apk", BRIDGES_APK)]},
        {"tag_name": "build-4", "assets": [
            _asset("sasonica-personal-4-abcdef0.apk", PERSONAL),
            _asset("sasonica-4-abcdef0.apk", RELEASE)]}]
    sas = app_update._builds(mixed)
    assert [(b["versionCode"], sorted(b["apks"])) for b in sas] == [(4, ["personal", "release"])]
    br = app_update._builds(mixed, "bridges")
    assert [(b["versionCode"], sorted(b["apks"])) for b in br] == [(5, ["bridges"])]
    assert br[0]["apks"]["bridges"]["name"] == "sasonica-bridges-5-abcdef0.apk"


def test_the_bridges_listing(server, signed_in, gh):
    res, obj = call(server, "GET", "/app/update?app=bridges&current=10", headers=AUTH)
    assert res.status == 200, obj
    assert obj == {"ok": True, "versionCode": 12, "commit": "0123abc" + "2" * 33,
                   "sha256": _sha(BRIDGES_APK), "size": len(BRIDGES_APK),
                   "published": "2026-11-16T01:00:00Z", "built_at": None,
                   "url": "/app/update/apk?app=bridges&build=12"}
    _, obj = call(server, "GET", "/app/update?app=bridges&current=12", headers=AUTH)
    assert obj == {"ok": True, "versionCode": None}
    # Its own list and cache: Sasonica's is not read, and is not confused with it.
    assert gh["bridges_lists"] == 1 and gh["lists"] == 0
    _, obj = call(server, "GET", "/app/update?flavor=personal", headers=AUTH)
    assert obj["versionCode"] == 1963 and gh["lists"] == 1 and gh["bridges_lists"] == 1


def test_the_bridges_repo_is_set_by_its_own_variable(monkeypatch):
    monkeypatch.delenv("MEDIA_BRIDGES_UPDATE_REPO", raising=False)
    monkeypatch.setenv("MEDIA_APP_UPDATE_REPO", "someone/else")
    assert app_update.repo("bridges") == "git.southpenlabs.com/sasonica/sasonica-bridges"
    monkeypatch.setenv("MEDIA_BRIDGES_UPDATE_REPO", "org/bridges")
    assert app_update.repo("bridges") == "org/bridges"
    assert app_update.repo() == "someone/else"
    monkeypatch.setenv("MEDIA_BRIDGES_UPDATE_REPO", "../x")
    with pytest.raises(app_update.Unavailable):
        app_update.repo("bridges")


def test_bridges_is_for_paired_devices_only(server, gh):
    """The bridges repo is private and its AGPL app not published yet: no
    token or an unknown one is 401, a guest 403; a member and an admin get it."""
    for hdr in ({}, {"Authorization": "Bearer forgotten-token"}):
        res, obj = call(server, "GET", "/app/update?app=bridges", headers=hdr)
        assert res.status == 401 and obj["code"] == "not_paired", hdr
        res, obj = call(server, "GET", "/app/update/apk?app=bridges&build=12", headers=hdr)
        assert res.status == 401, hdr
    guest = _paired(server, "guest")
    res, obj = call(server, "GET", "/app/update?app=bridges", headers=guest)
    assert res.status == 403 and obj["code"] == "guest"
    res, _ = call(server, "GET", "/app/update/apk?app=bridges&build=12", headers=guest)
    assert res.status == 403
    assert gh["bridges_lists"] == 0 and gh["downloads"] == []
    for role in ("member", "admin"):
        hdr = _paired(server, role)
        res, obj = call(server, "GET", "/app/update?app=bridges", headers=hdr)
        assert res.status == 200 and obj["versionCode"] == 12, (role, obj)
        res, body = call(server, "GET", "/app/update/apk?app=bridges&build=12", headers=hdr)
        assert res.status == 200 and body == BRIDGES_APK, role
    # The guest still updates Sasonica, as anyone may.
    res, _ = call(server, "GET", "/app/update?flavor=personal", headers=guest)
    assert res.status == 200


def test_the_bridges_apk(server, signed_in, gh):
    res, body = call(server, "GET", "/app/update/apk?app=bridges&build=12", headers=AUTH)
    assert res.status == 200 and body == BRIDGES_APK
    assert res.getheader("Content-Type") == "application/vnd.android.package-archive"
    assert gh["downloads"] == [("build-12", "sasonica-bridges-12-0123abc.apk")]
    # Its own dir, apart from Sasonica's APKs and their keep-last-two.
    d = app_update.cache_dir("bridges")
    assert d == app_update.cache_dir() / "bridges"
    assert (d / "sasonica-bridges-12-0123abc.apk").is_file()
    for build, flavor in (("1962", "personal"), ("1962", "release"), ("1963", "personal")):
        res, _ = call(server, "GET", f"/app/update/apk?build={build}&flavor={flavor}", headers=AUTH)
        assert res.status == 200
        time.sleep(0.02)
    assert (d / "sasonica-bridges-12-0123abc.apk").is_file()
    assert len(list(app_update.cache_dir().glob("*.apk"))) == 2
    # The older bridges build: an admin device (the signed-in owner) may.
    res, body = call(server, "GET", "/app/update/apk?app=bridges&build=11", headers=AUTH)
    assert res.status == 200 and body == BRIDGES_APK
    # A build with no bridges APK is 404 (the Sasonica-named ones in 11's
    # release are not it; 1963 is Sasonica's).
    res, _ = call(server, "GET", "/app/update/apk?app=bridges&build=1963", headers=AUTH)
    assert res.status == 404


@pytest.mark.parametrize("path", [
    "/app/update?app=other", "/app/update?app=Bridges", "/app/update/apk?app=other&build=12",
    "/app/update?app=bridges&flavor=personal", "/app/update/apk?app=bridges&build=12&flavor=release",
    "/app/update?app=bridges&current=x", "/app/update/apk?app=bridges", "/app/update/apk?app=bridges&build=1x",
])
def test_bridges_bad_parameters_are_400(server, signed_in, gh, path):
    res, obj = call(server, "GET", path, headers=AUTH)
    assert res.status == 400, obj
    assert gh["downloads"] == [] and gh["lists"] == 0 and gh["bridges_lists"] == 0


def test_app_sasonica_is_the_default(server, signed_in, gh):
    _, a = call(server, "GET", "/app/update?flavor=personal", headers=AUTH)
    _, b = call(server, "GET", "/app/update?app=sasonica&flavor=personal", headers=AUTH)
    assert a == b and a["url"] == "/app/update/apk?build=1963&flavor=personal"
    res, _ = call(server, "GET", "/app/update?app=sasonica", headers=AUTH)
    assert res.status == 400


def test_features_says_bridges(server, signed_in):
    _, obj = call(server, "GET", "/features", headers=AUTH)
    assert obj["app_update_bridges"] is True


# --- web bundles without a new APK (native=/web=, 17 Nov 2026) ----------------

WEB_A = b"web bundle for shell a " * 60
WEB_A_OLD = b"older web bundle for shell a " * 30
WEB_B = b"web bundle for shell b " * 20
NATIVE_A, NATIVE_B = "0123456789ab", "ba9876543210"

#: 2003 web-only for shell a (sha256 in the notes), 2002 a new shell b,
#: 2001 shell a, 2000 a bundle with no native line (before aac84ed6), 1999 a
#: native line with the old web asset only.
WEB_RELEASES = [
    {"tag_name": "build-2003", "published_at": "2026-11-17T13:00:00Z",
     "body": f"commit: aaaa003{'3' * 33}\nnative: {NATIVE_A}\nbuilt: 2026-11-17T12:51:30Z\nsha256:\n"
             f"    {_sha(WEB_A)}  sasonica-shellweb-2003-aaaa003.tar.gz\n",
     "assets": [_asset("sasonica-personal-2003-aaaa003.apk", PERSONAL),
                _asset("sasonica-shellweb-2003-aaaa003.tar.gz", WEB_A, digest=False),
                _asset("sasonica-web-2003-aaaa003.tar.gz", b"web")]},
    {"tag_name": "build-2002", "published_at": "2026-11-17T12:00:00Z",
     "body": f"native: {NATIVE_B}\n",
     "assets": [_asset("sasonica-personal-2002-bbbb002.apk", PERSONAL),
                _asset("sasonica-shellweb-2002-bbbb002.tar.gz", WEB_B)]},
    {"tag_name": "build-2001", "published_at": "2026-11-17T11:00:00Z",
     "body": f"native: {NATIVE_A}\n",
     "assets": [_asset("sasonica-personal-2001-cccc001.apk", PERSONAL),
                _asset("sasonica-shellweb-2001-cccc001.tar.gz", WEB_A_OLD)]},
    {"tag_name": "build-2000", "published_at": "2026-11-17T10:00:00Z",
     "assets": [_asset("sasonica-personal-2000-dddd000.apk", PERSONAL),
                _asset("sasonica-shellweb-2000-dddd000.tar.gz", WEB_A)]},
    {"tag_name": "build-1999", "body": f"native: {NATIVE_A}\n",
     "assets": [_asset("sasonica-personal-1999-eeee999.apk", PERSONAL),
                _asset("sasonica-web-1999-eeee999.tar.gz", b"web")]},
]


@pytest.fixture()
def webgh(gh, monkeypatch):
    gh["releases"] = WEB_RELEASES
    data = {"sasonica-shellweb-2003-aaaa003.tar.gz": WEB_A,
            "sasonica-shellweb-2002-bbbb002.tar.gz": WEB_B,
            "sasonica-shellweb-2001-cccc001.tar.gz": WEB_A_OLD}

    def download(name, tag, asset, dest):
        gh["downloads"].append((tag, asset))
        dest.write_bytes(gh.get("corrupt") or data[asset])
    monkeypatch.setattr(app_update, "_gh_download", download)
    return gh


def test_the_web_offer(server, webgh):
    res, obj = call(server, "GET", f"/app/update?flavor=personal&current=2001&native={NATIVE_A}&web=2001")
    assert res.status == 200, obj
    assert obj["versionCode"] == 2003
    assert obj["web"] == {"build": 2003, "commit": "aaaa003" + "3" * 33, "sha256": _sha(WEB_A),
                          "size": len(WEB_A), "published": "2026-11-17T13:00:00Z",
                          "built_at": "2026-11-17T12:51:30Z",
                          "url": "/app/update/web?build=2003", "native": NATIVE_A}
    # Another shell gets its own; a web build already as new gets none.
    _, obj = call(server, "GET", f"/app/update?flavor=personal&native={NATIVE_B}")
    assert obj["web"]["build"] == 2002 and obj["web"]["sha256"] == _sha(WEB_B)
    _, obj = call(server, "GET", f"/app/update?flavor=personal&native={NATIVE_A}&web=2003")
    assert obj["web"] is None
    # Without native, no web field at all (an older app's answer is unchanged);
    # "dev" or an unknown shell is offered nothing.
    _, obj = call(server, "GET", "/app/update?flavor=personal")
    assert "web" not in obj
    for native in ("dev", "ffffffffffff"):
        _, obj = call(server, "GET", f"/app/update?flavor=personal&native={native}")
        assert obj["web"] is None, native
    res, _ = call(server, "GET", f"/app/update?flavor=personal&native={NATIVE_A}&web=x")
    assert res.status == 400
    assert "/app/update/web" in app.CORS_PATHS


def test_no_native_line_no_bundle(webgh):
    """A build before aac84ed6 (no native line) offers none, whatever its
    assets; the old sasonica-web asset is never a bundle."""
    by = {b["versionCode"]: b for b in app_update.builds()}
    assert by[2000]["web"] is None and by[1999]["web"] is None
    assert app_update.newest_web(NATIVE_A, 2002)["build"] == 2003
    assert app_update.newest_web(NATIVE_A, 0)["build"] == 2003


def test_the_bundle_is_served_and_kept(server, signed_in, webgh):
    res, body = call(server, "GET", "/app/update/web?build=2003")
    assert res.status == 200 and body == WEB_A
    assert res.getheader("Content-Type") == "application/gzip"
    assert res.getheader("Content-Length") == str(len(WEB_A))
    assert (app_update.cache_dir("web") / "sasonica-shellweb-2003-aaaa003.tar.gz").is_file()
    res, body = call(server, "GET", "/app/update/web?build=2003")
    assert body == WEB_A and len(webgh["downloads"]) == 1
    # The newest of another shell is open too; an older one of the same
    # shell is for admins; a build with no bundle is 404.
    res, _ = call(server, "GET", "/app/update/web?build=2002")
    assert res.status == 200
    res, obj = call(server, "GET", "/app/update/web?build=2001", headers={"Authorization": ""})
    assert res.status == 404 and obj["code"] == "not_newest"
    res, _ = call(server, "GET", "/app/update/web?build=2001", headers=AUTH)
    assert res.status == 200
    for b in ("2000", "1999", "1700"):
        res, _ = call(server, "GET", f"/app/update/web?build={b}", headers=AUTH)
        assert res.status == 404, b
    kept = sorted(p.name for p in app_update.cache_dir("web").glob("*.tar.gz"))
    assert kept == ["sasonica-shellweb-2001-cccc001.tar.gz", "sasonica-shellweb-2002-bbbb002.tar.gz"]
    # The APKs' own cache is untouched by the bundles.
    assert list(app_update.cache_dir().glob("*.apk")) == []


@pytest.mark.parametrize("query", ["", "build=", "build=x", "build=..%2F2003", "build=1234567890"])
def test_bad_bundle_parameters_are_400(server, webgh, query):
    res, _ = call(server, "GET", f"/app/update/web?{query}")
    assert res.status == 400
    assert webgh["downloads"] == []


def test_a_bundle_that_does_not_match_is_not_kept(server, webgh):
    webgh["corrupt"] = b"not the bundle"
    res, obj = call(server, "GET", "/app/update/web?build=2003")
    assert res.status == 502 and "web bundle" in obj["error"]
    assert list(app_update.cache_dir("web").iterdir()) == []


def test_features_says_web(server, signed_in):
    _, obj = call(server, "GET", "/features", headers=AUTH)
    assert obj["app_update_web"] is True


def test_built_at_from_the_notes():
    """`built:` in the notes (sasonica-app b2d20f04 on) is the offer's
    built_at; a release without one, or with a line that is not a time, is
    None."""
    f = app_update._built_at
    assert f("versionCode: 2531\nbuilt: 2026-11-18T03:32:00Z\ncommit: abc\n") == "2026-11-18T03:32:00Z"
    assert f("built: 2026-11-18T14:32:00+11:00") == "2026-11-18T14:32:00+11:00"
    assert f("versionCode: 1\n") is None and f("") is None and f(None) is None
    assert f("built: yesterday\n") is None and f("rebuilt: 2026-11-18T03:32:00Z") is None


def test_a_host_owner_name_repo_is_read_from_forgejo(monkeypatch, tmp_path):
    """A bridges repo on Forgejo (28 Nov 2026): the list and the asset come
    from that host's API, not gh."""
    import io
    import json

    asked = []
    rel = {"tag_name": "build-1001", "body": "commit: " + "a" * 40, "assets": [
        {"name": "sasonica-bridges-1001-aaaaaaa.apk", "size": 3,
         "browser_download_url": "https://git.example.org/attachments/x1"}]}

    def get(host, path, timeout):
        asked.append((host, path))
        if path.endswith("releases?limit=30"):
            return io.BytesIO(json.dumps([rel]).encode())
        if "/releases/tags/" in path:
            return io.BytesIO(json.dumps(rel).encode())
        return io.BytesIO(b"apk")

    monkeypatch.setattr(app_update, "_forgejo_get", get)
    name = "git.example.org/sasonica/sasonica-bridges"
    assert app_update._gh_releases(name) == [rel]
    dest = tmp_path / "x.apk"
    app_update._gh_download(name, "build-1001", rel["assets"][0]["name"], dest)
    assert dest.read_bytes() == b"apk"
    assert asked == [
        ("git.example.org", "/api/v1/repos/sasonica/sasonica-bridges/releases?limit=30"),
        ("git.example.org", "/api/v1/repos/sasonica/sasonica-bridges/releases/tags/build-1001"),
        ("git.example.org", "/attachments/x1")]
