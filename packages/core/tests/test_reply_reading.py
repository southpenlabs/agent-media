"""submit.reply_reading: a reply holds the playback token, read from
/proc/locks without taking it (screens.now, David, 28 Nov 2026)."""

import fcntl
import os
import sys

import pytest

from agent_media_core.intake import submit

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"),
                                reason="/proc/locks is Linux's")


def test_held_token_reads_as_reading(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    path = submit._speech_lock_path()
    path.parent.mkdir(parents=True)
    path.touch()
    assert submit.reply_reading() is False
    fd = os.open(path, os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert submit.reply_reading() is True
        fcntl.flock(fd, fcntl.LOCK_UN)
        assert submit.reply_reading() is False
    finally:
        os.close(fd)


def test_no_token_file_is_not_reading(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    assert submit.reply_reading() is False
