"""Spaces (spaces.py): projects for agents, Google Contacts labels as groups."""

from __future__ import annotations

from agent_media_server import contacts, spaces

from test_contract import (AUTH, SID, call, server, shelf,  # noqa: F401
                           signed_in, typed)

LABELS = {"labels": [{"name": "Family", "count": 4}, {"name": "My Contacts", "count": 90},
                     {"name": "Close friends", "count": 2}, {"name": "Starred", "count": 3}]}


def test_groups_are_labels_less_everyones(monkeypatch):
    monkeypatch.setattr(contacts, "labels", lambda: LABELS)
    assert spaces.groups() == [
        {"id": "group:family", "kind": "group", "name": "Family", "people": 4},
        {"id": "group:close_friends", "kind": "group", "name": "Close friends", "people": 2}]


def test_a_row_in_its_spaces(monkeypatch):
    monkeypatch.setattr(contacts, "labels", lambda: LABELS)
    rows = [
        # An agent thread: its project.
        {"session": "a", "harness": "claude", "project": "agent-media", "tags": []},
        # An agent thread tagged with a label: the project and the group.
        {"session": "b", "harness": "claude", "project": "spl/sasonica-app", "tags": ["family"]},
        # A chat with a labelled person, tagged with a project: both.
        {"session": "c", "harness": "matrix", "project": None,
         "tags": ["Close_friends", "sasonica_app"]},
        # A chat with someone in no group: People.
        {"session": "d", "harness": "matrix", "tags": ["holiday"]},
        # A quick chat: the Inbox.
        {"session": "e", "harness": "quick", "tags": []},
        # A sealed thread is in nothing.
        {"session": "f", "harness": "claude", "project": "agent-media", "sealed": True, "tags": []},
    ]
    by = {r["session"]: r["spaces"] for r in spaces.add_spaces(rows)}
    assert by == {"a": ["project:agent-media"],
                  "b": ["group:family", "project:spl/sasonica-app"],
                  "c": ["group:close_friends", "project:spl/sasonica-app"],
                  "d": ["people"], "e": ["inbox"], "f": []}
    cat = spaces.catalogue(rows)
    assert [s["id"] for s in cat] == ["inbox", "group:family", "group:close_friends", "people",
                                      "project:agent-media", "project:spl/sasonica-app"]


def test_targets_and_spaces_route(server, shelf, signed_in, typed, monkeypatch):
    monkeypatch.setattr(contacts, "labels", lambda: LABELS)
    _, targets = call(server, "GET", "/targets", headers=AUTH)
    row = {r["session"]: r for r in targets["sessions"]}[SID]
    assert row["spaces"] and all(s in {x["id"] for x in targets["spaces"]} for s in row["spaces"])
    res, obj = call(server, "GET", "/spaces", headers=AUTH)
    assert res.status == 200 and obj["ok"]
    assert obj["spaces"][0] == {"id": "inbox", "kind": "inbox", "name": "Inbox"}
    assert {"id": "group:family", "kind": "group", "name": "Family", "people": 4} in obj["spaces"]
