"""A reply's shared files, kept as the turn ends (§6.18).

David, 8 Nov 2026: a reply's file chip read "Could not open the file". The
agent had written its `Shared file:` line by hand, pointing into a temporary
worktree, and had removed the worktree seven seconds before the reply. The
server copies such a file into the upload root when it reads the thread
(agent_media_server/file_links.py), but by then there was nothing to copy.

So a synchronous Stop hook (`media-hook-shared-check`), separate from the
async speech hook, looks over the reply's `Shared file:` lines as the turn
ends:

- a file that is there but outside the upload root is copied in now
  (shared_files.copy_once, the same rules and the same record the server
  uses), so the chip still opens if the file is deleted later: the server
  points a line whose file is gone at its newest copy (`last_copy`);
- a file that is gone, with no copy made earlier, blocks the stop once with
  a reason naming it, so the agent shares it again (`media attach`) or says
  it is gone, in the same turn. Never twice in a row: a stop the hook
  already blocked (`stop_hook_active`) is let through.

Only `Shared file:` lines: a bare path the server links on its own is a
mention, not a promise. Never fails the turn; `MEDIA_SHARED_CHECK=0` turns
it off.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

_LINE = re.compile(r"^Shared file: (\S.*?)\s*$", re.MULTILINE)


def check(text: str) -> list[str]:
    """The `Shared file:` paths in `text` the app could not open, each one
    that can still be copied having been copied."""
    from .. import shared_files

    missing: list[str] = []
    for raw in dict.fromkeys(m.group(1) for m in _LINE.finditer(text or "")):
        p = Path(os.path.expanduser(raw))
        if p.is_file():
            if not shared_files.inside(p):
                real = shared_files.eligible(raw, any_kind=True)
                if real is not None:
                    shared_files.copy_once(real)
            continue
        if not p.exists() and shared_files.last_copy(raw) is not None:
            continue
        missing.append(raw)
    return missing


def reason(missing: list[str]) -> str:
    names = ", ".join(f"`{m}`" for m in missing)
    one = len(missing) == 1
    return (f"Your reply's `Shared file:` line{'' if one else 's'} name{'s' if one else ''} "
            f"{names}, which {'is' if one else 'are'} not there any more, so the app "
            "shows \"Could not open the file\". If the file can be found or made "
            "again, run `media attach <file>` and add the line it prints in a short "
            "follow-up; otherwise say in one line that the picture is gone. Always "
            "share with `media attach` before deleting a temporary folder.")


def main() -> int:
    try:
        if os.environ.get("MEDIA_SHARED_CHECK", "1") == "0":
            return 0
        payload = json.loads(sys.stdin.read() or "{}")
        if not isinstance(payload, dict) or payload.get("stop_hook_active"):
            return 0
        text = str(payload.get("last_assistant_message") or "")
        if "Shared file: " not in text:
            return 0
        missing = check(text)
    except Exception:  # noqa: BLE001 - a chip is never worth a turn
        return 0
    if missing:
        print(json.dumps({"decision": "block", "reason": reason(missing)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
