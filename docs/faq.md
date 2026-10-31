# Frequently asked questions

Short answers to the things people run into with agent-media and the
Sasonica app. Each one says what is going on, gives the command to check or
fix it, and points at the reference doc or `server-contract.md` section
that has the full story.

Device names, hosts and paths in commands are examples; use your own.

**In the app**

- Why aren't replies playing automatically?
- Speech keeps pausing by itself, or voice typing says another app is using the mic.
- Why does the voice sometimes change to a different one?
- Why is text bigger in the app than in the browser?
- How do I link to a thread?
- What is the music under replies, and how do I change it?
- What is the Backing music level relative to?
- What can I do with the song after a long reply?
- Why does my music go quiet, then swell, while a reply plays?

**On the server**

- Can I delete `~/.cache/agent-media` to free disk?
- I changed `agent-media.env` and nothing changed.
- YouTube won't download on my VPS.
- A Codex session is stuck on "Hooks need review".
- Mopidy or Iris won't start after upgrading to Mopidy 4.
- Keys on the canvas do strange things (open a search, scroll).

**On a phone running Termux**

- Speech is silent on the phone, but everything looks healthy.
- `cryptography` fails with "cannot locate symbol PyLong_Type".
- YouTube music fails with HTTP 403.
- How do I update a Termux install?

---

## In the app

### Why aren't replies playing automatically?

Your phone is probably on silent or Do Not Disturb. With **Settings → When
you're busy → Hold replies while on silent** on (the default), a quiet phone
holds every normal reply, even the open thread's. The thread shows an "On
silent: replies wait" (or "Do Not Disturb: replies wait") chip with
**Speak anyway**. Held replies keep a Play button, and **Catch me up** reads
out what waited. Turn the setting off if you want silent to hold only alerts.

See also: `server-contract.md` §6.22 "Can David be spoken to now"
(`held_why`, `quiet_kind`, `quiet_replies`).

### Speech keeps pausing by itself, or voice typing says another app is using the mic.

On many Android phones (Pixels especially), Android System Intelligence
opens the microphone over and over, all day. The app reads each one as you
starting to talk, so it pauses the reply. Blocking that app's mic stops it.
Check who is recording, then block it:

```sh
adb shell dumpsys audio | sed -n '/Recording Activity/,/^$/p'
adb shell appops set --uid com.google.android.as RECORD_AUDIO ignore
```

Android undoes the block on its own, sometimes within minutes. To keep it
in place, run the `mic-block` service on the phone with
`MEDIA_MIC_BLOCK_PACKAGES=com.google.android.as` set (it needs adb on the
phone). `media doctor` reports a block that has come loose. To undo it, run
the same `appops` command with `allow`. This costs you "Now Playing" and
"Hey Google".

See also: `packages/core/src/agent_media_core/mic_block.py`.

### Why does the voice sometimes change to a different one?

In phone mode the app makes the speech itself. It asks Microsoft's online
service for the voice you chose, and if a sentence doesn't arrive in time it
uses a Google voice on the phone for that sentence. Mobile data makes this
more likely: some carriers get "too many requests" answers, which the app
retries before it gives up. If it keeps happening, look for the
`edge-voice-phone` alert under **Home → Alerts**. You can also switch the
Voice setting to server mode, so the server makes the speech.

See also: `server-contract.md` §6.4 "`GET` / `POST /speech/voice`", §6.17
"Alerts".

### Why is text bigger in the app than in the browser?

The app follows Android's own font size setting (usually under Display),
and the browser does not. At Android's 1.5× scale, "Default" text in
the app is 1.5 times the size of the same setting in a browser. Make
Android's font smaller if you want them to match.

### How do I link to a thread?

There are two forms. Either one opens the thread in place when you tap it
in the app:

```
sasonica://t/<session>
https://<server>/app/t/<session>
```

The https form opens the web app's thread page in a browser. On a phone with
the app it is an Android App Link, so it opens the app instead. Agents are
told their own thread's link at the start of a session; they use
`MEDIA_PUBLIC_URL` when it is set and the `sasonica://` form when it isn't.
Set `MEDIA_THREAD_LINK=0` to turn that off.

See also: `server-contract.md` §19, "Thread links".

### What is the music under replies, and how do I change it?

Replies can play over quiet backing music, the way a radio host talks over
a bed. **Settings → Music under long readings** picks the source: generated
moods and styles (bossa nova, piano, lo-fi…), your streaming service
(house speakers only), a free online library (archive.org and ccMixter), or
**your favourites, instrumental**: official instrumental or karaoke
versions of the songs you play most, or ones with the vocals removed
overnight. **Auto** follows the time of day (and your Astro Tunes reading,
where set up); **Slow music only** keeps it calm. The switches below it shape
the mix like a DJ would: the music breathes up in the pauses, steps out of
the voice's way, has an intro and an ending, and carries on between replies
that follow each other.

See also: `server-contract.md` §`/speech/music-bed` ("DJ mixing").

### What is the Backing music level relative to?

To the voice. The number is roughly how far under a reply's voice the music
sits: -22 dB means the music is about 22 dB quieter than the voice (each 10
dB is about half as loud to the ear). It is approximate: songs differ in
loudness, and a voice rendered on your phone may differ from one rendered on
the server. A new install starts at -30 dB. The level is the one **under the words**: in the pauses the music
swells up to 6 dB above it. Every device shares it: change it on the full
player (**Levels**, tap to fold) or in Settings → Music, and the next sound
plays at the new level.

The same slider sets how far your own music (the radio, a playlist) is
turned down while a reply plays: at -14 dB it ducks as it always did (to
about a tenth of its volume), and each 6 dB lower halves that again.

See also: `packages/core/src/agent_media_core/music_bed.py` (`gain_db`,
`VOICE_REF_DB`, `duck_scale`).

### What can I do with the song after a long reply?

After a long reply over one of your favourites, the original song, vocals
and all, takes over from the instrumental and plays to the end of a chorus
(**Sing it out after long replies** in Settings → Music). While it plays it
shows in the mini player and at the top of the Media tab, with:

- **Keep playing**: the whole song, no fade at the chorus.
- **Stop**: a quick fade.
- **Like** / **Dislike**: a like ranks it higher among your favourites; a
  dislike takes it out of them for good, and stops it.
- **Start radio**: your music player carries the song on from the same place
  and a radio station seeded with it follows. From then on it is ordinary
  music, turned down under later replies.

With a headset: **next** keeps it playing, a **double press** starts the
radio, a single press stops it. By voice (hands-free or the mic key): "keep
playing", "start the radio", "stop the music". Only on the phone; the song
after a reply on house speakers has no controls yet.

See also: `server-contract.md` §`/speech/music-bed` ("Singing it out"),
`POST /speech/music-bed/song`, `POST /music/radio`.

### Can it guide me through a meditation?

Yes. Ask in any chat, for example "guide me through a ten-minute body scan".
The agent writes the script, and you hear it said more slowly than a reply,
over quiet generated music, with real silences and a singing bowl at the
start and end. It plays like an audiobook, so pause, stop and seek work as
usual. A reply that arrives during it doesn't talk over it: the meditation
fades out over five seconds, pauses, and fades back in after the reply.

From a shell, a script with `[[pause: 20]]` (or `1m`, `1m30s`) and
`[[bell]]` markers:

```
media meditate --file body-scan.txt --bed night
```

Settings, in `~/.config/agent-media.env`: `MEDIA_MEDITATE_RATE` (-12%,
how much slower than a reply), `MEDIA_MEDITATE_BED_PEAK_DBFS` (-20, the
music's level), `MEDIA_MEDITATE_MOOD` (night), `MEDIA_MEDITATE_GAP_S` (2,
the quiet after each paragraph), `MEDIA_MEDITATE_TAIL_S` (30, the music's
fade after the last words), `MEDIA_MEDITATE_EASE_OUT_S` (5) and
`MEDIA_MEDITATE_EASE_IN_S` (2), the fades around a reply.

See also: `packages/core/src/agent_media_core/meditate.py`, the `meditate`
MCP tool, `docs/proposals/2026-10-31-relax-and-meditate.md`.

### Why does my music go quiet, then swell, while a reply plays?

Your own music is ducked under a reply the way a DJ talks over a record: it
slides down as the voice starts, rises a little in the pauses, and comes back
up after the last word. How far down follows the **Backing music level**
(above). A short notice only dips it; a question or a very long reply pauses
it; books and podcasts always pause, since you can't half-hear narration.

See also: `packages/core/src/agent_media_core/route/dj.py`.

## On the server

### Can I delete `~/.cache/agent-media` to free disk?

Yes. Since 18 Oct 2026 it holds only copies of things that live elsewhere or
are made again on demand: fetched books and music, rendered documents and web
clips, downloaded Matrix voice notes (a held one that has not been played yet
loses its Play, but the note is still on the Matrix server). The spoken clips
that replay needs live in the state dir, `~/.local/state/agent-media/audio`
(`$XDG_STATE_HOME`, or `MEDIA_AUDIO_DIR` to put them on a bigger disk). Don't
delete that one unless you are willing to lose replay: history points at those
files, so transcripts and search keep working but old replies can no longer be
heard.

An older install kept the clips in `~/.cache/agent-media/audio`. The first
speech after upgrading moves them across, rewrites the history rows to the new
path and leaves `~/.cache/agent-media/audio` as a link to the new folder (the
clip server for the phone serves it from there). Deleting the link is
harmless: it is made again on the next reply.

### Are my own typed messages saved as audio?

No, not since 18 Oct 2026. A message you type (or send from the app, or an
option you pick) is kept as a speech-history row with its text, which is what
the thread, its "thinking" line and `/conversation/log` read. When Replay
reads your question before the answer, it reads it out then, in the listener's
voice (`MEDIA_LISTENER_VOICE`, an edge voice): the phone is sent the words
when it renders speech itself (`MEDIA_SPEECH_RENDER_<TARGET>=device`), and
anywhere else the server renders it as the replay starts and keeps that file
beside the other clips. If it cannot be voiced, the reply plays on its own.
The `*-listener-*.mp3` clips made before then still play while they last.

### I changed `agent-media.env` and nothing changed.

The env file only fills in variables that are **not already set**. Anything
already exported wins. A long-running tmux server is the usual culprit,
because it hands the environment it started with to every new pane. Compare
it with your file and clear the stale ones; you don't need a restart:

```sh
tmux show-environment -g | grep -E '^(MEDIA|TTS)_'
tmux set-environment -gr MEDIA_SOME_VAR
```

Check your shell's own exports too (`env | grep MEDIA_`).

### YouTube won't download on my VPS.

YouTube blocks downloads from data-centre IP addresses. agent-media works
around that by downloading on a machine with a home connection (usually your
phone) and playing the file from there. Point it at that machine with
`MEDIA_MUSIC_LOCAL_SSH`; for the rooms lane only, use
`MEDIA_MUSIC_ROOMS_FETCH_SSH` (an always-on box at home, for example).
Downloading on the server and copying the file over is not an option.

See also: `packages/core/src/agent_media_core/sinks/music_fetch.py`.

### A Codex session is stuck on "Hooks need review".

Codex stops and asks you to approve its hooks whenever `~/.codex/hooks.json`
changes. That includes the first time `media-setup profile` adds
agent-media's hook. Until someone answers, the session takes no input. The
app shows the prompt as an approval, so you can answer it there or at the
terminal. You only need to answer it once per change.

See also: `server-contract.md` §6.2, approvals and `POST /session/answer`.

### Mopidy or Iris won't start after upgrading to Mopidy 4.

There are three usual causes:

- New setuptools releases dropped `pkg_resources`, which Iris and
  Mopidy-YouTube still import. Pin `setuptools<81` in Mopidy's environment.
- Iris imports `mopidy.models.serialize`, which Mopidy 4 removed. Iris
  needs a patch or a release that supports Mopidy 4.
- Watch out for `pipx inject mopidy <pkg>`: it can quietly upgrade Mopidy
  itself, and the break only shows at the next restart. Compare `pip
  freeze` before and after.

See also: `docs/reference/music-local-and-snapcast.md`.

### Keys on the canvas do strange things (open a search, scroll).

A keyboard browser extension, such as Vimium, is catching the keys before
the canvas gets them. The canvas uses `c` (captions) and `f` (sound
effects). Add an exclusion rule in the extension for the canvas page (for
example `http://<host>:8781/*`).

## On a phone running Termux

### Speech is silent on the phone, but everything looks healthy.

Check Android's **media** volume. Speech plays into the music stream, so at
0 nothing can be heard, even though every component reports healthy.
`media doctor` flags "media volume is 0" but never changes the volume for
you.

```sh
termux-volume
```

### `cryptography` fails with "cannot locate symbol PyLong_Type".

A pip-built `cryptography` builds fine on Termux, but then Android can't
load it. Install Termux's own build and link it into the agent-media venv,
using your Python version in the paths:

```sh
pkg install python-cryptography
V=~/projects/agent-media/.venv/lib/python3.14/site-packages
SYS=$PREFIX/lib/python3.14/site-packages
rm -rf $V/cryptography $V/cryptography-*.dist-info
ln -s $SYS/cryptography $V/
ln -s $SYS/cryptography-*.dist-info $V/
```

Other native packages work the same way: get them from `pkg`, not pip.

### YouTube music fails with HTTP 403.

If the track is found but the download returns 403, the stable yt-dlp is
behind a YouTube change. Run bare `yt-dlp` on the phone to confirm. Then
install the nightly; if `~/.local/bin` comes first on your PATH, it takes
over from Termux's package:

```sh
python3 -m pip install --user --pre --upgrade yt-dlp
```

### How do I update a Termux install?

Pull the checkout, then restart the services:

```sh
git -C ~/projects/agent-media pull --ff-only
media restart-services
```

If the pull added a package or changed a dependency pin, install it too:
`uv pip install --no-deps --python .venv/bin/python -e packages/<pkg>` (the
venv has no pip). After Termux upgrades Python, run
`packages/core/scripts/termux-python-heal.sh` (`--install-hook` makes it
run on its own). After moving the checkout, run `media-setup profile`. From
the server, `media doctor --fix` pulls and restarts any host that is only
behind.

See also: `docs/reference/version-skew-monitoring.md`.
