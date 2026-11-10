# Parity sweeps

One file per area of the app, each a table of edge cases checked against
established clients (Element, Signal, Telegram, Google Messages for
messaging; the equivalent for each area). Ideas only, from their public
behaviour and docs: no code is read or copied from them.

Each row: the edge case, what a reference app does, ours as pass, fail,
untested or n/a, and the test that pins it. A server gap is a strict
`xfail` in `packages/server/tests/test_parity_<area>.py`, so fixing it
fails the run until the row and the marker are updated together. An app gap
is a build item for sasonica-app, named at the foot of the area's file.

Rotation, one area a Monday: messaging (`messaging.md`, 10 Nov 2026),
contacts, audiobooks, music.
