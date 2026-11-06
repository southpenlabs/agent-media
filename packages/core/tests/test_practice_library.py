"""Saved practice (practice_library.py): sources, feeds, the mark."""

import json

import pytest

from agent_media_core import practice_library as L

FEED = b"""<?xml version="1.0"?>
<rss xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"><channel>
<title>Calm Talks</title>
<item><title>Letting go</title><pubDate>Fri, 30 Oct 2026</pubDate>
<itunes:duration>22:10</itunes:duration>
<enclosure url="https://cdn.example/letting-go.mp3" type="audio/mpeg"/></item>
<item><title>A video</title><enclosure url="https://cdn.example/v.mp4" type="video/mp4"/></item>
<item><title>Breath</title><enclosure url="https://cdn.example/breath.mp3" type="audio/mpeg"/></item>
</channel></rss>"""


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("MEDIA_PRACTICE_DIR", str(tmp_path / "Practice"))
    monkeypatch.setattr(L, "_fetch", lambda url, timeout=15.0: FEED)
    monkeypatch.setattr(L, "_yt_title", lambda url: "")
    # Never the real YouTube add-on (it downloads on the phone), and a
    # video's sound got ready inline, so no thread outlives its test.
    monkeypatch.setattr(L, "_downloads_addon", lambda: None)
    inline = L.prepare_sound
    monkeypatch.setattr(L, "prepare_sound", lambda it, background=True: inline(it, background=False))
    L._fetching.clear()


def test_parse_feed_keeps_audio_episodes():
    got = L.parse_feed(FEED)
    assert got["title"] == "Calm Talks"
    assert [e["title"] for e in got["episodes"]] == ["Letting go", "Breath"]
    assert got["episodes"][0]["duration"] == "22:10"


def test_youtube_ids():
    assert L.youtube_id("https://www.youtube.com/watch?v=AAAAAAAAAAA&t=3") == "AAAAAAAAAAA"
    assert L.youtube_id("https://youtu.be/BBBBBBBBBBB") == "BBBBBBBBBBB"
    assert L.youtube_id("https://example.com/feed.xml") is None


def test_a_feed_adds_its_episodes():
    got = L.add("https://example.com/feed.xml")
    assert got == {"added": "podcast", "title": "Calm Talks", "episodes": 2}
    pods = [i for i in L.items() if i["source"] == "podcast"]
    assert [(i["title"], i["show"]) for i in pods] == [("Letting go", "Calm Talks"),
                                                       ("Breath", "Calm Talks")]
    assert L.remove("https://example.com/feed.xml")
    assert not [i for i in L.items() if i["source"] == "podcast"]


def test_a_link_that_is_neither_is_refused(monkeypatch):
    monkeypatch.setattr(L, "_fetch", lambda url, timeout=15.0: b"<html></html>")
    with pytest.raises(ValueError):
        L.add("https://example.com/page")
    with pytest.raises(ValueError):
        L.add("ftp://example.com/x")


def test_files_and_made_ones_are_listed(tmp_path):
    from agent_media_core import meditate
    d = tmp_path / "Practice"
    (d / "sleep").mkdir(parents=True)
    (d / "sleep" / "Deep_Sleep.m4a").write_bytes(b"x")
    meditate.out_dir().mkdir(parents=True)
    clip = meditate.out_dir() / "meditation-0123456789abcdef.mp3"
    clip.write_bytes(b"x")
    L.remember(clip, "Evening wind-down")
    got = {(i["source"], i["title"]) for i in L.items()}
    assert got == {("file", "Deep Sleep"), ("made", "Evening wind-down")}
    assert L.match("wind down")["id"] == "made:0123456789abcdef"
    assert L.match("deep sleep")["source"] == "file"
    assert L.match("nothing like it") is None
    assert L.remove("made:0123456789abcdef") and not clip.exists()
    assert not L.remove("made:../../etc")


def test_the_mark_finds_what_the_book_plays():
    L.mark_playing("yt:https://www.youtube.com/watch?v=AAAAAAAAAAA")
    assert L.is_marked("/lib/Body scan [AAAAAAAAAAA].m4a")
    assert L.is_marked("https://www.youtube.com/watch?v=AAAAAAAAAAA")
    assert not L.is_marked("/books/dune.mp3")
    L.mark_playing("https://cdn.example/letting-go.mp3?x=1")
    assert L.is_marked("https://cdn.example/letting-go.mp3?x=1")
    assert L.is_marked("/sdcard/Audiobooks/letting-go.mp3")
    from agent_media_core import meditate
    assert meditate.is_meditation("/sdcard/Audiobooks/letting-go.mp3")
    assert not meditate.is_meditation("/books/dune.mp3")


def test_play_refuses_what_is_not_saved():
    assert L.play("/etc/passwd")["ok"] is False


def test_a_single_episode_is_saved_and_removed():
    got = L.add_episode("https://cdn.example/breath.mp3", "Breath", show="Calm Talks")
    assert got["added"] == "episode" and got["id"].startswith("ep:")
    eps = [i for i in L.items() if i["id"] == got["id"]]
    keys = ("id", "title", "source", "show", "added")
    assert [{k: e[k] for k in keys} for e in eps] == [{"id": got["id"], "title": "Breath", "source": "podcast",
                                                       "show": "Calm Talks", "added": eps[0]["added"]}]
    assert L.uri_for(got["id"]) == "https://cdn.example/breath.mp3"
    with pytest.raises(ValueError):
        L.add_episode("file:///etc/passwd", "x")
    assert L.remove(got["id"]) and not [i for i in L.items() if i["id"] == got["id"]]


def test_who_what_and_for_what():
    yt = {"title": "Yoga Nidra For Sleep — Melanie Cooper", "source": "youtube"}
    assert L.teacher_of(yt) == "Melanie Cooper"
    assert L.style_of(yt["title"]) == "Yoga Nidra" and L.benefit_of(yt["title"]) == "Sleep"
    assert L.teacher_of({"title": "Letting go", "show": "Calm Talks"}) == "Calm Talks"
    assert L.teacher_of({"title": "Meditation", "source": "made"}) is None
    assert L.style_of("Dialectic") == "Guided" and L.benefit_of("Dialectic") is None
    assert L.style_of("Heart Opening Yoga Nidra 528 Hz") == "Yoga Nidra", "the first rule wins"


def test_a_feeds_duration_in_seconds():
    assert [L._seconds(x) for x in ("754", "12:34", "1:02:03", "", "soon", None)] == [754, 754, 3723, None, None, None]


def test_items_carry_length_and_plays(monkeypatch):
    L.add("https://feeds.example/calm.xml")
    L.add("https://www.youtube.com/watch?v=AAAAAAAAAAA", "Morning Yoga Nidra — Jaime Tan")
    got = {i["title"]: i for i in L.items()}
    assert got["Letting go"]["duration_s"] == 1330 and got["Letting go"]["teacher"] == "Calm Talks"
    nidra = got["Morning Yoga Nidra — Jaime Tan"]
    assert nidra["duration_s"] is None and nidra["plays"] == 0 and nidra["last_played"] is None
    assert (nidra["teacher"], nidra["style"], nidra["benefit"]) == ("Jaime Tan", "Yoga Nidra", "Morning & energy")
    L.note_play("yt:AAAAAAAAAAA")
    L.note_play("yt:AAAAAAAAAAA")
    again = next(i for i in L.items() if i["id"] == "yt:AAAAAAAAAAA")
    assert again["plays"] == 2 and again["last_played"] > 0


def test_lengths_are_looked_up_in_the_background(monkeypatch):
    monkeypatch.setenv("MEDIA_PRACTICE_DURATIONS", "1")
    L.add("https://www.youtube.com/watch?v=AAAAAAAAAAA", "A")
    L.add("https://www.youtube.com/watch?v=BBBBBBBBBBB", "B")
    asked = []
    monkeypatch.setattr(L, "_youtube_lengths", lambda vids: (asked.append(sorted(vids)), {"AAAAAAAAAAA": 600})[1])
    started = []
    import threading
    monkeypatch.setattr(threading, "Thread", lambda target, **_: type("T", (), {"start": lambda self: (started.append(1), target())})())
    L.items()
    got = {i["id"]: i["duration_s"] for i in L.items()}
    assert got == {"yt:AAAAAAAAAAA": 600, "yt:BBBBBBBBBBB": None}
    assert asked == [["AAAAAAAAAAA", "BBBBBBBBBBB"]], "both in one call to the phone, the failed one not again so soon"
    # A day on, the one that failed is asked again.
    d = json.loads(L._durations_path().read_text())
    d["yt:BBBBBBBBBBB"]["failed"] -= L.RETRY_S + 1
    L._durations_path().write_text(json.dumps(d))
    L.items()
    assert asked[-1] == ["BBBBBBBBBBB"]


def test_youtube_lengths_come_from_the_add_on(monkeypatch):
    from agent_media_core import personal_use
    assert L._youtube_lengths(["AAAAAAAAAAA"]) == {}  # no add-on: no lengths
    meta = type("Meta", (), {"lengths": staticmethod(lambda vids: {"AAAAAAAAAAA": 1820.0})})()
    monkeypatch.setitem(personal_use._PROVIDERS, "youtube-metadata", meta)
    assert L._youtube_lengths(["AAAAAAAAAAA", "BBBBBBBBBBB"]) == {"AAAAAAAAAAA": 1820}


def test_the_old_meditation_files_are_moved_across_once():
    old = L.state_dir() / "meditations.json"
    old.parent.mkdir(parents=True, exist_ok=True)
    old.write_text(json.dumps({"youtube": [{"vid": "AAAAAAAAAAA", "url": "https://www.youtube.com/watch?v=AAAAAAAAAAA", "title": "Kept"}]}))
    (L.state_dir() / "meditation-plays.json").write_text(json.dumps({"yt:AAAAAAAAAAA": {"n": 4, "last": 1.0}}))
    got = L.items()
    assert [(i["title"], i["plays"]) for i in got] == [("Kept", 4)]
    assert not old.exists() and (L.state_dir() / "practice.json").exists()
    assert (L.state_dir() / "practice-plays.json").exists()


def test_a_teachers_profile_comes_from_the_addon_kept_a_month(monkeypatch):
    monkeypatch.setenv("MEDIA_PRACTICE_PROFILES", "1")
    calls = []
    profile = {"name": "Pura Rasa", "region": "Vilnius, Lithuania", "country": "Lithuania", "followers": 92037,
               "photo": "p.jpg", "photo_large": "p-large.jpg"}
    from agent_media_core import personal_use
    personal_use.register("insight-timer-profiles", type("IT", (), {"lookup": staticmethod(lambda n: calls.append(n) or profile)})())
    assert L.teacher_profile("Pura Rasa")["profile"] == profile
    assert L.teacher_profile("Pura Rasa")["profile"] == profile and calls == ["Pura Rasa"], "kept, not asked again"


def test_a_profile_kept_before_photo_large_is_asked_again(monkeypatch):
    monkeypatch.setenv("MEDIA_PRACTICE_PROFILES", "1")
    from agent_media_core import personal_use
    new = {"name": "Pura Rasa", "photo": "p.jpg", "photo_large": "p-large.jpg"}
    personal_use.register("insight-timer-profiles", type("IT", (), {"lookup": staticmethod(lambda n: new)})())
    L._profiles_path().parent.mkdir(parents=True, exist_ok=True)
    L._profiles_path().write_text(json.dumps({"purarasa": {"profile": {"name": "Pura Rasa", "photo": "p.jpg"},
                                                           "at": __import__("time").time()}}))
    assert L.teacher_profile("Pura Rasa")["profile"] == new


def test_without_the_addon_no_profile(monkeypatch):
    monkeypatch.setenv("MEDIA_PRACTICE_PROFILES", "1")
    from agent_media_core import personal_use
    personal_use.register("insight-timer-profiles", None)
    assert L.teacher_profile("Calm Minutes") == {"name": "Calm Minutes", "profile": None}


def test_teacher_places_from_the_kept_profiles(monkeypatch):
    monkeypatch.setenv("MEDIA_PRACTICE_PROFILES", "1")
    from agent_media_core import personal_use
    personal_use.register("insight-timer-profiles", type("IT", (), {"lookup": staticmethod(lambda n: None)})())
    L._profiles_path().parent.mkdir(parents=True, exist_ok=True)
    L._profiles_path().write_text(json.dumps({
        "purarasa": {"profile": {"region": "Vilnius, Lithuania", "photo": "p.jpg"}, "at": 1},
        "calmminutes": {"profile": None, "at": 1}}))
    asked = []
    monkeypatch.setattr(L, "_fill_profiles", lambda names: asked.extend(names))
    got = L.teacher_places(["Pura Rasa", "Calm Minutes", "Nid Ra"])
    assert got == {"Pura Rasa": {"region": "Vilnius, Lithuania", "country": "Lithuania", "state": None,
                                 "city": "Vilnius", "photo": "p.jpg"}}
    assert asked == ["Nid Ra"], "only one never looked up is asked for"


def test_countries_are_written_one_way():
    c = lambda **p: L._country(p)  # noqa: E731
    assert [c(country="USA"), c(region="Asheville, NC"), c(country="UK"), c(region="Manly NSW 2095, Australia"),
            c(region="Toronto, ON"), c(country="Lithuania"), c(country="Nevada"), c(country="Manchester UK"), L._country(None)] == \
        ["United States", "United States", "United Kingdom", "Australia", "Canada", "Lithuania", "United States", "United Kingdom", None]


# ---- the lengths every install shares (central_facts) -----------------------------

def test_only_public_items_have_a_shared_key(tmp_path):
    from agent_media_core import central_facts as C

    assert C.key_for({"id": "yt:AAAAAAAAAAA", "source": "youtube", "_uri": "yt:https://…"}) == "yt:AAAAAAAAAAA"
    k = C.key_for({"id": "pod:x", "source": "podcast", "_uri": "https://cdn.example/a.mp3"})
    assert k.startswith("audio:") and len(k) == 38
    assert C.key_for({"id": "file:x", "source": "file", "_uri": str(tmp_path / "mine.mp3")}) is None
    assert C.key_for({"id": "made:x", "source": "made", "_uri": "/home/me/clip.mp3"}) is None


def test_a_length_the_shared_store_knows_is_not_measured_or_sent(monkeypatch):
    from agent_media_core import central_facts as C

    L.add("https://www.youtube.com/watch?v=AAAAAAAAAAA", "One — Teacher")
    L.add("https://www.youtube.com/watch?v=BBBBBBBBBBB", "Two — Teacher")
    asked = []
    monkeypatch.setattr(C, "lookup", lambda keys: asked.extend(keys) or {"yt:AAAAAAAAAAA": 600})
    measured = []
    monkeypatch.setattr(L, "_measure_all", lambda todo: measured.extend(i["id"] for i in todo) or {"yt:BBBBBBBBBBB": 300})
    monkeypatch.delenv("MEDIA_PRACTICE_DURATIONS")
    # The lengths are filled in the background: here, at once.
    monkeypatch.setattr("threading.Thread", lambda target, **kw: type("T", (), {"start": lambda self: target()})())
    L.items()
    assert sorted(asked) == ["yt:AAAAAAAAAAA", "yt:BBBBBBBBBBB"]
    assert measured == ["yt:BBBBBBBBBBB"]
    lengths = {i["id"]: i["duration_s"] for i in L.items()}
    assert lengths["yt:AAAAAAAAAAA"] == 600 and lengths["yt:BBBBBBBBBBB"] == 300
    # Only what this install measured goes to the store, and only once.
    assert L.to_report() == {"yt:BBBBBBBBBBB": ("yt:BBBBBBBBBBB", 300)}
    L.mark_reported(["yt:BBBBBBBBBBB"])
    assert L.to_report() == {}


def test_a_file_in_the_practice_folder_is_never_sent(tmp_path):
    folder = tmp_path / "Practice"
    folder.mkdir()
    (folder / "Mine.mp3").write_bytes(b"")
    fid = next(i["id"] for i in L.items() if i["source"] == "file")
    L._durations_path().parent.mkdir(parents=True, exist_ok=True)
    L._durations_path().write_text(json.dumps({fid: 120}))
    assert L.to_report() == {}


def test_the_shared_store_off_asks_nothing(monkeypatch):
    from agent_media_core import central_facts as C

    monkeypatch.setattr(C, "_post", lambda *a, **k: pytest.fail("asked the store while off"))
    assert C.lookup(["yt:AAAAAAAAAAA"]) == {}
    assert C.report({"yt:AAAAAAAAAAA": 5}, b"\0" * 32, b"\0" * 32) == (False, {"error": "off"})


def test_state_and_city_from_a_profile():
    sc = lambda **p: L._state_city(p)  # noqa: E731
    assert sc(state="CA", city="Los Angeles") == ("California", "Los Angeles")
    assert sc(region="Manly NSW 2095, Australia") == ("New South Wales", "Manly")
    assert sc(region="Asheville, NC") == ("North Carolina", "Asheville")
    assert sc(region="Stonehouse GL10, UK") == (None, "Stonehouse")
    assert sc(region="Lithuania") == (None, None)


def test_a_report_sends_lengths_signed(monkeypatch):
    from agent_media_core import central_facts as C

    monkeypatch.setenv("MEDIA_FACTS_URL", "https://facts.example/v1")
    sent = []
    monkeypatch.setattr(C, "_post", lambda path, data, headers=None, timeout=15.0:
                        sent.append((path, json.loads(data), headers)) or (True, {"accepted": 1}))
    seed = bytes(range(32))
    from agent_media_core._ed25519 import public_key
    ok, got = C.report({"yt:AAAAAAAAAAA": 60}, seed, public_key(seed))
    assert ok and got == {"accepted": 1}
    assert [(p, b["durations"]) for p, b, _h in sent] == [("/report", {"yt:AAAAAAAAAAA": 60})]
    assert all("X-Sasonica-Signature" in h for _p, _b, h in sent)


def test_a_video_file_is_listed_and_watched_by_signed_link(tmp_path):
    # David, 6 Nov 2026: "Can videos be added to the practise library?"
    d = tmp_path / "Practice"
    d.mkdir()
    (d / "Morning_Yoga.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42")
    (d / "Talk.mkv").write_bytes(b"x")          # not one the WebView plays
    files = [i for i in L.items() if i["source"] == "file"]
    assert [(i["title"], i.get("video")) for i in files] == [("Morning Yoga", "file")]
    got = L.watch(files[0]["id"])
    assert got["ok"] and got["title"] == "Morning Yoga"
    name, _, sig = got["video"].removeprefix("/practice/files/").partition("?sig=")
    assert name.endswith(".mp4") and "/" not in name
    f, ctype = L.served(name, sig)
    assert f == d / "Morning_Yoga.mp4" and ctype == "video/mp4"
    assert L.served(name, "0" * len(sig)) is None
    from agent_media_core import clip_sign
    assert L.served(name, clip_sign.sign(name)) is None   # another scope's signature
    assert L.served("../../x.mp4", L._video_sig("../../x.mp4")) is None
    (d / "Morning_Yoga.mp4").unlink()
    assert L.served(name, sig) is None


def test_a_youtube_link_is_watched_by_its_id():
    L.add("https://youtu.be/AAAAAAAAAAA", title="Flow")
    yt = next(i for i in L.items() if i["source"] == "youtube")
    assert yt["video"] == "youtube" and yt["youtube_id"] == "AAAAAAAAAAA"
    assert L.watch("yt:AAAAAAAAAAA") == {"ok": True, "id": "yt:AAAAAAAAAAA",
                                         "title": "Flow", "youtube_id": "AAAAAAAAAAA"}


def test_only_a_video_can_be_watched(tmp_path):
    (tmp_path / "Practice").mkdir()
    (tmp_path / "Practice" / "Nidra.mp3").write_bytes(b"x")
    audio = next(i for i in L.items() if i["source"] == "file")
    assert "video" not in audio
    assert L.watch(audio["id"])["status"] == 400
    assert L.watch("/etc/passwd")["status"] == 404


def test_watching_pauses_the_same_practice_heard_aloud(tmp_path, monkeypatch):
    # David, 6 Nov 2026: "Add the pause too". The book channel is a fake:
    # nothing here reaches a player.
    from agent_media_core import mcp_server
    d = tmp_path / "Practice"
    d.mkdir()
    (d / "Flow.mp4").write_bytes(b"x")
    (d / "Nidra.mp3").write_bytes(b"x")
    vid = next(i for i in L._files() if i.get("video"))
    other = next(i for i in L._files() if not i.get("video"))
    paused = []
    now = {"uri": vid["_uri"], "target": "phone"}

    class FakeState:
        def get_now_playing(self, sink):
            return now
    monkeypatch.setattr(mcp_server, "_state", lambda: FakeState())
    monkeypatch.setattr(mcp_server, "book_pause", lambda target="": paused.append(target) or {"ok": True})
    monkeypatch.setattr(L, "_book_seconds", lambda target: 754.6)
    monkeypatch.setattr(L, "prepare_sound", lambda it, background=True: None)

    L.mark_playing(other["_uri"])                  # another practice was last played
    assert "paused" not in L.watch(vid["id"]) and paused == []
    L.mark_playing(vid["_uri"])
    now["uri"] = "/books/some-audiobook.m4b"       # the book channel moved on since
    assert "paused" not in L.watch(vid["id"]) and paused == []
    now["uri"] = vid["_uri"]                       # this one, heard aloud
    got = L.watch(vid["id"])
    assert got["paused"] is True and paused == ["phone"]
    assert got["start_s"] == 754                   # the video starts where the sound was


def test_a_video_files_sound_is_extracted_and_played_from_a_point(tmp_path, monkeypatch):
    # A video handed back to sound (play with start_ms) plays its extracted
    # sound, marked as this practice. book_play is a fake.
    from agent_media_core import mcp_server
    d = tmp_path / "Practice"
    d.mkdir()
    (d / "Flow.mp4").write_bytes(b"x")
    vid = next(i for i in L._files() if i.get("video"))
    monkeypatch.setattr(L, "_extract", lambda src, dest: dest.parent.mkdir(parents=True, exist_ok=True) or dest.write_bytes(b"m4a") or True)
    L.prepare_sound(vid, background=False)
    assert L.sound_for(vid).read_bytes() == b"m4a"
    sent = []
    monkeypatch.setattr(mcp_server, "book_play", lambda uri, **kw: sent.append((uri, kw["start_ms"])) or {"ok": True})
    assert L.play(vid["id"], start_ms=754000)["ok"]
    assert sent == [(str(L.sound_for(vid)), 754000)]
    assert L.is_marked(str(L.sound_for(vid)))
    assert json.loads(L._now_path().read_text())["id"] == vid["id"]


def test_downloads_are_kept_within_the_cap_longest_unplayed_first(tmp_path, monkeypatch):
    lib = tmp_path / "lib"
    lib.mkdir()
    monkeypatch.setenv("MEDIA_AUDIOBOOK_LIB", str(lib))
    monkeypatch.setenv("MEDIA_PODCAST_LINK_DIR", str(tmp_path / "links"))
    monkeypatch.setenv("MEDIA_PRACTICE_KEEP_MB", "0.0025")        # 2.5 KiB
    for vid in ("AAAAAAAAAAA", "BBBBBBBBBBB", "CCCCCCCCCCC"):
        L.add(f"https://youtu.be/{vid}", title=vid)
        (lib / f"{vid} [{vid}].mka").write_bytes(b"x" * 1024)
    (lib / "An audiobook [DDDDDDDDDDD].mka").write_bytes(b"x" * 4096)   # not the practice's
    plays = {"yt:AAAAAAAAAAA": {"n": 1, "last": 300}, "yt:BBBBBBBBBBB": {"n": 1, "last": 100},
             "yt:CCCCCCCCCCC": {"n": 1, "last": 200}}
    L._plays_path().write_text(json.dumps(plays))
    L.mark_playing(str(lib / "BBBBBBBBBBB [BBBBBBBBBBB].mka"), "yt:BBBBBBBBBBB")
    gone = L._keep_within_cap()
    # B is oldest but is the one last played: C goes instead.
    assert [f.name for f in gone] == ["CCCCCCCCCCC [CCCCCCCCCCC].mka"]
    assert (lib / "An audiobook [DDDDDDDDDDD].mka").exists()
    # Removing a practice deletes its download.
    assert L.remove("yt:AAAAAAAAAAA") and not (lib / "AAAAAAAAAAA [AAAAAAAAAAA].mka").exists()
    assert (lib / "BBBBBBBBBBB [BBBBBBBBBBB].mka").exists()


def test_an_extracted_sound_whose_video_is_gone_is_deleted(tmp_path):
    d = L._sound_dir()
    d.mkdir(parents=True)
    (d / "0123456789ab.m4a").write_bytes(b"x")
    assert [f.name for f in L._keep_within_cap()] == ["0123456789ab.m4a"]


def test_a_youtube_practice_kept_small_for_offline(monkeypatch):
    # David, 6 Nov 2026: "an option for downloading the video too", small.
    # The add-on is a fake: nothing reaches the phone.
    asked = []

    class Addon:
        LOW_AUDIO = "low"

        def fetch_video(self, vid, dest):
            asked.append(("video", vid))
            dest.mkdir(parents=True, exist_ok=True)
            (dest / f"Flow [{vid}].mp4").write_bytes(b"mp4")
            return dest / f"Flow [{vid}].mp4"

        def fetch_audio(self, vid, dest, fmt="bestaudio"):
            asked.append(("audio", vid, fmt))
            dest.mkdir(parents=True, exist_ok=True)
            (dest / f"Flow [{vid}].webm").write_bytes(b"opus")
            return dest / f"Flow [{vid}].webm"
    monkeypatch.setattr(L, "_downloads_addon", lambda: Addon())
    L.add("https://youtu.be/AAAAAAAAAAA", title="Flow")
    assert next(i for i in L.items() if i["source"] == "youtube")["kept"] is None
    assert "video" not in L.watch("yt:AAAAAAAAAAA")
    L._fetch_small("AAAAAAAAAAA", video=True)            # what keep runs in the background
    assert next(i for i in L.items() if i["source"] == "youtube")["kept"] == "ready"
    kept = L.keep("yt:AAAAAAAAAAA")
    assert kept["kept"] == "ready" and kept["video"].startswith("/practice/files/yt-AAAAAAAAAAA.mp4?sig=")
    got = L.watch("yt:AAAAAAAAAAA")
    assert got["youtube_id"] == "AAAAAAAAAAA"
    name, _, sig = got["video"].removeprefix("/practice/files/").partition("?sig=")
    assert name == "yt-AAAAAAAAAAA.mp4" and L.served(name, sig)[0].read_bytes() == b"mp4"
    # Watching got the sound ready, small, in the practice's own folder.
    assert ("audio", "AAAAAAAAAAA", "low") in asked
    assert L._yt_sound("AAAAAAAAAAA").read_bytes() == b"opus"
    # Removed: the kept video and the sound go with it, and the link is dead.
    assert L.remove("yt:AAAAAAAAAAA")
    assert L.kept_video("AAAAAAAAAAA") is None and L._yt_sound("AAAAAAAAAAA") is None
    assert L.served(name, sig) is None


def test_only_a_practice_with_a_video_is_kept(tmp_path):
    assert L.keep("nothing")["status"] == 404
    (tmp_path / "Practice").mkdir()
    (tmp_path / "Practice" / "Flow.mp4").write_bytes(b"x")
    (tmp_path / "Practice" / "Nidra.mp3").write_bytes(b"x")
    files = {i["title"]: i["id"] for i in L.items() if i["source"] == "file"}
    assert L.keep(files["Nidra"])["status"] == 400
    got = L.keep(files["Flow"])                       # a file: ready at once, its link
    assert got["kept"] == "ready" and L.served(*got["video"].removeprefix("/practice/files/").split("?sig="))
