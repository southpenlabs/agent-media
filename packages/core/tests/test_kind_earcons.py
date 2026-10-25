"""Tones by kind (David, 26 Oct 2026; agent_media_core/earcons.py): a harp
under a person's message, a wind chime before an alert, a paper whoosh before
an agent's reply to agent mail, Air in a key per project, and the phone told
the kind (`tone-kind`) so a phone sound picked in the app can stand in. The
real submission loop, with no real players."""
import array
import json
import math
from pathlib import Path

import pytest

from agent_media_core import earcons
from agent_media_core.intake import hook_claude_code as H
from agent_media_core.intake import submit as S
from agent_media_core.state import StateStore
from agent_media_core.types import Event, Priority, Source, Target


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    import os
    for name in list(os.environ):
        if name.startswith(("MEDIA_", "RELAY_")):
            monkeypatch.delenv(name, raising=False)
    for name in ("HOME", "XDG_STATE_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name))
    monkeypatch.delenv("TMUX_PANE", raising=False)
    monkeypatch.setenv("MEDIA_FOLLOW_AUTO", "0")
    monkeypatch.setattr(S, "render_text", _render)
    monkeypatch.setattr(S, "_clip_duration", lambda p: 0.0)
    monkeypatch.setattr(S, "_gate_hold", lambda *a: False)
    monkeypatch.setattr(S, "_busy_hold", lambda *a: None)
    monkeypatch.setattr(S, "_ringer_hold", lambda *a: None)
    monkeypatch.setattr(S, "_remote_playlist", lambda *a: False)
    monkeypatch.setattr(S, "_source_place", lambda *a: ("", ""))
    monkeypatch.setattr(S, "ringer_quiet", lambda *a: None)
    monkeypatch.setattr(earcons, "_wait_played", lambda *a: None)


def _render(text, outfile, **kw):
    Path(outfile).write_bytes(b"fake audio")
    return True, ""


class Coord:
    def cancel_pre_pause(self): pass
    def pre_pause_remote(self): pass
    def before_speech(self, **kw): pass
    def after_speech(self): pass
    def speaking_line(self, *a): pass


class KindSink:
    """Records tones (with the kind each was told as) and clips in order."""

    def __init__(self):
        self.played = []

    def play_cue(self, uri, target, kind=""):
        self.played.append(("cue", Path(uri).name, kind))
        return True

    def play_under(self, uri, target, loop=False, kind=""):
        self.played.append(("under", Path(uri).name, kind))
        return True

    def stop_under(self, target):
        self.played.append(("stop_under",))

    def clear_under(self, target):
        self.played.append(("clear_under",))

    def clear_tone_kind(self, target):
        self.played.append(("clear_kind",))

    def play(self, uri, target, **kw):
        self.played.append(uri)

    def idle(self, target): return True
    def paused(self, target): return False
    def stop(self, target): pass

    def tones(self, what):
        return [p[1:] for p in self.played if isinstance(p, tuple) and p[0] == what]


def speak(text, source, sink, priority=Priority.NORMAL, **meta):
    return S.submit_event(
        Event(text, source, priority, target=Target("local"),
              metadata={"session": "kind-test", **meta}),
        state=StateStore(), sink=sink, coordinator=Coord())


# ---- the tones -----------------------------------------------------------------

def _peak_dbfs(name):
    pk = max(abs(s) for s in array.array("h", earcons.synth(name)))
    return 20 * math.log10(pk / 32767)


@pytest.mark.parametrize("name,level", [
    ("people", -26.0), ("alert", -15.0), ("mail", -16.0),
    ("reply-E", -28.0), ("reply-G", -28.0), ("reply-C", -28.0)])
def test_each_new_tone_is_made_at_the_chosen_level(name, level):
    assert name in earcons.NAMES
    assert abs(_peak_dbfs(name) - level) < 0.2
    p = earcons.path(name)
    assert p.name == f"earcon-{name}-v{earcons.VERSION}.wav"
    assert p.stat().st_size > 44


def test_the_version_is_bumped_for_the_new_recipes():
    assert earcons.VERSION >= 10


def test_their_lengths_are_the_recipes():
    assert 4.0 < earcons.duration_s("people") < 4.1        # cut at 4 s, padded
    assert 1.6 < earcons.duration_s("alert") < 1.85        # up to 0.6 + 1.2 s
    assert 0.6 < earcons.duration_s("mail") < 0.66         # 0.5 s + 0.1 quiet
    for key in "EGC":
        assert 3.8 < earcons.duration_s(f"reply-{key}") < 3.9


def test_the_random_ones_are_the_same_every_time():
    for name in ("alert", "mail"):
        assert earcons.synth(name) == earcons.synth(name)


def test_the_harp_loops_without_a_click():
    pcm = array.array("h", earcons.synth("people"))
    assert abs(pcm[0]) < 50 and abs(pcm[-1]) < 50


def test_the_keys_are_different_chords_and_reply_is_e():
    assert earcons.synth("reply-E") != earcons.synth("reply-G") != earcons.synth("reply-C")
    assert earcons.path("reply") == earcons.path("reply-E")


def test_the_switches(monkeypatch):
    for name in ("people", "alert", "mail", "reply-G"):
        assert earcons.enabled(name)
    monkeypatch.setenv("MEDIA_EARCON_REPLY", "0")
    assert not earcons.enabled("reply-G") and not earcons.enabled("reply")
    monkeypatch.setenv("MEDIA_EARCON_PEOPLE", "0")
    monkeypatch.setenv("MEDIA_EARCON_ALERT", "0")
    monkeypatch.setenv("MEDIA_EARCON_MAIL", "off")
    assert not any(earcons.enabled(n) for n in ("people", "alert", "mail"))
    assert earcons.enabled("think")


# ---- the reply's key -------------------------------------------------------------

def _repo(path):
    (path / ".git").mkdir(parents=True)
    return path


def test_agent_media_is_in_e_and_a_worktree_is_its_main_checkout(tmp_path):
    main = _repo(tmp_path / "agent-media")
    (main / "packages" / "core").mkdir(parents=True)
    assert earcons.project_name(str(main / "packages" / "core")) == "agent-media"
    assert earcons.reply_key(str(main / "packages" / "core")) == "E"
    wt = tmp_path / "agent-media-wt-x"
    wt.mkdir()
    (main / ".git" / "worktrees" / "agent-media-wt-x").mkdir(parents=True)
    (wt / ".git").write_text(f"gitdir: {main}/.git/worktrees/agent-media-wt-x\n")
    assert earcons.project_name(str(wt)) == "agent-media"
    assert earcons.reply_key(str(wt)) == "E"


def test_other_projects_keep_one_key_each(tmp_path):
    names = ["sasonica-app", "agent-mail", "agent-memory", "websites", "paragtd",
             "meridian", "astrotunes", "agent-yoga"]
    keys = {n: earcons.reply_key(str(_repo(tmp_path / n))) for n in names}
    assert set(keys.values()) <= {"E", "G", "C"}
    assert len(set(keys.values())) > 1, "every project in one key"
    # Stable: the same answer every time, and for any folder inside.
    for n in names:
        (tmp_path / n / "src").mkdir()
        assert earcons.reply_key(str(tmp_path / n / "src")) == keys[n]


def test_keys_can_be_chosen_or_turned_off(monkeypatch, tmp_path):
    app = str(_repo(tmp_path / "sasonica-app"))
    monkeypatch.setenv("MEDIA_REPLY_TONE_KEYS", "sasonica-app=C, agent-media=g")
    assert earcons.reply_key(app) == "C"
    assert earcons.reply_key(str(_repo(tmp_path / "agent-media"))) == "G"
    monkeypatch.setenv("MEDIA_REPLY_TONE_KEYS", f"{app}=G")
    assert earcons.reply_key(app) == "G"
    monkeypatch.setenv("MEDIA_REPLY_TONE_KEYS", "off")
    assert earcons.reply_key(app) == "E"
    assert earcons.reply_name(app) == "reply-E"
    monkeypatch.delenv("MEDIA_REPLY_TONE_KEYS")
    assert earcons.reply_key("") == "E"


# ---- which speech gets which tone --------------------------------------------------

LINE = "A line long enough to be its own sentence. And a second one."


def test_an_agents_reply_gets_air_in_its_projects_key(monkeypatch, tmp_path):
    monkeypatch.setenv("MEDIA_REPLY_TONE_KEYS", "proj=G")
    folder = str(_repo(tmp_path / "proj"))
    sink = KindSink()
    speak(LINE, Source.CLAUDE_CODE, sink, cwd=folder)
    assert sink.tones("under") == [(earcons.path("reply-G").name, "")]
    assert sink.tones("cue") == []
    assert ("stop_under",) in sink.played


def test_a_reply_from_a_workspace_session_uses_its_project(monkeypatch):
    monkeypatch.setenv("MEDIA_REPLY_TONE_KEYS", "someproj=C")
    sink = KindSink()
    speak(LINE, Source.CLAUDE_CODE, sink, tmux="p-someproj")
    assert sink.tones("under") == [(earcons.path("reply-C").name, "")]


def test_a_persons_message_gets_the_harp_told_as_people():
    sink = KindSink()
    speak("Anna: are you coming for dinner? We are at the usual place.",
          Source.MATRIX, sink)
    assert sink.tones("under") == [(earcons.path("people").name, "people")]
    assert sink.tones("cue") == []
    assert ("stop_under",) in sink.played


def test_the_people_switch(monkeypatch):
    monkeypatch.setenv("MEDIA_EARCON_PEOPLE", "0")
    sink = KindSink()
    speak("Anna: are you coming for dinner? We are at the usual place.",
          Source.MATRIX, sink)
    assert sink.tones("under") == []


@pytest.mark.parametrize("source,meta,priority", [
    (Source.CLI, {"alert": True}, Priority.NORMAL),        # media say --alert
    (Source.HA_SSE, {"kind": "announce"}, Priority.HIGH),  # Home Assistant
])
def test_an_alert_opens_with_the_chime_told_as_alert(source, meta, priority):
    sink = KindSink()
    speak(LINE, source, sink, priority=priority, **meta)
    cues = sink.tones("cue")
    assert (earcons.path("alert").name, "alert") in cues
    # Before the voice, and its kind taken back after it.
    first_clip = next(i for i, p in enumerate(sink.played) if isinstance(p, str))
    cue_at = sink.played.index(("cue", earcons.path("alert").name, "alert"))
    assert cue_at < first_clip
    assert ("clear_kind",) in sink.played[cue_at:first_clip]
    assert sink.tones("under") == []


def test_a_plain_announcement_and_a_catch_up_get_no_alert():
    sink = KindSink()
    speak(LINE, Source.CLI, sink)
    speak(LINE, Source.WATCHER, sink, priority=Priority.HIGH, kind="catchup")
    assert sink.tones("cue") == []


def test_the_alert_switch(monkeypatch):
    monkeypatch.setenv("MEDIA_EARCON_ALERT", "0")
    sink = KindSink()
    speak(LINE, Source.CLI, sink, alert=True)
    assert sink.tones("cue") == []


def test_a_reply_to_agent_mail_opens_with_the_whoosh():
    sink = KindSink()
    speak(LINE, Source.CLAUDE_CODE, sink, mail_from="Sam")
    assert sink.tones("cue") == [(earcons.path("mail").name, "")]
    # Still the agent's reply: Air under it as well.
    assert [u[0] for u in sink.tones("under")] == [earcons.path("reply-E").name]


def test_mail_gives_way_to_a_marked_ending_and_needs_an_agent():
    sink = KindSink()
    speak(LINE, Source.CLAUDE_CODE, sink, mail_from="Sam", sound="done")
    assert sink.tones("cue") == [(earcons.path("done").name, "")]
    sink = KindSink()
    speak(LINE, Source.CLI, sink, mail_from="Sam")
    assert sink.tones("cue") == []


# ---- the mail, as the hook sees it ----------------------------------------------------

def _transcript(path, *entries):
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
    return path


def _user(content, **kw):
    return {"type": "user", "message": {"role": "user", "content": content}, **kw}


def _assistant(text):
    return {"type": "assistant",
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


def test_the_turns_prompt_is_found_past_its_tool_calls(tmp_path):
    tp = _transcript(
        tmp_path / "t.jsonl",
        _user("an earlier question"), _assistant("an earlier answer"),
        _user("[From Sam] the build on red5 is green, tell David"),
        _assistant("checking"),
        _user([{"type": "tool_result", "tool_use_id": "x", "content": "ok"}]),
        _user([{"type": "text", "text": "<system-reminder>x</system-reminder>"}]),
        _user("meta line", isMeta=True),
        _assistant("The build is green."))
    prompt = H._turn_prompt(tp)
    assert prompt.startswith("[From Sam]")
    assert H.mail_from(prompt) == "Sam"


@pytest.mark.parametrize("prompt,sender", [
    ("[From Cece] hello", "Cece"),
    ("  [From Agent-media-92] reply: got it", "Agent-media-92"),
    ("[From the session host] The session host restarted", ""),
    ("what do you think of [From Sam] in the middle", ""),
    ("plain words from David", ""),
    ("", ""),
])
def test_mail_is_a_from_prefix_but_not_the_session_hosts_own(prompt, sender):
    assert H.mail_from(prompt) == sender


def test_the_stop_hook_marks_mail_and_the_folder(monkeypatch, tmp_path):
    tp = _transcript(tmp_path / "t.jsonl",
                     _user([{"type": "text", "text": "[From Gigi] the import finished"}]),
                     _assistant("Gigi says the import finished."))
    seen = []
    monkeypatch.setattr(H, "_play_detached", lambda ev: seen.append(ev))
    monkeypatch.setattr(H, "_source_place", lambda *a: {})
    monkeypatch.setattr(H, "voice_for_session", lambda *a: None)
    monkeypatch.setattr(H, "_session_name", lambda: "")
    import agent_media_core.intake.toast as toast
    monkeypatch.setattr(toast, "should_hold", lambda *a: False)
    H._handle_stop({"session_id": "s1", "transcript_path": str(tp),
                    "cwd": str(tmp_path),
                    "last_assistant_message": "Gigi says the import finished."})
    assert seen and seen[0].metadata["mail_from"] == "Gigi"
    assert seen[0].metadata["cwd"] == str(tmp_path)


# ---- the phone told the kind ------------------------------------------------------------

def test_play_under_tells_a_phone_the_kind_first(monkeypatch, tmp_path):
    from agent_media_core.sinks import speech
    monkeypatch.setenv("MEDIA_SPEECH_SOCKET_TESTPHONE", "tcp://127.0.0.1:1")
    monkeypatch.setenv("MEDIA_SPEECH_CLIP_LOCALDIR_TESTPHONE", "/phone/clips")
    sent = []
    monkeypatch.setattr(speech.ipc, "set_property",
                        lambda sock, name, value, **kw: sent.append((name, value)))
    monkeypatch.setattr(speech.SinkSpeech, "_prefer_url", lambda self, t: False)
    sink = speech.SinkSpeech.__new__(speech.SinkSpeech)
    assert sink.play_under(str(tmp_path / "earcon-people-v10.wav"),
                           Target("testphone"), loop=True, kind="people")
    assert sent == [(speech.TONE_KIND_PROPERTY, "people"),
                    (speech.UNDER_LOOP_PROPERTY, "1"),
                    (speech.UNDER_PROPERTY, "/phone/clips/earcon-people-v10.wav")]
    sent.clear()
    # Any other tone says none, so an old kind never lingers.
    sink.play_under(str(tmp_path / "earcon-think-v10.wav"), Target("testphone"), loop=True)
    assert sent[0] == (speech.TONE_KIND_PROPERTY, "")
    sent.clear()
    sink.clear_under(Target("testphone"))
    assert (speech.TONE_KIND_PROPERTY, "") in sent
    sent.clear()
    sink.clear_tone_kind(Target("testphone"))
    assert sent == [(speech.TONE_KIND_PROPERTY, "")]


def test_a_cue_tells_a_phone_its_kind_in_the_same_batch(monkeypatch, tmp_path):
    from agent_media_core.sinks import speech
    monkeypatch.setenv("MEDIA_SPEECH_SOCKET_TESTPHONE", "tcp://127.0.0.1:1")
    monkeypatch.setattr(speech.ipc, "get_properties",
                        lambda *a, **kw: {"idle-active": True, "pause": False})
    batches = []
    monkeypatch.setattr(speech.ipc, "command_batch",
                        lambda sock, cmds, **kw: batches.append(cmds))
    monkeypatch.setattr(speech.SinkSpeech, "_load_cmds",
                        lambda self, uris, target, gapless=False: [["loadfile", uris[0]]])
    sink = speech.SinkSpeech.__new__(speech.SinkSpeech)
    assert sink.play_cue("/clips/earcon-alert-v10.wav", Target("testphone"), kind="alert")
    assert batches[0][0] == ["set_property", speech.TONE_KIND_PROPERTY, "alert"]
    assert batches[0][1] == ["loadfile", "/clips/earcon-alert-v10.wav"]
    sink.play_cue("/clips/earcon-cut-v10.wav", Target("testphone"))
    assert batches[1][0] == ["set_property", speech.TONE_KIND_PROPERTY, ""]


def test_a_desk_cue_is_not_told_a_kind(monkeypatch, tmp_path):
    from agent_media_core.sinks import speech
    monkeypatch.setenv("MEDIA_SPEECH_SOCKET_TESTDESK", str(tmp_path / "s.sock"))
    monkeypatch.setattr(speech.ipc, "get_properties",
                        lambda *a, **kw: {"idle-active": True, "pause": False})
    batches = []
    monkeypatch.setattr(speech.ipc, "command_batch",
                        lambda sock, cmds, **kw: batches.append(cmds))
    monkeypatch.setattr(speech.SinkSpeech, "_load_cmds",
                        lambda self, uris, target, gapless=False: [["loadfile", uris[0]]])
    sink = speech.SinkSpeech.__new__(speech.SinkSpeech)
    sink.play_cue("/clips/earcon-alert-v10.wav", Target("testdesk"), kind="alert")
    assert all(c[0] != "set_property" or c[1] != speech.TONE_KIND_PROPERTY
               for c in batches[0])


def test_a_sink_without_kinds_still_gets_its_tone():
    class Old:
        def __init__(self): self.got = []
        def play_cue(self, uri, target): self.got.append(("cue", uri)); return True
        def play_under(self, uri, target, loop=False): self.got.append(("under", uri)); return True
    old = Old()
    assert earcons.play("alert", Target("local"), old)
    assert earcons.play_under("people", Target("local"), old, loop=True)
    assert [g[0] for g in old.got] == ["cue", "under"]
