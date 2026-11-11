"""A conversation laid out as an item that grows.

The property everything else rests on: **nothing already written is ever
rewritten**. That is what lets Audiobookshelf keep the item's id, keep the
existing files' inodes, and keep the listener's position while the conversation
carries on — measured against 2.35.1 in
`docs/proposals/2026-09-02-growing-item-experiment.md`, which is what this
module was written against.
"""

import json

import pytest

from agent_media_core import book_tracks, session_feed


@pytest.fixture(autouse=True)
def tree(tmp_path, monkeypatch):
    """A book tree and a state dir of our own."""
    monkeypatch.setenv("MEDIA_BOOK_TRACKS_ROOT", str(tmp_path / "books"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    return tmp_path


def _clip(tmp_path, name: str, body: bytes = b"audio") -> str:
    p = tmp_path / "clips" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(body)
    return str(p)


def _turn(tmp_path, at: float, text: str, clips: int = 1, said=None):
    return session_feed.Turn(
        at=at, text=text, workspace="p-agent-media",
        clips=[_clip(tmp_path, f"{at}-{i}.mp3", f"clip {at}.{i}".encode())
               for i in range(clips)],
        durations=[3.0] * clips,
        sentences=list(said or []))


@pytest.fixture
def conversation(tmp_path, monkeypatch):
    """Three turns that can be added to, one clip each (no ffmpeg needed)."""
    turns = [_turn(tmp_path, 100.0, "First thing said.", clips=2,
                   said=["First thing said.", "And a second sentence."]),
             _turn(tmp_path, 200.0, "Second thing said.", clips=1,
                   said=["Second thing said."]),
             _turn(tmp_path, 300.0, "Third thing said.", clips=1,
                   said=["Third thing said."])]
    state = {"turns": turns[:2]}
    monkeypatch.setattr(session_feed, "turns",
                        lambda session, store=None: list(state["turns"]))
    monkeypatch.setattr(session_feed, "workspace_for",
                        lambda session, ts: "p-agent-media")
    # asked_for, not title_for: the shelf files under the workspace already,
    # so the folder is named for the conversation alone.
    monkeypatch.setattr(session_feed, "asked_for",
                        lambda session, ts: "How the growing item works")
    return state, turns


def test_the_clips_are_the_tracks_named_by_what_they_say(conversation):
    """No audio is written: each track is a second name for the clip the
    renderer already made, and it is named with the sentence it says."""
    folder, added = book_tracks.export_session("sess-1")
    assert added == 3                      # two clips in turn one, one in turn two
    assert sorted(p.name for p in folder.iterdir()) == [
        "0001 - First thing said.mp3",
        "0002 - And a second sentence.mp3",
        "0003 - Second thing said.mp3"]
    for track in folder.iterdir():
        assert track.stat().st_nlink == 2  # linked, never copied


def test_the_folder_is_workspace_over_title(conversation):
    """What a book library reads off a path: the author is the workspace, the
    title is the conversation."""
    folder, _ = book_tracks.export_session("sess-1")
    assert folder.parent.name == "p-agent-media"
    assert folder.name == "How the growing item works"


@pytest.mark.parametrize("workspace", [
    "tmp-claude-1000--home-ryer-projects-runlet-027c24b1-a031-44a8-97e4-"
    "f890c45494d4-scratchpad-handoff-test",
    "home-ryer-x-scratchpad-probe"])
def test_a_throwaway_session_is_not_shelved(conversation, monkeypatch, workspace):
    """A session started in a temp dir to test something is not a project."""
    monkeypatch.setattr(session_feed, "workspace_for",
                        lambda session, ts: workspace)
    assert book_tracks.export_session("sess-1") == (None, 0)


def test_the_scratch_workspace_is_still_shelved(conversation, monkeypatch):
    monkeypatch.setattr(session_feed, "workspace_for",
                        lambda session, ts: "scratch")
    folder, _ = book_tracks.export_session("sess-1")
    assert folder.parent.name == "scratch"


def test_a_new_turn_appends_and_touches_nothing_else(conversation):
    state, turns = conversation
    folder, _ = book_tracks.export_session("sess-1")
    before = {p.name: (p.stat().st_ino, p.stat().st_mtime_ns)
              for p in folder.iterdir()}

    state["turns"] = turns                      # the conversation goes on
    folder2, added = book_tracks.export_session("sess-1")

    assert (folder2, added) == (folder, 1)
    after = {p.name: (p.stat().st_ino, p.stat().st_mtime_ns)
             for p in folder.iterdir()}
    assert "0004 - Third thing said.mp3" in after
    for name, was in before.items():
        assert after[name] == was, f"{name} was rewritten"


def test_running_again_with_nothing_new_writes_nothing(conversation):
    book_tracks.export_session("sess-1")
    folder, added = book_tracks.export_session("sess-1")
    assert added == 0


def test_the_folder_survives_a_better_title(conversation, monkeypatch):
    """A conversation's title comes from what was asked and can improve as it
    goes. Renaming the folder would hand ABS a new item — new id, no progress,
    the old one left behind — so the first answer is the one that is kept."""
    folder, _ = book_tracks.export_session("sess-1")
    monkeypatch.setattr(session_feed, "asked_for",
                        lambda session, ts: "A much better title")
    folder2, _ = book_tracks.export_session("sess-1")
    assert folder2 == folder


def test_a_lost_manifest_adopts_what_is_on_disk(conversation, tmp_path):
    """The manifest is a record of the tree, not its owner. If it goes and the
    tree stays, the export must recognise its own work — not write every turn
    again beside itself, which is an item that plays the conversation twice."""
    folder, _ = book_tracks.export_session("sess-1")
    book_tracks._manifest_path("sess-1").unlink()

    folder2, added = book_tracks.export_session("sess-1")
    assert (folder2, added) == (folder, 0)     # adopted, not re-linked
    assert len(list(folder.iterdir())) == 3
    kept = json.loads(book_tracks._manifest_path("sess-1").read_text())
    assert [f for t in kept["turns"] for f in t["files"]] == [
        "0001 - First thing said.mp3", "0002 - And a second sentence.mp3",
        "0003 - Second thing said.mp3"]


def test_a_clip_that_will_not_go_stops_the_run(conversation, monkeypatch):
    """The numbering is sequential, so carrying on past a failure would file
    the next sentence under this one's number — a conversation out of order."""
    state, turns = conversation
    state["turns"] = turns
    monkeypatch.setattr(book_tracks, "place_clip", lambda src, out: None)
    folder, added = book_tracks.export_session("sess-1")
    assert added == 0
    assert not folder.exists() or not list(folder.iterdir())


def test_track_numbers_are_padded_so_ten_follows_nine():
    names = [book_tracks.track_name(i, "A sentence") for i in (2, 10, 1000)]
    assert names[:2] == ["0002 - A sentence.mp3", "0010 - A sentence.mp3"]
    assert sorted(names) == names          # what a scanner will do with them


def test_a_clip_with_no_recorded_sentence_falls_back_to_the_turn():
    assert book_tracks.track_name(1, "", "The turn") == "0001 - The turn.mp3"


def test_a_track_is_a_second_name_not_a_second_copy(tmp_path):
    src = tmp_path / "one.mp3"
    src.write_bytes(b"the only copy")
    out = tmp_path / "item" / "0001 - one.mp3"
    assert book_tracks.place_clip(str(src), out) == out
    assert out.stat().st_ino == src.stat().st_ino


# --- the transcript shows a turn before the manifest catches up -----------------

def test_conversation_log_includes_the_live_tail(tmp_path, monkeypatch):
    """A turn in speech history but not yet in the manifest still appears —
    with no position — so a reply lands in the transcript within a poll of
    being spoken instead of waiting out the debounced publish."""
    from agent_media_core import book_tracks as bt, session_feed
    # The manifest is one turn behind: it has the agent's turn at 100 only.
    monkeypatch.setattr(bt, "_read_manifest",
                        lambda s: {"turns": [{"at": 100.0, "title": "First answer"}]})
    # Speech history has the published turn plus a newer listener turn (200)
    # and the agent's fresh reply (300) that has not been published yet.
    hist = [
        session_feed.Turn(at=100.0, text="First answer", workspace="p-agent-media",
                          clips=[], durations=[], sentences=[], listener=False),
        session_feed.Turn(at=200.0, text="You: a question", workspace="p-agent-media",
                          clips=[], durations=[], sentences=[], listener=True),
        session_feed.Turn(at=300.0, text="The fresh reply", workspace="p-agent-media",
                          clips=[], durations=[], sentences=[], listener=False),
    ]
    monkeypatch.setattr(session_feed, "turns", lambda s, store=None: list(hist))

    lines = bt.conversation_log("sess-1")
    assert [(l["who"], l["text"]) for l in lines] == [
        ("agent", "First answer"),   # from the manifest turn
        ("you", "a question"),        # live tail: "You: " stripped
        ("agent", "The fresh reply"), # live tail: the reply, shown at once
    ]


def test_conversation_log_lines_carry_their_history_id(tmp_path, monkeypatch):
    """A tap on a line replays that turn by id through the speech player."""
    from agent_media_core import book_tracks as bt, session_feed
    monkeypatch.setattr(bt, "_read_manifest",
                        lambda s: {"turns": [{"at": 100.0, "title": "First answer"}]})
    hist = [
        session_feed.Turn(at=100.0, text="First answer", id=41),
        session_feed.Turn(at=300.0, text="The fresh reply", id=42),
    ]
    monkeypatch.setattr(session_feed, "turns", lambda s, store=None: list(hist))
    monkeypatch.setattr(bt, "_live_turn", lambda s: None)

    lines = bt.conversation_log("sess-1")
    assert [l.get("id") for l in lines] == [41, 42]


def test_a_replay_lights_up_its_own_line(tmp_path, monkeypatch):
    """Replaying a turn marks that line live where it is, not a new line at
    the bottom with the replay's own start time."""
    from agent_media_core import book_tracks as bt, session_feed
    monkeypatch.setattr(bt, "_read_manifest", lambda s: {"turns": []})
    hist = [
        session_feed.Turn(at=100.0, text="First answer", id=41),
        session_feed.Turn(at=300.0, text="Second answer", id=42),
    ]
    monkeypatch.setattr(session_feed, "turns", lambda s, store=None: list(hist))
    monkeypatch.setattr(bt, "_live_turn", lambda s: {
        "at": 900.0, "text": "First answer", "listener": False,
        "sentences": ["First answer"], "sentence": 0, "offsets": [0.0],
        "elapsed": 0.4, "paused": False, "server_time": 900.4, "delay": 0.0,
        "history_id": 41})

    lines = bt.conversation_log("sess-1")
    assert [(l["text"], bool(l.get("live"))) for l in lines] == [
        ("First answer", True), ("Second answer", False)]


def test_conversation_log_shows_the_turn_being_spoken_now(tmp_path, monkeypatch):
    """The in-flight turn (in now_playing, not yet in history or the manifest)
    shows at once, so a reply appears while it is being spoken."""
    from agent_media_core import book_tracks as bt, session_feed
    from agent_media_core.state.store import StateStore
    monkeypatch.setattr(bt, "_read_manifest",
                        lambda s: {"turns": [{"at": 100.0, "title": "Earlier"}]})
    monkeypatch.setattr(session_feed, "turns", lambda s, store=None: [
        session_feed.Turn(at=100.0, text="Earlier", workspace="p-agent-media",
                          clips=[], durations=[], sentences=[], listener=False)])
    # now_playing: this session's reply is mid-flight, writer alive (our pid).
    import os
    monkeypatch.setattr(StateStore, "get_now_playing", lambda self, sink: {
        "started_at": 300.0,
        "extras": {"source_session": "sess-1", "text": "Speaking this now",
                   "writer_pid": os.getpid(), "listener": False,
                   "clip_sentences": ["Speaking this now.", "And this next."],
                   "current_sentence_idx": 1,
                   # Local lane: durations, no offsets. Paused 4.5s in.
                   "clip_durations_s": [2.0, 3.0],
                   "play_started_at": 1000.0, "paused_at": 1004.5}})
    lines = bt.conversation_log("sess-1")
    # When the reading was taken, and the offset the reader takes off it.
    assert lines[-1].pop("server_time") > 0
    assert lines[-1].pop("delay") >= 0
    assert lines[-1] == {"at": 300.0, "key": "",
                         "who": "agent", "text": "Speaking this now",
                         # Live, which sentence the voice is on, and the
                         # timeline so a reader can move it on its own clock.
                         "live": True, "sentence": 1,
                         "sentences": ["Speaking this now.", "And this next."],
                         "offsets": [0.0, 2.0], "elapsed": 4.5, "paused": True}
    # Still playing: elapsed runs on the wall clock from play_started_at.
    # (The first time this path ran for real it raised NameError — `time`
    # was never imported — and every transcript asked for while a reply was
    # audible came back as a 500 the app showed as "Nothing said yet.")
    monkeypatch.setattr(StateStore, "get_now_playing", lambda self, sink: {
        "started_at": 300.0,
        "extras": {"source_session": "sess-1", "text": "Speaking this now",
                   "writer_pid": os.getpid(), "listener": False,
                   "clip_sentences": ["Speaking this now.", "And this next."],
                   "current_sentence_idx": 0,
                   "clip_durations_s": [2.0, 3.0],
                   "play_started_at": 1000.0}})
    monkeypatch.setattr(bt.time, "time", lambda: 1001.25)
    live = bt.conversation_log("sess-1")[-1]
    assert live["live"] and live["paused"] is False
    assert live["elapsed"] == 1.25 and live["sentence"] == 0


def test_conversation_log_ignores_a_live_turn_from_another_session(tmp_path, monkeypatch):
    from agent_media_core import book_tracks as bt, session_feed
    from agent_media_core.state.store import StateStore
    import os
    monkeypatch.setattr(bt, "_read_manifest",
                        lambda s: {"turns": [{"at": 100.0, "title": "Earlier"}]})
    monkeypatch.setattr(session_feed, "turns", lambda s, store=None: [
        session_feed.Turn(at=100.0, text="Earlier", workspace="p-agent-media",
                          clips=[], durations=[], sentences=[], listener=False)])
    monkeypatch.setattr(StateStore, "get_now_playing", lambda self, sink: {
        "started_at": 300.0,
        "extras": {"source_session": "OTHER", "text": "not this conversation",
                   "writer_pid": os.getpid()}})
    lines = bt.conversation_log("sess-1")
    assert [l["text"] for l in lines] == ["Earlier"]


def test_live_turn_carries_the_playout_delay_for_its_target(monkeypatch):
    import json, time
    from agent_media_core import book_tracks as b
    ex = {"source_session": "s1", "text": "One. Two.", "clip_sentences": ["One.", "Two."],
          "clip_offsets_s": [0.0, 1.5], "play_started_at": time.time() - 1.0}
    row = {"started_at": 100.0, "target": "phone", "extras": json.dumps(ex)}

    class Store:
        def get_now_playing(self, sink):
            return row

    monkeypatch.setattr("agent_media_core.state.store.StateStore", lambda: Store())
    monkeypatch.setenv("MEDIA_SPEECH_PLAYOUT_MS_PHONE", "1200")
    turn = b._live_turn("s1")
    assert turn["delay"] == 1.2 and turn["offsets"] == [0.0, 1.5] and 0.9 < turn["elapsed"] < 1.3


# --- what a conversation is called ------------------------------------------------

def test_a_rename_is_kept_and_told(tmp_path, monkeypatch):
    from agent_media_core import book_tracks as b
    from agent_media_core import conversation

    monkeypatch.setattr(b, "state_dir", lambda: tmp_path)
    b._write_manifest("s1", {"session": "s1", "folder": "/c/p-x/Old name"})
    told = []
    monkeypatch.setattr(conversation, "set_session_name",
                        lambda session, title: told.append((session, title)) or True)

    assert b.rename("s1", "  A better   name  ") == "A better name"
    assert b._read_manifest("s1")["title"] == "A better name"
    assert told == [("s1", "A better name")]


def test_a_rename_with_no_name_is_not_one(tmp_path, monkeypatch):
    from agent_media_core import book_tracks as b

    monkeypatch.setattr(b, "state_dir", lambda: tmp_path)
    assert b.rename("s1", "   ") == ""


def test_a_rename_leaves_the_tmux_window_alone(tmp_path, monkeypatch):
    """The window name is the pane's business on this host (tmux builds it
    from the pane's label), and renaming it here turned its automatic-rename
    off — so every later name was lost. A live session is renamed by typing
    `/rename` into it instead (reply.send_rename)."""
    from agent_media_core import book_tracks as b
    from agent_media_core import conversation

    monkeypatch.setattr(b, "state_dir", lambda: tmp_path)
    b._write_manifest("s1", {"session": "s1"})
    named = []
    monkeypatch.setattr(conversation, "set_session_name",
                        lambda session, title: named.append(title) or True)
    monkeypatch.setattr(conversation, "pane_of",
                        lambda session: pytest.fail("touched tmux"))
    assert b.rename("s1", "A better name") == "A better name"
    assert named == ["A better name"]


def test_the_window_is_this_sessions_pane_and_a_live_one(tmp_path, monkeypatch):
    import os
    from agent_media_core import conversation

    monkeypatch.setenv("MEDIA_PANE_REGISTRY_DIR", str(tmp_path))
    (tmp_path / "7").write_text(f"s-live {os.getpid()} /home/ryer")
    (tmp_path / "8").write_text("s-dead 999999999 /home/ryer")
    assert conversation.pane_of("s-live") == "%7"
    assert conversation.pane_of("s-dead") == ""      # its pid is gone
    assert conversation.pane_of("s-never") == ""


def test_the_newest_turn_carries_its_timeline_without_being_live(tmp_path, monkeypatch):
    """The live line exists only while `now_playing` names the reply, and a
    barge-in (or a submit that died) can take that row away while the phone
    plays on. The newest turn hands over its sentences and offsets anyway, so
    a player that knows its own position keeps bolding — and says nothing
    about being live, because it is not a claim that anything is playing."""
    from agent_media_core import book_tracks as bt, session_feed
    monkeypatch.setattr(bt, "_read_manifest", lambda s: {"turns": []})
    monkeypatch.setattr(bt, "_live_turn", lambda s: None)
    hist = [
        session_feed.Turn(at=100.0, text="An older answer.",
                          sentences=["An older answer."], durations=[2.0]),
        session_feed.Turn(at=300.0, text="One. Two. Three.",
                          sentences=["One.", "Two.", "Three."],
                          durations=[1.0, 2.0, 3.0],
                          starts=[0.0, 1.4, 3.9]),
    ]
    monkeypatch.setattr(session_feed, "turns", lambda s, store=None: list(hist))

    lines = bt.conversation_log("sess-1")
    newest = lines[-1]
    assert newest["sentences"] == ["One.", "Two.", "Three."]
    assert newest["offsets"] == [0.0, 1.4, 3.9]   # measured, not summed
    assert newest["measured"] is True
    assert "live" not in newest and "elapsed" not in newest
    # Only the newest: a whole conversation's sentences is payload nobody reads.
    assert "sentences" not in lines[0]


def test_a_newest_turn_measured_part_way_predicts_the_rest(tmp_path, monkeypatch):
    """Starts are measured as the player reaches each sentence, so a reply
    still playing on the phone (or cut off) has them only as far as it got.
    The rest follow from the clip lengths, so the app's sentence marks span
    the whole reply instead of crowding into the part already heard (David,
    2 Nov 2026: "The ticks aren't spaced out properly, all the way to the end
    of the line")."""
    from agent_media_core import book_tracks as bt, session_feed
    monkeypatch.setattr(bt, "_read_manifest", lambda s: {"turns": []})
    monkeypatch.setattr(bt, "_live_turn", lambda s: None)
    hist = [session_feed.Turn(at=300.0, text="One. Two. Three. Four.",
                              sentences=["One.", "Two.", "Three.", "Four."],
                              durations=[1.0, 2.0, 3.0, 4.0],
                              starts=[5.0, 6.4])]
    monkeypatch.setattr(session_feed, "turns", lambda s, store=None: list(hist))

    newest = bt.conversation_log("sess-1")[-1]
    # From the first word, measured where measured, then by the clip lengths.
    assert newest["offsets"] == [0.0, 1.4, 3.4, 6.4]
    assert newest["measured"] is True


def test_an_unmeasured_turn_apportions_its_offsets(tmp_path, monkeypatch):
    """A reply that never played far enough to measure a start still gets a
    timeline — summed clip lengths — and is labelled as not measured, because
    that one drifts within a reply and a reader is owed the difference."""
    from agent_media_core import book_tracks as bt, session_feed
    monkeypatch.setattr(bt, "_read_manifest", lambda s: {"turns": []})
    monkeypatch.setattr(bt, "_live_turn", lambda s: None)
    hist = [session_feed.Turn(at=300.0, text="One. Two.",
                              sentences=["One.", "Two."], durations=[1.5, 2.0])]
    monkeypatch.setattr(session_feed, "turns", lambda s, store=None: list(hist))

    line = bt.conversation_log("sess-1")[-1]
    assert line["offsets"] == [0.0, 1.5]
    assert line["measured"] is False


def test_a_live_newest_turn_keeps_the_live_fields(tmp_path, monkeypatch):
    """When the row is live, the live line wins: its `elapsed`/`server_time`
    are not overwritten by the ended turn's static timeline."""
    from agent_media_core import book_tracks as bt, session_feed
    monkeypatch.setattr(bt, "_read_manifest", lambda s: {"turns": []})
    hist = [session_feed.Turn(at=300.0, text="One. Two.", id=7,
                              sentences=["One.", "Two."], durations=[1.5, 2.0])]
    monkeypatch.setattr(session_feed, "turns", lambda s, store=None: list(hist))
    monkeypatch.setattr(bt, "_live_turn", lambda s: {
        "at": 900.0, "text": "One. Two.", "listener": False,
        "sentences": ["One.", "Two."], "sentence": 1, "offsets": [0.0, 1.4],
        "elapsed": 2.0, "paused": False, "server_time": 902.0, "delay": 0.0,
        "history_id": 7})

    line = bt.conversation_log("sess-1")[-1]
    assert line["live"] is True
    assert line["offsets"] == [0.0, 1.4] and line["elapsed"] == 2.0


def test_live_turn_starts_its_clock_at_the_first_word(monkeypatch):
    # A reply claimed two minutes before it was heard (held for "When open"):
    # the measured starts count from the claim. `elapsed` and the offsets must
    # both count from the first word, as the player's `pos` does, or the app's
    # skew correction takes the wait off and bolds sentence one forever.
    import json, time
    from agent_media_core import book_tracks as b
    now = time.time()
    ex = {"source_session": "s1", "text": "One. Two. Three.",
          "clip_sentences": ["One.", "Two.", "Three."],
          "clip_starts_s": [123.7, 132.9], "clip_durations_s": [9.1, 6.8, 6.4],
          "play_started_at": now - 173.5}
    row = {"started_at": 100.0, "target": "sasonica", "extras": json.dumps(ex)}

    class Store:
        def get_now_playing(self, sink):
            return row

    monkeypatch.setattr("agent_media_core.state.store.StateStore", lambda: Store())
    turn = b._live_turn("s1")
    assert turn["offsets"] == [0.0, 9.2, 16.0]
    assert 49.5 < turn["elapsed"] < 50.2


def test_the_live_line_carries_the_replys_key(tmp_path, monkeypatch):
    """The turn being spoken joins its message by key (transcript.join_speech).
    Keyless, it joined by words — and a reply whose two code blocks were spoken
    as descriptions scored 0.56 against a 0.6 bar, so it had no live line and
    no bold for its whole length (session 2ccb8b31, 18 Oct 2026)."""
    import os
    from agent_media_core import book_tracks as bt, session_feed
    from agent_media_core.state.store import StateStore
    monkeypatch.setattr(bt, "_read_manifest", lambda s: {"turns": []})
    monkeypatch.setattr(session_feed, "turns", lambda s, store=None: [])
    monkeypatch.setattr(StateStore, "get_now_playing", lambda self, sink: {
        "started_at": 300.0,
        "extras": {"source_session": "sess-1", "text": "Described words.",
                   "writer_pid": os.getpid(), "dedup_key": "c900c50f",
                   "clip_sentences": ["Described words."],
                   "clip_durations_s": [2.0], "play_started_at": 1000.0}})
    assert bt._live_turn("sess-1")["key"] == "c900c50f"
    (line,) = bt.conversation_log("sess-1")
    assert line["live"] and line["key"] == "c900c50f"
