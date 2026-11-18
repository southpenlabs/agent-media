# Forgejo: the workflow and the app's side of it, with one umbrella over several repos

**Status:** recommendations, not agreed (David, 19 Nov 2026: "is there any
recommendations you have about interface enhancements for integration of or
workflow with forgejo? including the incorporation of multiple repositories
in the one umbrella project"). Builds on roadmap item 55 and
[issues on Forgejo](2026-11-03-issues-on-forgejo.md), which stand.

## Where things are

- Forgejo is not running yet: it is still on red2's open list (Red2 system
  planning and setup, 11 Nov). CI runs on GitHub runners on red2.
- The server already knows forges by host (`git_state.py`: `forgejo`,
  `gitea`, `MEDIA_GIT_FORGES` for a self-hosted one) and links files and
  commits to their web pages (§6.49).
- A project is one folder, one repo (§6.24). The Sasonica work is one
  product spread over `agent-media`, `sasonica-app`, `sasonica-web`,
  `sasonica-shell` under `~/spl/projects/sasonica`, plus many app worktrees.
  Nothing in the app knows they belong together.

## 1. Stand the forge up so nothing breaks on the day

- Forgejo on red2 behind the public front door (HTTPS, scoped tokens: the
  off-the-tailnet rule), SSH on its own port.
- **Push mirrors to GitHub** for each repo, so GitHub stays the public face
  and Dependabot keeps working while the move settles. Forgejo is the
  origin; GitHub is written only by the mirror.
- **Forgejo Actions** runs the existing `.github/workflows` mostly as is
  (it reads that folder when `.forgejo/workflows` is absent). Register
  `forgejo-runner` on red2 with the same labels (`red2`, `e2e`,
  `red2-apk`) so `runs-on` lines need no change; keep the GitHub runners
  until a week of green runs.
- **AGit pushes for agents**: `git push origin HEAD:refs/for/main -o
  topic=<topic>` opens a pull request without a fork or a branch on the
  server. It suits worktree sessions: the worktree's branch stays local,
  the PR appears, merging it is one call.

## 2. The umbrella is a Forgejo organisation

**The layout (agreed, David, 19 Nov 2026: "agreed").** The hierarchy is
the Order of Ryer, then Vanta, then South Pen Labs Pty Ltd, then its
brands (`~/spl/admin/legal/delegation-matrix.md`; Sasonica is a brand
there by the `~/spl` README, not yet in the matrix). Forgejo orgs do not
nest, so the tree is flattened:

- `ryer`: private (members only); Ryer's own repos. Ryer is never named
  outside the house, so it shows nowhere public, and the forge's address
  is not a Ryer name.
- `southpenlabs`: the company's repos (`spl`, `company-docs`, `websites`).
- One org per brand: `sasonica` (agent-media, sasonica-app, sasonica-web,
  sasonica-shell), `somahz`, `novelsonica`.
- David's own account: personal tools (agent-memory, agent-mail,
  dotfiles…).
- Vanta gets no org until it has repos of its own.

A brand org is the umbrella below. What Forgejo gives an org, used as the
umbrella:
  - **Org-wide labels**: one label set (`bug`, `wish`, `app`, `server`,
    `phone`, `needs-repro`, `blocked`, `public`…) shared by every repo, so
    a filter means the same thing everywhere.
  - **Org projects (boards)**: one board can hold issues and PRs from all
    four repos. The roadmap's big items become columns or cards here.
  - **Cross-repo references and dependencies**: `sasonica/sasonica-app#12`
    in a commit or issue links across; "blocked by" works between repos on
    one instance. A contract change (server first, app second) is two
    issues, the app's blocked by the server's.
  - Org-level Actions secrets and runners, one token scope for the bots.
- Milestones are per repo in Forgejo. For an item spanning repos, use a
  label (`roadmap-52`) or the org board, not a milestone in each.

## 3. The server learns the umbrella

- **An umbrella is a folder whose children are repos on the same forge
  org.** Inferred: `~/spl/projects/sasonica` has four repo children whose
  `origin` shares one owner. Overridable in `config.toml` (`[[umbrella]]
  name, path, repos`). No new registry for the ordinary case, in the same
  spirit as "there is no project to register".
- A worktree (`sasonica-app-voicechips`) counts as its main repo, as the
  reply-tone key already does, so the 20-odd worktree folders collapse
  under `sasonica-app` in the app instead of being peers of it.
- The forge adapter of item 55 (open, close, comment, list, state by
  number) gains `list(org)` so one call answers across the umbrella.

## 4. What the app shows

In order of value for the least app code:

1. **CI on the git line.** The thread's git line (§6.49) gets the commit
   status dot from the forge's status API (`/repos/{o}/{r}/commits/{sha}/status`):
   pending, green, red; tap opens the run. Removes most "watch the CI run"
   waits sessions do by hand.
2. **Webhooks, not polling.** Forgejo posts push, PR, issue and
   workflow-run events to the server (same host, one secret). A red run on
   `main` becomes an alert and a spoken line, routed to the thread named by
   the commit's `Thread:` trailer — what `media test-failure notify` does
   now, done by the forge event. A PR review comment reaches the thread
   that opened the PR.
3. **Issue badge and "Open as issue".** As in the 3 Nov proposal, with a
   repo picker defaulting to the thread's repo and listing the umbrella's
   others.
4. **The umbrella in the menu.** Projects grouped by umbrella, worktrees
   folded under their repo, and an umbrella row opening one list: open
   issues and PRs across its repos, a chip per repo to filter, newest
   activity first. A row opens Forgejo's own page; the app does not
   reimplement issue editing (Forgejo's mobile web is good enough, and the
   app stays generic).
5. **A thread that spans repos** shows a git line per repo it touched
   (its commits carry its `Thread:` line in each), not just its folder's.

## 5. Agents on the forge

- A bot user per agent folder (`agent-media`, `sasonica-app`), each with
  a token scoped to its repos: assignee is the session, as the 3 Nov
  proposal has it, and the forge's audit says which agent did what.
- `Thread:` trailers written as the full thread link, so Forgejo's commit
  page is one tap back to the chat (it linkifies URLs, not bare ids).
- Agents drive the forge with an existing tool rather than our own
  wrapper: `tea` (the Gitea CLI, works against Forgejo) for the shell;
  a Forgejo MCP server (`forgejo-mcp` on Codeberg is one candidate) to be
  checked before writing anything. Open-source-first: whichever is used,
  record why, and gaps go in docs/reference/upstream-contributions.md.

## Suggested order

1. Install Forgejo, the org, mirrors to GitHub, the runner (section 1, 2).
2. Import docs/notes/issues-to-import.md as the first issues.
3. Webhooks and CI dots (4.1, 4.2): the most felt, the least app work.
4. The forge adapter with umbrella listing (3, item 55).
5. The app's umbrella menu and issues list (4.3–4.5).
