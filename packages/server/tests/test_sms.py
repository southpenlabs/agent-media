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


def _refusing_bridge(monkeypatch, whoami):
    """A sender whose bridge refuses create_dm (HTTP 400) and answers whoami with `whoami`."""
    monkeypatch.setenv("MATRIX_OWNER_TOKEN", "tok")
    monkeypatch.setenv("MATRIX_OWNER_ID", "@owner:example.org")
    monkeypatch.setenv("MATRIX_HOMESERVER", "http://hs.invalid")
    from agent_media_server import bridge_addon
    bridge = type("B", (), {"url": "http://bridge.invalid", "secret": "s"})()
    monkeypatch.setattr(bridge_addon, "bridges", lambda: type("Bs", (), {"find": lambda self, n: bridge})())

    def http(method, url, token, body, what, timeout=60):
        if "/create_dm/" in url:
            raise sms.SmsError(f"{what} refused it (HTTP 400)")
        if "/whoami" in url:
            if isinstance(whoami, Exception):
                raise whoami
            return whoami
        raise AssertionError(url)
    monkeypatch.setattr(sms.GmessagesSender, "_http", staticmethod(http))
    return sms.GmessagesSender(dict(sms.DEFAULTS))


@pytest.mark.parametrize("whoami, says", [
    ({"logins": []}, "not signed in to the bridge; sign it in again"),
    ({"logins": [{"id": "a", "state": {"state_event": "TRANSIENT_DISCONNECT",
                                        "error": "gm-phone-not-responding"}}]}, "not answering the bridge"),
    ({"logins": [{"id": "a", "state": {"state_event": "BAD_CREDENTIALS", "error": "gm-other"}}]},
     r"bad credentials at the bridge \(gm-other\)"),
    ({"logins": [{"id": "a", "state": {"state_event": "CONNECTED"}}]}, r"refused it \(HTTP 400\)"),
    (sms.SmsError("whoami down"), r"refused it \(HTTP 400\)"),
])
def test_a_refused_chat_says_why_from_the_bridges_logins(monkeypatch, whoami, says):
    with pytest.raises(sms.SmsError, match=says) as e:
        _refusing_bridge(monkeypatch, whoami).send("+61400999888", "Hi", "t")
    assert "61400999888" not in str(e.value)
