# A tune after the reply, picked for what it said

David, 3 Nov 2026: "Can we play a tune on Spotify after a reply chosen by
the reply's relevance? Is that legal in a distributable package?" — "yes
please" (to writing this up).
Status: **proposed**.

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

**Yes, in the form above**, with two lines not to cross.

1. **Nothing from Spotify goes into a model.** Spotify's Developer Terms
   (v10, in force since 15 May 2025) forbid training a model on Spotify
   Content *or otherwise ingesting it into one*. In the design above the
   model never sees Spotify data: the agent writes the song line itself,
   and code takes Spotify's top search hit. Showing the model Spotify's
   search results, the listener's playlists, saved tracks or recently played
   to choose from would cross that line. Any such variant is a
   `personal_use` method (`docs/decisions/2026-11-02-personal-use.md`).
   Whether runtime use without training counts as "ingesting" is not
   settled publicly, which is why the safe design avoids it entirely.
2. **Spotify plays in Spotify's player, on its own.** No mixing it with,
   ducking it under, or recording it with our speech; no clipping a song to
   a jingle. The Connect route tells the listener's device to play, and
   that is all.

Hand-off involves no Spotify developer terms at all: the listener's own app
plays a search the listener could have typed.

**The quota is a practical limit, not a legal one.** A Spotify developer app
in development mode serves a handful of hand-listed users. Extended access
has been only for organisations with ≥250k monthly users since May 2025. So a
distributable package has each install register its own Spotify app (as
`music_accounts` already expects, `spotify_client_id()`). A hosted
sasonica.com cannot offer the Connect route to the public, but it can offer
hand-off.

**spotifyd** (an unofficial Spotify Connect speaker built on librespot)
stays as it is: host setup on red5, never in this repository.

## Steps

1. The marker: parse `[[tune: …]]`, strip it from speech and display, record
   it on the reply. Add the agent note and the cooldown.
2. Play through hand-off and the existing radio routes, after the speech
   ends (the same point the reply's speech sink reports done).
3. The Spotify Connect route: `spotify_remote.search_track(artist, title)`
   → `play(uri, device)`, reusing `devices()` and the `choose` answer.
4. The switch: Settings → Music, "A tune after some replies", off by default.
   `GET /features` says which routes are there, so the app stays generic.

## Open questions

- The cooldown, and whether it applies per thread or across the whole house.
- Whether the reply card shows the tune (a small "♪ Artist – Title" line
  that opens it in the listener's app). Probably yes; it is the reply's own
  text.

Sources: Spotify Developer Terms v10 as reported by
[Hypebot](https://www.hypebot.com/updated-spotify-terms-mean-you-cant-use-their-data-but-they-can-use-yours/)
and the [Vorp Labs tracker](https://vorplabs.com/agent-tools/spotify-api-changes)
(check developer.spotify.com's own terms page before building step 3);
quota modes per `2026-09-29-licensed-music.md`.
