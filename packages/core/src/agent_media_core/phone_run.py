"""Run a command in the phone's Termux through its worker, as `ssh p8a cmd` would.

    python -m agent_media_core.phone_run '<command>'

What `music_local.phone_argv` hands its callers when `MEDIA_PHONE_JOBS=1`
(roadmap item 15): the command goes to the canvas on this host
(`POST /jobs/run`, the host's own token), which gives it to the phone's
Termux worker over the stream the worker holds open (server phone_jobs.py),
and the answer comes back here. Its stdout and stderr are printed and its
exit code is this one's, so a caller reading `ssh`'s output reads this the
same. 255 is ssh's own "could not reach it", and is what this says when no
worker is connected or the canvas cannot be reached.

stdin is not carried (see phone_jobs).

While the command runs, the progress lines the phone's worker sees (a
download's yt-dlp bar) come back as they go and are printed to stderr, one
a line, as ssh would have shown them: the caller's progress mark
(agent_media_core.downloads) reads them there (David, 10 Nov 2026: "let's
change that helper to get the percentage"). A canvas or worker from before
answers only at the end, as it always did.

A download says what it is: agent_media_core.downloads.run hands its child
`AGENT_MEDIA_DOWNLOAD=<purpose>:<video id>:<priority>`, sent on as the job's
`tag` and `priority`, so the server runs one or two at a time, a play ahead
of the radio's prefetch, and `cancel(tag)` can stop the ones no longer
wanted (phone_jobs; David, 10 Nov 2026).
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request


def _token() -> str:
    """The host's own key (host_token)."""
    from . import host_token
    return host_token.read()


def _base() -> str:
    raw = (os.environ.get("MEDIA_PHONE_JOBS_URL") or os.environ.get("MEDIA_VISUAL_URL")
           or "http://127.0.0.1:8781")
    return raw.replace(",", " ").split()[0].rstrip("/")


def _download() -> dict:
    """The tag and priority of the download this command is, from
    AGENT_MEDIA_DOWNLOAD (downloads.run); {} for any other command."""
    raw = os.environ.get("AGENT_MEDIA_DOWNLOAD", "")
    parts = raw.split(":")
    if len(parts) != 3 or not parts[0] or not parts[1]:
        return {}
    return {"tag": f"{parts[0]}:{parts[1]}", "priority": parts[2]}


def cancel(tag: str) -> int:
    """Stop the phone's downloads tagged `tag` (`"radio:<id>"`, or `"radio:"`
    for every one of the radio's): `POST /jobs/cancel`. How many; 0 when the
    canvas cannot be reached or does not know the route (a server from
    before), and nothing is cancelled then."""
    req = urllib.request.Request(
        _base() + "/jobs/cancel", data=json.dumps({"tag": tag}).encode(), method="POST",
        headers={"Content-Type": "application/json", "X-Auth-Token": _token()})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return int(json.loads(r.read() or b"{}").get("cancelled") or 0)
    except (OSError, ValueError, TypeError):
        return 0


def run(cmd: str, timeout: float | None = None, on_progress=None) -> tuple[int, str, str]:
    """`(rc, out, err)` from the phone; `on_progress(line)` for each progress
    line the worker sends while it runs."""
    if timeout is None:
        try:
            timeout = float(os.environ.get("MEDIA_PHONE_JOBS_TIMEOUT") or 600)
        except ValueError:
            timeout = 600.0
    req = urllib.request.Request(
        _base() + "/jobs/run",
        data=json.dumps({"cmd": cmd, "timeout": timeout, "stream": True, **_download()}).encode(),
        method="POST",
        headers={"Content-Type": "application/json", "X-Auth-Token": _token()})
    try:
        with urllib.request.urlopen(req, timeout=timeout + 15) as r:
            if (r.headers.get("Content-Type") or "").startswith("application/x-ndjson"):
                got = {}
                for raw in r:
                    if not raw.strip():
                        continue
                    frame = json.loads(raw)
                    if isinstance(frame, dict) and "progress" in frame and "rc" not in frame:
                        if on_progress is not None:
                            on_progress(str(frame["progress"]))
                    else:
                        got = frame
            else:
                got = json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return 255, "", f"phone_run: the canvas said {e.code}"
    except (OSError, ValueError) as e:
        return 255, "", f"phone_run: {e}"
    try:
        return int(got.get("rc", 255)), str(got.get("out") or ""), str(got.get("err") or "")
    except (TypeError, ValueError):
        return 255, "", "phone_run: a malformed answer"


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not args:
        print("usage: python -m agent_media_core.phone_run '<command>'", file=sys.stderr)
        return 2
    def said(line: str) -> None:
        sys.stderr.write(line + "\n")
        sys.stderr.flush()
    rc, out, err = run(" ".join(args), on_progress=said)
    if out:
        sys.stdout.write(out)
    if err:
        sys.stderr.write(err if err.endswith("\n") else err + "\n")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
