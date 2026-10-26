# A stories row on the Chats tab

David, 26 Oct 2026: a collapsible, horizontally scrolling row across the top
of the Chats tab, as in Facebook Messenger, showing friends' real Facebook
and Instagram stories. There is no official API for friends' stories (the
Graph API gives a Page's or a business account's own stories only; the
Instagram Basic Display API was switched off in December 2024), so the
question is how the wrapper apps do it, and what that would cost us.

**Status: step 1 built (sasonica-app 1d61da3); step 1b building (David,
26 Oct 2026: "I was hoping the stories would be scrolled horizontally across
the top, embedded"; he chose Facebook's own tray shown in the row, not faces
read and redrawn).** Step 2 (faces read from the pages) is not being built.

Confidence is given per finding: **high**
(read in code or a primary source), **medium** (several secondary sources
agree, or a strong inference), **low** (inference, untested).

## What exists to build on

- `ReadItBrowserActivity` (sasonica-app 09e3a19): a site full screen in a
  WebView, Done/Back closes, `intent:`/`fb:` links refused so a page cannot
  bounce out to the Facebook app. Signs in through the app-wide
  `CookieManager`.
- `ReadItPlugin.readPage`: a page loaded out of sight in a WebView, its
  text read by `evaluateJavascript` once it settles. The same mechanism
  would read a stories tray.
- The Chats tab (`app/routes/chats.tsx` → `ThreadList chats`) already has a
  horizontal strip (`nav.project-strip`, the kind buttons) and a
  collapsible filter box whose open state is saved (`saveChatsBoxOpen`):
  the row sits above the strip and collapses the same way.
- The Messenger bridge (mautrix-meta) is a separate Facebook login; per the
  09e3a19 notes its sign-in expires Read it's Facebook session cookies, so
  Read it asks again afterwards. A stories row inherits that.

## 1. How the wrapper apps show stories

**Frost for Facebook** (GPL-3.0; read, not copied). **High.** Frost is a
themed WebView around `m.facebook.com`, with a desktop or mobile Chrome user
agent of its own instead of the WebView's (`FbConst.kt`). Stories are not a
native feature: they are Facebook's own tray in the feed and Facebook's own
viewer. Every stories issue in its tracker is about that site viewer ("Video
stories won't open" #1833, "Unable to slide between stories" #1814,
"Watching stories videos doesn't pause the stories queue" #1868), and the
CSS work went the other way, *hiding* the tray (#1279, #1452, where an
"aggressive" hider was reverted because it hid ordinary posts). The one
thing Frost did natively was **notifications and message badges**: a
scheduled background job fetched `m.facebook.com/notifications` (and
mbasic pages) with the session cookie and parsed the HTML with jsoup
(`parsers/NotifParser.kt`, `MessageParser.kt`), with battery-friendly
scheduling. That is the precedent for a "periodic hidden read". The repo has
been archived since Dec 2023; the README says it is unmaintained and users
report it barely loads the feed (#1941, #1963).

**Swipe for Facebook, Simple Social.** **High.** Same kind of wrapper. Both
left the Play Store in April 2021 after Facebook's lawyers sent Swipe a
cease and desist; Facebook objected to their use of data and to the CSS
that hid sponsored posts. The *developers'* own Facebook and Instagram
accounts were permanently disabled. No ban of ordinary users was reported;
Simple Social warned its users that Facebook *might* deactivate them.

**Friendly Social Browser** (Sensor Tower). **Medium.** A multi-site
wrapper (Facebook, Messenger, Instagram, X, …) with ad blocking; its store
notes mention only fixing "IG video stories", i.e. it shows the sites' own
story viewers. No native tray found.

Conclusion: **none of the known wrappers render their own list of friends'
stories.** They show the site's tray and viewer, restyled at most. The
riskiest thing they did (hiding ads, reading data) is what drew Facebook's
legal action, against the developer, not the users.

## 2. Can a hidden WebView read the list?

**Technically yes, robustly no. Medium.**

- **Facebook.** Facebook's markup is generated with obfuscated, changing
  class names; scraping guides in 2026 say the only stable target is the
  JSON embedded in `<script type="application/json">` blocks, not the DOM,
  and that Facebook fingerprints the client (a plain HTTP fetch gets a login
  wall). The stories tray on `m.facebook.com` is drawn by script, so a read
  must wait for it, as `readPage` waits for text to settle. Names and
  avatar images are reachable through `aria-label`s and `<img>`s in the
  tray; seen/unseen is only a ring style or a field in the embedded JSON.
  Expect the selectors to break every few weeks to months, silently (a row
  that shows nothing rather than an error). mbasic, which Frost leaned on
  for cheap parsing, is reported much reduced (**low**, not confirmed). (**Medium**:
  inferred from sources and Frost's history; I did not load the page.)
- **Instagram.** The web app's tray is fed by a private endpoint
  (`reels_tray`, unseen stories first) and GraphQL calls, which unofficial
  libraries (instagrapi, aiograpi) use, and which change without notice.
  Reading the tray from the DOM has the same fragility as Facebook. Desktop
  and mobile instagram.com show friends' stories and play them. (**Medium.**)
- **Does Instagram refuse WebViews?** Not as a rule (**medium**, untested):
  Instagram's own in-app browser is an Android WebView, and Google's
  `disallowed_useragent` block applies to *Google* sign-in, not Instagram's.
  Expect "Open the app" banners, `intent:` links (already refused by
  `ReadItBrowserActivity`), and a "suspicious login" check the first time.
  The WebView's `; wv` user agent token is how sites spot an embedded
  browser; Frost replaced the user agent, which I would not do (it is the
  "circumvent technological measures" clause, §4 below) unless a page flatly
  refuses to work.
- **Reading never marks a story seen** as long as it only reads the tray
  page and never opens a story. Opening one in the viewer marks it seen,
  which is what David would expect.
- **A hidden load of the home feed loads the feed's ads.** A periodic
  invisible read of facebook.com or instagram.com counts ad impressions
  nobody saw. That is the most concrete thing Meta could object to (invalid
  traffic), beyond the terms. A read must use the narrowest page that has
  the tray, rarely, and never in the background.

## 3. mautrix-meta and Beeper

**High** for the bridge (read in its code): mautrix-meta bridges **story
replies, story reactions and story shares that arrive in a chat** (a DM
that says "replied to your story", with a preview; `msgconv/from-meta.go`
sets `com.beeper.relation_preview_type` to `story_reply` /
`story_reaction`, and Instagram story media is refetched on demand,
`igconnector/directmedia.go`). It has **no stories tray**: it never lists
friends' active stories, and nothing in its roadmap does. Since v26.08
Instagram is a separate bridge, `mautrix-instagram`, on Instagram's new DM
protocol. Beeper uses these bridges and likewise shows story replies, not a
tray (**medium**: no Beeper source found saying either way).

So the bridge can improve one small thing: a "replied to your story" message
in Messages could show the story it replies to. That belongs to the messages
features list (2026-10-20-messages-features.md, item 1), not this row.

## 4. Terms and account risk

- **Facebook Terms §3.2.3**: "You may not access or collect data from our
  Products using automated means (without our prior permission)". §3.2.7:
  no circumventing technological measures. Meta's **Automated Data
  Collection Terms** (7 Oct 2024) cover "automated or programmatic tools",
  scrapers and bots, and require express written permission.
- **Instagram Terms**: no "collecting information in an automated way
  without our express permission".
- **Showing the site's own page** in a WebView the person drives is a
  browser. It is what Frost, Friendly and every in-app browser do, and is
  within the terms as long as nothing is hidden or rewritten. **Risk: low.**
  The residual risk is a "suspicious login" or checkpoint at sign-in, and
  the bridge logout noted above.
- **Reading the list in a hidden WebView** on a timer is automated
  collection, plainly, whatever the volume. **Risk: low per read for one
  person, real in aggregate.** Meta's enforcement against wrappers has hit
  developers (Swipe) more than users; for one personal account at a few
  reads an hour the likely outcomes are a checkpoint, a forced re-login, or
  "we suspect automated behaviour" on Instagram, which is quicker to flag
  than Facebook. A disabled account is possible and not undoable by us. For
  a *distributed* Sasonica (the monetization plan), shipping a scraper of
  Meta's pages is the Swipe story again, with South Pen Labs as the
  developer.
- **Limits to respect if it is ever built**: only when the Chats tab is
  open and in the foreground, never from a background job or on the
  server; at most one read per site per 15 minutes; stop for a day after
  any checkpoint, login page or error; never with a changed user agent;
  never open a story or scroll the feed automatically; one page load, no
  follow-up requests.

## 5. Recommendation

**Build the safe row first; keep the avatar row as an opt-in experiment, if
at all.**

### Step 1: the site's own stories, one tap away (recommended)

A row at the top of Chats with one round entry per signed-in site:
"Facebook stories" and "Instagram stories", each with the site's mark (the
names as text; brand marks only as Meta's brand rules allow, else a neutral
icon and the word). A tap opens the site's own stories viewer full screen
in `ReadItBrowserActivity`: `https://m.facebook.com/stories/` and
`https://www.instagram.com/` (whose tray is the top row; Instagram has no
tray page of its own on the web, and a story is `/stories/<user>/`). Done or
Back returns to Chats. Swiping through stories, replying and reacting are
the site's, and a reply to a story shows up in Messages through the bridge
as it does today.

- **Signed out**: the entry shows "Sign in" and opens the site's login
  (the existing Read it sign-in, so it is shared with Read it and the video
  viewer). Settings → Read it sign-ins already lists and signs out.
- **No site signed in and none wanted**: the row is not shown; Settings
  gets "Stories row: Facebook, Instagram" switches.
- **Risk**: as for Read it. Nothing is read, nothing is automated.
- **Server**: nothing beyond a `/features` flag (`stories: true`) so a
  server can turn the row off, per the app-stays-generic rule; the sites'
  URLs are generic, not host setup, so they live in the app. Nothing goes
  to red5, not even which sites are signed in.

### Step 1b: the site's own tray, embedded in the row (building)

Signed in to Facebook in the app, the row shows Facebook's own stories tray,
swiped sideways as Facebook draws it, in place of the Facebook stories button
(StoriesTrayPlugin.java, lib/storiesTray.ts, StoriesRow.tsx TraySlot).

- **How**: a small native WebView (the app-wide CookieManager, as
  ReadItBrowserActivity) laid over a 200 px placeholder in the row. The page
  sends the placeholder's rect (ResizeObserver, scroll, resize, the
  keyboard's visual viewport) and the native view follows it, clipped below
  the sticky bar; it hides (and pauses) when the slot is off screen, folded,
  or under a sheet, menu or dialog, since a native view would cover them.
- **Which page**: `m.facebook.com/`, whose top is the stories tray. Not
  verified signed in (no sign-in here): `m.facebook.com/stories/` opens a
  story viewer rather than the tray (as far as known), and mbasic is
  reported reduced. The tray is found by a short list of selectors
  (`lib/storiesTray.ts` TRAYS), tried for 10 s after the page loads.
- **Display only**: once found, a style hides everything but the tray and
  pins it to the top. The page is not read: the shell's only answer is a
  state (ready, missing, wall, error). No automated scrolling; loaded once
  per visit to Chats, only while the row is open and on screen, destroyed
  on leaving Chats. Nothing goes to red5.
- **A tap on a story**: a navigation out of the tray, or the page's own
  move to a `/stories/` address, opens that page full screen in the Read it
  browser; Done comes back.
- **Fallback**: the Facebook stories button, as in step 1, when signed out,
  when no selector matches (`missing`), on a load error, behind a login or
  checkpoint (`wall`, said on the button), in a browser tab, on an APK
  without the plugin, and on e-ink (`(update: slow)`: the row stays still).
- **Instagram** keeps its button: instagram.com in an app's WebView pushes
  for its app and a login check, so its tray is not tried.
- **Text size**: the tray is drawn at 100% text zoom so it fits its slot at
  Android font scale 1.5.
- **Risk**: as step 1 for the terms (a page the person sees and drives;
  hiding the rest of the page is styling), but more fragile: Facebook's
  markup changes without notice and the selectors will need fixing, in which
  case the row quietly falls back to the button. The hidden part of the
  page still loads, so the feed's ads load (and may count impressions)
  behind the tray each visit; that is the main thing Meta could object to,
  and the reason it loads at most once per visit and never in the
  background. Vertical swipes that start on the tray stay in the tray.

- **First test (build 1510, 26 Oct 2026)**: signed in, the slot showed
  "Loading Facebook stories…" and fell back to the button: the tray was not
  found (or a wall or an error). The tray now reports each failure to the
  server as structure only (§6.21d, `<state>/stories-tray-log.jsonl`: where
  the page ended, its title, a password field or a known interstitial, the
  selectors' match counts, the top 40 elements' tags, roles, data-* names
  and cut class names), shows the last one on About, offers "Show what
  loaded" under the button, waits up to 20 s, and asks as a phone's Chrome
  (the WebView's "; wv" and "Version/4.0" marks dropped).

### Step 2 (optional, David's call): avatars from a foreground read

If David wants faces: when the Chats tab opens and the last read is over 15
minutes old, `ReadItPlugin` loads the tray page out of sight (the
`readPage` mechanism) and returns `[{name, avatar, unseen, url}]` for each
friend with an active story, read from the page's own tray. The row shows
them after the site entry, unseen ones ringed, newest first; a tap opens
that friend's story (`url`) in the browser activity, which is the only
thing that marks it seen. Avatars are fetched by the WebView and drawn as
`data:` images or kept in the app's cache, on the phone only.

- **Privacy**: names, avatars and the list stay on the phone (app storage,
  replaced on each read, dropped on sign-out). Nothing to red5. Matching a
  name to a Sasonica contact (2026-10-17-contacts.md) would be on the phone
  too.
- **Fails safe**: if the read finds no tray, a login page or a checkpoint,
  the row falls back to step 1's entry and the read stops for a day, with
  one quiet line in Settings ("Facebook stories could not be read").
- **Cost**: the extractor is per site and will break; budget for fixing it
  every month or two, or accept that it goes quiet. Instagram is likelier to
  flag it than Facebook.
- **Not for the distributed app**: behind a setting marked experimental,
  default off, or left out of release builds.

### Collapsing

As Messenger does: the row scrolls away with the list (it is the list's
first item, not a fixed header), and a small handle at its end collapses it
to a single thin line ("Stories" with a dot when anything is unseen, in
step 2) that expands on tap. The collapsed state is saved like the filter
box (`saveChatsBoxOpen`). On the Pine Note the row is plain round outlines
and text, no rings in colour (DU4 greys).

### What each side does

| | Step 1 | Step 2 |
|---|---|---|
| App (web) | the row, collapse state, Settings switches | renders the read list, caches it, the 15-min rule |
| App (Android) | opens `ReadItBrowserActivity` at the stories URL | `ReadItPlugin.readStories(site)`: out-of-sight load + tray extractor |
| Server | `/features` `stories` flag | nothing more |
| red5 | nothing | nothing |

## Open questions for David

1. Is step 1 (one tap to each site's own stories, no faces) enough, or are
   the faces the point?
2. If faces: happy to accept a small but real risk of a checkpoint or a
   disabled Facebook or Instagram account, and a row that breaks now and
   then? Facebook only (lower risk), or Instagram too?
3. Should the row be in the distributed app at all, or only David's build?
4. The bridge login logs Read it out of Facebook (and so this row). Live
   with re-signing in, or look at giving the bridge and the browser the
   same session?
5. Show story replies' previews in Messages (the bridge already sends
   them) as a separate small item?

## Not verified

Nothing was loaded signed in, so these need a look on the phone before
step 1 is built: that `m.facebook.com/stories/` opens the stories viewer
(else the feed's tray is the entry), how instagram.com behaves in the
WebView (banners, a login check), and whether mbasic still serves anything
signed in (reported reduced; not confirmed).

## Sources

- Frost: github.com/AllanWang/Frost-for-Facebook (legacy branch:
  `facebook/FbConst.kt`, `facebook/parsers/NotifParser.kt`; issues #1279,
  #1452, #1814, #1833, #1868, #1941); archived, GPL-3.0.
- Swipe and Simple Social: notebookcheck.net, 19 Apr 2021, "Facebook forces
  third-party versions of its Android app off the Play Store"; Android
  Police, 18 Apr 2021.
- Friendly: App Store listings (id400169658, id382011064).
- mautrix-meta: github.com/mautrix/meta (`ROADMAP.md`,
  `pkg/msgconv/from-meta.go`, `pkg/igconnector/directmedia.go`); mau.fi
  blog, July 2026 releases (new Instagram bridge); v26.08 release notes.
- Facebook Terms §3.2 (facebook.com/terms.php); Meta Automated Data
  Collection Terms, 7 Oct 2024 (facebook.com/legal/automated_data_collection_terms);
  Instagram Terms of Use ("collect information in an automated way").
- Scrapfly, "How to scrape Facebook" (2026), on embedded JSON vs DOM.
