# Proposal: the canvas in the app's Now playing, then video beside audio

Status: step 1 in progress (5 Nov 2026)

## What David asked

> Can we include the canvas in the app?

He chose a full-screen view built on the Media screen's Now playing tab, where
the reply takes over while it is spoken. Then:

> I was thinking, media could be made to handle video as well as audio, so
> instead of album art it would be a video in now playing. Maybe even on
> simultaneous channels so that audio plays with a separate video clip ...
> same goes for the canvas.

## The shape

Now playing has one **stage** (where the album art is today) and the audio
under it. What fills the stage is chosen in this order:

1. A reply being spoken: its picture, the sentence being spoken over its
   foot, the thread's name as the title. No picture: the sentence alone, in
   large type. When the reply ends, the music comes back.
2. A video for what plays: a music video, a practice class, a clip.
3. The album art, as today.

The stage and the audio are separate **channels**. Usually the video is the
audio's own (a music video plays its own sound). It can also be a separate
clip, muted, looped or timed, over audio from somewhere else: a reply read
over a clip the agent picked, or a song over a visualiser. The wall canvas
(`static/canvas.html`, which already embeds YouTube) reads the same stage, so
a screen across the room and the app show the same thing.

## Steps

1. **The reply takes over Now playing.** `GET /speech/now` carries the live
   reply's `images`/`figure` (agent-media `97b84b74`, contract §6.5). The
   app shows them with the sentence and the speech keys, and full screen
   hides the bars as the wall does. App only from here.
2. **A video for what plays.** `GET /music` names a video for the live item
   when it has one (a YouTube song's own video, a practice class): `video:
   {kind: "youtube"|"file", src, muted}`. The stage plays it muted and in
   step with the audio's position, or as the sound itself when the video is
   the audio's own and plays on this device.
3. **A separate video channel.** A `visual` channel in `/audio/targets`
   (§6.9) beside speech, music and book: `POST /visual {src, loop, mute,
   until}` puts a clip on a device's stage over whatever it hears. An agent
   picks it as it picks a figure (a marker such as `[[clip: ...]]`). Each
   screen chooses where its stage shows, as with speech.
4. **The wall reads the stage.** The canvas page shows step 2's and step 3's
   video the way it shows figures now, so the stage has one source.

## Open questions

- Sync: does a video separate from the audio follow the audio's position,
  or run on its own clock? Step 2 follows the audio. Step 3 is undecided:
  David leans to following the audio, "or maybe a bit of both" (5 Nov 2026).
  One way to have both is a clip pinned to a point in the audio (it starts,
  pauses and seeks with the audio from there) that loops or holds its last
  frame on its own clock once it runs out. Not decided; step 3 is not yet
  agreed either.
- Decision 2026-09-21 (the canvas carries words): a clip is decoration
  unless it shows something the voice does not. Step 3 should keep that
  test: a clip the user asked for, or one that shows the thing being talked
  about, never a mood loop by default.
- E-ink (the Pine Note) gets no video. Its stage stays on the picture and
  the words.
