# Parity: the messaging chat list

The first parity sweep (David, 6 Nov 2026: catch this class of bug by
comparing with established clients, not by hitting it ourselves; the bug
that started it was a twenty-name Messenger group filling its row, fixed in
fcaefab5). One area per Monday, rotating: messaging, then contacts,
audiobooks, music. See `README.md` here for the format.

References, from their public behaviour and docs only (no code read or
copied): Element (and the Matrix spec's room name calculation, client-server
API "Calculating the display name for a room"), Signal, Telegram, Google
Messages.

What was checked: the server's Matrix rows (`packages/server/src/agent_media_server/matrix.py`,
`Room.title` and `rows`) and the app's chat list (sasonica-app
`app/routes/threads.tsx` row, `app/app.css` `.thread-row .title`).
Server tests: `packages/server/tests/test_parity_messaging.py` (a known gap
is a strict xfail, so its fix turns it into a pass and fails the run until
this table is updated).

Swept 10 Nov 2026.

| # | Edge case | Reference behaviour | Ours | Pinned by |
|---|---|---|---|---|
| M1 | Big unnamed group (20 members) | Element: "Alice, Bob and 18 others" | pass: three first names of whoever wrote last, "+N" | `test_matrix_rooms.py::test_a_big_unnamed_group_is_titled_by_a_few_first_names` |
| M2 | Empty `m.room.name` | Spec: treated as no name, members used | pass | `test_an_empty_name_is_named_from_its_members` |
| M3 | Blank name (spaces only) | Expected: treated as no name (members used) | **fail**: the row's title is blank | xfail `test_a_blank_name_is_named_from_its_members` |
| M4 | Placeholder senders ("Facebook user") | Element: the name as given, never repeated | pass when real names exist (skipped for them); **fail** when every member is a placeholder: "Facebook, Facebook, Facebook +1" | `test_a_big_group_skips_a_placeholder_for_a_real_name`; xfail `test_a_group_of_only_placeholders_does_not_repeat_one` |
| M5 | Emoji in a name | Element/Signal show the name whole | pass for a small group; **fail** in a big group when the name starts with an emoji ("🎉 Pete Rose" gives "🎉") | xfail `test_an_emoji_led_name_in_a_big_group_gives_a_word` |
| M6 | Very long name | All: one line, ellipsis | pass: server sends it whole, app ellipsises; preview capped at 300 characters | `test_a_long_name_reaches_the_app_whole_and_the_preview_is_capped` |
| M7 | RTL names (Hebrew, Arabic) | Element: `dir="auto"` per name, so mixed titles keep their order and alignment | server pass (first name taken right); **app untested**: the title span has no `dir="auto"` or `<bdi>`, so a mixed title ("שרה, Bob +2") may reorder | server: `test_an_rtl_name_passes_through_and_gives_its_first_name`; app: build item A1 |
| M8 | A room with only the owner (everyone else left) | Element: "Empty room" (or "Empty room (was Alice)") | **fail**: titled by its raw room id, "!abc:ryer.org" | xfail `test_a_room_with_only_the_owner_is_not_titled_by_its_id` |
| M9 | Two members with one display name | Element: disambiguates with the Matrix id | **fail**: "Sam Lee, Sam Lee" | xfail `test_two_members_of_one_name_are_told_apart` |
| M10 | Unnamed room with a canonical alias | Spec: name, then canonical alias, then members | **fail**: the alias is not read (rare for bridged rooms) | xfail `test_an_unnamed_room_with_an_alias_is_titled_by_it` |
| M11 | Member with no display name | Spec/Element: the Matrix id | pass | `test_a_member_with_no_display_name_shows_as_their_id` |
| M12 | Unread count over 99 | Signal/Telegram: "99+" badge | n/a: we show a dot, not a count (a choice, not a gap) | none |
| M13 | Very old timestamp | Google Messages/Signal: a date past a week ("12 Mar 2022"), a weekday within it | **app fail**: "1552d" (`ago` in threads.tsx has no date step) | app: build item A2 |
| M14 | Future timestamp (clock skew) | Clamped to "now" | **app fail**: shows "1m" (rounds a negative age up to one minute); readable but wrong | app: build item A2 |
| M15 | No timestamp (`origin_server_ts` 0) | No time shown | pass: `at` 0, the app shows nothing | `test_a_message_with_no_timestamp_gives_no_time` |
| M16 | Duplicate titles across rooms (one person on SMS and Signal) | Google Messages: one conversation per person | pass: People folds a person's rooms into one row (contact match); with People off the service chip tells them apart | app `lib/messagesView.ts` People; no server test |
| M17 | Muted room's unread badge | Telegram: grey badge; Signal: muted icon, badge kept | **app untested**: a quiet room shows the quiet mark and the same unread dot | app: build item A3 |
| M18 | Room the owner left | Element: moves to Historical | pass: Leave archives it (`room_group.session_leave`) | `test_room_group.py::test_rename_and_leave_a_group` |
| M19 | Room the bridge deleted (portal closed) | WhatsApp/Messenger: one chat continues | pass: the closed portal's messages are read into the new room, one row | `test_matrix_rooms.py::test_a_portal_closed_while_both_are_held_becomes_one_chat` and neighbours |
| M20 | Placeholder sender in the preview line | Element: "Facebook user: hello" | pass | `test_a_placeholder_senders_preview_names_them_as_the_bridge_does` |

## Build items from this sweep

Server (each has its xfail; the fix turns it into a pass, then mark the row
pass here):

- **S1 (M3, M8, M10)** `Room.title`: strip the name; read
  `m.room.canonical_alias` before members; an owner-only room is "Empty
  room" (a key the app can translate is better than English from the
  server: a `title_kind: "empty"` beside the title, say).
- **S2 (M4, M9)** never repeat a name in a title: placeholders collapse
  ("Facebook user +3"), a shared display name gets its id's local part.
- **S3 (M5)** a big group's first name is the first word with a letter in it.

App (sasonica-app, notes for an app spec):

- **A1 (M7)** `dir="auto"` on the row title (or each name in a `<bdi>`); a
  spec with a Hebrew and a mixed title.
- **A2 (M13, M14)** `ago` in `routes/threads.tsx`: a weekday within a week,
  a date past it; clamp a future time to now. The other `ago` copies
  (home, person, share, org) want the same and could share one helper.
- **A3 (M17)** decide how a quiet room's unread shows (a muted dot, as
  Telegram greys its badge) and pin it in a spec.

## Next

Next Monday: contacts. Carried from the plan's notes, for the agent chat
sweep when it comes round: a huge tool result in the stream (paseo
truncates oversized entries) and the composer's IME Enter (omnara's fix).
