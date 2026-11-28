"""The same device paired again (28 Nov 2026: two "TCL Smart TV"s and a
"p8a-firefox" beside "p8a (Firefox)" sat in Devices for weeks). An install
id the app reports retires the old pairing at once; pairings made before it
are guessed from name, account, kind and when each was last seen. Private
state only; no real devices or playback."""

import pytest

from agent_media_server import devices, session_events, speech_frames as frames

CLAIMS = {"iss": "https://id.example", "sub": "u1", "email": "me@example.com"}
INSTALL = "i_0123456789abcdef0123"


@pytest.fixture(autouse=True)
def private_state(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setattr(session_events, "poke", lambda: None)
    devices._reset_for_tests()
    frames._reset_for_tests()
    yield
    frames._reset_for_tests()
    devices._reset_for_tests()


def _pair(name="Pixel 8a", claims=CLAIMS):
    return devices.enrol_account(claims, name)["device_id"]


def _coded(name):
    code, _ = devices.mint_code(name)
    return devices.redeem(code, name)["device_id"]


def _edit(device_id, **fields):
    with devices._locked():
        rows = devices._load(fresh=True)
        devices._save([{**d, **fields} if d["id"] == device_id else d for d in rows])


def _row(device_id):
    return next((d for d in devices.list_devices() if d["id"] == device_id), None)


def _ids():
    return {d["id"] for d in devices.list_devices()}


# --- the install id ---------------------------------------------------------

def test_the_same_install_retires_the_old_pairing_and_keeps_its_settings():
    old = _pair("TCL Smart TV")
    devices.claim_install(old, INSTALL)
    devices.set_kind(old, "tv")
    devices.set_pause(old, True)
    devices.set_scope(old, ["p1"])
    devices.set_expiry(old, 4_000_000_000)
    frames.listening(old, True)
    frames.select_output("speech", old)
    _edit(old, created=1000.0)
    new = _coded("TCL Smart TV")  # Paired by code this time: no account.
    assert devices.claim_install(new, INSTALL) == [old]
    assert _ids() == {new}
    row = _row(new)
    assert row["kind"] == "tv" and row["kind_set_by"]
    assert row["pause_here"] is True
    assert row["scope"] == {"projects": ["p1"]}
    assert row["expires_at"] == 4_000_000_000
    assert "install" not in row  # Never leaves the store.
    assert frames.output_device("speech") == new


def test_devices_following_the_old_pairing_follow_the_new_one():
    phone = _pair()
    screen = _coded("hpo screen")
    devices.claim_install(phone, INSTALL)
    devices.set_follow(screen, phone)
    _edit(phone, created=1000.0)
    again = _pair()
    devices.claim_install(again, INSTALL)
    assert _row(screen)["follow"] == again


def test_role_and_enrol_come_from_the_new_pairing_not_the_old():
    old = _coded("Tablet")
    devices.set_enrol(old, True)
    devices.claim_install(old, INSTALL)
    _edit(old, created=1000.0)
    new = _coded("Tablet")
    devices.claim_install(new, INSTALL)
    assert _row(new)["enrol"] is False


def test_a_newer_pairing_with_the_install_is_never_retired_by_an_older_one():
    old = _pair()
    _edit(old, created=1000.0)
    new = _pair()
    devices.claim_install(new, INSTALL)
    assert devices.claim_install(old, INSTALL) == []
    assert _ids() == {old, new}


def test_a_different_install_or_a_bad_one_retires_nothing():
    a = _pair()
    devices.claim_install(a, INSTALL)
    _edit(a, created=1000.0)
    b = _pair()
    assert devices.claim_install(b, "i_ffffffffffffffffffff") == []
    assert devices.claim_install(b, "short") == []
    assert devices.claim_install(b, "../" + INSTALL) == []
    assert _ids() == {a, b}


def test_a_workers_token_is_never_retired_by_an_install():
    code, _ = devices.mint_code("red5 jobs", jobs=True)
    worker = devices.redeem(code, "red5 jobs")["device_id"]
    _edit(worker, install=INSTALL, created=1000.0)
    phone = _pair()
    assert devices.claim_install(phone, INSTALL) == []
    assert worker in _ids()


# --- the guess for pairings made before ------------------------------------

def test_an_old_pairing_unseen_since_the_new_one_looks_replaced():
    old = _pair("TCL Smart TV")
    _edit(old, created=1000.0, last_seen=2000.0)
    new = _coded("TCL Smart TV")  # No account on this one: that is no reason to doubt.
    assert _row(old)["likely_replaced_by"] == new
    assert "likely_replaced_by" not in _row(new)
    assert any(old in line for line in devices.doctor_lines())


def test_names_that_differ_only_in_punctuation_are_the_same_name():
    old = _pair("p8a-firefox")
    _edit(old, created=1000.0, last_seen=2000.0)
    new = _pair("p8a (Firefox)")
    assert _row(old)["likely_replaced_by"] == new


def test_an_old_pairing_still_in_use_is_not_replaced():
    old = _pair("Android (Chrome)")
    _edit(old, created=1000.0, last_seen=90_000.0)
    new = _pair("Android (Chrome)")
    _edit(new, created=50_000.0)  # The old one was used for hours after.
    assert "likely_replaced_by" not in _row(old)


def test_two_accounts_two_kinds_or_two_installs_are_two_devices():
    for other in ({"account": "someone@example.com"}, {"kind": "tablet"},
                  {"install": "i_ffffffffffffffffffff"}):
        devices._reset_for_tests()
        old = _pair("Tablet")
        _edit(old, created=1000.0, last_seen=2000.0, kind="phone", install=INSTALL)
        new = _pair("Tablet")
        _edit(new, **{"kind": "phone", "install": INSTALL, **other})
        assert "likely_replaced_by" not in _row(old), other


def test_forget_replaced_forgets_only_the_guessed(capsys):
    old = _pair("TCL Smart TV")
    _edit(old, created=1000.0, last_seen=2000.0)
    new = _pair("TCL Smart TV")
    keep = _pair("Pixel 8a")
    assert devices.cli_devices(["--forget-replaced"]) == 0
    assert _ids() == {new, keep}
    assert f"revoked {old}" in capsys.readouterr().out
