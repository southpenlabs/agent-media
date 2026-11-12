"""The request counter (hits.py).

It exists because the obvious check failed: `share` and `speech-state` both
silence `log_message`, so seven days of empty journals said nothing about
whether anything still reaches their mesh-bound ports, and I nearly read
that silence as "unused". These pin the two things that would make the
counter worse than useless — losing the count on a restart, and quietly
becoming a record of which conversations happened when.
"""

from __future__ import annotations

import json

import pytest

from agent_media_core import hits


@pytest.fixture(autouse=True)
def _own_state(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIA_HITS", "1")
    monkeypatch.setattr(hits, "state_dir", lambda: tmp_path / "state")
    monkeypatch.setattr(hits, "_pending", {})
    monkeypatch.setattr(hits, "_last_flush", {})


def test_a_request_is_counted_with_its_client_and_endpoint():
    hits.record("share", "100.64.0.7", "/share", flush=True)
    rec = hits.read("share")
    assert rec["total"] == 1 and rec["remote"] == 1 and rec["local"] == 0
    assert rec["clients"] == {"100.64.0.7": 1}
    assert rec["paths"] == {"/share": 1}
    assert rec["first"] and rec["last"]


def test_local_and_remote_are_counted_apart():
    """The whole decision rests on this split: only-local means the listener
    could bind loopback, which is what we are trying to find out."""
    for client in ("127.0.0.1", "::1", "127.0.0.1"):
        hits.record("speech-state", client, "/speech", flush=True)
    hits.record("speech-state", "100.64.0.9", "/speech", flush=True)
    rec = hits.read("speech-state")
    assert rec["total"] == 4 and rec["local"] == 3 and rec["remote"] == 1


@pytest.mark.parametrize("path,want", [
    ("/sessions/9f3c-aaaa-bbbb/events", "/sessions"),
    ("/speech", "/speech"),
    ("/speech/clips/20261107-secret.mp3?sig=abcdef", "/speech"),
    ("/share?url=https://example.test/private", "/share"),
    ("/", "/"),
    ("", "/"),
])
def test_only_the_first_path_segment_is_kept(path, want):
    """A counter must not become a record of which conversations happened
    when, so no ids, no signed names and no query strings are stored."""
    assert hits.segment(path) == want
    hits.record("x", "1.2.3.4", path, flush=True)
    assert list(hits.read("x")["paths"]) == [want]
    stored = json.dumps(hits.read("x"))
    for leak in ("9f3c", "secret", "sig=", "example.test"):
        assert leak not in stored, leak


def test_a_restart_does_not_reset_the_count():
    """These services restart most days and the measurement takes days, so a
    counter that started from zero each time would never answer anything."""
    hits.record("share", "100.64.0.7", "/share", flush=True)
    # A fresh process: nothing in memory, the file still there.
    hits._pending.clear()
    hits._last_flush.clear()
    hits.record("share", "100.64.0.8", "/share", flush=True)
    rec = hits.read("share")
    assert rec["total"] == 2
    assert rec["clients"] == {"100.64.0.7": 1, "100.64.0.8": 1}


def test_the_first_request_is_written_at_once_then_counts_are_held(monkeypatch):
    """One file write per request would be write amplification on a polled
    endpoint; one every 30s is plenty for a question measured in days.

    But the first request of a process writes immediately, deliberately: the
    first thing anyone does with this is check that it counts at all, and
    "wait 30 seconds and look again" is how a working counter gets mistaken
    for a broken one.
    """
    monkeypatch.setenv("MEDIA_HITS_FLUSH_S", "3600")
    hits.record("share", "100.64.0.7", "/share")
    assert hits.read("share")["total"] == 1, "the first one lands at once"

    for _ in range(4):
        hits.record("share", "100.64.0.7", "/share")
    assert hits.read("share")["total"] == 1, "the next four are held"

    hits.flush_all()
    assert hits.read("share")["total"] == 5


def test_counting_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("MEDIA_HITS", "0")
    hits.record("share", "100.64.0.7", "/share", flush=True)
    assert hits.read("share")["total"] == 0


def test_a_counter_never_breaks_a_request(monkeypatch, tmp_path):
    """It replaces `log_message`, which runs inside answering a request. An
    unwritable state dir must cost a count, not the response."""
    monkeypatch.setattr(hits, "state_dir",
                        lambda: tmp_path / "nope" / "deeper")
    (tmp_path / "nope").write_text("a file, not a directory")
    hits.record("share", "100.64.0.7", "/share", flush=True)   # must not raise


def test_the_counter_is_shaped_like_log_message():
    """Wired in as `log_message = hits.counter("share")`, so it is called
    with a format string and args and must ignore them."""
    class FakeHandler:
        client_address = ("100.64.0.7", 51234)
        path = "/share?url=secret"
        log_message = hits.counter("share")

    FakeHandler().log_message('"%s" %s %s', "POST /share HTTP/1.1", "200", "-")
    hits.flush_all()
    rec = hits.read("share")
    assert rec["total"] == 1 and rec["clients"] == {"100.64.0.7": 1}
    assert rec["paths"] == {"/share": 1}
    # The query string the handler had is not stored.
    assert "secret" not in json.dumps(rec)


def test_a_handler_with_no_client_address_still_answers():
    class Odd:
        log_message = hits.counter("share")

    Odd().log_message("x")      # must not raise
    hits.flush_all()
    assert hits.read("share")["total"] == 1


def test_both_endpoints_are_wired_to_it():
    """The point of the patch: the `pass` that destroyed the evidence is
    gone from both."""
    from agent_media_core.entrypoints import share_listener, speech_state

    for handler in (share_listener.Handler, speech_state.Handler):
        src = getattr(handler.log_message, "__qualname__", "")
        assert "counter" in src, handler


def test_held_counts_are_written_on_a_clean_exit(monkeypatch):
    """A kill loses at most one window; a clean exit should lose nothing."""
    import atexit

    registered = []
    monkeypatch.setattr(atexit, "register", registered.append)
    monkeypatch.setattr(hits, "_atexit_armed", False)
    monkeypatch.setenv("MEDIA_HITS_FLUSH_S", "3600")

    hits.record("share", "100.64.0.7", "/share")   # first: lands at once
    hits.record("share", "100.64.0.7", "/share")   # held
    assert hits.read("share")["total"] == 1
    assert hits.flush_all in registered, "nothing would flush on exit"

    for fn in registered:
        fn()
    assert hits.read("share")["total"] == 2


def test_the_first_request_of_a_process_always_lands(monkeypatch):
    """The property the whole measurement rests on: a port something still
    reaches cannot look untouched, however often the service restarts."""
    monkeypatch.setenv("MEDIA_HITS_FLUSH_S", "86400")
    for restart in range(3):
        hits._pending.clear()
        hits._last_flush.clear()          # a fresh process
        hits.record("speech-state", "100.64.0.9", "/speech")
    assert hits.read("speech-state")["total"] == 3


def test_this_hosts_own_mesh_address_counts_as_local(monkeypatch):
    """A process ON red5 that dials `red5:8675` reaches it through MagicDNS,
    so the source address is red5's own 100.x.y.z — not 127.0.0.1. Counting
    that as "from elsewhere" would answer the question backwards, which is
    what the first live counts did: my own probes looked like the phone.
    """
    from agent_media_core.entrypoints import speech_state

    monkeypatch.setattr(hits, "_own", None)
    monkeypatch.setattr(speech_state, "_peer_ip", lambda: "100.103.43.93")
    assert "100.103.43.93" in hits.local_addresses()

    hits.record("speech-state", "100.103.43.93", "/speech", flush=True)
    hits.record("speech-state", "100.64.0.9", "/speech", flush=True)
    rec = hits.read("speech-state")
    assert rec["local"] == 1, "our own mesh address is this host"
    assert rec["remote"] == 1, "another host's is not"


def test_a_host_with_no_mesh_just_has_the_loopback_names(monkeypatch):
    from agent_media_core.entrypoints import speech_state

    monkeypatch.setattr(hits, "_own", None)
    monkeypatch.setattr(speech_state, "_peer_ip", lambda: None)
    assert hits.local_addresses() == hits.LOCAL


def test_the_mesh_address_is_resolved_once(monkeypatch):
    """It costs a subprocess; a counter in a request path must not pay that
    per request."""
    from agent_media_core.entrypoints import speech_state

    calls = []
    monkeypatch.setattr(hits, "_own", None)
    monkeypatch.setattr(speech_state, "_peer_ip",
                        lambda: calls.append(1) or "100.0.0.1")
    for _ in range(5):
        hits.record("share", "127.0.0.1", "/share", flush=True)
    assert len(calls) == 1, calls
