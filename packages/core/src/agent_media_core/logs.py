"""One way to set up logging, for every agent-media program
(docs/proposals/2026-11-19-design-principles.md, "One logging setup").

Each program used to call logging.basicConfig with its own level and
format, and the API server and others not at all, so their warnings went
out bare through Python's last-resort handler and their info was dropped.
Now each entrypoint's main() calls setup() first:

    from agent_media_core import logs
    logs.setup()                      # INFO, "<logger>: <message>"
    logs.setup(fmt="%(message)s")     # a program whose lines were bare

MEDIA_LOG_LEVEL (DEBUG, INFO, WARNING …) overrides the level for every
program at once, e.g. to see failures.swallowed's later occurrences. Lines
go to stderr, which systemd and runit keep with their own timestamps.
"""

from __future__ import annotations

import logging
import os
import sys

DEFAULT_FORMAT = "%(name)s: %(message)s"


def level(default: int = logging.INFO) -> int:
    """MEDIA_LOG_LEVEL as a logging level, else `default`."""
    raw = (os.environ.get("MEDIA_LOG_LEVEL") or "").strip().upper()
    if not raw:
        return default
    if raw.isdigit():
        return int(raw)
    got = logging.getLevelName(raw)
    return got if isinstance(got, int) else default


def setup(default_level: int = logging.INFO, fmt: str = DEFAULT_FORMAT,
          stream=None) -> None:
    """Configure the root logger once (a second call changes nothing, as
    basicConfig did), at MEDIA_LOG_LEVEL or `default_level`."""
    logging.basicConfig(level=level(default_level), format=fmt,
                        stream=stream if stream is not None else sys.stderr)
