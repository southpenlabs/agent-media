"""The context hook's earlier threads (related_threads.py): matched by the
prompt for a new thread, by the notes graph for one with zettels, never a
private or the current one, silent when nothing fits or nothing is there."""
import io
import json
import sqlite3
from array import array

from agent_media_core import private_cards
from agent_media_core import related_threads as rt
from agent_media_core.intake import context

NEW = "11111111-new"


def _zettel(notes, name, zid, source, title, body, thread="", related=()):
    rel = "".join(f"- [[id:{r}][x]]\n" for r in related)
    (notes / f"{name}.org").write_text(
        f":PROPERTIES:\n:ID:          {zid}\n:ZK_SOURCE:   claude:host:{source}\n:END:\n"
        f"#+title: {title}\n#+filetags: :zk:\n\n"
        f"- Source :: [[id:claude:host:{source}][{thread or 'thread ' + source}]]\n\n{body}\n\n"
        "* Related\n# >>> agent-sessions zk: related (regenerated, do not edit) >>>\n"
        f"{rel}# <<< agent-sessions zk: related <<<\n")


def _tree(tmp_path, monkeypatch):
    notes = tmp_path / "org" / "roam" / "notes"
    notes.mkdir(parents=True)
    _zettel(notes, "a", "zk:a:1", "aaaa", "Edit-as-new and Branch are distinct actions",
            "Branch starts a new conversation; edit-as-new prefills the compose box from "
            "an older sent message.", thread="the sent message card")
    _zettel(notes, "b", "zk:b:1", "bbbb", "Pine Note canvas runs four greys",
            "The e-ink canvas draws line art without mid-tones.", related=["zk:a:1"])
    _zettel(notes, "c", "zk:c:1", "cccc", "Kitchen speaker drops the stream",
            "Snapcast client on the kitchen speaker reconnects every hour.")
    (notes / "plain.org").write_text("#+title: no source\n\nbranch compose message\n")
    monkeypatch.setenv("MEDIA_ORG_DIR", str(tmp_path / "org"))
    return notes


def test_a_new_thread_is_matched_by_its_prompt_words(tmp_path, monkeypatch):
    _tree(tmp_path, monkeypatch)
    found = rt.related(NEW, "fix edit-as-new and branch on an older sent message", timeout=0)
    assert [f["session"] for f in found] == ["aaaa"]
    assert found[0]["title"] == "the sent message card"
    assert found[0]["note"].startswith("Edit-as-new")
    text = rt.note(found, "https://h/app/t/")
    assert "[the sent message card](https://h/app/t/aaaa)" in text
    assert found[0]["path"] in text


def test_nothing_scores_well_nothing_is_told(tmp_path, monkeypatch):
    _tree(tmp_path, monkeypatch)
    assert rt.related(NEW, "what's the weather like tomorrow in town", timeout=0) == []
    assert rt.related(NEW, "hi", timeout=0) == []


def test_no_roam_tree_steps_aside(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIA_ORG_DIR", str(tmp_path / "nowhere"))
    assert rt.related(NEW, "fix edit-as-new and branch on an older sent message", timeout=0) == []


def test_private_and_sealed_and_current_threads_are_left_out(tmp_path, monkeypatch):
    _tree(tmp_path, monkeypatch)
    prompt = "fix edit-as-new and branch on an older sent message"
    assert all(f["session"] != "aaaa" for f in rt.related("aaaa", prompt, timeout=0))
    priv = tmp_path / "private.d"
    priv.mkdir(exist_ok=True)
    (priv / "aaaa").write_text("{}")
    assert rt.related(NEW, prompt, timeout=0) == []
    (priv / "aaaa").unlink()
    from agent_media_core import sealed
    monkeypatch.setattr(sealed, "is_sealed", lambda s: s == "aaaa")
    assert rt.related(NEW, prompt, timeout=0) == []


def test_a_private_thread_gets_private_cards_only(tmp_path, monkeypatch):
    _tree(tmp_path, monkeypatch)
    priv = tmp_path / "private.d"
    priv.mkdir(exist_ok=True)
    (priv / NEW).write_text("{}")
    asked = []
    monkeypatch.setattr(private_cards, "related", lambda text, exclude=None, limit=3: asked.append(exclude) or [
        {"session": "pppp", "title": "the quiet one", "score": 0.8, "how": "close in meaning"},
        {"session": NEW, "title": "me", "score": 0.9}])
    found = rt.related(NEW, "fix edit-as-new and branch on an older sent message", timeout=0)
    assert [f["session"] for f in found] == ["pppp"] and asked == [{NEW}]
    text = rt.note(found, "https://h/app/t/")
    assert "[the quiet one](https://h/app/t/pppp)" in text and "private" in text
    # An ordinary chat never asks the cards.
    asked.clear()
    rt.related("2222-ordinary", "fix edit-as-new and branch on an older sent message", timeout=0)
    assert asked == []


def test_a_private_thread_without_cards_is_told_nothing(tmp_path, monkeypatch):
    _tree(tmp_path, monkeypatch)
    priv = tmp_path / "private.d"
    priv.mkdir(exist_ok=True)
    (priv / NEW).write_text("{}")
    def boom(*a, **k):
        raise RuntimeError("no key")
    monkeypatch.setattr(private_cards, "related", boom)
    assert rt.related(NEW, "fix edit-as-new and branch on an older sent message", timeout=0) == []


def test_a_thread_with_zettels_takes_the_graph(tmp_path, monkeypatch):
    _tree(tmp_path, monkeypatch)
    # b's note lists a's as Related: each reaches the other, whatever the prompt.
    assert [f["session"] for f in rt.related("bbbb", "unrelated words here", timeout=0)] == ["aaaa"]
    found = rt.related("aaaa", "unrelated words here", timeout=0)
    assert [f["session"] for f in found] == ["bbbb"]
    assert found[0]["note"] == "Pine Note canvas runs four greys"


def test_the_stored_vectors_decide_when_the_model_answers(tmp_path, monkeypatch):
    _tree(tmp_path, monkeypatch)
    db = tmp_path / "zk-index.sqlite"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE zk_embeddings (node_id TEXT PRIMARY KEY, model TEXT, "
                 "content_hash TEXT, dim INTEGER, vec BLOB)")
    unit = lambda i: [1.0 if j == i else 0.0 for j in range(rt.DIM)]  # noqa: E731
    for zid, i in (("zk:a:1", 0), ("zk:b:1", 1), ("zk:c:1", 2)):
        conn.execute("INSERT INTO zk_embeddings VALUES (?, 'm', 'h', ?, ?)",
                     (zid, rt.DIM, array("f", unit(i)).tobytes()))
    conn.commit()
    conn.close()
    monkeypatch.setattr(rt, "embed", lambda text, timeout: ("m", unit(2)))
    # No word in common with c's note: the vectors find it.
    found = rt.related(NEW, "the loudspeaker by the stove keeps cutting out", timeout=1)
    assert [f["session"] for f in found] == ["cccc"]


def test_the_notes_are_cached_by_mtime(tmp_path, monkeypatch):
    notes = _tree(tmp_path, monkeypatch)
    assert len(rt.load_notes()) == 3
    calls = []
    real = rt.parse
    monkeypatch.setattr(rt, "parse", lambda *a: calls.append(a) or real(*a))
    assert len(rt.load_notes()) == 3 and calls == []
    _zettel(notes, "d", "zk:d:1", "dddd", "New", "words")
    assert len(rt.load_notes()) == 4 and len(calls) == 1


def _hook(monkeypatch, capsys, payload):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    context.main()
    return capsys.readouterr().out


def test_the_hook_tells_it_once_with_the_thread_link(tmp_path, monkeypatch, capsys):
    _tree(tmp_path, monkeypatch)
    monkeypatch.setenv("MEDIA_THREAD_LINK", "0")
    monkeypatch.setenv("MEDIA_AGENT_NOTES", "0")
    monkeypatch.setenv("MEDIA_PUBLIC_URL", "https://h.example")
    monkeypatch.setenv("MEDIA_RELATED_TIMEOUT", "0")
    payload = {"session_id": NEW, "hook_event_name": "UserPromptSubmit",
               "prompt": "fix edit-as-new and branch on an older sent message"}
    out = json.loads(_hook(monkeypatch, capsys, payload))
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "[the sent message card](https://h.example/app/t/aaaa)" in ctx
    assert _hook(monkeypatch, capsys, payload) == ""


def test_the_hook_gives_up_after_three_quiet_prompts(tmp_path, monkeypatch, capsys):
    _tree(tmp_path, monkeypatch)
    monkeypatch.setenv("MEDIA_THREAD_LINK", "0")
    monkeypatch.setenv("MEDIA_AGENT_NOTES", "0")
    monkeypatch.setenv("MEDIA_RELATED_TIMEOUT", "0")
    quiet = {"session_id": NEW, "hook_event_name": "UserPromptSubmit",
             "prompt": "what's the weather like tomorrow in town"}
    for _ in range(3):
        assert _hook(monkeypatch, capsys, quiet) == ""
    hit = dict(quiet, prompt="fix edit-as-new and branch on an older sent message")
    assert _hook(monkeypatch, capsys, hit) == ""


def test_the_hook_switch_keeps_it_out(tmp_path, monkeypatch, capsys):
    _tree(tmp_path, monkeypatch)
    monkeypatch.setenv("MEDIA_THREAD_LINK", "0")
    monkeypatch.setenv("MEDIA_AGENT_NOTES", "0")
    monkeypatch.setenv("MEDIA_RELATED_THREADS", "0")
    assert _hook(monkeypatch, capsys, {"session_id": NEW, "hook_event_name": "UserPromptSubmit",
                                       "prompt": "fix edit-as-new and branch on an older sent message"}) == ""
