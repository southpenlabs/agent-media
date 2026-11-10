"""Several Google accounts in one book (contract §6.25 "Google, per book",
"Several accounts"; David, 10 Nov 2026: "multiple contact accounts"): each
sign-in's address adds an account or signs one in again, each account syncs
on its own, a person in two of them is one contact tagged with both, and one
account signing out or expiring leaves the others alone. The first account
is the single sign-in's own file and sids, so a live book's contact ids do
not move. Against a fake Google (no network), in a throwaway state dir."""

from __future__ import annotations

import base64
import json
import os
import urllib.parse

import pytest

from agent_media_server import auth, contacts, contacts_edit, devices, matrix
from test_contract import call, server, typed  # noqa: F401

ISS = "https://cms.test"


def _id_token(email):
    seg = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")  # noqa: E731
    return f"{seg({'alg': 'none'})}.{seg({'email': email})}.sig"


def _person(n, name, phones=(), emails=(), groups=(), deleted=False):
    p = {"resourceName": f"people/c{n}", "etag": f"e{n}"}
    if deleted:
        p["metadata"] = {"deleted": True}
        return p
    p["names"] = [{"displayName": name}]
    p["phoneNumbers"] = [{"value": v} for v in phones]
    p["emailAddresses"] = [{"value": v} for v in emails]
    if groups:
        p["memberships"] = [{"contactGroupMembership": {"contactGroupResourceName": g}}
                            for g in groups]
    return p


class FakeGoogle:
    """The token endpoint, the People API, its groups and revoke, as
    `_request` sees them; each refresh token is one Google account with
    its own people, sync token and changes."""

    def __init__(self):
        self.calls = []
        self.codes = {}            # code -> (email, refresh token)
        self.refusing = set()      # refresh tokens answered invalid_grant
        self.people = {}           # refresh token -> [person]
        self.changes = {}          # refresh token -> [person] for a sync token
        self.groups = {}           # refresh token -> {resource name: name}
        self.failing = set()       # refresh tokens whose People API answers 500

    def __call__(self, method, url, *, form=None, headers=None, timeout=20.0):
        self.calls.append((method, url, dict(form or {}), dict(headers or {})))
        if url.endswith("/token"):
            if form["grant_type"] == "authorization_code":
                email, refresh = self.codes[form["code"]]
                body = {"access_token": f"at-{refresh}", "expires_in": 3600,
                        "refresh_token": refresh, "scope": contacts.LOGIN_SCOPE}
                if email:
                    body["id_token"] = _id_token(email)
                return 200, body
            if form["refresh_token"] in self.refusing:
                return 400, {"error": "invalid_grant"}
            return 200, {"access_token": f"at-{form['refresh_token']}", "expires_in": 3600}
        if "/revoke" in url:
            return 200, {}
        who = headers["Authorization"].removeprefix("Bearer at-")
        if "/contactGroups" in url:
            return 200, {"contactGroups": [{"resourceName": k, "name": v}
                                           for k, v in self.groups.get(who, {}).items()]}
        assert "people/me/connections" in url, url
        if who in self.failing:
            return 500, {"error": {"code": 500, "message": "backend error"}}
        q = {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).items()}
        if q.get("syncToken"):
            assert q["syncToken"] == f"st-{who}", "another account's sync token"
            return 200, {"connections": self.changes.get(who, []),
                         "nextSyncToken": f"st-{who}"}
        return 200, {"connections": self.people.get(who, []), "nextSyncToken": f"st-{who}"}

    def people_calls(self, refresh):
        return [c for c in self.calls if "people/me/connections" in c[1]
                and c[3].get("Authorization") == f"Bearer at-{refresh}"]


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    for k in list(os.environ):
        if k.startswith(("MATRIX_", "MEDIA_", "RELAY_")):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("MEDIA_CONTACTS_ADB", str(tmp_path / "no-such-adb") + " shell")
    monkeypatch.setattr(matrix, "_ROOMS", {})
    contacts._reset_for_tests()
    yield
    auth._REQ.__dict__.clear()
    contacts._reset_for_tests()


@pytest.fixture
def google(monkeypatch):
    fake = FakeGoogle()
    monkeypatch.setattr(contacts, "_request", fake)
    path = contacts.client_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"installed": {"client_id": "cid.apps", "client_secret": "s"}}))

    def inline(key, aid=None):                 # the sync after a sign-in, inline
        with contacts.as_account(key):
            contacts.sync_google(full=True, account=aid)
    monkeypatch.setattr(contacts, "_kick_sync", inline)
    return fake


def H(tok):
    return {"Authorization": f"Bearer {tok}"}


@pytest.fixture
def kat():
    return devices.enrol_account({"iss": ISS, "sub": "kat", "email": "k@x.test"}, "phone")["token"]


def _sign_in(server, tok, google, email, refresh):
    res, obj = call(server, "POST", "/contacts/google/login", {"port": 48123}, headers=H(tok))
    assert res.status == 200, obj
    code = f"4/code-{refresh}"
    google.codes[code] = (email, refresh)
    res, obj = call(server, "POST", "/contacts/google/finish",
                    {"state": obj["state"], "code": code}, headers=H(tok))
    assert res.status == 200, obj
    return obj["google"]


PERSONAL = [_person(1, "Lionel Smith", ["0412 345 678"], groups=["contactGroups/fam"]),
            _person(2, "Mel Jones", ["+61 400 111 222"])]
WORK = [_person(1, "Lionel S. (work)", ["+61412345678"], ["lionel@work.test"],
                groups=["contactGroups/col"]),
        _person(7, "Priya Patel", [], ["priya@work.test"])]


def _two(server, kat, google):
    google.people = {"rt-me": PERSONAL, "rt-work": WORK}
    google.groups = {"rt-me": {"contactGroups/fam": "Family"},
                     "rt-work": {"contactGroups/col": "Colleagues"}}
    _sign_in(server, kat, google, "me@gmail.test", "rt-me")
    return _sign_in(server, kat, google, "davidj4phs@gmail.test", "rt-work")


def test_a_second_address_adds_an_account(server, kat, google):
    g = _two(server, kat, google)
    work = contacts.account_id_for("davidj4phs@gmail.test")
    assert [(a["id"], a["email"]) for a in g["accounts"]] == [
        ("main", "me@gmail.test"), (work, "davidj4phs@gmail.test")]
    for a in g["accounts"]:
        assert set(a) == {"id", "email", "signed_in", "expired", "signed_in_at", "last_sync",
                          "last_error", "contacts"}
        assert a["signed_in"] and not a["expired"] and a["last_sync"] and a["last_error"] is None
    assert [a["contacts"] for a in g["accounts"]] == [2, 2]
    # The old fields describe the first account; `contacts` is every record.
    assert g["signed_in"] and g["email"] == "me@gmail.test" and g["contacts"] == 4
    key = auth.account_key_of(ISS, "kat")
    with contacts.as_account(key):
        assert contacts.load_token()["refresh_token"] == "rt-me"
        path = contacts.token_path(work)
        assert path.parent.name == "google-contacts-tokens" and path.name == f"{work}.json"
        assert json.loads(path.read_text())["refresh_token"] == "rt-work"
        assert oct(path.stat().st_mode & 0o777) == "0o600"
    res, obj = call(server, "GET", "/contacts/google", headers=H(kat))
    assert obj["google"]["accounts"] == g["accounts"]


def test_a_person_in_both_accounts_is_one_contact_with_both(server, kat, google):
    _two(server, kat, google)
    work = contacts.account_id_for("davidj4phs@gmail.test")
    res, obj = call(server, "GET", "/contacts", headers=H(kat))
    rows = {r["name"]: r for r in obj["contacts"]}
    assert sorted(rows) == ["Lionel Smith", "Mel Jones", "Priya Patel"]
    lionel = rows["Lionel Smith"]
    assert lionel["google_accounts"] == ["main", work]
    # The first account's contact keeps its id, as before a second came.
    assert lionel["id"] == contacts.contact_id("google", "people/c1")
    assert rows["Mel Jones"]["google_accounts"] == ["main"]
    assert rows["Priya Patel"]["google_accounts"] == [work]
    assert rows["Priya Patel"]["id"] == contacts.contact_id("google", f"{work}:people/c7")
    # The earlier account's values win; labels are both accounts'.
    assert lionel["phones"] == ["+61412345678"] and lionel["emails"] == ["lionel@work.test"]
    assert lionel["labels"] == ["Colleagues", "Family"]
    res, obj = call(server, "GET", f"/contacts/{lionel['id']}", headers=H(kat))
    assert [(s["kind"], s["id"], s["account"], s["email"]) for s in obj["contact"]["sources"]] == [
        ("google", "people/c1", "main", "me@gmail.test"),
        ("google", f"{work}:people/c1", work, "davidj4phs@gmail.test")]


def test_the_list_narrows_to_one_account(server, kat, google):
    _two(server, kat, google)
    work = contacts.account_id_for("davidj4phs@gmail.test")
    res, obj = call(server, "GET", f"/contacts?account={work}", headers=H(kat))
    assert [r["name"] for r in obj["contacts"]] == ["Lionel Smith", "Priya Patel"]
    assert obj["total"] == 2
    res, obj = call(server, "GET", "/contacts?account=main&q=mel", headers=H(kat))
    assert [r["name"] for r in obj["contacts"]] == ["Mel Jones"] and obj["total"] == 1
    res, obj = call(server, "GET", "/contacts?account=nosuch", headers=H(kat))
    assert res.status == 200 and obj["contacts"] == [] and obj["total"] == 0


def test_each_account_syncs_on_its_own(server, kat, google):
    _two(server, kat, google)
    work = contacts.account_id_for("davidj4phs@gmail.test")
    google.changes = {"rt-work": [_person(7, "", deleted=True)]}
    key = auth.account_key_of(ISS, "kat")
    with contacts.as_account(key):
        got = contacts.sync_google()
        assert got["changed"] == 0 and got["deleted"] == 1
        assert set(got["accounts"]) == {"main", work}
        # Each with its own sync token (the fake refuses another's).
        assert "syncToken=st-rt-me" in google.people_calls("rt-me")[-1][1]
        assert "syncToken=st-rt-work" in google.people_calls("rt-work")[-1][1]
        names = [r["name"] for r in contacts.listing()["contacts"]]
    assert names == ["Lionel Smith", "Mel Jones"]
    # A failing account says so and does not stop the other.
    google.failing.add("rt-work")
    google.changes = {"rt-me": [_person(3, "Zoë Brown", ["0400 333 444"])]}
    with contacts.as_account(key):
        got = contacts.sync_google()
        assert set(got["errors"]) == {work} and got["changed"] == 1
    res, obj = call(server, "GET", "/contacts/google", headers=H(kat))
    a, b = obj["google"]["accounts"]
    assert a["last_error"] is None and "500" in b["last_error"] and b["signed_in"]
    assert obj["google"]["last_error"] is None


def test_an_expired_account_does_not_stop_the_other(server, kat, google):
    _two(server, kat, google)
    google.refusing.add("rt-me")
    google.changes = {"rt-work": [_person(8, "Sam Lee", ["0400 555 666"])]}
    key = auth.account_key_of(ISS, "kat")
    with contacts.as_account(key):
        contacts._access("main").clear()       # the hour is up: a refresh is needed
        got = contacts.sync()
        assert got["errors"] == {} and got["synced"][0]["errors"] == {
            "main": "Google no longer takes the sign-in (revoked, or the 7 days a "
                    "testing-mode sign-in lasts are up); sign in again"}
        assert "google" in contacts.sources()   # the other is still read
        # From then on the expired one is not tried.
        n = len(google.calls)
        contacts.sync()
        assert not [c for c in google.calls[n:] if c[2].get("refresh_token") == "rt-me"]
    res, obj = call(server, "GET", "/contacts/google", headers=H(kat))
    g = obj["google"]
    assert [(a["signed_in"], a["expired"]) for a in g["accounts"]] == [(False, True), (True, False)]
    assert g["expired"] and not g["signed_in"]       # the first account's, as before
    assert g["contacts"] == 5                         # the expired one's copy stays
    # Signing in again with that address replaces its token, not a new one.
    g = _sign_in(server, kat, google, "me@gmail.test", "rt-me-2")
    assert [(a["id"], a["signed_in"]) for a in g["accounts"]] == [
        ("main", True), (contacts.account_id_for("davidj4phs@gmail.test"), True)]


def test_signing_out_one_account_leaves_the_other(server, kat, google):
    _two(server, kat, google)
    work = contacts.account_id_for("davidj4phs@gmail.test")
    res, obj = call(server, "POST", "/contacts/google/signout", {"account": "main"},
                    headers=H(kat))
    assert res.status == 200
    g = obj["google"]
    assert [a["id"] for a in g["accounts"]] == [work] and g["contacts"] == 2
    assert g["email"] == "davidj4phs@gmail.test"     # the old fields: now the first one
    assert any("/revoke" in c[1] and c[2]["token"] == "rt-me" for c in google.calls)
    assert not any("/revoke" in c[1] and c[2]["token"] == "rt-work" for c in google.calls)
    res, obj = call(server, "GET", "/contacts", headers=H(kat))
    assert sorted((r["name"], tuple(r["google_accounts"])) for r in obj["contacts"]) == [
        ("Lionel S. (work)", (work,)), ("Priya Patel", (work,))]
    key = auth.account_key_of(ISS, "kat")
    with contacts.as_account(key):
        assert contacts.load_token() is None
        got = contacts.sync_google()           # the other keeps its own sync token
        assert got["full"] is False
    # An account the book does not hold: nothing to do.
    res, obj = call(server, "POST", "/contacts/google/signout", {"account": "nosuch"},
                    headers=H(kat))
    assert res.status == 200 and len(obj["google"]["accounts"]) == 1
    # A new address now is an added account, not the first file.
    google.people["rt-third"] = [_person(9, "Ann Third", ["0400 777 888"])]
    g = _sign_in(server, kat, google, "third@gmail.test", "rt-third")
    assert [a["email"] for a in g["accounts"]] == ["davidj4phs@gmail.test", "third@gmail.test"]
    # No account: every one goes.
    res, obj = call(server, "POST", "/contacts/google/signout", {}, headers=H(kat))
    assert obj["google"]["accounts"] == [] and obj["google"]["contacts"] == 0
    assert not obj["google"]["signed_in"]
    assert call(server, "GET", "/contacts", headers=H(kat))[1]["contacts"] == []


def test_a_live_single_sign_in_reads_on_unchanged(google):
    """The owner's book from before: one token file, records synced with
    bare resource names. Its contacts keep their ids when a second account
    comes, and a token with no address is still the first account."""
    contacts._save_token({"refresh_token": "rt-old", "scope": contacts.SCOPE,
                          "obtained_at": 1.0})          # the scope before `email`
    google.people = {"rt-old": PERSONAL, "rt-work": WORK}
    contacts.sync_google()
    ids = {r["name"]: r["id"] for r in contacts.listing()["contacts"]}
    assert ids == {"Lionel Smith": contacts.contact_id("google", "people/c1"),
                   "Mel Jones": contacts.contact_id("google", "people/c2")}
    assert [a["id"] for a in contacts.google_accounts()] == ["main"]
    # A sign-in with no address is that account signing in again...
    google.codes["4/a"] = ("", "rt-old-2")
    kept = contacts.exchange(contacts.load_client(), "4/a", "v", "http://127.0.0.1:1")
    assert kept["id"] == "main" and contacts.load_token()["refresh_token"] == "rt-old-2"
    google.people["rt-old-2"] = PERSONAL
    # ...and so is the first address to come, while the first has none.
    google.codes["4/b"] = ("me@gmail.test", "rt-me")
    google.people["rt-me"] = PERSONAL
    kept = contacts.exchange(contacts.load_client(), "4/b", "v", "http://127.0.0.1:1")
    assert kept["id"] == "main" and contacts.load_token()["email"] == "me@gmail.test"
    assert contacts.load_token()["added_at"] == 1.0      # its place in the order kept
    # A new address after that is an added account.
    google.codes["4/c"] = ("davidj4phs@gmail.test", "rt-work")
    kept = contacts.exchange(contacts.load_client(), "4/c", "v", "http://127.0.0.1:1")
    work = kept["id"]
    assert work == contacts.account_id_for("davidj4phs@gmail.test") != "main"
    contacts.sync_google(full=True)
    rows = {r["name"]: r for r in contacts.listing()["contacts"]}
    assert rows["Lionel Smith"]["id"] == ids["Lionel Smith"]
    assert rows["Mel Jones"]["id"] == ids["Mel Jones"]
    assert rows["Lionel Smith"]["google_accounts"] == ["main", work]
    # Cards made before accounts are made again from the kept records.
    conn = contacts._connect()
    try:
        contacts._set_meta(conn, "cards", "gateways-1")
    finally:
        conn.close()
    assert contacts.refold()


def test_status_and_the_editor_link_name_each_account(google, capsys):
    contacts._save_token({"refresh_token": "rt-me", "email": "me@gmail.test",
                          "obtained_at": 1.0})
    google.people = {"rt-me": PERSONAL, "rt-work": WORK}
    google.codes["4/c"] = ("davidj4phs@gmail.test", "rt-work")
    work = contacts.exchange(contacts.load_client(), "4/c", "v", "http://127.0.0.1:1")["id"]
    contacts.sync_google()
    s = contacts.status()
    assert [(a["id"], a["email"], a["contacts"]) for a in s["google"]["accounts"]] == [
        ("main", "me@gmail.test", 2), (work, "davidj4phs@gmail.test", 2)]
    assert s["google"]["accounts"][1]["token"].endswith(f"google-contacts-tokens/{work}.json")
    assert contacts.main(["status"]) == 0
    out = capsys.readouterr().out
    assert "google account main      me@gmail.test: signed in, 2 contacts" in out
    assert f"google account {work}  davidj4phs@gmail.test: signed in, 2 contacts" in out
    priya = contacts.card(contacts.contact_id("google", f"{work}:people/c7"))
    assert contacts_edit.google_url(priya) == (
        "https://contacts.google.com/person/c7?authuser=davidj4phs%40gmail.test")
    mel = contacts.card(contacts.contact_id("google", "people/c2"))
    assert contacts_edit.google_url(mel) == "https://contacts.google.com/person/c2"
