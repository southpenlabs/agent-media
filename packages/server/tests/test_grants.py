"""Who may come into this server (grants.py) and what an account's tier
allows (services._allowed): account access and tiers, 18 Nov 2026."""

from __future__ import annotations

import pytest

from agent_media_server import auth, devices, grants, services, servers_report


def test_a_granted_email_signs_in_with_its_role():
    assert grants.put("Ana@Example.com", role="guest")
    assert grants.role_for({"email": "ana@example.com"}) == "guest"
    assert grants.role_for({"email": "ana@example.com", "email_verified": False}) == ""
    assert grants.role_for({"email": "bob@example.com"}) == ""
    assert not grants.put("not an email", role="member")


def test_newest_change_wins_and_a_removal_stays_removed():
    grants.put("ana@example.com", role="member", changed_at=100)
    grants.put("ana@example.com", remove=True, changed_at=200)
    # An older add from the website doesn't undo the removal made here.
    assert grants.merge_from_web([{"email": "ana@example.com", "role": "admin",
                                   "changed_at": 150}]) == 0
    assert grants.role_for({"email": "ana@example.com"}) == ""
    assert grants.merge_from_web([{"email": "ana@example.com", "role": "admin",
                                   "changed_at": 300}, {"email": "x"}, "junk"]) == 1
    assert grants.role_for({"email": "ana@example.com"}) == "admin"
    rep = {r["email"]: r for r in grants.for_report()}
    assert rep["ana@example.com"]["by"] == "web" and not rep["ana@example.com"].get("removed")


def test_services_kept_cleared_and_left_alone():
    grants.put("ana@example.com", role="member", services=["music"])
    grants.put("ana@example.com", role="guest")                  # services left as they were
    assert grants.services_for("ana@example.com") == ["music"]
    grants.put("ana@example.com", services=None)                 # cleared
    assert grants.services_for("ana@example.com") is None


@pytest.fixture
def users(monkeypatch):
    who = {"admin": {"username": "d"}, "member": {"username": "m", "device": "x", "role": "member"}}
    monkeypatch.setattr(auth, "gate", lambda b: (who[b], {}) if b in who
                        else (None, {"status": 401, "error": "no"}))


def test_settings_people_is_an_admins(users):
    ok, d = grants.settings("admin", {"email": "ana@example.com", "role": "guest"})
    assert ok and d["people"][0]["email"] == "ana@example.com"
    assert grants.settings("member")[1]["code"] == "not_admin"
    assert grants.settings("admin", {"email": "nope"})[1]["code"] == "email"
    assert grants.settings("admin", {"email": "a@b.co", "role": "boss"})[1]["code"] == "role"
    assert grants.settings("admin", {"email": "a@b.co", "services": "music"})[1]["code"] == "services"
    ok, d = grants.settings("admin", {"email": "ana@example.com", "remove": True})
    assert ok and d["people"] == []


def _account_device(monkeypatch, email="ana@example.com"):
    row = {"id": "d_a", "iss": "https://cms.example", "sub": "s1", "account": email}
    monkeypatch.setattr(devices, "list_devices", lambda: [row])
    monkeypatch.setattr(devices, "may_enrol", lambda d: False)
    key = auth.account_key_of("https://cms.example", "s1")
    return row, {"device": "d_a", "account_key": key}


def test_a_members_tier_from_the_report_limits_services(monkeypatch):
    row, user = _account_device(monkeypatch)
    assert services._allowed("maps", user)                       # nothing said: allowed
    servers_report._keep_tier(row, {"tier": "free", "services_allowed": ["music"]})
    assert servers_report.tier_of(user["account_key"])["tier"] == "free"
    assert services._allowed("music", user) and not services._allowed("maps", user)
    assert services._allowed("maps", {"device": "d_o", "account_key": ""})   # the owner


def test_on_a_resellers_server_the_owners_setting_is_the_tier(monkeypatch):
    row, user = _account_device(monkeypatch)
    monkeypatch.setenv("MEDIA_TIERS_FROM", "owner")
    servers_report._keep_tier(row, {"tier": "free", "services_allowed": ["music"]})
    assert services._allowed("maps", user)                       # owner set nothing yet
    grants.put("ana@example.com", role="member", services=["maps"])
    assert services._allowed("maps", user) and not services._allowed("music", user)


def test_an_admins_report_carries_the_people_and_takes_the_websites(monkeypatch):
    monkeypatch.setattr(servers_report, "account_role", lambda d: "admin")
    monkeypatch.setattr(servers_report, "account_devices", lambda d: [])
    monkeypatch.setattr(devices, "list_devices", lambda: [])
    grants.put("ana@example.com", role="member", changed_at=100)
    body = servers_report.describe("d_admin")
    assert [p["email"] for p in body["people"]] == ["ana@example.com"]
    servers_report.keep_ceiling("d_admin", {"people": [
        {"email": "bob@example.com", "role": "guest", "changed_at": 200}]})
    assert grants.role_for({"email": "bob@example.com"}) == "guest"
    monkeypatch.setattr(servers_report, "account_role", lambda d: "member")
    assert "people" not in servers_report.describe("d_member")
    servers_report.keep_ceiling("d_member", {"people": [
        {"email": "eve@example.com", "role": "admin", "changed_at": 300}]})
    assert grants.role_for({"email": "eve@example.com"}) == ""


def test_an_add_and_a_remove_in_the_same_millisecond_keep_their_order(monkeypatch):
    # The stored time is rounded to the millisecond; the next change's time
    # is rounded before it is compared (CI 18 Nov 2026: a remove 0.4 ms after
    # an add was taken as older and ignored).
    times = iter([1000.0006, 1000.0008])
    monkeypatch.setattr(grants.time, "time", lambda: next(times))
    grants.put("ana@example.com", role="member")
    grants.put("ana@example.com", remove=True)
    assert grants.people() == []


def test_settings_people_reports_soon_at_most_every_few_minutes(monkeypatch):
    from agent_media_server import auth, servers_report

    monkeypatch.setattr(auth, "may_admin", lambda b: ({"device": "d_a"}, {}))
    sent = []
    monkeypatch.setattr(servers_report, "report_soon", lambda d, base="": sent.append(d))
    grants._REPORTED.clear()
    grants.settings("tok")
    grants.settings("tok", {"email": "ana@example.com", "role": "guest"})
    assert sent == ["d_a"]                         # once in REPORT_EVERY_S
    monkeypatch.setattr(grants, "REPORT_EVERY_S", 0.0)
    grants.settings("tok")
    assert sent == ["d_a", "d_a"]
