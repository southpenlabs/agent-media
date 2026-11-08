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
  shell line from the reply). A tap on one with `confirm` asks "Restart the canvas?" first, then
  `POST /actions/run {session, task, key}`; the result comes back into the
  thread as a note. A task the list does not hold is never shown.

**Which ask first** (David, 6 Nov 2026: "not all actions should require a
confirmation and the ones that do should be distinguishable"): each served
action carries `confirm: true|false`, decided by the server, never by the
reply. open, listen/play and app controls act at once (each is undone by
going back, stopping or its own key); a task confirms when its entry in
the server's list says so (a restart, a deploy, anything shared or hard to
undo), and acts at once otherwise. A chip that confirms is drawn with a
trailing "…" on its label and a bold border (David chose it, 6 Nov 2026;
both survive e-ink), so it is told apart before the tap.

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

## 6. Seven more (David, 8 Nov 2026: "I think we can do a lot with these quick replies", then "omg, yes I want all seven of them!")

Built in this order ("reach first"):

1. **Answer from anywhere** (part 2): the chips, and a question card's
   options, in the notification (at most three actions), the light bubble
   card and the mini player. The server side is in: `/sessions/events`
   waiting rows carry `suggestions`.
2. **Actions** (part 5): `[[actions:]]`, checked and served by the server;
   a confirming chip marked with a trailing "…" and a bold border.
3. **Pick by voice** (part 3): on the black screen or with earbuds, "the
   second one" / "first" / its words picks a chip; a question card's options
   the same way. **Built 9 Nov 2026** (sasonica-app `a8959a05`): in
   hands-free and from the mic key's countdown, sent as the chip's tap;
   stricter than a card (a bare "two", or more said than the chip, goes as
   words); the black screen shows the chips, or a one-question card's
   options, as large buttons once the reply has been read (Screen.choices).
4. **Saved replies**: the user's own short replies ("On my way", "Call you
   later"), kept on the server, shown after the suggested chips (behind +N
   when folded), edited in Settings; a hold on any message you sent offers
   "Save as quick reply". People chats and agent chats alike.
5. **Tone keys**, both ways David chose: a hold on a chip offers Edit,
   Shorter, Warmer, Formal (the last three rewrite it and send); and the
   Undo bar after a chip's send offers "Warmer instead" / "Shorter instead",
   which unsends or takes back the message and sends the rewrite. Rewrites
   by the fast models, a people chat's by the private model only.
6. **Learning from you**: which chips are tapped or skipped, and your recent
   sent words, kept on your server only (never a private or sealed
   thread's), used to rank chips and to give the model examples of how you
   phrase things. A setting turns it off and a key forgets what is kept.
7. **Context-aware**: replies that use what the server knows — a travel
   time from the phone's location to the place named ("Running 10 minutes
   late") and free slots from the calendar when someone asks to meet —
   offered as chips with the value filled in; location only when the phone
   shares it (maps proposal), nothing sent outside but to the private model.
8. **Patterns, Fabric-style** (David, 8 Nov 2026: "fabric was the
   prompting engine I was trying to think of", then item 8): named prompts
   kept as plain markdown files on the server, in the shape of Daniel
   Miessler's Fabric (MIT; its pattern library importable, never its code
   needed). The tone keys (item 5) are patterns (`shorter`, `warmer`,
   `formal`), so a new tone is a new file. A hold on any message also offers
   pattern chips — Summarize, Pull out the to-dos, Explain simply — whose
   result shows (and can be spoken) under it. The user's own folder of
   patterns shows as chips too. A people chat's text goes only to the private
   model, as for every model call on it. Built with the tone keys.

## 7. More like this, and tips (David, 9 Nov 2026)

- **More like this** ("It would be nice to have an option to get a fresh
  batch of quick replies based on the one selected"): on a chip's hold, with
  Edit, Shorter, Warmer and Formal — a fresh set of answers in the direction
  of the one held, from the same model the chips came from (a people chat's
  from the private model only). Built with the tone keys and patterns.
- **Tips** ("do we have hints for all of this?"): counted tips, the first
  three times each, with a key to stop them — the first chips (tap sends,
  hold for more), the first send (Undo and Edit), the first action chip
  (↗ ▶ ⚙, "…" asks first), the first folded row with +N.
- Done the same day: Edit beside Undo, both in the chips' look; a chip's
  leftover click no longer replays the last reply (sasonica-app dd6e42e9).
