"""Songs of the listener's own near some words (tune_related.py).

Nothing is reached: MusicBrainz and Ollama are stood in for through
`_http_json`, the plays through music_recent.listed, and the index lives in
a scratch XDG_STATE_HOME."""

from __future__ import annotations

import json

import pytest

from agent_media_core import music_recent, tune_related as tr


@pytest.fixture(autouse=True)
def scratch(monkeypatch, tmp_path):
    for name in ("MEDIA_TUNE_EMBED", "MEDIA_TUNE_EMBED_URL", "MEDIA_TUNE_EMBED_MODEL",
                 "ZK_OLLAMA", "MEDIA_MUSICBRAINZ_UA"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr(tr.time, "sleep", lambda s: None)
    tr._last_mb_call[0] = 0.0


PLAYS = {
    "most": [
        {"uri": "yt:aaa", "title": "Chris Cornell - Like a Stone", "artist": "Chris Cornell",
         "song": "Like a Stone", "genre": "Rock", "plays": 4},
        {"uri": "yt:bbb", "title": "Brian Eno - An Ending (Ascent)", "artist": "Brian Eno",
         "song": "An Ending (Ascent)", "genre": "Ambient", "plays": 2},
        {"uri": "yt:mix", "title": "Sleep", "artist": "vaniBorn",
         "song": "Spiritual Morning Mix Yoga, Healing Music & Songs to Come Back to Your Body",
         "genre": None, "plays": 9},
        {"uri": "yt:noart", "title": "The Gambler", "artist": None, "song": "The Gambler",
         "genre": None, "plays": 1},
        {"uri": "yt:med", "title": "Fall Asleep Fast Deep Sleep Meditation", "artist": "The Mindful Movement",
         "song": "Fall Asleep Fast Deep Sleep Meditation", "genre": None, "plays": 3},
    ],
    "liked": [
        {"uri": "https://www.youtube.com/watch?v=aaa", "title": "Chris Cornell - Like a Stone",
         "artist": "Chris Cornell", "song": "Like a Stone", "genre": "Rock"},
    ],
}


@pytest.fixture
def plays(monkeypatch):
    monkeypatch.setattr(music_recent, "listed",
                        lambda kind, which, store=None, limit=40: list(PLAYS.get(which, [])))
    monkeypatch.setattr(music_recent, "_vid", lambda uri: uri.split("v=")[-1] if "v=" in uri else uri[3:])


class Fake:
    """MusicBrainz and Ollama, answered from here."""

    def __init__(self, embed=True):
        self.calls: list[str] = []
        self.embed = embed

    def __call__(self, url, data=None, headers=None, timeout=0):
        self.calls.append(url)
        if "/api/embed" in url:
            if not self.embed:
                raise OSError("no ollama")
            texts = json.loads(data)["input"]
            return {"embeddings": [self._vec(t) for t in texts]}
        if "/recording?" in url:
            assert headers["User-Agent"].startswith("agent-media/")
            if "Cornell" in url:
                return {"recordings": [{"id": "rec-1", "score": 100,
                                        "artist-credit": [{"artist": {"id": "art-cc"}}],
                                        "tags": [{"name": "grunge", "count": 2}],
                                        "genres": [{"name": "alternative rock"}]}]}
            if "Eno" in url:
                return {"recordings": [{"id": "rec-2", "score": 55}]}
            return {"recordings": []}
        if "/artist/art-cc" in url:
            return {"tags": [{"name": "seattle"}, {"name": "grunge"}], "genres": [{"name": "rock"}]}
        raise AssertionError(url)

    @staticmethod
    def _vec(text):
        # Two axes: how rock-ish and how calm-ish the words are.
        t = text.lower()
        rock = sum(w in t for w in ("rock", "grunge", "cornell", "loud", "triumph"))
        calm = sum(w in t for w in ("ambient", "eno", "sleep", "calm", "quiet"))
        return [float(rock), float(calm)] + [0.0] * 766


def test_corpus_keeps_songs_not_mixes(plays):
    c = tr.corpus()
    assert set(c) == {"aaa", "bbb"}
    assert c["aaa"]["liked"] is True and c["aaa"]["plays"] == 4
    assert c["bbb"]["liked"] is False


def test_index_tags_embeds_and_answers(plays, monkeypatch):
    fake = Fake()
    monkeypatch.setattr(tr, "_http_json", fake)
    got = tr.index()
    assert got == {"songs": 2, "looked_up": 2, "found": 1, "embedded": 2, "pending": 0,
                   "embed": True}
    d = tr.load()
    cc = d["songs"]["aaa"]
    assert cc["mb"]["tags"] == ["grunge"] and cc["mb"]["genres"] == ["alternative rock"]
    assert cc["mb"]["artist_tags"] == ["rock", "seattle"]  # grunge already on the recording
    assert cc["text"] == "Chris Cornell - Like a Stone. rock, alternative rock, grunge, seattle."
    assert d["songs"]["bbb"]["mb"]["found"] is False  # score under 80
    assert d["songs"]["bbb"]["text"] == "Brian Eno - An Ending (Ascent). ambient."
    assert "art-cc" in d["artists"]

    rows = tr.related("a long job finally done, loud and triumphant")
    assert [r["song"] for r in rows] == ["Like a Stone", "An Ending (Ascent)"]
    assert rows[0]["how"] == "embed" and "vec" not in rows[0]
    rows = tr.related("winding down to sleep, calm")
    assert rows[0]["song"] == "An Ending (Ascent)"
    assert tr.line(rows[0]).startswith("Brian Eno - An Ending (Ascent)  (")

    # A second run asks MusicBrainz for nothing already answered, and embeds nothing.
    n = len(fake.calls)
    got = tr.index()
    assert got["looked_up"] == 0 and got["embedded"] == 0
    assert len(fake.calls) == n


def test_lookups_cap_and_retry(plays, monkeypatch):
    fake = Fake()
    monkeypatch.setattr(tr, "_http_json", fake)
    got = tr.index(lookups=1)
    assert got["looked_up"] == 1 and got["pending"] == 1
    got = tr.index(lookups=1)
    assert got["looked_up"] == 1 and got["pending"] == 0
    # A service not reached leaves the song to be asked again next run.
    monkeypatch.setattr(tr, "_http_json", lambda *a, **k: (_ for _ in ()).throw(OSError("down")))
    d = tr.load()
    d["songs"]["aaa"].pop("mb")
    tr.save(d)
    got = tr.index(lookups=5)
    assert got["looked_up"] == 1 and got["found"] == 0 and got["pending"] == 1
    # Its tags went with `mb`, so its text changed and Ollama (down too) was asked.
    assert got["embed"] is False


def test_without_ollama_words_overlap(plays, monkeypatch):
    monkeypatch.setattr(tr, "_http_json", Fake(embed=False))
    got = tr.index()
    assert got["embed"] is False and got["embedded"] == 0
    rows = tr.related("some ambient music")
    assert rows[0]["song"] == "An Ending (Ascent)" and rows[0]["how"] == "words"
    monkeypatch.setenv("MEDIA_TUNE_EMBED", "off")
    assert tr.embed(["x"]) is None


def test_corpus_change_drops_and_reembeds(plays, monkeypatch):
    fake = Fake()
    monkeypatch.setattr(tr, "_http_json", fake)
    tr.index()
    monkeypatch.setattr(music_recent, "listed",
                        lambda kind, which, store=None, limit=40: PLAYS["most"][:1] if which == "most" else [])
    got = tr.index()
    assert got["songs"] == 1 and "bbb" not in tr.load()["songs"]


def test_session_text(monkeypatch):
    class Store:
        def recent_history(self, sink=None, limit=20):
            return [
                {"text": "You: hi", "extras": {"listener": True, "source_session": "abc-1"}},
                {"text": "Third reply", "extras": json.dumps({"source_session": "abc-1"})},
                {"text": "Other thread", "extras": {"source_session": "zzz"}},
                {"text": "Second reply", "extras": {"session": "abc-1"}},
                {"text": "First reply", "extras": {"source_session": "abc-1"}},
            ]

    assert tr.session_text("abc", store=Store(), replies=2) == "Second reply\n\nThird reply"
    assert tr.session_text("abc-1", store=Store()) == "First reply\n\nSecond reply\n\nThird reply"


def test_cli(plays, monkeypatch, capsys):
    from agent_media_core import cli

    monkeypatch.setattr(tr, "_http_json", Fake())
    assert cli.main(["tune", "index"]) == 0
    assert "2 songs; 2 asked of MusicBrainz, 1 found" in capsys.readouterr().out
    assert cli.main(["tune", "related", "loud", "rock"]) == 0
    assert capsys.readouterr().out.startswith("Chris Cornell - Like a Stone  (")
    assert cli.main(["tune", "related", "--json", "calm"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["song"] == "An Ending (Ascent)"
    assert cli.main(["tune", "related"]) == 2
