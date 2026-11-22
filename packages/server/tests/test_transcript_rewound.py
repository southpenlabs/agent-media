"""Turns a rewind set aside (transcript.py `Builder._rewind`, proposal
docs/proposals/2026-11-23-rewind-a-chat.md §2).

`claude --resume <id> --resume-session-at <uuid>` (and a terminal /rewind)
keeps the file and writes the next prompt with `parentUuid` = that earlier
record, so the turns after it stay in the file as a dead branch. The records
below are trimmed from a real rewound transcript (uuids shortened, prompts
APPLE / BANANA, then a prompt whose parent is the first reply's thinking
record); everything else is synthetic.
"""

from __future__ import annotations

import json

import pytest

from agent_media_server import transcript as T


def _user(uid, parent, text, ts):
    return {"type": "user", "uuid": uid, "parentUuid": parent, "timestamp": ts,
            "message": {"role": "user", "content": text}}


def _asst(uid, parent, block, ts):
    return {"type": "assistant", "uuid": uid, "parentUuid": parent, "timestamp": ts,
            "message": {"role": "assistant", "content": [block], "stop_reason": "end_turn"}}


def _stop(uid, parent, ts):
    return {"type": "system", "subtype": "stop_hook_summary", "uuid": uid,
            "parentUuid": parent, "timestamp": ts}


def _att(uid, parent, ts):
    return {"type": "attachment", "uuid": uid, "parentUuid": parent, "timestamp": ts,
            "attachment": {"type": "hook_success"}}


THINK = {"type": "thinking", "thinking": "", "signature": "x"}

REWIND_TS = "2026-11-22T19:08:33.216Z"


def records(parent="6ce504fe", rewound_text="List every word I asked you to remember."):
    return [
        _user("82e790e1", None, "Remember the word APPLE. Reply with just OK.",
              "2026-11-22T19:08:08.146Z"),
        _att("a3f6285f", "82e790e1", "2026-11-22T19:08:08.980Z"),
        _asst("6ce504fe", "a3f6285f", THINK, "2026-11-22T19:08:10.982Z"),
        _asst("7ff63762", "6ce504fe", {"type": "text", "text": "OK"},
              "2026-11-22T19:08:11.001Z"),
        _att("e730778e", "7ff63762", "2026-11-22T19:08:11.033Z"),
        _stop("46c7a9e8", "e730778e", "2026-11-22T19:08:11.087Z"),
        _user("24491402", "46c7a9e8", "Now also remember BANANA. Reply OK.",
              "2026-11-22T19:08:20.733Z"),
        _att("5f054a39", "24491402", "2026-11-22T19:08:20.731Z"),
        _asst("9e63fe5b", "5f054a39", {"type": "text", "text": "OK"},
              "2026-11-22T19:08:23.352Z"),
        _stop("aaff4d51", "9e63fe5b", "2026-11-22T19:08:23.437Z"),
        # The rewind: the parent is a record of the first reply.
        _user("4735cc8d", parent, rewound_text, REWIND_TS),
        _att("19feffe6", "4735cc8d", "2026-11-22T19:08:33.214Z"),
        _asst("e8aa9078", "19feffe6", THINK, "2026-11-22T19:08:36.855Z"),
        _asst("b2b1bc05", "e8aa9078", {"type": "text", "text": "APPLE"},
              "2026-11-22T19:08:37.091Z"),
        _stop("027d0ae3", "b2b1bc05", "2026-11-22T19:08:37.191Z"),
        _user("787f6688", "027d0ae3", "List them again.", "2026-11-22T19:08:46.796Z"),
        _asst("aa636729", "787f6688", {"type": "text", "text": "APPLE"},
              "2026-11-22T19:08:49.446Z"),
        _stop("219a7493", "aa636729", "2026-11-22T19:08:49.538Z"),
    ]


def build(recs) -> list[dict]:
    b = T.Builder()
    for r in recs:
        b.feed(r)
    b.settle()
    return b.messages


def text_of(m):
    return " ".join(p.get("text", "") for p in m["parts"]).strip()


def rewound(msgs):
    return [i for i, m in enumerate(msgs) if "rewound" in m]


@pytest.mark.parametrize("parent", ["6ce504fe", "7ff63762"],
                         ids=["mid-message (thinking record)", "reply's last record"])
def test_turns_after_the_parent_are_marked(parent):
    msgs = build(records(parent))
    assert [m["role"] for m in msgs] == ["user", "assistant"] * 4
    # The message holding the parent is kept; BANANA and its answer are set aside.
    assert rewound(msgs) == [2, 3]
    assert text_of(msgs[2]).startswith("Now also remember BANANA")
    for m in msgs[2:4]:
        assert m["rewound"] == {"to": parent, "at": T.epoch(REWIND_TS)}
    assert not msgs[3]["turn"]["running"]


def test_rewind_to_the_first_prompt_sets_aside_its_answer():
    msgs = build(records("82e790e1"))
    assert rewound(msgs) == [1, 2, 3]


def test_an_ordinary_conversation_marks_nothing():
    assert rewound(build(records("aaff4d51"))) == []


def test_same_words_after_a_rewind_are_a_new_message():
    recs = records("7ff63762", rewound_text="Now also remember BANANA. Reply OK.")
    # BANANA went unanswered, then was sent again after the rewind.
    del recs[7:10]
    msgs = build(recs)
    assert [text_of(m) for m in msgs[:4]] == [
        "Remember the word APPLE. Reply with just OK.", "OK",
        "Now also remember BANANA. Reply OK.", "Now also remember BANANA. Reply OK."]
    assert rewound(msgs) == [2]


def test_a_parent_out_of_view_folds_nothing():
    # A tail read starting at BANANA never saw the first reply.
    assert rewound(build(records()[6:])) == []


def test_a_second_rewind_keeps_the_first_marking():
    recs = records("7ff63762")
    recs.append(_user("99999999", "82e790e1", "Start over.", "2026-11-22T19:09:00Z"))
    msgs = build(recs)
    assert rewound(msgs) == list(range(1, 8))
    assert msgs[2]["rewound"]["to"] == "7ff63762"
    assert msgs[1]["rewound"]["to"] == "82e790e1"
    assert msgs[4]["rewound"]["to"] == "82e790e1"


def test_merge_older_shifts_and_places_the_rewind(tmp_path, monkeypatch):
    """A tail read that starts at the rewound prompt cannot see its parent;
    reading the older records in places it."""
    T._reset_for_tests()
    monkeypatch.setattr(T, "TAIL_PROMPTS", 2)
    path = tmp_path / "t.jsonl"
    path.write_text("".join(json.dumps(r, separators=(",", ":")) + "\n" for r in records()))
    msgs, more = T._read(str(path), False)
    assert more and len(msgs) == 4 and rewound(msgs) == []
    msgs, more = T._read(str(path), True)
    assert not more
    assert msgs == build(records())
    assert rewound(msgs) == [2, 3]
    # Indices kept by the tail builder moved up with its messages: a later
    # rewind to the rewound prompt's answer cuts after it.
    with path.open("a") as fh:
        fh.write(json.dumps(_user("88888888", "b2b1bc05", "Only the first?",
                                  "2026-11-22T19:09:00Z"), separators=(",", ":")) + "\n")
    msgs, _ = T._read(str(path), True)
    assert rewound(msgs) == [2, 3, 6, 7]
    assert msgs[6]["rewound"]["to"] == "b2b1bc05"
    T._reset_for_tests()


def test_harness_builders_inherit_a_working_merge():
    b = T.CodexBuilder()
    b.merge_older(T.CodexBuilder())
    assert b.messages == [] and b._at == {}
