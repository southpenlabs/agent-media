# A git worktree per chat in shared repos

David, 17 Nov 2026, in sasonica-app's thread d69cd7ac ("let's implement all
of these things"), passed here as agent mail #860: each chat in a shared
repo works in its own checkout, not the one tree every session edits.

Not built yet. This is the design and the order to build it in.

## The pain it ends

- Every commit means carving your own hunks out of other sessions' work
  (`git apply --cached --recount`, AGENTS.md "Git").
- sasonica-app's e2e runs break when another session rebuilds
  `build/client` mid-run, so each run needs a private build copy.
- A `sed` on CLAUDE.md turned the AGENTS.md link into a file in the shared
  tree (17 Nov 2026).
- Two sessions' What's new entries conflicted (now one file per entry,
  sasonica-app 7f1b8885).

## What was found (open source first)

| Project | What it does | Used? |
|---|---|---|
| Claude Code `--worktree` / `-w` (built in, 2.1.x; `claude rm`) | Makes `<repo>/.claude/worktrees/<name>` on a new branch from the default remote branch (`worktree.baseRef` changes it), resumes a chat back into it, `WorktreeCreate`/`WorktreeRemove` hooks for other set-ups, removes it when clean | Not as the engine. Only Claude chats get it; sessiond also runs opencode and Antigravity chats, and the branch fork (`--fork-session`) and mail-by-folder need to know where the chat is. We copy its conventions instead: a branch from the default remote branch, removal only when clean, resume into the worktree |
| [Worktrunk](https://github.com/max-sixty/worktrunk) (Rust CLI, MIT/Apache) | `wt switch -c`, `wt list`, `wt merge` (rebase, squash, fast-forward the target), `wt remove`, post-create hooks to copy or link `node_modules`, agent plugins | Not needed for step 1: the create/remove is five git calls that `new_project.worktree` already makes. Worth another look at step 2 (`wt merge` is the rebase-and-push step) and step 4 (`wt list` shows ahead/behind and dirty state); a Rust binary on every host is the cost |
| Claude Squad, Crystal, vibe-kanban, Conductor | Desktop or TUI front ends that run agents in worktrees | No: they are their own front end; Sasonica is ours |

## Design

1. **Opt-in per repo, on the host.** `MEDIA_CHAT_WORKTREES` lists the repo
   tops (os.pathsep) whose new chats get a worktree. Host config, not repo
   config: the same repo on another host can stay shared. A chat started in
   a subfolder of a listed repo is not moved (it asked for that folder).
2. **Start.** A fresh chat (not a branch with history: Claude's
   `--fork-session` looks the transcript up by folder) in a listed repo
   starts in `<repo>/.worktrees/chat-<id8>` on branch `chat/<id8>`, cut from
   the repo's `main` as it is in the shared checkout (it has the commits
   sessions there have not pushed yet). `node_modules` and the paths in
   `MEDIA_CHAT_WORKTREE_LINKS` (relative, os.pathsep) are linked from the
   main checkout when they exist there. A worktree that cannot be made
   (git fails, disk full) leaves the chat in the main checkout, logged: a
   chat never fails to start over this.
   `.worktrees/` is already filed under its project (`sessions.project_of`)
   and excluded from the repo's status (`new_project._exclude`).
3. **Resume.** The session's `cwd` is the worktree, saved with it, so
   sessiond resumes it there and Claude finds the transcript (filed by
   folder). Nothing more to do.
4. **Reaching it.** Mail and `media session send <folder>` find a live chat
   by folder: a chat in `<folder>/.worktrees/…` counts as in `<folder>`
   (`headless_mail.find`).
5. **Pushing (step 2).** The chat commits on `chat/<id8>`. "Push" is
   `git fetch && git rebase origin/main && git push origin HEAD:main`: linear
   history, the others see it at once. The agent notes say so for opted-in
   repos (the context hook tells a chat it is in a worktree).
6. **The main checkout follows (step 3).** It is where local builds and
   `MEDIA_WEB_APP_DIR` come from. A watcher on `origin/main` (a timer, or a
   post-push call) fast-forwards it when that is clean to do and runs the
   repo's rebuild (sasonica-app: both web builds), which also ends stale
   `/app/` builds. Until then, a repo should not opt in if a local build is
   served from its main checkout.
7. **Tidying (step 4).** An archived or ended chat's worktree is removed once
   its branch is merged into `origin/main` and the tree is clean; one with
   unpushed commits or changes is kept and listed (`media worktrees`), as
   `claude rm` does.

## Build order

1. Start in a worktree, link `node_modules`, mail by folder (this commit's
   follow-up).
2. Agent notes for opted-in repos and the push recipe.
3. The main-checkout follower and rebuild.
4. Tidying and the list.

sasonica-app opts in after step 3, since its main checkout serves `/app/`.
