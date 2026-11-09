"""Downloads under way, and how far each has got: the app's progress marks.

David, 10 Nov 2026: "It would be nice to have a little progress indicator
when something is downloading". A YouTube download runs on the phone (the
personal-use add-on's youtube-downloads, the book channel's audiobook-fetch)
or a residential fetcher and is brought here, in whatever process asked for
it: a `media music play` the server ran, a detached helper, a thread of the
canvas. So the registry is files, one small JSON each under
``<state>/downloads/``, which `GET /music` reads (server-contract §6.9a
`downloads`).

One entry is one video id downloading for one `purpose`:

- ``music``    a song or link put on a music player, before it plays
- ``book``     a link put on the book channel, before it plays
- ``radio``    the station's next song, fetched ahead of its turn
- ``podcast``  a saved link of the Podcasts tab, kept on the server
- ``practice`` a practice's small video, kept on the server to save offline
- ``practice_sound`` a practice's sound, fetched ahead (shown nowhere)

``{"id", "purpose", "title", "pct", "phase", "updated_at"}``: `pct` 0-100,
or null while the size is not known (a route that only answers at the end,
the phone's jobs worker, says nothing until then); `phase` "downloading" or
"copying" (brought from the phone). An entry not touched for STALE_S is a
download that died without saying, and is not shown.

Writers use `track` (a context manager, removed on the way out however it
ends) and `run` (`subprocess.run(capture_output=True, text=True)` whose
output is read as it comes, each progress line moving the entry). A shell
helper pipes its yt-dlp through ``python -m agent_media_core.downloads
track --id <vid> --purpose <p>``, which passes every other line on.

The progress lines read (`parse_pct`): yt-dlp's own (``[download]  42.3% of
...``), the template this module hands it (PROGRESS_ARGS, bytes so far and
the total) and rsync's ``--info=progress2`` (``1,234,567  42%  1.2MB/s``),
split on newlines and carriage returns both.
"""

from __future__ import annotations

import codecs
import contextlib
import contextvars
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import IO, Callable, Iterator, Optional, Sequence

from ._paths import state_dir

PURPOSES = ("music", "book", "radio", "podcast", "practice", "practice_sound")
PHASES = ("downloading", "copying")
#: An entry not updated for this long is not shown (its writer died).
STALE_S = 60.0
#: ...and is deleted once this old.
GONE_S = 3600.0
#: Writes are kept to one a second unless the whole percent moves.
_MIN_WRITE_S = 1.0

#: yt-dlp arguments that print a progress line this module reads, one per
#: line, to stdout (where `--print` sends the file's name too): bytes so
#: far, the total and the total's estimate ("NA" for one not known), then
#: the video's title, which names an entry its writer could not.
PROGRESS_TEMPLATE = ("download:[amdl] %(progress.downloaded_bytes)s "
                     "%(progress.total_bytes)s %(progress.total_bytes_estimate)s %(info.title)s")
PROGRESS_ARGS = ("--progress", "--newline", "--progress-template", PROGRESS_TEMPLATE)

_TEMPLATE_RE = re.compile(r"^\[amdl\]\s+(\S+)\s+(\S+)\s+(\S+)(?:\s+(.*\S))?\s*$")
_YTDLP_RE = re.compile(r"^\[download\]\s+(\d{1,3}(?:\.\d+)?)%")
_RSYNC_RE = re.compile(r"^\s*[\d,.]+[KMGT]?\s+(\d{1,3})%\s")

#: What the caller is downloading for, when the writer does not say
#: (`context`): the radio's prefetch goes through the same phone fetch as a
#: song put on, and only the caller knows which it is.
_CONTEXT: contextvars.ContextVar[dict] = contextvars.ContextVar("agent_media_downloads", default={})


def folder() -> Path:
    return state_dir() / "downloads"


def _safe(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", s)[:64]


def _path(vid: str, purpose: str) -> Path:
    return folder() / f"{_safe(purpose)}-{_safe(vid)}.json"


def parse_pct(line: str) -> Optional[float]:
    """The percent a progress line says, 0-100; None for any other line."""
    s = line.strip()
    m = _TEMPLATE_RE.match(s)
    if m:
        def num(x: str) -> Optional[float]:
            try:
                v = float(x)
            except ValueError:
                return None
            return v if v > 0 else None
        done, total = num(m.group(1)) or 0.0, num(m.group(2)) or num(m.group(3))
        if not total:
            return None
        return max(0.0, min(100.0, 100.0 * done / total))
    m = _YTDLP_RE.match(s) or _RSYNC_RE.match(line)
    if m:
        return max(0.0, min(100.0, float(m.group(1))))
    return None


def is_progress(line: str) -> bool:
    """A line that is only progress (the template's or yt-dlp's): not passed on."""
    s = line.strip()
    return bool(_TEMPLATE_RE.match(s) or _YTDLP_RE.match(s))


@contextlib.contextmanager
def context(purpose: Optional[str] = None, title: Optional[str] = None) -> Iterator[None]:
    """Downloads started inside this block are for `purpose`, named `title`,
    unless their writer says otherwise (the radio's prefetch)."""
    cur = dict(_CONTEXT.get())
    if purpose:
        cur["purpose"] = purpose
    if title:
        cur["title"] = title
    tok = _CONTEXT.set(cur)
    try:
        yield
    finally:
        _CONTEXT.reset(tok)


class Tracker:
    """One download's entry: `feed` it output lines, `phase` it to copying."""

    def __init__(self, vid: str, purpose: str, title: Optional[str] = None) -> None:
        self.vid, self.purpose, self.title = vid, purpose, title
        if not title:
            # A later step of the same download (the copy after it) keeps its name.
            with contextlib.suppress(OSError, ValueError, AttributeError):
                self.title = json.loads(_path(vid, purpose).read_text()).get("title") or None
        self.pct: Optional[float] = None
        self._phase = "downloading"
        self._wrote = 0.0
        self._wrote_pct: Optional[int] = None
        self._lock = threading.Lock()
        self.write(force=True)

    def write(self, force: bool = False) -> None:
        now = time.time()
        whole = None if self.pct is None else int(self.pct)
        if not force and whole == self._wrote_pct and now - self._wrote < _MIN_WRITE_S:
            return
        entry = {"id": self.vid, "purpose": self.purpose, "title": self.title,
                 "pct": None if self.pct is None else round(self.pct, 1),
                 "phase": self._phase, "updated_at": now}
        try:
            folder().mkdir(parents=True, exist_ok=True)
            p = _path(self.vid, self.purpose)
            tmp = p.with_name(f".{p.name}.{os.getpid()}.{threading.get_ident()}")
            tmp.write_text(json.dumps(entry))
            tmp.replace(p)
        except OSError:
            return  # a progress mark is never worth failing a download for
        self._wrote, self._wrote_pct = now, whole

    def feed(self, line: str) -> bool:
        """Read one line of output; True when it was a progress line."""
        pct = parse_pct(line)
        if pct is None:
            return False
        with self._lock:
            if not self.title:
                m = _TEMPLATE_RE.match(line.strip())
                if m and m.group(4) and m.group(4) != "NA":
                    self.title = m.group(4)[:200]
            self.pct = pct
            self.write()
        return True

    def set_phase(self, phase: str) -> None:
        """Downloading done, now copying (or back): the percent starts again."""
        with self._lock:
            self._phase, self.pct = phase, None
            self.write(force=True)

    def done(self) -> None:
        with contextlib.suppress(OSError):
            _path(self.vid, self.purpose).unlink()


@contextlib.contextmanager
def track(vid: str, purpose: Optional[str] = None, title: Optional[str] = None) -> Iterator[Tracker]:
    """An entry for `vid` while the block runs, removed when it ends.
    `purpose` and `title` default to the caller's `context`, then "music"."""
    ctx = _CONTEXT.get()
    t = Tracker(vid, ctx.get("purpose") or purpose or "music", title or ctx.get("title"))
    try:
        yield t
    finally:
        t.done()


def finish(vid: str, purpose: str) -> None:
    with contextlib.suppress(OSError):
        _path(vid, purpose).unlink()


def active() -> list[dict]:
    """The downloads under way, newest first: ``[{id, purpose, title, pct, phase}]``."""
    d = folder()
    if not d.is_dir():
        return []
    now = time.time()
    out = []
    for p in d.glob("*.json"):
        try:
            e = json.loads(p.read_text())
            at = float(e.get("updated_at") or 0)
        except (OSError, ValueError, TypeError, AttributeError):
            continue
        if now - at > GONE_S:
            with contextlib.suppress(OSError):
                p.unlink()
            continue
        if now - at > STALE_S or not e.get("id"):
            continue
        pct = e.get("pct")
        out.append({"id": str(e["id"]), "purpose": e.get("purpose") or "music",
                    "title": e.get("title") or None,
                    "pct": float(pct) if isinstance(pct, (int, float)) else None,
                    "phase": e.get("phase") if e.get("phase") in PHASES else "downloading",
                    "_at": at})
    out.sort(key=lambda e: -e["_at"])
    for e in out:
        del e["_at"]
    return out


def lines(stream: IO, peek: Optional[Callable[[str], object]] = None) -> Iterator[str]:
    """`stream`'s lines, split on newlines and carriage returns both (a
    progress bar rewrites its line with ``\\r``), each with its ending.
    rsync and yt-dlp's bar start a line with ``\\r`` and end it only when the
    next comes, so the unended rest is handed to `peek` as it arrives."""
    buf = ""
    dec = codecs.getincrementaldecoder("utf-8")("replace")
    while True:
        chunk = stream.read1(4096) if hasattr(stream, "read1") else stream.read(1)
        if isinstance(chunk, bytes):
            if not chunk:
                buf += dec.decode(b"", final=True)
                break
            chunk = dec.decode(chunk)
        elif not chunk:
            break
        buf += chunk
        while True:
            i = min((j for j in (buf.find("\n"), buf.find("\r")) if j >= 0), default=-1)
            if i < 0:
                break
            yield buf[:i + 1]
            buf = buf[i + 1:]
        if buf and peek is not None:
            peek(buf)
    if buf:
        yield buf


def run(argv: Sequence[str], tracker: Optional[Tracker], timeout: Optional[float] = None,
        keep_progress: bool = False, **kw) -> subprocess.CompletedProcess:
    """`subprocess.run(argv, capture_output=True, text=True, timeout=…)`, its
    stdout and stderr read as they come and each progress line fed to
    `tracker`. Progress lines are left out of the output unless
    `keep_progress`. Raises TimeoutExpired (the process killed) and OSError
    as `subprocess.run` does."""
    proc = subprocess.Popen(list(argv), stdin=kw.pop("stdin", subprocess.DEVNULL),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kw)
    got: dict[str, list[str]] = {"out": [], "err": []}

    def pump(stream, key: str) -> None:
        for ln in lines(stream, tracker.feed if tracker is not None else None):
            fed = tracker.feed(ln) if tracker is not None else False
            if fed and not keep_progress:
                continue
            got[key].append(ln.replace("\r", "\n") if ln.endswith("\r") else ln)

    readers = [threading.Thread(target=pump, args=(proc.stdout, "out"), daemon=True),
               threading.Thread(target=pump, args=(proc.stderr, "err"), daemon=True)]
    for r in readers:
        r.start()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        for r in readers:
            r.join(timeout=2)
        raise subprocess.TimeoutExpired(list(argv), timeout or 0,
                                        "".join(got["out"]), "".join(got["err"])) from None
    for r in readers:
        r.join(timeout=5)
    return subprocess.CompletedProcess(list(argv), proc.returncode,
                                       "".join(got["out"]), "".join(got["err"]))


def _filter(stdin: IO, stdout: IO[str], tracker: Tracker, write: Callable[[str], None] | None = None) -> None:
    out = write or stdout.write
    for ln in lines(stdin, tracker.feed):
        if tracker.feed(ln) and is_progress(ln):
            continue
        out(ln if not ln.endswith("\r") else ln[:-1] + "\n")
        stdout.flush()


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="python -m agent_media_core.downloads",
                                 description="Downloads under way (the app's progress marks).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("track", help="read a download's output on stdin, mark its progress, pass the rest on")
    t.add_argument("--id", required=True)
    t.add_argument("--purpose", default="music", choices=PURPOSES)
    t.add_argument("--title", default=None)
    t.add_argument("--phase", default="downloading", choices=PHASES)
    t.add_argument("--keep", action="store_true", help="leave the entry when the input ends (a later step goes on)")
    d = sub.add_parser("done", help="remove a download's entry")
    d.add_argument("--id", required=True)
    d.add_argument("--purpose", default="music", choices=PURPOSES)
    sub.add_parser("list", help="the downloads under way, as JSON")
    a = ap.parse_args(argv)
    if a.cmd == "list":
        print(json.dumps(active(), indent=1))
        return 0
    if a.cmd == "done":
        finish(a.id, a.purpose)
        return 0
    tr = Tracker(a.id, a.purpose, a.title)
    if a.phase != "downloading":
        tr.set_phase(a.phase)
    try:
        _filter(sys.stdin.buffer, sys.stdout, tr)
    finally:
        if not a.keep:
            tr.done()
    return 0


if __name__ == "__main__":
    sys.exit(main())
