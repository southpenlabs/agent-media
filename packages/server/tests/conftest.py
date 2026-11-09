"""Test isolation for the server package.

The same isolation `packages/visual/tests/conftest.py` gives the canvas, and
for the same reasons. core's cli.py calls load_env_file at module import, so a
combined run pulls the machine's real ~/.config/agent-media.env into
os.environ during collection; scrub every MEDIA_* var so these tests always
see package defaults, and set what a test needs with monkeypatch.setenv.

And point the state store at a throwaway dir. The routes here read and write
the speech history, drafts and the book-tracks shelf; a test module that
skipped this would write into David's real ones.
"""

import os

import pytest

# A fake chat that exits just after its turn would have sessiond run the real
# speech hook to keep a "lost" reply (`Supervisor._recover_reply`), into the
# real history: it did, 30 Oct 2026. Off for the whole process, since an exit
# can land after a test's teardown; set again below after the MEDIA_* scrub.
os.environ["MEDIA_SESSIOND_RECOVER_REPLY"] = "0"


@pytest.fixture(autouse=True)
def _clean_media_env(monkeypatch, tmp_path, request):
    for k in list(os.environ):
        if k.startswith("MEDIA_"):
            monkeypatch.delenv(k, raising=False)
    # Inside a systemd unit (sessiond's shells, a CI runner), a replay a test
    # starts would go into a scope on the host's user manager
    # (procinfo.outliving_service): never from a test.
    monkeypatch.delenv("INVOCATION_ID", raising=False)
    # A fresh chat asks whether its agent is installed and signed in *on this
    # machine* (send._agent_unready) — which made the /ask tests pass on a
    # desk with Claude Code signed in and fail everywhere else (CI, a scratch
    # HOME). Ready, unless a test is about that check (`real_agent_check`).
    if request.node.get_closest_marker("real_agent_check") is None:
        from agent_media_server import send

        monkeypatch.setattr(send, "_agent_unready", lambda agent, profile="": "")
    # Sasonica Shell's config (shell_signin.py): an empty one, so /dashboard
    # never reads red5's real runner token or calls its live Worker.
    monkeypatch.setenv("SASONICA_CONF", str(tmp_path / "sasonica-conf"))
    # The layout (agent_media_core/layout.py) is pinned to David's, which is
    # what these tests were written against: detection reads the real HOME
    # (~/.amux, ~/.claude/settings.json), so unpinned they would pass on red5
    # and fail anywhere else. test_layout.py unpins it where it needs to.
    monkeypatch.setenv("MEDIA_LAYOUT", "projects-per-tmux-session")
    monkeypatch.setenv("MEDIA_SESSIOND_RECOVER_REPLY", "0")
    # A restart of sessiond is said aloud (restart_services.announce, `media
    # say --alert`): never from a test, which would speak on the real phone.
    monkeypatch.setenv("MEDIA_RESTART_ANNOUNCE", "0")
    # The play gate (agent_media_core.play_gate): off ("-", which no env file
    # refills), so no test asks red5's real treatment check. A test of the
    # gate sets its own.
    monkeypatch.setenv("MEDIA_PLAY_GATE_CMD", "-")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    # Files a reply names are copied into the upload root as the thread is
    # read (file_links.py): never into the real ~/shared, and no copy
    # remembered from another test.
    monkeypatch.setenv("MEDIA_UPLOAD_DIR", str(tmp_path / "shared"))
    from agent_media_server import file_links

    file_links.reset_cache()
    # The phone's contacts are read, and a Join written back to Google
    # (contacts_writeback.py), through `agent-phone-adb shell`: never the
    # real phone from a test. A test that needs one sets a stand-in.
    monkeypatch.setenv("MEDIA_CONTACTS_ADB", str(tmp_path / "no-such-adb") + " shell")
    # Private-thread markers (agent_media_core.private): never red5's own.
    monkeypatch.setenv("AGENT_PRIVATE_DIR", str(tmp_path / "private.d"))
    # The thread tags live in the real ~/org (tags.py): never read or edit it.
    monkeypatch.setenv("MEDIA_THREAD_TAGS_DIR", str(tmp_path / "org-sessions"))
    # Nor load the embedding model (tag_embed.py), installed or not.
    monkeypatch.setenv("MEDIA_TAG_EMBED", "0")
    # Nor tag chats in the background (tags_auto.py).
    monkeypatch.setenv("MEDIA_AUTO_TAG_CHATS", "0")
    # Nor suggest a space for an Inbox chat with a real model (space_suggest.py).
    monkeypatch.setenv("MEDIA_SPACE_SUGGEST", "0")
    # Nor ask a model for a people chat's one-tap answers (room_replies.py).
    monkeypatch.setenv("MEDIA_ROOM_REPLIES", "0")
    # Nor send a clip to a real speech-to-text with this machine's keys
    # (agent_media_core.transcribe; voice_clips.py, voice_notes.py). A test
    # that wants one turns it on and replaces the service.
    monkeypatch.setenv("MEDIA_STT", "0")
    from agent_media_server import tags_auto

    tags_auto._reset_for_tests()
    from agent_media_server import space_suggest

    space_suggest._reset_for_tests()
    from agent_media_server import room_replies

    room_replies._reset_for_tests()
    # A model one test made fail is not skipped in the next (intake/fast.py).
    from agent_media_core.intake import fast

    fast._reset_for_tests()
    # The alert store files TODOs in ~/org/inbox.org: never David's here.
    monkeypatch.setenv("MEDIA_ALERTS_INBOX", "0")
    # No ~/agent-personas: a host without the repo, unless a test makes one.
    monkeypatch.setenv("MEDIA_PERSONAS_DIR", str(tmp_path / "no-agent-personas"))
    # No ~/spl/agents: no business agents' folders, unless a test makes some.
    monkeypatch.setenv("MEDIA_AGENT_WORKSPACES", str(tmp_path / "no-agent-workspaces"))
    monkeypatch.delenv("MEDIA_AGENT_FRONT", raising=False)
    # A sign-in would tell its (stand-in) issuer about this server from a
    # thread (servers_report.py); test_servers_report.py turns it on.
    monkeypatch.setenv("MEDIA_SERVERS_REPORT", "0")
    # A quick tunnel's URL is published to sasonica.com/r (tunnel.py): never
    # from a test. test_tunnel.py points it at a local stand-in.
    monkeypatch.setenv("MEDIA_LOOKUP_URL", "-")
    # Feedback goes to sasonica.com/feedback (feedback.py): never from a test.
    # test_feedback.py points it at a stand-in.
    monkeypatch.setenv("MEDIA_FEEDBACK_URL", "-")
    # A thread marked Done or archived gets a recap through the gateway
    # (reap.recap_soon): never from a test. test_issues.py turns it on.
    monkeypatch.setenv("MEDIA_DONE_RECAP", "0")
    # … and so does a thread gone quiet, on each reaper run (reap.recap_quiet).
    monkeypatch.setenv("MEDIA_QUIET_RECAP", "0")
    # A podcast link saved from the Media tab downloads through the phone
    # (podcast_links.start_fetch): never from a test.
    monkeypatch.setenv("MEDIA_PODCAST_LINK_FETCH", "0")
    monkeypatch.setenv("MEDIA_PODCAST_LINK_CHANNELS", "0")
    # A practice's length is looked up with yt-dlp or ffprobe in the
    # background (practice_library._fill_durations): never from a test.
    monkeypatch.setenv("MEDIA_PRACTICE_DURATIONS", "0")
    # … and a teacher's profile from Insight Timer (practice_library.teacher_profile).
    monkeypatch.setenv("MEDIA_PRACTICE_PROFILES", "0")
    monkeypatch.setenv("MEDIA_FACTS_URL", "-")
    # A shared podcast link is looked up on its page and in the iTunes
    # directory (podcast_resolve): never from a test.
    monkeypatch.setenv("MEDIA_PODCAST_RESOLVE", "0")
    # … and who an artist, author or show is (media_people).
    monkeypatch.setenv("MEDIA_PEOPLE", "0")
    # … and a video's channel, a channel's page and uploads (music_channel).
    monkeypatch.setenv("MEDIA_MUSIC_CHANNEL", "0")
    # The tests exercise the personal-use methods (personal_use.py) as a
    # personal install has them; test_personal_use.py checks a public one.
    monkeypatch.setenv("MEDIA_PERSONAL_USE", "1")
    # The add-on methods (personal_use.ADDON_METHODS) as stand-ins, never
    # an installed personal-use add-on even where there is one: a test
    # registers what it needs (facebook-previews' agent is one line).
    from agent_media_core import personal_use as _pu
    # youtube-radio is a stand-in that is never asked (a test fakes
    # radio_io.youtube_mix / radio_dj.search itself), so the radio's own
    # switch, MEDIA_RADIO_YOUTUBE, decides as it did before the add-on.
    def _never(*a, **kw):
        raise AssertionError("a test reached the YouTube add-on's stand-in")
    _yt_radio = type("YTRadio", (), {"mix": staticmethod(_never), "search": staticmethod(_never)})()
    monkeypatch.setattr(_pu, "_PROVIDERS", {"facebook-previews": type("FB", (), {"user_agent": "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)"})(),
                                            "youtube-radio": _yt_radio,
                                            # Streaming has nothing to call: it is allowed, as on
                                            # a personal install; a test of the refusal drops it.
                                            "youtube-streaming": object(),
                                            "youtube-search": object()})
    monkeypatch.setattr(_pu, "_LOADED", [True])
    # A device job (`media restart-services --host`, `media update --host`,
    # doctor --fix) asks this host's canvas, by default 127.0.0.1:8781 with
    # the real ~/.amux token: on red5 that is the live server, which would
    # pull and restart the real phone. Nowhere, unless a test sets its own.
    monkeypatch.setenv("MEDIA_PHONE_JOBS_URL", "http://127.0.0.1:9")
    # Texts (sms.py, `media sms send`) go out as SMS through the real Google
    # Messages bridge: never from a test. Its one network call fails; a test
    # fakes `sms._sender` (or `_http`) instead.
    from agent_media_server import sms

    def _no_real_sms(*a, **kw):
        raise AssertionError("a test reached the real SMS bridge (sms.GmessagesSender)")
    monkeypatch.setattr(sms.GmessagesSender, "_http", staticmethod(_no_real_sms))
    # Paired devices live under that state dir too (devices.json, the pairing
    # codes); the in-process bits — the parsed-file cache and the per-address
    # pairing-failure counts — are reset so one test's refusals cannot
    # rate-limit the next.
    from agent_media_server import devices

    devices._reset_for_tests()
    # Recaps are read out of Claude Code's transcripts: point that at an empty
    # throwaway dir, so a test never reads David's real ones, and forget what
    # the last test's files held (the cache is keyed by path and inode).
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    # The other harnesses' stores are read the same way (the thread list is
    # every harness's conversations, not only Claude's), so point those at
    # throwaway dirs too — unset, a test would list this machine's real
    # Codex, pi, Hermes and opencode conversations.
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path / "pi"))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    # And Gemini CLI's, under GEMINI_CLI_HOME's `.gemini`.
    monkeypatch.setenv("GEMINI_CLI_HOME", str(tmp_path / "gemini-home"))
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    # And Grok's, the whole of its home under GROK_HOME.
    monkeypatch.setenv("GROK_HOME", str(tmp_path / "grok-home"))
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    from agent_media_server import context, recaps

    recaps._reset_for_tests()
    context._reset_for_tests()
    # The same for the messages read out of those transcripts, and no thread
    # stream's watcher left running from another test.
    from agent_media_server import thread_events, transcript

    transcript._reset_for_tests()
    thread_events._reset_for_tests()
    from agent_media_server import session_events

    session_events._reset_for_tests()
    # Asks to speak for another device (mic.py) are in memory: none carried over.
    from agent_media_server import mic

    mic._reset_for_tests()
    # And an agent's asks of the phone (phone.py).
    from agent_media_server import phone

    phone._reset_for_tests()
    # And the notes agents put in the phone's shade (notes.py).
    from agent_media_server import notes

    notes._reset_for_tests()
    # Memory for /sessions/state is read from /proc: an empty fake root, so
    # a test sees "unknown" unless it builds a process tree of its own, and
    # no pid left over from a real sweep in another test.
    from agent_media_server import procmem, sessions

    (tmp_path / "fake-proc").mkdir(exist_ok=True)
    monkeypatch.setattr(procmem, "PROC", tmp_path / "fake-proc")
    monkeypatch.setattr(sessions, "_PIDS", {})
    # A session's directory is kept once found; the subagents' scans too.
    monkeypatch.setattr(sessions, "_CWDS", {})
    # Titles read out of a transcript are cached by (size, mtime); a tmp_path
    # file can land on the same pair as the last test's.
    monkeypatch.setattr(sessions, "_HEADLESS_TITLES", {})
    monkeypatch.setattr(sessions, "_STORED_TITLES", {})
    monkeypatch.setattr(sessions, "_FIRST_PROMPT", {})
    from agent_media_server import agents

    agents._reset_for_tests()
    # Microsoft's voice list is fetched over the network: offline here, so
    # the built-in list serves (render/device.py).
    from agent_media_core.render import device

    def offline():
        raise OSError("offline in tests")

    monkeypatch.setattr(device, "_fetch_edge_voices", offline)
    monkeypatch.setattr(device, "_last_fetch_try", 0.0)
    # The search index lives under the state dir above; forget the last
    # test's connection and its short-lived caches.
    from agent_media_server import search

    search._reset_for_tests()
    # The model sheet asks Claude Code for its own list (claude_models.py):
    # never here — the bare names serve, and no sheet is kept between tests.
    from agent_media_core import antigravity, claude_models
    from agent_media_server import session_settings

    # The private index (private_cards.py): never this machine's Ollama or
    # its seal key; a test that wants either sets its own.
    monkeypatch.setenv("MEDIA_PRIVATE_OLLAMA", "http://127.0.0.1:9")
    for k in ("SOPS_AGE_KEY_FILE", "SOPS_AGE_RECIPIENT"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("MEDIA_SEAL_KEY_FILE", str(tmp_path / "no-seal-key.txt"))
    monkeypatch.setattr(claude_models, "models", lambda **_: [])
    monkeypatch.setattr(session_settings, "_models_memo", (0.0, []))
    antigravity.forget()
    monkeypatch.setattr(antigravity, "_list", lambda *a, **k: "")
    # No server pinned the thread-watching table here, so a stream never
    # writes it (core's watching.publish_pinned); and the watchers stop now,
    # while the environment above is still in place (this teardown runs
    # before monkeypatch's), not later under the real XDG_STATE_HOME.
    from agent_media_core import watching

    watching._reset_for_tests()
    yield
    thread_events._reset_for_tests()
    watching._reset_for_tests()


def pytest_configure(config):
    config.addinivalue_line("markers", "real_agent_check: use the real send._agent_unready "
                            "(the machine's harnesses), not the conftest's ready stub")
