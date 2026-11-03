"""media sms send: one text through the Google Messages bridge. No real bridge."""
import os

import pytest

from agent_media_server import sms


def test_the_bridge_sender_without_a_login_says_so(monkeypatch, tmp_path):
    for k in list(os.environ):
        if k.startswith("MATRIX_"):
            monkeypatch.delenv(k, raising=False)
    with pytest.raises(sms.SmsError, match="matrix-owner-login"):
        sms.GmessagesSender({**sms.DEFAULTS, "matrix_env": str(tmp_path / "none")}).send("+61400999888", "Hi", "t")


def test_send_wants_an_international_number_and_a_body_and_never_echoes_the_number(monkeypatch):
    sent = []
    monkeypatch.setattr(sms, "_sender", lambda cfg: type("S", (), {
        "send": lambda self, to, body, txn: sent.append((to, body, txn)) or "$e"})())
    with pytest.raises(sms.SmsError) as e:
        sms.send("0400999888", "Hi")
    assert "0400999888" not in str(e.value)
    with pytest.raises(sms.SmsError):
        sms.send("+61400999888", "  ")
    got = sms.send("+61400999888", "Hi", "txn-9")
    assert got == {"ok": True, "event_id": "$e", "txn": "txn-9"}
    assert sms.send("+61400999888", "Hi")["txn"].startswith("sms-")
