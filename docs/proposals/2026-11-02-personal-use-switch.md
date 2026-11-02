# A personal-use switch for what a public product could not do

David, 2 Nov 2026, after Insight Timer's terms led to `embed_play`
(§6.45): "We should probably include that method for the other functions
that wouldn't be allowed in a commercial system ... Or something similar?"
Status: **built** 2 Nov 2026 for Spotify via YouTube, Insight Timer and
the Facebook/bridges methods (David: "Allowed alternative"; YouTube left
out for now). Next: these methods moved out into a separate open-source
add-on (David: "Switch now, add-ons later").

## The idea

One install-wide setting says the server serves only its owner's personal
use: `MEDIA_PERSONAL_USE=1`. Off in the package, on red5. Each method a
public product could not use asks one place (`personal_use.allowed(name)`)
whether it may run; each keeps its own switch to turn it off (or on) alone.
`GET /features` lists what is on
(`"personal_use"`) and the app shows the allowed alternative where one exists — the maker's
own player, a link out to their app — or leaves the feature out.

`docs/decisions/` gets the rule: a new feature that depends on a method in
the list below is gated from the start.

## What it would cover

From a read of both repos (2 Nov 2026). "Switch" is today's own setting.

| Method | Where | Switch today | Public alternative |
|---|---|---|---|
| **YouTube downloads** through the phone (circumventing the host's block) | music_fetch (house speakers), audiobook-fetch, podcast_links, practice YouTube items, share pipeline | `MEDIA_MUSIC_ROOMS_FETCH`, `MEDIA_PODCAST_LINK_FETCH`; others none | YouTube's own player / a link to YouTube |
| **YouTube streaming outside its player** | Mopidy-YouTube music, music bed stream | `MEDIA_MUSIC_BED_STREAM` | the official embed |
| YouTube radio | radio_io | `MEDIA_RADIO_YOUTUBE`, already off by default | hand-off to the listener's music app (exists) |
| YouTube lengths by yt-dlp | practice durations | `MEDIA_PRACTICE_DURATIONS` | none needed: no length |
| Music bed favourites (download + vocal removal) | music_bed_favourites | `MEDIA_MUSIC_BED_FAVOURITES` | generated / library beds |
| YouTube account reading (ytmusicapi, copied headers) | music_accounts | `MEDIA_YOUTUBE_PROFILES`, empty | the official Data API with OAuth |
| **Spotify lists played from YouTube** (undocumented embed page) | music_links | none | Spotify Connect remote play (exists, official) |
| Insight Timer track page in a hidden WebView | practice embed | `MEDIA_PRACTICE_EMBED_PLAY` (done) | their widget + open in their app (done) |
| Insight Timer profiles (internal endpoints, photos shown) | practice_library.teacher_profile | `MEDIA_PRACTICE_PROFILES` | name and counts only |
| **Messaging bridges** (Meta, Google Messages, Instagram, Teams), cookie sign-in, Find on Messenger | bridges.py, matrix.py, BridgeLoginActivity, contacts_find | `MEDIA_BRIDGES_WATCH`, `MEDIA_MATRIX_SYNC` | Matrix-native chats only |
| Facebook stories tray (reframing their site) | StoriesTrayPlugin | `MEDIA_STORIES` | none |
| Facebook previews as `facebookexternalhit` | link_preview | none | plain Open Graph fetch, else no card |
| Signed-in video viewer, Read it on signed-in pages | VideoLinkCards, readIt.ts | none | open in the browser |

Not covered, as fine for a product (with attribution or approval where
noted): Audiobookshelf (self-hosted), MusicBrainz, Wikidata, Wikipedia
(CC BY-SA: attribute), ListenBrainz, iTunes Search (link back to Apple),
podcast RSS, the Google People API and Spotify's Web API (both need the
provider's app review for a public app), YouTube oEmbed and embeds,
Read it on public pages (copyright of stored articles is a separate
question), contacts from the user's own data exports.

See also `2026-10-19-opening-to-the-public-legally.md` (bridges and
WebViews) and `2026-09-29-licensed-music.md` (the radio gate).
