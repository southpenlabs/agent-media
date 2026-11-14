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

## Step 1 results (14 Nov 2026, red2)

David chose all three: personal use first, ask MTG, look at open models.

**The spike.** Essentia 2.1-beta6 (`essentia-tensorflow`, in its own
Python 3.11 venv outside the repo, `~/.local/share/agent-media-essentia/`),
MusiCNN-MSD embedding with the five mood heads and the emoMusic
valence/arousal head (scaled from 1–9 to 0–1). It scored 30 s clips
(60–90 s into each song) of 20 of David's most-played songs. The whole
run took 24 s, about **1 s per song** on CPU, with models loading in
0.1 s. Sorted by valence:

| song | valence | arousal | happy | sad | relaxed | aggressive | party |
|---|---|---|---|---|---|---|---|
| AC/DC - Thunderstruck | 0.67 | 0.71 | 0.89 | 0.00 | 0.00 | 0.95 | 0.99 |
| R.E.M. - Losing My Religion | 0.65 | 0.61 | 0.99 | 0.23 | 0.01 | 0.04 | 0.14 |
| Eagles - Take It Easy | 0.61 | 0.57 | 0.99 | 0.28 | 0.07 | 0.01 | 0.12 |
| Tears For Fears - Everybody Wants To Rule The World | 0.60 | 0.62 | 0.91 | 0.00 | 0.25 | 0.01 | 0.73 |
| CCR - Have You Ever Seen The Rain | 0.58 | 0.56 | 0.97 | 0.43 | 0.14 | 0.02 | 0.42 |
| John Mellencamp - Jack & Diane | 0.58 | 0.56 | 0.90 | 0.63 | 0.60 | 0.00 | 0.19 |
| Eagles - Hotel California | 0.55 | 0.49 | 0.79 | 0.85 | 0.65 | 0.01 | 0.47 |
| Morcheeba - The Sea | 0.54 | 0.49 | 0.90 | 0.19 | 0.21 | 0.01 | 0.03 |
| 4 Non Blondes - What's Up | 0.54 | 0.54 | 0.78 | 0.69 | 0.24 | 0.03 | 0.37 |
| Télépopmusik - Breathe | 0.51 | 0.44 | 0.32 | 0.07 | 0.97 | 0.00 | 0.03 |
| Massive Attack - Unfinished Sympathy | 0.50 | 0.46 | 0.45 | 0.29 | 0.70 | 0.00 | 0.31 |
| The Who - My Generation | 0.49 | 0.58 | 0.78 | 0.09 | 0.27 | 0.17 | 0.33 |
| The Animals - House Of The Rising Sun | 0.48 | 0.47 | 0.78 | 0.80 | 0.39 | 0.00 | 0.00 |
| Camila Cabello - Havana | 0.47 | 0.51 | 0.82 | 0.51 | 0.38 | 0.00 | 0.54 |
| Massive Attack - Teardrop | 0.45 | 0.41 | 0.07 | 0.28 | 0.89 | 0.00 | 0.06 |
| Mazzy Star - Fade Into You | 0.43 | 0.33 | 0.24 | 0.98 | 0.93 | 0.00 | 0.00 |
| Guns N' Roses - November Rain | 0.42 | 0.43 | 0.05 | 0.88 | 0.87 | 0.06 | 0.02 |
| Nils Frahm - Says | 0.42 | 0.28 | 0.02 | 0.12 | 1.00 | 0.00 | 0.00 |
| Debussy - Clair de Lune | 0.38 | 0.22 | 0.00 | 1.00 | 1.00 | 0.00 | 0.00 |
| Hammock - Tremendum | 0.28 | 0.33 | 0.00 | 0.08 | 1.00 | 0.03 | 0.00 |

What the numbers show:

- **Energy (arousal) reads right.** Thunderstruck is highest, and Clair
  de Lune, Nils Frahm and Hammock are lowest.
- **Valence is squeezed** into 0.28–0.67, so it needs rescaling (a rank
  across the library, not raw numbers).
- **It hears the music, not the words.** Losing My Religion and Hotel
  California score as cheerful because of their bright sound.
- **The five mood heads are the more telling line.** Mazzy Star is "sad,
  relaxed"; Thunderstruck is "aggressive, party".
- One 30 s clip may be unrepresentative. In production, average three
  clips taken across the song.

**Open models** (web research by a subagent; the licences are as listed
on each model's page):

- **No published mood dataset is clear for commercial training.**
  MTG-Jamendo, emoMusic and DEAM are non-commercial; PMEmo has no named
  licence. MERT, MuQ and music2emo weights are non-commercial too.
- **LAION CLAP** (`larger_clap_music`, Apache-2.0 on Hugging Face; code
  CC0) is the most shippable embedding. Its training audio is murky, and
  zero-shot prompts are poor at mood (r = 0.08 against human ratings in
  GlobalMood, arXiv 2505.09539).
- **AcousticBrainz's per-recording mood outputs are CC0.** That makes
  them the one commercially safe source of mood labels: frozen in 2022,
  keyed by MBID, noisy.
- The best shippable route is CLAP embeddings with a small head we train
  on AcousticBrainz's CC0 labels plus David's own ratings. OpenL3 (MIT
  code, CC BY weights) is the fallback embedding.
- Unverified: whether the msclap and MusicFM weights carry the licence
  their pages say, the official DEAM text, VGGish's licence, and any CPU
  timing other than Essentia's.

**Draft to MTG** (not sent; David to approve), to the contact on
https://essentia.upf.edu/models.html:

> Subject: Commercial licence for Essentia mood models
>
> Hello — South Pen Labs makes Sasonica, an app that plays music alongside
> coding agents. We would like to score songs for mood on the user's own
> server with your MusiCNN-MSD embedding and the mood_happy, mood_sad,
> mood_relaxed, mood_aggressive, mood_party and emomusic heads, running
> Essentia as a separate process. Could you tell us what a proprietary
> licence for those models would cost for a small commercial product,
> and its terms (per-install, per-seat, or flat)? Thank you.

## Open questions

- Personal-use gated for now, or ask MTG about a commercial licence first?
- Arousal/valence head plus the five mood heads, or only valence +
  `mood_happy` to start?
- Score only what is played from now on, or backfill the whole history?
