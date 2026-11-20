"""The CardDAV source and the edit written to it (contacts_carddav.py,
contacts_edit.edit_carddav). Nothing here reaches a real address book: a
small CardDAV server runs in this process on 127.0.0.1, with the members,
etags, sync tokens and conditional writes Radicale has."""

import base64
import hashlib
import json
import os
import re
import threading
import urllib.parse
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from agent_media_server import contacts, contacts_carddav as cd, contacts_edit, devices
from test_contract import call, server, typed  # noqa: F401

COLL = "/u/contacts/"
D = "{DAV:}"


class FakeBook:
    """Members by href; every change gets a new sync token (its count)."""

    def __init__(self):
        self.members: dict[str, str] = {}
        self.log: list[tuple[int, str]] = []     # (version, href) changed or gone
        self.version = 0
        self.requests: list[tuple[str, str, dict]] = []
        self.before_put = None                     # a hook: called with the href

    def etag(self, href):
        return '"' + hashlib.sha256(self.members[href].encode()).hexdigest()[:12] + '"'

    def set(self, href, text):
        self.members[href] = text
        self.version += 1
        self.log.append((self.version, href))

    def drop(self, href):
        self.members.pop(href, None)
        self.version += 1
        self.log.append((self.version, href))


def _ms(responses, token=None):
    tok = f"<sync-token>tok-{token}</sync-token>" if token is not None else ""
    return ('<?xml version="1.0"?><multistatus xmlns="DAV:" '
            'xmlns:C="urn:ietf:params:xml:ns:carddav">' + tok + "".join(responses)
            + "</multistatus>").encode()


def _resp(href, etag=None, data=None, gone=False):
    if gone:
        return f"<response><href>{href}</href><status>HTTP/1.1 404 Not Found</status></response>"
    ad = f"<C:address-data>{data.replace('&', '&amp;').replace('<', '&lt;')}</C:address-data>" \
        if data is not None else ""
    return (f"<response><href>{href}</href><propstat><prop><getetag>{etag}</getetag>{ad}"
            "</prop><status>HTTP/1.1 200 OK</status></propstat></response>")


def handler(book: FakeBook):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body=b"", headers=None):
            self.send_response(code)
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            return self.rfile.read(n) if n else b""

        def _auth(self):
            want = "Basic " + base64.b64encode(b"u:secret").decode()
            if self.headers.get("Authorization") != want:
                self._send(401)
                return False
            return True

        def do_REPORT(self):
            body = self._body()
            book.requests.append(("REPORT", self.path, dict(self.headers)))
            if not self._auth():
                return
            root = ET.fromstring(body)
            if root.tag == D + "sync-collection":
                tok = root.findtext(D + "sync-token") or ""
                if tok:
                    m = re.fullmatch(r"tok-(\d+)", tok)
                    if not m or int(m.group(1)) > book.version:
                        self._send(403, b'<error xmlns="DAV:"><valid-sync-token/></error>')
                        return
                    since = int(m.group(1))
                    hrefs = {h for v, h in book.log if v > since}
                else:
                    hrefs = set(book.members)
                out = [_resp(h, book.etag(h)) if h in book.members else _resp(h, gone=True)
                       for h in sorted(hrefs)]
                self._send(207, _ms(out, book.version))
                return
            # addressbook-multiget
            out = []
            for h in root.findall(D + "href"):
                href = urllib.parse.unquote(h.text)
                if href in book.members:
                    out.append(_resp(href, book.etag(href), book.members[href]))
            self._send(207, _ms(out))

        def do_PROPFIND(self):
            self._body()
            if not self._auth():
                return
            self._send(207, _ms([_resp(h, book.etag(h)) for h in sorted(book.members)]))

        def do_GET(self):
            book.requests.append(("GET", self.path, dict(self.headers)))
            if not self._auth():
                return
            href = urllib.parse.unquote(self.path)
            if href not in book.members:
                self._send(404)
                return
            self._send(200, book.members[href].encode(), {"ETag": book.etag(href)})

        def do_PUT(self):
            body = self._body().decode()
            href = urllib.parse.unquote(self.path)
            book.requests.append(("PUT", href, dict(self.headers)))
            if not self._auth():
                return
            if book.before_put:
                hook, book.before_put = book.before_put, None
                hook(href)
            im, inm = self.headers.get("If-Match"), self.headers.get("If-None-Match")
            if im and (href not in book.members or book.etag(href) != im):
                self._send(412)
                return
            if inm == "*" and href in book.members:
                self._send(412)
                return
            new = href not in book.members
            book.set(href, body)
            self._send(201 if new else 204, b"", {"ETag": book.etag(href)})

    return H


@pytest.fixture
def book():
    b = FakeBook()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler(b))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    b.url = f"http://127.0.0.1:{srv.server_port}{COLL}"
    yield b
    srv.shutdown()
    srv.server_close()


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
    contacts._reset_for_tests()
    monkeypatch.setattr(contacts_edit, "_later", lambda fn, *args: None)
    yield
    contacts._reset_for_tests()


@pytest.fixture
def on(book, monkeypatch, tmp_path):
    pw = tmp_path / "pw"
    pw.write_text("secret\n")
    monkeypatch.setenv("MEDIA_CARDDAV_URL", book.url)
    monkeypatch.setenv("MEDIA_CARDDAV_USER", "u")
    monkeypatch.setenv("MEDIA_CARDDAV_PASSWORD_FILE", str(pw))
    return book


PETER = "\r\n".join([
    "BEGIN:VCARD", "VERSION:3.0", "UID:peter-uid",
    "FN:Peter Ryan", "N:Ryan;Peter;;;",
    "TEL;TYPE=MOBILE:0412 345 678",
    "TEL;TYPE=THAILAND MOBILE:+66 81 234 5678",
    "EMAIL;TYPE=INTERNET,HOME:peter@example.test",
    "EMAIL;TYPE=INTERNET,OTHER:peter@hotmail.test",
    "IMPP;X-SERVICE-TYPE=Jabber:xmpp:peter%hotmail.test@msn.gajim.org",
    "IMPP;X-SERVICE-TYPE=MSN:msn:peter@hotmail.test",
    "ADR;TYPE=HOME:;;1 Main Rd;Donvale;VIC;3111;Australia",
    "LABEL;TYPE=HOME:1 Main Rd\\nDonvale VIC 3111",
    "ORG:Acme", "TITLE:Boss",
    "URL;TYPE=PROFILE:https://example.test/peter",
    "BDAY;X-APPLE-OMIT-YEAR=1604:1604-03-09",
    "NOTE:met at the springs",
    'X-GOOGLE-EXTRA:{"miscKeywords":[{"value":"a"\\,"type":"HOME"}]}',
    "X-GOOGLE-RESOURCE:people/c123",
    "END:VCARD", ""])


def group(uid, name, members, resource=""):
    lines = ["BEGIN:VCARD", "VERSION:3.0", f"UID:{uid}", f"FN:{name}", f"N:{name};;;;",
             "X-ADDRESSBOOKSERVER-KIND:group"]
    lines += [f"X-ADDRESSBOOKSERVER-MEMBER:urn:uuid:{m}" for m in members]
    if resource:
        lines.append(f"X-GOOGLE-RESOURCE:{resource}")
    return "\r\n".join(lines + ["END:VCARD", ""])


def fill(book):
    book.set(COLL + "peter.vcf", PETER)
    book.set(COLL + "fam.vcf", group("fam-uid", "Family", ["peter-uid"]))
    book.set(COLL + "work.vcf", group("work-uid", "Work", []))
    book.set(COLL + "star.vcf", group("star-uid", "starred", ["peter-uid"],
                                      "contactGroups/starred"))


def only_card():
    conn = contacts._connect()
    try:
        rows = conn.execute("SELECT id, card FROM contacts").fetchall()
    finally:
        conn.close()
    assert len(rows) == 1, rows
    return rows[0][0], json.loads(rows[0][1])


# --- reading --------------------------------------------------------------------

def test_off_until_the_url_is_set(book, monkeypatch):
    assert "carddav" not in contacts.sources()
    monkeypatch.setenv("MEDIA_CARDDAV_URL", book.url)
    assert contacts.sources()[0] == "carddav"
    monkeypatch.setenv("MEDIA_CONTACTS_SOURCES", "google")
    assert "carddav" not in contacts.sources()


def test_a_vcard_becomes_the_card_other_sources_make(on):
    fill(on)
    got = contacts.sync_carddav()
    assert got["full"] is True and got["changed"] == 4
    cid, c = only_card()
    assert c["name"] == "Peter Ryan"
    assert c["names"] == [{"display": "Peter Ryan", "given": "Peter", "family": "Ryan"}]
    assert c["phones"] == [
        {"value": "+61412345678", "raw": "0412 345 678", "type": "Mobile"},
        {"value": "+66812345678", "raw": "+66 81 234 5678", "type": "Thailand mobile"}]
    assert [e["value"] for e in c["emails"]] == ["peter@example.test", "peter@hotmail.test"]
    # The old MSN gateway address folds away, as Google's do.
    assert c["old_addresses"] == [{"address": "peter%hotmail.test@msn.gajim.org",
                                   "real": "peter@hotmail.test"}]
    assert c["addresses"] == [{"formatted": "1 Main Rd\nDonvale VIC 3111", "type": "Home"}]
    assert c["organizations"] == [{"company": "Acme", "title": "Boss"}]
    assert c["websites"] == [{"url": "https://example.test/peter", "type": "Profile"}]
    assert c["birthday"] == "--03-09" and c["notes"] == "met at the springs"
    # Labels from the group cards; Google's hidden starred is not one.
    assert c["labels"] == ["Family"]
    assert c["sources"][0]["kind"] == "carddav" and c["sources"][0]["id"] == "peter-uid"
    assert c["sources"][0]["href"] == COLL + "peter.vcf" and c["sources"][0]["etag"]
    assert cid == contacts.contact_id("carddav", "peter-uid")
    assert contacts.status()["carddav"]["sync_token"] is True


def test_changes_only_with_the_sync_token_and_a_lost_token_reads_it_whole(on):
    fill(on)
    contacts.sync_carddav()
    on.requests.clear()
    assert contacts.sync_carddav()["changed"] == 0
    assert len(on.requests) == 1                      # the REPORT, nothing fetched
    on.set(COLL + "work.vcf", group("work-uid", "Work", ["peter-uid"]))
    got = contacts.sync_carddav()
    assert got["full"] is False and got["changed"] == 1
    assert only_card()[1]["labels"] == ["Family", "Work"]
    on.drop(COLL + "fam.vcf")
    assert contacts.sync_carddav()["deleted"] == 1
    assert only_card()[1]["labels"] == ["Work"]
    # A token the server no longer knows: everything again, the gone dropped.
    conn = contacts._connect()
    contacts._set_meta(conn, "carddav_sync_token", "tok-999")
    conn.close()
    on.drop(COLL + "peter.vcf")
    on.log.clear()
    got = contacts.sync_carddav()
    assert got["full"] is True
    conn = contacts._connect()
    try:
        assert conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0] == 0
    finally:
        conn.close()


def test_unreachable_keeps_the_last_copy_and_says_why(on, monkeypatch):
    fill(on)
    contacts.sync_carddav()
    monkeypatch.setenv("MEDIA_CARDDAV_URL", "http://127.0.0.1:9/u/contacts/")
    with pytest.raises(cd.Unreachable):
        contacts.sync_carddav()
    assert only_card()[1]["name"] == "Peter Ryan"
    st = contacts.status()["carddav"]
    assert "cannot be reached" in st["last_error"]


def test_a_wrong_password_is_said(on, tmp_path):
    (tmp_path / "pw").write_text("nope")
    fill(on)
    with pytest.raises(cd.Unreachable, match="refused the sign-in"):
        contacts.sync_carddav()


def _seed(conn, google_card, android=True):
    recs = [("google", "people/c123", google_card, {"etag": "g1"})]
    if android:
        recs.append(("android", "lookup-1", google_card,
                     {"raw": [12], "accounts": ["com.google"], "version": 3}))
    for kind, sid, card, meta in recs:
        conn.execute("INSERT INTO records (kind, sid, card, meta) VALUES (?, ?, ?, ?)",
                     (kind, sid, json.dumps(card), json.dumps(meta)))
    contacts._rebuild(conn)


GOOGLE_PETER = {"name": "Pete R", "names": [{"display": "Pete R", "given": "", "family": ""}],
                "phones": [{"value": "+61412345678", "raw": "0412345678", "type": "Mobile"}],
                "emails": [{"value": "old@example.test", "type": "Home"}],
                "addresses": [], "organizations": [], "notes": "google's note",
                "labels": ["My Contacts", "Old label"]}


def test_a_person_keeps_their_id_and_the_book_wins(on):
    """The contact id the person had (the phone contact Google joined) is
    kept through the CardDAV import, so levels, overrides and Drupal links
    carry over; CardDAV's fields win over Google's and the phone's, even an
    empty one."""
    conn = contacts._connect()
    try:
        _seed(conn, GOOGLE_PETER)
        (old,) = [c for (c,) in conn.execute("SELECT id FROM contacts")]
        conn.execute("INSERT INTO person_level (contact, level, at) VALUES (?, 'quiet', 0)",
                     (old,))
    finally:
        conn.close()
    assert old == contacts.contact_id("android", "lookup-1")
    fill(on)
    on.set(COLL + "peter.vcf", PETER.replace("NOTE:met at the springs\r\n", ""))
    contacts.sync_carddav()
    cid, c = only_card()
    assert cid == old
    assert [s["kind"] for s in c["sources"]] == ["carddav", "google", "android"]
    assert c["name"] == "Peter Ryan" and c["notes"] == ""
    assert c["labels"] == ["Family"]                 # the book's labels, not Google's
    assert "peter@example.test" in [e["value"] for e in c["emails"]]
    assert "old@example.test" not in [e["value"] for e in c["emails"]]
    assert contacts.card(cid)["level"] == "quiet"
    # Once given, the id stays, whatever the phone and Google do later.
    conn = contacts._connect()
    try:
        conn.execute("DELETE FROM records WHERE kind IN ('android', 'google')")
        contacts._rebuild(conn)
    finally:
        conn.close()
    assert only_card()[0] == old


def test_a_google_person_on_their_own_keeps_theirs(on):
    conn = contacts._connect()
    try:
        _seed(conn, {**GOOGLE_PETER, "phones": []}, android=False)
    finally:
        conn.close()
    fill(on)
    contacts.sync_carddav()
    cid, c = only_card()
    assert cid == contacts.contact_id("google", "people/c123")
    assert [s["kind"] for s in c["sources"]] == ["carddav", "google"]


# --- writing --------------------------------------------------------------------

def _synced(on):
    fill(on)
    contacts.sync_carddav()
    return only_card()[0]


def test_the_card_is_editable_and_not_through_the_phone(on):
    cid = _synced(on)
    c = contacts.card(cid)
    assert c["edit"] is True and c["phone_edit"] is None


def test_fields_are_written_with_if_match_and_the_rest_kept(on):
    cid = _synced(on)
    etag = on.etag(COLL + "peter.vcf")
    got = contacts_edit.edit(cid, {
        "base": {"name": "Peter Ryan", "notes": "met at the springs"},
        "set": {"name": "Pete Ryan", "notes": "new note",
                "phones": [{"value": "0412 345 678", "type": "Work"}, "0400 111 222"],
                "emails": ["peter@example.test"],
                "addresses": [{"value": "2 Side St, Ivanhoe", "type": "Work"}],
                "organization": {"company": "Acme Pty", "title": ""},
                "birthday": "1980-01-26",
                "websites": []}})
    assert got["ok"] is True
    assert set(got["done"]) == {"name", "notes", "phones", "emails", "addresses",
                                "organization", "birthday", "websites"}
    puts = [r for r in on.requests if r[0] == "PUT"]
    assert len(puts) == 1 and puts[0][2]["If-Match"] == etag
    text = on.members[COLL + "peter.vcf"]
    assert "FN:Pete Ryan" in text and "N:Ryan;Pete;;;" in text
    assert "NOTE:new note" in text and "met at the springs" not in text
    assert "TEL;TYPE=WORK:0412 345 678" in text
    assert "TEL;TYPE=CELL:0400 111 222" in text and "+66" not in text
    assert "peter@hotmail.test" not in text.split("IMPP")[0]
    # The old gateway address and everything else not edited stays as it was.
    assert "xmpp:peter%hotmail.test@msn.gajim.org" in text
    assert "X-GOOGLE-EXTRA:" in text and "X-GOOGLE-RESOURCE:people/c123" in text
    assert "Donvale" not in text and "2 Side St" in text
    assert "ORG:Acme Pty" in text and "TITLE" not in text and "URL" not in text
    assert "BDAY:1980-01-26" in text
    # The store is synced straight after: the card shows it.
    c = contacts.card(cid)
    assert c["name"] == "Pete Ryan" and c["notes"] == "new note"
    assert c["phones"][0]["type"] == "Work" and c["birthday"] == "1980-01-26"


def test_a_birthday_without_a_year_and_nothing_to_change(on):
    cid = _synced(on)
    assert contacts_edit.edit(cid, {"set": {"birthday": "--03-09"}})["done"] == []
    contacts_edit.edit(cid, {"set": {"birthday": "--04-01"}})
    assert "BDAY;X-APPLE-OMIT-YEAR=1604:1604-04-01" in on.members[COLL + "peter.vcf"]
    assert contacts.card(cid)["birthday"] == "--04-01"


def test_a_field_changed_in_the_book_since_is_a_conflict(on):
    cid = _synced(on)
    on.set(COLL + "peter.vcf", PETER.replace("met at the springs", "changed on the phone"))
    with pytest.raises(contacts_edit.Conflict) as e:
        contacts_edit.edit(cid, {"base": {"notes": "met at the springs"},
                                 "set": {"notes": "mine"}})
    assert e.value.fields == {"notes": {"yours": "met at the springs",
                                        "theirs": "changed on the phone"}}
    assert not [r for r in on.requests if r[0] == "PUT"]


def test_a_412_looks_again(on):
    """A card changed between the sync and the PUT: another field's change is
    taken in and the write goes over it; the same field's is a conflict."""
    cid = _synced(on)
    href = COLL + "peter.vcf"
    on.before_put = lambda h: on.set(href, on.members[href].replace("ORG:Acme", "ORG:Other"))
    contacts_edit.edit(cid, {"base": {"notes": "met at the springs"}, "set": {"notes": "mine"}})
    text = on.members[href]
    assert "NOTE:mine" in text and "ORG:Other" in text
    on.before_put = lambda h: on.set(href, on.members[href].replace("NOTE:mine", "NOTE:theirs"))
    with pytest.raises(contacts_edit.Conflict) as e:
        contacts_edit.edit(cid, {"base": {"notes": "mine"}, "set": {"notes": "again"}})
    assert e.value.fields["notes"]["theirs"] == "theirs"


def test_labels_are_the_group_cards_members_and_a_new_one_is_made(on):
    cid = _synced(on)
    got = contacts_edit.edit(cid, {"base": {"labels": ["Family"]},
                                   "set": {"labels": ["Work", "Bloom"]}})
    assert got["done"] == ["labels"] and got["skipped"] == []
    assert "peter-uid" not in on.members[COLL + "fam.vcf"]
    assert "urn:uuid:peter-uid" in on.members[COLL + "work.vcf"]
    # Starred is not a label shown, so it is left as it was.
    assert "peter-uid" in on.members[COLL + "star.vcf"]
    new = [h for h, t in on.members.items() if "FN:Bloom" in t]
    assert len(new) == 1 and "X-ADDRESSBOOKSERVER-KIND:group" in on.members[new[0]]
    assert "urn:uuid:peter-uid" in on.members[new[0]]
    creates = [r for r in on.requests if r[0] == "PUT" and r[1] == new[0]]
    assert creates[0][2].get("If-None-Match") == "*"
    # The person's own card was not written for a labels-only change.
    assert not [r for r in on.requests if r[0] == "PUT" and r[1] == COLL + "peter.vcf"]
    assert contacts.card(cid)["labels"] == ["Bloom", "Work"]


def test_a_group_changed_meanwhile_still_gets_the_member(on):
    cid = _synced(on)
    href = COLL + "work.vcf"
    on.before_put = lambda h: on.set(href, group("work-uid", "Work", ["someone"]))
    contacts_edit.edit(cid, {"set": {"labels": ["Family", "Work"]}})
    text = on.members[href]
    assert "urn:uuid:someone" in text and "urn:uuid:peter-uid" in text


def test_the_address_book_down_is_503_and_a_412_conflict_is_409(server, on, monkeypatch):
    cid = _synced(on)
    code, _ = devices.mint_code("desk")
    tok = devices.redeem(code, "desk")["token"]
    H = {"Authorization": f"Bearer {tok}"}
    res, obj = call(server, "POST", f"/contacts/{cid}/edit",
                    {"base": {"notes": "stale"}, "set": {"notes": "new"}}, H)
    assert res.status == 409 and obj["conflict"]["notes"]["theirs"] == "met at the springs"
    res, obj = call(server, "POST", f"/contacts/{cid}/edit",
                    {"base": {"notes": "met at the springs"}, "set": {"notes": "new"}}, H)
    assert res.status == 200 and obj["done"] == ["notes"]
    monkeypatch.setenv("MEDIA_CARDDAV_URL", "http://127.0.0.1:9/u/contacts/")
    res, obj = call(server, "POST", f"/contacts/{cid}/edit", {"set": {"notes": "x"}}, H)
    assert res.status == 503 and obj["error"] == "the address book cannot be reached"


def test_pictures_are_kept_from_the_card(on):
    try:
        import io

        from PIL import Image

        out = io.BytesIO()
        Image.new("RGB", (400, 300), (200, 30, 30)).save(out, "JPEG")
        raw, pillow = out.getvalue(), True
    except ImportError:
        raw, pillow = b"\xff\xd8\xff\xe0" + b"\0" * 64, False   # kept as sent
    pic = base64.b64encode(raw).decode()
    on.set(COLL + "peter.vcf",
           PETER.replace("END:VCARD", f"PHOTO;ENCODING=b;TYPE=JPEG:{pic}\r\nEND:VCARD"))
    contacts.sync_carddav()
    from agent_media_server import contacts_photos

    cid, c = only_card()
    assert contacts.card(cid)["photo"]["url"] == f"/contacts/{cid}/photo"
    data, ctype, _, _ = contacts_photos.photo(cid)
    assert ctype == "image/jpeg" and data
    big, _, etag, _ = contacts_photos.photo(cid, "large")   # never fetched from anywhere
    assert etag.startswith("L") if pillow else big == data
    # A pass of the picture refresh keeps it (nothing to fetch for it).
    contacts_photos.refresh()
    assert contacts_photos.photo(cid) is not None
    on.set(COLL + "peter.vcf", PETER)
    contacts.sync_carddav()
    assert contacts.card(cid)["photo"] is None
