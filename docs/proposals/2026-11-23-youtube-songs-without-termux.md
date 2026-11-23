# YouTube songs without Termux: downloaded on the server, kept on the phone, or streamed

23 Nov 2026. David tapped a song in Media → Music and it said "Downloading"
and never played: every YouTube download went through the phone's Termux
jobs worker, which was offline. He asked:

> Can we make it happen without relying on termux?

> also, can we add an option to stream it or download it to the host server and stream it?

> Can it have the option to download without termux and the tailnet to the
> phone and play it offline? Can it also have the option to stream straight
> from YouTube?

His answers: keep songs on the phone both automatically and with a Save
offline button; stream from YouTube in the app on the personal build, and
hand off to YouTube Music on the public one.

## What there is now

- **Downloaded on the server** (done, 23 Nov 2026). red2 is on a home line
  (Optus), so YouTube lets it download directly.
  `MEDIA_YOUTUBE_FETCH_HERE=1` has the personal-use add-on's `rooms_fetch`
  run yt-dlp on red2 into the speakers' cache before asking any phone
  (sasonica-personal-use da2a659). The app's player gets a signed
  `/music/files/<id>.<ext>` link on the public address and streams it
  (sinks.music_app). No Termux, no tailnet. A failed play says why
  (`downloader_offline`, 6234733c), and the app keeps the message on screen
  (sasonica-app 2bd78c48).

## To build

### 1. Kept on the phone, played offline (app, native)

The app's player (Media3Music) streams the server's link. It gets a Media3
cache (`SimpleCache`, Apache-2.0, part of Media3 already in use), keyed by
the track's file name rather than its signed URL, so the same song is the
same entry under any signature.

- **Kept automatically:** every song played fills the cache as it plays.
  Least-recently-used songs are dropped past a size the user sets in
  Settings → Music (default 1 GB).
- **Save offline:** a song's info page gets Save offline / Remove from
  offline. A saved song goes through Media3's `DownloadManager` into a
  cache that never drops anything, so it stays until it's removed.
- **Played offline:** the player reads both caches before the network. With
  no connection, Media → Music lists what is on the phone (a plugin call
  answers the kept and saved songs with their titles) and a tap plays it in
  Media3Music directly, with no server round trip.

### 2. Stream straight from YouTube, personal build (app)

The app finds the song's audio stream itself on the phone's own
connection, so the link works wherever the phone is: youtubei.js (MIT;
NewPipeExtractor is GPL-3 and can't be used), its requests made through
the native HTTP client so no CORS applies. Only in the `personal` flavour,
as a personal-use method (decisions/2026-11-02-personal-use.md). It breaks
when YouTube changes things, and falls back to the server download when it
does.

### 3. Hand off to YouTube Music, public build (app)

Opens the song in the YouTube Music app, as the radio's handoff already
does (`MEDIA_RADIO_HANDOFF_APP`). Sasonica's own player, queue and lock
screen are not used for that song.

### 4. The choice (server and app)

Settings → Music, "YouTube songs": **Download on the server** (default),
**Stream from YouTube** (personal build) or **Open in YouTube Music**
(public build). The server answers which ways it offers in `GET /music`,
and a play carries the choice. When the app is to stream or hand off, the
server answers with the video id rather than loading a file. Plays the
server starts itself (the radio, a voice request) follow the same setting
through the app's player.

## Order

1, then 4 with 3, then 2. The offline copy helps every way of playing;
the handoff is small; in-app streaming is the most work and the most
fragile. Each native step ships in one APK, installed after checking that
nobody else is using the phone.
