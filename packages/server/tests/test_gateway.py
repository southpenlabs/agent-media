"""GET /gateway, /gateway/login, /gateway/logout (gateway.py, contract §6.6a).

Every test builds its own units, configs and auth dir; nothing reads the
host's systemd, the real proxy or its sign-ins.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from agent_media_server import gateway

GATEWAY_YAML = """
model_list:
  - model_name: claude-opus
    litellm_params: {model: openai/opus, api_base: "http://red9:3456/v1", api_key: placeholder}
  - model_name: claude-sonnet
    litellm_params: {model: openai/sonnet, api_base: "http://red9:3456/v1", api_key: placeholder}
  - model_name: gemini-3-pro
    litellm_params: {model: openai/gemini-3-pro, api_base: "http://127.0.0.1:8317/v1", api_key: os.environ/CLIPROXY_API_KEY}
  - model_name: kimi-k3
    litellm_params: {model: openai/kimi-k3, api_base: "https://api.venice.ai/api/v1", api_key: os.environ/VENICE_API_KEY}
  - model_name: gpt-5.6-sol
    litellm_params: {model: chatgpt/gpt-5.6-sol}
general_settings: {master_key: sk-never-read}
"""


@pytest.fixture
def host(tmp_path):
    cfg = tmp_path / "litellm.yaml"
    cfg.write_text(GATEWAY_YAML)
    envf = tmp_path / "litellm.env"
    envf.write_text("CLIPROXY_API_KEY=abc\nVENICE_API_KEY=\nCHATGPT_TOKEN_DIR=%s\n" % (tmp_path / "chatgpt"))
    auth = tmp_path / "auth"
    auth.mkdir()
    (auth / "antigravity-a@example.com.json").write_text(json.dumps({
        "type": "antigravity", "email": "a@example.com", "access_token": "x", "refresh_token": "r",
        "expired": "2026-11-25T10:00:00+11:00", "disabled": False}))
    (auth / "claude-b@example.com.json").write_text(json.dumps({
        "type": "claude", "email": "b@example.com", "disabled": True}))
    (tmp_path / "chatgpt").mkdir()
    (tmp_path / "chatgpt/auth.json").write_text(json.dumps({"refresh_token": "r", "expires_at": 1795847185}))
    exe = tmp_path / "cli-proxy-api"
    exe.write_text("#!/bin/sh\necho 'Usage:'\necho '  -antigravity-login'\necho '  -claude-login'\n"
                   "echo '  -codex-device-login'\necho '  -no-browser'\n")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    pcfg = tmp_path / "proxy.yaml"
    pcfg.write_text(f'host: "127.0.0.1"\nport: 8317\nauth-dir: "{auth}"\n')
    shows = {
        "litellm.service": (f"ExecStart={{ path=/x ; argv[]=/x/litellm --config {cfg} --host 10.0.0.2 --port 4000 ; ignore_errors=no }}\n"
                            f"EnvironmentFiles={envf} (ignore_errors=no)\nActiveState=active\n"),
        "cli-proxy-api.service": f"ExecStart={{ path={exe} ; argv[]={exe} -config {pcfg} ; }}\nActiveState=active\n",
    }

    def run(argv):
        return shows.get(argv[3], "") if argv[:2] == ["systemctl", "--user"] else ""

    gateway._help.clear()
    return {"run": run, "env": {}, "auth": auth, "tmp": tmp_path, "exe": exe}


def test_status_reads_routes_signins_and_logins(host):
    seen = []
    s = gateway.status(run=host["run"], env=host["env"], now=lambda: 1795560000,
                       alive=lambda url: seen.append(url) or True)
    assert s["present"] and s["up"] and s["active"]
    assert s["url"] == "http://10.0.0.2:4000"
    assert "http://10.0.0.2:4000/health/liveliness" in seen
    routes = {r["id"]: r for r in s["routes"]}
    assert routes["red9:3456"]["label"] == "Claude via red9:3456"
    assert routes["red9:3456"]["key"] == {"name": None, "set": True}
    assert routes["proxy"]["key"] == {"name": "CLIPROXY_API_KEY", "set": True}
    assert routes["proxy"]["signins"] == "proxy"
    assert routes["api.venice.ai"]["key"] == {"name": "VENICE_API_KEY", "set": False}
    assert routes["chatgpt"]["signins"] == "litellm-chatgpt"
    assert "sk-never-read" not in json.dumps(s) and "abc" not in json.dumps(s)
    sig = {x["id"]: x for x in s["signins"]}
    ag = sig["antigravity-a@example.com"]
    assert (ag["state"], ag["account"], ag["plan"], ag["actions"]) == ("in", "a@example.com", "antigravity",
                                                                      ["login", "logout"])
    assert sig["claude-b@example.com"]["state"] == "off"
    assert sig["litellm-chatgpt"]["state"] == "in" and sig["litellm-chatgpt"]["actions"] == []
    assert [(a["provider"], a["held"]) for a in s["can_add"]] == [
        ("antigravity", True), ("claude", True), ("codex", False)]
    assert s["proxy"]["url"] == "http://127.0.0.1:8317"


def test_no_gateway_is_not_present():
    s = gateway.status(run=lambda argv: "", env={}, alive=lambda url: True)
    assert s == {"present": False, "up": False, "url": "", "unit": "", "routes": [],
                 "signins": [], "can_add": [], "proxy": None}


def test_login_opens_a_setup_window(host, monkeypatch):
    from agent_media_server import harnesses

    monkeypatch.setattr(gateway.auth, "may_control_speech", lambda b: (True, {}))
    opened, kept = [], []
    monkeypatch.setattr(harnesses, "_window", lambda argv, name: opened.append((argv, name)) or ("%7", ""))
    monkeypatch.setattr(harnesses, "_remember", lambda *a: kept.append(a))
    ok, d = gateway.login("antigravity", "tok", run=host["run"], env=host["env"])
    assert ok and d["pane"] == "%7"
    argv, name = opened[0]
    assert argv[0] == str(host["exe"]) and argv[-2:] == ["-antigravity-login", "-no-browser"]
    assert name == "gateway-antigravity-login"
    assert kept[0][1:3] == ("gateway-antigravity", "login")
    ok, d = gateway.login("kimi", "tok", run=host["run"], env=host["env"])
    assert not ok and d["status"] == 400


def test_logout_deletes_only_a_listed_signin(host, monkeypatch):
    monkeypatch.setattr(gateway.auth, "may_control_speech", lambda b: (True, {}))
    for bad in ("../auth/antigravity-a@example.com", "nope", "litellm-chatgpt"):
        ok, d = gateway.logout(bad, "tok", run=host["run"], env=host["env"])
        assert not ok and d["status"] in (404, 409)
    ok, d = gateway.logout("antigravity-a@example.com", "tok", run=host["run"], env=host["env"])
    assert ok and not (host["auth"] / "antigravity-a@example.com.json").exists()
    assert (host["auth"] / "claude-b@example.com.json").exists()


def test_routes_are_gated(monkeypatch):
    monkeypatch.setattr(gateway.auth, "may_control_speech", lambda b: (False, {"error": "no", "status": 403}))
    assert gateway.get("x") == (False, {"error": "no", "status": 403})
    assert gateway.login("antigravity", "x")[0] is False
    assert gateway.logout("a", "x")[0] is False


def test_unit_parses_systemctl_show():
    out = ("ExecStart={ path=/bin/l ; argv[]=/bin/l --config '/a b/c.yaml' --port 4000 ; ignore_errors=no ; pid=1 }\n"
           "EnvironmentFiles=/e/one.env (ignore_errors=no)\nActiveState=inactive\n")
    u = gateway.unit("x", run=lambda argv: out)
    assert u == {"argv": ["/bin/l", "--config", "/a b/c.yaml", "--port", "4000"],
                 "env_files": ["/e/one.env"], "active": False}
    assert Path(u["env_files"][0]).name == "one.env"
