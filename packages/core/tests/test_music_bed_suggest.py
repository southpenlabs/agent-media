"""Songs he suggests for the backing music, and distinctive tunes first
(music_bed_favourites.suggest, rate_renown, pick_weight; David, 1 Nov
2026: "an easy way to be able to suggest tracks for backing music. also
for backing music, distinctive tunes and riffs should be preferred", and
"the use in popular movie soundtracks ... are often a good indicator").
No network, Mopidy or model: conftest refuses them, each test fakes one."""
import os
import random

import pytest

from agent_media_core import music_bed_favourites as F

#: The real model ask, before conftest swaps it out in every test.
REAL_RATE = F._rate

TEARDROP = {"id": "3h-JYx76QNM", "artist": "Massive Attack", "title": "Teardrop", "dur": 331}


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    for name in list(os.environ):
        if name.startswith(("MEDIA_", "RELAY_")):
            monkeypatch.delenv(name, raising=False)
    for name in ("HOME", "XDG_STATE_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME",
                 "XDG_DATA_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name))
    monkeypatch.setenv("MEDIA_AUDIO_DIR", str(tmp_path / "audio"))
    monkeypatch.setattr(F, "lookup", lambda vid, data=None: dict(TEARDROP) if vid == TEARDROP["id"] else None)
    monkeypatch.setattr(F, "_from_store", lambda: {})


def _ready(key, title, features=None):
    data = F.load()
    d = F._dir()
    d.mkdir(parents=True, exist_ok=True)
    name = f"music-bed-fav-{key}.mp3"
    (d / name).write_bytes(b"x" * 2000)
    data["tracks"].append({"key": key, "file": name, "title": title, "artist": "A",
                           "method": "official", "original_id": "", "made": "2026-11-01",
                           "features": features or {}})
    F.save(data)


def test_suggesting_what_is_playing_puts_it_first():
    got = F.suggest("yt:https://www.youtube.com/watch?v=3h-JYx76QNM")
    key = F.key_for("Massive Attack", "Teardrop")
    assert got == {"key": key, "title": "Teardrop", "artist": "Massive Attack",
                   "id": "3h-JYx76QNM", "ready": False}
    data = F.load()
    assert data["favourites"][0]["key"] == key
    assert "pass_at" not in data  # looked for at the next pass
    assert F.suggested_key("3h-JYx76QNM") == key
    # And it stays first at every later gather, above anything counted.
    assert F.gather(F.load())[0]["key"] == key


def test_a_search_names_a_song(monkeypatch):
    monkeypatch.setattr(F, "search", lambda q: [{"id": "abcdefghijk", "artist": "Portishead",
                                                 "title": "Glory Box (Official Video)", "dur": 300}])
    got = F.suggest("portishead glory box")
    assert (got["artist"], got["title"]) == ("Portishead", "Glory Box")


def test_nothing_found_is_refused(monkeypatch):
    monkeypatch.setattr(F, "search", lambda q: [])
    with pytest.raises(ValueError):
        F.suggest("no such song")
    with pytest.raises(ValueError):
        F.suggest("file:///etc/passwd")


def test_suggesting_lifts_a_dislike_and_unsuggest_takes_it_back():
    key = F.key_for("Massive Attack", "Teardrop")
    F._write_json(F._excluded_path(), {key: {}, "id:3h-JYx76QNM": {}})
    F.suggest("3h-JYx76QNM")
    assert F.excluded() == {}
    assert F.unsuggest("yt:https://www.youtube.com/watch?v=3h-JYx76QNM")["key"] == key
    assert F.suggested() == {}
    with pytest.raises(ValueError):
        F.unsuggest(key)


def test_a_suggested_track_plays_past_the_slow_rule(monkeypatch):
    monkeypatch.setenv("MEDIA_MUSIC_BED_SLOW", "1")
    key = F.key_for("Massive Attack", "Teardrop")
    busy = {"bpm": 62, "onsets": 4.5, "lufs": -15, "tempo": 0.02, "energy": 0.4}
    _ready(key, "Teardrop", busy)
    _ready("other", "Other", busy)
    assert F.playable() == []
    F.suggest("3h-JYx76QNM")
    assert [e["key"] for e in F.playable()] == [key]


def test_renown_moves_the_gather(monkeypatch):
    songs = {"v1aaaaaaaaa": ("Bland Band", "Filler"), "v2aaaaaaaaa": ("Lalo Schifrin", "Mission Impossible")}
    monkeypatch.setattr(F, "_from_store", lambda: {v: {"likes": 0, "plays": 10} for v in songs})
    monkeypatch.setattr(F, "lookup", lambda vid, data=None: {
        "id": vid, "artist": songs[vid][0], "title": songs[vid][1], "dur": 200})
    asked = []

    def rate(todo):
        asked.append([s["title"] for s in todo])
        return {i: (10 if s["title"] == "Mission Impossible" else 1) for i, s in enumerate(todo, 1)}
    monkeypatch.setattr(F, "_rate", rate)
    data = F.load()
    favs = F.gather(data)
    assert [f["title"] for f in favs] == ["Mission Impossible", "Filler"]
    assert favs[0]["score"] == 15.0 and favs[1]["score"] == 6.0
    # Asked once a song: the next gather asks nothing.
    F.gather(data)
    assert len(asked) == 1


def test_renown_off(monkeypatch):
    monkeypatch.setenv("MEDIA_MUSIC_BED_FAVOURITES_RENOWN", "0")
    monkeypatch.setattr(F, "_rate", lambda todo: pytest.fail("asked with renown off"))
    assert F.rate_renown({}, [{"key": "k", "artist": "a", "title": "t"}]) == 0


def test_the_model_answer_is_read(monkeypatch):
    from agent_media_core.intake import fast
    songs = [{"artist": "a", "title": "t1"}, {"artist": "b", "title": "t2"}]
    seen = {}

    def chat(system, user, timeout, purpose_env=None, private=False):
        seen["user"] = user
        return 'Sure:\n```json\n{"1": 9, "2": "3", "7": 5}\n```'
    monkeypatch.setattr(fast, "chat", chat)
    assert REAL_RATE(songs) == {1: 9.0, 2: 3.0}
    assert seen["user"] == "1. a — t1\n2. b — t2"
    monkeypatch.setattr(fast, "chat", lambda *a, **k: None)
    assert REAL_RATE(songs) == {}
    monkeypatch.setattr(fast, "chat", lambda *a, **k: "no idea")
    assert REAL_RATE(songs) == {}


def test_pick_weight():
    assert F.pick_weight("s", {}, {"s": {}}) == 2.5
    assert F.pick_weight("x", {}, {}) == 1.0
    assert F.pick_weight("x", {"x": 10}, {}) == 2.25
    assert F.pick_weight("x", {"x": 0}, {}) == 0.25
    assert F.pick_weight("x", {"x": 5}, {}) == pytest.approx(0.75)


def test_the_distinctive_come_up_most(monkeypatch):
    monkeypatch.setenv("MEDIA_MUSIC_BED_SLOW", "0")
    _ready("iconic", "Iconic")
    _ready("bland", "Bland")
    data = F.load()
    data["renown"] = {"iconic": 10, "bland": 0}
    F.save(data)
    random.seed(1)
    picks = [F.track_for()[1]["original_title"] for _ in range(400)]
    assert picks.count("Iconic") > 5 * picks.count("Bland")
