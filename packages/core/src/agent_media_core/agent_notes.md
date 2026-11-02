# How to write for this install

Told once to every agent session agent-media hooks (intake/context.py
`notes_for`; again after a compaction), so each one behaves for a reader who
mostly *listens*, on a phone. One rule per line: `- [<when>] <rule>`.
<when> is `all`, `claude` (Claude Code's own tools), or `canvas` (the
canvas draws figures: MEDIA_SPEECH_VISUAL=1) or `tune` (a reply's song
plays: MEDIA_REPLY_TUNE). MEDIA_AGENT_NOTES=0 turns them
off; MEDIA_AGENT_NOTES_FILE points at a site's own copy of this file.

- [all] Your replies are read aloud in full. Lead with the answer, aim for under a minute (about 150 words), and put long tables, file lists or logs in a file and say where.
- [all] Put anything the user has to copy (a command, a URL to paste, a value) in its own fenced code block: they copy from a phone.
- [all] To show the user a file of yours (a screenshot, a PDF), run `media attach <file>` and put the `Shared file:` line it prints on a line of its own in your reply: the app shows it as a picture or file they can tap, and the voice skips it. An image, video, audio file or PDF named by its full path in a reply also becomes a file they can tap, but `media attach` is still the sure way.
- [all] When a reply reports that a long job you were asked to do has finished, end it with `[[sound: done]]` on its own line; when something you were asked to do has failed, `[[sound: fail]]`. A tone plays before the reply is spoken and the marker is never shown or read. Not on every reply: only those two moments. Quote someone else's words (a message, an email) as a `>` blockquote: they are read in a different voice texture.
- [claude] To let the user hear a clip as an AskUserQuestion option (an audio test, a sound to pick), run `media attach <clip>` (mp3, wav, ogg, opus, m4a, aac or flac) and put the `Shared file:` line it prints on its own last line of that option's `description`: the app gives the option a Play button, and the voice skips the line.
- [all] Give places to go as markdown links with full https URLs, not bare host or file names.
- [all] Before ending a turn on a background wait, give the watcher a deadline and make it report either way, so a turn never ends on a check that can fail silently.
- [claude] Give every Bash call a plain-words `description`: the phone shows it as the step being run.
- [claude] Ask questions as AskUserQuestion cards, recommended option first; they can be answered with a tap. Prose written just before the card is spoken with it: keep it to a couple of sentences and never speak it yourself.
- [canvas] A reply can carry a figure: `[[visual: <description>]]` is drawn beside it, `[[reveal: <description>]]` also pauses the speech until it is on screen. Use one only when the picture carries meaning, and describe it concretely (boxes, labels, arrows).
- [tune] Rarely, when a reply lands at a moment a song would suit (a long job finally done, a late night, a mood the user named), end it with `[[tune: Artist - Title]]` on its own line, a real song you know that fits what the reply says. It plays after the reply is spoken, never under it; it is never shown or read; at most one every half hour plays. Never on an ordinary reply, and never a song picked from a music service's own results.
