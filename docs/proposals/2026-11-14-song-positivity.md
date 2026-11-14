# Song positivity: a mood score for every song, worked out here

David, 14 Nov 2026: "Is there some kind of positivity rating for songs?" …
"let's scope the essentia option".

## What it is

Every song played here gets two numbers between 0 and 1:

- **valence**: how positive it sounds, from sad or tense up to happy;
- **arousal**: how much energy it has, from calm up to driving.

Five mood probabilities come with them (happy, sad, relaxed, aggressive,
party). The song card shows them as one line ("Upbeat · high energy"), and
the radio, backing music and `media tune related` can steer by them.

Spotify's `valence` would have been the obvious source, but Spotify closed
the audio-features API to new apps in Nov 2024. AcousticBrainz had per-song
mood scores, but it stopped in 2022, so its data dump has nothing newer. So
the songs are scored here, from their audio.

## The tool

[Essentia](https://essentia.upf.edu/) (MTG, Universitat Pompeu Fabra), with
its TensorFlow models:

- an embedding model over the audio (MusiCNN trained on the Million Song
  Dataset, or EffNet-Discogs), then small heads on top of it:
- `mood_happy`, `mood_sad`, `mood_relaxed`, `mood_aggressive`, `mood_party`
  (each a probability), and an arousal/valence regression head
  (trained on emoMusic, a mix of public and in-house sets).

Each head is a few hundred kB. MusiCNN is small and runs comfortably on a
CPU. Running a 30 s excerpt through the embedding and five heads should take
a second or two on red2 (16 cores, 30 GB). **To measure** in step 1.

## Licensing: the deciding question

- **Essentia itself is AGPL-3.0.** Under the licensing decision
  (docs/decisions/2026-09-22-licensing.md) it runs as a separate process in
  its own venv, never imported and never a dependency, the way Demucs and
  Piper do. That is fine.
- **The models are CC BY-NC-SA 4.0**, which means non-commercial use only.
  MTG sells a proprietary licence on request
  ([models page](https://essentia.upf.edu/models.html)). Scoring for
  David's own install is personal use. Shipping the feature in a paid
  Sasonica product would need that licence or other models.

So the first version is **gated by the personal-use switch**
(`personal_use.py`), with a row in docs/reference/outside-services.md
saying so. Ways to make it shippable later, cheapest first:

1. ask MTG what a commercial licence costs;
2. a zero-shot audio-text model with a permissive licence (e.g. LAION
   CLAP: score "a happy upbeat song" against "a sad song"), whose licence
   and quality are **to check**;
3. our own heads trained on a set we may use commercially. That is the
   most work.

The scores live behind one interface (`song_mood.score(path) -> dict`), so
swapping the models changes nothing else.

## How it runs (the Demucs pattern, much lighter)

`music_bed_favourites.py` already fetches songs' audio and runs a heavy
model on them overnight. This copies its shape:

- **Audio:** `sinks/music_fetch.ensure_local(<YouTube url>)`, the rooms
  cache the separation uses. Score a 30 s excerpt from the middle of the
  song (ffmpeg to 16 kHz mono), not the intro.
- **Runner:** `essentia-venv/bin/python -I song_mood_worker.py <wav>`
  (`$XDG_DATA_HOME/agent-media/essentia-venv`, or
  MEDIA_SONG_MOOD_PYTHON), printing JSON. Models are downloaded once into
  `$XDG_DATA_HOME/agent-media/essentia-models/`. `media setup` gets a
  `song-mood` row that makes the venv (`pip install essentia-tensorflow`)
  and fetches the models. The row is opt-in and shows the licence line.
- **When:** a background queue in the jobs worker. It runs niced and
  ioniced, skips while the host is strained (memwatch's PSI check), and
  takes a song when it is first played. A backfill works through the
  history overnight, most-played first, a few hundred a night.
- **Kept:** `<state_dir>/song-mood.json` (or a table in state.db), keyed by
  the song's uri (and artist + title, so another upload of the same song
  reuses the score), with the model name and version so a model change
  rescores.
- **A mix** is scored per chapter (agent-media §6.9a "A song inside a
  mix") once its songs are known; until then, not at all.

## What the app and agents get

- `GET /music/recent` rows and `GET /music`'s now playing carry
  `mood: {valence, arousal, happy, sad, relaxed, aggressive, party,
  model}` when scored (contract §6.9a).
- The song card (MediaAbout) shows one line in words, e.g. "Upbeat · high
  energy" or "Melancholy · calm", with no meters, so it reads on the Pine
  Note.
- `media tune related` and the `[[tune:]]` picker can weigh valence
  against the moment ("a late night" → low arousal).
- The radio's music mood (`music_bed.MOODS`: calm, dusk, morning…) can
  prefer songs whose arousal and valence fit, beside its tags.
- Music tab sorting and filtering by mood is possible later, not in the
  first version.

## Steps

1. **Spike (half a day):** make the venv on red2, score 20 songs David
   knows, and check the numbers against his ear. Measure time and memory
   per song. If the scores don't match what he hears, stop here.
2. **Scorer + store + queue:** `song_mood.py`, the worker script, the
   `media setup` row, the jobs-worker queue with its guards, and tests
   with a stub runner (no model in CI).
3. **Server + app:** `mood` on rows and now playing, the song card line,
   contract and outside-services rows, and What's new.
4. **Steering:** tune picker and radio mood weighting, each its own
   change.

## Open questions

- Personal-use gated for now, or ask MTG about a commercial licence first?
- Arousal/valence head plus the five mood heads, or only valence +
  `mood_happy` to start?
- Score only what is played from now on, or backfill the whole history?
