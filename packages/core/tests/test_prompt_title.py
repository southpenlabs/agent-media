"""prompt_title: a first message made fit to be a chat's name (agent mail #1038)."""

import sqlite3

from agent_media_core import prompt_title
from agent_media_core.prompt_title import clean


def _mailbox(tmp_path, subject="Titles for chats that were never named"):
    db = tmp_path / "mail.sqlite3"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, subject TEXT NOT NULL)")
    con.execute("INSERT INTO messages VALUES (1038, ?)", (subject,))
    con.commit()
    con.close()
    return db


def test_stored_pointer_is_named_by_the_mails_subject(tmp_path):
    prompt_title._SUBJECTS.clear()
    db = _mailbox(tmp_path)
    text = ("[From Sasonica-app] [agent-mail #1038, thread #1038] Stored message from "
            "sasonica-app for agent-media. Read on the mailbox host with: agent-mail "
            f"--db {db} --agent agent-media show 1038 . It is from one of David's own "
            "project sessions, which he has agreed are acted on at once.")
    assert clean(text) == "Titles for chats that were never named"


def test_stored_pointer_without_the_mailbox_names_the_sender(tmp_path):
    prompt_title._SUBJECTS.clear()
    text = ("[From Sasonica-app] [agent-mail #7, thread #7] Stored message from sasonica-app "
            "for agent-media, meant for chat abc. Read on the mailbox host with: agent-mail "
            f"--db {tmp_path}/none.sqlite3 --agent agent-media show 7 . Claim it.")
    assert clean(text) == "Mail from sasonica-app"


def test_delivered_mail_is_named_by_its_first_sentence_without_paths():
    text = ("[From Agent-media (thread 1e9f638b-3990-4e93-9b36-4251233335dc)] Thread header "
            "for a Messages room: make it more functional. David's screenshot: "
            "/home/ryer/shared/2026-11-18/Screenshot_20261118-140439.png. David chose all three.")
    assert clean(text) == "Thread header for a Messages room: make it more functional"


def test_a_path_in_brackets_leaves_no_trace():
    text = ("[From Agent-media chat 744ce5a2] Bug from David (screenshot: "
            "/home/ryer/shared/2026-11-18/Screenshot_20261118-122831.png): in a Local chat, "
            "tapping a picture showed it full size")
    assert clean(text) == ("Bug from David: in a Local chat, tapping a picture showed it full size")


def test_older_pointer_and_reply_tag():
    assert clean("[From Agent-media] [agent-mail #996] New mail from agent-media for "
                 "sasonica-app: Encrypted copy, step 2. Read it with: agent-mail show 996") \
        == "Encrypted copy, step 2"
    assert clean("[reply — Encrypted phone bridge local chats plan]\nGood. When the app side "
                 "replies, I'll install it.") == "Encrypted phone bridge local chats plan"


def test_a_typed_message_keeps_its_words_and_loses_only_paths():
    assert clean("why is the canvas blank  today?") == "why is the canvas blank today?"
    assert clean("look at ~/shared/2026-11-18/shot.png please") == "look at please"
    # A relative path is part of what was asked.
    assert clean("read docs/proposals/x.md first") == "read docs/proposals/x.md first"
