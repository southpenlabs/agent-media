"""The agent gateway on the Coding agents page: `GET /gateway` (§6.6a).

A model gateway is one OpenAI-compatible endpoint in front of every plan and
key a host has: LiteLLM (github.com/BerriAI/litellm), with CLIProxyAPI
(github.com/router-for-me/CLIProxyAPI) behind it holding OAuth sign-ins
(Antigravity, Claude, Codex, …) as API routes. Antigravity's usage on the
model sheets comes from the proxy's Google login, and nothing on screen said
so (sasonica-app mail #1305, David, 25 Nov 2026: "Ask for both").

What this answers, all read from files and the two services' own health
routes, nothing that needs a secret:

- the gateway: whether its unit is running and its health route answers,
  and where (`url`), from LiteLLM's unit (`--config`, `--host`, `--port`) or
  MEDIA_GATEWAY_CONFIG / MEDIA_GATEWAY_URL;
- its routes: LiteLLM's `model_list`, a row per provider (by its prefix and
  `api_base`), with how many models and the name of the key each uses and
  whether that key is set (in the unit's EnvironmentFile or our env). Never a
  key's value;
- its sign-ins: each file in CLIProxyAPI's `auth-dir` (provider, account,
  state, when its access token lapses), and LiteLLM's own ChatGPT login
  (`CHATGPT_TOKEN_DIR`) when a route uses it;
- what can be signed in: the proxy's `-<provider>-login` flags its `-h`
  lists. `POST /gateway/login` runs one with `-no-browser` in a setup window
  (harnesses._window), read and typed into through /harnesses/screen, /keys
  and /close like an agent's sign-in: the page shows a URL, and the address
  the browser lands on afterwards is pasted back. `POST /gateway/logout`
  deletes the sign-in's file; the proxy watches the directory.

The management API CLIProxyAPI offers would need its secret key; files do
not, and are what it reads itself.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shlex
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, Mapping, Optional

from . import auth

GATEWAY_UNIT = "litellm.service"
PROXY_UNIT = "cli-proxy-api.service"

#: A provider's name, by the host its `api_base` points at.
HOSTS = {
    "api.venice.ai": "Venice", "openrouter.ai": "OpenRouter", "api.openai.com": "OpenAI",
    "api.anthropic.com": "Anthropic", "generativelanguage.googleapis.com": "Google AI",
    "api.x.ai": "xAI", "api.moonshot.ai": "Moonshot", "api.deepseek.com": "DeepSeek",
    "api.groq.com": "Groq", "api.mistral.ai": "Mistral", "opencode.ai": "OpenCode",
}
#: A provider's name, by LiteLLM's prefix, where no `api_base` says more.
PREFIXES = {
    "chatgpt": "ChatGPT plan", "ollama": "Ollama", "ollama_chat": "Ollama",
    "auto_router": "Auto router", "anthropic": "Anthropic", "gemini": "Google AI",
    "openrouter": "OpenRouter", "xai": "xAI", "deepseek": "DeepSeek", "groq": "Groq",
    "mistral": "Mistral", "vertex_ai": "Vertex AI", "bedrock": "Bedrock",
}
FAMILIES = {"claude": "Claude", "gpt": "GPT", "gemini": "Gemini", "kimi": "Kimi", "grok": "Grok",
            "qwen": "Qwen", "deepseek": "DeepSeek", "llama": "Llama", "glm": "GLM"}
#: CLIProxyAPI's sign-ins: its `type` → (name, the login flag). Codex signs
#: in by device code, the rest by an OAuth page whose address is pasted back.
PROXY_LOGINS = {
    "antigravity": ("Antigravity", "-antigravity-login"),
    "gemini": ("Gemini", "-login"),
    "claude": ("Claude", "-claude-login"),
    "codex": ("ChatGPT (Codex)", "-codex-device-login"),
    "kimi": ("Kimi", "-kimi-login"),
    "xai": ("xAI", "-xai-login"),
    "qwen": ("Qwen", "-qwen-login"),
    "iflow": ("iFlow", "-iflow-login"),
}
#: A sign-in whose plan /usage?plan= reads (plan_usage.PLANS).
PLANS = {"antigravity": "antigravity"}

ID = re.compile(r"[A-Za-z0-9@._+-]{1,200}")
PROBE_S = 2.0

Run = Callable[[list[str]], str]


def _run(argv: list[str]) -> str:
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def unit(name: str, run: Run = _run) -> dict:
    """A user unit's `argv`, `env_files` and whether it is `active`."""
    out = run(["systemctl", "--user", "show", name, "-p", "ExecStart",
               "-p", "EnvironmentFiles", "-p", "ActiveState"])
    got: dict = {"argv": [], "env_files": [], "active": False}
    for line in out.splitlines():
        key, _, val = line.partition("=")
        if key == "ExecStart":
            m = re.search(r"argv\[\]=(.*?) ;", val)
            if m:
                try:
                    got["argv"] = shlex.split(m.group(1))
                except ValueError:
                    pass
        elif key == "EnvironmentFiles" and val:
            got["env_files"] = [v.split(" (")[0] for v in val.split("\n") if v.strip()]
        elif key == "ActiveState":
            got["active"] = val.strip() == "active"
    return got


def _flag(argv: list[str], *names: str) -> str:
    for i, a in enumerate(argv):
        for n in names:
            if a == n and i + 1 < len(argv):
                return argv[i + 1]
            if a.startswith(n + "="):
                return a.split("=", 1)[1]
    return ""


def _yaml(path: str) -> dict:
    import yaml

    try:
        data = yaml.safe_load(Path(path).expanduser().read_text())
    except (OSError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def env_names(files: list[str]) -> set[str]:
    """The names set in env files; their values are never kept."""
    names = set()
    for f in files:
        try:
            text = Path(f).expanduser().read_text()
        except OSError:
            continue
        for line in text.splitlines():
            m = re.match(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.+)", line)
            if m and m.group(2).strip().strip("'\""):
                names.add(m.group(1))
    return names


def _env_value(files: list[str], name: str) -> str:
    """One named, non-secret value from env files (a directory)."""
    for f in files:
        try:
            text = Path(f).expanduser().read_text()
        except OSError:
            continue
        for line in text.splitlines():
            m = re.match(rf"\s*(?:export\s+)?{name}=(.*)", line)
            if m:
                return m.group(1).strip().strip("'\"")
    return ""


def _alive(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=PROBE_S) as r:
            return 200 <= r.status < 300
    except Exception:
        return False


def _host(url: str) -> str:
    p = urllib.parse.urlparse(url or "")
    return f"{p.hostname}:{p.port}" if p.port else (p.hostname or "")


def _epoch(v: object) -> Optional[int]:
    if isinstance(v, (int, float)):
        return int(v / 1000) if v > 1e12 else int(v)
    if isinstance(v, str) and v:
        from datetime import datetime

        try:
            return int(datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp())
        except ValueError:
            return None
    return None


# --- the proxy ---------------------------------------------------------------------

def proxy(run: Run = _run, env: Mapping[str, str] = os.environ) -> dict:
    """CLIProxyAPI: its binary, config, auth dir and address, or `{}`."""
    u = unit(env.get("MEDIA_GATEWAY_PROXY_UNIT") or PROXY_UNIT, run)
    exe = env.get("MEDIA_GATEWAY_PROXY") or (u["argv"][0] if u["argv"] else "")
    cfg = env.get("MEDIA_GATEWAY_PROXY_CONFIG") or _flag(u["argv"], "-config", "--config")
    if not exe and not cfg:
        return {}
    conf = _yaml(cfg) if cfg else {}
    auth_dir = str(Path(str(conf.get("auth-dir") or "~/.cli-proxy-api")).expanduser())
    host = str(conf.get("host") or "127.0.0.1") or "127.0.0.1"
    port = int(conf.get("port") or 8317)
    return {"exe": exe, "config": cfg, "auth_dir": auth_dir, "active": u["active"],
            "url": f"http://{'127.0.0.1' if host in ('0.0.0.0', '') else host}:{port}",
            "port": port}


_help: dict[str, tuple[float, str]] = {}


def login_flags(exe: str, run: Run = _run) -> dict[str, str]:
    """`{provider: flag}` for the logins this build of the proxy has."""
    if not exe:
        return {}
    try:
        mtime = Path(exe).stat().st_mtime
    except OSError:
        return {}
    at, text = _help.get(exe, (0.0, ""))
    if at != mtime:
        try:
            done = subprocess.run([exe, "-h"], capture_output=True, text=True, timeout=10)
            text = (done.stdout or "") + (done.stderr or "")
        except (OSError, subprocess.TimeoutExpired):
            text = ""
        _help[exe] = (mtime, text)
    have = set(re.findall(r"^\s+(-[a-z-]+)", text, re.M))
    return {p: flag for p, (_, flag) in PROXY_LOGINS.items() if flag in have}


def proxy_signins(auth_dir: str, now: float) -> list[dict]:
    out = []
    for f in sorted(Path(auth_dir).glob("*.json")):
        try:
            d = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(d, dict):
            continue
        kind = str(d.get("type") or f.stem.split("-")[0])
        expires = _epoch(d.get("expired"))
        if d.get("disabled"):
            state = "off"
        elif d.get("refresh_token") or (expires and expires > now):
            state = "in"
        else:
            state = "expired"
        out.append({"id": f.stem, "provider": kind,
                    "label": PROXY_LOGINS.get(kind, (kind.title(), ""))[0],
                    "account": str(d.get("email") or d.get("account") or ""),
                    "state": state, "expires_at": expires, "via": "proxy",
                    "plan": PLANS.get(kind)})
    return out


# --- the gateway -------------------------------------------------------------------

def _jwt_email(token: str) -> str:
    try:
        part = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except (IndexError, ValueError):
        return ""
    return str(claims.get("email") or (claims.get("https://api.openai.com/profile") or {}).get("email") or "")


def chatgpt_signin(env_files: list[str], env: Mapping[str, str]) -> Optional[dict]:
    """LiteLLM's own ChatGPT login (litellm/llms/chatgpt/authenticator.py)."""
    d = (env.get("CHATGPT_TOKEN_DIR") or _env_value(env_files, "CHATGPT_TOKEN_DIR")
         or "~/.config/litellm/chatgpt")
    name = env.get("CHATGPT_AUTH_FILE") or _env_value(env_files, "CHATGPT_AUTH_FILE") or "auth.json"
    try:
        data = json.loads((Path(d).expanduser() / name).read_text())
    except (OSError, ValueError):
        return {"id": "litellm-chatgpt", "provider": "chatgpt", "label": "ChatGPT plan",
                "account": "", "state": "out", "expires_at": None, "via": "gateway", "plan": None}
    return {"id": "litellm-chatgpt", "provider": "chatgpt", "label": "ChatGPT plan",
            "account": _jwt_email(str(data.get("id_token") or "")),
            "state": "in" if data.get("refresh_token") else "expired",
            "expires_at": _epoch(data.get("expires_at")), "via": "gateway", "plan": None}


def routes(conf: dict, keys_set: set[str], proxy_port: Optional[int]) -> list[dict]:
    """LiteLLM's `model_list`, a row per provider."""
    rows: dict[tuple, dict] = {}
    for m in conf.get("model_list") or []:
        if not isinstance(m, dict):
            continue
        lp = m.get("litellm_params")
        p: dict = lp if isinstance(lp, dict) else {}
        prefix = str(p.get("custom_llm_provider") or str(p.get("model") or "").split("/")[0])
        base = str(p.get("api_base") or "")
        host = _host(base)
        hostname = urllib.parse.urlparse(base).hostname or ""
        local = hostname in ("127.0.0.1", "localhost", "::1")
        if base and local and proxy_port and urllib.parse.urlparse(base).port == proxy_port:
            rid, label = "proxy", "CLIProxyAPI"
        elif prefix in ("ollama", "ollama_chat"):
            rid, label = "ollama", "Ollama"
        elif hostname in HOSTS:
            rid, label = hostname, HOSTS[hostname]
        elif base:
            rid, label = host, host
        else:
            rid, label = prefix, PREFIXES.get(prefix, prefix)
        key_ref = str(p.get("api_key") or "")
        if key_ref.startswith("os.environ/"):
            kname = key_ref.split("/", 1)[1]
            key = {"name": kname, "set": kname in keys_set}
        elif key_ref:
            key = {"name": None, "set": True}      # written in the config; never shown
        else:
            key = None
        row = rows.setdefault((rid, host), {"id": rid, "label": label, "via": host,
                                            "models": 0, "names": [], "key": key})
        row["models"] += 1
        name = str(m.get("model_name") or "")
        if name and name not in row["names"]:
            row["names"].append(name)
        if key and (row["key"] is None or (row["key"]["set"] and not key["set"])):
            row["key"] = key               # the worst of its keys speaks for the row
    out = list(rows.values())
    for r in out:
        # A route named only by its address says whose models it carries
        # when they share a family ("Claude via red2:3456", a local bridge).
        fams = {n.split("-")[0].lower() for n in r["names"]}
        if r["label"] == r["via"] and len(fams) == 1 and r["names"]:
            fam = fams.pop()
            r["label"] = f"{FAMILIES.get(fam, fam.title())} via {r['via']}"
        r["signins"] = ("proxy" if r["id"] == "proxy" else
                        "litellm-chatgpt" if r["id"] == "chatgpt" else None)
    return sorted(out, key=lambda r: -r["models"])


def status(run: Run = _run, env: Mapping[str, str] = os.environ, now: Callable[[], float] = time.time,
           alive: Callable[[str], bool] = _alive) -> dict:
    """`GET /gateway`'s answer, ungated."""
    u = unit(env.get("MEDIA_GATEWAY_UNIT") or GATEWAY_UNIT, run)
    cfg = env.get("MEDIA_GATEWAY_CONFIG") or _flag(u["argv"], "--config", "-c")
    url = env.get("MEDIA_GATEWAY_URL") or ""
    if not url and u["argv"]:
        host = _flag(u["argv"], "--host") or "127.0.0.1"
        url = f"http://{'127.0.0.1' if host == '0.0.0.0' else host}:{_flag(u['argv'], '--port') or '4000'}"
    px = proxy(run, env)
    if not cfg and not url and not px:
        return {"present": False, "up": False, "url": "", "unit": "", "routes": [],
                "signins": [], "can_add": [], "proxy": None}
    conf = _yaml(cfg) if cfg else {}
    keys_set = env_names(u["env_files"]) | {k for k, v in env.items() if v}
    rts = routes(conf, keys_set, px.get("port"))
    signins: list[dict] = []
    flags: dict[str, str] = {}
    if px:
        flags = login_flags(px["exe"], run)
        for s in proxy_signins(px["auth_dir"], now()):
            s["actions"] = (["login"] if s["provider"] in flags else []) + ["logout"]
            signins.append(s)
    if any(r["id"] == "chatgpt" for r in rts):
        c = chatgpt_signin(u["env_files"], env)
        if c:
            c["actions"] = []      # LiteLLM renews it itself; signing in again is at the desk
            signins.append(c)
    held = {s["provider"] for s in signins if s["via"] == "proxy"}
    return {
        "present": True,
        "up": bool(url) and alive(url.rstrip("/") + "/health/liveliness"),
        "active": u["active"], "unit": (env.get("MEDIA_GATEWAY_UNIT") or GATEWAY_UNIT) if u["argv"] else "",
        "url": url, "host": _host(url),
        "models": sum(r["models"] for r in rts),
        "routes": rts,
        "signins": signins,
        "can_add": [{"provider": p, "label": PROXY_LOGINS[p][0], "held": p in held} for p in flags],
        "proxy": ({"up": alive(px["url"] + "/"), "active": px["active"], "url": px["url"],
                   "unit": env.get("MEDIA_GATEWAY_PROXY_UNIT") or PROXY_UNIT}
                  if px else None),
    }


# --- the routes --------------------------------------------------------------------

def get(bearer: str, **kw) -> tuple[bool, dict]:
    ok, detail = auth.may_control_speech(bearer)
    if not ok:
        return False, detail
    return True, status(**kw)


def login(provider: str, bearer: str, run: Run = _run, env: Mapping[str, str] = os.environ) -> tuple[bool, dict]:
    """`POST /gateway/login {provider}`: the proxy's sign-in in a setup window."""
    ok, detail = auth.may_control_speech(bearer)
    if not ok:
        return False, detail
    from . import harnesses

    px = proxy(run, env)
    if not px or not px.get("exe"):
        return False, {"error": "no gateway proxy on this host", "status": 409}
    flags = login_flags(px["exe"], run)
    provider = (provider or "").strip()
    if provider not in flags:
        return False, {"error": f"the gateway proxy has no sign-in for {provider!r}", "status": 400}
    argv = [px["exe"]] + (["-config", px["config"]] if px.get("config") else []) + [flags[provider], "-no-browser"]
    pane, err = harnesses._window(argv, f"gateway-{provider}-login")
    if err:
        return False, {"error": err, "status": 503}
    cmd = shlex.join(argv)
    harnesses._remember(pane, f"gateway-{provider}", "login", cmd)
    return True, {"pane": pane, "provider": provider, "action": "login", "cmd": cmd}


def logout(sid: str, bearer: str, run: Run = _run, env: Mapping[str, str] = os.environ) -> tuple[bool, dict]:
    """`POST /gateway/logout {id}`: delete that sign-in's file from the proxy."""
    ok, detail = auth.may_control_speech(bearer)
    if not ok:
        return False, detail
    sid = (sid or "").strip()
    if sid == "litellm-chatgpt":
        return False, {"error": "the gateway's ChatGPT login is not signed out from here", "status": 409}
    px = proxy(run, env)
    if not px or not ID.fullmatch(sid):
        return False, {"error": f"no gateway sign-in {sid!r}", "status": 404}
    f = Path(px["auth_dir"]) / f"{sid}.json"
    if f.parent.resolve() != Path(px["auth_dir"]).resolve() or not f.is_file():
        return False, {"error": f"no gateway sign-in {sid!r}", "status": 404}
    try:
        f.unlink()
    except OSError as e:
        return False, {"error": f"could not remove it: {e}", "status": 500}
    return True, {"id": sid, "removed": True}
