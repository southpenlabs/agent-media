# Decision: leave Audiobookshelf

Status: **accepted**
Date: 2026-11-11

## The decision

agent-media no longer uses Audiobookshelf for anything. David, 11 Nov 2026:
"I'd like to move away from audiobookshelf if we're still using it for
serving sasonica books and podcasts" — and, asked how far: "All the way".
The old Sasonica ABS phone app (`com.sasonica.abs`) and the sasonica-web
client are retired with it.

## What changed

- Books are the server's own: `book_library` reads the audiobook folder for
  tags, length, cover and how far through each, and `media library-scan`
  (alias `abs-scan`) rereads it.
- The app authenticates with paired device tokens only; the ABS bearer
  fallback (`auth_abs.py`) is gone.
- Threads are keyed by session only: `GET /item`, the `?item=` / `{item}` /
  `player_item` forms and the ABS-shaped fields (`item`, `scanning`, `tail`,
  `start`/`end`) are gone.
- `abs-bridge`, the conversation library's chapters, covers, tags and
  progress, and the phone's Sasonica ABS player (`phone_player`, the `abs`
  target) are deleted.

The conversation shelf (hardlinked clips plus a manifest per conversation)
stays: the transcript and the thread list read its manifest.

See docs/simplification-plan.md §2 and docs/server-contract.md §9-§10.
