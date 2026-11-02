# Scheduled turns, the small version

**Status:** step 1 built (3 Nov 2026). Carries forward idea 1 of
[OpenClaw as an add-on](2026-10-22-openclaw-as-an-add-on.md) ("a session can
be woken on a timer with a standing instruction") and is the first piece of
roadmap item 11, *Routines from the phone*.

## Why

David, 3 Nov 2026, wants agents that run day-to-day work on their own: each
one a persona, each working in its own folder, woken on a schedule, and
acting within limits he sets. Nothing woke an agent on a timer: every
existing timer runs a script, and `media session start` took no persona.

## Step 1: `media session wake` (built)

```
media session wake <folder> --order orders/daily.md --persona <id> --permissions folder --from schedule
```

- The folder's live headless session is sent the order (`send`: queued
  behind a running turn).
- With none live, a new one is started with it, as the persona if given
  (`start --persona`, the same public render `/ask` uses, §6.24).
- A question or permission card up is never answered: the wake is refused
  (exit 4) and nothing is started beside it. The order waits for the next
  wake.

The schedule itself is the host's: a systemd user timer, a cron line, or
anything else that runs a command. agent-media ships no timers and names no
agents. What an agent may do on its own is its folder's business: its
`CLAUDE.md`, and its `.claude/settings.json` permissions. Under the default
strict profile every command it runs would be a card on the phone, the
folder's allow rules included, so a scheduled agent starts with the
**folder** profile (contract, headless sessions: Permissions): the folder's
allow and deny rules decide, the user's do not apply, the user's hooks still
run, and anything else is a card. The folder must be trusted once
(Claude Code's trust dialog) or its allow rules are ignored.

## Later

- **Routines in the app** (roadmap 11): make, list and pause them from the
  phone, kept by the server, run by sessiond's own clock rather than a
  host timer.
- **A digest per routine:** today the standing order tells the agent to
  report with `agent-alert --kind digest`. A routine could file it itself.
- **One thread per routine or one per run:** `wake` reuses a live
  session, and a reaped one means a fresh thread. Revisit once it has run a
  few weeks.
