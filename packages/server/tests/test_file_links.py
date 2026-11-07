"""Files an agent names in a reply, as chips (file_links.py, §6.18). David,
3 Nov 2026: "that PNG link isn't clickable by the way. can we get any
relevant formats clickable from here"."""

from __future__ import annotations

import os
import shutil

import pytest

from agent_media_server import auth, file_links, threads, uploads


@pytest.fixture()
def root(tmp_path, monkeypatch):
    r = tmp_path / "shared"
    monkeypatch.setenv("MEDIA_UPLOAD_DIR", str(r))
    monkeypatch.setattr(auth, "gate", lambda bearer: ({"id": "u"}, {}) if bearer == "good"
                        else (None, {"error": "who?", "status": 401}))
    return r


def _file(path, data=b"\x89PNG data"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _lines(text):
    return [ln[len("Shared file: "):] for ln in text.splitlines() if ln.startswith("Shared file: ")]


def _reply(text, role="assistant", **extra):
    return {"id": "a-1", "role": role, "at": 1.0, "turn": {"running": False},
            "parts": [{"type": "text", "text": text}], **extra}


def test_a_bare_path_in_a_reply_becomes_a_chip_the_app_can_fetch(tmp_path, root):
    shot = _file(tmp_path / "shots" / "ask-08-way-out.png")
    text = f"Here is the screen: {shot}. Looks right."
    out = file_links.link_text(text)
    assert out.startswith(text + "\n\nShared file: ")
    (copy,) = _lines(out)
    assert copy.startswith(str(root) + "/") and copy.endswith("/ask-08-way-out.png")
    ok, detail = uploads.find(copy, "good")
    assert ok and detail["type"] == "image/png"
    # Read again: the same line, no second copy.
    assert file_links.link_text(text) == out
    assert file_links.link_text(out) == out
    assert len(list(root.rglob("*.png"))) == 1


@pytest.mark.parametrize("form", ["`{p}`", "[the shot]({p})", "file://{p}", "({p})", "{p},"])
def test_backticks_links_and_punctuation(tmp_path, root, form):
    shot = _file(tmp_path / "my shots" / "a.png" if form == "`{p}`" else tmp_path / "a.png")
    (copy,) = _lines(file_links.link_text("See " + form.format(p=shot) + " now"))
    assert copy.endswith("/a.png")


def test_a_home_relative_path(tmp_path, root, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    _file(tmp_path / "home" / "out" / "talk.mp3", b"ID3")
    (copy,) = _lines(file_links.link_text("Rendered to ~/out/talk.mp3 for you."))
    assert copy.endswith("/talk.mp3")


@pytest.mark.parametrize("name", ["a.png", "b.jpeg", "c.webm", "d.mov", "e.opus", "f.flac", "g.pdf"])
def test_the_kinds_linked(tmp_path, root, name):
    p = _file(tmp_path / name)
    assert len(_lines(file_links.link_text(f"made {p}"))) == 1


@pytest.mark.parametrize("name", ["notes.md", "log.txt", "data.csv", "page.html", "fig.svg",
                                  "script.py", "archive.tar.gz"])
def test_kinds_not_linked_from_a_bare_path(tmp_path, root, name):
    p = _file(tmp_path / name)
    assert file_links.link_text(f"wrote {p}") == f"wrote {p}"


def test_what_is_never_linked(tmp_path, root, monkeypatch):
    monkeypatch.setenv("MEDIA_SHARED_LINK_MAX_MB", "1")
    never = [
        _file(tmp_path / ".ssh" / "id.png"),
        _file(tmp_path / ".config" / "app" / "shot.png"),
        _file(tmp_path / "secrets" / "scan.pdf"),
        _file(tmp_path / "dotfiles-secrets" / "x.png"),
        _file(tmp_path / "My-Secret-Stuff" / "x.png"),
        _file(tmp_path / "big.mp4", b"\0" * ((1 << 20) + 1)),
        _file(tmp_path / "empty.png", b""),
        tmp_path / "missing.png",
    ]
    (tmp_path / "dir.png").mkdir()
    never.append(tmp_path / "dir.png")
    # A symlink from a harmless name into a secrets folder is judged by where it leads.
    link = tmp_path / "harmless.png"
    link.symlink_to(tmp_path / "secrets" / "scan.pdf")
    never.append(link)
    unreadable = _file(tmp_path / "locked.png")
    unreadable.chmod(0)
    if not os.access(unreadable, os.R_OK):    # root reads anything
        never.append(unreadable)
    for p in never:
        text = f"see {p}"
        assert file_links.link_text(text) == text, p
    assert not root.exists()


def test_urls_and_longer_names_are_not_paths(tmp_path, root):
    _file(tmp_path / "a.png")
    _file(tmp_path / "a.png.bak")
    for text in (f"https://example.com{tmp_path}/a.png", f"{tmp_path}/a.png.bak",
                 f"x{tmp_path}/a.png", "a relative shots/a.png"):
        assert file_links.link_text(text) == text, text


def test_a_screenshot_folder_of_a_browser_tool_is_allowed(tmp_path, root):
    p = _file(tmp_path / "proj" / ".playwright-mcp" / "page.png")
    assert len(_lines(file_links.link_text(f"saved {p}"))) == 1


def test_a_shared_file_line_outside_the_root_is_pointed_at_a_copy(tmp_path, root):
    shot = _file(tmp_path / "chat-shots" / "ask.png")
    text = f"The card:\n\nShared file: {shot}"
    out = file_links.link_text(text)
    (copy,) = _lines(out)
    assert copy != str(shot) and copy.startswith(str(root))
    assert out == f"The card:\n\nShared file: {copy}"
    # One under the root already stays as written, and gets no second line.
    assert file_links.link_text(out) == out


def test_a_file_media_attach_shared_gets_no_second_chip(tmp_path, root):
    from agent_media_core import shared_files

    shot = _file(tmp_path / "shot.png")
    kept = shared_files.share(shot)
    text = f"I took {shot}.\n\nShared file: {kept}"
    assert file_links.link_text(text) == text
    assert len(list(root.rglob("*.png"))) == 1


def test_the_copy_is_remembered_across_a_restart_and_redone_when_the_file_changes(tmp_path, root):
    shot = _file(tmp_path / "shot.png")
    first = _lines(file_links.link_text(f"see {shot}"))
    file_links.reset_cache()        # the server restarted
    assert _lines(file_links.link_text(f"see {shot}")) == first
    shot.write_bytes(b"\x89PNG changed, longer")
    second = _lines(file_links.link_text(f"see {shot}"))
    assert second != first and len(list(root.rglob("*.png"))) == 2
    # A copy deleted from the root is made again.
    os.unlink(second[0])
    assert _lines(file_links.link_text(f"see {shot}"))[0] and len(list(root.rglob("*.png"))) == 2


def test_only_the_assistants_words_are_read(tmp_path, root, monkeypatch):
    shot = _file(tmp_path / "shot.png")
    said = f"look at {shot}"
    msgs = [
        _reply(said, role="user"),
        {"id": "a-2", "role": "assistant", "at": 2.0, "turn": {"running": False},
         "parts": [{"type": "reasoning", "text": said, "redacted": False},
                   {"type": "tool", "name": "Bash", "title": said, "input_summary": said,
                    "result_summary": said, "status": "done", "tool_use_id": "t"},
                   {"type": "text", "text": said}]},
    ]
    monkeypatch.setattr(threads, "_messages_for", lambda *a, **k: (msgs, False))
    got, _ = threads.messages_for("s", [], working=False, live=False)
    assert got[0]["parts"][0]["text"] == said
    parts = got[1]["parts"]
    assert [p.get("text") for p in parts[:1]] == [said]
    assert parts[1]["result_summary"] == said and parts[1]["input_summary"] == said
    assert len(_lines(parts[2]["text"])) == 1


def test_a_copy_that_cannot_be_made_leaves_the_words(tmp_path, root, monkeypatch):
    shot = _file(tmp_path / "shot.png")

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(shutil, "copy2", boom)
    assert file_links.link_text(f"see {shot}") == f"see {shot}"


def test_a_shared_file_line_whose_file_is_gone_points_at_its_earlier_copy(tmp_path, root):
    # David, 8 Nov 2026: a worktree removed after its screenshot was shared.
    shot = _file(tmp_path / "wt" / "shot.png")
    text = f"Done.\n\nShared file: {shot}"
    first = _lines(file_links.link_text(text))
    shot.unlink()
    file_links.reset_cache()
    assert _lines(file_links.link_text(text)) == first


def test_a_gone_file_never_copied_keeps_its_line(tmp_path, root):
    text = f"Shared file: {tmp_path / 'never.png'}"
    assert file_links.link_text(text) == text
