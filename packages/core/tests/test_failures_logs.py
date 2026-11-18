"""failures.swallowed and logs.setup (docs/proposals/2026-11-19-design-principles.md §2, §3)."""

from __future__ import annotations

import logging

from agent_media_core import failures, logs


def _drop(e):
    failures.swallowed(e)


def test_the_first_is_a_warning_naming_the_place_later_ones_debug(caplog):
    failures.reset()
    caplog.set_level(logging.DEBUG, logger="agent-media.failures")
    for _ in range(3):
        _drop(ValueError("bad row"))
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    msg = warnings[0].getMessage()
    assert "test_failures_logs._drop:" in msg and "ValueError: bad row" in msg
    assert sum(1 for r in caplog.records if r.levelno == logging.DEBUG) == 3
    (key, n), = failures.counts().items()
    assert key.endswith(" ValueError") and n == 3


def test_each_kind_and_place_is_its_own(caplog):
    failures.reset()
    caplog.set_level(logging.WARNING, logger="agent-media.failures")
    _drop(ValueError("a"))
    _drop(KeyError("b"))
    failures.swallowed(OSError("c"), where="here")
    assert len(caplog.records) == 3
    assert "here OSError" in failures.counts()


def test_it_never_raises():
    class Bad(Exception):
        def __str__(self):
            raise RuntimeError("no words")
    failures.swallowed(Bad())


def test_nothing_new_is_printed_without_a_logging_setup(capsys):
    """A short program (the CLI, a hook) sets up no logging: the NullHandler
    keeps Python's last-resort handler from printing these."""
    failures.reset()
    root = logging.getLogger()
    saved = root.handlers[:]
    root.handlers = []
    try:
        _drop(ValueError("quiet"))
    finally:
        root.handlers = saved
    assert capsys.readouterr().err == ""


def test_log_level_from_the_environment(monkeypatch):
    monkeypatch.delenv("MEDIA_LOG_LEVEL", raising=False)
    assert logs.level() == logging.INFO
    monkeypatch.setenv("MEDIA_LOG_LEVEL", "debug")
    assert logs.level() == logging.DEBUG
    monkeypatch.setenv("MEDIA_LOG_LEVEL", "10")
    assert logs.level() == 10
    monkeypatch.setenv("MEDIA_LOG_LEVEL", "loud")
    assert logs.level(logging.WARNING) == logging.WARNING
