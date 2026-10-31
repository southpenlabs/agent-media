# Linking YouTube and Spotify for music preferences (plan, 1 Nov 2026)

David, 1 Nov 2026, from the Media tab: "I'd like to also link my YouTube
and Spotify accounts for preferences". He chose "both, plan first".

## Where it plugs in

The accounts group in Settings → Music → About you (§6.42,
`personal_prefs.py`) already does this for ListenBrainz: a sign-in, a kept
secret (`personal.json`, 0600), and a source line saying what is missing.
YouTube and Spotify become two more sign-ins there, read by the same places
that read ListenBrainz today:

| reader | what it gets |
|---|---|
| favourites gather (`music_bed_favourites.gather`) | liked or saved songs (5 points, as a like), top tracks (points by rank); named through Mopidy's YouTube Music as ListenBrainz ones are |
| distinctiveness (`rate_renown`) | nothing new: the songs it rates just come from more places |
| radio / Claude DJ (`radio_dj`) | his top artists and recent plays as taste, so a station starts closer to him |
| Media tab Liked list | optional: his Spotify saved and YouTube liked songs beside the in-app likes |

Read only, everywhere. Nothing is ever written back to either account.

## Spotify

- **Setup, once (David):** make an app at developer.spotify.com (free).
  Redirect URI `https://red5.sasonica.com/accounts/spotify/callback` (Spotify
  allows only https, or a loopback address, for redirects). The app stays in
  development mode, which is fine for one listener (allowlisted users only).
  Paste the client ID into Settings; there is no client secret (PKCE).
- **Sign-in:** Authorization Code with PKCE, opened from the app; the server
  keeps the refresh token as a secret field (`spotify.token`).
- **Scopes:** `user-top-read`, `user-library-read`,
  `user-read-recently-played`.
- **Reads:** `/me/top/tracks?time_range=long_term|medium_term`,
  `/me/tracks` (saved), `/me/player/recently-played`, `/me/top/artists`.
- Spotify's own audio features and recommendations endpoints are closed to
  new apps, so tempo stays our own measurement (`music_bed_tempo`).
- Playback stays YouTube: Spotify only tells us what he likes.

## YouTube: reuse yt-profile (David: "I think we may have already done this
## for audiobookshelf or Iris. can we reuse that?")

Yes. yt-profile (dotfiles; `~/.config/yt-dlp/README-yt-profile.md`) already
has, per Google account, a cookie jar, YouTube Music API headers (what Iris
lists "My Youtube playlists" with), and optionally an auto-refreshing OAuth
token from its own Google OAuth client (device flow, `yt-profile oauth
<name>`). Found on red5, 1 Nov 2026:

- `davidj4music`: the headers work (135 YouTube Music history items, 0 YT
  Music likes); an OAuth token `davidj4music-oauth` exists (scope
  `youtube`) but is untested.
- `davidj4` (main): the jar dates from 16 Jun and the music API refuses it,
  so it has expired. One `yt-profile oauth davidj4-oauth` (a code entered at
  google.com/device) fixes that for good.

So no new Google Cloud project, and no unverified-app question: the reader
uses yt-profile's files for each account named in
`MEDIA_MUSIC_ACCOUNTS_YOUTUBE` (default `davidj4,davidj4music`):
YouTube Music history and liked songs (ytmusicapi), and YouTube liked
videos in the Music category (Data API, with the OAuth token, if that API
is on in the client's project). Audiobookshelf has nothing for Spotify or
YouTube; Iris's Spotify sign-in goes through Iris's own auth server and
keeps nothing on red5.

The routes below were the fallback before this was found.

Two routes. I recommend A.

- **A. YouTube Data API, read only.** A Google Cloud project, the YouTube
  Data API on, an OAuth client (type Web), scope `youtube.readonly`. Reads
  his liked videos (`videos?myRating=like`), his playlists and their items,
  and his subscriptions (as artist taste). This gets only what he has liked
  or saved, not his listening history.
  - Gotcha: while the OAuth consent screen is in "Testing", Google expires
    refresh tokens after 7 days, so the link would die every week.
    Publishing it ("In production", unverified, one user) keeps the token,
    at the cost of a "Google hasn't verified this app" screen at sign-in.
    login-watch would alert when the token stops working, as it does for
    the other logins.
- **B. YouTube Music via ytmusicapi.** Also reads his YT Music history and
  library songs, which is richer for music. But its sign-in is browser
  cookies (they expire and break silently) or the same Google Cloud client
  as A. More fragile; worth it only if A's likes turn out too thin.

## Questions for David

1. Which Google account holds the YouTube likes that matter? (He has
   several; the persona accounts would only add noise.)
2. Spotify: is there an account with real listening history, or would it
   be mostly empty?
3. For A: is the "unverified app" screen at sign-in acceptable, in return
   for a link that doesn't expire weekly?

## Build order

1. Spotify: the sign-in field, the PKCE callback route, the gather reader,
   and the tests (no network in the tests, as for ListenBrainz).
2. YouTube A: the same pieces for Google.
3. Both into the radio DJ's taste, then optionally the Liked list.
4. login-watch checks for both tokens.
