"""Places: a linked folder is one place; two folders of one name are told apart
(David, 17 Nov 2026: websites and sasonica-app each listed twice)."""
import os

from agent_media_server import sessions


def test_clones_of_one_name_are_told_apart(monkeypatch):
    from agent_media_core import layout

    monkeypatch.setattr(layout, "projects", lambda: False)
    names = sessions._place_names(["/h/projects/app", "/h/spl/projects/sasonica/app", "/h/projects/other"])
    assert names == {"/h/projects/app": "projects/app",
                     "/h/spl/projects/sasonica/app": "sasonica/app",
                     "/h/projects/other": "other"}


def test_a_linked_folder_is_one_place(monkeypatch, tmp_path):
    real = tmp_path / "spl" / "websites"
    real.mkdir(parents=True)
    link = tmp_path / "websites"
    link.symlink_to(real)
    cwds = {"a": str(link), "b": str(real)}
    monkeypatch.setattr(sessions, "live_sessions", lambda: [])
    monkeypatch.setattr(sessions, "_manifest_dir", lambda: tmp_path / "none")
    monkeypatch.setattr(sessions, "_history", lambda: [(2.0, "a"), (1.0, "b")])
    monkeypatch.setattr(sessions, "transcript_cwd", lambda sid: cwds.get(sid, ""))
    out = sessions.places(limit=0)
    assert [(p["name"], p["path"], p["at"]) for p in out] == [("websites", os.path.realpath(real), 2.0)]
