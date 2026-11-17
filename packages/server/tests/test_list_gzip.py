"""The thread list's answers compressed for a client that takes gzip (18 Nov
2026: /targets was 776 KB on red2, 141 KB gzipped)."""

from __future__ import annotations

import gzip
import json

from agent_media_server import sessions

from test_contract import AUTH, call, server, shelf, signed_in, typed  # noqa: F401


def test_targets_gzipped_when_asked(server, shelf, signed_in, monkeypatch):
    real = sessions.targets

    def big(bearer, history=""):
        ok, d = real(bearer, history)
        return ok, {**d, "padding": ["row"] * 5000}     # past _json_z's 4 KB floor
    monkeypatch.setattr(sessions, "targets", big)
    res, raw = call(server, "GET", "/targets", headers={**AUTH, "Accept-Encoding": "gzip"})
    assert res.status == 200 and res.getheader("Content-Encoding") == "gzip"
    assert json.loads(gzip.decompress(raw))["ok"] is True
    res, obj = call(server, "GET", "/targets", headers=AUTH)
    assert res.status == 200 and res.getheader("Content-Encoding") is None and obj["ok"] is True
