# AI edits to a photo before it is sent (proposal, 16 Nov 2026)

David, 16 Nov 2026, after asking for Take photo in the box: "Maybe even with
some ai filters and mods?", then "the photo proposal let's do it".

## What it is

Between choosing or taking a picture and sending it, the preview gets an
**Edit** button. It offers:

- **Presets, one tap each:** Fix the light, Blur the background, Remove the
  background, Painting, Sketch, and a few fun ones (Cartoon, Vintage film,
  Sunset). The list comes from the server, so it can grow without a new
  build.
- **Describe a change:** a box for anything ("make it sunset", "remove the
  person on the left", "add a party hat"), by voice or typing.

The result shows beside the original, with Use this, Try again and Back to
original. Edits can be stacked (a preset, then a description). The original
is never lost, and nothing is sent until he sends it.

It works for every chat that takes pictures: Local chats (through the
companion's `sendMedia`) and server chats alike, since the edit happens
before the attachment goes anywhere.

## Where it runs, and the privacy

On the server, through the AI gateway's image model. The first one tried,
`gemini-3.1-flash-image` through cli-proxy-api (the Google sign-in the
gateway already holds), took a 64×64 test picture and returned a watercolour
in 12 s through the OpenAI-style `/v1/chat/completions` (the edited picture
in `message.images`). No new account, no per-picture charge.

**The photo leaves the house**: it goes to Google to be edited. So Edit is
always a step he chooses, never automatic, and the sheet says once, plainly,
"Edits are made by Google's image model". A private or sealed thread gets no
Edit unless a private image model is set (`MEDIA_PRIVATE_IMAGE_MODELS`, none
today).

Fallbacks if Google's sign-in is down: OpenAI's `gpt-image-1` or Venice's
edit model through the keys the gateway already has (each per picture, so
off unless set: `MEDIA_IMAGE_EDIT_MODELS`, an ordered list, default the
Gemini route only).

**Public builds:** a Gemini sign-in borrowed from Google's CLI is a personal
method. A public install names its own image model and key, so the route
sits behind the personal-use switch with a public alternative (any
OpenAI-compatible image endpoint the owner configures). That goes in its row
in `docs/reference/outside-services.md`.

## The server (agent-media)

- `GET /photo/edit/presets` (paired) → `{"presets": [{"key", "label",
  "group": "fix"|"style"|"fun"}], "describe": true, "provider": "Google"}`
  in the order the app shows them; the prompt behind each key stays on the
  server (one list in code, shown in Settings → Prompts with the others).
- `POST /photo/edit` (paired, not guests) with the picture as the body
  (`image/jpeg|png|webp`, up to 15 MB; downscaled to 2048 px on the long
  side before it is sent) and `?preset=<key>` or `?describe=<text>`
  (`session=` for the private-thread rule) → the edited picture
  (`image/png` or `image/jpeg`), or JSON `{"ok": false, "why":
  "refused"|"no_model"|"too_big"|"failed"}`. The model's refusal (a face
  edit it will not do, for one) says so in words, not a broken picture.
- Nothing is kept: no copy of the picture, the result or the description
  is written to the server's state; one line per edit in the journal
  (preset key or "describe", size, model, time taken).
- `/features`: `photo_edit: true` where an image model is configured.
- Limits: one edit at a time per device, a timeout of 60 s, a daily cap per
  device (`MEDIA_PHOTO_EDIT_DAILY`, default 100) so a loop cannot run up a
  paid fallback.

## The app (sasonica-app)

The Edit button on the attachment preview where `/features` has
`photo_edit`; the presets as chips from the server's list; the describe box
with the mic; a progress state ("Editing… about 10 s"); the result beside
the original; Use this, Try again, Back to original; stacked edits. The
edited picture then goes the normal attach path (caption, reply, send).

## Order of work

1. Server: the two routes, the presets, the model call with its fallbacks,
   tests with a stubbed model; contract section; outside-services row.
2. App: the Edit sheet, after Take photo has landed.
3. Try it on p8a: each preset on a real photo, a describe, a refusal, a
   private thread.
4. Later: edits on a received picture ("make this a sticker"), and a picture
   made from words alone (no photo to start from).

## Open

- Whether to show the provider's name on every edit or once in Settings.
  Proposed: once, the first time Edit is opened, and in Settings.
