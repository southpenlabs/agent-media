"""A library book in the app's own book player (org_listen.listen_book,
sweep 2026-11-27 item 1): streamed by a signed /books/library/ link, its
keys and place answered as a note's are, its speed its own.

The player is the stub test_org_listen's `book` fixture gives (org_listen
._read and _send), so nothing reaches a phone; the library is a temporary
folder, and the catalogue is stubbed, so nothing is probed."""

from __future__ import annotations

import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from agent_media_core import book_files, book_library, music_recent
from agent_media_server import music, org_listen

from test_org import _call, server, tree  # noqa: F401  (fixtures)
from test_org_listen import book  # noqa: F401  (fixture)

PUBLIC = "https://example.test"


@pytest.fixture()
def lib(book, monkeypatch, tmp_path):  # noqa: F811
    root = tmp_path / "audiobooks"
    (root / "Series").mkdir(parents=True)
    f = root / "Series" / "Hounded Book 1.m4b"
    f.write_bytes(b"\0" * 4096)
    monkeypatch.setenv("MEDIA_AUDIOBOOK_ABS_DIR", str(root))
    monkeypatch.setenv("MEDIA_PUBLIC_URL", PUBLIC)
    monkeypatch.setenv("MEDIA_LISTEN_ENDPOINT", "tcp://127.0.0.1:16627")
    monkeypatch.setattr(book_library, "by_name", lambda: {
        f.name: {"title": "Hounded", "duration": 600.0, "path": str(f)}})
    org_listen._TITLES.clear()
    sent: list = []
    lasts: list = []
    book.set_book_last = lambda uri, title=None: lasts.append((uri, title))

    def send(cmds):
        sent.append(cmds)
        for c in cmds:
            if c[0] == "loadfile":
                book.np = {"idle": False, "uri": c[1], "position_ms": 0,
                           "duration_ms": 600000, "paused": False, "speed": 1.0}
            elif c[0] == "seek":
                book.np["position_ms"] = int(float(c[1]) * 1000)
            elif c[0] == "stop":
                book.np = {"idle": True}
            elif c[1] == "pause":
                book.np["paused"] = c[2]
            elif c[1] in ("speed", "volume"):
                book.np[c[1]] = c[2]
    monkeypatch.setattr(org_listen, "_send", send)
    savers: list = []
    monkeypatch.setattr(org_listen, "_start_saver", lambda: savers.append(1))
    book.sent, book.lasts, book.savers, book.file = sent, lasts, savers, f
    return book


def test_a_library_book_plays_in_the_apps_player_from_its_place(lib):
    lib.resume_pos[str(lib.file)] = 120000
    ok, got = org_listen.listen_book(str(lib.file), gate=False)
    assert ok and got["state"] == "playing"
    load = lib.sent[0]
    url = load[0][1]
    assert load[0][0] == "loadfile" and url.startswith(f"{PUBLIC}/books/library/Series/Hounded%20Book%201.m4b?sig=")
    assert load[1] == ["set_property", "force-media-title", "Hounded"]
    assert ["seek", 120.0, "absolute"] in load
    assert lib.calls == []                          # not the desk's book channel
    assert lib.lasts == [(str(lib.file), "Hounded")]   # Recently played, `book resume`
    assert lib.savers                               # its place kept while it plays
    assert got["book"] == str(lib.file) and got["title"] == "Hounded"
    assert got["pos_ms"] == 120000 and got["dur_ms"] == 600000 and got["chapters"] == []


def test_a_book_heard_to_the_end_starts_again(lib):
    lib.resume_pos[str(lib.file)] = 599000
    org_listen.listen_book(str(lib.file), gate=False)
    assert not any(c[0] == "seek" for c in lib.sent[0])


def test_a_tap_on_the_book_playing_carries_on(lib):
    org_listen.listen_book(str(lib.file), gate=False)
    org_listen.control("pause", "good")
    org_listen._forget_cache()
    ok, got = org_listen.listen_book(str(lib.file), gate=False)
    assert ok and got["state"] == "playing"
    assert sum(1 for b in lib.sent for c in b if c[0] == "loadfile") == 1


def test_the_keys_answer_for_a_book(lib, server):  # noqa: F811
    org_listen.listen_book(str(lib.file), gate=False)
    lib.np["position_ms"] = 100000
    org_listen._forget_cache()
    _, got = _call(server, "GET", "/org/listen")
    assert got["state"] == "playing" and got["book"] == str(lib.file) and got["line"] is None
    _, got = _call(server, "POST", "/org/listen/ctl", {"action": "prev"})
    assert abs(got["pos_ms"] - 70000) < 300          # back 30 s (run on by the clock)
    _, got = _call(server, "POST", "/org/listen/ctl", {"action": "next"})
    assert abs(got["pos_ms"] - 100000) < 600
    _call(server, "POST", "/org/listen/ctl", {"action": "pause"})
    kept = lib.resume_pos[str(lib.file)]             # kept on pause, by its path
    assert abs(kept - 100000) < 900
    org_listen._forget_cache()
    _call(server, "POST", "/org/listen/ctl", {"action": "stop"})
    assert lib.np == {"idle": True}
    kept = lib.resume_pos[str(lib.file)]             # and on stop
    # Resume with nothing loaded reopens the book, where it was.
    _, got = _call(server, "POST", "/org/listen/ctl", {"action": "toggle"})
    assert lib.sent[-1][0][0] == "loadfile" and ["seek", kept / 1000.0, "absolute"] in lib.sent[-1]


def test_a_books_speed_is_its_own(lib, server):  # noqa: F811
    org_listen._keep_prefs(1.2, None)               # the listener's, for notes
    org_listen.listen_book(str(lib.file), gate=False)
    assert ["set_property", "speed", 1.2] in lib.sent[-1]   # none of its own yet
    _call(server, "POST", "/org/listen/ctl", {"action": "speed+"})
    _call(server, "POST", "/org/listen/ctl", {"action": "speed+"})
    assert org_listen._book_speed(lib.file) == 1.4
    assert org_listen._prefs()["speed"] == 1.2     # the notes' speed untouched
    org_listen._keep_prefs(None, 80)                # a volume press keeps the books' speeds
    assert org_listen._book_speed(lib.file) == 1.4
    org_listen._forget_cache()
    _call(server, "POST", "/org/listen/ctl", {"action": "stop"})
    org_listen.listen_book(str(lib.file), gate=False)
    assert ["set_property", "speed", 1.4] in lib.sent[-1]


def test_the_saver_keeps_a_playing_books_place(lib, monkeypatch):
    """Every few seconds while it plays, so a killed app loses little."""
    org_listen.listen_book(str(lib.file), gate=False)
    lib.np["position_ms"] = 300000
    org_listen._forget_cache()
    monkeypatch.setattr(org_listen, "_BOOK_SAVE_S", 0.01)
    monkeypatch.setattr(org_listen, "_SAVER_IDLE_S", 0.05)
    t = __import__("threading").Thread(target=org_listen._saver_loop, daemon=True)
    t.start()
    deadline = time.monotonic() + 2
    while lib.resume_pos.get(str(lib.file)) != 300000 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert lib.resume_pos.get(str(lib.file)) == 300000
    lib.np = {"idle": True}
    org_listen._forget_cache()
    t.join(2)
    assert not t.is_alive()                         # stops once the player is idle


def test_without_the_app_player_the_book_channel_plays_it(lib, monkeypatch):
    monkeypatch.delenv("MEDIA_LISTEN_ENDPOINT")
    ok, got = org_listen.listen_book(str(lib.file), gate=False)
    assert not ok and got["status"] == 409
    assert music._book_on_app(str(lib.file), False) is None


def test_a_file_outside_the_library_is_not_a_book(lib, tmp_path):
    other = tmp_path / "elsewhere.m4b"
    other.write_bytes(b"x")
    assert book_files.library_url(other) is None
    assert not org_listen.listen_book(str(other), gate=False)[0]


def test_the_books_tab_plays_a_library_book_in_the_app(lib, monkeypatch):
    monkeypatch.setattr(music, "_played_book", lambda uri: True)
    monkeypatch.setattr(music, "_answer", lambda: {"now": {}})
    ran: list = []
    monkeypatch.setattr(music, "_run_book", lambda *a, **k: ran.append(a) or (True, ""))
    ok, got = music.owner_control({"action": "book", "uri": str(lib.file)})
    assert ok and got["listening"]["book"] == str(lib.file) and ran == []


def test_library_links_are_signed_and_stay_in_the_library(lib, monkeypatch):
    url = book_files.library_url(lib.file)
    rel = "Series/Hounded Book 1.m4b"
    sig = url.split("sig=")[1]
    assert book_files.served_library(rel, sig) == (lib.file.resolve(), "audio/mp4")
    assert book_files.library_path(url) == Path(str(lib.file))
    assert book_files.served_library(rel, "0" * 32) is None
    # A /books/files/ signature for the same path does not open it here.
    assert book_files.served_library(rel, book_files.sign(rel)) is None
    assert book_files.served_library("../x.m4b", book_files.sign_library("../x.m4b")) is None
    secret = lib.file.parent.parent.parent / "secret.m4b"
    secret.write_bytes(b"s")
    (lib.file.parent / "link.m4b").symlink_to(secret)
    assert book_files.served_library("Series/link.m4b", book_files.sign_library("Series/link.m4b")) is None


def test_the_library_route_serves_with_ranges(lib, server):  # noqa: F811
    url = book_files.library_url(lib.file)
    path = url.removeprefix(PUBLIC)
    base = f"http://{server[0]}:{server[1]}"
    req = urllib.request.Request(base + path, headers={"Range": "bytes=0-99"})
    with urllib.request.urlopen(req, timeout=5) as r:
        assert r.status == 206 and len(r.read()) == 100
    try:
        urllib.request.urlopen(base + path.split("?")[0] + "?sig=" + "0" * 32, timeout=5)
        raise AssertionError("an unsigned link opened")
    except urllib.error.HTTPError as e:
        assert e.code == 404


def test_start_over_plays_from_the_top_in_the_app(lib, monkeypatch):
    """Item 6's start over, on a server with the app player: from 0 there,
    even with the book already loaded at its place."""
    org_listen.listen_book(str(lib.file), gate=False)
    lib.np["position_ms"] = 250000
    org_listen._forget_cache()
    monkeypatch.setattr(music_recent, "_library_books", lambda which: [{"uri": str(lib.file)}])
    monkeypatch.setattr(book_library, "start_over", lambda uri: lib.resume_pos.update({uri: 0}))
    monkeypatch.setattr(music, "_answer", lambda: {"now": {}})
    monkeypatch.setattr(music, "_run_book", lambda *a, **k: (_ for _ in ()).throw(AssertionError("desk")))
    ok, got = music._book_mark({"uri": str(lib.file), "mark": "start_over"})
    assert ok and got["mark"] is None and got["listening"]["pos_ms"] < 500
    assert lib.sent[-1][0] == ["seek", 0, "absolute"]
