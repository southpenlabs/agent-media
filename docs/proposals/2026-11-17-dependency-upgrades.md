# Keeping every dependency up to date (proposal, 17 Nov 2026)

David, 17 Nov 2026, after the open-source-first rule: "let's add upgrades to
all the dependencies. we have part of the process too". Part of the rule in
`docs/decisions/2026-11-17-open-source-first.md`: using other people's
projects means taking their fixes as they come.

## What already upgrades itself

| what | how | where |
|---|---|---|
| The mautrix bridges on red5 | `agent-bridge-update.timer`, weekly (Wed 04:30): official releases installed, then `bridge-upkeep follow` | red5, sasonica-personal-use's `bridge-upkeep` |
| Dotfiles and the host tools it installs | `dotfiles-auto-update`, with the dotfiles doctor watching it | every host |
| The Sasonica app and the bridges app on the phone | Sasonica's own updater, from each repo's CI releases | p8a |
| agent-media on red2 | `media update` by hand after a merge | red2 |
| Pinned binaries (opencode, txcript) | a pin and sha256 in the installer, raised by hand | `packages/sasonica` |

## What does not

Nothing watches the code's own dependencies: the Python packages under
`packages/*` (agent-media), sasonica-personal-use's, the app's npm and
Gradle ones, the bridges app's Go modules and Gradle, the CI actions, and
the pinned binaries. assistant-ui is the example: a fix we needed shipped
upstream on 2 Nov and the app is still on the version before it.

## The proposal

1. **Dependabot in every repo** (GitHub's own, its engine open source under
   MIT; Renovate is the open-source alternative if we outgrow it), weekly,
   with minor and patch updates grouped into one pull request per ecosystem
   and majors one each, so a week brings a handful of PRs, not dozens:
   - agent-media: pip for each `packages/*`, GitHub Actions
   - sasonica-app: npm (pnpm), Gradle (`android/`), GitHub Actions
   - sasonica-bridges: Go modules, Gradle (`android/`), GitHub Actions
   - sasonica-personal-use: pip, GitHub Actions
2. **CI decides most of it.** Each PR runs the repo's CI. Once a week, the
   repo's session (agent-media's for itself and the add-on, sasonica-app's for
   the app, this thread's for the bridges app) merges the green grouped
   minor/patch PRs after a skim of the changelogs, deploys as usual, and
   notes anything worth knowing. A major or a red PR gets a line to David:
   what changed, what it would take.
3. **Pinned things get a weekly check too.** A small `media deps check` lists
   each pin (opencode, txcript, the Kokoro and Whisper models, the bridges
   app's mautrix versions) beside the newest release, so raising a pin is a
   decision made from a list, not something found by accident.
4. **The mautrix versions stay in step.** The bridges app pins mautrix-meta,
   -whatsapp, -gmessages and mautrix-go together; Dependabot's Go group keeps
   them one PR, and red5's server bridges and the phone's move to the same
   release line within a week of each other.

## Order of work

1. Dependabot files in sasonica-bridges and sasonica-personal-use (this
   thread's), and agent-media; sasonica-app's by its session.
2. The weekly merge sweep written into each repo's AGENTS.md.
3. `media deps check`.

## Open

- Whether the weekly sweep merges green minor/patch PRs without asking.
  Proposed: yes (David, 4 Nov 2026: "commits merges and pushes should be done
  when appropriate"); majors are always asked.
