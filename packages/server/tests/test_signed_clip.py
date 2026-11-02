"""`GET /speech/clips/<name>?sig=` (24 Oct 2026): a server-rendered clip by
signed link, for the phone off the tailnet. The key and the clips are the
test's own (XDG_STATE_HOME, MEDIA_AUDIO_DIR)."""

from __future__ import annotations

import pytest

from agent_media_core import clip_sign

from test_contract import call, server, typed  # noqa: F401


@pytest.fixture()
def clips(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("MEDIA_AUDIO_DIR", str(tmp_path / "audio"))
    d = tmp_path / "audio"
    d.mkdir()
    (d / "r--000.mp3").write_bytes(b"ID3clip")
    (d / "r--000.txt").write_text("words")
    return d


def test_a_signed_link_plays_without_a_bearer(server, clips):
    res, body = call(server, "GET", f"/speech/clips/r--000.mp3?sig={clip_sign.sign('r--000.mp3')}")
    assert res.status == 200
    assert res.getheader("Content-Type") == "audio/mpeg"
    assert body == b"ID3clip"


@pytest.mark.parametrize("path", [
    "/speech/clips/r--000.mp3",
    "/speech/clips/r--000.mp3?sig=00000000000000000000000000000000",
    "/speech/clips/r--001.mp3?sig={r--001.mp3}",
    "/speech/clips/r--000.txt?sig={r--000.txt}",
    "/speech/clips/..%2Fclip-sign.key?sig={../clip-sign.key}",
])
def test_anything_else_is_not_found(server, clips, path):
    if "{" in path:
        name = path[path.index("{") + 1:-1]
        path = path[:path.index("{")] + clip_sign.sign(name)
    res, _ = call(server, "GET", path)
    assert res.status == 404


def test_a_signature_is_for_its_own_clip(clips):
    assert clip_sign.sign("a.mp3") != clip_sign.sign("b.mp3")
    assert clip_sign.signed("https://x/speech/clips/", "a.mp3") == \
        f"https://x/speech/clips/a.mp3?sig={clip_sign.sign('a.mp3')}"
    assert (clips.parent / "state" / "agent-media" / "clip-sign.key").stat().st_mode & 0o077 == 0


def test_a_saved_podcast_download_by_signed_link(server, monkeypatch, tmp_path):
    # `GET /podcasts/files/<id><ext>?sig=` (2 Nov 2026): the phone's music
    # player fetches a saved link's download off the tailnet.
    from agent_media_core import podcast_links

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("MEDIA_PODCAST_LINK_DIR", str(tmp_path / "links"))
    monkeypatch.setenv("MEDIA_PODCAST_LINK_FETCH", "0")
    monkeypatch.setattr(podcast_links, "_title", lambda vid: "Talk")
    podcast_links.add("https://youtu.be/AAAAAAAAAAA")
    (tmp_path / "links").mkdir()
    (tmp_path / "links" / "Talk [AAAAAAAAAAA].opus").write_bytes(b"OggS")
    sig = podcast_links._sig("AAAAAAAAAAA.opus")
    res, body = call(server, "GET", f"/podcasts/files/AAAAAAAAAAA.opus?sig={sig}")
    assert res.status == 200 and res.getheader("Content-Type") == "audio/ogg" and body == b"OggS"
    res, _ = call(server, "GET", f"/podcasts/files/AAAAAAAAAAA.opus?sig={clip_sign.sign('AAAAAAAAAAA.opus')}")
    assert res.status == 404
