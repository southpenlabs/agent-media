# Show filters for the Media tab's tabs

David, 31 Oct 2026: "let's get some relevant show filters going for each
tab. one that we seemed to have lost with the redesign is recently played"
… "Can we get some ideas from other sites and apps?"

## Built (31 Oct 2026)

Each tab's Show ▾ has two parts: **List**, then **Group by**.

| Tab | List | Group by |
|---|---|---|
| Music | Recently played, Liked, Most played | Artists, Albums, Genres |
| Books | Recently played, In progress, Finished, All books, Most played | Authors, Series, Genres |
| Podcasts | Recently played, New episodes, Most played | Shows, Genres |
| Meditations | All, or one source: Written by the agent, YouTube, Folder, Podcasts | (the sources are the groups) |

Server: `GET /music/recent?kind=…&list=…` (server-contract.md §6.9a,
`music_recent.listed`). Books' lists and new episodes come from
Audiobookshelf; Liked from the likes `media music like` keeps; Most played
counts plays in the history.

## What other apps offer

From memory of how these apps worked as of mid-2026. I haven't checked
them against the current versions.

- **Spotify (Your Library):** chips for Playlists, Artists, Albums,
  Podcasts, Audiobooks, Downloaded; sort by Recents, Recently added,
  Alphabetical, Creator; Liked Songs as a list of its own; search in the
  library; a list or grid view.
- **YouTube Music (Library):** chips for Playlists, Songs, Albums, Artists,
  Podcasts; sort by Recent activity, Recently added, A–Z; Downloads; Liked
  music as an automatic playlist; a "Listen again" shelf on Home.
- **Apple Music:** Recently Added on the library's front; sort by title,
  artist, recently added or played; Favourites; Downloaded only.
- **Audible:** library filters Not started, In progress, Finished; sort
  by Recent, Title, Author, Length, Release date; Collections; Downloaded;
  "Continue listening" at the top.
- **Audiobookshelf:** Continue listening, Recently added, Recent series,
  Discover, Listen again; filters by progress, genre, tag, series, author,
  narrator, language; sort by title, author, added, published, duration,
  size; Collections and playlists.
- **Pocket Casts:** New releases, In progress, Starred, Downloads,
  Listening history; podcasts sorted by name, date added or latest
  episode; per-show "hide played".
- **Insight Timer / Calm / Headspace (meditations):** filter by length
  (under 10 min, 10–20, 20+), by teacher, by purpose (sleep, anxiety,
  focus, morning); Favourites; History; "Continue" for courses.

## Ideas worth taking next

1. **Sort** beside Show: Newest, A–Z, Longest (every app has one). Cheap
   in the app for lists it already has.
2. **Search** within the tab's list (Spotify, Audiobookshelf), the header
   David mentioned: Show, Sort and a search field together.
3. **Meditations by length**: under 10 min, 10–20, 20+ (every meditation
   app). Needs durations for the folder files and the agent's renders.
4. **Continue listening** on Now playing when nothing plays: the book in
   progress and the last podcast, one tap each (Audible, Audiobookshelf,
   Pocket Casts).
5. **Hide played / Unplayed** for podcasts and new episodes (Pocket Casts).
6. **On this phone** (Downloaded): what plays without the network.
7. **Listen again** shelf: the most played as a row of pictures rather
   than a list (YouTube Music).
