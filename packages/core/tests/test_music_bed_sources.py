"""The music bed's three sources (roadmap 39; agent_media_core/music_bed.py,
music_bed_styles.py, music_bed_library.py, music_bed_stream.py): the choice,
the fallback order, the licence filter, the cache cap, Mopidy under a
reading, the new styles' loops. No network: conftest refuses every request,
and each test fakes the one it needs."""
import array
import json
import math
import os
import time
import wave
from datetime import datetime
from pathlib import Path

import pytest

from agent_media_core import music_bed as B
from agent_media_core import music_bed_library as L
from agent_media_core import music_bed_stream as T
from agent_media_core import music_bed_styles as ST
from agent_media_core.types import Target


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    for name in list(os.environ):
        if name.startswith(("MEDIA_", "RELAY_")):
            monkeypatch.delenv(name, raising=False)
    for name in ("HOME", "XDG_STATE_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name))
    monkeypatch.setenv("MEDIA_MUSIC_BED_WEATHER", "off")
    monkeypatch.setenv("MEDIA_AUDIO_DIR", str(tmp_path / "audio"))


def _fake_generated(monkeypatch, tmp_path, only=None):
    def ready(style):
        if only is not None and style not in only:
            return None
        p = tmp_path / f"music-bed-{style}-v{B.VERSION}.wav"
        p.write_bytes(b"\0" * 64)
        return p
    monkeypatch.setattr(B, "ready", ready)


def _cache_track(genre, key, mtime=None, size=1000, **extra):
    """A library track as fill() leaves one: a file and an index entry."""
    d = L._dir()
    d.mkdir(parents=True, exist_ok=True)
    name = f"music-bed-lib-{key}.mp3"
    (d / name).write_bytes(b"x" * size)
    if mtime is not None:
        os.utime(d / name, (mtime, mtime))
    data = L.load()
    data["tracks"].append({"key": key, "genre": genre, "file": name,
                           "title": extra.get("title", key), "creator": "Kevin MacLeod",
                           "license": "CC BY 3.0", "license_id": "cc-by",
                           "license_url": "http://creativecommons.org/licenses/by/3.0/",
                           "url": "https://archive.org/details/x", "source": "archive.org"})
    L.save(data)
    return d / name


# ---- the choice ---------------------------------------------------------------------

def test_old_values_still_mean_the_generated_source():
    for old in ("off", "auto", "calm", "dusk", "blend", "morning", "rainy", "night"):
        assert B.normalise(old) == old
    assert B.parse("dusk") == ("generated", "dusk")
    assert B.parse("auto") == ("generated", "auto")
    assert B.parse("off") is None
    assert B.normalise("generated:jazz") == "jazz"
    assert B.normalise("generated") == "auto"
    assert B.normalise("library") == "library:auto"
    assert B.parse("streaming:lofi") == ("streaming", "lofi")
    assert B.normalise("library:nature") is None      # nature is generated only
    assert B.normalise("classical") is None           # classical is not generated
    assert B.compose("library", "jazz") == "library:jazz"
    assert B.compose("generated", "auto") == "auto"
    assert B.compose("off") == "off"
    assert B.compose("radio") is None


def test_a_stored_mood_keeps_working(monkeypatch):
    B._settings_path().parent.mkdir(parents=True, exist_ok=True)
    B._settings_path().write_text(json.dumps({"choice": "night", "at": 1}))
    assert B.choice() == "night"
    assert B.pick_for(when=datetime(2026, 10, 26, 9)) == B.Pick("generated", "night", "night")


def test_the_source_env(monkeypatch):
    monkeypatch.setenv("MEDIA_MUSIC_BED_SOURCE", "library")
    assert B.choice() == "library:auto"
    monkeypatch.setenv("MEDIA_MUSIC_BED", "jazz")
    assert B.choice() == "library:jazz"
    monkeypatch.setenv("MEDIA_MUSIC_BED", "nature")      # not a library genre
    assert B.choice() == "library:auto"
    monkeypatch.setenv("MEDIA_MUSIC_BED", "off")
    assert B.choice() == "off"
    monkeypatch.setenv("MEDIA_MUSIC_BED", "streaming:piano")
    assert B.choice() == "streaming:piano"


@pytest.mark.parametrize("hour,genre,mood", [
    (8, "classical", "morning"), (12, "piano", "calm"), (15, "lofi", "blend"),
    (19, "jazz", "dusk"), (23, "ambient", "night")])
def test_auto_for_the_other_sources_is_a_genre_by_the_hour(hour, genre, mood):
    when = datetime(2026, 10, 26, hour)
    for src in ("library", "streaming"):
        assert B.pick_for(f"{src}:auto", when=when, weather=None) == B.Pick(src, genre, mood)


def test_a_fixed_genre_falls_back_to_its_nearest_generated_style():
    assert B.pick_for("library:classical") == B.Pick("library", "classical", "piano")
    assert B.pick_for("streaming:lofi") == B.Pick("streaming", "lofi", "lofi")
    assert B.pick_for("lofi") == B.Pick("generated", "lofi", "lofi")


# ---- the fallback order -------------------------------------------------------------------

def test_library_first_then_generated_then_nothing(monkeypatch, tmp_path):
    pick = B.Pick("library", "jazz", "jazz")
    _fake_generated(monkeypatch, tmp_path)
    plan = B.choose(pick, False, Target("local"))
    assert plan.source == "generated" and plan.path.name == f"music-bed-jazz-v{B.VERSION}.wav"
    p = _cache_track("jazz", "ia-aaa", title="Acid Trumpet")
    plan = B.choose(pick, False, Target("local"))
    assert plan.kind == "under" and plan.source == "library" and plan.path == p
    assert plan.credit["title"] == "Acid Trumpet" and plan.credit["license"] == "CC BY 3.0"
    # Another genre's track is not this one's.
    assert B.choose(B.Pick("library", "piano", "piano"), False).source == "generated"
    _fake_generated(monkeypatch, tmp_path, only=())
    assert B.choose(B.Pick("library", "piano", "piano"), False) is None
    # Never over other audio, or when it is not known.
    assert B.choose(pick, True) is None and B.choose(pick, None) is None


def test_the_library_plays_on_a_phone_too(monkeypatch, tmp_path):
    monkeypatch.setenv("MEDIA_SPEECH_SOCKET_SASONICA", "tcp://127.0.0.1:9")
    _cache_track("piano", "ia-bbb")
    assert B.choose(B.Pick("library", "piano", "piano"), False, Target("sasonica")).source == "library"


def test_streaming_is_not_for_a_phone(monkeypatch, tmp_path):
    monkeypatch.setenv("MEDIA_SPEECH_SOCKET_SASONICA", "tcp://127.0.0.1:9")
    assert T.available("sasonica") == T.PHONE_WHY
    assert T.available("rooms") is None and T.available("local") is None
    monkeypatch.setenv("MEDIA_MUSIC_BED_STREAM_TARGETS", "rooms")
    assert "MEDIA_MUSIC_BED_STREAM_TARGETS" in T.available("local")
    monkeypatch.delenv("MEDIA_MUSIC_BED_STREAM_TARGETS")
    _fake_generated(monkeypatch, tmp_path)
    song = tmp_path / "song.mka"
    song.write_bytes(b"x")
    T.save({"tracks": [{"id": "abcdefghijk", "title": "Late Jazz", "artist": "Trio",
                        "genre": "jazz", "file": str(song)}], "tried": {}})
    pick = B.Pick("streaming", "jazz", "jazz")
    assert B.choose(pick, False, Target("sasonica")).source == "generated"
    plan = B.choose(pick, False, Target("rooms"))
    assert plan.kind == "stream" and plan.tracks[0]["title"] == "Late Jazz"
    assert plan.credit["creator"] == "Trio" and plan.credit["license"] == ""


def test_ready_or_warm_starts_the_background_for_an_empty_source(monkeypatch, tmp_path):
    started = []
    monkeypatch.setattr(B, "spawn_warm", lambda: started.append(1) or True)
    _fake_generated(monkeypatch, tmp_path)
    # Nothing cached: the generated stand-in is what there is, and the
    # background process is asked to fetch.
    assert B.ready_or_warm(B.Pick("library", "jazz", "jazz"), Target("local"))
    assert started
    started.clear()
    _cache_track("jazz", "ia-ccc")
    assert B.ready_or_warm(B.Pick("library", "jazz", "jazz"), Target("local")) is True
    assert not started


# ---- the licence filter ------------------------------------------------------------------------

@pytest.mark.parametrize("url,label", [
    ("http://creativecommons.org/licenses/by/3.0/", "CC BY 3.0"),
    ("https://creativecommons.org/licenses/by/4.0/", "CC BY 4.0"),
    ("http://creativecommons.org/publicdomain/zero/1.0/", "CC0"),
    ("http://creativecommons.org/publicdomain/mark/1.0/", "Public domain"),
    ("http://creativecommons.org/licenses/publicdomain/", "Public domain")])
def test_licences_that_allow_any_use(url, label):
    assert L.licence(url)["label"] == label


@pytest.mark.parametrize("url", [
    "http://creativecommons.org/licenses/by-nc/3.0/",
    "http://creativecommons.org/licenses/by-nd/3.0/",
    "http://creativecommons.org/licenses/by-nc-nd/4.0/",
    "http://creativecommons.org/licenses/by-nc-sa/4.0/",
    "http://creativecommons.org/licenses/by-sa/4.0/",
    "https://example.com/licence", "", None])
def test_nc_nd_sa_and_none_are_refused(url):
    assert L.licence(url) is None


def _fake_ia(monkeypatch, items, search=None):
    """archive.org, faked: `items` maps an identifier to its metadata."""
    seen = []

    def fake_json(url):
        seen.append(url)
        if url.startswith(L.IA_META):
            return items[url[len(L.IA_META):]]
        if url.startswith(L.IA_SEARCH):
            return {"response": {"docs": search if search is not None else [
                {"identifier": k, "licenseurl": "http://creativecommons.org/licenses/by/3.0/"}
                for k in items]}}
        raise AssertionError(url)
    monkeypatch.setattr(L, "_json", fake_json)
    return seen


def _item(lic, files=None, title="Jazz Sampler", subject=None):
    return {"metadata": {"title": title, "creator": "Kevin MacLeod", "licenseurl": lic,
                         "subject": subject},
            "files": files if files is not None else [
                {"name": "Kevin_MacLeod_-_AcidJazz.mp3", "format": "VBR MP3",
                 "length": "212.12", "title": "AcidJazz"},
                {"name": "Kevin_MacLeod_-_AcidJazz.ogg", "format": "Ogg Vorbis",
                 "length": "204.49"},
                {"name": "Kevin_MacLeod_-_Corncob.mp3", "format": "VBR MP3",
                 "length": "55.53", "title": "Corncob"},
                {"name": "Long.mp3", "format": "VBR MP3", "length": "12:40",
                 "title": "Allegro"},
                {"name": "Aria.mp3", "format": "VBR MP3", "length": "300",
                 "title": "Aria for soprano"}]}


def test_an_item_is_judged_by_its_own_metadata(monkeypatch):
    # The search said CC BY; the item itself says NC: refused.
    _fake_ia(monkeypatch, {
        "nc-item": _item("http://creativecommons.org/licenses/by-nc/3.0/"),
        "nd-item": _item("http://creativecommons.org/licenses/by-nd/4.0/"),
        "no-licence": _item(None),
        "Jazz_Sampler-9619": _item("http://creativecommons.org/licenses/by/3.0/")})
    got = L.search_ia("jazz")
    assert {c["url"] for c in got} == {"https://archive.org/details/Jazz_Sampler-9619"}
    # Too short, an ogg, and a sung title are skipped; m:ss lengths are read.
    assert sorted(c["title"] for c in got) == ["AcidJazz", "Allegro"]
    c = got[0]
    assert c["license"] == "CC BY 3.0" and c["creator"] == "Kevin MacLeod"
    assert c["license_url"] == "http://creativecommons.org/licenses/by/3.0/"
    assert c["download"].startswith("https://archive.org/download/Jazz_Sampler-9619/")


def test_a_sung_or_spoken_item_is_skipped(monkeypatch):
    _fake_ia(monkeypatch, {"opera": _item("http://creativecommons.org/publicdomain/mark/1.0/",
                                          title="The Marriage of Figaro (opera)"),
                           "book": _item("http://creativecommons.org/publicdomain/mark/1.0/",
                                         title="Gatsby", subject=["librivox", "audiobook"])})
    assert L.search_ia("classical") == []


def test_the_search_licence_is_a_first_sieve(monkeypatch):
    seen = _fake_ia(monkeypatch, {"x": _item("http://creativecommons.org/licenses/by/3.0/")},
                    search=[{"identifier": "x",
                             "licenseurl": "http://creativecommons.org/licenses/by-nc/3.0/"}])
    assert L.search_ia("jazz") == []
    assert not any(u.startswith(L.IA_META) for u in seen)   # never even fetched


def test_ccmixter_takes_cc_by_instrumentals_only(monkeypatch):
    def fake_json(url):
        assert url.startswith(L.CC_QUERY) and "lic=by" in url and "instrumental" in url
        def it(lic, tags=",instrumental,chill,", name="Drops", ps="5:15"):
            return {"upload_name": name, "user_name": "djlang59", "license_url": lic,
                    "upload_tags": tags, "file_page_url": "https://ccmixter.org/files/djlang59/1",
                    "files": [{"download_url": f"https://ccmixter.org/content/{name}.mp3",
                               "file_format_info": {"ps": ps}}]}
        return [it("http://creativecommons.org/licenses/by/3.0/"),
                it("http://creativecommons.org/licenses/by-nc/3.0/", name="NC"),
                it("http://creativecommons.org/licenses/by/3.0/", tags=",vocals,", name="Sung"),
                it("http://creativecommons.org/licenses/by/3.0/", name="Short", ps="0:41")]
    monkeypatch.setattr(L, "_json", fake_json)
    got = L.search_ccmixter("lofi")
    assert [c["title"] for c in got] == ["Drops"]
    assert got[0]["license"] == "CC BY 3.0" and got[0]["creator"] == "djlang59"
    assert got[0]["url"] == "https://ccmixter.org/files/djlang59/1"
    assert L.search_ccmixter("classical") == []          # no ccMixter tags for it


# ---- fetching and the cache ------------------------------------------------------------------------

def test_fill_keeps_the_credit_and_backs_off(monkeypatch, tmp_path):
    cands = [{"key": f"ia-{i:03d}", "genre": "jazz", "download": f"https://archive.org/download/x/{i}.mp3",
              "title": f"Track {i}", "creator": "Kevin MacLeod", "album": "Jazz Sampler",
              "license": "CC BY 3.0", "license_id": "cc-by",
              "license_url": "http://creativecommons.org/licenses/by/3.0/",
              "url": "https://archive.org/details/x", "source": "archive.org", "seconds": 200}
             for i in range(5)]
    monkeypatch.setattr(L, "search_ia", lambda g: cands[:2])
    monkeypatch.setattr(L, "search_ccmixter", lambda g: [])
    monkeypatch.setattr(L, "_download", lambda url, dest, referer=None: Path(dest).write_bytes(b"raw"))
    made = []

    def norm(src, dst):
        made.append(Path(dst).name)
        Path(dst).write_bytes(b"y" * 2048)
        return True
    monkeypatch.setattr(L, "normalise", norm)
    assert L.needs("jazz")
    assert L.fill(["jazz"]) == 2
    got = L.tracks("jazz")
    assert len(got) == 2 and all(e["license"] == "CC BY 3.0" for e in got)
    assert all(n.startswith("music-bed-lib-ia-") and n.endswith(".mp3") for n in made)
    assert {e["title"] for e in got} == {"Track 0", "Track 1"}
    # Short of three, but just tried: not again for six hours.
    assert not L.needs("jazz")
    assert L.needs("jazz", now=time.time() + 7 * 3600)
    # And its credit is what a reading's plan carries.
    path, credit = L.track_for("jazz")
    assert credit["title"].startswith("Track") and credit["creator"] == "Kevin MacLeod"
    assert path.parent == L._dir()


def test_without_the_network_fill_finds_nothing_and_does_not_raise():
    assert L.fill(["jazz", "lofi"]) == 0      # conftest refuses every request
    assert L.counts()["jazz"] == 0


def test_the_cache_cap_drops_the_least_recently_played(monkeypatch):
    now = time.time()
    old = _cache_track("jazz", "ia-old", mtime=now - 300, size=4000)
    mid = _cache_track("piano", "ia-mid", mtime=now - 200, size=4000)
    new = _cache_track("lofi", "ia-new", mtime=now - 100, size=4000)
    data = L.prune(L.load(), cap=9000)
    assert not old.exists() and mid.exists() and new.exists()
    assert [e["key"] for e in data["tracks"]] == ["ia-mid", "ia-new"]
    # Playing one marks it recent, so the other goes next.
    L.save(data)
    os.utime(mid)
    L.prune(L.load(), cap=5000)
    assert mid.exists() and not new.exists()
    monkeypatch.setenv("MEDIA_MUSIC_BED_LIBRARY_MAX_MB", "0.001")
    assert L.max_bytes() == 1048


def test_track_for_rotates(monkeypatch):
    now = time.time()
    _cache_track("jazz", "ia-1", mtime=now - 50)
    _cache_track("jazz", "ia-2", mtime=now - 40)
    first = L.track_for("jazz")[0].name
    second = L.track_for("jazz")[0].name
    assert first == "music-bed-lib-ia-1.mp3" and second == "music-bed-lib-ia-2.mp3"


# ---- streaming through Mopidy ----------------------------------------------------------------------------

class FakeMopidy:
    def __init__(self, state="stop", queue=("local:track:his.mp3",), volume=80, random="0"):
        self.state, self.queue, self.volume, self.random = state, list(queue), volume, random
        self.lines = []

    def __call__(self, *lines):
        out = []
        for line in lines:
            self.lines.append(line)
            if line == "status":
                out.append(f"volume: {self.volume}\nrandom: {self.random}\n"
                           f"playlistlength: {len(self.queue)}\nstate: {self.state}\n")
            elif line.startswith("add "):
                self.queue.append(line[5:-1])
                out.append("")
            elif line.startswith("play "):
                self.state = "play"
                out.append("")
            elif line == "stop":
                self.state = "stop"
                out.append("")
            elif line == "playlistinfo":
                out.append("".join(f"file: {u}\nPos: {i}\nId: {i + 10}\n"
                                   for i, u in enumerate(self.queue)))
            elif line.startswith("delete "):
                del self.queue[int(line[7:])]
                out.append("")
            elif line.startswith("random "):
                self.random = line[7:]
                out.append("")
            elif line.startswith("search any"):
                out.append("file: youtube:playlist:MPREb_x\nTime: 0\nTitle: Album: x\n"
                           "file: youtube:video:aaaaaaaaaaa\nTime: 240\nArtist: Trio\nTitle: Late Jazz\n"
                           "file: youtube:video:bbbbbbbbbbb\nTime: 7200\nArtist: Mix\nTitle: Two hours\n"
                           "file: youtube:video:ccccccccccc\nTime: 30\nArtist: X\nTitle: Short\n"
                           "file: youtube:video:ddddddddddd\nTime: 300\nArtist: Duo\nTitle: Blue\n")
            else:
                out.append("")
        return out


def test_stream_plays_after_his_queue_quietly_and_tidies_up(monkeypatch, tmp_path):
    mop = FakeMopidy(random="1")
    vols = []
    monkeypatch.setattr(T, "_mpd", mop)
    monkeypatch.setattr(T, "_set_volume", vols.append)
    from agent_media_core.route import dj
    monkeypatch.setattr(dj, "fade", lambda set_v, a, b, ms, **k: set_v(b))
    entries = [{"id": "aaaaaaaaaaa", "file": str(tmp_path / "a.mka")},
               {"id": "ddddddddddd", "file": str(tmp_path / "d.mka")}]
    stop = T.start(entries)
    assert stop is not None
    assert mop.queue == ["local:track:his.mp3", f"mpv:{tmp_path}/a.mka", f"mpv:{tmp_path}/d.mka"]
    assert "play 1" in mop.lines and "random 0" in mop.lines
    stop()
    assert mop.queue == ["local:track:his.mp3"] and mop.state == "stop"
    assert mop.random == "1"
    assert vols[0] == 0 and T.volume() in vols and vols[-1] == 80
    stop()                                  # twice is harmless
    assert mop.queue == ["local:track:his.mp3"]


@pytest.mark.parametrize("state", ["play", "pause"])
def test_stream_leaves_his_music_alone(monkeypatch, state):
    mop = FakeMopidy(state=state)
    monkeypatch.setattr(T, "_mpd", mop)
    monkeypatch.setattr(T, "_set_volume", lambda v: None)
    assert T.start([{"id": "aaaaaaaaaaa", "file": "/x.mka"}]) is None
    assert not any(line.startswith(("add", "play")) for line in mop.lines)


def test_stream_fill_searches_and_fetches_in_the_background(monkeypatch, tmp_path):
    monkeypatch.setattr(T, "_mpd", FakeMopidy())
    fetched = []

    def fetch(vid):
        fetched.append(vid)
        p = tmp_path / f"{vid}.mka"
        p.write_bytes(b"x")
        return str(p)
    monkeypatch.setattr(T, "_fetch_local", fetch)
    assert T.fill(["jazz"]) == 2
    assert fetched == ["aaaaaaaaaaa", "ddddddddddd"]   # 1.5-20 minutes only
    assert [e["title"] for e in T.tracks("jazz")] == ["Late Jazz", "Blue"]
    assert not T.needs("jazz")


def test_a_busy_mopidy_falls_back_to_the_generated_bed(monkeypatch, tmp_path):
    _fake_generated(monkeypatch, tmp_path)
    monkeypatch.setattr(T, "_mpd", FakeMopidy(state="play"))
    plan = B.Plan("stream", "streaming", "jazz", tracks=({"id": "a", "file": "/a"},),
                  fallback="dusk")
    sink = _Sink()
    tone, stop = B.start(plan, Target("rooms"), sink)
    assert tone is True and sink.unders == [f"music-bed-dusk-v{B.VERSION}.wav"]
    stop()


# ---- what Settings is told ------------------------------------------------------------------------------

class _Sink:
    def __init__(self):
        self.unders = []

    def play_under(self, uri, target, loop=False, kind=""):
        self.unders.append(Path(uri).name)
        return True


def test_status_lists_sources_with_availability_per_target(monkeypatch, tmp_path):
    monkeypatch.setenv("MEDIA_SPEECH_SOCKET_SASONICA", "tcp://127.0.0.1:9")
    monkeypatch.setenv("MEDIA_SPEECH_DEFAULT_TARGET", "sasonica")
    monkeypatch.setenv("MEDIA_SPEECH_DEVICE_SASONICA", "default")
    s = B.status()
    assert s["choice"] == "auto" and s["source"] == "generated" and s["style"] == "auto"
    ids = [c["id"] for c in s["choices"]]
    assert ids[:2] == ["off", "auto"] and "library:jazz" in ids and "lofi" in ids
    src = {x["id"]: x for x in s["sources"]}
    assert list(src) == ["generated", "streaming", "library"]
    assert src["generated"]["styles"][0] == {"id": "auto", "label": B.AUTO_LABEL}
    assert {"id": "nature", "label": "Nature (rain, stream, birds)"} in src["generated"]["styles"]
    assert [x["id"] for x in src["library"]["styles"]] == ["auto"] + list(B.NET_GENRES)
    assert s["target"] == "sasonica"
    assert src["streaming"]["available"] is False
    assert src["streaming"]["reason"] == T.PHONE_WHY
    per = {t["target"]: t for t in src["streaming"]["targets"]}
    assert per["sasonica"]["available"] is False
    assert src["library"]["available"] is True and src["generated"]["available"] is True
    assert s["now"] is None and s["credit"] is None


def test_status_reports_the_credit_while_it_plays(monkeypatch, tmp_path):
    B.set_choice("library:jazz")
    p = _cache_track("jazz", "ia-zzz", title="Acid Trumpet")
    plan = B.choose(B.pick_for(), False, Target("local"))
    assert plan.path == p
    s = B.status()
    assert s["choice"] == "library:jazz" and s["source"] == "library" and s["style"] == "jazz"
    assert s["library"]["jazz"] == 1
    assert "1 track kept" in {x["id"]: x for x in s["sources"]}["library"]["note"]
    tone, stop = B.start(plan, Target("local"), _Sink())
    assert tone
    s = B.status()
    assert s["now"]["playing"] is True and s["now"]["source"] == "library"
    assert s["credit"]["title"] == "Acid Trumpet" and s["credit"]["license"] == "CC BY 3.0"
    stop()
    assert B.status()["now"]["playing"] is False


def test_warm_fetches_for_the_chosen_library_genre(monkeypatch, tmp_path):
    _fake_generated(monkeypatch, tmp_path)
    B.set_choice("library:piano")
    called = []
    monkeypatch.setattr(L, "fill", lambda genres: called.append(tuple(genres)) or 0)
    assert B.warm_needed()
    B.warm()
    assert called == [("piano",)]
    B.set_choice("library:auto")
    called.clear()
    B.warm()
    assert set(called[0]) == set(B.NET_GENRES)


# ---- the new styles' sound ------------------------------------------------------------------------------

SHORT = {
    "ambient": {"bar": 1.0, "seed": 21, "chords": [[62, 69], [59, 66]], "bells": 1},
    "piano": {"bar": 0.75, "rounds": 1, "seed": 9, "chords": [(41, [57, 64]), (40, [55, 62])],
              "pattern": [0.0, 2.0]},
    "lofi": {"bpm": 120.0, "rounds": 1, "seed": 4, "chords": [(41, [57, 64])],
             "hiss": 0.05},
    "jazz": {"bpm": 240.0, "rounds": 1, "seed": 13, "chords": [(43, [58, 65]), (48, [58, 64])]},
    "nature": {"seconds": 2.0, "seed": 31, "birds": 2},
}


@pytest.fixture
def short_styles(monkeypatch):
    monkeypatch.setattr(ST, "RECIPES", {**ST.RECIPES, **SHORT})


def test_every_style_has_a_recipe_of_25_to_45_seconds():
    assert set(ST.STYLES) == set(B.GENRES) == set(ST.RECIPES)
    for s in ST.STYLES:
        assert 25.0 <= ST.loop_seconds(s) <= 45.0, s


@pytest.mark.parametrize("style", ST.STYLES)
def test_each_style_loops_without_a_click(short_styles, style):
    x = B.render(style)
    assert len(x) == int(round(B.RATE * ST.loop_seconds(style)))
    around = x[-200:] + x[:200]
    steps = [abs(around[i + 1] - around[i]) for i in range(len(around) - 1)]
    near = steps[:195] + steps[204:]
    assert abs(x[0] - x[-1]) <= 1.5 * max(near), style


@pytest.mark.parametrize("style", ST.STYLES)
def test_each_style_keeps_energy_above_300_hz(short_styles, style):
    """A phone's speaker loses most of what is under 300 Hz: at least a
    third of each style's energy is above it."""
    x = B.render(style)
    hi = ST._hp(x, 300.0)
    assert sum(v * v for v in hi) >= 0.33 * sum(v * v for v in x), style


def test_the_seamless_crossfade_meets_itself():
    import random
    rng = random.Random(1)
    n = 2 * B.RATE
    xs = ST._noise(n + B.RATE, rng)
    out = ST._seamless(xs, n)
    assert len(out) == n and out[0] == xs[n]          # the head starts as the tail went on
    assert out[-1] == xs[n - 1]


def test_a_style_file_is_mono_48k_at_minus_24(short_styles):
    p = B.write("lofi")
    assert p.name == f"music-bed-lofi-v{B.VERSION}.wav"
    with wave.open(str(p)) as w:
        assert (w.getnchannels(), w.getframerate(), w.getsampwidth()) == (1, 48000, 2)
        pcm = array.array("h", w.readframes(w.getnframes()))
    assert abs(20 * math.log10(max(abs(v) for v in pcm) / 32767) + 24) < 0.2
    assert B.synth("jazz") == B.synth("jazz")          # seeded
