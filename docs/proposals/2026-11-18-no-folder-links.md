# One file per chat, with no folder links (proposal, 18 Nov 2026)

David, 18 Nov 2026, on 36 chats found with second copies on disk: link the
moved repos' old Claude folders to the new ones, as was done for agent-media
on 17 Nov, "but let's work towards alleviating the need for the link".

## What happened

Claude Code files a chat under `~/.claude/projects/<the folder's path with
/ as ->/`, and `claude --resume <id>` only finds it from that folder. Two
things made second copies:

1. **A repo moved** (`~/projects/websites` → `~/spl/projects/spl/websites`,
   agent-media, sasonica-shell, somahz-home…). New chats file under the new
   name; old ones stay under the old name and can't be resumed from the new
   folder. The fix so far: the old folder's name made a link to the new one.
2. **A chat resumed from a folder other than its own** (sessiond resuming a
   moved thread where it started, 18 Oct; a pane opened in another
   folder): Claude Code writes the rest of the chat to a new file under the
   folder it was resumed from, leaving the first file behind. agent-media's
   thread moves (moves.py) move the file properly; these copies came from
   resumes, not moves.

Done on 18 Nov: each of the 29 chats with copies (36 files) kept one file,
the largest then newest, in the folder agent-media resumes it from (its
recorded move, else the real path of the folder it ran in); the others went
to `~/backups/claude-duplicates/20261118-040414/`; two emptied folders of
moved repos became links, six empty ones of paths that no longer exist were
removed, one holding Claude's memory notes was left. search and
agent-sessions' catalogue skip linked folders (6fe8688e, agent-sessions
907c571). red2 now has 8,712 chat files for 8,712 chats.

## So no link is needed again

1. **Moving a repo moves its chats.** `media session move-folder <old>
   <new>` (or the repo-move step David's dotfiles/host scripts run): every
   chat filed under the old folder's name is moved to the new one's, each
   with its own side folder (subagents, tool results), the cwd recorded in
   `moved.json` as moves.py does for one thread, and Claude's `memory` notes
   merged into the new folder's. The old name is then removed, not linked.
   Run once for agent-media and websites to retire today's two links.
2. **Every resume opens a chat in the folder it is filed under.** sessiond,
   the pane driver's revive and Continue in… already look up where a chat
   ran (`sessions.session_cwd`, moves-aware); they should resume from the
   folder whose name holds the file, so Claude Code never starts a second
   one. Where those differ (a file filed under one name, its moved cwd under
   another), the file is moved first (moves.move_transcript).
3. **A check that says when it happens.** `media selfcheck` (or agent-sessions'
   catalogue, which already sees every file) reports a chat with files in two
   folders and a project folder that is a link, raising an alert rather than
   leaving it for a search to trip over.
4. **Readers key on the chat's id**, as search, the thread list and the
   catalogue now do, so a stray copy is never a duplicate thread.

## Links where they make sense, each with a way out (18 Nov 2026)

David: links are fine where they make sense, "but have a plan to remove them
in the case of deprecation". So a link is a supported tool, not a failure,
and every one is retired by a rule, not by memory:

- **Each link is recorded** when made: `<state>/claude-folder-links.json`,
  `{"<old folder name>": {"to", "old_path", "why", "at"}}` (the move tool
  writes it; today's three are entered by hand: agent-media, websites,
  somahz-home).
- **Deprecated** when the old path is: nothing exists at `old_path` any more
  (the `~/projects/<repo>` compatibility link to the new place removed), or
  David marks it so.
- **Retired** by the check (step 3) once deprecated and quiet: no chat has
  been written through the old name for 14 days (Claude writes under the
  folder it was resumed from, so a write there means something still opens
  the old path). It then moves anything still filed under the old name into
  the real folder (as step 1 does), removes the link and the record, and
  says so; a write through the old name instead raises an alert naming what
  still uses it.
- Until then the link stays and costs nothing: search, the thread list and
  agent-sessions' catalogue already read linked folders once.

## Steps

1, then 3, then 2 (the resume paths are the riskiest to touch and the
check will say whether they still make copies).
