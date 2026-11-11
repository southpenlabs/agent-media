import os

import pytest


@pytest.fixture(autouse=True)
def _no_embed_pages(monkeypatch):
    """Practice embeds' pictures are read from their makers' pages
    (practice_embed.fill_art): never from a test. One that wants a page
    patches `_fetch_page` itself."""
    from agent_media_core import practice_embed

    def _no_embed_page(url):
        raise RuntimeError("a test asked a real embed page for its picture")

    monkeypatch.setattr(practice_embed, "_fetch_page", _no_embed_page)


@pytest.fixture(autouse=True)
def _no_live_phone_backend(monkeypatch):
    """Keep the suite away from the real phone player.

    `media music` transport routes to the phone-local backend whenever
    MEDIA_MUSIC_LOCAL_ENDPOINT is set (the cli module loads it from
    ~/.config/agent-media.env at import) and the phone's mpv has a track
    loaded — so on a dev box with music playing, an un-scrubbed test run
    would seek/pause the user's actual playback.

    Since the `app` target (music in Sasonica) the router prefers an even
    nearer backend, chosen by MEDIA_PHONE_PLAYER_URL — and it is tried
    FIRST, so scrubbing only the mpv endpoint stopped covering this. It
    was not hypothetical: `media music seek` in the suite reached the
    phone's player for real, and the two music-seek tests failed here
    while passing in CI, which is what an inherited-config hazard looks
    like from the outside. Scrubbed by prefix so a per-target key
    (..._URL_APP) goes too; a test that wants a player sets its own.
    Sasonica's own media player (the `sasonica` target) is probed first of
    all, so its endpoint goes as well.
    """
    monkeypatch.delenv("MEDIA_MUSIC_LOCAL_ENDPOINT", raising=False)
    monkeypatch.delenv("MEDIA_MUSIC_SASONICA_ENDPOINT", raising=False)
    for key in [k for k in os.environ if k.startswith("MEDIA_PHONE_PLAYER_URL")]:
        monkeypatch.delenv(key, raising=False)


@pytest.fixture(autouse=True)
def _no_live_play_gate(monkeypatch):
    """The env file names the play gate (MEDIA_PLAY_GATE_CMD, red5's PHS
    treatment check): inherited, every play in the suite would ask the real
    check and be held during a treatment. Off with "-", which no later load
    of the env file refills; a test of the gate sets its own."""
    monkeypatch.setenv("MEDIA_PLAY_GATE_CMD", "-")
    monkeypatch.delenv("MEDIA_PLAY_MAX_AGE_S", raising=False)
    monkeypatch.delenv("MEDIA_PLAY_FORCE", raising=False)
    monkeypatch.delenv("MEDIA_PLAY_REQUESTED_AT", raising=False)
    from agent_media_core import play_gate
    play_gate.forget_verdict()


@pytest.fixture(autouse=True)
def _no_live_rooms(monkeypatch):
    """Keep the suite away from the house speakers' Snapcast server.

    The env file names it (MEDIA_SNAP_JSONRPC_HOST) and the music stream
    (MEDIA_DUCK_ROOMS_STREAM), and the rooms' volume and mute are set through
    it (route/coordinator.py's duck, music_rooms.set_room): an un-scrubbed
    test would mute or turn down a room someone is listening in. The host is
    pointed at a closed local port rather than unset, since unset means the
    local snapserver, which on the rooms hub is the real one. A test that
    wants rooms fakes the snapcast calls and sets the stream itself.
    """
    monkeypatch.delenv("MEDIA_DUCK_ROOMS_STREAM", raising=False)
    monkeypatch.setenv("MEDIA_SNAP_JSONRPC_HOST", "127.0.0.1")
    monkeypatch.setenv("MEDIA_SNAP_JSONRPC_PORT", "9")


@pytest.fixture(autouse=True)
def _no_inherited_dj_duck(monkeypatch):
    """The env file switches DJ-style ducking on (MEDIA_DJ_DUCK=1, 20 Oct
    2026), and something earlier in the suite loads that file into
    os.environ: after_speech then waits the DJ's beat and the coordinator
    tests' exact sleeps fail in a full run and pass alone. test_dj_duck
    sets it itself."""
    monkeypatch.delenv("MEDIA_DJ_DUCK", raising=False)


@pytest.fixture(autouse=True)
def _no_live_trakt_or_tmdb(monkeypatch):
    """The env file may name a Trakt API app (MEDIA_TRAKT_CLIENT_ID/_SECRET)
    and a TMDb credential (MEDIA_TMDB_TOKEN/_API_KEY/_REGION): inherited, a
    Movies/TV listing would call them for real with the user's signed-in
    token. Scrubbed by prefix; a test that wants them sets its own and
    stubs the HTTP (trakt._request, tmdb._request)."""
    for key in [k for k in os.environ if k.startswith(("MEDIA_TRAKT_", "MEDIA_TMDB_", "MEDIA_MUSIC_REGION"))]:
        monkeypatch.delenv(key, raising=False)


@pytest.fixture(autouse=True)
def _no_inherited_music_level(monkeypatch):
    """The env file may set the backing music level (MEDIA_MUSIC_BED_GAIN_DB),
    and every duck level follows it (route/dj.py `scaled`):
    inherited, the duck tests' exact levels move. A test of the level sets
    its own."""
    monkeypatch.delenv("MEDIA_MUSIC_BED_GAIN_DB", raising=False)
    # The tests were written at the reference level (-4, DEFAULT_GAIN_DB):
    # the files unnamed by a gain, the duck unscaled. The shipped default
    # (DEFAULT_LEVEL_DB, -20 since 31 Oct 2026) is checked on its own.
    from agent_media_core import music_bed
    monkeypatch.setattr(music_bed, "DEFAULT_LEVEL_DB", music_bed.DEFAULT_GAIN_DB)
    # Favourites gives way to the library or a generated bed at random
    # (mix_share): never here unless a test of it sets the share.
    monkeypatch.setattr(music_bed, "DEFAULT_MIX_SHARE", 0.0)
    monkeypatch.delenv("MEDIA_MUSIC_BED_MIX_SHARE", raising=False)
    for key in ("BREATHE", "BREATHE_DB", "ATTACK_MS", "RELEASE_MS", "HOLD_MS"):
        monkeypatch.delenv(f"MEDIA_MUSIC_BED_{key}", raising=False)


@pytest.fixture(autouse=True)
def _no_restart_announcement(monkeypatch):
    """A restart of sessiond is said aloud (restart_services.announce runs
    `media say --alert`), which from a test would speak on the real phone.
    test_restart_drain turns it on against a stand-in."""
    monkeypatch.setenv("MEDIA_RESTART_ANNOUNCE", "0")


@pytest.fixture(autouse=True)
def _no_remote_say(monkeypatch):
    """Keep the suite on the local render path.

    MEDIA_REMOTE_SAY_CMD replaces rendering entirely: submit_event hands the
    whole reply to another host and returns. Any test asserting on clips,
    history extras or playback then exercises a branch it never meant to, and
    fails in a way that points at the code rather than at the config it
    inherited. That config is not hypothetical — a host that speaks through a
    remote hub (or has media-lane switching lanes by network) sets this
    variable in ~/.config/agent-media.env, so the suite would pass or fail
    depending on which room the developer is standing in.

    A test that wants the remote path should set it explicitly.

    The per-target keys have to go too, and by prefix rather than by name:
    the lane is now chosen by MEDIA_REMOTE_SAY_CMD_<TARGET>, so media-lane
    writes ..._PHONE and scrubbing only the bare name would leave exactly the
    inherited-config hazard above — passing or failing by which room the
    developer is standing in — while looking as though it were handled.
    """
    for key in [k for k in os.environ if k.startswith("MEDIA_REMOTE_SAY_CMD")]:
        monkeypatch.delenv(key, raising=False)


@pytest.fixture(autouse=True)
def _no_inherited_mailbox(monkeypatch):
    """Same hazard as above, and it was live: the converse doorbell's mailbox.

    `doorbell.post` reads MEDIA_CONVERSE_MAILBOX and does nothing when it is
    unset, so two tests that asserted on the relay-msg argv passed here and
    failed in CI — this developer's ~/.config/agent-media.env names the box
    (`cece`) and the sender (`sam`), and something earlier in the suite loads
    that file into os.environ for real. In isolation the same two tests failed
    locally too, which is the tell: they were reading config, not fixtures.

    Scrubbed by prefix like the remote-say keys, and deliberately not
    MEDIA_CONVERSE_NOTIFY, which the notification tests set for themselves.
    """
    for key in [k for k in os.environ if k.startswith("MEDIA_CONVERSE_MAILBOX")]:
        monkeypatch.delenv(key, raising=False)


@pytest.fixture(autouse=True)
def _no_inherited_app(monkeypatch):
    """A suite run from a Paseo agent inherits PASEO_AGENT_ID, which makes
    every reply count as watched (toast._in_other_app) and nothing hold."""
    monkeypatch.delenv("PASEO_AGENT_ID", raising=False)
    monkeypatch.delenv("MEDIA_TOAST_APP_ENV", raising=False)


# Keep test transitions out of the production floor history.
#
# Same hazard as the fixtures in this file — real config, real side effect —
# but the damage lands somewhere durable rather than on the developer's desk.
# Every speech-hold and every armed rendezvous in this suite spawns
# `relay-floor.sh publish`, so a run deposits rows like `speech/pane7 hold 120`
# and `input/sam arm 5` into the live D1 table, permanently and
# indistinguishably from real ones. That table exists for exactly one question
# — whether the per-owner holds have started colliding now that there are three
# of us — and it was added (tmux-relay migrations/0008) precisely because the
# local markers are reaped and that question had no answer on 2026-08-11. A
# history salted with fixture collisions cannot answer it either, which makes
# the mirror worse than useless: it looks authoritative while lying.
#
# Set at import rather than by an autouse monkeypatch fixture, which is what
# this was first written as and which leaked. `_armed()` in the rendezvous
# tests runs the `with Rendezvous(...)` block on a thread, and the tests that
# assert a *refusal* never join it — so `__exit__`, and its `_mirror("disarm")`,
# can fire after teardown has already restored the variable. That race let
# roughly one row per full run through: rare enough to look like it worked.
# A process-wide assignment has no restore window for the thread to land in.
#
# A test that wants to assert on the mirror should monkeypatch it back on.
os.environ["MEDIA_FLOOR_MIRROR"] = "0"

# The Claude Code hook forks its Stop path off at once (`_detach_early`); a
# test driving `main()` must stay in this process to see what it did.
os.environ["MEDIA_HOOK_NO_EARLY_DETACH"] = "1"


@pytest.fixture(autouse=True)
def _no_live_speech_token(monkeypatch, tmp_path):
    """Keep the suite off the live speech token and its followers.

    A replay now takes the playback token (`speech-playback.lock`), registers
    in `speech-waiters/` and stops any earlier replay's follower by the pid in
    `replay-track.pid` — all under the state dir. A test that replays without
    isolating that dir would queue behind (or make yield) whatever reply is
    speaking on the developer's phone, and could SIGTERM a real replay's
    follower. So every test starts with its own state dir; a test that points
    XDG_STATE_HOME somewhere itself still wins, being set after this.
    """
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))
    # Private-thread markers (agent_media_core.private): never red5's own.
    monkeypatch.setenv("AGENT_PRIVATE_DIR", str(tmp_path / "private.d"))
    # opencode's conversations live under XDG_DATA_HOME (harnesses.opencode_db);
    # unset, a test that lists every harness's sessions would read this
    # machine's real ones.
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    # Gemini CLI's under GEMINI_CLI_HOME's `.gemini` (harnesses.gemini_dir),
    # and its sign-in is read from there: never this machine's.
    monkeypatch.setenv("GEMINI_CLI_HOME", str(tmp_path / "gemini-home"))
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    # Grok's whole home moves with GROK_HOME (harnesses.grok_dir): its
    # sessions, sign-in and settings are never this machine's.
    monkeypatch.setenv("GROK_HOME", str(tmp_path / "grok-home"))
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    # A test run from inside a systemd unit (sessiond's shells, a CI runner)
    # must not put the replays it starts into scopes on the host's user
    # manager (procinfo.outliving_service): they stay plain children.
    monkeypatch.delenv("INVOCATION_ID", raising=False)
    # The hook's related threads (related_threads.py): never this machine's
    # roam tree, agent-sessions index or Ollama.
    monkeypatch.setenv("MEDIA_ORG_DIR", str(tmp_path / "org"))
    monkeypatch.setenv("MEDIA_RELATED_DB", str(tmp_path / "zk-index.sqlite"))
    monkeypatch.setenv("ZK_OLLAMA", "http://127.0.0.1:9")
    # A private chat's cards (private_cards.py): never this host's seal key
    # or its model.
    monkeypatch.setenv("MEDIA_PRIVATE_OLLAMA", "http://127.0.0.1:9")
    monkeypatch.delenv("SOPS_AGE_KEY_FILE", raising=False)
    monkeypatch.delenv("SOPS_AGE_RECIPIENT", raising=False)
    monkeypatch.delenv("MEDIA_SEAL_KEY_FILE", raising=False)


@pytest.fixture(autouse=True)
def _no_ringer_reads(monkeypatch):
    """Keep the suite from asking the real phone whether it is on silent.

    Every earcon now reads the ringer verdict for a phone target, over the
    broker socket the shell's config names — a real round trip to p8a from a
    test about tones, and a flaky one on a phone that happens to be silenced.
    No target is ringer-gated unless a test says so (test_ringer_gate does).
    """
    monkeypatch.setenv("MEDIA_RINGER_TARGET", "")


@pytest.fixture(autouse=True)
def _no_follow_pane(monkeypatch):
    """Keep the suite out of the developer's terminal.

    Speaking with auto-highlight on now opens the follow-along pane
    (`ensure_follow_view`), and the suite is very often run from inside the
    tmux session it would open it in — so an un-scrubbed run would split panes
    and add windows under the person watching the tests. Same hazard as the
    phone backend above: real config, real side effect, nothing to do with what
    the test is asserting. A test that wants the coupling sets this itself.
    """
    monkeypatch.setenv("MEDIA_FOLLOW_AUTO", "0")


@pytest.fixture(autouse=True)
def _no_voice_list_fetch(monkeypatch):
    """Microsoft's voice list is fetched over the network (render/device.py):
    offline here, so the built-in list serves. Tests of the fetch put their
    own in."""
    from agent_media_core.render import device

    def offline():
        raise OSError("offline in tests")

    monkeypatch.setattr(device, "_fetch_edge_voices", offline)
    monkeypatch.setattr(device, "_last_fetch_try", 0.0)


@pytest.fixture(autouse=True)
def _no_listener_audio_choice(monkeypatch, tmp_path):
    """Keep the listener's own speech/music choice out of the suite.

    `media speech-target` (and the app's picker) writes a file in the real
    state dir that wins over MEDIA_SPEECH_DEFAULT_TARGET for every new reply.
    This suite does not isolate the state dir as a whole, so without this a
    developer who had moved speech to the rooms would see every test that
    asserts on the env default's target fail — or, worse, pass for the wrong
    reason. The files go to a per-test directory instead; a test that wants
    a choice writes it there through audio_targets.
    """
    from agent_media_core import audio_targets

    d = tmp_path / "audio-choice"
    monkeypatch.setattr(audio_targets, "_path", lambda name: d / name)


@pytest.fixture(autouse=True)
def _no_followup_calls(monkeypatch):
    """The Stop hook's follow-up is a gateway call on a thread; a hook test
    that runs the detached path would otherwise make it for real (and file
    the answer in the developer's own state dir). Tests of the follow-up
    itself re-enable it explicitly."""
    monkeypatch.setenv("MEDIA_FOLLOWUP", "0")


@pytest.fixture(autouse=True)
def _david_layout(monkeypatch):
    """The layout (agent_media_core/layout.py) pinned to David's, which is
    what these tests were written against: detection reads the real HOME
    (~/.amux, ~/.claude/settings.json), so unpinned they would pass on red5
    and fail anywhere else. test_layout.py unpins it where it needs to."""
    monkeypatch.setenv("MEDIA_LAYOUT", "projects-per-tmux-session")


@pytest.fixture(autouse=True)
def _not_headless(monkeypatch):
    """Keep a headless run's own identity out of the suite.

    A session the server started from the phone (media-sessiond) carries
    MEDIA_SOURCE_KIND=headless and its workspace, and the hook honours both —
    so running the suite from such a session filed every "outside tmux" stop
    under that workspace. A test that wants a headless session sets them.
    """
    monkeypatch.delenv("MEDIA_SOURCE_KIND", raising=False)
    monkeypatch.delenv("MEDIA_SOURCE_WORKSPACE", raising=False)


@pytest.fixture(autouse=True)
def _no_earcons(monkeypatch):
    """No tones from the suite (agent_media_core/earcons.py).

    An earcon is a real clip sent to a real player: a test that stops speech
    through the real SinkSpeech, or holds a reply behind the toast, would tick
    or chime on whatever broker the developer's env points at — the phone in
    his pocket, usually. Same hazard as the fixtures above. The earcon tests
    turn them back on for themselves, against a recording sink.
    """
    monkeypatch.setenv("MEDIA_EARCONS", "0")


@pytest.fixture(autouse=True)
def _no_live_jobs_server(monkeypatch, tmp_path_factory):
    """A device job (`media restart-services --host`, `media update --host`,
    `media doctor --fix`) asks this host's own canvas — by default
    127.0.0.1:8781 with the real ~/.amux token. On red5 that is the live
    server, and the job would pull and restart the real phone. Pointed
    nowhere; a test that wants a server sets MEDIA_PHONE_JOBS_URL itself.

    MEDIA_PHONE_JOBS goes too: the env file's `=1` turns every phone-side
    command (music_local.phone_argv) into a
    job for the phone's worker instead of ssh, so the ssh tests read config
    rather than code — they failed here and passed in CI. A test of the job
    route sets it itself."""
    monkeypatch.setenv("MEDIA_PHONE_JOBS_URL", "http://127.0.0.1:9")
    monkeypatch.delenv("MEDIA_PHONE_JOBS", raising=False)
    # A podcast link saved from the Media tab downloads through the phone
    # (podcast_links.start_fetch): never from a test.
    monkeypatch.setenv("MEDIA_PODCAST_LINK_FETCH", "0")
    # The book library reads its folder (book_library): never this
    # machine's own audiobooks (a slow mount on red5) — an empty one.
    monkeypatch.setenv("MEDIA_AUDIOBOOK_ABS_DIR", str(tmp_path_factory.mktemp("audiobooks")))
    monkeypatch.setenv("MEDIA_PODCAST_LINK_CHANNELS", "0")


@pytest.fixture(autouse=True)
def _no_music_bed_renderer(monkeypatch):
    """Never start the music bed's detached renderer (music_bed.py): it
    would outlive the test, spend a minute of CPU and fetch the weather.
    Patched rather than switched off by env, since many suites strip every
    MEDIA_* variable. A test of the spawn patches it back itself."""
    from agent_media_core import music_bed

    monkeypatch.setattr(music_bed, "spawn_warm", lambda: False)


@pytest.fixture(autouse=True)
def _no_music_bed_network(monkeypatch):
    """The music bed's other sources reach off the machine: the online
    library (music_bed_library.py) fetches from archive.org and ccMixter,
    and the streaming source (music_bed_stream.py) drives Mopidy — the house
    speakers' real queue and volume — and fetches tracks on the phone. Every
    one of those goes through a function patched here to refuse, so no test
    reaches the internet, the queue or the phone; a test of them fakes the
    one it needs."""
    from agent_media_core import music_bed_favourites, music_bed_library, music_bed_stream

    def refuse(*a, **k):
        raise OSError("no network in the tests (conftest)")

    monkeypatch.setattr(music_bed_library, "_http", refuse)
    monkeypatch.setattr(music_bed_library, "_download", refuse)
    monkeypatch.setattr(music_bed_stream, "_mpd", refuse)
    monkeypatch.setattr(music_bed_stream, "_fetch_local", lambda vid: None)
    monkeypatch.setattr(music_bed_stream, "_set_volume", refuse)
    # The favourites source (music_bed_favourites.py) reads ListenBrainz,
    # searches and looks up through Mopidy, fetches on the phone and runs
    # Demucs (under systemd-run): all four refused here.
    monkeypatch.setattr(music_bed_favourites, "_http", refuse)
    monkeypatch.setattr(music_bed_favourites, "_mpd", refuse)
    monkeypatch.setattr(music_bed_favourites, "_fetch_local", lambda vid: None)
    monkeypatch.setattr(music_bed_favourites, "_run", refuse)
    # A tune's distinctiveness is asked of a model (rate_renown): unrated.
    monkeypatch.setattr(music_bed_favourites, "_rate", lambda songs: {})
    # His YouTube accounts are read by an add-on, none here unless a test
    # registers one (music_accounts); Spotify over HTTP, refused.
    from agent_media_core import music_accounts
    monkeypatch.setattr(music_accounts, "_http", refuse)
    for key in [k for k in os.environ if k.startswith("MEDIA_MUSIC_BED_FAVOURITES")]:
        monkeypatch.delenv(key, raising=False)
    # A track's tempo is measured with ffmpeg (music_bed_tempo.measure): a
    # test of the slow rule gives its tracks their features itself.
    from agent_media_core import music_bed_tempo
    monkeypatch.setattr(music_bed_tempo, "measure", lambda *a, **k: {})


@pytest.fixture(autouse=True)
def _no_personal_prefs(monkeypatch):
    """The personal settings (personal_prefs.py) fall back to the env file's
    place, Astro Tunes switch and ListenBrainz account: none of the host's
    here, and no geocoder, forecast or astrotunes run. A test of them sets
    what it needs and fakes the one call it makes. With MEDIA_ASTRO gone,
    Astro Tunes is off, so astrotunes is never run."""
    from agent_media_core import personal_prefs

    def refuse(*a, **k):
        raise OSError("no network in the tests (conftest)")

    monkeypatch.setattr(personal_prefs, "_http_json", refuse)
    for key in [k for k in os.environ if k.startswith(("MEDIA_ASTRO", "MEDIA_LISTENBRAINZ"))]:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("MEDIA_MUSIC_BED_WEATHER", raising=False)


_REAL_OS_NAME = os.name


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """A failure is reported with the real os.name, whatever the test left.

    Tests of a Windows branch set os.name to "nt" with monkeypatch, which is
    undone only at teardown, after the call's report is made. On Python 3.11
    pathlib.Path() is a WindowsPath while os.name is "nt" and cannot be made
    off Windows, so a test failing with it set crashed pytest's own report
    (INTERNALERROR: cannot instantiate 'WindowsPath' on your system) and
    hid which test failed (CI, 28-29 Oct 2026)."""
    left = os.name
    os.name = _REAL_OS_NAME
    try:
        yield
    finally:
        os.name = left


@pytest.fixture(autouse=True)
def _no_practice_lookups(monkeypatch):
    """A practice's length is looked up with yt-dlp or ffprobe in the
    background (practice_library._fill_durations): never from a test."""
    monkeypatch.setenv("MEDIA_PRACTICE_DURATIONS", "0")
    monkeypatch.setenv("MEDIA_PRACTICE_PROFILES", "0")
    monkeypatch.setenv("MEDIA_FACTS_URL", "-")
    monkeypatch.setenv("MEDIA_PODCAST_RESOLVE", "0")
    monkeypatch.setenv("MEDIA_PEOPLE", "0")
    monkeypatch.setenv("MEDIA_MUSIC_CHANNEL", "0")
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
    # The person's own embed index (practice_embed) never joins a test's list.
    monkeypatch.delenv("MEDIA_PRACTICE_EMBED_INDEX", raising=False)


@pytest.fixture(autouse=True)
def _no_endpoints_or_schedule_from_the_env_file(monkeypatch):
    """Keep the suite from binding this host's real ports, or running its
    jobs.

    Both are new on 12 Nov 2026 and both are inherited-config hazards of
    exactly the kind this file exists for
    (docs/proposals/2026-11-12-fewer-services.md §4b, §4c):

    * `MEDIA_ENDPOINTS` names the small HTTP endpoints the server hosts in
      its own process. Inherited from ~/.config/agent-media.env, a test that
      calls `endpoints.start_all()` would bind the live speech-state, share
      and clip ports — on a working install, taking them from the running
      services or failing depending on who got there first.
    * `MEDIA_SCHEDULE` and the tick turn the scheduler thread on. A test that
      started it would run this host's real jobs: `state-gc` against the real
      state DB, `audio-prune` against the clips that are the only copy of
      what was said in that voice.

    A test that wants either sets its own, as test_endpoints.py and
    test_scheduler.py do.
    """
    monkeypatch.delenv("MEDIA_ENDPOINTS", raising=False)
    monkeypatch.setenv("MEDIA_SCHEDULE", "0")
    monkeypatch.delenv("MEDIA_SCHEDULE_TICK_S", raising=False)
    monkeypatch.delenv("MEDIA_AUDIO_KEEP_DAYS", raising=False)
    # The per-endpoint binds and ports, by prefix: a test must never be able
    # to reach, or displace, a listener the host is actually running.
    for prefix in ("MEDIA_CLIPS_", "MEDIA_SPEECH_STATE_", "MEDIA_SHARE_",
                   "MEDIA_SHIM_", "MEDIA_COMPLETIONS_"):
        for key in [k for k in os.environ if k.startswith(prefix)]:
            monkeypatch.delenv(key, raising=False)
