# Things worth sending upstream

David, 17 Nov 2026: "maybe we could even contribute to the litellm
project", then "I think we should do the same for assistant-ui too". A
running list of fixes and features we needed from the open-source projects
Sasonica stands on, kept as we meet them. Opening an issue or pull request
is a public step under David's name: ask him before sending any.

Each entry: what we ran into, what we did meanwhile, what the upstream
change would be, and its state (idea, drafted, sent with a link, merged).

## LiteLLM (BerriAI/litellm, MIT) — the AI gateway

- **Spend tracking without Postgres.** Spend logs, budgets and the
  dashboard need a Postgres database (through Prisma); a gateway on a home
  server has none, so usage is off. Meanwhile: Sasonica counts its own calls
  (Settings → Models usage) and tags gateway calls with their feature
  (`metadata.tags`). Upstream: a SQLite (or file) store for spend logs and
  keys, for single-host installs. State: idea.
- **A clear error when no database is set.** Without one, a spend or key
  route answers a 500 from its error handler ("prisma" missing; seen on red2,
  agent-gateway d78e19e, 16 Nov 2026). Upstream: a 501/503 that says the
  feature needs a database. State: idea.

## assistant-ui (assistant-ui/assistant-ui, MIT) — the app's chat components

The app uses `@assistant-ui/react` 0.15.36 (17 Nov 2026).
No pnpm patches and no forked components: the app builds on the primitives
and works around them in its own code. The first review was 4 Nov 2026
(sasonica-app handover 70a18b06, agent-mail #160); entries from the
sasonica-app session.

- **The composer box ignores width changes.** `ComposerPrimitive.Input`
  uses react-textarea-autosize, which re-measures on value change, window
  resize and font load only. An Android WebView can lay the box out before
  it has its width, so the hidden clone wraps the placeholder one character
  per line and the box stays at its maximum height. Meanwhile: the app's own
  `AutoGrowTextarea` (sasonica-app `app/components/AutoGrow.tsx`) is passed
  as the input's child and re-measures on a ResizeObserver width change.
  Upstream: react-textarea-autosize's issue #389 and its open PR #425 (a
  width-only ResizeObserver, our fix's idea). State: sent, a comment on
  #425 with the WebView case (4 Nov 2026,
  https://github.com/Andarist/react-textarea-autosize/pull/425#issuecomment-6624887059);
  #425 is still open and unmerged (17 Nov).
- **Redacted reasoning has no representation.** The ThreadMessageLike
  converter drops a reasoning part with no text and no `unstable_summary`,
  so a redacted or encrypted thinking block (Anthropic `redacted_thinking`,
  OpenAI encrypted reasoning) vanishes. Meanwhile: sasonica-app
  `app/lib/convert.ts` gives it a placeholder summary ("thought"). Upstream:
  a `redacted: true` reasoning part kept when its text is empty. State:
  sent, https://github.com/assistant-ui/assistant-ui/issues/10313 (open,
  17 Nov).
- **The viewport sticks to the bottom with `autoScroll={false}`.** The
  shared stick-to-bottom still pulled the view down during a run. Meanwhile:
  `ThreadPrimitive.Viewport autoScroll={false} scrollToBottomOnRunStart={false}`
  plus a guard in sasonica-app `app/hooks/useFollowAlong.ts` that holds it
  off until a hand scrolls (the follow-along reader owns the scroll anyway).
  Upstream: issue #10228, fixed by PR #10266 (merged 2 Nov), after our
  0.15.32. State: fixed upstream, in store 0.3.23; the app is on 0.15.36
  with it since sasonica-app c2875e52 (17 Nov). The guard stays: it is the
  app's rule that assistant-ui never scrolls the thread by itself. Nothing
  to send.
- **Loading older messages keeps the reader's place.** Upstream's viewport
  has handled prepends since 0.15.x (`messagesPrepended`, `hasEarlier`,
  `onLoadEarlier`, `ThreadPrimitive.LoadEarlier`, #8798). The app pages
  older turns itself and pins the view from the bottom while it does
  (`holdPlace` in sasonica-app `app/components/Thread.tsx`). Both aim at the
  same position, so they agree. To do on our side: move older-page loading
  onto `onLoadEarlier` and drop `holdPlace`. Nothing to send.
- **Enter on a phone keyboard.** IME Enter no longer sends (#8148, shipped)
  and plain-Enter newline on touch exists (#4091,
  `unstable_insertNewlineOnTouchEnter`); the app uses `submitMode="ctrlEnter"`
  with Android's send action wired in its own WebView. Nothing to send.
- **Looked at, not worth filing:** http images dropped by the converter
  (upstream keeps only https/blob on purpose; ours was a tailnet case, which
  new work avoids); the files-only Send key (the files are the app's own, not
  assistant-ui attachments); the swipe hooks (`useSwipeUpDown.ts`,
  `useSheetSwipeDown.ts`: gestures on the app's own player and footer, no
  assistant-ui code).

## mautrix-teams (YourSandwich/mautrix-teams, AGPL-3.0) — Teams in Sasonica Bridges

The Microsoft Teams bridge (Go, bridgev2; what red5 runs) is not a mautrix
project. Sasonica Bridges runs it on the phone under its own
`localmatrix` connector, from our public fork
[davidj4tech/mautrix-teams](https://github.com/davidj4tech/mautrix-teams),
branch `sasonica` (two commits on its v32.0; sasonica-bridges b0705e7,
17 Nov 2026).

- **mautrix-go v0.33.0.** It was on v0.32.0; in v0.33.0
  `ASIntent.Matrix` is a method, so its call, meeting and presence code no
  longer compiled beside the other bridges (which all share v0.33.0).
  Change: call `Matrix()` (fork commit 6c1473e). Upstream: a PR.
  State: drafted (the branch), sent 18 Nov 2026 (David: "yeah, let's do it"): https://github.com/YourSandwich/mautrix-teams/pull/4 (mautrix-go v0.33) and /pull/5 (without the appservice connector).
- **Running without the appservice connector.** Element Call and Teams
  presence need a homeserver (MatrixRTC state events, OpenID for LiveKit,
  TURN, ghost presence, all through `*matrix.ASIntent`). Under any other
  `bridgev2.MatrixConnector`, `Start` now turns both off with one log line
  instead of failing each call or presence update; nothing changes under
  the appservice connector (fork commit 9fe17b5, with a test). Upstream: a
  PR, with the v0.33 one or after it. State: drafted, not sent: waiting
  for David's yes.
- **Its module path, `go.mau.fi/mautrix-teams`, does not resolve**, so
  sasonica-bridges needs a `replace`. Upstream: an issue asking for a
  resolvable path (a vanity import or `github.com/YourSandwich/...`).
  State: idea.
- **A build tag that leaves the call code out.** pion and LiveKit add
  17 MB to the phone's core, and pion's Android interface lookup
  (wlynxg/anet) needs `-checklinkname=0` to link. A tag (or a separate
  package) for calls would drop both where calls cannot run. State: idea.

## Capacitor (ionic-team/capacitor, MIT) — the Android shell

- **A route with a dot in its last segment is not served as the app.**
  `WebViewLocalServer` answers `index.html` only when the last path segment
  has no "." (html5mode), and takes any other path for a bundled file; a
  reload of a Local chat (`/t/phone.<id>`) failed with "Webpage not
  available … net::ERR_INVALID_RESPONSE" (David, 18 Nov 2026). Meanwhile:
  sasonica-app's `AppRoutes` (a `BridgeWebViewClient` on the bridge)
  answers a main-frame page load of the app's own host whose last segment
  has a dot as `/` is, leaving `/_capacitor_file_/` alone. Upstream: answer
  a main-frame request (`isForMainFrame()`) that finds no bundled file with
  `index.html`, or a setting for it. State: idea (sasonica-app, thread
  744ce5a2).

## The rest we stand on (to watch and give back to)

The rule behind this file: `docs/decisions/2026-11-17-open-source-first.md`.
No entries yet for these; add one when we work around or need something.

Federated and messaging:
- **Matrix**: the tuwunel homeserver (mel.ryer.org, red5), the protocol
  and its specs (MSCs we lean on, e.g. MSC4144 per-message profiles for the
  bridges' second stage).
- **mautrix** bridges (AGPL: meta, whatsapp, gmessages, telegram, slack, discord, signal; teams above) and
  **mautrix-go** (MPL): for the phone companion, anything in our
  `localmatrix` connector that a general "no homeserver" connector upstream
  would make unnecessary.

AI and agents:
- **LiteLLM** (above), **CLIProxyAPI** (the Gemini sign-in route),
  **Meridian** (Claude Max through the Agent SDK), **opencode**, **txcript**
  (copying a chat into another agent), **Ollama**, **Kokoro** and **Piper**
  (voices), **Whisper** (speech to text).

Music, books and media:
- **Mopidy**, **Snapcast**, **spotifyd**, **beets**, **mpv**,
  **Audiobookshelf** (forked, GPL v3: kept upstream-mergeable), **yt-dlp**.

Mail, backup, plumbing:
- **notmuch**, **isync/mbsync**, **restic**, **rclone**, **cloudflared**.

The app:
- **assistant-ui** (above), **Capacitor**, **React Router**.
