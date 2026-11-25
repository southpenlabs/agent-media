"""Private homes: a harness profile of its own for one private chat.

A private persona (agent-media personas.py, `--mode private`) may only go to a
trusted model, must reach the harness as a system prompt rather than as a
message the phone shows, and must leave nothing behind once the thread is
sealed (seal.py). Claude and pi do that from the user's own directories: a
flag takes the render, and the transcript is one file. Codex does not: it
copies every turn into shared databases (`thread_history_1`, `state_5`) and
writes summaries of chats into its memories (`memories_1`), so one thread
cannot be sealed out of them (25 Nov 2026, David: "why not all agents?").

So a private chat on Codex runs in a **home of its own**: a harness profile
(harness_profiles.py) made for it under the profiles root, named `p-<hex>`,
with `CODEX_HOME` pointing there. The user's sign-in, hooks, skills and
plugins are linked in (never copied); `config.toml` is the user's, then
`developer_instructions` (the render), and for a `venice/` model the Venice
provider with its key (`experimental_bearer_token`, so the key is never on a
command line). Everything the chat leaves is under that one folder, which
seal.py takes whole. Homes are profiles so the readers that already look in
every profile's directory (harnesses._transcript, profile_of) find the chat
and a resume runs in the same home; `listed()` keeps them out of Settings'
login list. A Codex chat on Venice runs in one even with no persona: that is
how Codex reaches Venice at all.

An opencode chat's private home is its own data folder (XDG_DATA_HOME at
the home, so `<home>/opencode/opencode.db` is the chat's alone) and its own
`opencode serve` (opencode_api.HomeService, address in `<home>/server.json`),
which sessiond holds the chat on and the readers ask. opencode's state folder
is not moved: that is also where agent-media's speech plugin keeps the
speech history, and moving it moved that too (tried 25 Nov 2026). The render
goes as `system` with every prompt, as for any headless opencode chat. The
chat's id is written in the home (`.session`) so a reader knows which server
to ask. Only headless: a pane would use the user's background server.

Standard library only, as harness_profiles.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import urllib.parse
from pathlib import Path

from . import harness_profiles

#: The harnesses whose private chats run in a home of their own.
HARNESSES = ("codex", "opencode")

#: The file that says a profile's directory is a private home.
MARK = ".private-home"

PREFIX = "p-"


def is_home(p: "harness_profiles.Profile | None") -> bool:
    return bool(p) and p.name.startswith(PREFIX) and (Path(p.dir) / MARK).is_file()


def home_of(harness: str, name: str) -> Path | None:
    p = harness_profiles.get(harness, name) if name else None
    return Path(p.dir) if is_home(p) else None


def listed(harness: str) -> list["harness_profiles.Profile"]:
    """The profiles a person made: every one but the private homes."""
    return [p for p in harness_profiles.of(harness) if not is_home(p)]


def _codex_default() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


def _opencode_data_default() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "opencode"


def _link(src: Path, dest: Path) -> None:
    if src.exists() and not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.symlink_to(src)


def _toml_string(text: str) -> str:
    # A JSON string is a TOML basic string: the same escapes, and every
    # control character escaped.
    return json.dumps(text, ensure_ascii=False)


def make(harness: str, render: str, *, model: str = "", venice_key: str = "",
         venice_context: int = 0) -> "harness_profiles.Profile":
    """A new home for one private chat on `harness`, with `render` as its
    standing instructions. `model` a `venice/<id>` for Codex sets the Venice
    provider (with `venice_key`). Raises harness_profiles.ProfileError."""
    if harness not in HARNESSES:
        raise harness_profiles.ProfileError(f"{harness} has no private home")
    name = PREFIX + secrets.token_hex(6)
    p = harness_profiles.create(harness, name)
    d = Path(p.dir)
    try:
        (d / MARK).write_text(json.dumps({"harness": harness}) + "\n")
        if harness == "codex":
            _make_codex(d, render, model, venice_key, venice_context)
        else:
            relink("opencode", d)
    except BaseException:
        harness_profiles.remove(harness, name, delete=True)
        raise
    return p


def _make_codex(d: Path, render: str, model: str, venice_key: str, context: int) -> None:
    # The sign-in, and what makes a chat speak and act as the user's do (the
    # hooks agent-media installed, skills, plugins); never the chats.
    relink("codex", d)
    src = _codex_default()
    try:
        base = (src / "config.toml").read_text()
    except OSError:
        base = ""
    top, tables = _split_toml(base)
    top = [ln for ln in top if not ln.split("=", 1)[0].strip() in
           ("developer_instructions", *(("model", "model_provider", "model_context_window")
                                        if model.startswith("venice/") else ()))]
    lines = [*top, *([f"developer_instructions = {_toml_string(render.strip())}"]
                     if render.strip() else [])]
    if model.startswith("venice/"):
        lines += [f"model = {_toml_string(model.split('/', 1)[1])}", 'model_provider = "venice"']
        if context:
            lines.append(f"model_context_window = {int(context)}")
    out = "\n".join(lines).rstrip() + "\n"
    if tables.strip():
        out += "\n" + tables.strip() + "\n"
    if model.startswith("venice/") and "[model_providers.venice]" not in tables:
        out += ("\n[model_providers.venice]\nname = \"Venice\"\n"
                "base_url = \"https://api.venice.ai/api/v1\"\nwire_api = \"responses\"\n"
                f"experimental_bearer_token = {_toml_string(venice_key)}\n")
    cfg = d / "config.toml"
    cfg.write_text(out)
    os.chmod(cfg, 0o600)


def _split_toml(text: str) -> tuple[list[str], str]:
    """The top-level lines of a TOML file, and the rest (from its first table)."""
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        if ln.lstrip().startswith("["):
            return lines[:i], "\n".join(lines[i:])
    return lines, ""


#: Codex needs room for its own prompt and tools (a 128k model ran out on
#: the first turn, 25 Nov 2026).
VENICE_MIN_CONTEXT = 200_000


def _venice_catalogue() -> dict:
    """Venice's models as opencode last fetched them (models.dev, in its
    cache): no request of our own."""
    base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    try:
        data = json.loads((base / "opencode" / "models.json").read_text())
    except (OSError, ValueError):
        return {}
    models = (data.get("venice") or {}).get("models") if isinstance(data, dict) else None
    return models if isinstance(models, dict) else {}


def venice_ready() -> bool:
    """A Venice key is here: the environment, or opencode's sign-in."""
    if os.environ.get("VENICE_API_KEY", "").strip():
        return True
    try:
        return "venice" in json.loads((_opencode_data_default() / "auth.json").read_text())
    except (OSError, ValueError, TypeError):
        return False


def venice_context(model_id: str) -> int:
    m = _venice_catalogue().get(model_id) or {}
    try:
        return int((m.get("limit") or {}).get("context") or 0)
    except (TypeError, ValueError):
        return 0


def venice_models(setting: str | None = None) -> list[dict]:
    """Codex's Venice choices, where a key is here: the `venice/<id>` models
    `MEDIA_CODEX_MODELS` names (comma-separated, each optionally `=Label`,
    as MEDIA_OPENCODE_MODELS), in its order. Venice has over a hundred, so
    the host says which; none named, none offered. One too small for Codex's
    own prompt (VENICE_MIN_CONTEXT, by opencode's cached catalogue) is left
    out."""
    raw = os.environ.get("MEDIA_CODEX_MODELS", "") if setting is None else setting
    if not raw.strip() or not venice_ready():
        return []
    catalogue = _venice_catalogue()
    rows: list[dict] = []
    for item in raw.split(","):
        mid, _, label = item.strip().partition("=")
        mid = mid.strip()
        if not mid.startswith("venice/") or any(r["id"] == mid for r in rows):
            continue
        bare = mid.split("/", 1)[1]
        if not bare or not all(c.isalnum() or c in "._-" for c in bare):
            continue
        context = venice_context(bare)
        if context and context < VENICE_MIN_CONTEXT:
            continue
        name = (catalogue.get(bare) or {}).get("name") or bare
        rows.append({"id": mid, "label": label.strip() or f"Venice · {name}",
                     "note": "Venice: trusted for private personas"})
    return rows


def env(harness: str, d: Path) -> dict[str, str]:
    """The environment a chat in home `d` runs with (harness_profiles.env)."""
    if harness == "opencode":
        return {"XDG_DATA_HOME": str(d)}
    return {"CODEX_HOME": str(d)}


def opencode_homes() -> list[Path]:
    """Every opencode private home here."""
    return [Path(p.dir) for p in harness_profiles.of("opencode") if is_home(p)]


def is_opencode_home(path: str) -> bool:
    """Whether `path` is one of this host's opencode private homes (sessiond
    is told one by the server; nothing else is taken as one)."""
    try:
        want = Path(path).resolve()
    except (OSError, ValueError):
        return False
    return any(d.resolve() == want for d in opencode_homes())


def note_session(home: Path, sid: str) -> None:
    """The opencode chat a home holds (sessiond, once opencode made it)."""
    f = Path(home) / ".session"
    f.write_text(sid + "\n")
    _FOUND[sid] = Path(home)


_FOUND: dict[str, Path] = {}


def opencode_home_of(sid: str) -> Path | None:
    """The private home whose server holds opencode session `sid`: the one
    its id is written in, else (a subagent's session) a home whose server
    is up and has it. None: the user's own server."""
    got = _FOUND.get(sid)
    if got is not None and got.is_dir():
        return got
    homes = opencode_homes()
    for d in homes:
        try:
            if (d / ".session").read_text().strip() == sid:
                _FOUND[sid] = d
                return d
        except OSError:
            continue
    if not homes or not sid.startswith("ses"):
        return None
    from . import opencode_api

    for d in homes:
        h = opencode_api.HomeService(d)
        if h.find(start=False):
            try:
                h.call("GET", "/api/session/" + urllib.parse.quote(sid), timeout=3.0)
            except opencode_api.ApiError:
                continue
            _FOUND[sid] = d
            return d
    return None


def quiesce(harness: str, d: Path) -> None:
    """Before a home is sealed: nothing of it still running (opencode's server)."""
    if harness == "opencode":
        from . import opencode_api

        opencode_api.stop_home(d)
        for k in [k for k, v in _FOUND.items() if v == d]:
            del _FOUND[k]


def readopt(harness: str, d: Path) -> None:
    """Make a home a profile again (an unsealed thread's), by its folder name."""
    name = d.name[len(harness) + 1:] if d.name.startswith(harness + "-") else ""
    if not name.startswith(PREFIX) or harness_profiles.get(harness, name):
        return
    rows = harness_profiles.load()
    harness_profiles._save([*rows, harness_profiles.Profile(harness, name, str(d))])
    relink(harness, d)


def relink(harness: str, d: Path) -> None:
    """The sign-in (and Codex's hooks, skills, plugins) linked into a home
    again: a bundle keeps links out of what it restores."""
    if harness == "codex":
        src = _codex_default()
        for name in ("auth.json", "hooks.json", "skills", "plugins", "packages"):
            _link(src / name, d / name)
    elif harness == "opencode":
        _link(_opencode_data_default() / "auth.json", d / "opencode" / "auth.json")


def forget(harness: str, name: str) -> None:
    """The profile row of a home whose folder seal.py has taken."""
    if name.startswith(PREFIX):
        harness_profiles.remove(harness, name, delete=True)


def remove_tree(d: Path) -> None:
    shutil.rmtree(d, ignore_errors=True)
