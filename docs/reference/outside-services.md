# Outside services: what agent-media and the Sasonica app depend on

Written 13 Nov 2026, after Trakt's device page said "This app is still in
development. Only its developer and their testers can connect it" and David
asked: "when we're ready for production we're going to have to reflect that
in a whole bunch of the services we're offering... do you have a record of
all those services?" There was none. This is it.

It lists every service outside the install that the server
(`agent-media`) or the app (`sasonica-app`) talks to: what it is used for,
how it is reached, whose credential it uses, the mode it is in today, what
its terms ask of a product, and what has to change before people other than
the owner use it.

How to read it:

- **Blocks** means a public release cannot go out with this as it is: the
  service is capped, closed to this kind of app, non-commercial only, or
  reached in a way its maker does not offer.
- **Needs work** means there is a known step (a form, a credit line, a
  setting), and nothing stops it.
- **Fine** means nothing to do beyond what the code already does.
- "Personal use only" means the feature is gated by
  `personal_use.allowed(...)` (`MEDIA_PERSONAL_USE`, off in the package;
  `packages/core/src/agent_media_core/personal_use.py:34`), so a public
  install does not run it. See `docs/decisions/2026-11-02-personal-use.md`.
- Terms were read on 13 Nov 2026 and are quoted with their address. Where a
  page would not load or does not settle the point, the entry says **check
  current terms** or **to confirm**. None of this is legal advice;
  `docs/proposals/2026-10-19-opening-to-the-public-legally.md` already says
  a lawyer looks before anything is public.

Not listed, because they run on the install's own machines: Mopidy,
Snapcast, mpv, Piper, Kokoro, Home Assistant, the Matrix homeserver, notmuch,
Ollama or a LiteLLM gateway, Termux. Their licences are in `NOTICE` and
`docs/decisions/2026-09-22-licensing.md`.

## Summary

Sorted with what blocks a public release first. 47 rows.

| # | Service | Used for | Credential | Mode today | Needs for production | Personal use only? |
|---|---|---|---|---|---|---|
| 1 | Microsoft Edge Read Aloud (edge-tts) | the default voice, on the server and on the phone | none (a token copied from Edge) | an endpoint Microsoft does not offer to others | **Blocks.** Replace the default: a local engine (Piper, Kokoro), the phone's own voice, or a paid API with the user's key | **No, not gated** |
| 2 | Spotify Web API | music taste for the DJ and favourites; play on the listener's devices | the owner's own app (client ID in Settings, PKCE) | Development Mode: 5 users, owner needs Premium | **Blocks** as a shared app (extended access is for organisations with 250k monthly users). Per-install own app only, or drop | reading: no; reply tune and speaker: yes |
| 3 | TMDb | posters, trailers, where to watch | the owner's own key | free key, non-commercial | **Blocks** a paid product: a commercial licence from TMDb, or keep each install on its owner's key and confirm that is allowed | no |
| 4 | Trakt | Movies and Shows tabs | the owner's own API app | Development app: its maker plus 16 invited people | **Needs work**: request Production access (free, reviewed), https redirect, privacy policy, the "not endorsed" notice; decide one product app or one per install | no |
| 5 | Open-Meteo | weather for the music bed; city lookup | none | free endpoint, non-commercial only | **Blocks** a paid product on the free endpoint: a paid plan, a self-hosted Open-Meteo, or another source; add credit | no |
| 6 | Google People API | contacts book | the owner's own Google OAuth client | per-install client, unverified | **Needs work**: a shared client needs Google's verification (sensitive scope); a per-install client is capped at 100 users and shows a warning | no |
| 7 | YouTube through yt-dlp, Mopidy-YouTube, ytmusicapi | music playback, radio, downloads, lengths, account likes | none, or copied browser sign-ins | against YouTube's terms; already moved to an add-on | Nothing to register. The product has **no music source of its own** without it: decide what public installs play | **yes** (six methods) |
| 8 | Apple iTunes Search API and Apple's chart feeds | podcast search and charts, top songs and albums, show profiles, shared episode links | none | public, keyless; about 20 calls a minute | **Needs work**: Apple's terms are for promoting store content beside a badge; likely not a general podcast directory. Podcast Index is the usual replacement | no |
| 9 | Anthropic OAuth usage endpoint | the `/usage` card | the host's own Claude login | an undocumented endpoint | **Needs work**: gate it or drop it for public installs | **No, not gated** |
| 10 | Coding agents' subscriptions (Claude Code, Codex, Gemini, Grok, pi, opencode, Hermes, Antigravity) | the chats themselves | each user's own login or API key | each user's own account | check current terms of each on driving a subscription login from another program, and on a host serving several people | no |
| 11 | Nominatim (public) | `media place find`; Victoria's camera roads | none (named client) | public instance, 1 request a second | **Needs work**: the limit is summed over all users of an app; ship with a geocoder setting per install (exists) and plan a hosted or paid one | no |
| 12 | Overpass (public instances) | camera layers on the Map | none (named client) | public instances | **Needs work**: "commercial use should use self-hosted or paid Overpass servers"; the weekly-extract path already avoids it | no |
| 13 | Cloudflare quick tunnel | a stranger's install reachable from the phone | none | `trycloudflare.com`, no account | **Needs work**: check current terms for quick tunnels in a product and for audio through a tunnel; named tunnels on the user's own account are the alternative | no |
| 14 | Messaging bridges (Messenger, Instagram, Google Messages, Teams) | other networks in Messages | the user's own sign-in cookies | unofficial clients; add-on | Not in the product. Official business APIs if ever sold (see the legal proposal) | **yes** |
| 15 | Facebook and Instagram stories, Facebook previews, signed-in reading | stories row, link cards, friends-only video | the app's stored sign-ins | reframing their sites | Not in the product | **yes** |
| 16 | Insight Timer | saved practices, teachers' profiles | none; the user's own sign-in for premium tracks | widget for everyone; page player and profiles personal | **Needs work**: no published embed terms found; ask Insight Timer, or keep to a link out | page and profiles: yes; widget and pictures: no |
| 17 | Spotify lists played from YouTube; Spotify Connect speaker | shared Spotify links; house speakers | none / the user's Spotify | undocumented embed page; unofficial client; add-on | Not in the product | **yes** |
| 18 | Community camera reports | phone and seatbelt camera reports on the Map | none (read through the phone) | a community site; add-on | Not in the product | **yes** |
| 19 | Stremio | "Open in Stremio" | none | deep link for everyone; its web app inside ours is personal | Nothing for the link | page: yes |
| 20 | Google Fonts | the app's two typefaces, fetched at run time | none | loaded from Google on every start | **Needs work** (small): bundle the fonts (both OFL), so no request goes to Google | no |
| 21 | GitHub | install scripts, releases, app updates, CI | the owner's `gh` login for the private app repo | app repo private | **Needs work**: a public place for the APK and its updates | no |
| 22 | OpenAI | optional voices, realtime voice, speech-to-text, spoken summaries, pictures | the user's own key | off unless set | Tell listeners the voice is AI-generated (OpenAI's rule); check current terms on user-supplied keys | no |
| 23 | Groq, OpenRouter, Google Gemini API | speech-to-text; free coding models | the user's own key | off unless set | nothing; each user's own account | no |
| 24 | Alibaba DashScope (Qwen voices) | optional voice engine | the user's own key | off unless set | nothing | no |
| 25 | Venice | canvas pictures | the user's own key | off unless set | nothing | no |
| 26 | TypeSafe (Jev) | "is this a good moment to speak" | the user's own key | shadow mode, off without a key | nothing | no |
| 27 | sasonica.com workers (lookup, facts, feedback) on Cloudflare | finding an install; shared lengths; feedback | per-install Ed25519 key | the project's own, on Cloudflare Workers, D1 and KV | a privacy policy line for each; watch Workers plan limits | no |
| 28 | cms.sasonica.com (Drupal) with Google sign-in | accounts, enrolling a device | the project's Google OAuth client | published 8 Oct 2026, sign-in scopes only | terms link still to add; GitHub and Facebook sign-in need their own apps | no |
| 29 | sasonica.com shop and licence keys | what an install has paid for | the project's signing key | built; checked offline | a payment provider's terms when it sells (not in this repo) | no |
| 30 | OpenFreeMap | map tiles | none | public instance, free, commercial allowed | nothing; credit is shown; no uptime promise | no |
| 31 | OpenStreetMap data (Geofabrik extracts) | cameras, speed limits | none | weekly download, kept locally | credit shown; ODbL notice with any database we hand on | no |
| 32 | Victorian Government camera sites | phone and seatbelt camera roads | none | monthly spreadsheet; off unless `MEDIA_MAP_VIC=1` | credit shown; confirm the licence on the page | no |
| 33 | MusicBrainz | artists, albums, recordings | none (named client) | public API, 1 request a second | sign up with MetaBrainz as a commercial user (tiers from $0) | no |
| 34 | Cover Art Archive | album covers | none | public | check current terms on showing covers in a paid product | no |
| 35 | ListenBrainz | recommendations, likes, fresh releases | the listener's own name (and token) | public API, 1 request a second | as MusicBrainz | no |
| 36 | Wikipedia and Wikidata | profiles of artists, authors, shows | none | public APIs | credit Wikipedia text (CC BY-SA) where shown: to confirm it is | no |
| 37 | Internet Archive | free recordings for the music bed; free films that play in the app (`watch_free.py`, §6.54 "Free films") | none (named client) | public APIs | nothing; licences are filtered and kept (films: only items stating public domain or Creative Commons, streamed by the device from archive.org) | no |
| 38 | ccMixter | CC BY instrumentals for the music bed | none | public query API | show each track's credit where it plays: to confirm | no |
| 39 | Podcast RSS feeds | episodes of followed shows | none | each publisher's own feed | nothing | no |
| 40 | YouTube embeds, oEmbed and thumbnails | trailers, video cards, link titles | none | the official player and oEmbed | follow the player rules (size, no overlay, no background play) | no |
| 41 | Facebook video plugin | public Facebook video cards | none | Meta's embed plugin | nothing | no |
| 42 | Streaming services' search pages | "Open in Netflix" and the like | none | plain links | nothing; addresses are best-effort | no |
| 43 | JustWatch (through TMDb) | "Where to watch" | TMDb's | via TMDb's endpoint | credit JustWatch (done) | no |
| 44 | Calendar links (Google, Outlook ICS) and the user's own mail | calendar events; reading mail | the user's secret link; their own mailbox | read only | nothing | no |
| 45 | Google Takeout and Meta data exports | contacts from the user's own exports | none | files the user downloads | nothing | no |
| 46 | Android platform | the app | the project's signing key | sideloaded APK; no Firebase, no Play services | Play's policies only if it goes to Google Play | no |
| 47 | Build-time fetches (jsDelivr, npm, PyPI) | vendoring MapLibre and the like | none | at build only | nothing | no |

## The services, one by one

### 1. Microsoft Edge Read Aloud (edge-tts)

- **Used for:** the default voice. Core ships one engine, `edge`, "zero-config
  (no API key)" (`packages/core/src/agent_media_core/render/engines.py:1`,
  `defaults.env:23`, `packages/core/pyproject.toml:16`). The phone asks the
  same service itself (`sasonica-app`
  `android/app/src/main/java/com/sasonica/app/speech/EdgeVoice.java:50`).
- **How:** the websocket Edge's own Read Aloud feature uses, with a client
  token copied from Edge and a version string. The app's own comment: "The
  service is Edge's Read Aloud, which is not an offered API: it is spoken
  the way the edge-tts project found it spoken, and it can change under us."
  It has changed twice in six weeks: `engines.py:38` patches the installed
  library to a new host, and `entrypoints/edge_voice_watch.py` is a daily
  alarm for the next change.
- **Terms:** no Microsoft page offering this endpoint to other programs was
  found. The offered product is Azure Speech (free tier "0.5 million
  characters free per month",
  https://azure.microsoft.com/en-us/pricing/details/cognitive-services/speech-services/).
- **Gated?** No. It is the one unofficial endpoint that is on by default
  for every install.
- **Production:** a public install should default to something the user may
  use: Piper or Kokoro on the server (`packages/engine-piper`,
  `packages/engine-kokoro`), the phone's own voice (already the fallback in
  `Media3Speech`), or a paid engine with the user's key. Keep `edge` as a
  personal-use method, in `personal_use.METHODS`, like the others.

### 2. Spotify Web API

- **Used for:** the listener's top, saved and recent tracks as taste
  (`music_accounts.py:141`, scopes at `:159`); playing a reply's tune on
  their devices (`spotify_remote.py`, `reply_tune.py`, gated as
  `spotify-reply-tune`).
- **How:** the official API, Authorization Code with PKCE, callback
  `/accounts/spotify/callback` (`packages/server/src/agent_media_server/spotify_auth.py`).
- **Credential:** the install owner's own Spotify developer app; its client
  ID is typed into Settings (`spotify.client_id`) or
  `MEDIA_SPOTIFY_CLIENT_ID`.
- **Mode today:** Development Mode. "Up to 5 authenticated Spotify users
  can use an app that is in development mode" and "The app owner must have
  a Spotify Premium account"
  (https://developer.spotify.com/documentation/web-api/concepts/quota-modes).
  Development apps lost several endpoints in February and March 2026
  (https://developer.spotify.com/documentation/web-api/tutorials/february-2026-migration-guide).
- **Leaving it:** extended access is "only ... from organizations (not
  individuals)" with "at least 250k MAUs" and a launched service (same
  page). Out of reach for now.
- **Terms that bite** (https://developer.spotify.com/policy):
  - III.13 "Do not analyze the Spotify Content or the Spotify Service for
    any purpose": reading top tracks to build a taste profile is close to
    this. To confirm.
  - III.5 "Do not create any product or service which is integrated with
    streams or content from another service": why `spotify-via-youtube` is
    personal use only.
  - III.7 no mixing or overlapping with other audio: a reply tune under
    speech is close to this (already gated, with III.3 and III.5 named in
    `personal_use.py:60`).
  - IV.2 "commercial uses are not permitted" for apps that stream.
  - II.4 metadata must carry Spotify's marks and a link back.
  - VI.1.c of the Developer Terms: "You may not sell, transfer, sublicense
    or otherwise disclose your account or Security Codes"
    (https://developer.spotify.com/terms). Each user making their own app
    is not addressed; the product must never ship a shared client ID.
- **Production:** either keep it as "bring your own Spotify developer app"
  (works for at most 5 people per app, each owner on Premium) and say so in
  Settings, or leave Spotify out of public installs. ListenBrainz already
  gives the same taste signal with no cap.

### 3. TMDb

- **Used for:** posters, the trailer, the region's streaming services
  (`packages/core/src/agent_media_core/tmdb.py:35`).
- **Credential:** the owner's `MEDIA_TMDB_TOKEN` or `MEDIA_TMDB_API_KEY`
  (`agent-media.env.example:527`). Nothing calls TMDb without one.
- **Mode today:** a free personal key.
- **Terms:** "free to use for non-commercial purposes as long as you
  attribute TMDB as the source"
  (https://developer.themoviedb.org/docs/faq). The licence "does not permit
  any commercial use", and commercial includes "Charging users a fee for
  Your Application" (https://www.themoviedb.org/api-terms-of-use, 2.A).
  Caching is capped at 6 months (1.C). The same clause bars using the APIs
  with ML or AI-based applications: to confirm what that means for an
  assistant that shows posters.
- **Already complies:** the notice is in the answer and the app prints it
  (`tmdb.py:42`, `sasonica-app/app/components/WatchTab.tsx:737` and `:922`);
  posters are kept a month, details a day (`tmdb.py:39`). The notice's
  wording follows the FAQ; the terms page words it slightly differently.
  The FAQ asks for it in an About or Credits section too: the app's About
  page has no TMDb line yet (`app/routes/about.tsx:32`).
- **Production:** a paid Sasonica needs TMDb's commercial licence
  (sales@themoviedb.org; no price published). Whether "each install uses
  its owner's free key inside a paid product" is allowed is not stated: ask
  TMDb. A shared project key is ruled out ("non-transferable,
  non-sublicensable").

### 4. Trakt

- **Used for:** what was watched, the watchlist, the next episode
  (`packages/core/src/agent_media_core/trakt.py:52`, `trakt_browse.py`).
- **Credential:** the owner's own Trakt API app:
  `MEDIA_TRAKT_CLIENT_ID`, a secret only where Trakt issued one
  (`agent-media.env.example:517`). New apps get none and sign in with PKCE.
- **Mode today:** a Development app. "Every app starts as a Development
  app: you and up to 16 people you invite can sign in"
  (https://github.com/trakt/trakt-api/blob/master/projects/developer/src/lib/guides/create-an-app.md).
  That is the message David saw.
- **Leaving it:** "open it from My Apps and choose Request Production
  access"; a review "usually within a few business days"; a separate
  Production client ID. A production app needs https redirect URIs, a short
  description, a privacy policy link and a link showing the notice.
- **Terms:** "Standard API access is free for commercial and noncommercial
  use"
  (https://github.com/trakt/trakt-api/blob/master/projects/developer/src/lib/guides/api-use-policy.md).
  The notice: "[App name] uses Trakt but is not endorsed or certified by
  Trakt", in About or Settings and in the store description. Images "must
  be cached". Limits: 500 GETs per 5 minutes per user, 1 write a second.
- **Not done yet:** the notice is nowhere in either repo; the redirect
  defaults to `http://localhost` with the address pasted back
  (`trakt.py:25`), which Production does not allow.
- **Production:** Trakt's model is one app per product, and the policy
  forbids making extra apps to get round limits. So: register "Sasonica"
  as a Production app with a redirect on a project address, ship its client
  ID (PKCE has no secret to protect), add the notice. A self-hosted tool
  syncing a person's own account is listed as permitted, so "your own app"
  can stay as the fallback. Trakt's general Terms of Use would not load:
  check current terms at https://app.trakt.tv/terms.

### 5. Open-Meteo

- **Used for:** the current weather that picks a music bed
  (`music_bed.py:511`, `MEDIA_MUSIC_BED_WEATHER`), and finding the
  listener's city and forecast (`personal_prefs.py:73`, `:77`).
- **How:** the free, keyless endpoints `api.open-meteo.com` and
  `geocoding-api.open-meteo.com`.
- **Terms:** "You may only use the free API services for non-commercial
  purposes", under 10,000 calls a day; commercial includes "Websites or
  apps that have subscriptions" (https://open-meteo.com/en/terms). Credit
  is required (CC BY 4.0, https://open-meteo.com/en/pricing).
- **Not done yet:** no credit line is shown anywhere.
- **Production:** small. A paid plan with a key, or a self-hosted
  Open-Meteo (it is open source), or a setting for the address so each
  install chooses. Add the credit.

### 6. Google People API (contacts)

- **Used for:** reading the owner's Google contacts into the book
  (`packages/server/src/agent_media_server/contacts.py:118`, scope
  `contacts.readonly` plus `openid email`).
- **Credential:** an OAuth client of the owner's own Google Cloud project
  (`MEDIA_GOOGLE_OAUTH_CLIENT`, `contacts.py:1210`).
- **Mode today:** per install, unverified. To confirm which publishing
  status the owner's project is in.
- **Terms:** "Apps that request access to scopes categorized as sensitive
  or restricted must complete Google's OAuth app verification"
  (https://support.google.com/cloud/answer/13463073). In Testing, at most
  100 listed users and "Authorizations by a test user will expire seven
  days from the time of consent"; published but unverified shows a warning
  and stops at "100 new users in total"
  (https://support.google.com/cloud/answer/15549945). Contacts is not on
  the restricted list (https://support.google.com/cloud/answer/13464325),
  so no yearly security assessment; that it is "sensitive" is to confirm at
  https://developers.google.com/identity/protocols/oauth2/scopes. Google's
  Limited Use rules apply
  (https://developers.google.com/terms/api-services-user-data-policy).
- **Production:** two roads. One shared "Sasonica" client: verification
  (owned domain, home page, privacy policy saying how Google data is used),
  and a redirect that works for every install, which means a project-run
  relay. Or each install its own client: no review, but a warning screen
  and setup most people will not manage. The exports path (row 45) needs
  neither and already exists.
- **Never add** Gmail's `https://mail.google.com/` scope to a shared
  client: it is restricted and brings the yearly assessment.

### 7. YouTube outside its player

- **Used for:** most of the music: the house speakers' and the phone's
  playback, the radio's Mix, Discover's "play this" (a name found by a
  YouTube search, `music_discover.py`), the music bed's stream, audiobook
  and podcast downloads, lengths, and reading the listener's likes.
- **How:** yt-dlp, Mopidy-YouTube and ytmusicapi with sign-ins copied from
  a browser. No API key.
- **Terms:** YouTube's terms bar downloading "except: (a) as specifically
  permitted by the Service" (https://www.youtube.com/t/terms); the API
  policies bar storing copies, separating audio from video and playing from
  a background player
  (https://developers.google.com/youtube/terms/developer-policies). Google
  Play refuses "Apps that access or use a service or API in a manner that
  violates its terms of service"
  (https://support.google.com/googleplay/android-developer/answer/9888379).
- **Gated?** Yes, all six methods, and moved out to an add-on the user
  installs (`youtube_addon.py`, `personal_use.py:46`;
  `docs/proposals/2026-11-02-personal-use-addons.md`).
- **Production:** nothing to register. The open question is the product's:
  without the add-on a public install can hand a song to the listener's own
  music app (`HandoffMusic.java`) and play local files, and nothing else.
  `docs/proposals/2026-09-29-licensed-music.md` is the place that decides.

### 8. Apple: iTunes Search API and chart feeds

- **Used for:** podcast search, lookup and the top chart
  (`podcast_discover.py:32`), an episode's audio from a shared Apple link
  (`podcast_resolve.py:37`), show profiles (`media_people.py:62`), cover
  and metadata lookups (`media_meta.py:231`), top songs and albums
  (`music_discover.py:56`, `rss.marketingtools.apple.com`).
- **How:** public, keyless endpoints.
- **Terms:** "limited to approximately 20 calls per minute"; content may be
  used "only to promote store content and not for entertainment purposes",
  with artwork "proximate to a store badge"
  (https://performance-partners.apple.com/search-api). The chart feed's
  page carries no terms: check current terms at
  https://rss.marketingtools.apple.com/.
- **Already complies in part:** answers are kept an hour; a show's profile
  links to its Apple page. No badge is shown.
- **Production:** most podcast apps use this directory, and Apple's wording
  does not clearly allow it. Move search and charts to Podcast Index (free
  key, made for this) and keep Apple only for resolving a shared Apple
  link. Podcast Index and its terms were not read for this list: check
  https://podcastindex.org/. Whether the 20-a-minute limit counts per
  address or per app is not stated.

### 9. Anthropic OAuth usage endpoint

- **Used for:** the `/usage` card
  (`packages/server/src/agent_media_server/plan_usage.py:30`).
- **How:** `https://api.anthropic.com/api/oauth/usage`, asked with the
  host's own Claude login. It is what Claude Code's own screen reads, and
  it is not a documented API.
- **Gated?** No.
- **Production:** add it to `personal_use.METHODS`, or show the card only
  where a documented source exists.

### 10. The coding agents

- **Used for:** every agent chat. The server starts and reads Claude Code,
  Codex, pi, opencode, Hermes, Gemini, Grok and Antigravity
  (`packages/core/src/agent_media_core/harnesses.py`), each signed in with
  the user's own account or key. Installers are fetched from their makers
  (`harnesses.py:1819`, `:1896`, `deploy/android/install.sh:129`).
- **Credential:** each person's own; never the project's
  (`docs/proposals/2026-10-29-a-server-per-person.md`: logins stay in each
  person's home).
- **Production:** check current terms of each maker on two points: running
  a subscription login from another program rather than their own app, and
  one machine serving several people. Anthropic:
  https://www.anthropic.com/legal/consumer-terms and
  https://www.anthropic.com/legal/aup. OpenAI:
  https://openai.com/policies. An API key is always the safe form.

### 11. Nominatim

- **Used for:** `media place find` (`place_find.py:26`,
  `MEDIA_GEOCODER_URL`) and the roads of Victoria's camera sites
  (`map_vic.py:36`).
- **Terms** (https://operations.osmfoundation.org/policies/nominatim/): "an
  absolute maximum of 1 request per second"; a User-Agent naming the
  application; "the usage limits above apply per website/application: the
  sum of traffic by all your users"; "Apps must make sure that they can
  switch the service at our request at any time"; no autocomplete.
- **Already complies:** named client, one a second, results kept, and the
  address is a setting.
- **Production:** fine while installs are few. Before many, point the
  default at a geocoder the project runs or pays for.

### 12. Overpass

- **Used for:** speed and surveillance cameras on the Map
  (`packages/server/src/agent_media_server/map_osm.py:52`,
  `MEDIA_OVERPASS_URL`: overpass-api.de, then two mirrors).
- **Terms:** under 10,000 queries and 1 GB a day, counted over all of an
  app's users; "Commercial use should use self-hosted or paid Overpass
  servers" (https://wiki.openstreetmap.org/wiki/Overpass_API). The
  private.coffee mirror allows "any project ... including commercial use"
  and asks for a User-Agent (https://overpass.private.coffee/). The
  maps.mail.ru mirror's terms were not read: check, or drop it from the
  default.
- **Production:** make the weekly extract (`MEDIA_MAP_OSM_EXTRACT`) the
  documented path and put private.coffee first in the default list.

### 13. Cloudflare tunnels

- **Used for:** reaching a server with no domain: `sasonica install` runs
  `cloudflared tunnel --url`, a quick tunnel with no account
  (`packages/server/src/agent_media_server/tunnel.py:1`). An install with
  its own domain uses a named tunnel on its owner's Cloudflare account
  (`server_address.py`, `docs/reference/server-address.md`).
- **Terms:** Cloudflare's CDN terms reserve the right to limit accounts
  that "serve video and other large files" without its paid media products
  (https://www.cloudflare.com/service-specific-terms-application-services/).
  No rule specific to tunnels was found. Quick tunnels are described by
  Cloudflare as for testing: check current terms at
  https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/do-more-with-tunnels/trycloudflare/.
- **Production:** fine for a first run. A lasting install should move to a
  named tunnel or its own address, and the audio a phone streams through
  the tunnel is the part to ask Cloudflare about.

### 14. Messaging bridges

Unofficial clients of Messenger, Instagram, Google Messages and Teams
(mautrix bridges), their cookie sign-in and Find on Messenger. Personal use
only and in the add-on (`bridge_addon.py`, `personal_use.py:41`); SMS goes
through the same bridge (`sms.py`). The official routes a product would
sell are listed in
`docs/proposals/2026-10-19-opening-to-the-public-legally.md`. Matrix itself
is the user's own homeserver and is fine.

### 15. Facebook and Instagram, reframed

The stories row (`facebook-stories`), Facebook pages read as Facebook's own
previewer (`facebook-previews`, `link_preview.py`), and pages read with
stored sign-ins (`signed-in-reading`): all personal use only
(`features.py`). The public build of the app has none of the sign-in
browser code (three build types since 4 Nov 2026; the CI check that proves
it is still to write, per the legal proposal).

### 16. Insight Timer

- **Used for:** the user's saved tracks, listed from their own export and
  played in Insight Timer's widget (`practice_embed.py`,
  `server/practice.py`); premium tracks play signed in
  (`sasonica-app/app/lib/practicePage.ts:81`).
- **Personal use only:** playing their track page in our own player
  (`insight-timer-page`) and teachers' profiles from internal endpoints
  (`insight-timer-profiles`, add-on).
- **Not gated:** the widget, and reading each track's public page once for
  its picture (`practice_embed.py:90`, `MEDIA_PRACTICE_EMBED_ART`).
- **Terms** (https://insighttimer.com/terms-of-service, v4.0): "You will
  only use the Services for your own internal, personal, non-political,
  non-commercial use"; no crawling or scraping (5(8)). No terms for the
  widget were found, and `widgets.insighttimer.com` did not answer.
- **Production:** ask Insight Timer whether the widget may sit in another
  app. Until they say yes, a public install should link out, and the
  picture fetch should be gated with the page player.

### 17 to 19. Other personal-use methods

- **Spotify lists from YouTube, Spotify speaker** (`music_links.py`,
  `spotify_auth.py`): add-on only.
- **Community camera reports** (`map_reports.py`): add-on only; the add-on
  knows the site, this repository does not name it.
- **Stremio** (`watch_open.py:155`): the "Open in Stremio" link is
  Stremio's documented deep link and is fine; showing web.stremio.com
  inside the app is `stremio-page`, personal use only.

### 20. Google Fonts

The app links `fonts.googleapis.com` for Fraunces and IBM Plex Sans on
every start (`sasonica-app/app/root.tsx:53`). Both are OFL and credited
(`app/routes/about.tsx:38`). For other people it means each start tells
Google the device's address, which a German court ruled in 2022 needs
consent, and the faces are missing offline. Bundle the files with the app.

### 21. GitHub

- **Used for:** the install scripts (`deploy/install.sh:49`,
  `deploy/android/install.sh:52`), `media release`, CI, and the app's
  self-update, where the server reads the private `sasonica-app` releases
  with the owner's `gh` login
  (`packages/server/src/agent_media_server/app_update.py:83`).
- **Production:** a stranger's server has no access to a private repo. The
  APK needs a public home (the website's publish step exists; the
  self-hosted F-Droid repo is parked in the roadmap) and `/app/update` a
  source that needs no login. GitHub Actions minutes and runner terms are
  the project's own account matter.

### 22 to 26. AI services with the user's own key

All off until a key is set, all called with a key the install's owner made:

- **OpenAI:** voices (`packages/engine-openai`), realtime voice
  (`packages/engine-realtime`), speech-to-text
  (`transcribe/__init__.py:69`), spoken summaries (`intake/_summary.py:23`),
  pictures (`packages/visual/src/agent_media_visual/generate.py:120`).
  OpenAI's rule for voices: "provide a clear disclosure to end users that
  the TTS voice they are hearing is AI-generated"
  (https://developers.openai.com/api/docs/guides/text-to-speech). The
  policies pages refused to load: check current terms at
  https://openai.com/policies/services-agreement.
- **Groq, OpenRouter, Gemini:** speech-to-text (`transcribe/__init__.py:67`
  to `:73`); OpenRouter also for opencode's free models
  (`opencode_keys.py:30`).
- **DashScope:** Qwen voices (`packages/engine-qwen`).
- **Venice:** canvas pictures (`generate.py:46`).
- **TypeSafe Jev:** `jev.py:59`, shadow mode by default.

Nothing to register. If the project ever supplies a key of its own (a
hosted tier), every one of these becomes a reseller question.

### 27 to 29. The project's own services

- **sasonica.com/r, /facts, /feedback:** three Cloudflare Workers with KV
  and D1 (`deploy/lookup`, `deploy/facts`, `deploy/feedback`), written to
  by each install under its own key (`tunnel.py`, `central_facts.py`,
  `feedback.py`). Facts holds lengths only, never anything drawn from
  someone else's site
  (`docs/proposals/2026-11-01-three-data-layers.md`). Feedback is on by
  default and sends only what a person writes. Each needs its line in the
  privacy policy.
- **cms.sasonica.com:** the Drupal that issues account sign-ins
  (`oidc.py:12`). Its Google sign-in is published and asks only for name
  and email, so it needs no verification; a terms link is still missing
  (roadmap, "Social sign-in").
- **Licences:** signed tokens checked offline
  (`docs/reference/entitlements.md`); the shop that mints them is outside
  this repository.

### 30 to 32. Maps

- **OpenFreeMap** (`sasonica-app/app/lib/mapLib.ts:11`): "Is commercial
  usage allowed?" "Yes."; "Attribution is required. If you are using
  MapLibre, they are automatically added"; no uptime promise
  (https://openfreemap.org/, https://openfreemap.org/tos/). The Map page
  uses MapLibre's own credit; the places card turns it off and prints its
  own (`PlacesCard.tsx:63`, `:105`). The style address should become
  something the server can name, so an install can move if the free
  instance ends.
- **OpenStreetMap data:** cameras and speed limits from a Geofabrik extract
  (`map_osm.py`, `map_limits.py`), credited "© OpenStreetMap contributors"
  (`map_osm.py:62`). ODbL share-alike applies only if the project hands on
  a database made from it
  (https://osmfoundation.org/wiki/Licence/Attribution_Guidelines).
- **Victorian Government:** `map_vic.py:34`, credited as CC BY 4.0
  (`map_vic.py:38`). The licence was not confirmed from the page: check
  https://www.vic.gov.au/copyright.

### 33 to 38. Open music and reference data

- **MusicBrainz** (`music_brainz.py:35`, `media_people.py:59`,
  `tune_related.py:62`): one request a second and a User-Agent of the form
  "Application name/<version> ( contact-url )"
  (https://musicbrainz.org/doc/MusicBrainz_API/Rate_Limiting), which
  `music_brainz.py:38` sends. Core data is CC0. MetaBrainz asks commercial
  users to sign up, with tiers from $0
  (https://metabrainz.org/supporters/account-type).
- **Cover Art Archive** (`music_brainz.py:36`, `music_discover.py:57`):
  "All images are copyrighted by their respective copyright owners"
  (https://coverartarchive.org/). Showing covers beside the music is what
  every player does; a paid product should check current terms.
- **ListenBrainz** (`music_discover.py:55`, `personal_prefs.py:74`,
  `music_bed_favourites.py:170`): "never make more than ONE call per
  second"
  (https://listenbrainz.readthedocs.io/en/latest/users/api/index.html). The
  listener's own name and optional token (`MEDIA_LISTENBRAINZ_USER`,
  `MEDIA_LISTENBRAINZ_TOKEN`). Its terms page did not load: check current
  terms at https://listenbrainz.org/terms-of-service/.
- **Wikipedia and Wikidata** (`media_people.py:60`, `:61`): Wikidata is
  CC0; Wikipedia's text is CC BY-SA and needs a credit and link where the
  words are shown. The profile carries `source` and a link; that the app
  prints it is to confirm. Wikimedia asks for a descriptive User-Agent
  (https://foundation.wikimedia.org/wiki/Policy:User-Agent_policy).
- **Internet Archive** (`music_bed_library.py:100`): only public domain,
  CC0 and CC BY items, the licence read from each item, title and creator
  kept (`music_bed_library.py:15`). Archive asks for a descriptive
  User-Agent (https://archive.org/developers/bots.html), which is sent.
  Its terms page did not load: check https://archive.org/about/terms.
- **ccMixter** (`music_bed_library.py:104`): CC BY uploads only. Its terms
  ask users to "attribute the work in the manner specified by the author"
  (http://ccmixter.org/terms). The credit is kept in
  `music-bed-library.json`; where a listener sees it is to confirm.

### 39 to 45. Links, embeds and the user's own data

- **Podcast feeds:** each publisher's RSS, fetched by the install
  (`practice_library.read_feed`). Nothing to do.
- **YouTube's player, oEmbed and thumbnails:** trailers and video cards
  play in the official embed on `youtube-nocookie.com`
  (`sasonica-app/app/lib/videoLinks.ts:113`), titles come from oEmbed
  (`music_links.py:100`, `link_preview.py`), thumbnails from `i.ytimg.com`.
  The player's rules: a viewport of "at least 200px by 200px", nothing
  drawn over it, no autoplay before it is visible
  (https://developers.google.com/youtube/terms/required-minimum-functionality).
  To confirm the card meets them.
- **Facebook video plugin:** public videos in Meta's own embed
  (`link_preview.py:35`).
- **Streaming services:** "Open in" keys are search links
  (`watch_open.py:39`); nothing is fetched from them.
- **JustWatch:** "In order to use this data you must attribute the source
  of the data as JustWatch"
  (https://developer.themoviedb.org/reference/movie-watch-providers). Done
  in `tmdb.py:42`.
- **Calendars and mail:** published ICS links the user pastes
  (`calendar_sources.py`), the host's own notmuch mail (`mail_read.py`).
- **Exports:** Google Takeout and Meta's download, read from files
  (`contacts_exports.py:60`, `:74`).

### 46 and 47. The platform and the build

- **Android:** no Firebase and no Play services (the Gradle plugin is
  applied only if `google-services.json` exists, and it does not:
  `sasonica-app/android/app/build.gradle:125`), so there is no push service
  to register; notifications ride the connection the phone opens. The APK
  is signed in CI with the project's key
  (`.github/workflows/build-apk.yml`). Google Play would add its own
  review: the microphone, camera, location, foreground-service and Termux
  permissions each need a declared reason there.
- **Build-time:** `packages/visual/vendor.sh:23` fetches from jsDelivr once;
  npm and PyPI at build. Nothing at run time.

### Related repositories

- **sasonica-shell:** sasonica.com, cms.sasonica.com, relay.sasonica.com
  and Cloudflare's API for its own tunnel; nothing beyond the rows above.
- **sasonica-web and the old `sasonica` app:** the Audiobookshelf forks,
  retired (`docs/decisions/2026-11-11-audiobookshelf-exit.md`).
- **websites:** the Drupal behind cms.sasonica.com and the public site; its
  own mail, payment and sign-in providers belong in a list of its own.

## Before other people use this

Ordered by effort, least first.

**An afternoon each**

1. Trakt: add the "uses Trakt but is not endorsed or certified by Trakt"
   line to About and Settings, then request Production access.
2. TMDb, Open-Meteo, Wikipedia, ccMixter: one Credits section on the app's
   About page naming each source the server says it used.
3. Bundle the two fonts; drop the Google Fonts link.
4. Put `/usage` (Anthropic's endpoint) behind `personal_use`.
5. Gate Insight Timer's picture fetch with its page player.
6. Overpass: private.coffee first, mail.ru out, the weekly extract
   documented as the normal way.
7. Sign up with MetaBrainz as a commercial user at the tier that fits.
8. Write the CI check that the public APK has no sign-in browser code.

**A few days each**

9. The default voice: make a local engine or the phone's own voice the
   default for a new install, and move `edge` to personal use.
10. Trakt: a redirect on a project address and a shipped Production client
    ID, so nobody else makes a Trakt app.
11. Podcasts: search and charts from Podcast Index instead of Apple.
12. Open-Meteo: a setting for the address and key, or a self-hosted one.
13. The app's updates and downloads from a public place, not a private
    GitHub repo.
14. A privacy policy and terms that name each service data goes to
    (Google asks for it, Trakt asks for it, YouTube's API terms would).

**Decisions, then weeks**

15. TMDb: ask about a commercial licence, or about each install using its
    owner's key.
16. Google contacts: verification for one shared client, or exports only.
17. Spotify: "bring your own developer app" with its 5-person cap, or out.
18. Music for public installs with no YouTube add-on
    (`docs/proposals/2026-09-29-licensed-music.md`).
19. Geocoding and tunnels at scale: a hosted geocoder, named tunnels.
20. The coding agents' terms on subscription logins, read by a lawyer with
    the bridges question.

## Keeping this current

Add a row here in the same change that adds a setting naming a service, a
key, a client ID, a token or an outside address (a new `MEDIA_*` in
`agent-media.env.example`, a new host in the code or the app). Say in the
row whether it is gated by `personal_use`. To find what this list may have
missed, list the hosts the code names:

```
git ls-files 'packages/*/src/*' deploy | xargs grep -hoE 'https?://[a-zA-Z0-9._-]+\.[a-z]{2,}' | sort | uniq -c | sort -rn
```
