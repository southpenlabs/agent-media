"""The popup's book row: the book mpv's socket. (The phone's own player, the
Sasonica ABS app, was asked first until the app was retired, 11 Nov 2026.)"""

from agent_media_visual import canvas


def test_the_book_row_is_the_mpv_socket(monkeypatch):
    monkeypatch.setattr(canvas, "_book_title", lambda: "On the shelf")
    monkeypatch.setattr(canvas, "_media", lambda args: "▶ 00:10 / 01:00")
    row = canvas.channel_status("book")
    assert (row["label"], row["status"]) == ("On the shelf", "▶ 00:10 / 01:00")
