"""mail_threads: a thread another agent's mail started carries `mail_from`
until someone other than an agent or the session host writes in it."""
import json

import pytest

from agent_media_server import mail_threads, sessions


def _user(text, **extra):
    return json.dumps({"type": "user", "message": {"role": "user", "content": text}, **extra}) + "\n"


def _reply(text):
    return json.dumps({"type": "assistant", "message": {"role": "assistant",
                                                        "content": [{"type": "text", "text": text}]}}) + "\n"


@pytest.fixture
def transcript(tmp_path, monkeypatch):
    mail_threads._SEEN.clear()
    files = {}

    def make(sid, *lines):
        p = tmp_path / f"{sid}.jsonl"
        p.write_text("".join(lines))
        files[sid] = str(p)
        return p

    monkeypatch.setattr(sessions, "_claude_files", lambda sid: [files[sid]] if sid in files else [])
    yield make
    mail_threads._SEEN.clear()


def test_mail_started_thread_is_marked(transcript):
    transcript("a", _user("<command-name>/x</command-name>"),
               _user("[From Cece] agent-mail #127 is for this session: claim it"),
               _reply("Claimed."),
               _user("[From the session host] You were stopped"),
               _user("[Request interrupted by user]"),
               _user("[From Sam] and another"))
    assert mail_threads.mail_from("a") == "Cece"


def test_davids_own_words_unmark_it_for_good(transcript):
    p = transcript("a", _user("[From Cece] agent-mail #1 is for this session"), _reply("ok"))
    assert mail_threads.mail_from("a") == "Cece"
    with open(p, "a") as fh:
        fh.write(_user("thanks, now do the other thing"))
    assert mail_threads.mail_from("a") is None
    with open(p, "a") as fh:
        fh.write(_user("[From Cece] more mail"))
    assert mail_threads.mail_from("a") is None


def test_ordinary_chat_and_host_notes_are_not_mail(transcript):
    transcript("a", _user("why is the mic beeping?"))
    transcript("b", _user("[From the session host] resume where you were"))
    assert mail_threads.mail_from("a") is None
    assert mail_threads.mail_from("b") is None


def test_half_written_line_waits(transcript):
    p = transcript("a")
    p.write_text(_user("[From Cece] mail")[:20])
    assert mail_threads.mail_from("a") is None
    p.write_text(_user("[From Cece] mail"))
    assert mail_threads.mail_from("a") == "Cece"


def test_rows(transcript):
    transcript("a", _user("[From Cece] mail"))
    rows = mail_threads.add_mail([{"session": "a"}, {"session": "a", "sealed": True},
                                  {"session": "a", "harness": "matrix"}, {"session": "zz"}])
    assert [r["mail_from"] for r in rows] == ["Cece", None, None, None]
