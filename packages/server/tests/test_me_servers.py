"""GET /me/servers: the account's servers from its issuer, this one marked;
the app's "Pair another server" lists the others (14 Nov 2026)."""

import pytest

from agent_media_server import app, devices, servers_report

ISS = "https://cms.sasonica.test"


@pytest.fixture(autouse=True)
def _fresh():
    devices._reset_for_tests()
    app._ACCESS.clear()
    yield
    app._ACCESS.clear()


def test_the_accounts_servers_with_this_one_marked(monkeypatch):
    got = devices.enrol_account({"iss": ISS, "sub": "1", "email": "a@example.com"}, "phone",
                                refresh_token="rt-0", username="a")
    asked = []
    monkeypatch.setattr(app, "_account_access", lambda did, iss, fresh=False: asked.append(fresh) or "at")
    calls = []

    def get(iss, access):
        calls.append(iss)
        if len(calls) == 1:
            return 401, {}                       # a token the issuer no longer takes: once more, fresh
        return 200, {"ok": True, "servers": [{"id": "here", "name": "red2"}, {"id": "other", "name": "red5"}]}

    monkeypatch.setattr(servers_report, "_get", get)
    monkeypatch.setattr(servers_report, "server_id", lambda: "here")
    status, out = servers_report.account_servers(got["device_id"], ISS)
    assert status == 200 and asked == [False, True] and calls == [ISS, ISS]
    assert out["servers"] == [{"id": "here", "name": "red2", "this": True},
                              {"id": "other", "name": "red5", "this": False}]
