"""People made here (contacts_manual.py, the `manual` records): a person
added by hand (`POST /contacts`) and a .vcf imported (`POST /contacts/import`)
in a book with no source (contacts sweep, 24 Nov 2026, K1/K2). HOME, the
config dir and the state dir are throwaway; no MEDIA_* or MATRIX_* setting
of this host leaks in; no phone, no Google, no CardDAV book."""

from __future__ import annotations

import json
import os

import pytest

from agent_media_server import auth, contacts, contacts_manual, devices, matrix
from test_contract import call, server, typed  # noqa: F401

ISS = "https://cms.test"

VCF = """BEGIN:VCARD\r
VERSION:3.0\r
UID:ana-1\r
FN:Ana Example\r
N:Example;Ana;;;\r
TEL;TYPE=CELL:0412 345 678\r
EMAIL;TYPE=HOME:ana@example.test\r
END:VCARD\r
BEGIN:VCARD\r
VERSION:3.0\r
FN:Bo Sample\r
TEL;TYPE=WORK:+61 3 9000 1111\r
ORG:Sample Co\r
END:VCARD\r
BEGIN:VCARD\r
VERSION:4.0\r
UID:cy-3\r
FN:Cy Test\r
EMAIL:cy@example.test\r
NOTE:met at the market\r
END:VCARD\r
BEGIN:VCARD\r
VERSION:3.0\r
X-ADDRESSBOOKSERVER-KIND:group\r
FN:Friends\r
X-ADDRESSBOOKSERVER-MEMBER:urn:uuid:ana-1\r
END:VCARD\r
"""


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    for k in list(os.environ):
        if k.startswith(("MATRIX_", "MEDIA_", "RELAY_")):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("MEDIA_CONTACTS_ADB", str(tmp_path / "no-such-adb") + " shell")
    monkeypatch.setenv("MEDIA_CONTACTS_PHOTOS", "0")
    monkeypatch.setattr(matrix, "_ROOMS", {})
    contacts._reset_for_tests()

    def nowhere(*a, **k):
        raise AssertionError(f"a real Google request: {a[:2]}")
    monkeypatch.setattr(contacts, "_request", nowhere)
    yield
    auth._REQ.__dict__.clear()
    contacts._reset_for_tests()


def _enrol(sub, email):
    return devices.enrol_account({"iss": ISS, "sub": sub, "email": email}, "phone")["token"]


def H(tok):
    return {"Authorization": f"Bearer {tok}"}


def _post_raw(addr, path, data, headers):
    import http.client

    conn = http.client.HTTPConnection(*addr, timeout=10)
    conn.request("POST", path, body=data, headers=headers)
    res = conn.getresponse()
    raw = res.read()
    conn.close()
    return res, json.loads(raw)


def _names(addr, tok):
    res, obj = call(addr, "GET", "/contacts", headers=H(tok))
    assert res.status == 200
    return sorted(c["name"] for c in obj["contacts"])


# --- the module ------------------------------------------------------------------

def test_a_vcard_is_known_by_its_start_or_name():
    assert contacts_manual.looks_like_vcard(b"\xef\xbb\xbf\r\nBEGIN:VCARD\r\n")
    assert contacts_manual.looks_like_vcard(b"begin:vcard\n")
    assert contacts_manual.looks_like_vcard(b"junk", "contacts.VCF")
    assert not contacts_manual.looks_like_vcard(b"PK\x03\x04", "facebook.zip")


def test_a_hand_made_card_names_someone():
    c = contacts_manual.card_from({"name": "Ana Example", "phones": "0412 345 678, 03 9000 1111",
                                   "emails": ["ana@example.test"]}, "AU")
    assert c["name"] == "Ana Example"
    assert [p["value"] for p in c["phones"]] == ["+61412345678", "+61390001111"]
    assert c["names"][0]["given"] == "Ana" and c["names"][0]["family"] == "Example"
    assert contacts_manual.card_from({"phones": ["0412 345 678"]}, "AU")["name"] == "0412 345 678"
    with pytest.raises(ValueError):
        contacts_manual.card_from({"name": "  "}, "AU")
    with pytest.raises(ValueError):
        contacts_manual.card_from({"name": "X", "emails": "not-an-address"}, "AU")


def test_a_vcf_makes_its_people_once_however_often_imported():
    got = contacts.import_export(VCF.encode(), "book.vcf")
    assert got["imported"] == {"vcard": 3} and got["people"] == 3 and got["contacts"] == 3
    names = sorted(r["name"] for r in contacts.listing()["contacts"])
    assert names == ["Ana Example", "Bo Sample", "Cy Test"]
    ana = next(r for r in contacts.listing("ana")["contacts"])
    assert ana["phones"] == ["+61412345678"] and ana["emails"] == ["ana@example.test"]
    contacts.import_export(VCF.encode(), "book.vcf")             # again: the same three
    assert contacts.listing()["total"] == 3
    card = contacts.card(contacts.contact_id("manual", "vcf:cy-3"))
    assert card is not None and card["notes"] == "met at the market"


def test_a_file_with_no_one_in_it_is_refused():
    from agent_media_server import contacts_import

    with pytest.raises(contacts_import.NotAnExport):
        contacts.import_export(b"BEGIN:VCARD\r\nVERSION:3.0\r\nEND:VCARD\r\n", "x.vcf")
    with pytest.raises(contacts_import.NotAnExport):
        contacts.import_export(b"not a card at all", "x.vcf")


def test_a_hand_made_person_sharing_a_number_joins_who_is_there():
    contacts.import_export(VCF.encode(), "book.vcf")
    got = contacts_manual.create({"name": "Ana E", "phones": ["+61 412 345 678"],
                                  "emails": "ana.work@example.test"})
    assert got["joined"] and got["id"] == contacts.contact_id("manual", "vcf:ana-1")
    assert contacts.listing()["total"] == 3
    card = contacts.card(got["id"])
    assert card["name"] == "Ana Example"                        # who was there wins
    assert [s["kind"] for s in card["sources"]] == ["manual", "manual"]


# --- the routes ------------------------------------------------------------------

def test_a_new_account_imports_adds_and_keeps_its_own(server):
    kat, lee = _enrol("kat", "k@x.test"), _enrol("lee", "l@x.test")
    assert _names(server, kat) == []
    res, obj = _post_raw(server, "/contacts/import?name=contacts.vcf", VCF.encode(),
                         {**H(kat), "Content-Type": "text/vcard"})
    assert res.status == 200 and obj["ok"] and obj["imported"] == {"vcard": 3}
    assert _names(server, kat) == ["Ana Example", "Bo Sample", "Cy Test"]
    res, obj = call(server, "POST", "/contacts",
                    {"name": "Dee Hand", "phones": ["0400 111 222"]}, headers=H(kat))
    assert res.status == 200 and obj["ok"] and not obj["joined"]
    assert obj["contact"]["name"] == "Dee Hand"
    assert obj["contact"]["phones"][0]["value"] == "+61400111222"
    assert _names(server, kat) == ["Ana Example", "Bo Sample", "Cy Test", "Dee Hand"]
    res, _ = call(server, "GET", f"/contacts/{obj['id']}", headers=H(kat))
    assert res.status == 200
    # Another account sees none of them.
    assert _names(server, lee) == []
    res, _ = call(server, "GET", f"/contacts/{obj['id']}", headers=H(lee))
    assert res.status == 404
    assert not contacts.db_path().exists()                     # the owner's book: untouched


def test_adding_nobody_is_a_400_and_the_route_is_gated(server):
    kat = _enrol("kat", "k@x.test")
    res, obj = call(server, "POST", "/contacts", {"name": ""}, headers=H(kat))
    assert res.status == 400 and not obj["ok"]
    res, _ = call(server, "POST", "/contacts", {"name": "Eve"})
    assert res.status in (401, 403)
    assert _names(server, kat) == []
