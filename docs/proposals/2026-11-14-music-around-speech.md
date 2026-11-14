# Music around speech: one log, a chooser that reads it, and a DJ's timing

David, 14 Nov 2026: "Maybe we need to keep a log of Sing it out ... I'd like
more intelligence around song choice, including the backing music for
replies. Maybe even work some DJing techniques into how music is managed
around speech." — "Yes, write the proposal."

Nothing here is built. It came out of the footer work in the app (a song's
player could not be brought back once the song had stopped, because nothing
remembers it).

## What plays around a reply today

Five things, each choosing for itself:

- **The bed** under a long reply (`music_bed.choose`: 45 s or 12 sentences,
  nothing else playing). A generated mood by hour, weather and the astro
  axes; a library track, shuffled; a streaming track, the one played longest
  ago; or a favourite as an instrumental, drawn at random weighted by fit to
  the astro axes, by rotation, and by a boost for the one a fast model says
  suits the reply's words (`music_bed_favourites.track_for`). The last six
  sit out. Real recordings must measure slow (`music_bed_tempo`).
- **Sing it out** (`music_bed_vocal`): after a long reply whose bed was a
  favourite, that song's own original with its vocals, crossfaded in at the
  voice's end and run to the end of the next chorus.
- **Sing it in**: 3.5 s of the original before a long reply, timed so the
  first chorus peaks as the fade ends. Phone only, off by default.
- **The reply tune** (`reply_tune`): a song the agent names with
  `[[tune: …]]`, after the reply, at most one every half hour.
- **The radio DJ** (`radio_dj`): a model picks from the moment, what was
  played, likes and avoids.

The mixing (`music_bed_mix`) already has a DJ's hands on the level: the
music swells in the pauses (breathe), a 3 dB dip between 300 and 3000 Hz
makes room for the voice (carve), it starts a second and a half before the
first word (intro), a low-pass sweep on the fades, a held bed between
readings (carry), and a generated cadence to end on (outro).

## What is written down, and what is not

- A reply's history row has its bed (`extras.bed`, `extras.bed_song`).
- `music-sung.json` has the last 200 Sing it outs: started, and kept.
- Likes go to the store's `likes`; a dislike excludes the favourite.
- The reply tune keeps only its last time, for the half hour.
- The radio has its own files.

Not written anywhere: that a song ended or was stopped (the app knows,
`HandoverSongState.why`, and does not say), a skip or a turn of the volume
under a bed, a reply replayed for its music, any reply tune that played.
And no one place has all of it: five choosers, five memories.

## The changes

### 1. One log of music around speech

A table in the state store, `speech_music`, one row for each thing that
played: when; the reply (history id, thread); the kind (bed, sing in, sing
out, tune); the track (our own ids: the favourite's key, the library id,
artist and title); why it was chosen (the source, the mood, the topic
boost, the weights that won); how it was mixed (the switches on, the level,
where the handover started and ended); and how it ended.

"How it ended" is the part that is new: ran to its end, stopped (and how
far in), skipped, turned down or up, liked, disliked, kept, the reply
replayed within a minute. The app already has most of these and sends two;
it sends the rest on the request it already makes
(`POST /speech/music-bed/song`), and the same for a reply tune.

`music-sung.json` becomes a view of this table. `media music log` prints it;
the app shows it under Recent replies, each row with Play again — which is
the way back to a song that has stopped.

### 2. One chooser, reading the log

The bed, Sing it in, Sing it out and the reply tune are chosen as one set
for a reply, by one function, from: the reply (its length, its words, the
thread's mood), the moment (hour, weather, the axes, as now), and the log.

From the log, plainly counted and never a model's guess:

- a track stopped early twice in a week rests for a month; one let run and
  kept is drawn more often;
- no artist twice in a session, no song twice in a day unless liked;
- what was heard at this hour, on this kind of reply, and let run;
- the bed and the song after it suit each other (below).

The model's part stays where it is today: which of a handful of candidates
suits the words. It is given our own names and tags, as now.

### 3. A DJ's timing

What a DJ does that the mixing does not yet do:

- **Hitting the post.** The voice ends as the song's vocal comes in. The
  vocal's first entry is known for a separated favourite (the Demucs stem);
  the reply's length is known before it is spoken. So the bed's start is
  placed so that its vocal entry falls on the last word, and Sing it out is
  the bed carrying on with its voice back, not a crossfade to another
  place in the song.
- **Phrases, not seconds.** Starts and ends on a bar line: the beat grid
  from the onset code `music_bed_tempo` already runs, the sections from
  `music_bed_sections`. Today only the handover snaps, and only to a
  section within 8 s.
- **Key.** The chroma `music_bed_sections` computes gives a key for each
  track, kept beside its tempo. A bed carried into the next reply's bed, or
  a tune after a bed, prefers the same or a neighbouring key (the Camelot
  wheel), and a tempo within a few percent.
- **A session's arc.** Over an hour of replies the energy rises and falls
  on purpose: the log knows what the last few were.
- **Talking over the intro.** A short reply can ride a song's instrumental
  opening and stop at the vocal, where today a short reply has no music.

### 4. A song comes back

With the log, the app's player can show the last song after it has ended,
as it now keeps the last reply: its row in the log, Play again.

## What is left alone

- Spotify. Nothing of Spotify's goes to a model, is analysed or is kept
  (its terms IV.2.1, III.13, IV.3.1; docs/reference/outside-services.md).
  Tempo, key and sections are measured from files we hold: generated,
  library, and the listener's own favourites.
- The agent still names the reply tune; the chooser only decides whether
  the moment takes one and what follows it.
- Every switch in Settings → Music stays, and each new technique gets one.
- The app stays generic: the log, the chooser and the measurements are the
  server's; the app reports what happened and draws what it is given.
- GPL tools (Demucs) stay separate processes.

## Order of work

1. The log, and the app saying how each thing ended. Nothing chooses
   differently yet. A week of it shows what is worth learning from.
2. The log in the app, with Play again.
3. The rests and repeats from the log (the counted rules).
4. Key and a beat grid measured and stored for each track.
5. Hitting the post, behind a switch, on favourites with a stem.
6. One chooser for the set; key and tempo between neighbours.
7. The session's arc; talking over an intro.

## How it is measured

- From the log itself: the share of beds stopped early, of Sing it outs
  kept, before and after each step.
- Hitting the post: the gap between the last word's end and the vocal's
  entry, from the mix's own timings; under a quarter of a second.
- A repeat within a day, an artist twice in a session: counted, and none.

## What can go wrong

- A stop is not always a verdict: the phone rang. Counted only when the
  reply was let run and the music alone was stopped, and never from one.
- Placing a bed for its vocal entry needs the reply's length before it is
  spoken; a reply cut short misses the post. It falls back to today's
  crossfade.
- Key from chroma is wrong for some tracks (modal, atonal, a long intro in
  another key). A preference, never a rule.
- More choosing is more to explain: every row says why, in words.

## Open questions

- Should a turn of the volume under a bed move the level for good, as a
  mixer's hand would, or only be noted?
- Does the log belong in Recent replies, or a page of its own?
- How long is kept: the last year, or all of it?
