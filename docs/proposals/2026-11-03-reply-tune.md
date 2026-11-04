# A tune after the reply, picked for what it said

David, 3 Nov 2026: "Can we play a tune on Spotify after a reply chosen by
the reply's relevance? Is that legal in a distributable package?" — "yes
please" (to writing this up).
Status: **steps 1–4 built** (1–3 on 3 Nov 2026, `reply_tune.py`, off unless
`MEDIA_REPLY_TUNE=handoff|spotify`; 4 on 4 Nov 2026: the `tune` switch among
`/speech/music-bed`'s settings, off by default, under **Sing it out** on the
full player and in Settings → Music). David's doubts, 4 Nov: a song he was
not already listening to can take him away from his thoughts on the reply,
so it is his to switch on; it starts quiet, at the backing music level
("Use the speech versus music slider for the volume adjustment"): the
hand-off route lowers the phone's media volume to that share and puts it
back before the next reply speaks, the Spotify route sets the device's
volume. Next, David's "let's try both for now": the level slider under the
reply while its song plays, and in the mini player's song bar.
David, after this was written: "Yes, please... and read Spotify's official
terms" — read the same day (below), and they moved the Spotify route
behind the personal-use switch.

## The idea

When a reply ends, a song that fits it plays: a long fix finally green, and
something triumphant; a late-night reply on a hard problem, something calm.
It plays after the reply's speech ends, never under it, and only when the
user switched it on.

## Who picks: the agent, in its own words

The reply carries a marker, in the same way as `[[sound: done]]` and
`[[visual: …]]`:

```
[[tune: Artist - Title]]
```

The agent writes it from what it knows of music and of its own reply. It
works like the radio DJ's song lines (`radio_dj.py`). Nothing comes back
from a music service to the model. That is what keeps the public version
within Spotify's terms (below). The marker is parsed with `[[sound:]]`
(`intake/_text.py`, `intake/submit.py`, the server's `transcript.py`), and is
never shown or read aloud. `agent_notes.md` tells the agent when to use it:
rarely, at a moment that earns one, never on every reply. The server enforces
this with a cooldown: at most one tune per N minutes (the default to be
decided) and none while music is already playing.

The other way round (the server asks a small model for a song after reading
the reply) costs another model call, and picks less well than the agent
that wrote the reply. That stays an option if agents use the marker poorly.

## Where it plays: the routes the radio already has

The tune follows the radio's player setting (`handoff`, `phone`, `rooms`,
`sasonica`). It adds one route:

| Route | How | Who can use it |
|---|---|---|
| **Hand-off** (the product's route, `2026-09-29-licensed-music.md`) | the phone sends `MEDIA_PLAY_FROM_SEARCH` with artist and title to the listener's own music app | anyone with any music app |
| **Spotify Connect** (new) | `spotify_remote`: Web API search for `artist:… track:…`, then play the top hit on the listener's device | the listener signed in to Spotify (Premium); see the quota below |
| Rooms / phone via YouTube | the radio's existing path | personal-use only (`personal_use`), as today |

When the tune is over, playback stops and nothing resumes on its own.
If music was already playing, no tune plays.

## Is it legal in the package

Read on 3 Nov 2026 from Spotify's own pages: the Developer Terms ("Version
10, effective as of 15 May, 2025"), the Developer Policy (same date) and the
quota modes page.

**The hand-off route: yes.** It uses none of Spotify's developer platform:
the listener's own app plays a search they could have typed, under their
subscription. This is the product's route.

**The Spotify Connect route: personal use only** (`personal_use`
`spotify-reply-tune`, per `docs/decisions/2026-11-02-personal-use.md`).
These clauses leave it in doubt for a public product:

- Policy III.3: "Do not create a voice-enabled SDA that enables a user to
  control Spotify with their voice, or any kind of voice assistant that
  provides voice-control functionality." Sasonica is a voice assistant; here
  the agent rather than the listener's voice picks the song, but the clause
  is broad.
- Policy III.5: "Do not create any product or service which is integrated
  with streams or content from another service." Sasonica also plays
  YouTube, Audiobookshelf and radio.
- Policy II.5: "there shall be no playback of Spotify Content without
  showing relevant cover art and metadata in your SDA." With II.4.1–2
  (attribute with the Spotify Marks, link back), a public version would
  need a card with the art, the name and a link for every tune.
- Terms III.1.1 licenses an app "for private personal use" on "Approved
  Devices" (desktops, laptops, tablets, mobiles, "and such other devices
  that we approve in writing"); a server sending the commands is not listed.

These hold for the route even when private:

- **Nothing from Spotify goes into a model.** Terms IV.2.1 / Policy III.14:
  no "otherwise ingesting Spotify Content into a machine learning or AI
  model", with no carve-out for use at the moment of a request. Terms II.8
  defines Spotify Content to include "metadata, playlists, and user data",
  so search results count. The agent writes the song line; code takes the
  top hit (`spotify_remote.search_track`), and no model sees it.
- **No overlap.** Policy III.7: "Do not permit any device or system to
  segue, mix, re-mix, or overlap any Spotify Content with any other audio
  content." The tune starts after the speech; the next reply's speech must
  pause Spotify rather than duck it (open question below).
- **Not stored.** Terms IV.3.1: "Do not store Spotify Content indefinitely."
  `reply-tune.json` keeps only the last hit, for the cooldown.
- **Quota:** an app in development mode serves "up to 5 authenticated
  Spotify users", and needs its owner on Premium. Since 15 May 2025 extended
  mode is open only to organisations ("at least 250k MAUs"). Each install
  registers its own client ID (Terms VI.1.2–3: a separate code per app,
  never shared).

**spotifyd** (an unofficial Spotify Connect speaker built on librespot)
stays as it is: host setup on red5, never in this repository.

## Steps

What was built: `[[tune:]]` parsed (`intake/_text.extract_tune`) and taken
out of the speech and the thread (`transcript.display_text`); after
`submit_event` has said the reply, `reply_tune.after_reply` plays it, once
per cooldown (`MEDIA_REPLY_TUNE_COOLDOWN_MIN`, default 30), not while a
radio station or the route's player is playing; the agent note (`[tune]`)
told only where a route is on. Spotify device: `MEDIA_REPLY_TUNE_DEVICE`
(id or name), else the active one.

1. The marker: parse `[[tune: …]]`, strip it from speech and display, record
   it on the reply. Add the agent note and the cooldown.
2. Play through hand-off and the existing radio routes, after the speech
   ends (the same point the reply's speech sink reports done).
3. The Spotify Connect route: `spotify_remote.search_track(artist, title)`
   → `play(uri, device)`, reusing `devices()` and the `choose` answer.
4. The switch: Settings → Music, "A tune after some replies", off by default.
   `GET /features` says which routes are there, so the app stays generic.

## Related songs: the listener's own, by embedding (4 Nov 2026)

David, 4 Nov 2026: "in terms of playing songs that are related to the
reply, do you think the reply can be reworded to a point where it matches a
song too? We could even maybe use the agent-sessions links to find other
relations" — "I like it! and maybe we can use music brains or something
like that". Status: **`media tune related` built** (`tune_related.py`);
the agent note and the reply's last line (below) to follow.

**Not the reply bent to the song.** The reply's job is the answer, and
David's doubt of the same day stands: a song can take him away from his
thoughts on the reply. So the words are never reworded to fit a lyric. The
other way round is cheap and allowed: the agent picks the tune first, and
may close the reply with a line that leans into the title, only when it
lands naturally (a note in `agent_notes.md`, no code).

**The second picker: his own songs, nearest the words.** agent-sessions'
zettel layer (`zk_related.py`) embeds every note and memory with
nomic-embed-text through Ollama on red5 and writes each its neighbours.
`tune_related.py` does the same for the songs he has played most and
liked (`music_recent.listed`): each is a short text — artist, song, the
iTunes catalogue's genre (`media_meta`) and MusicBrainz's community tags
for the recording and its artist — embedded once and kept in
`tune-related.json`. `media tune related <words>` embeds the words
(`search_query:`) and prints the nearest songs; `--session <id>` takes the
words from that thread's last replies, so a thread can keep one theme
rather than each reply rolling dice. Without Ollama it falls back to word
overlap. `media tune index` brings the index up to the plays, asking
MusicBrainz for at most 20 new songs a run, one a second with the app's
User-Agent as its service asks (its data is CC0).

Why these songs rather than any the agent knows: they are familiar, which
answers the "takes me away" worry; and the corpus is his own history, not
a music service's search, so nothing from Spotify goes near a model
(Terms IV.2.1) and the `[tune]` note's bar on a service's results holds.

**Next:** the `[tune]` note points the agent at `media tune related`, so
it names one of his own songs when one sits near the reply and its own
pick otherwise; a `tune` route option that asks `related(reply)` itself
when the agent wrote no marker (the server-side picker the first section
set aside, now without a model call); the thread's zettel concepts as a
second query, once agent-sessions exposes them; ListenBrainz's similar
artists to widen a thin history. A thread's related songs could show in
the full player under Sing it out, as a list to tap.

## Open questions

- After the song ends, the listener's app may keep going (Spotify's
  autoplay). Whether a tune should stop when it ends is not settled yet.
- Whether the next reply's speech pauses the hand-off app or ducks it
  under (Android audio focus): it should pause. Check this on the phone.
- A reply that was cut off (barge-in, stop) still plays its tune today.

- The cooldown, and whether it applies per thread or across the whole house.
- Whether the reply card shows the tune (a small "♪ Artist – Title" line
  that opens it in the listener's app). Probably yes; it is the reply's own
  text.

Sources (read 3 Nov 2026):
[Developer Terms](https://developer.spotify.com/terms),
[Developer Policy](https://developer.spotify.com/policy),
[Quota modes](https://developer.spotify.com/documentation/web-api/concepts/quota-modes).
Terms §IX–X and Appendix A were not read word for word.
