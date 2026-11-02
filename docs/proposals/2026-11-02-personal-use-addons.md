# Personal-use add-ons: the gated methods moved out of the product

David, 2 Nov 2026: "Is it possible to make some of these legal by making
them free open source add-ons?" … "Switch now, add-ons later" … "yeah,
let's do the add-ons". Follows `2026-11-02-personal-use-switch.md` and
`docs/decisions/2026-11-02-personal-use.md`. Status: **step 1 built** (2
Nov 2026) for `spotify-via-youtube`, `insight-timer-profiles` and
`facebook-previews`: the add-on lives outside this repository, published
by its author rather than by the product's maker, under AGPL-3.0-or-later
(David: "Your personal GitHub"; "I guess agpl"). This repository does not
name or link it, so the product neither ships nor promotes it. The app side stays as it
is for now (David: "Server side only for now"); the bridges, Find on
Messenger and the stories log are the next server methods to move.

## The YouTube methods (2 Nov 2026)

David: "Is YT profiles something that should be in the add-on?" — "All
YouTube methods now" … "might as well do downloads along with it". Six
methods, asked for through `youtube_addon.get(name)`; each has its own
setting beside `MEDIA_PERSONAL_USE`, and without the add-on each caller
takes the public alternative:

| Method | What the add-on does | Without it |
|---|---|---|
| `youtube-accounts` (`MEDIA_YOUTUBE_ACCOUNTS`) | reads the listener's YouTube likes and history (ytmusicapi with copied sign-ins; `ytmusic_reader.py`), lists the yt-profile sign-ins | nothing read; Settings says it is not available |
| `youtube-metadata` (`MEDIA_YOUTUBE_METADATA`) | yt-dlp's probe of a shared link, playlist ids, titles, lengths | a link judged by its shape; the single link, not the playlist; oEmbed titles; no lengths |
| `youtube-radio` (`MEDIA_RADIO_YOUTUBE`, still required) | the radio's Mix and the DJ's search, on the phone | the hand-off player by name only |
| `youtube-downloads` (`MEDIA_YOUTUBE_DOWNLOADS`) | the house speakers' fetch and copy, the phone's fetch and play, audiobook imports (`audiobook-fetch`), the Podcasts tab's downloads | nothing downloaded; files already cached still play |
| `youtube-streaming` (`MEDIA_YOUTUBE_STREAMING`) | allows Mopidy or mpv to stream YouTube | a YouTube stream is refused (`sinks.music.StreamRefused`) |
| `youtube-search` (`MEDIA_YOUTUBE_SEARCH`) | allows Mopidy-YouTube's search and lookup for the backing music | the backing music's YouTube sources find nothing |

Streaming and search have nothing to call: Mopidy-YouTube and mpv's
yt-dlp are the host's programs, so the add-on only grants them. The
helpers the downloads run on the phone (`play-local`, `fetch-track`) were
never in this repository.

## What it does and does not change

Open source does not make a method fit a service's terms: those bind the
person using the account, whoever wrote the software. What a separate
add-on changes is who ships it. The product (agent-media, the Sasonica
app as distributed) carries none of these methods; a person who wants them
installs the add-on themselves, as people install yt-dlp, NewPipe, Kodi
add-ons or the mautrix bridges. The separation has to be real: its own
repository and package, not bundled, not offered or promoted inside the
product. Not legal advice: a lawyer looks before anything is public.

## The server side: a Python add-on package

agent-media already loads optional packages by entry point
(`agent_media.render_engines`, `agent_media.org_profiles`). The add-on is
one more:

- A package in its own repository declares
  `[project.entry-points."agent_media.addons"]`.
- Each add-on provides methods by the names `personal_use.METHODS` already
  uses: `spotify-via-youtube`, `insight-timer-profiles`,
  `insight-timer-page`, `messaging-bridges`, `facebook-stories`,
  `facebook-previews`, `signed-in-reading`.
- `personal_use.allowed(name)` becomes: an installed add-on provides it,
  and it is switched on (`MEDIA_PERSONAL_USE`, or the method's own
  setting). Without the add-on, nothing to switch on.
- The code that moves (about 1,700 lines): Spotify's embed-page reading
  and YouTube playback (`music_links`), Insight Timer's internal profile
  and search lookups (`practice_library`), the bridge provisioning and
  sign-in relay (`bridges.py`), Find on Messenger (`contacts_find.py`), the
  stories tray log, and the Facebook previewer agent. agent-media keeps
  small hooks where each plugs in, and every public alternative.
- Matrix itself stays in agent-media: Matrix-native chats are fine.

## The app side: a build of its own

App code cannot be installed beside an APK the way a package can. The
pieces are the stories tray, the bridge sign-in browser and its cookie
rules, the signed-in reader and video viewer, and the player of Insight
Timer's page (about 3,500 lines of Java and some TypeScript).

- **Recommended:** they move into the add-on repository, and the app gets
  a second Android build flavour, `personal`, built from source with them;
  the distributed build (`public`) has none of them. CI builds `public`;
  a person builds `personal` themselves (red5's runner builds David's).
- Alternative: a companion APK the app hands those screens to. Cleaner
  separation, but a second app to install, sign and keep in step.

## Order

1. The add-on repository and package, the entry point, `allowed()` asking
   it; move the server methods; agent-media's tests run with and without
   it. red5 installs it.
2. The app's `personal` flavour and the moved Android pieces; red5 builds
   and installs `personal` for David.
3. A short README in the add-on: what each method does, why it is not in
   the product, and that its user takes on the service's terms.
