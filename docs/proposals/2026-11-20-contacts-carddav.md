# Contacts on CardDAV: Radicale as the book, Sasonica edits it

20 Nov 2026. Status: proposal. Follows docs/proposals/2026-10-17-contacts.md.

## Why

David, 20 Nov 2026: contacts should live on an open standard he hosts, not
in Google's People API. Agreed in the thread
(https://red2-ryer.sasonica.com/app/t/49483691-3cf2-4703-a222-7acaa9582a9b):

- **The book**: Radicale on red2 (`radicale`, `radicale-tunnel` user units;
  https://dav-red2-ryer.sasonica.com, address book `david/contacts`, store
  `~/.local/share/radicale/collections`, git-committed on every change).
  Loaded from Google the same day (2,160 people, 81 labels, photos) by
  `~/.config/radicale/google-to-radicale.py`.
- **Groups are group cards** (Apple's `X-ADDRESSBOOKSERVER-KIND:group` with
  `-MEMBER:urn:uuid:<uid>`), not `CATEGORIES`: they are objects with ids, as
  Google's labels are, can be renamed in one place and can be empty.
- **Drupal** keeps the business layer and links to a person by vCard `UID`.
- **Matrix** stays the messaging layer; a person's Matrix ids go on their
  card as `IMPP:matrix:@…`.

Today's edit path (contacts_edit.py, contacts_writeback.py) writes to Google
through the phone's contacts provider over adb. It works only while the phone
answers (today: "the phone did not answer in 180 s"), cannot make a label
Google lacks, and guards conflicts by comparing the card. CardDAV makes all of
that a server-side HTTP write with a real etag.

## Steps

1. **Read: a `carddav` source** (contacts_carddav.py). `sync-collection`
   (RFC 6578) with a stored sync token each interval, a full `PROPFIND` +
   `addressbook-multiget` the first time; each vCard parsed (vobject) into the
   card shape the other sources use (names, phones, emails, addresses,
   organisations, notes, urls, photo) and `labels` from the group cards that
   list it. Records `kind="carddav"`, id = vCard `UID`, with the href and
   etag. The contact id for a person Radicale knows becomes the uuid of
   `carddav:<UID>` (a one-off id map from the Google resource names the
   import kept in `X-GOOGLE-RESOURCE`, so cards, overrides, levels and Drupal
   links carry over). Precedence: carddav > google > android > drupal.
   Settings: `MEDIA_CARDDAV_URL`, `MEDIA_CARDDAV_USER`,
   `MEDIA_CARDDAV_PASSWORD_FILE`; `carddav` in `MEDIA_CONTACTS_SOURCES`.
2. **Edit: the same route, a CardDAV write.** `POST /contacts/<id>/edit`
   keeps its body (`base`/`set`) and answers; for a person with a carddav
   record, contacts_edit GETs the vCard, applies the change, and PUTs it with
   `If-Match: <etag>`. A 412 is the existing 409 conflict shape. Labels
   rewrite the group cards' member lists (each with its own etag); a label
   that does not exist is created, which Google's path could not do. The
   phone path stays for people only Android knows, until step 5.
3. **New things the book allows**: add a contact (PUT a new card), rename or
   delete a label, delete a contact (to Radicale's git history, so undoable).
   App: an Add button in People and a label editor; the card's "Open in
   Google Contacts" goes away for carddav people.
4. **Joins write to the card**, not Google: the network links
   contacts_writeback adds today become `URL` and `IMPP` entries (Matrix ids
   as `matrix:`), with the same only-adds rule.
5. **Cutover.** The phone syncs the book through DAVx⁵ (group-card mode);
   once David is happy, its Google contacts sync is switched off and the
   `google` source is dropped from `MEDIA_CONTACTS_SOURCES`, leaving Google
   as a frozen copy (or, if he wants, a one-way mirror from Radicale over the
   People API, signed in with `media contacts login --write`).
   Drupal's `seed` copies only people marked as clients, keyed by `UID`.

## Open source used

Radicale (GPLv3, run as a separate process, never imported) for the server;
vobject (Apache-2.0, already Radicale's dependency) to parse vCards;
DAVx⁵ on the phone. Looked at and not chosen: Baïkal/sabre-dav (PHP, MySQL;
its pull is sharing Drupal's database, which ties the address book to the
CRM), Xandikos (git-native, smaller community), Nextcloud (far heavier).

## Meanwhile

c72cf8e1 (20 Nov 2026) added `media contacts login --write`, the People
API's full `contacts` scope, so labels can be made from a chat (a Bloom
label). That stays the path until step 2 lands; after cutover such a write
goes to Radicale instead.

## Not doing

- Two-way sync between Radicale and Google. One book, one writer; Google is
  read until cutover and a mirror at most after it.
- Storing the book in Matrix or Drupal (see the thread for why).

## Open

- Whether to fold the 2,444 old MSN-gateway `IMPP:xmpp:` entries away on the
  card as "Old chat addresses" (as Google's are today) or drop them from the
  book once their decoded addresses are in place.
- Which of the 81 labels to keep: many are old location and project tags.
