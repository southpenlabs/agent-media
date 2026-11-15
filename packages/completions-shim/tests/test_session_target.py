"""A conversation by its session id as the shim's target (#491 step 5)."""

import agent_media_completions_shim as C


def test_session_target_reads_and_sends_by_id(monkeypatch):
    sid = "027c24b1-a031-44a8-97e4-f890c45494d4"
    monkeypatch.setenv("MEDIA_COMPLETIONS_TARGET", f"session:{sid}")
    monkeypatch.setenv("MEDIA_COMPLETIONS_MUTE", "0")
    monkeypatch.setenv("MEDIA_COMPLETIONS_SETTLE", "0")
    monkeypatch.setattr(C, "POLL_S", 0.0)
    turns = [["earlier"]]
    gets, posts = [], []

    def get(path):
        gets.append(path)
        return {"turns": turns[-1], "state": "waiting"}

    def post(path, body):
        posts.append((path, body))
        turns.append(["earlier", "the answer"])
        return 200, {"ok": True}

    monkeypatch.setattr(C, "_get", get)
    monkeypatch.setattr(C, "_post", post)
    assert C.converse("what now?") == "the answer"
    assert posts == [("/input", {"text": "what now?", "target": f"session:{sid}"})]
    assert all(p.startswith(f"/peek?session={sid}") for p in gets)
