# The feed — rendered speech as a podcast

Status: **built**, running on red5 since 2026-08-30. The reasoning behind the
shape is in [the proposal](../proposals/2026-08-30-a-feed-of-what-was-said.md);
this page is the surface.

Everything agent-media speaks is rendered to audio and then, historically,
forgotten: the clips lived in `~/.cache/agent-media/audio`, which is a cache
and is allowed to delete them — a disk-full sweep on 2026-08-28 destroyed the
audio behind 373 history rows. (They are state since 18 Oct 2026, in
`~/.local/state/agent-media/audio`, but a host may still prune them by age.)
The feed is where audio goes to be *kept*, and a podcast client is how it comes
back: queue, resume position, playback speed, offline download and lock-screen
controls, none of which we had to write.

An **episode** is one audio file plus a JSON sidecar. A **feed** is a directory
of them, served as RSS. Three feeds exist by convention:

| feed | one episode is | default retention |
|---|---|---|
| `docs` | a document read aloud | kept until removed |
| `<tmux session>` | a conversation from that workspace | 90 days |
| `talks` | a conversation from no workspace at all | 90 days |
| `digest` | a day's agenda | 7 days |

An episode is titled `<tmux session> · <Claude Code's name for the session>`
— the workspace you would recognise, then the name shown in the resume list,
taken from the transcript's `ai-title` records (the last one: the name is
rewritten as a conversation turns out to be about something else). It falls
back to the opening question, which is what was typed before anybody knew
where the afternoon would go.

**A conversation goes to a feed named for its tmux session** —
`p-agent-media`, `scratch` — so a podcast client's subscription list is your
project list rather than one undifferentiated stream. `talks` is the catch-all
for conversations with no workspace behind them. `--feed <name>` overrides,
and any feed nobody has configured inherits the `talks` retention, because the
alternative is a directory per project that nothing ever prunes.

## Publishing

```sh
media doc play <name> --feed            # a document, instead of playing it
media doc agenda --feed                 # today's digest
media feed session [<session-id>]       # one conversation, chaptered
media feed publish-quiet                # every conversation that has finished
media feed publish <feed> <file> --title … --guid …   # anything else
```

`--feed` on `doc play` publishes *instead of* playing. The render is cached
either way, so playing it afterwards is a second command that costs nothing.

**Every episode has a guid, and publishing the same guid replaces it.** A
document's guid is its path, a conversation's is its session id, the agenda's
is its date. So re-publishing after an edit updates the episode rather than
leaving two versions of one thing in a client, and no caller has to ask whether
it has published this before.

The one consequence worth knowing: **clients never re-fetch a guid they already
have.** Audiobookshelf and AntennaPod both match on it. A conversation
published while still in progress will therefore stay short on the phone
forever, even though the spool is correct. Publish a session when it is done.

## Reading

```sh
media feed list [<feed>]        # feeds, episodes, durations
media feed sessions             # conversations available to publish
media feed xml <feed>           # the RSS, without writing it
media feed write <feed>         # regenerate <feed>/feed.xml
media feed remove <feed> <guid>
media feed gc [<feed>]          # apply retention
```

## Switching it on

```sh
media-setup feed          # token, address, services, subscribe URL
```

Opt-in, and a command of its own rather than part of `media-setup init`,
because this is the part of agent-media that publishes recordings of private
conversations to anything that can reach a URL.

It generates a token if there isn't one, takes the bind address from Tailscale
(`--bind` to override; `0.0.0.0` is refused, not warned about), installs the
three services, and prints the subscribe URLs — including an AntennaPod deep
link, so nobody types a 32-character token on a phone keyboard. Idempotent: run
it again and it tells you the URL again without touching the token, since a new
token silently unsubscribes every client that has the old one.

Until it is run, the three units are **skipped with a reason** rather than
installed idle — their templates declare `requires-env: MEDIA_FEED_BASE_URL`.
`media-setup status` reports the feed too: address, whether it is guarded, and
how many episodes each feed holds.

## Serving

`media-feed` (port 8782) serves `/feed/<name>.xml` and `/ep/<name>/<file>`,
plus `/` — a plain-text list of subscribe URLs, which is the easiest way to get
one onto a phone.

```
MEDIA_FEED_BIND=100.x.y.z         # tailnet address; never 0.0.0.0
MEDIA_FEED_TOKEN=…                # required off loopback; the whole auth story
MEDIA_FEED_BASE_URL=http://host:8782   # optional; see below
MEDIA_FEED_SPOOL=…                # default ~/.local/state/agent-media/feed
```

A podcast client cannot send a header, so authentication is a **capability
URL**: `?k=<token>`, carried into every enclosure the feed lists. (A token that
authorises only the XML gives you a feed that loads and episodes that all 401 —
which reads as a broken server rather than wrong auth.) `X-Agent-Media-Token`
and HTTP Basic also work, for `curl`; enclosures only carry `?k=` when the
request that asked for the feed did.

Binding off loopback without a token is a **startup failure**. The enclosures
are recordings of private conversations; a listener that came up anyway would
be an open archive reporting itself healthy. Tailnet only — never the public
interface.

Enclosure hosts come from the request's own `Host` header, so the address that
reached the feed is the address that reaches the episodes. `MEDIA_FEED_BASE_URL`
overrides that (for a reverse proxy) and is also what the *publishing* commands
use to regenerate `feed.xml` — a host without it still publishes; it just does
not rewrite the served XML.

## The same conversations, on a shelf

Each conversation's clips are also hardlinked into a folder of its own,
one track per sentence, appended as turns land (`media feed tracks`, run by
`agent-media-feed-publish`):

```
~/conversations/<workspace>/<title>/0001 - <sentence>.mp3
```

**Hardlinks, not copies**: one inode with two names, so the shelf costs no
disk and deleting either name never takes the audio with it. A manifest per
conversation (`<state_dir>/book-tracks/<session>.json`) records which turns
were placed; the transcript and the server's thread list read it.
`MEDIA_BOOK_TRACKS_ROOT` moves the tree; `MEDIA_CONVERSATIONS_LIBRARY=0`
turns it off.

This shelf was built for Audiobookshelf, which served it as a
"Conversations" library with a chapter per turn, covers, live/archived tags
and re-opened progress. Audiobookshelf was left on 11 Nov 2026 (David: "I'd
like to move away from audiobookshelf"); the chapters, tags, covers,
progress and `media abs-scan --target conversations` went with it. Feeds
remain the delivery mechanism.

## Retention

Per feed, in `~/.config/agent-media/config.toml`:

```toml
[feeds.talks]
keep_days = 90
keep_max  = 0      # 0 = unlimited; age is applied first, then count
```

Anything unset falls back to the table above. "Keep until played" is not
offered: play state lives in the subscriber's app and is never reported back,
so it would be a guess presented as a fact.

## Services

Episode descriptions are HTML, one paragraph per chapter: a client renders
them as HTML, so a list joined by newlines arrives as one unbroken wall of
text with the timestamps buried in it.

Installed by `media-setup feed`, or by role with
`media-setup install-services … --now` once the feed is configured:

| unit | what | when |
|---|---|---|
| `agent-media-feed` | serves the feed | always |
| `agent-media-feed-publish` | puts new turns on the shelf (`media feed tracks`) | on silence, + every 30 min |
| `agent-media-feed-gc` | applies retention, rewrites the XML | daily |

`MEDIA_FEED_QUIET_MIN` (default 60, set to 10 on red5) is how much silence
marks a conversation finished. Short is the point — an episode that arrives an
hour after you stop talking is a different thing from one waiting when you
pick up the phone — but too short publishes a conversation you were only
pausing, and no client ever re-fetches, so that half is the half you keep.

`agent-media-abs-sync`, which subscribed Audiobookshelf to every feed, went
with Audiobookshelf on 11 Nov 2026.

**It is triggered by silence, not by a clock.** The end of every turn arms a
one-shot for the quiet window and cancels the one before it, so a finished
conversation is built the moment the silence is long enough to mean something
— and nothing wakes up at all while you are talking, or while you are not.
The half-hourly timer is only the safety net for turns that could not arm one:
a reboot mid-conversation, or a host without systemd.

Per-turn publishing is not on offer, and cannot be: no client re-fetches a
guid it already has, so an episode published mid-conversation is frozen at
whatever version reached the client first.

The last two are timer-driven, so their units are *inactive between runs* —
which `media doctor` reports as parked, not down.

`agent-media-feed-publish` exits immediately where `MEDIA_FEED_BASE_URL` is
unset: publishing into a spool nothing can reach is disk spent on an audience
of nobody.

## Subscribing

```sh
curl -s "$MEDIA_FEED_BASE_URL/?k=$MEDIA_FEED_TOKEN"     # prints subscribe URLs
```

**AntennaPod** takes a deep link, which saves typing a 32-character token on a
phone keyboard:

```sh
ssh <phone> "am start -a android.intent.action.VIEW \
  -d 'antennapod-subscribe://host:8782/feed/talks.xml?k=<token>'"
```

Audiobookshelf used to be kept in step by `media-abs-sync`; it was left on
11 Nov 2026, and the sync and its notes (the SSRF allow-list, backfilling)
went with it.

## What the spool is, and why it is not a table

```
$XDG_STATE_HOME/agent-media/feed/<name>/
    <id>.mp3        the episode
    <id>.json       guid, title, description, times, byte size
    feed.xml        generated (the server does not read it)
```

The audio and the metadata describing it are one thing on disk, so a restored
backup is a working feed and no listing can outlive the file it points at —
which is exactly the failure the clip cache has. `<id>` is a hash of the guid,
because guids are paths and session ids and the filesystem should not have an
opinion about either.

Under `state_dir`, never `cache_dir`, and publishing **copies** rather than
links: a link into the cache would inherit the cache's permission to vanish.

The server generates the XML per request rather than serving the `feed.xml` on
disk, so a client syncing between a publish and a rewrite cannot cache a
listing that is missing the episode it came for.
