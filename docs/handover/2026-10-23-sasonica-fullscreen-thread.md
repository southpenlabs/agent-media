# Handover: thread 0f9bdb09 (full screen, speech keys, calendar, texts) — 23 Oct 2026

Working copy for the app: ~/spl/projects/sasonica/sasonica-app.fs2 (branch per change, off origin/main; the shared tree ~/projects/sasonica-app has other sessions' edits — never commit there).
Build/test loop: `ln -s ~/projects/sasonica-app/node_modules node_modules`; `pnpm -s typecheck && pnpm -s build`; suites via `/tmp/fs-scripts/some.sh $PWD <port> <suites…>`; remove the node_modules link before `git add`; push `HEAD:main`; `/app/` = `SASONICA_BASE=/app/ pnpm build` then rsync build-app/client → ~/projects/sasonica-app/build-app/client (no --delete).
APK to David off Wi-Fi: CI release asset → `build-app/client/dl/<token>/` → https://red5.sasonica.com/app/dl/<token>/<apk>, 24 h systemd-run expiry.

## In hand (not yet committed)

**Update 28 Oct 2026:** the flick-enter branch has landed on sasonica-app main (see 3d10721, the full-screen tip about the flick); the items under Open still stand.

- Branch `flick-enter` in sasonica-app.fs2: full screen entered by a quick scroll down then up (FLICK_MS 900); folded, scrolling down keeps it, a scroll up after reading on (readOn) brings the bars back; plain scroll down no longer folds. test/fullscreen.mjs updated, 55 pass. Next: What's new entry, commit, push, /app/, APK link.
- Last pushed app commit: b01751d (hands-free tap while waiting = listenNow). Its APK finished; superseded by the flick build.

## Decided, done
- Mic ducking pause fix (f2de2a2), screen-off delay number + corner keys above the input when shown, input only while listening (and while editing after a caption tap), tap on screen-off repeats last held choice, reply row under replies with Stop after play, black-screen gestures via server, swipe down = hands-free, transcript on black screen.
- Rye Remedial: booking mirror asks before overwriting Google edits, website changes tested (site_command set by another session), 'rescheduled' email deployed, >15 min email rule, SMS texts on with test_to = David's number (config.toml [calendar.rye.sms]); test bookings 20/21 Mon 26 Oct (cancel later).

## Open
- Thinking read-aloud: works but sparse (most thinking blocks signature-only); offered a distinct voice for thoughts — no answer yet.
- Remove test bookings 20/21 when David says; switch texts from test_to to clients when he says.
