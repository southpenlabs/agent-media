"""His YouTube accounts as taste (music_accounts; David, 1 Nov 2026: "link
my YouTube and Spotify accounts for preferences" ... "can we reuse that?").
The reader process is faked at music_accounts._run."""
import json
import os

import pytest

from agent_media_core import music_accounts as A
from agent_media_core import music_bed_favourites as F
from agent_media_core import personal_prefs as P

READ = {"accounts": [
    {"profile": "main", "auth": "data api", "error": None, "songs": [
        {"id": "aaaaaaaaaaa", "title": "Paranoid (2009 Remaster)", "artist": "Black Sabbath",
         "dur": 170, "liked": True, "plays": 0},
        {"id": "bbbbbbbbbbb", "title": "Ace of Spades", "artist": "Motörhead", "dur": 169,
         "liked": False, "plays": 1}]},
    {"profile": "music", "auth": "headers", "error": None, "songs": [
        {"id": "bbbbbbbbbbb", "title": "Ace of Spades", "artist": "Motörhead", "dur": 169,
         "liked": False, "plays": 2}]},
    {"profile": "dead", "auth": None, "error": "headers: KeyError", "songs": []}]}


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    for name in list(os.environ):
        if name.startswith(("MEDIA_", "RELAY_")):
            monkeypatch.delenv(name, raising=False)
    for name in ("HOME", "XDG_STATE_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name))
    monkeypatch.setenv("MEDIA_YTMUSIC_PYTHON", "/usr/bin/python3")
    ran = []
    monkeypatch.setattr(A, "_run", lambda argv: (ran.append(argv), json.dumps(READ))[1])
    return ran


def test_no_profiles_reads_nothing(isolated):
    assert A.youtube_songs() == {} and isolated == []
    assert A.youtube_why().startswith("Not set")


def test_profiles_are_read_merged_and_kept_a_day(isolated, monkeypatch):
    monkeypatch.setenv("MEDIA_YOUTUBE_PROFILES", "main, music,dead")
    songs = A.youtube_songs(now=1000.0)
    assert songs["aaaaaaaaaaa"]["liked"] is True
    assert songs["bbbbbbbbbbb"]["plays"] == 3 and songs["bbbbbbbbbbb"]["liked"] is False
    assert isolated[0][-3:] == ["main", "music", "dead"] and isolated[0][1] == "-I"
    A.youtube_songs(now=2000.0)
    assert len(isolated) == 1  # the kept read
    A.youtube_songs(now=1000.0 + A.TTL_S + 1)
    assert len(isolated) == 2
    assert A.youtube_why() == "Nothing read from dead: sign in again with yt-profile."


def test_a_failed_read_keeps_the_last(isolated, monkeypatch):
    monkeypatch.setenv("MEDIA_YOUTUBE_PROFILES", "main")
    A.youtube_songs(now=0.0)

    def boom(argv):
        raise OSError("gone")
    monkeypatch.setattr(A, "_run", boom)
    assert "aaaaaaaaaaa" in A.youtube_songs(now=A.TTL_S * 2)


def test_the_gather_counts_them(isolated, monkeypatch):
    monkeypatch.setenv("MEDIA_YOUTUBE_PROFILES", "main,music")
    monkeypatch.setattr(F, "_from_store", lambda: {})
    monkeypatch.setattr(F, "lookup", lambda vid, data=None: None)
    favs = {f["title"]: f for f in F.gather(F.load())}
    assert favs["Paranoid"]["score"] == 5 and favs["Paranoid"]["from"] == ["youtube-liked"]
    assert favs["Ace of Spades"]["score"] == 3


def test_the_setting(isolated):
    P.update({"youtube.profiles": "davidj4, davidj4music"})
    assert A.youtube_profiles() == ["davidj4", "davidj4music"]
    with pytest.raises(P.Invalid):
        P.update({"youtube.profiles": "a;rm -rf"})
