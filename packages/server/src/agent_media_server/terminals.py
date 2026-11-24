"""A terminal the server holds itself: install and sign-in without tmux.

Step 7 of docs/proposals/2026-11-15-other-agents-and-tmux-optional.md. The
harness setup routes (`/harnesses/run`, `/screen`, `/keys`, `/close`) used
to open a background tmux window and read it back; that needs a tmux client
on the host, and when none could be had the install was refused ("could not
get a client onto tmux session …", sasonica-app mail #1303). Now the server
runs the command itself:

* an install (or anything else that only prints) is a plain subprocess, its
  stdout and stderr read line by line as they come, a `\\r` progress line
  overwriting itself, nothing drawn;
* a sign-in, which asks questions and often draws a menu, runs in a pty
  (`pty.openpty` on Linux and Mac) whose output is fed through pyte, a
  terminal emulator, so the screen read back is what a terminal would show
  rather than a pile of cursor moves. Without a pty (Windows) it runs piped.

Each is named by an id (`t` and eight hex digits) where tmux gave `%N`, and
the routes take either. A terminal lives in this process: it is gone after
a restart of the server, and the screen route then answers 410 as it did
for a tmux window that went away. A finished one is kept LINGER_S to be
read, as the tmux window was, and keys reach the program only while it
runs: this is never a remote shell.

`signin(lines)` finds the URL and device code a sign-in prints, so the app
can show both with an Open button instead of the terminal.
"""

from __future__ import annotations

import os
import re
import secrets
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field

from agent_media_core import failures

COLS, ROWS = 80, 30

#: How many lines a piped command keeps (a terminal keeps its screen).
KEEP = 500

#: How long a finished terminal is kept to be read.
LINGER_S = 86400

#: Named keys (harnesses.KEYS) as the bytes a terminal sends for them.
KEY_BYTES = {
    "Enter": b"\r", "Escape": b"\x1b", "Space": b" ", "Tab": b"\t", "BSpace": b"\x7f",
    "Up": b"\x1b[A", "Down": b"\x1b[B", "Right": b"\x1b[C", "Left": b"\x1b[D",
    "C-c": b"\x03", "C-d": b"\x04",
}

ID = re.compile(r"t[0-9a-f]{8}")

_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]")


@dataclass
class Terminal:
    id: str
    argv: list[str]
    proc: subprocess.Popen
    tty: bool
    fd: int = -1                       # the pty's master side, when tty
    exit: int | None = None
    ended_at: float | None = None
    _screen: object = None             # pyte.Screen, when tty and pyte is here
    _stream: object = None
    _lines: list[str] = field(default_factory=lambda: [""])
    _lock: threading.Lock = field(default_factory=threading.Lock)

    # --- reading ------------------------------------------------------------

    def _feed(self, data: bytes) -> None:
        with self._lock:
            if self._stream is not None:
                try:
                    self._stream.feed(data)      # type: ignore[attr-defined]
                    return
                except Exception as e:  # noqa: BLE001 — a bad sequence must not end the reader
                    failures.swallowed(e)
            text = _ANSI.sub("", data.decode("utf-8", "replace"))
            for i, part in enumerate(text.replace("\r\n", "\n").split("\n")):
                if i:
                    self._lines.append("")
                if "\r" in part:
                    # A progress line drawn over itself: what is left after
                    # the last carriage return is what a terminal shows.
                    self._lines[-1] = part.rsplit("\r", 1)[1]
                else:
                    self._lines[-1] += part
            if len(self._lines) > KEEP:
                del self._lines[:-KEEP]

    def lines(self) -> list[str]:
        with self._lock:
            if self._screen is not None:
                rows = [r.rstrip() for r in self._screen.display]  # type: ignore[attr-defined]
            else:
                rows = [r.rstrip() for r in self._lines]
        if self.exit is not None:
            rows = _trim(rows) + ["", f"[finished: {self.exit}]"]
        return _trim(rows)

    @property
    def done(self) -> bool:
        return self.exit is not None

    # --- typing -------------------------------------------------------------

    def send(self, data: bytes) -> bool:
        """Bytes to the program while it runs; False once it has ended."""
        if self.exit is not None:
            return False
        try:
            if self.tty:
                os.write(self.fd, data)
            elif self.proc.stdin:
                self.proc.stdin.write(data.replace(b"\r", b"\n"))
                self.proc.stdin.flush()
            return True
        except OSError as e:
            failures.swallowed(e)
            return False

    def kill(self) -> None:
        if self.proc.poll() is None:
            try:
                os.killpg(self.proc.pid, signal.SIGTERM)
            except (OSError, AttributeError):
                self.proc.terminate()
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(self.proc.pid, signal.SIGKILL)
                except (OSError, AttributeError):
                    self.proc.kill()


def _trim(rows: list[str]) -> list[str]:
    while rows and not rows[-1]:
        rows = rows[:-1]
    return rows


_HELD: dict[str, Terminal] = {}
_HELD_LOCK = threading.Lock()


def _reap(now: float | None = None) -> None:
    now = time.time() if now is None else now
    with _HELD_LOCK:
        for tid, t in list(_HELD.items()):
            if t.ended_at is not None and now - t.ended_at > LINGER_S:
                del _HELD[tid]


def _pyte_screen():
    try:
        import pyte
    except ImportError:
        return None, None
    screen = pyte.Screen(COLS, ROWS)
    return screen, pyte.ByteStream(screen)


def open_terminal(argv: list[str], *, env: dict[str, str], cwd: str,
                  tty: bool = True) -> Terminal:
    """Start `argv` held by this process; OSError when it cannot start.

    `tty` runs it in a pty (a sign-in that draws and asks); without, it is
    piped (an install, which only prints). No pty on this platform: piped.
    """
    _reap()
    tty = tty and os.name == "posix"
    env = {**env, "TERM": "xterm-256color", "COLUMNS": str(COLS), "LINES": str(ROWS)}
    tid = "t" + secrets.token_hex(4)
    if tty:
        import fcntl
        import pty
        import struct
        import termios

        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, COLS, 0, 0))
        try:
            proc = subprocess.Popen(argv, stdin=slave, stdout=slave, stderr=slave, cwd=cwd,
                                    env=env, start_new_session=True, close_fds=True)
        except OSError:
            os.close(master)
            raise
        finally:
            os.close(slave)
        screen, stream = _pyte_screen()
        term = Terminal(tid, argv, proc, True, fd=master, _screen=screen, _stream=stream)
    else:
        proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, cwd=cwd, env=env,
                                start_new_session=(os.name == "posix"))
        term = Terminal(tid, argv, proc, False)
    with _HELD_LOCK:
        _HELD[tid] = term
    threading.Thread(target=_pump, args=(term,), daemon=True, name=f"terminal-{tid}").start()
    return term


def _pump(term: Terminal) -> None:
    """Read the program's output until it ends, then note how it ended."""
    try:
        while True:
            try:
                if term.tty:
                    data = os.read(term.fd, 65536)
                else:
                    assert term.proc.stdout is not None
                    data = term.proc.stdout.read1(65536)  # type: ignore[attr-defined]
            except OSError:
                data = b""                       # EIO: the pty's last writer has gone
            if not data:
                break
            term._feed(data)
    finally:
        code = term.proc.wait()
        if term.tty:
            try:
                os.close(term.fd)
            except OSError:
                pass
        term.exit = code
        term.ended_at = time.time()


def get(tid: str) -> Terminal | None:
    if not ID.fullmatch(tid or ""):
        return None
    with _HELD_LOCK:
        return _HELD.get(tid)


def close(tid: str) -> None:
    with _HELD_LOCK:
        term = _HELD.pop(tid, None)
    if term is not None:
        term.kill()


# --- what a sign-in asks for -------------------------------------------------------

_URL = re.compile(r"https://[^\s<>\"'`)\]]+")
#: A device code: groups of letters and digits joined by dashes, each at
#: least four long (ABCD-EFGH, ABCD-12345), and not a date or a version.
_CODE = re.compile(r"(?<![\w/.-])([A-Z0-9]{4,8}(?:-[A-Z0-9]{4,8})+)(?![\w/.-])")
_PASTE = re.compile(r"paste|authori[sz]ation code|enter (?:the |your )?code", re.I)
_AUTHY = re.compile(r"device|oauth|auth|login|signin|sign-in|activate|code|consent", re.I)


def _unwrap(lines: list[str], cols: int = COLS) -> str:
    """The lines as text, a row filled exactly to the edge joined to the next: a
    long sign-in URL wraps at the terminal's width and must be read whole."""
    out: list[str] = []
    for ln in lines:
        if out and len(out[-1]) == cols and ln[:1] not in ("", " "):
            out[-1] += ln
        else:
            out.append(ln)
    return "\n".join(out)


def signin(lines: list[str]) -> dict | None:
    """`{"url", "code", "paste"}` when the screen shows a sign-in URL, else None.

    `code` is a device code shown beside it (codex's and grok's
    `--device-auth`): typed on the page the URL opens, nothing comes back.
    Without one, `paste` says whether the program asks for a code from that
    page to be typed back (Claude's, Gemini's), which the app sends with
    `/harnesses/keys`.
    """
    text = _unwrap([ln.rstrip() for ln in lines])
    urls = _URL.findall(text)
    if not urls:
        return None
    url = next((u for u in reversed(urls) if _AUTHY.search(u)), urls[-1]).rstrip(".,;:")
    rest = _URL.sub(" ", text)
    codes = [c for c in _CODE.findall(rest) if any(ch.isalpha() for ch in c) or len(c) >= 9]
    code = codes[-1] if codes else ""
    return {"url": url, "code": code, "paste": not code and bool(_PASTE.search(rest))}
