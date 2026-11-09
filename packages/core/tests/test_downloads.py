"""agent_media_core.downloads: the registry of downloads under way and the
progress lines it reads (the app's progress marks, server-contract §6.9a
`downloads`). The output fed here was recorded from yt-dlp and rsync on a
local file; nothing is downloaded and no ssh is run.
"""

from __future__ import annotations

import io
import json
import sys
import time

import pytest

from agent_media_core import downloads

#: yt-dlp with downloads.PROGRESS_ARGS and `--print after_move:filename`
#: (stdout): one template line per update, then the file's name.
TEMPLATE_OUT = (
    "[amdl] 1024 3000000 NA clip\n"
    "[amdl] 1500000 3000000 NA clip\n"
    "[amdl] 3000000 3000000 NA clip\n"
    "clip [clip].mp3\n"
)
#: yt-dlp's own bar (a helper's stderr, e.g. the phone's play-local): rewritten with \r.
YTDLP_BAR = (
    "[generic] Extracting URL: file:///tmp/clip.mp3\n"
    "[download] Destination: clip.mp3\n"
    "\r[download]   0.1% of    2.86MiB at    1.29MiB/s ETA 00:02"
    "\r[download]  69.9% of    2.86MiB at    6.47MiB/s ETA 00:00"
    "\r[download] 100% of    2.86MiB in 00:00:00 at 7.68MiB/s   \n"
)
#: rsync --info=progress2 (stdout).
RSYNC_OUT = (
    "\r         32,768   1%    0.00kB/s    0:00:00  "
    "\r      3,000,000 100%  117.91MB/s    0:00:00 (xfr#1, to-chk=0/1)\n"
)


def _entry(vid: str, purpose: str) -> dict:
    return json.loads(downloads._path(vid, purpose).read_text())


@pytest.mark.parametrize("line,pct", [
    ("[amdl] 1500000 3000000 NA clip", 50.0),
    ("[amdl] 750 NA 1000 A title with spaces", 75.0),
    ("[amdl] NA NA NA clip", None),
    ("[download]  42.3% of ~  3.20MiB at  1.00MiB/s ETA 00:02 (frag 3/10)", 42.3),
    ("[download] 100% of    2.86MiB in 00:00:00 at 7.68MiB/s", 100.0),
    ("[download] Destination: clip.mp3", None),
    ("         32,768   1%    0.00kB/s    0:00:00  ", 1.0),
    ("      3,000,000 100%  117.91MB/s    0:00:00 (xfr#1, to-chk=0/1)", 100.0),
    ("clip [clip].mp3", None),
    ("/data/data/com.termux/files/home/.cache/music-offline/abc.mka", None),
])
def test_parse_pct(line, pct):
    assert downloads.parse_pct(line) == pct


def test_lines_split_on_carriage_returns_too():
    got = list(downloads.lines(io.BytesIO(YTDLP_BAR.encode())))
    assert [downloads.parse_pct(ln) for ln in got if downloads.parse_pct(ln) is not None] == [0.1, 69.9, 100.0]
    assert "".join(got) == YTDLP_BAR


def test_a_tracked_download_is_listed_then_gone():
    with downloads.track("dQw4w9WgXcQ", "podcast") as t:
        listed = downloads.active()
        assert listed == [{"id": "dQw4w9WgXcQ", "purpose": "podcast", "title": None,
                           "pct": None, "phase": "downloading"}]
        for ln in TEMPLATE_OUT.splitlines():
            t.feed(ln)
        e = _entry("dQw4w9WgXcQ", "podcast")
        # The template names it; the last write may wait a second, the percent never goes back.
        assert e["title"] == "clip" and t.pct == 100.0
        t.set_phase("copying")
        assert downloads.active()[0]["phase"] == "copying" and downloads.active()[0]["pct"] is None
        t.feed(RSYNC_OUT.split("\r")[2])
        assert downloads.active()[0]["pct"] == 100.0
    assert downloads.active() == []


def test_the_callers_context_names_the_purpose():
    """The radio's prefetch goes through the same phone fetch as a song put
    on: the caller's context says which."""
    with downloads.context("radio", "Next song"):
        with downloads.track("aaaaaaaaaaa") as t:
            assert (t.purpose, t.title) == ("radio", "Next song")
    with downloads.track("aaaaaaaaaaa", "podcast") as t:
        assert t.purpose == "podcast"
    with downloads.track("aaaaaaaaaaa") as t:
        assert t.purpose == "music"


def test_a_stale_entry_is_not_shown_and_an_old_one_is_deleted():
    t = downloads.Tracker("aaaaaaaaaaa", "music")
    downloads.Tracker("bbbbbbbbbbb", "music")
    p = downloads._path("aaaaaaaaaaa", "music")
    e = json.loads(p.read_text())
    e["updated_at"] = time.time() - downloads.STALE_S - 5
    p.write_text(json.dumps(e))
    assert [d["id"] for d in downloads.active()] == ["bbbbbbbbbbb"]
    assert p.exists()
    e["updated_at"] = time.time() - downloads.GONE_S - 5
    p.write_text(json.dumps(e))
    downloads.active()
    assert not p.exists()
    t.done()


def test_a_later_step_keeps_the_name():
    downloads.Tracker("aaaaaaaaaaa", "book", "A Talk")
    later = downloads.Tracker("aaaaaaaaaaa", "book")
    later.set_phase("copying")
    assert downloads.active()[0]["title"] == "A Talk"
    later.done()


def test_run_feeds_progress_and_keeps_the_rest():
    """`run` is subprocess.run(capture_output=True, text=True) read as it
    comes: progress lines move the entry and stay out of the output."""
    script = ("import sys,time\n"
              f"sys.stdout.write({TEMPLATE_OUT!r}); sys.stdout.flush()\n"
              f"sys.stderr.write({YTDLP_BAR!r})\n")
    seen = []
    with downloads.track("aaaaaaaaaaa", "music") as t:
        orig = t.feed
        t.feed = lambda ln: (seen.append(downloads.parse_pct(ln)), orig(ln))[1]  # type: ignore[method-assign]
        r = downloads.run([sys.executable, "-c", script], t, timeout=30)
    assert r.returncode == 0
    assert r.stdout.strip().splitlines()[-1] == "clip [clip].mp3"
    assert "[amdl]" not in r.stdout and "%" not in r.stderr
    assert "Destination: clip.mp3" in r.stderr
    assert 50.0 in seen and 69.9 in seen
    assert downloads.active() == []


def test_run_times_out_as_subprocess_does():
    with pytest.raises(downloads.subprocess.TimeoutExpired):
        downloads.run([sys.executable, "-c", "import time; time.sleep(30)"], None, timeout=0.5)


def test_the_filter_passes_other_lines_on(monkeypatch, capsys):
    """`python -m agent_media_core.downloads track`: a shell helper's pipe."""
    seen = []
    real = downloads.Tracker.feed

    def feed(self, ln):
        got = real(self, ln)
        if got:
            seen.append(_entry(self.vid, self.purpose)["pct"])
        return got
    monkeypatch.setattr(downloads.Tracker, "feed", feed)
    monkeypatch.setattr(downloads, "_MIN_WRITE_S", 0.0)
    monkeypatch.setattr(sys, "stdin", type("S", (), {"buffer": io.BytesIO(TEMPLATE_OUT.encode())})())
    assert downloads.main(["track", "--id", "dQw4w9WgXcQ", "--purpose", "book"]) == 0
    assert capsys.readouterr().out == "clip [clip].mp3\n"
    assert seen[-1] == 100.0
    assert downloads.active() == []


def test_the_filter_can_leave_the_entry_for_the_next_step(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", type("S", (), {"buffer": io.BytesIO(RSYNC_OUT.encode())})())
    assert downloads.main(["track", "--id", "dQw4w9WgXcQ", "--purpose", "book", "--phase", "copying", "--keep"]) == 0
    capsys.readouterr()
    assert downloads.active()[0]["phase"] == "copying"
    assert downloads.main(["done", "--id", "dQw4w9WgXcQ", "--purpose", "book"]) == 0
    assert downloads.active() == []


def test_run_says_what_the_download_is_to_its_child():
    """phone_run sends it on as the job's tag and priority (the phone's line)."""
    with downloads.track("aaaaaaaaaaa", "radio") as t:
        r = downloads.run([sys.executable, "-c", "import os; print(os.environ['AGENT_MEDIA_DOWNLOAD'])"], t, timeout=10)
    assert r.stdout.strip() == "radio:aaaaaaaaaaa:background"
    with downloads.context(priority="foreground"), downloads.track("bbbbbbbbbbb", "radio") as t:
        assert t.priority == "foreground"
    with downloads.track("ccccccccccc", "music") as t:
        assert t.priority == "foreground"


def test_cancel_goes_to_the_phones_line_only_where_downloads_go_that_way(monkeypatch):
    from agent_media_core import phone_run

    asked = []
    monkeypatch.setattr(phone_run, "cancel", lambda tag: asked.append(tag) or 1)
    monkeypatch.delenv("MEDIA_PHONE_JOBS", raising=False)
    downloads.cancel("radio:", wait=True)
    assert asked == []
    monkeypatch.setenv("MEDIA_PHONE_JOBS", "1")
    downloads.cancel("radio:", wait=True)
    assert asked == ["radio:"]


def test_the_radio_turned_off_cancels_its_downloads(monkeypatch):
    from agent_media_core import radio

    asked = []
    monkeypatch.setattr(downloads, "cancel", lambda tag, wait=False: asked.append(tag))
    radio.stop()
    assert asked == ["radio:"]
