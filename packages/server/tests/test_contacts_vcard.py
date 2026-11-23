"""The book as a vCard file (`GET /contacts/export.vcf`, contract §6.25
"Export"; contacts parity sweep 24 Nov 2026, K8). The file reads back,
through the CardDAV source's own parser, into the same people; each account
gets its own book only. Over real HTTP with real device tokens in a
throwaway state dir; no phone, no Google."""

from __future__ import annotations

import http.client

import pytest
import vobject

from agent_media_server import auth, contacts, contacts_carddav, contacts_vcard
from test_contacts_accounts import _book, _enrol, _paired, isolated  # noqa: F401
from test_contract import server, typed  # noqa: F401

ISS = "https://cms.test"

ANNA = {"name": "Anna Bell", "names": [{"display": "Anna Bell", "given": "Anna", "family": "Bell"}],
        "phones": [{"value": "+61412345678", "raw": "0412 345 678", "type": "Mobile"},
                   {"value": "+61398765432", "raw": "03 9876 5432", "type": "Work"}],
        "emails": [{"value": "anna@example.test", "type": "Home"}],
        "addresses": [{"formatted": "1 Main St, Carlton VIC 3053", "type": "Home"}],
        "organizations": [{"company": "Bell & Co", "title": "Director"}],
        "websites": [{"url": "https://anna.example.test", "type": "Homepage"}],
        "birthday": "--04-12", "notes": "Met at the market.\nLikes tea.",
        "labels": ["Family", "Melbourne"]}
NONAME = {"name": "+61 400 111 222", "names": [],
          "phones": [{"value": "+61400111222", "raw": "+61 400 111 222", "type": ""}],
          "emails": [], "addresses": [], "organizations": [], "notes": ""}


def _get(addr, path, tok=None):
    conn = http.client.HTTPConnection(*addr, timeout=10)
    conn.request("GET", path, headers={"Authorization": f"Bearer {tok}"} if tok else {})
    res = conn.getresponse()
    body = res.read()
    conn.close()
    return res, body


def _cards(text: str) -> list[dict]:
    return [contacts_carddav.card_of(vc) for vc in vobject.readComponents(text)]


@pytest.fixture
def books():
    _book(("google", "people/c1", ANNA, {"etag": "e1"}),
          ("google", "people/c2", NONAME, {"etag": "e2"}))
    kat = auth.account_key_of(ISS, "kat")
    with contacts.as_account(kat):
        _book(("google", "people/c9", dict(NONAME, name="Kat's Friend",
                                           names=[{"display": "Kat's Friend"}]), {"etag": "e9"}))
    return kat


def test_the_book_reads_back_as_the_same_people(books):
    text = contacts_vcard.export()
    got = {c["name"]: c for c in _cards(text)}
    assert set(got) == {"Anna Bell", "+61 400 111 222"}
    a = got["Anna Bell"]
    assert a["names"] == [{"display": "Anna Bell", "given": "Anna", "family": "Bell"}]
    assert [(p["value"], p["type"]) for p in a["phones"]] == \
        [("+61412345678", "Mobile"), ("+61398765432", "Work")]
    assert a["emails"] == [{"value": "anna@example.test", "type": "Home"}]
    assert a["addresses"] == [{"formatted": "1 Main St, Carlton VIC 3053", "type": "Home"}]
    assert a["organizations"] == [{"company": "Bell & Co", "title": "Director"}]
    assert a["websites"] == [{"url": "https://anna.example.test", "type": "Homepage"}]
    assert a["birthday"] == "--04-12"
    assert a["notes"] == "Met at the market.\nLikes tea."
    vc = next(v for v in vobject.readComponents(text) if v.fn.value == "Anna Bell")
    assert vc.categories.value == ["Family", "Melbourne"]
    assert vc.uid.value == contacts.contact_id("google", "people/c1")
    assert "PHOTO" not in text


def test_a_photo_goes_in_only_when_asked(books, monkeypatch):
    from agent_media_server import contacts_photos

    jpeg = b"\xff\xd8\xff\xe0" + b"x" * 40
    monkeypatch.setattr(contacts_photos, "photo",
                        lambda cid, size="": (jpeg, "image/jpeg", "e", 0.0))
    vc = next(v for v in vobject.readComponents(contacts_vcard.export(photos=True))
              if v.fn.value == "Anna Bell")
    assert vc.photo.value == jpeg and vc.photo.params["TYPE"] == ["JPEG"]
    assert contacts_carddav.photo_of(vc) == jpeg


def test_each_account_downloads_its_own_book(server, books):  # noqa: F811
    res, body = _get(server, "/contacts/export.vcf", _paired())
    assert res.status == 200
    assert res.getheader("Content-Type") == "text/vcard; charset=utf-8"
    assert res.getheader("Content-Disposition").startswith('attachment; filename="contacts-')
    assert sorted(c["name"] for c in _cards(body.decode())) == ["+61 400 111 222", "Anna Bell"]
    kat = _enrol("kat", "k@x.test")
    res, body = _get(server, f"/contacts/export.vcf?access_token={kat}")
    assert res.status == 200 and [c["name"] for c in _cards(body.decode())] == ["Kat's Friend"]
    res, _ = _get(server, "/contacts/export.vcf")
    assert res.status in (401, 403)


def test_no_book_is_an_empty_file(server):  # noqa: F811
    res, body = _get(server, "/contacts/export.vcf", _enrol("new", "n@x.test"))
    assert res.status == 200 and body == b""
