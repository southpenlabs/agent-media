"""A conversation's turns, as they land: the transcript and its shelf folder.

Two things live here. The transcript — `conversation_log`, the lines the app
reads, joined from speech history, the turn being spoken now and the
manifest — and the listener's own turns (`record_listener_turn`). And the
shelf: each turn's clips hardlinked into a folder per conversation
(`export_session`), with a manifest recording which turns have been placed.

The shelf was built for Audiobookshelf, which served it as a growing
"Conversations" library: chapters per turn, covers, live/archived tags, the
listener's progress re-opened as turns landed. Audiobookshelf was left on
11 Nov 2026 (David: "I'd like to move away from audiobookshelf" — "All the
way"), and everything that talked to it went with it; the folder and the
manifest stay, local files that the transcript and the server's thread list
still read.

**The clips are the tracks.** The renderer already splits a reply into a clip
per sentence, and those files already exist — so this writes no audio at all.
Each track is a *hardlink* to the clip the renderer made: one inode with two
names, nothing to re-encode, nothing to keep in step, and a conversation costs
the shelf zero bytes.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import time
from pathlib import Path
from typing import Optional

from .render import device as device_voice
from . import session_feed
from ._paths import state_dir

log = logging.getLogger(__name__)


#: A folder name a scanner and three filesystems can all live with. Not the
#: episode title verbatim: those carry `/`, `:` and the em dash that separates
#: workspace from question.
_UNSAFE = re.compile(r"[^\w .,()'’-]+")


#: The folder a conversation with no workspace is filed under: a catch-all,
#: not a project, so nothing should show it as one.
UNFILED = "conversation"


def safe_name(text: str, limit: int = 110) -> str:
    name = _UNSAFE.sub(" ", (text or "").replace("·", "-")).strip()
    name = re.sub(r"\s+", " ", name).strip(" .")
    if len(name) > limit:
        name = name[:limit].rsplit(" ", 1)[0].rstrip(" .,-")
    return name or UNFILED


def enabled() -> bool:
    """Whether conversations are put on the shelf at all.

    Off with MEDIA_CONVERSATIONS_LIBRARY=0 (David, 27 Sep 2026: the
    Conversations library came out of Sasonica ABS — speech is heard in
    Sasonica, and a phone-voiced turn has no audio to shelve). The turns,
    their words and replay are the speech history's, not the library's."""
    return (os.environ.get("MEDIA_CONVERSATIONS_LIBRARY", "1").strip().lower()
            not in ("0", "false", "no", "off"))


def root() -> Path:
    """Where the library lives. `MEDIA_BOOK_TRACKS_ROOT` overrides, and
    `MEDIA_BOOK_EXPORT_ROOT` still does too — it named this tree first, and a
    host that set it meant this tree."""
    raw = (os.environ.get("MEDIA_BOOK_TRACKS_ROOT", "").strip()
           or os.environ.get("MEDIA_BOOK_EXPORT_ROOT", "").strip())
    return Path(raw).expanduser() if raw else Path.home() / "conversations"


def _manifest_path(session: str) -> Path:
    return state_dir() / "book-tracks" / f"{safe_name(session, 80)}.json"


def _read_manifest(session: str) -> dict:
    try:
        d = json.loads(_manifest_path(session).read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_manifest(session: str, data: dict) -> None:
    p = _manifest_path(session)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1))
        tmp.replace(p)
    except OSError as e:
        log.warning("book-tracks: cannot write %s (%s)", p, e)


def _ffprobe(p: Path) -> float:
    return session_feed._ffprobe_duration(p)


def place_clip(src, out: Path) -> Optional[Path]:
    """Give the clip a second name inside the item. `out`, or None.

    A hardlink, not a copy: the render cache holds the only durable copy of
    this audio, and a byte-for-byte duplicate would double what a conversation
    costs while being able to drift from it. Two names for one inode cannot
    disagree, and deleting either never takes the audio with it. Cross-device
    falls back to copying, because a link that cannot be made is not a reason
    to have no track.
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    src = Path(src)
    try:
        os.link(src, out)
    except FileExistsError:
        return out
    except OSError:
        try:
            shutil.copyfile(src, out)
        except OSError as e:
            log.warning("book-tracks: cannot place %s (%s)", src, e)
            return None
    return out


def track_name(index: int, said: str, fallback: str = "") -> str:
    """`007 - The sentence this clip says.mp3`.

    The index leads because a scanner orders an item's files by name, and it is
    zero-padded to four because otherwise track 10 sorts before track 2 — which
    corrupts nothing, it just plays the conversation in the wrong order,
    quietly. Four digits, because a long conversation is thousands of
    sentences and the day that wraps is the day every later one is misfiled.
    """
    return f"{index:04d} - {safe_name(said or fallback, 90)}.mp3"


def throwaway_workspace(workspace: str) -> bool:
    """A session run from a temp dir, which no library should shelve.

    A session that tests something by starting another Claude in its scratch
    folder leaves a tmux session named after that path
    (`tmp-claude-1000--home-…-scratchpad-handoff-test`), and every one became a
    project of its own on the Projects shelf. `scratch` is not one of these:
    that is a workspace David keeps on purpose.
    """
    return workspace.startswith("tmp-") or "-scratchpad" in workspace


def folder_for(session: str, turns: list, manifest: dict) -> Optional[Path]:
    """The item folder for this conversation, decided once and then kept.

    The title comes from what was asked, so it can change as a conversation
    grows — and a library item that renames itself is a *new* item to
    Audiobookshelf: new id, no progress, and the old one left behind as a
    duplicate. So the first export writes the folder into the manifest and
    every later one uses it, even when a better title exists by then. An item
    that keeps its identity is worth more than an item with the best name.
    """
    kept = manifest.get("folder")
    if kept:
        return Path(kept)
    if not turns:
        return None
    workspace = session_feed.workspace_for(session, turns)
    if throwaway_workspace(workspace):
        return None
    # The workspace is the folder above, which Audiobookshelf reads as the
    # author — so putting it in the title too says it twice on every shelf.
    title = session_feed.asked_for(session, turns)
    return root() / safe_name(workspace) / safe_name(title)


# --- the listener's own turns -------------------------------------------------
#
# A conversation you can reply to is a conversation with two people in it, and
# only one of them was being recorded. The reply box put words into the session
# and they vanished — the answer came back on the shelf, the question did not,
# and a chapter list of answers to invisible questions is a worse record than
# no record.
#
# So a typed reply is written to speech history like any other turn. It is not
# *played*: nobody wants their own message read at them as they send it. The
# row is what matters — the transcript, the thread's `pending` and the log read
# history, so the turn lands in order, beside the reply it asked for.
#
# It is not rendered either (David, 18 Oct 2026). It used to be, to an mp3 in
# the listener's voice, so the shelf had both sides and a replay could read the
# question before its answer; nearly all of those clips were never heard. A
# replay now reads the question live, from the row's text, the way a reply is
# rendered on the fly (`cli._question_to_play`): sentence text to a target
# that renders on the device, a render made at replay time for one that does
# not. A row from before still has its clip, and that is played while it lasts.
#
# In a different voice, deliberately. The whole representation is audio, so the
# only way to hear who is speaking is to hear it.

#: Australian, and not the assistant's. Override with MEDIA_LISTENER_VOICE
#: (an edge voice; MEDIA_LISTENER_ENGINE is no longer read).
LISTENER_VOICE = "en-AU-WilliamNeural"


#: How long the same words from the same listener count as the same turn.
LISTENER_REPEAT_S = 120.0


def _claim_listener_turn(session: str, text: str, now: float,
                         store=None) -> bool:
    """Claim `text` for this session; False if someone already has.

    The history check below is not enough on its own: the two recorders start
    within the same second and each spends one or two rendering before it
    writes, so both look, see nothing, and both record. A claim file, created
    exclusively before any of that, is decided at once. Old claims are swept
    as they are passed, so the directory never grows past a day's replies.
    """
    import hashlib

    d = state_dir() / "listener-claims"
    try:
        d.mkdir(parents=True, exist_ok=True)
        for p in d.iterdir():
            try:
                if now - p.stat().st_mtime > LISTENER_REPEAT_S:
                    p.unlink()
            except OSError:
                pass
        flat = " ".join(text.split())
        key = hashlib.sha1(f"{session}\n{flat}".encode()).hexdigest()[:24]
        fd = os.open(str(d / key), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        os.close(fd)
        return True
    except FileExistsError:
        # Held — but a claim from before the session last spoke belongs to the
        # earlier turn. "y", a reply, "y" again is two answers, not a repeat.
        claim = d / key
        try:
            since = claim.stat().st_mtime
        except OSError:
            return False
        if store is not None and _session_spoke_since(store, session, text, since):
            try:
                os.utime(claim, (now, now))
            except OSError:
                pass
            return True
        return False
    except OSError:
        # A state dir that cannot take a file cannot dedupe; the history check
        # still stands, and a repeat is better than a lost turn.
        return True


def _flat(text) -> str:
    return " ".join(str(text or "").split())


def _session_spoke_since(store, session: str, text: str, since: float) -> bool:
    """Whether anything but `text` was said in this session after `since`."""
    try:
        rows = store.recent_history(sink="speech", limit=50)
    except Exception:  # noqa: BLE001 — no answer, so no evidence of a new turn
        return False
    label = _flat(f"You: {text}")
    for row in rows:
        ex = row.get("extras")
        if not isinstance(ex, dict) or ex.get("source_session") != session:
            continue
        if _flat(row.get("text")) != label and float(row.get("started_at") or 0) > since:
            return True
    return False


def _listener_turn_recently(store, session: str, text: str, now: float) -> bool:
    """Whether `text` was already recorded as this session's listener turn.

    Only the latest thing said in the session counts. The repeat this catches
    — the canvas and the prompt hook recording one reply — arrives with
    nothing in between; the same short answer given twice ("y", then "y" to
    the next question) has the assistant's reply between, and is a new turn.
    """
    try:
        rows = store.recent_history(sink="speech", limit=50)
    except Exception:  # noqa: BLE001 — a store that cannot answer cannot dedupe
        return False
    label = _flat(f"You: {text}")
    for row in rows:  # newest first
        ex = row.get("extras")
        if not isinstance(ex, dict) or ex.get("source_session") != session:
            continue
        if not ex.get("listener") or _flat(row.get("text")) != label:
            return False
        return now - float(row.get("started_at") or 0) <= LISTENER_REPEAT_S
    return False


def record_listener_turn(session: str, text: str, *, store=None,
                         extras: Optional[dict] = None) -> bool:
    """Add a reply the listener typed to the conversation, as a history row.

    Returns whether the turn was recorded. Nothing is rendered: the row is the
    turn (text and metadata, no audio), and a replay that reads it out renders
    it then (see the note above). A failed write is not fatal to the reply
    itself — the words still reached the session, which is the point of the
    feature; they just do not appear in the transcript.
    """
    import time as _time

    # Line breaks kept: a message typed over several lines reads that way in
    # the transcript. The repeat checks compare flattened copies, because the
    # canvas types a reply into the terminal on one line and the prompt hook
    # then sees it without the breaks the reply box recorded.
    from .intake._text import tidy_lines

    text = tidy_lines(text)
    if not text or not session:
        return False
    from .state.store import StateStore

    at = _time.time()
    st = store or StateStore()
    # The same words arrive twice when a reply sent from the player is typed
    # into the session by the canvas: the canvas records it as it sends, and
    # the prompt hook records it as Claude Code receives it. Both are right to
    # try — either may be the only one installed — so the recorder is where
    # the repeat is dropped. Same session, same words, within a couple of
    # minutes: already a turn, and reported as one.
    if _listener_turn_recently(st, session, text, at):
        return True
    if not _claim_listener_turn(session, text, at, st):
        return True

    try:
        st.add_history(
            # No clip: the uri is empty (the column is NOT NULL), and every
            # reader of a listener row copes with that (session_feed.turns
            # keeps it, cli._row_has_audio says there is nothing to play).
            sink="speech", uri="", started_at=at, ended_at=at,
            # Never played anywhere: the row exists to be read, not routed.
            target="none", source="listener", content_type=None,
            # The chapter title comes from this text, so it carries the speaker
            # label — a chapter list is read, not heard, and without it the
            # questions and the answers look alike. A replay reads only the
            # reply; the label is not spoken.
            text=f"You: {text}",
            extras={"source_session": session, "listener": True,
                    # A slash command rides along as `command`, so the chat can
                    # show it as the instruction it is rather than a sentence
                    # somebody said out loud.
                    **(extras or {})})
    except Exception as e:  # noqa: BLE001 — the reply already landed
        log.warning("book-tracks: could not record a listener turn (%s)", e)
        return False
    return True


def export_session(session: str, *, store=None) -> tuple[Optional[Path], int]:
    """Write any clips this conversation has gained. `(folder, added)`.

    Idempotent and append-only. The turn is the unit that lands — a reply's
    clips all exist by the time it is over — so a turn already exported is
    identified by the `started_at` its history row carries and skipped whole.

    The running number comes from the manifest, never from recounting the
    turns. Speech history outlives the render cache, so a conversation can lose
    an old turn's audio entirely (`session_feed.turns` drops what it cannot
    find) — and a number derived by counting would then shift every track after
    the gap, renaming files that are already on disk and already downloaded.
    What has been written is what decides where the next one goes.
    """
    from .deleted import is_deleted
    from .private import hidden

    if is_deleted(session) or hidden(session):
        # Deleted or sealed: its folder would be rebuilt from whatever speech
        # is left.
        return None, 0
    turns = session_feed.turns(session, store=store)
    manifest = _read_manifest(session)
    folder = folder_for(session, turns, manifest)
    if folder is None:
        return None, 0

    done = {float(t["at"]) for t in manifest.get("turns", [])}
    written = list(manifest.get("turns", []))
    index = sum(len(t.get("files") or []) for t in written)
    added = 0
    for turn in turns:
        at = float(turn.at)
        if at in done:
            continue
        if not turn.clips:
            # A listener turn, which is a row with no audio (18 Oct 2026):
            # nothing to place, and it must not stop the turns after it.
            continue
        if any(device_voice.is_clip(c) for c in turn.clips):
            # Said in the device's own voice: there is no audio to place.
            continue
        files = []
        for i, clip in enumerate(turn.clips):
            said = turn.sentences[i] if i < len(turn.sentences) else ""
            dest = folder / track_name(index + 1, said, turn.title)
            if dest.exists():
                # Ours already, from a run whose manifest was lost. Adopt it
                # rather than counting it as new: "+12 tracks" for a run that
                # wrote nothing is a report nobody can act on.
                index += 1
                files.append(dest.name)
                continue
            if place_clip(clip, dest) is not None:
                index += 1
                files.append(dest.name)
                added += 1
                continue
            # Stop at the first clip that will not go: the numbering is
            # sequential, so carrying on would file the next sentence under
            # this one's number and leave the conversation out of order.
            log.warning("book-tracks: %s stopped at track %d", session, index + 1)
            break
        if not files:
            break
        written.append({"at": at, "title": turn.title, "files": files})

    if added or not manifest:
        manifest.update({"session": session, "folder": str(folder),
                         "turns": written})
        _write_manifest(session, manifest)
    return folder, added


def export_all(store=None, since_hours: float = 24.0) -> list:
    """Conversations that have said something lately. `[(session, folder, added)]`.

    Bounded on purpose. Speech history goes back months, and an unbounded run
    would build a growing item for every conversation that ever spoke — a
    library of hundreds where the point is the handful still being had. A
    conversation quiet for longer than the window is finished as far as this is
    concerned, and `book_export` already publishes those. `since_hours <= 0`
    means all of them, for the one-off backfill.
    """
    import time as _time

    cutoff = (_time.time() - since_hours * 3600.0) if since_hours > 0 else 0.0
    out = []
    for conv in session_feed.conversations(store=store):
        session = conv.get("session") or ""
        if not session:
            continue
        last = float(conv.get("last") or conv.get("at") or 0.0)
        if cutoff and last and last < cutoff:
            continue
        folder, added = export_session(session, store=store)
        if folder is not None:
            out.append((session, folder, added))
    return out


def _fill_starts(starts: list, durations, n: int) -> list:
    """Measured sentence starts, with the ones not reached yet predicted.

    Starts are measured as the player reaches each sentence, so a reply still
    playing (or cut off) has them only as far as it got. The rest follow from
    the last measured start plus the clip lengths in between, and each new
    measurement corrects them. Without that a timeline stopped where the voice
    was: the app's sentence marks crowded into the part already heard
    (David, 2 Nov 2026: "The ticks aren't spaced out properly, all the way
    to the end of the line").
    """
    out = [float(x) for x in starts]
    durs = [float(d or 0) for d in (durations or [])]
    while out and len(out) < n and len(out) <= len(durs):
        out.append(out[-1] + durs[len(out) - 1])
    return out


def _live_turn(session: str) -> Optional[dict]:
    """The turn being spoken *right now* for `session`, as
    `{at, text, listener}`, or None.

    History is only written when a turn ends, so for the whole time a reply is
    audible its words are missing from speech history — which is why the
    transcript could not show a turn until after it finished speaking. This
    reads the same now_playing row the speech controls use, with the same
    writer-pid liveness guard, so a submit that crashed without clearing the
    row does not leave a phantom line. Keyed by `started_at`, which is the same
    `at` the ended history row and the manifest turn will carry, so the line
    merges into one row as the turn progresses rather than doubling.
    """
    import os as _os
    from .state.store import StateStore

    np = StateStore().get_now_playing("speech")
    if not np:
        return None
    ex = np.get("extras")
    if isinstance(ex, str):
        try:
            ex = json.loads(ex)
        except ValueError:
            ex = {}
    if not isinstance(ex, dict):
        return None
    if ex.get("thinking"):
        return None  # already present as a reasoning part, never a reply
    if ex.get("source_session") != session:
        return None
    # A question is spoken on the alert lane but belongs to the conversation —
    # same exception session_feed.turns makes, for the same reason.
    if ex.get("kind") == "notif" and not isinstance(ex.get("ask"), list):
        return None
    wp = ex.get("writer_pid")
    if wp:
        from . import procinfo

        if not procinfo.alive(wp):
            return None
    at, text = np.get("started_at"), (ex.get("text") or "")
    if at is None or not text.strip():
        return None
    # The sentence being spoken, for a transcript that wants to show it: the
    # same `clip_sentences` + `current_sentence_idx` the terminal highlight and
    # the follow pane read, kept up to date by the sentence loop as it plays.
    try:
        sentence = int(ex.get("current_sentence_idx"))
    except (TypeError, ValueError):
        sentence = None
    # The timeline as well as the index, so a reader can move the highlight on
    # its own clock between asks instead of trailing the voice by a poll. The
    # phone lane records where each sentence starts; the local lane records
    # how long each clip is, which is the same thing summed. Elapsed is read
    # the way the sentence loop reads it (intake.submit.elapsed_from_row):
    # from `play_started_at`, frozen at `paused_at` while paused.
    offsets = [float(x) for x in (ex.get("clip_offsets_s") or [])]
    if not offsets and ex.get("clip_starts_s"):
        # When each sentence actually started, measured as the reply plays —
        # truer than summed durations, which count from submit and leave out
        # the gaps between clips. But measured starts only run as far as the
        # sentence playing, and a reader that waits to be told the next one
        # has begun moves a beat behind the voice at every boundary. So the
        # ones still to come are predicted from the last measured start plus
        # the clip lengths in between, and each new measurement corrects them.
        offsets = _fill_starts([float(x) for x in ex["clip_starts_s"]],
                               ex.get("clip_durations_s"),
                               len(ex.get("clip_sentences") or []))
    if not offsets and ex.get("clip_durations_s"):
        acc = 0.0
        for d in ex.get("clip_durations_s") or []:
            offsets.append(acc)
            acc += float(d or 0)
    base = float(ex.get("play_started_at") or at)
    if ex.get("clip_starts_s") and not ex.get("clip_offsets_s") and offsets and offsets[0] > 0:
        # The clock starts when the voice does. A reply can be claimed long
        # before its first word — held for "When open" until the thread is
        # opened, or behind a hold — and the measured starts count from the
        # claim, so the first sentence began 123 s in (p8a, 26 Sep 2026).
        # Consistent on its own, but the app corrects `elapsed` against the
        # player's `pos`, which counts from the first word: it took the wait
        # for lag, took it off, and landed before sentence one on every
        # sample — the bold kept jumping back to the top.
        lead = offsets[0]
        offsets = [round(o - lead, 3) for o in offsets]
        base += lead
    paused_at = ex.get("paused_at")
    now = float(paused_at) if paused_at else time.time()
    # How long after the clock starts the audio is actually heard — the bridge
    # hop, the far player's start, a Snapcast buffer. The terminal highlight
    # waits this long before moving (`_HighlightScheduler`); a reader that
    # does not is that far ahead of the voice, which is exactly how the app's
    # bold read: a sentence early.
    try:
        from .intake.submit import _playout_delay_s

        delay = _playout_delay_s(str(np.get("target") or ""))
    except Exception:  # noqa: BLE001 — a beat off beats no highlight
        delay = 0.0
    if ex.get("clip_starts_s") and not ex.get("clip_offsets_s"):
        # Measured starts are when the player itself reached each sentence, so
        # the hop to the player is already in them. Taking the playout delay
        # off as well put the bold a beat behind at every boundary.
        delay = 0.0
    return {"at": round(float(at), 3), "text": text,
            # The reply's dedup key: what joins the live line to its message
            # (transcript.join_speech) when the spoken words differ from the
            # written ones — a described or omitted code block.
            "key": str(ex.get("dedup_key") or ""),
            "listener": bool(ex.get("listener")),
            "sentences": [str(x) for x in (ex.get("clip_sentences") or [])],
            "sentence": sentence, "offsets": offsets,
            "elapsed": round(max(0.0, now - base), 3),
            "delay": round(delay, 3),
            # Whether the offsets were measured by the far player (sentence
            # marks) or apportioned by character count — the second drifts
            # within a reply, and a reader debugging a bold that runs ahead
            # needs to know which it is looking at.
            "measured": bool(ex.get("sentence_marks")),
            "target": str(np.get("target") or ""),
            "server_time": round(time.time(), 3),
            "paused": bool(paused_at),
            # Set on a replay: which history row is audible again, so the
            # transcript can light up that line rather than add a new one.
            "history_id": int(ex.get("history_id") or 0)}


def conversation_log(session: str) -> list:
    """The conversation as lines you can read. `[{who, text, at, key}]`.

    A chapter title is one sentence, because a table of contents is for finding
    your place. Reading what was actually said needs the whole turn, and the
    whole turn is in speech history — the manifest keeps only what it needs to
    name a file. So this joins the two: the manifest gives the turns it placed
    their order, history gives them their words.

    Read-only, and derived on demand. It is not a second copy of the
    conversation to be kept in step with the audio; it is the same rows,
    rendered.

    Lines used to carry `start`/`end`, seconds into the conversation's
    Audiobookshelf item; they went with Audiobookshelf (11 Nov 2026).
    """
    turns = _read_manifest(session).get("turns", [])

    # History keyed by the same `at` the manifest recorded, so a turn whose
    # audio has been swept from the cache still has its words here. Speech
    # history is written per turn as it is spoken — well before the debounced
    # publish writes that turn into the manifest — so it is also the source of
    # the *live tail*: turns said but not yet placed in the manifest.
    said = {}
    for t in session_feed.turns(session):
        said[round(float(t.at), 3)] = t

    live = _live_turn(session)
    if not turns and not said and not live:
        return []

    def _line(who_listener, text, at=None, key="", ask=None, command=None, rid=0,
              unheard=False, resume=None, song=None):
        text = (text or "").strip()
        who = "you" if who_listener else "agent"
        if who == "you" and text.startswith("You: "):
            # The label belongs to the chapter title, where there is no other
            # way to tell the sides apart. Here the side is its own field.
            text = text[len("You: "):]
        # `at` and `key` name the turn for anything that wants to hang more on
        # it — the canvas attaches the picture drawn for a reply by its key.
        line = {"who": who, "text": text, "at": at, "key": key or ""}
        if command:
            line["command"] = command
        if rid:
            # The history row, for `/speech/ctl` replay-id: a tap plays the
            # turn through the speech player rather than the book's.
            line["id"] = rid
        if unheard:
            line["unheard"] = True
        if resume:
            line["resume"] = resume
        if song:
            # The song the music bed played under it (music_bed_song): the
            # reply's ⋯ "Play the song".
            line["song"] = song
        if ask:
            # The spoken sentence is "host / pane: Question. Option 1: …" —
            # right for a voice, wrong for a bubble. Hand over the structure
            # and let the reader lay it out; `text` becomes the question alone
            # so a client that knows nothing of `ask` still reads sensibly.
            line["ask"] = ask
            asked = " ".join(str(q.get("question") or "").strip() for q in ask).strip()
            if asked:
                line["text"] = asked
        return line

    def _said_by_anyone(line) -> bool:
        """Was this line said by a person or the assistant at all?

        The hook used to record the harness's own asides — a finished
        background task, a system reminder — as listener turns, and they are
        still in the history of every conversation from before it stopped.
        They are filtered here as well as at the source so the transcripts
        that already have them read properly rather than staying broken.
        """
        if line.get("who") != "you":
            return True
        from .intake._text import strip_system_blocks
        return bool(strip_system_blocks(line.get("text") or ""))

    out = []
    seen = set()
    for turn in turns:
        at = round(float(turn.get("at") or 0.0), 3)
        seen.add(at)
        spoken = said.get(at)
        out.append(_line(spoken and spoken.listener,
                         spoken.text if spoken else turn.get("title"),
                         at, spoken.key if spoken else "",
                         ask=(spoken.ask if spoken else None),
                         command=(spoken.command if spoken else None),
                         rid=(getattr(spoken, "id", 0) if spoken else 0),
                         unheard=bool(spoken and getattr(spoken, "unheard", False)),
                         resume=(getattr(spoken, "resume", None) if spoken else None),
                         song=(getattr(spoken, "song", None) if spoken else None)))

    # The live tail: turns in speech history the manifest has not caught up to
    # yet. Shown at once — the manifest does not place them until the next
    # publish, at which point they join `turns` above. This is what lets a
    # reply appear in the transcript within a poll of being spoken instead of
    # waiting out the publish cycle.
    for at in sorted(a for a in said if a not in seen):
        seen.add(at)
        out.append(_line(said[at].listener, said[at].text, at,
                         said[at].key, ask=said[at].ask,
                         command=said[at].command, rid=getattr(said[at], "id", 0),
                         unheard=getattr(said[at], "unheard", False),
                         resume=getattr(said[at], "resume", None),
                         song=getattr(said[at], "song", None)))

    # The turn speaking right now, if it has not already landed as an ended
    # row above. This is what puts a reply on screen *while* it is being
    # spoken, not after — keyed by the same `at` it will keep, so it becomes
    # the ended row in place rather than a duplicate.
    replayed = None
    if live and live.get("history_id"):
        replayed = next((l for l in out if l.get("id") == live["history_id"]), None)
    if live and (replayed is not None or live["at"] not in seen):
        line = replayed if replayed is not None else _line(
            live["listener"], live["text"], live["at"], live.get("key", ""))
        # Marked live, with the sentence being spoken, so the transcript can
        # follow the voice sentence by sentence rather than just show the turn.
        # A replay marks the turn it replays, in its place.
        line.pop("unheard", None)   # it is being heard now
        line.update({"live": True, "sentences": live["sentences"],
                     "sentence": live["sentence"], "offsets": live["offsets"],
                     "elapsed": live["elapsed"], "paused": live["paused"],
                     # When `elapsed` was read, so it can be brought up to the
                     # moment the answer leaves (canvas), and the offset the
                     # reader takes off the clock.
                     "server_time": live["server_time"], "delay": live["delay"]})
        if replayed is None:
            out.append(line)

    # The newest spoken turn carries its sentences and their offsets even when
    # nothing is live. The live line exists only while `now_playing` names the
    # reply, and there are several ways to lose that row while the audio plays
    # on — a barge-in, a submit that died, a phone that was handed the clips
    # and is playing them by itself. A reader that was following then had
    # nothing to follow for the rest of the reply.
    #
    # No `live`, no `elapsed`, no `server_time`: this is not a claim that the
    # turn is playing. It is the timeline, handed over so a player that knows
    # its own position can bold from its own clock — which on the phone lane
    # is the app itself. One turn, because a whole conversation's sentences is
    # a payload nobody reads and the newest is the only one still in a player.
    newest = max(said) if said else None
    if newest is not None and getattr(said[newest], "sentences", None):
        for line in reversed(out):
            if line.get("at") != newest:
                continue
            if not line.get("live") and "sentences" not in line:
                turn = said[newest]
                offsets = [round(x, 3) for x in _fill_starts(
                    getattr(turn, "starts", None) or [],
                    getattr(turn, "durations", None),
                    len(getattr(turn, "sentences", None) or []))]
                if offsets and offsets[0] > 0:
                    # From the first word, as `pos` counts (see _live_turn):
                    # the app bolds this timeline against the player's own
                    # position, and a wait before the voice would put every
                    # sentence that far past it.
                    offsets = [round(o - offsets[0], 3) for o in offsets]
                if not offsets:
                    acc, offsets = 0.0, []
                    for d in (getattr(turn, "durations", None) or []):
                        offsets.append(round(acc, 3))
                        acc += float(d or 0)
                line.update({"sentences": list(turn.sentences),
                             "offsets": offsets,
                             # Measured by the player, or apportioned from the
                             # clip lengths — the second drifts within a reply,
                             # and a reader owed an explanation for a bold that
                             # runs ahead needs to know which it was handed.
                             "measured": bool(getattr(turn, "starts", None))})
            break
    return [line for line in out if _said_by_anyone(line)]


def rename(session: str, title: str) -> str:
    """Call this conversation `title` from now on. "" if it could not be.

    Kept in the manifest, and the agent is told as well, so the terminal and
    the app agree.

    The name file is for a session that is not running — a live Claude Code
    does not read it again. A live one is renamed by typing `/rename` into it
    (`reply.send_rename`), which is what makes the window follow too: this
    host builds window names from the pane, and renaming the window here
    turned its automatic-rename off, which lost every name after the first.
    """
    from . import conversation

    title = " ".join((title or "").split())[:200]
    if not session or not title:
        return ""
    manifest = _read_manifest(session)
    manifest["title"] = title
    _write_manifest(session, manifest)
    # And the agent itself, where it can be told: Claude keeps a name file,
    # Hermes takes a rename on its store, the other two have no way in that
    # does not write into their own records (see harnesses.set_name).
    from . import harnesses

    if harnesses.is_hermes(session):
        harnesses.set_name(session, title)
    else:
        conversation.set_session_name(session, title)
        # Closed: the name has to be in the transcript to survive a resume.
        conversation.record_title(session, title)
    return title
