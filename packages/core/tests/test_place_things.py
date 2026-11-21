"""`media place thing` (place_things.py): an agent puts a device, room or
speaker on the Map by its name (maps proposal step 4)."""
from __future__ import annotations

from agent_media_core import cli, place_things

LISTING = {"things": [{"kind": "room", "id": "kitchen-music", "name": "Kitchen", "label": "Kitchen speaker",
                       "lat": 1.0, "lon": 2.0}],
           "unplaced": [{"kind": "room", "id": "study-music", "name": "Study"},
                        {"kind": "room", "id": "kids-study-music", "name": "Kids' study"},
                        {"kind": "device", "id": "d_1", "name": "Pixel 8a"},
                        {"kind": "speaker", "id": "local", "name": "red2"}]}


def test_match_by_id_name_label_and_words():
    assert [r["id"] for r in place_things.match(LISTING, "room", "kitchen speaker")] == ["kitchen-music"]
    assert [r["id"] for r in place_things.match(LISTING, "room", "kitchen")] == ["kitchen-music"]
    assert [r["id"] for r in place_things.match(LISTING, "room", "Study")] == ["study-music"]  # exact wins
    assert [r["id"] for r in place_things.match(LISTING, "", "pixel")] == ["d_1"]
    assert [r["id"] for r in place_things.match(LISTING, "speaker", "local")] == ["local"]
    assert place_things.match(LISTING, "speaker", "pixel") == []
    assert place_things.point("-37.8, 144.9") == (-37.8, 144.9) and place_things.point("x") is None


def test_place_here_posts_the_phones_point(monkeypatch, capsys):
    sent = []

    def call(method, path, body=None, timeout=10.0):
        if method == "GET":
            return 200, LISTING
        sent.append(body)
        return 200, {"thing": {**body, "name": "Study"}}

    monkeypatch.setattr("agent_media_core.phone_ask._call", call)
    monkeypatch.setattr("agent_media_core.place_find.near_point", lambda near: (-37.8, 144.96))
    parser = cli._build_parser()
    args = parser.parse_args(["place", "thing", "room", "study", "--here", "--label", "Desk"])
    assert args.func(args) == 0
    assert sent == [{"kind": "room", "id": "study-music", "lat": -37.8, "lon": 144.96, "label": "Desk"}]
    assert "Desk placed @ -37.8,144.96" in capsys.readouterr().out
    args = parser.parse_args(cli._place_point_opt(["place", "thing", "any", "red2", "-37.7,144.9"]))
    assert args.func(args) == 0 and sent[-1] == {"kind": "speaker", "id": "local", "lat": -37.7, "lon": 144.9}
    args = parser.parse_args(["place", "thing", "room", "attic", "--remove"])
    assert args.func(args) == 1 and "known: room Kitchen speaker" in capsys.readouterr().err
