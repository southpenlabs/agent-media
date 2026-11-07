"""The phone app updating itself (server-contract.md §6.52; app_update.py):
`GET /app/update` and `GET /app/update/apk`, admins only. gh is replaced in
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
     "body": "versionCode: 1963\ncommit: de8e1e7" + "1" * 33 + "\n",
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


@pytest.fixture()
def gh(monkeypatch):
    """gh replaced: counts list reads and downloads, writes the asset bytes."""
    seen = {"lists": 0, "downloads": [], "releases": RELEASES}
    data = {a["name"]: d for a, d in (
        (_asset("sasonica-personal-1963-de8e1e7.apk", PERSONAL), PERSONAL),
        (_asset("sasonica-personal-1962-b5babc3.apk", PERSONAL), PERSONAL),
        (_asset("sasonica-1962-b5babc3.apk", RELEASE), RELEASE))}

    def releases(name):
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
    monkeypatch.delenv("MEDIA_APP_UPDATE_TTL", raising=False)
    app_update.reset()
    yield seen
    app_update.reset()


def test_the_newest_build_of_each_flavor(server, signed_in, gh):
    res, obj = call(server, "GET", "/app/update?flavor=personal&current=1900", headers=AUTH)
    assert res.status == 200, obj
    assert obj == {"ok": True, "versionCode": 1963, "commit": "de8e1e7" + "1" * 33,
                   "sha256": _sha(PERSONAL), "size": len(PERSONAL),
                   "published": "2026-11-07T09:33:48Z",
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


def test_admins_only(server, gh):
    for role, want in (("member", 403), ("guest", 403), ("admin", 200)):
        code, _ = devices.mint_code(f"phone {role}", role=role)
        _, paired = call(server, "POST", "/pair", {"code": code, "device": f"phone {role}"})
        hdr = {"Authorization": f"Bearer {paired['token']}"}
        res, obj = call(server, "GET", "/app/update?flavor=personal", headers=hdr)
        assert res.status == want, (role, obj)
        res, _ = call(server, "GET", "/app/update/apk?build=1963&flavor=personal", headers=hdr)
        assert res.status == want, role
    res, _ = call(server, "GET", "/app/update?flavor=personal")
    assert res.status in (401, 403)
    # Only the admin's request downloaded anything.
    assert gh["downloads"] == [("build-1963", "sasonica-personal-1963-de8e1e7.apk")]


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
