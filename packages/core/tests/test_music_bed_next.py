"""Up next for backing music and outros (music_bed_next.py; David, 20 Nov
2026; agent-mail #1099): the next long reply's bed picked ahead, held, then
played by that reply, picked again when it no longer fits, and skipped.
music_bed.choose is a stand-in; nothing is fetched or played."""
import os

import pytest

from agent_media_core import music_bed as B
from agent_media_core import music_bed_next as N


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    for name in list(os.environ):
        if name.startswith(("MEDIA_", "RELAY_")):
            monkeypatch.delenv(name, raising=False)
    for name in ("HOME", "XDG_STATE_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name))
    monkeypatch.setenv("MEDIA_MUSIC_BED_WEATHER", "off")


@pytest.fixture()
def songs(monkeypatch, tmp_path):
    """Favourites a, b, c picked in turn, and the Pick the moment wants."""
    files = {}
    for k in "abc":
        p = tmp_path / f"music-bed-fav-{k}.mp3"
        p.write_bytes(b"x")
        files[k] = p
    state = {"pick": B.Pick("favourites", "auto", "dusk"), "order": list("abcabc"),
             "calls": 0}

    def choose(pick, other, target=None, topic=None):
        state["calls"] += 1
        k = state["order"].pop(0)
        return B.Plan("under", "favourites", "auto", path=files[k],
                      credit={"title": f"Instrumental of {k.upper()}", "creator": "Band",
                              "original_title": k.upper(), "original_id": f"vid{k}"})

    monkeypatch.setattr(B, "pick_for", lambda *a, **k: state["pick"])
    monkeypatch.setattr(B, "choose", choose)
    from agent_media_core import speech_music
    monkeypatch.setattr(speech_music, "_favourite_key", lambda path: path.stem[-1])
    return state


def test_a_peek_is_held_and_the_reply_plays_it(songs):
    first = N.peek()
    assert first["bed"]["title"] == "Instrumental of A"
    assert first["bed"]["art"] == "https://i.ytimg.com/vi/vida/hqdefault.jpg"
    assert first["reason"] is None
    assert N.peek()["bed"]["track_key"] == "a" and songs["calls"] == 1
    plan = N.take(songs["pick"])
    assert plan is not None and plan.path.name == "music-bed-fav-a.mp3"
    # Used: the next ask picks again, and nothing is held for a reply.
    assert N.take(songs["pick"]) is None
    assert N.peek()["bed"]["track_key"] == "b"


def test_nothing_held_means_the_reply_chooses_as_before(songs):
    assert N.take(songs["pick"]) is None and songs["calls"] == 0


def test_a_moment_that_moved_on_lets_the_pick_go(songs):
    N.peek()
    songs["pick"] = B.Pick("favourites", "auto", "night")
    assert N.take(songs["pick"]) is None
    assert N.peek()["bed"]["track_key"] == "b"


def test_a_missing_file_or_an_old_pick_is_chosen_again(songs, tmp_path):
    N.peek(now=1000.0)
    (tmp_path / "music-bed-fav-a.mp3").unlink()
    assert N.peek(now=1001.0)["bed"]["track_key"] == "b"
    assert N.peek(now=1001.0 + N.HOLD_S + 1)["bed"]["track_key"] == "c"


def test_skip_picks_another_song(songs):
    assert N.peek()["bed"]["track_key"] == "a"
    songs["order"] = list("abc")  # the stand-in offers the held song first
    after = N.skip("sing_out")
    assert after["bed"]["track_key"] == "b"
    assert N.take(songs["pick"]).path.name == "music-bed-fav-b.mp3"
    with pytest.raises(ValueError):
        N.skip("tune")


def test_the_outro_and_the_sing_in_follow_the_bed(songs, monkeypatch):
    from agent_media_core import music_bed_vocal as V
    monkeypatch.setattr(V, "after_for", lambda p: {"key": p.stem[-1]})
    monkeypatch.setattr(V, "before_for", lambda p: None)
    got = N.peek()
    assert got["sing_out"]["title"] == "A" and got["sing_out"]["track_key"] == "a"
    assert got["sing_in"] is None
    from agent_media_core import music_bed_mix as M
    monkeypatch.setattr(M, "on", lambda name: name != "vocal")
    assert N.peek()["sing_out"] is None


def test_off_says_so(songs):
    songs["pick"] = None
    got = N.peek()
    assert got["bed"] is None and got["reason"] == "off"
