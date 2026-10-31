"""The music bed's fourth source: his favourites, instrumental (roadmap 39;
agent_media_core/music_bed_favourites.py). The match rules, where the
favourites come from, the official search, Demucs's guard (the window, the
nightly cap, the host's memory, failures), the cache, and the source in
music_bed's choice and Settings list. No network, Mopidy, phone or Demucs:
conftest refuses them all, and each test fakes the one it needs."""
import os
import shutil
import time
from datetime import datetime
from pathlib import Path

import pytest

from agent_media_core import music_bed as B
from agent_media_core import music_bed_favourites as F


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    for name in list(os.environ):
        if name.startswith(("MEDIA_", "RELAY_")):
            monkeypatch.delenv(name, raising=False)
    for name in ("HOME", "XDG_STATE_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME",
                 "XDG_DATA_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name))
    monkeypatch.setenv("MEDIA_MUSIC_BED_WEATHER", "off")
    # These pin what plays with every tempo allowed; slow only (on by
    # default) has its own tests (test_music_bed_slow.py).
    monkeypatch.setenv("MEDIA_MUSIC_BED_SLOW", "0")
    monkeypatch.setenv("MEDIA_AUDIO_DIR", str(tmp_path / "audio"))
    monkeypatch.setattr(F, "_host_mem", lambda: {"mem_available_mb": 6000,
                                                 "mem_stall_pct": 0.0})
    # ffmpeg is never run: a "normalised" bed is the source copied.
    monkeypatch.setattr(F, "_normalise", lambda src, dst: bool(shutil.copy(src, dst)))
    monkeypatch.setattr(F, "_cut", lambda src, dst: bool(shutil.copy(src, dst)))
    monkeypatch.setattr(F, "_measure", lambda src: {})


NIGHT = datetime(2026, 10, 31, 2, 30)
DAY = datetime(2026, 10, 31, 14, 0)


def _fav(key="k1", title="When I'm Small", artist="Phantogram", vid="28tZ-S1LFok", dur=250):
    return {"key": key, "artist": artist, "title": title, "id": vid, "dur": dur,
            "score": 5, "from": ["liked"]}


def _ready(tmp_path, key="k1", method="separated", **kw):
    """A ready bed as _add leaves one."""
    data = F.load()
    d = F._dir()
    d.mkdir(parents=True, exist_ok=True)
    name = f"music-bed-fav-{key}.mp3"
    (d / name).write_bytes(b"x" * kw.pop("size", 2000))
    data["tracks"].append({"key": key, "file": name, "title": kw.get("title", "Angel"),
                           "artist": kw.get("artist", "Massive Attack"), "method": method,
                           "original_id": "66A_3uwuZ_I", "made": "2026-10-31"})
    F.save(data)
    return d / name


def _listing(*songs):
    lines = []
    for s in songs:
        lines += [f"file: youtube:video:{s['id']}", f"Time: {s['dur']}",
                  f"Artist: {s['artist']}", f"Title: {s['title']}"]
    return "\n".join(lines) + "\n"


# ---- the match rules ---------------------------------------------------------------------

ORIG = {"title": "When I'm Small", "artist": "Phantogram", "dur": 250}


@pytest.mark.parametrize("title,artist,dur", [
    ("When I'm Small (Instrumental)", "Phantogram", 252),
    ("When I'm Small - Instrumental", "Phantogram", 240),
    ("When I'm Small (Karaoke Version) [Originally Performed by Phantogram]", "Sing King", 260),
    ("Phantogram - When I'm Small (Backing Track)", "Backing Tracks Ltd", 250),
    ("When I’m Small (Instrumental)", "PHANTOGRAM", 280),     # 12 %, curly quote
])
def test_a_confident_official_instrumental_is_taken(title, artist, dur):
    assert F.is_instrumental_of(ORIG, {"title": title, "artist": artist, "dur": dur})


@pytest.mark.parametrize("title,artist,dur", [
    ("When I'm Small", "Phantogram", 250),                              # not marked
    ("When I'm Small (Instrumental Cover)", "Phantogram", 250),         # a cover
    ("When I'm Small (RAC Remix) [Instrumental]", "Phantogram", 250),  # a remix
    ("When I'm Small (Instrumental)", "Phantogram", 300),               # 20 % longer
    ("When I'm Small (Instrumental)", "Someone Else", 250),             # no artist
    ("I Am So Small - Instrumental Track", "Genie", 250),               # other song
    ("When I'm Small (Instrumental) (Slowed)", "Phantogram", 250),
])
def test_anything_less_than_confident_is_refused(title, artist, dur):
    assert not F.is_instrumental_of(ORIG, {"title": title, "artist": artist, "dur": dur})


def test_without_the_original_length_nothing_is_confident():
    assert not F.is_instrumental_of({**ORIG, "dur": 0},
                                    {"title": "When I'm Small (Instrumental)",
                                     "artist": "Phantogram", "dur": 250})


@pytest.mark.parametrize("artist,title,want", [
    ("WolfmotherVEVO", "Wolfmother - Joker And The Thief (Audio)",
     ("Wolfmother", "Joker And The Thief")),
    ("Some Channel", 'Phantogram "When I\'m Small" [Official Video]',
     ("Phantogram", "When I'm Small")),
    ("Massive Attack - Topic", "Angel (Remastered 2019)", ("Massive Attack", "Angel")),
    ("Nils Frahm", "Says", ("Nils Frahm", "Says")),
])
def test_an_upload_is_named_as_the_song(artist, title, want):
    assert F.clean_original(artist, title) == want


# ---- where the favourites come from ---------------------------------------------------------

def test_favourites_are_his_likes_plays_and_listenbrainz(monkeypatch):
    monkeypatch.setenv("MEDIA_MUSIC_BED_FAVOURITES_LISTENBRAINZ_USER", "him")
    monkeypatch.setattr(F, "_from_store", lambda: {
        "66A_3uwuZ_I": {"likes": 1, "plays": 2},     # 7
        "28tZ-S1LFok": {"likes": 0, "plays": 3},     # 3
        "shortshort1": {"likes": 0, "plays": 9}})    # too short to be a bed
    known = {"66A_3uwuZ_I": {"id": "66A_3uwuZ_I", "dur": 382, "artist": "Massive Attack - Topic",
                             "title": "Angel (Remastered 2019)"},
             "28tZ-S1LFok": {"id": "28tZ-S1LFok", "dur": 252, "artist": "Phantogram",
                             "title": "When I'm Small"},
             "shortshort1": {"id": "shortshort1", "dur": 30, "artist": "X", "title": "Jingle"}}
    searched = []

    def mpd(line):
        if line.startswith("find file"):
            vid = line.split("youtube:video:")[1].rstrip('"')
            return [_listing(known[vid])]
        searched.append(line)
        return [_listing({"id": "luXZuPTJ3f8", "dur": 463, "artist": "LCD Soundsystem",
                          "title": "All My Friends (Instrumental)"},
                         {"id": "allmyfriend", "dur": 463, "artist": "LCD Soundsystem",
                          "title": "All My Friends"})]
    monkeypatch.setattr(F, "_mpd", mpd)

    def http(url, timeout=20.0):
        assert "/user/him/" in url
        if "/stats/" in url:
            return (b'{"payload": {"recordings": [{"artist_name": "LCD Soundsystem", '
                    b'"track_name": "All My Friends", "listen_count": 4}]}}')
        return (b'{"feedback": [{"track_metadata": {"artist_name": "Phantogram", '
                b'"track_name": "When I\'m Small"}}]}')
    monkeypatch.setattr(F, "_http", http)

    data = F.load()
    favs = F.gather(data)
    names = [(f["artist"], f["title"], f["id"]) for f in favs]
    # Phantogram: 3 plays + loved on ListenBrainz (5) = 8; Angel 7; LCD 4.
    assert names == [("Phantogram", "When I'm Small", "28tZ-S1LFok"),
                     ("Massive Attack", "Angel", "66A_3uwuZ_I"),
                     ("LCD Soundsystem", "All My Friends", "allmyfriend")]
    assert favs[0]["from"] == ["played", "listenbrainz-loved"]
    # ListenBrainz's song was found as the original, never its instrumental;
    # the one already known was not searched for.
    assert len(searched) == 1


def test_the_state_store_counts_likes_and_asked_for_plays(monkeypatch):
    class Store:
        def list_likes(self, limit, channel=None):
            assert channel == "music"
            return [{"media_id": "66A_3uwuZ_I"}]

        def recent_history(self, sink=None, limit=20, requested=False):
            assert sink == "music" and requested
            return [{"uri": "yt:https://www.youtube.com/watch?v=66A_3uwuZ_I"},
                    {"uri": "https://youtu.be/28tZ-S1LFok"},
                    {"uri": "/music/a.flac"}]
    import agent_media_core.state as state
    monkeypatch.setattr(state, "StateStore", Store)
    assert F._from_store() == {"66A_3uwuZ_I": {"likes": 1, "plays": 1},
                               "28tZ-S1LFok": {"likes": 0, "plays": 1}}


# ---- official instrumentals ---------------------------------------------------------------------

def test_an_official_instrumental_is_fetched_and_kept(monkeypatch, tmp_path):
    raw = tmp_path / "rooms" / "inst.mka"
    raw.parent.mkdir()
    raw.write_bytes(b"audio" * 400)
    monkeypatch.setattr(F, "_mpd", lambda line: [_listing(
        {"id": "instINSTins", "dur": 255, "artist": "Phantogram",
         "title": "When I'm Small (Instrumental)"})])
    fetched = []
    monkeypatch.setattr(F, "_fetch_local", lambda vid: fetched.append(vid) or str(raw))
    data = F.load()
    data["favourites"] = [_fav()]
    assert F.official(data, now=1000.0) == 1
    assert fetched == ["instINSTins"]
    (e,) = F.tracks("official")
    assert e["method"] == "official" and e["title"] == "When I'm Small"
    assert e["artist"] == "Phantogram" and e["made"]
    path, credit = F.track_for("auto")
    assert path.name == "music-bed-fav-k1.mp3" and path.parent == F._dir()
    assert credit["title"] == "Instrumental of When I'm Small"
    assert credit["creator"] == "Phantogram" and credit["license"] == "official"


def test_a_song_with_none_is_not_searched_again_for_a_month(monkeypatch):
    calls = []
    monkeypatch.setattr(F, "_mpd", lambda line: calls.append(line) or [_listing(
        {"id": "otherother1", "dur": 250, "artist": "Phantogram",
         "title": "When I'm Small (Chuck Brody Remix)"})])
    data = F.load()
    data["favourites"] = [_fav()]
    assert F.official(data, now=1000.0) == 0
    assert len(calls) == 2                      # instrumental, then karaoke
    assert data["state"]["k1"]["official"] == ""
    F.official(data, now=1000.0 + 86400 * 29)
    assert len(calls) == 2
    F.official(data, now=1000.0 + 86400 * 31)
    assert len(calls) == 4


# ---- removing the vocals --------------------------------------------------------------------------

@pytest.fixture
def demucs(monkeypatch, tmp_path):
    """A Demucs that 'works' (writes the stem) unless told to fail; counts runs."""
    exe = tmp_path / "demucs"
    exe.write_text("#!/bin/sh\n")
    monkeypatch.setenv("MEDIA_MUSIC_BED_FAVOURITES_DEMUCS", str(exe))
    monkeypatch.setenv("MEDIA_MUSIC_BED_FAVOURITES_GUARD", "none")
    src = tmp_path / "rooms" / "orig.mka"
    src.parent.mkdir(exist_ok=True)
    src.write_bytes(b"song" * 500)
    monkeypatch.setattr(F, "_fetch_local", lambda vid: str(src))
    state = {"runs": [], "fail": False}

    def run(cmd, timeout, env=None):
        state["runs"].append(cmd)
        assert env["OMP_NUM_THREADS"] == "2"
        if state["fail"]:
            return 137
        out = Path(cmd[cmd.index("-o") + 1]) / "htdemucs" / "original"
        out.mkdir(parents=True)
        (out / "no_vocals.wav").write_bytes(b"inst" * 500)
        return 0
    monkeypatch.setattr(F, "_run", run)
    return state


def _searched(data, *favs):
    data["favourites"] = list(favs)
    for f in favs:
        data["state"][f["key"]] = {"official_at": 1.0, "official": ""}
    return data


def test_vocals_are_removed_in_the_window(demucs):
    data = _searched(F.load(), _fav())
    assert F.separate(data, NIGHT) == 1
    (e,) = F.tracks("separated")
    assert e["method"] == "separated"
    assert F.credit(e)["license"] == "separated"
    assert not (F._work_dir() / "k1").exists()


def test_never_outside_the_window(demucs):
    data = _searched(F.load(), _fav())
    assert F.separate(data, DAY) == 0
    assert demucs["runs"] == []


def test_not_before_the_official_search_has_been_made(demucs):
    data = F.load()
    data["favourites"] = [_fav()]
    assert F.separate(data, NIGHT) == 0
    assert demucs["runs"] == []


def test_a_strained_host_is_skipped_and_said(demucs, monkeypatch):
    monkeypatch.setattr(F, "_host_mem", lambda: {"mem_available_mb": 5000,
                                                 "mem_stall_pct": 14.2})
    data = _searched(F.load(), _fav())
    assert F.separate(data, NIGHT) == 0
    assert demucs["runs"] == []
    assert "stalled 14.2 %" in data["skipped"]["reason"]
    monkeypatch.setattr(F, "_host_mem", lambda: {"mem_available_mb": 900,
                                                 "mem_stall_pct": 0.0})
    assert F.separate(data, NIGHT) == 0
    assert "900 MB" in data["skipped"]["reason"]
    assert demucs["runs"] == []
    assert "Last skipped: only 900 MB" in F.note("auto", NIGHT)


def test_failures_never_loop(demucs):
    demucs["fail"] = True
    favs = [_fav(key=f"k{i}", title=f"Song {i}") for i in range(6)]
    data = _searched(F.load(), *favs)
    assert F.separate(data, NIGHT) == 0
    assert len(demucs["runs"]) == 3                       # the night's cap
    assert F.separate(data, NIGHT.replace(hour=4)) == 0   # same night: nothing
    assert len(demucs["runs"]) == 3
    nxt = datetime(2026, 11, 1, 2, 10)
    F.separate(data, nxt)
    # Next night: the three not yet tried before the three that failed.
    assert len(demucs["runs"]) == 6
    assert all(data["state"][f"k{i}"]["fail"] == 1 for i in range(6))


def test_a_track_is_given_up_after_three_failures(demucs):
    demucs["fail"] = True
    data = _searched(F.load(), _fav())
    for day in (1, 2, 3, 4, 5):
        F.separate(data, datetime(2026, 11, day, 2, 30))
    assert len(demucs["runs"]) == F.MAX_FAILURES


def test_a_full_cache_makes_no_more(demucs, monkeypatch, tmp_path):
    monkeypatch.setenv("MEDIA_MUSIC_BED_FAVOURITES_MAX_MB", "0.001")
    _ready(tmp_path, key="old", size=4000)
    data = _searched(F.load(), _fav())
    assert F.separate(data, NIGHT) == 0
    assert demucs["runs"] == []


def test_demucs_runs_niced_ioniced_and_under_a_ceiling(monkeypatch, tmp_path):
    monkeypatch.setenv("MEDIA_MUSIC_BED_FAVOURITES_MEMORY_MAX_MB", "2600")
    monkeypatch.setattr(F.shutil, "which", lambda name: f"/usr/bin/{name}")
    cmd = F.demucs_cmd("/venv/bin/demucs", tmp_path / "a.wav", tmp_path / "out")
    assert cmd[:4] == ["systemd-run", "--user", "--scope", "--quiet"]
    assert "MemoryMax=2600M" in cmd and "MemorySwapMax=0" in cmd
    i = cmd.index("nice")
    assert cmd[i:i + 6] == ["nice", "-n", "19", "ionice", "-c", "3"]
    assert cmd[i + 6] == "/venv/bin/demucs"
    assert ["--two-stems", "vocals"] == cmd[i + 7:i + 9]
    assert cmd[cmd.index("-n", i + 7) + 1] == "htdemucs"
    assert cmd[cmd.index("-j") + 1] == "1"


def test_without_demucs_only_official_ones(monkeypatch):
    monkeypatch.setattr(F.shutil, "which", lambda name: None)
    assert F.demucs_exe() is None
    assert "Demucs is not installed" in F.why_not_separate()


@pytest.mark.parametrize("when,inside", [
    (datetime(2026, 10, 31, 1, 59), False), (datetime(2026, 10, 31, 2, 0), True),
    (datetime(2026, 10, 31, 4, 59), True), (datetime(2026, 10, 31, 5, 0), False)])
def test_the_window(when, inside):
    assert F.in_window(when) is inside


def test_a_window_across_midnight(monkeypatch):
    monkeypatch.setenv("MEDIA_MUSIC_BED_FAVOURITES_WINDOW", "23:30-01:00")
    assert F.in_window(datetime(2026, 10, 31, 23, 45))
    assert F.in_window(datetime(2026, 11, 1, 0, 30))
    assert not F.in_window(datetime(2026, 11, 1, 1, 0))
    assert F.night_of(datetime(2026, 11, 1, 0, 30)) == F.night_of(datetime(2026, 10, 31, 23, 45))
    assert F.next_window(datetime(2026, 10, 31, 12, 0)) == datetime(2026, 10, 31, 23, 30)


# ---- the source in the music bed ------------------------------------------------------------------

def test_the_choice_takes_the_favourites():
    assert B.normalise("favourites") == "favourites:auto"
    assert B.parse("favourites:separated") == ("favourites", "separated")
    assert B.normalise("favourites:jazz") is None
    assert B.compose("favourites", "official") == "favourites:official"
    p = B.pick_for("favourites:auto", when=datetime(2026, 10, 31, 18, 0), weather=None)
    assert p == B.Pick("favourites", "auto", "dusk")


def test_a_ready_track_plays_and_none_falls_back(monkeypatch, tmp_path):
    def ready(style):
        p = tmp_path / f"music-bed-{style}-v{B.VERSION}.wav"
        p.write_bytes(b"\0" * 64)
        return p
    monkeypatch.setattr(B, "ready", ready)
    pick = B.Pick("favourites", "auto", "dusk")
    plan = B.choose(pick, other_audio=False)
    assert plan.source == "generated" and plan.style == "dusk"
    _ready(tmp_path, key="a", method="separated", title="Angel", artist="Massive Attack")
    plan = B.choose(pick, other_audio=False)
    assert plan.kind == "under" and plan.source == "favourites"
    assert plan.path.name == "music-bed-fav-a.mp3"
    assert plan.credit["title"] == "Instrumental of Angel"
    assert plan.credit["creator"] == "Massive Attack"
    assert plan.credit["license"] == "separated"
    # Only the official ones: none, so the stand-in.
    plan = B.choose(B.Pick("favourites", "official", "dusk"), other_audio=False)
    assert plan.source == "generated"
    assert B.choose(pick, other_audio=None) is None


def test_settings_lists_the_source_with_a_note(monkeypatch, tmp_path):
    B.set_choice("favourites:auto")
    _ready(tmp_path, key="a", method="official")
    st = B.status()
    assert st["source"] == "favourites" and st["style"] == "auto"
    row = next(s for s in st["sources"] if s["id"] == "favourites")
    assert row["label"] == "Your favourites, instrumental"
    assert [s["id"] for s in row["styles"]] == ["auto", "official", "separated"]
    assert row["available"] is True
    assert row["note"].startswith("1 ready (1 official, 0 with the vocals removed).")
    assert st["favourites"] == {"official": 1, "separated": 0}
    assert st["auto_style"] is None
    assert {"favourites:auto", "favourites:official", "favourites:separated"} <= {
        c["id"] for c in st["choices"]}


def test_switched_off_says_so(monkeypatch):
    monkeypatch.setenv("MEDIA_MUSIC_BED_FAVOURITES", "0")
    row = next(s for s in B.status()["sources"] if s["id"] == "favourites")
    assert row["available"] is False and "MEDIA_MUSIC_BED_FAVOURITES=0" in row["reason"]


def test_the_note_names_the_next_window(monkeypatch, tmp_path):
    exe = tmp_path / "demucs"
    exe.write_text("")
    monkeypatch.setenv("MEDIA_MUSIC_BED_FAVOURITES_DEMUCS", str(exe))
    monkeypatch.setenv("MEDIA_MUSIC_BED_FAVOURITES_GUARD", "none")
    n = F.note("auto", DAY)
    assert "None ready yet" in n
    assert "02:00–05:00 (next: Sun 02:00), up to 3 a night" in n


def test_nothing_is_prepared_unless_chosen(monkeypatch, capsys):
    called = []
    monkeypatch.setattr(F, "fill", lambda **k: called.append(1) or 0)
    assert F.main(["x", "night"]) == 0
    assert called == []
    B.set_choice("favourites:auto")
    assert F.chosen()


def test_the_background_pass_is_spaced(monkeypatch):
    monkeypatch.setattr(F, "gather", lambda data, now=None: data.update(
        favourites=[_fav()], favourites_at=now) or data["favourites"])
    searched = []
    monkeypatch.setattr(F, "official", lambda data, now=None: searched.append(now) or 0)
    F.fill(now=1000.0, when=DAY)
    F.fill(now=2000.0, when=DAY)
    assert searched == [1000.0]
    assert not F.needs(now=2000.0, when=DAY)
    assert F.needs(now=1000.0 + F.PASS_GAP_S, when=DAY)


# ---- Auto by astrotunes' reading (David, 30 Oct 2026: "The music picker should
# probably also use Astro Tunes") ----------------------------------------------------------

def _reading(at=None, **axes):
    import json
    full = {k: {"value": v, "label": k, "amplified": False} for k, v in axes.items()}
    p = B._astro_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"at": time.time() if at is None else at,
                             "moment": {"axes": full, "themes": ["Awakening"]}}))


@pytest.mark.parametrize("axes,hour,mood,genre", [
    (dict(energy=0.3, tempo=0.3, warmth=0.7, brightness=0.4, texture=0.4), 15, "dusk", "piano"),
    (dict(energy=0.3, tempo=0.3, warmth=0.4, brightness=0.4, texture=0.4), 15, "night", "ambient"),
    (dict(energy=0.8, tempo=0.7, warmth=0.5, brightness=0.7, texture=0.4), 9, "morning", "lofi"),
    (dict(energy=0.5, tempo=0.5, warmth=0.5, brightness=0.4, texture=0.8), 12, "rainy", "ambient"),
    (dict(energy=0.5, tempo=0.5, warmth=0.7, brightness=0.4, texture=0.4), 12, "blend", "jazz"),
    (dict(energy=0.5, tempo=0.5, warmth=0.4, brightness=0.4, texture=0.4), 12, "calm", "piano"),
])
def test_the_axes_map_to_a_mood_and_a_genre(axes, hour, mood, genre):
    got = B.astro_auto(axes, hour)
    assert (got.mood, got.genre) == (mood, genre)
    assert got.why.startswith(f"{mood} — ") and got.why.endswith("(astrotunes)")


def test_late_at_night_nothing_bright_or_energetic():
    lively = dict(energy=0.9, tempo=0.9, warmth=0.7, brightness=0.9, texture=0.3)
    got = B.astro_auto(lively, 23)
    assert (got.mood, got.genre) == ("dusk", "piano")
    assert "late, so kept low" in got.why
    got = B.astro_auto({**lively, "warmth": 0.3}, 3)
    assert got.mood == "night"
    assert B.astro_auto(lively, 10).mood == "morning"


def test_a_fresh_reading_drives_auto_for_every_source():
    _reading(energy=0.3, tempo=0.3, warmth=0.7, brightness=0.4, texture=0.3)
    when = datetime(2026, 10, 31, 9, 0)     # the hour alone would say morning
    assert B.mood_for(when, weather=None, pick="auto") == "dusk"
    assert B.pick_for("auto", when, weather=None) == B.Pick("generated", "dusk", "dusk")
    assert B.pick_for("library:auto", when, weather=None) == B.Pick("library", "piano", "dusk")
    assert B.pick_for("streaming:auto", when, weather=None).style == "piano"
    st = B.status()
    assert st["astro"]["energy"] == 0.3
    assert st["auto_why"] == "auto: dusk — low energy, warm (astrotunes)"
    B.set_choice("library:auto")
    assert B.status()["auto_why"] == "auto: piano — low energy, warm (astrotunes)"


def test_a_stale_reading_or_none_falls_back_to_the_clock(monkeypatch):
    when = datetime(2026, 10, 31, 9, 0)
    assert B.mood_for(when, weather=None, pick="auto") == "morning"
    _reading(at=time.time() - 3600, energy=0.3, warmth=0.7)
    assert B.mood_for(when, weather=None, pick="auto") == "morning"
    assert B.auto_now(when, weather=None).why == "morning — by the hour (6–11)"
    _reading(energy=0.3, warmth=0.7)
    monkeypatch.setenv("MEDIA_MUSIC_BED_ASTRO", "off")
    assert B.mood_for(when, weather=None, pick="auto") == "morning"


def test_a_reply_never_runs_astrotunes(monkeypatch):
    import subprocess
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("ran astrotunes"))
    assert B.astro_reading() is None
    B.auto_now(datetime(2026, 10, 31, 9, 0), weather=None)


def test_favourites_lean_to_the_reading(tmp_path, monkeypatch):
    for key, t, e in (("slow", 0.1, 0.2), ("fast", 0.9, 0.9)):
        _ready(tmp_path, key=key)
        data = F.load()
        data["tracks"][-1]["features"] = {"tempo": t, "energy": e}
        F.save(data)
    calm = {"tempo": 0.15, "energy": 0.2}
    picks = [F.track_for("auto", calm, DAY)[0].name for _ in range(200)]
    assert picks.count("music-bed-fav-slow.mp3") > 150
    assert picks.count("music-bed-fav-fast.mp3") > 0          # not always
    # Late, even an energetic reading leans to the slow one.
    lively = {"tempo": 0.9, "energy": 0.9}
    late = [F.track_for("auto", lively, datetime(2026, 10, 31, 23, 0))[0].name
            for _ in range(200)]
    assert late.count("music-bed-fav-slow.mp3") > 100
    day = [F.track_for("auto", lively, DAY)[0].name for _ in range(200)]
    assert day.count("music-bed-fav-fast.mp3") > 150


def test_the_tempo_of_a_beat():
    rate = 100
    env = [1.0 if i % 50 == 0 else 0.0 for i in range(rate * 30)]   # 120 BPM
    assert F.bpm_of(env, rate) == 120.0


def test_a_karaoke_version_is_credited_as_karaoke(monkeypatch, tmp_path):
    raw = tmp_path / "k.mka"
    raw.write_bytes(b"audio" * 400)
    monkeypatch.setattr(F, "_mpd", lambda line: [_listing(
        {"id": "karaokeKARA", "dur": 250, "artist": "Ameritz Countdown Karaoke",
         "title": "When I'm Small (In the Style of Phantogram) [Karaoke Version]"})])
    monkeypatch.setattr(F, "_fetch_local", lambda vid: str(raw))
    data = F.load()
    data["favourites"] = [_fav()]
    assert F.official(data, now=1000.0) == 1
    assert F.track_for("official")[1]["license"] == "karaoke"


def test_karaoke_can_be_refused(monkeypatch):
    monkeypatch.setenv("MEDIA_MUSIC_BED_FAVOURITES_KARAOKE", "0")
    k = {"title": "When I'm Small (Karaoke Version) [Originally Performed by Phantogram]",
         "artist": "Sing King", "dur": 250}
    assert not F.is_instrumental_of(ORIG, k)
    assert F.is_instrumental_of(ORIG, {**k, "title": "When I'm Small (Instrumental)",
                                       "artist": "Phantogram"})


# ---- his verdict on a song sung out (POST /speech/music-bed/song) ---------------------------

def test_a_like_is_counted_at_the_next_gather(tmp_path):
    from agent_media_core.state import StateStore
    _ready(tmp_path, key="massiveattackangel", title="Angel")
    assert F.verdict("massiveattackangel", "like") == {"key": "massiveattackangel",
                                                       "verdict": "like"}
    (row,) = StateStore().list_likes(channel="music")
    assert row["media_id"] == "66A_3uwuZ_I"
    # The gather reads it as five points for that video.
    assert F._from_store()["66A_3uwuZ_I"]["likes"] == 1


def test_a_dislike_is_gone_for_good(monkeypatch, tmp_path):
    from agent_media_core import music_bed_vocal as V
    bed = _ready(tmp_path, key="massiveattackangel", title="Angel")
    V._dir().mkdir(parents=True, exist_ok=True)
    (V._dir() / "music-bed-vocal-massiveattackangel.mp3").write_bytes(b"x" * 2000)
    V.save({"massiveattackangel": {"key": "massiveattackangel",
                                   "file": "music-bed-vocal-massiveattackangel.mp3"}})
    data = F.load()
    data["favourites"] = [_fav("massiveattackangel", "Angel", "Massive Attack", "66A_3uwuZ_I")]
    F.save(data)

    F.verdict("massiveattackangel", "dislike")
    assert not bed.exists() and F.tracks() == [] and F.load()["favourites"] == []
    assert V.load() == {}
    assert not (V._dir() / "music-bed-vocal-massiveattackangel.mp3").exists()

    # Liked and played as much as ever: a later gather never takes it again.
    monkeypatch.setattr(F, "_from_store", lambda: {"66A_3uwuZ_I": {"likes": 3, "plays": 9}})
    monkeypatch.setattr(F, "lookup", lambda vid, data=None: {
        "id": vid, "artist": "Massive Attack", "title": "Angel", "dur": 382})
    assert F.gather(F.load()) == []
    # Nor under another name for the same video.
    monkeypatch.setattr(F, "lookup", lambda vid, data=None: {
        "id": vid, "artist": "Massive Attack", "title": "Angel (Remastered)", "dur": 382})
    assert F.gather(F.load()) == []
    # Even a track entry written back by a pass that loaded the index before.
    _ready(tmp_path, key="massiveattackangel", title="Angel")
    assert F.tracks() == []


def test_an_unknown_key_or_verdict_is_refused(tmp_path):
    _ready(tmp_path, key="k1")
    with pytest.raises(ValueError):
        F.verdict("nope", "like")
    with pytest.raises(ValueError):
        F.verdict("k1", "love")
