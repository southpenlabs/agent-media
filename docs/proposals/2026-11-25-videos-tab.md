# A Videos tab: search and browse several video sites, YouTube first

25 Nov 2026. David asked for "better ways of searching and browsing
YouTube" and, offered a YouTube tab, his subscriptions and lists, filters,
and better Music and Practice searches, picked all four and added:

> rather than a YouTube section, maybe we can just have a videos section
> that searches multiple video sites... What do you think?

and, to the plan below: "Go ahead". Chat:
https://red2-ryer.sasonica.com/app/t/9b48a013-4f37-4e6e-b491-660cee8057cf

## What there is now

YouTube is searched in three places, each for one purpose:

- Music's + : songs from a YouTube search (`GET /music/discover list=search`).
- Practice's + and Discover: practices from a YouTube search (§6.45).
- A channel's page: its latest uploads (`GET /music/channel`, §6.9a).

Nowhere searches videos as videos, filters a search, or shows the
signed-in account's subscriptions, Watch later or playlists, although the
personal-use add-on already holds YouTube sign-ins (`music_accounts`,
"youtube-accounts").

## The shape

A **Videos** tab in Media. One box searches every video source the server
offers at once. Each row says which site it is from. The funnel opens the
account's lists. The server knows the sources (design rule: the app
renders what the server answers), so a new site is a server change only.

### Sources (each checked from red2, 25 Nov 2026)

| Source | How | Gated |
|---|---|---|
| YouTube | yt-dlp `ytsearchN:` / a results URL with filters, `--flat-playlist -J` (about 3 s for 3) | personal-use add-on |
| YouTube account | yt-dlp feeds `:ytsubs`, `:ytwatchlater`, `:ythistory`, `:ytrec`, the account's playlists, with the add-on's sign-in | personal-use add-on |
| PeerTube | Sepia Search, `https://sepiasearch.org/api/v1/search/videos` (no key; returns each video's licence) | no |
| Internet Archive | `archive.org/advancedsearch.php`, `mediatype:movies` (as Free films already does) | no |
| Dailymotion | `api.dailymotion.com/videos?search=` (no key) | no |
| Odysee | its search index, `https://lighthouse.odysee.tv/search?s=&mediaType=video&include=channel,title,duration,release_time` (no key); a video's page and stream through yt-dlp's `lbry` extractor | no |
| Rumble | not searched: no public search, and its search page refuses automated clients (403 from red2). A Rumble channel or video link is read by yt-dlp (`RumbleChannel`, `Rumble`) | no |

David asked for Odysee and Rumble too ("Can we also include Odyssee and
Rumble?"). Odysee is a source like the rest. Rumble comes in by link and
channel: a pasted Rumble link plays, and a Rumble channel can be followed,
its uploads listed among Subscriptions (`/videos/list?source=rumble&list=channel:<url>`).
Getting round its search page's block is not done.

Vimeo: its REST search (`GET https://api.vimeo.com/videos?query=`) answers
an unauthenticated ask with error 8003 ("The app didn't receive the user's
credentials"), so it needs a token from a free Vimeo developer app. Whether
that search still covers all public videos for a new app could not be
confirmed (the developer docs render only in a browser). Until a token is
tried, Vimeo comes in as Rumble does, by link and followed channel (yt-dlp's
`vimeo`, `vimeo:channel`, `vimeo:user`); with a token in a server setting
(`MEDIA_VIMEO_TOKEN`), it becomes a searched source. (An earlier draft said
its search was closed to new apps; that was not checked.) Invidious and
Piped were considered as YouTube back ends. They were not chosen: their
public instances come and go, and yt-dlp is already here.

The public build, without the add-on, gets PeerTube, the Archive and
Dailymotion: the Videos tab is not a personal-only feature.

## Contract (server, agent-media §6.x "Videos")

`GET /videos/sources` lists the sources this install offers:
`[{id, name, icon?, filters: [...], lists: [{id, name}]}]`, where the
lists are YouTube's subscriptions, Watch later, History, Recommended and
the account's playlists, given only when a sign-in is held.

`GET /videos/search?q=&sources=yt,peertube,archive,dailymotion,odysee&length=short|medium|long&date=today|week|month|year&type=video|live|playlist|channel&sort=relevance|date|views&page=`
answers `{items: [VideoItem], sources: [{id, ok, error?, next?}]}`. Each
source is asked in parallel with its own time limit (6 s). A slow or failed
source is reported in `sources` and leaves its rows out; it never holds up
the rest. Items are interleaved by each source's rank. A filter a source
cannot apply is dropped for that source, and `sources[].ignored` names it.

`GET /videos/list?source=yt&list=subs|watchlater|history|recommended|playlist:<id>&page=`
answers `{items, next}`.

`GET /videos/channel?source=&id=` and `GET /videos/playlist?source=&id=`
answer the channel or playlist (about, items, next). YouTube's channel
page reuses §6.9a's data.

```
VideoItem {
  source, id, url, title, channel: {name, id?, url?},
  duration_s?, published?, views?, thumb?, kind: video|live|playlist|channel,
  embed?            // a URL the app's viewer can frame, where the site allows it
  playable_audio?   // true where POST /music can play it (YouTube with the add-on)
  licence?          // PeerTube's and the Archive's, for a Creative Commons badge
}
```

Answers are cached for 10 minutes per query and filter set. The account's
feeds are cached for 5 minutes, refreshed on pull.

## The app

- **Videos tab** in Media (after Practice). The box searches every source.
  Chips under it set length, date, type and sort, and pick sources. A
  source that failed says so in one line above the list, with Retry.
- **Funnel**: Subscriptions (new uploads, newest first), Watch later,
  History, Recommended, and each playlist, from `/videos/sources`.
- **A row** has its thumbnail, title, channel · site · length · age. A tap
  opens it in the existing viewer (VideoLinkCards' embed, with Listen
  handing a YouTube video to the music player as now). A long press offers
  Watch, Listen (where `playable_audio`), Queue as music, Save to Practice
  (`POST /practice add`), Open on the site, and the channel's page.
- **Open in <app>** (built in step 1's server, 25 Nov 2026; idea from the
  Metrolist chat, agent-mail #1326): beside Open on the site, a row can be
  handed to an open-source player on the phone, NewPipe, Tubular or
  LibreTube, by a VIEW intent on its url with the package set.
  `/videos/sources` names each source's players (`open_in`); the app offers
  one only when it is installed and resolves the url, as HandoffPlugin
  checks music apps. GPL-3 apps, handed to and never built in.
- **Music's and Practice's searches** gain a "Videos" fold drawn from the
  same endpoint (YouTube only, type video). Their own song and practice
  rows stay as they are.
- The clip audit gets the tab. An e2e suite runs against the mock's
  `/videos/*`. Every catalog gets the tab's strings, and a What's new entry
  goes in.

## Order

1. Server: `/videos/sources` and `/videos/search` with the five searched sources,
   and the mock's copy (agent-media).
2. App: the tab, search, filters, row actions.
3. Server: `/videos/list` (the YouTube account feeds), `/videos/channel`,
   `/videos/playlist`, and followed Rumble and Odysee channels.
4. App: the funnel's lists, channel and playlist pages, and the Videos fold
   in Music and Practice.

## Terms

YouTube's rows come only with the personal-use add-on, as all of YouTube
outside its player does (outside-services.md §7). Watching a YouTube video
in the viewer is YouTube's own embed. PeerTube instances and the Archive
publish their videos for playback and say each video's licence.
Dailymotion's API terms allow search and its embed player. outside-services.md
gets entries for Sepia Search, Dailymotion, Odysee and Rumble with step 1.
