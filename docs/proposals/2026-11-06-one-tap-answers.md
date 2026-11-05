# One-tap answers (6 Nov 2026)

David, 6 Nov 2026: "I'd like to add a new feature for one tap answers";
asked which kinds, he ticked all four offered: suggested replies, answers
from the player or bubble, spoken then tapped, and the Inbox space
suggestion. Status: **decided, building** (chips and player first).

## What there is now

- **Question cards and permission prompts** answer with a tap in the
  thread, and from the notification when there is one question with
  options that is not multi-select (`POST /session/answer {session,
  choice, key}`, contract §6.4; the `approval` row, §6.1).
- **One follow-up line** per reply: the ghost prompt, or the Stop hook's
  own twelve-word guess at what David types next (`intake/_followup.py`,
  contract §6.2.1 `suggestion_for`). The app puts it in the reply box; it
  is one line, not a choice.
- **The small bubble card** shows a question reply as its question with
  "• label" per option, answered only through Open (sasonica-app 9d7ee58d).
- **Spaces**: a chat with no clear space lands in Inbox; the server's
  suggested space is not built (roadmap, Spaces: "Not yet").

## 1. Suggested replies

Under an ordinary reply (one that is not a question card), two to four
short chips. A tap sends the chip's words as David's message.

- **Where they come from**: the agent may end a reply with
  `[[replies: Commit it | Run the tests first | Leave it]]` when it knows
  the likely answers (never shown or read, like `[[sound:]]`); with none,
  the follow-up model is asked for up to three instead of its one line.
  The agent's win.
- **Server**: `suggestions: [{text, source: "agent"|"model"}]` on the
  reply's message (§6.2.2) and on the `/sessions/state` row next to
  `approval`, keyed to the reply so a stale set is never offered.
  `suggestion_for` stays as the first chip's text for older apps.
- **Tap**: sends through the normal send path, so Keep reading, read cuts
  and queueing behave as for a typed message.

## 2. From the player and the bubble

The question card's options, and §1's chips, shown where David already is:

- the mini player (thin footer) and the full player, under the reply
  being read;
- the small bubble card, in place of "Open to answer";
- the notification, as now, plus suggested replies as its actions (at
  most three, Android's limit).

Multi-select, partial and several-question cards still open the thread:
a tap cannot carry them. Nothing new on the server: the app reads the
`approval` and `suggestions` it already has.

## 3. Spoken, then tapped

When a reply with a question card or chips is read aloud, the options
come up as large buttons on the black screen (and the mini player) as the
speech reaches them, so a hands-busy David taps without reading. A
question card's options are read after its question ("Commit it, run the
tests first, or leave it"); suggested replies are not read, only shown,
so an ordinary reply does not grow a spoken menu.

## 4. Inbox space suggestion

The server suggests a space for an Inbox chat from its folder, the people
in it and the first message (spaces.py, through the fast model chain
quick.py uses). The Chats row carries `space_suggestion: {space, label}`;
the row shows "File in <label>?" with a tick, one tap tagging it
(`POST /session/tags`). Dismissing it keeps it in Inbox and is
remembered per chat.

## 5. Actions, not only replies

David, 6 Nov 2026: "let's also add actions rather than just replies"; asked
which, he ticked all four: open something, listen or play, app controls,
and server tasks.

A chip can do something instead of sending words. The agent writes them
beside its replies, each a label and what it does:

`[[actions: Open the PR -> open https://github.com/… | Listen -> listen docs/x.md | New chat here -> new ~/projects/foo | Restart the canvas -> task restart canvas]]`

The server reads the marker (stripped from the words and the speech like
the others), checks each action, and serves only the ones it accepts, as
`actions: [{label, kind, target}]` beside `suggestions`. The app renders
them as chips with a small mark for their kind, and knows nothing more of
the host than the answer says.

- **open**: a https link, a file the server can serve (`media attach`'s
  route), a thread (`/t/<id>`), or an app page (`/project?…`, `/map`,
  `/docs?…`). Anything else is dropped.
- **listen / play**: a document read aloud (the Documents page's Listen),
  a clip, or music (`play <query>` goes the music skill's way).
- **app**: what the app already does from its own keys: new chat in a
  folder, remind me in…, Quiet for…, compact, rename. Sent to the endpoints
  those keys use.
- **task**: only a task named in the server's list (`GET /actions/tasks`:
  `{name, label, confirm}`, from a server registry and add-ons, never a
  shell line from the reply). A tap asks "Restart the canvas?" first, then
  `POST /actions/run {session, task, key}`; the result comes back into the
  thread as a note. A task the list does not hold is never shown.

A tap on an action never sends a message; the agent hears of it only when
it was a task (the note).

## Decided (David, 6 Nov 2026)

1. Chips come from the agent's `[[replies:]]` marker, the follow-up
   model's up to three when there is none.
2. A tap sends at once, with a few seconds' Undo.
3. Built in order: suggested replies, then the same chips in the player,
   bubble and notification, then spoken options, then the Inbox space
   suggestion. Actions (part 5) come after the chips are in, since they
   share the row.
