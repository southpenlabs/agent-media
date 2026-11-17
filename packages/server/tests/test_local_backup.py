"""Local chats backups (server-contract.md §6.61 "Backup", local_backup.py):
the phone's encrypted bundle kept as ciphertext, per device.

Over real HTTP with the contract test's rig and really paired devices; the
state dir is conftest's throwaway one. The bodies are random bytes: the
server never reads what is in a bundle."""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import stat
import time

import pytest

from agent_media_server import devices, local_backup
from test_contract import call, server, typed  # noqa: F401 — fixtures

ISS = "https://id.example.test"


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _paired(server, role="", name="Pixel 8a"):
    code, _ = devices.mint_code(name, role=role)
    res, obj = call(server, "POST", "/pair", {"code": code, "device": name})
    assert res.status == 200, obj
    return obj["token"]


def _enrolled(sub, role="member", name="phone"):
    return devices.enrol_account({"iss": ISS, "sub": sub, "email": f"{sub}@x.test"}, name,
                                 role=role)["token"]


def upload(addr, token, body: bytes, created=None, sha=None, fmt="sbb1", via="query",
           extra_q="", length=None):
    created = created or int(time.time()) - 60
    sha = sha or hashlib.sha256(body).hexdigest()
    hdrs = {**_auth(token), "Content-Type": "application/octet-stream",
            "Content-Length": str(len(body) if length is None else length)}
    path = "/local/backup"
    if via == "query":
        path += f"?created={created}&format={fmt}&sha256={sha}{extra_q}"
    else:
        hdrs.update({"X-Backup-Created": str(created), "X-Backup-Format": fmt,
                     "X-Backup-Sha256": sha})
    conn = http.client.HTTPConnection(*addr, timeout=10)
    conn.putrequest("POST", path)
    for k, v in hdrs.items():
        conn.putheader(k, v)
    conn.endheaders()
    try:
        conn.send(body)
    except OSError:
        pass                    # refused unread: the server may close first
    res = conn.getresponse()
    raw = res.read()
    conn.close()
    try:
        return res.status, json.loads(raw)
    except ValueError:
        return res.status, raw


def listed(addr, token):
    res, obj = call(addr, "GET", "/local/backups", headers=_auth(token))
    assert res.status == 200, obj
    return [b["id"] for b in obj["backups"]]


def fetch(addr, token, bid, rng=None):
    conn = http.client.HTTPConnection(*addr, timeout=10)
    hdrs = _auth(token)
    if rng:
        hdrs["Range"] = rng
    conn.request("GET", f"/local/backup/{bid}", headers=hdrs)
    res = conn.getresponse()
    raw = res.read()
    conn.close()
    return res, raw


def delete(addr, token, bid):
    return call(addr, "DELETE", f"/local/backup/{bid}", headers=_auth(token))


# --- upload ---------------------------------------------------------------------

def test_upload_keeps_the_bytes_and_checks_the_hash(server):
    phone = _paired(server)
    body = os.urandom(300_000)
    st, got = upload(server, phone, body, created=1763400000)
    assert st == 200, got
    assert set(got) == {"ok", "id", "size", "created", "kept"}
    assert got["size"] == len(body) and got["created"] == 1763400000 and got["kept"] == 1
    dev, stem = got["id"].split(".", 1)
    assert stem == f"1763400000-{hashlib.sha256(body).hexdigest()[:8]}"
    folder = local_backup.root() / dev
    f = folder / f"{stem}.sbb"
    assert f.read_bytes() == body
    assert stat.S_IMODE(f.stat().st_mode) == 0o600
    assert stat.S_IMODE(folder.stat().st_mode) == 0o700
    assert stat.S_IMODE(local_backup.root().stat().st_mode) == 0o700
    assert not list(folder.glob("*.part")) and not list(folder.glob(".*"))


def test_headers_work_as_well_as_the_query(server):
    phone = _paired(server)
    st, got = upload(server, phone, b"ciphertext", via="headers")
    assert st == 200, got


def test_a_wrong_hash_keeps_nothing(server):
    phone = _paired(server)
    st, got = upload(server, phone, b"ciphertext", sha="0" * 64)
    assert st == 400 and got["code"] == "sha256_mismatch"
    assert not list(local_backup.root().glob("*/*"))
    assert listed(server, phone) == []


@pytest.mark.parametrize("q", ["created=now&format=sbb1&sha256=" + "a" * 64,
                               "created=1763400000&format=&sha256=" + "a" * 64,
                               "created=1763400000&format=a/b&sha256=" + "a" * 64,
                               "created=1763400000&format=sbb1&sha256=abc",
                               f"created={int(time.time()) + 3 * 86400}&format=sbb1&sha256=" + "a" * 64,
                               "created=1763400000&format=sbb1&size_plain=-1&sha256=" + "a" * 64])
def test_bad_fields_are_refused(server, q):
    phone = _paired(server)
    conn = http.client.HTTPConnection(*server, timeout=10)
    conn.request("POST", "/local/backup?" + q, body=b"x", headers=_auth(phone))
    res = conn.getresponse()
    assert res.status == 400, res.read()
    conn.close()


def test_over_the_cap_is_413_unread(server, monkeypatch):
    monkeypatch.setenv("MEDIA_LOCAL_BACKUP_MAX_MB", "1")
    phone = _paired(server)
    # Says 3 MB, sends 1 KB: refused on the header, before a byte is read.
    st, got = upload(server, phone, b"x" * 1024, length=3 << 20)
    assert st == 413 and got["code"] == "too_big"
    assert not local_backup.root().exists() or not list(local_backup.root().glob("*/*"))
    st, got = upload(server, phone, os.urandom((1 << 20) - 1))
    assert st == 200, got


def test_keeps_the_newest_n_per_device(server, monkeypatch):
    monkeypatch.setenv("MEDIA_LOCAL_BACKUP_KEEP", "3")
    phone, other = _paired(server), _paired(server, name="Tablet")
    st, theirs = upload(server, other, b"tablet", created=1763000000)
    assert st == 200
    ids = []
    for i in range(5):
        st, got = upload(server, phone, f"bundle {i}".encode(), created=1763400000 + i)
        assert st == 200, got
        ids.append(got["id"])
    assert got["kept"] == 3
    mine = [b for b in listed(server, phone) if b != theirs["id"]]
    assert mine == list(reversed(ids[2:]))
    assert theirs["id"] in listed(server, phone)      # pruning is per device
    folder = local_backup.root() / ids[0].split(".")[0]
    assert len(list(folder.glob("*.sbb"))) == 3 and len(list(folder.glob("*.json"))) == 3


# --- who sees which -------------------------------------------------------------

def test_listing_is_scoped_by_device_and_account(server):
    admin = _paired(server, name="Desk")
    lone = _paired(server, role="member", name="Lone")
    ana1, ana2 = _enrolled("ana", name="Ana's phone"), _enrolled("ana", name="Ana's tablet")
    ben = _enrolled("ben", name="Ben's phone")
    ids = {}
    for who, tok in (("lone", lone), ("ana1", ana1), ("ana2", ana2), ("ben", ben)):
        st, got = upload(server, tok, who.encode())
        assert st == 200, got
        ids[who] = got["id"]
    assert set(listed(server, admin)) == set(ids.values())
    assert listed(server, lone) == [ids["lone"]]
    assert set(listed(server, ana1)) == {ids["ana1"], ids["ana2"]}
    assert set(listed(server, ana2)) == {ids["ana1"], ids["ana2"]}
    assert listed(server, ben) == [ids["ben"]]
    res, obj = call(server, "GET", "/local/backups", headers=_auth(ana1))
    row = next(b for b in obj["backups"] if b["id"] == ids["ana2"])
    assert {"id", "device", "device_name", "created", "size", "sha256"} <= set(row)
    assert row["device_name"] == "Ana's tablet" and row["sha256"] == hashlib.sha256(b"ana2").hexdigest()
    # Not theirs to read or delete: the same 404 as one that is not there.
    assert fetch(server, ben, ids["ana1"])[0].status == 404
    assert delete(server, ben, ids["ana1"])[0].status == 404
    assert delete(server, lone, ids["ana1"])[0].status == 404
    assert fetch(server, ana2, ids["ana1"])[1] == b"ana1"
    assert fetch(server, admin, ids["ben"])[1] == b"ben"


def test_a_reinstalled_phone_finds_its_old_pairings_bundles(server):
    old = _enrolled("cat")
    st, got = upload(server, old, b"before the reinstall")
    devices.revoke(devices.lookup(old)["id"])
    new = _enrolled("cat")
    assert listed(server, new) == [got["id"]]
    assert fetch(server, new, got["id"])[1] == b"before the reinstall"


def test_a_guest_is_refused(server):
    guest = _paired(server, role="guest")
    owner = _paired(server)
    st, got = upload(server, owner, b"x")
    assert upload(server, guest, b"x")[0] == 403
    res, obj = call(server, "GET", "/local/backups", headers=_auth(guest))
    assert res.status == 403 and obj["code"] == "guest"
    assert fetch(server, guest, got["id"])[0].status == 403
    assert delete(server, guest, got["id"])[0].status == 403
    res, obj = call(server, "GET", "/local/backups")
    assert res.status == 401


# --- download and delete --------------------------------------------------------

def test_download_honours_range(server):
    phone = _paired(server)
    body = os.urandom(10_000)
    _, got = upload(server, phone, body)
    res, raw = fetch(server, phone, got["id"])
    assert res.status == 200 and raw == body
    assert res.getheader("Content-Type") == "application/octet-stream"
    assert res.getheader("X-Backup-Sha256") == hashlib.sha256(body).hexdigest()
    assert res.getheader("Accept-Ranges") == "bytes"
    res, raw = fetch(server, phone, got["id"], "bytes=4000-")
    assert res.status == 206 and raw == body[4000:]
    assert res.getheader("Content-Range") == f"bytes 4000-9999/{len(body)}"
    res, _ = fetch(server, phone, got["id"], "bytes=20000-")
    assert res.status == 416
    assert fetch(server, phone, "d_nope.1763400000-0123abcd")[0].status == 404
    assert fetch(server, phone, "..%2F..%2Fdevices.json")[0].status == 404


def test_delete(server):
    phone = _paired(server)
    _, got = upload(server, phone, b"to go")
    res, obj = delete(server, phone, got["id"])
    assert res.status == 200 and obj == {"ok": True, "id": got["id"], "deleted": True}
    assert listed(server, phone) == []
    assert fetch(server, phone, got["id"])[0].status == 404
    assert delete(server, phone, got["id"])[0].status == 404


def test_preflight_allows_delete(server):
    res, _ = call(server, "OPTIONS", "/local/backup/d_abc.1763400000-0123abcd")
    assert res.status == 204
    assert "DELETE" in res.getheader("Access-Control-Allow-Methods")


def test_nothing_of_the_contents_is_logged(server, capfd):
    phone = _paired(server)
    marker = b"SECRET-CHAT-TEXT-" + os.urandom(8).hex().encode()
    upload(server, phone, marker)
    upload(server, phone, marker, sha="0" * 64)
    _, got = upload(server, phone, marker + b"!")
    fetch(server, phone, got["id"])
    out, err = capfd.readouterr()
    assert marker.decode() not in out + err


def test_features_say_so(server):
    res, obj = call(server, "GET", "/features", headers=_auth(_paired(server)))
    assert obj["local_backup"] is True
