"""The business agents' workspaces: folders an agent works in, each with a
persona of the same id, told apart from projects so the app lists them under
an Agents heading of their own (David, 6 Nov 2026: "their own folded Agents
heading in the app's menu, instead of being mixed in with the projects";
server-contract §6.1 `/targets` `agent_workspaces`, `/project/list` `kind`).

A workspace is a direct child folder of an agents folder that holds a
CLAUDE.md. The agents folders are `MEDIA_AGENT_WORKSPACES` (one path, or
several separated by os.pathsep; default `~/spl/agents`). Its persona is the
one of the same id in the personas repo (personas.repo_dir(), `personas/
<id>.md` or `personas/<id>/`), else None — the folder is listed either way.
Its name is the persona's own `name:`, else the id capitalised. `front` is
the one David talks to: `MEDIA_AGENT_FRONT` (default `nova`). A host with no
agents folder has none.
"""

from __future__ import annotations

import os
from pathlib import Path

from . import personas


def roots() -> list[Path]:
    raw = os.environ.get("MEDIA_AGENT_WORKSPACES")
    if raw is None:
        raw = "~/spl/agents"
    return [Path(os.path.expanduser(p.strip())) for p in raw.split(os.pathsep) if p.strip()]


def front_id() -> str:
    return (os.environ.get("MEDIA_AGENT_FRONT") or "nova").strip()


def _persona_name(path: Path) -> str:
    """The `name:` in a persona file's front matter, or ""."""
    try:
        with path.open(encoding="utf-8") as fh:
            if fh.readline().strip() != "---":
                return ""
            for _ in range(60):
                line = fh.readline()
                if not line or line.strip() == "---":
                    break
                if line.startswith("name:"):
                    return line[5:].strip().strip("\"'")
    except (OSError, UnicodeDecodeError):
        pass
    return ""


def _persona(agent_id: str) -> tuple[str | None, str]:
    """`(persona id or None, its display name or "")`."""
    base = personas.repo_dir() / "personas"
    md = base / f"{agent_id}.md"
    if md.is_file():
        return agent_id, _persona_name(md)
    folder = base / agent_id
    if folder.is_dir():
        return agent_id, _persona_name(folder / "persona.md")
    return None, ""


def workspaces() -> list[dict]:
    """`[{project, id, name, path, persona, front}]`, the front one first, then
    by name. `project` is the key the thread list files a chat in that folder
    under (sessions.project_of)."""
    from .sessions import project_of

    front, seen, out = front_id(), set(), []
    for root in roots():
        try:
            kids = sorted(root.iterdir())
        except OSError:
            continue
        for d in kids:
            if d.name.startswith(".") or not d.is_dir() or not (d / "CLAUDE.md").is_file():
                continue
            path = str(d.resolve())
            if path in seen:
                continue
            seen.add(path)
            pid, pname = _persona(d.name)
            out.append({"project": project_of(path) or d.name, "id": d.name,
                        "name": pname or d.name[:1].upper() + d.name[1:],
                        "path": path, "persona": pid, "front": d.name == front})
    out.sort(key=lambda w: (not w["front"], w["name"].lower()))
    return out


def for_targets(rows: list[dict] | None = None) -> list[dict]:
    """`/targets` `agent_workspaces`: `[{project, id, name, path, front}]`."""
    rows = workspaces() if rows is None else rows
    return [{k: w[k] for k in ("project", "id", "name", "path", "front")} for w in rows]


def find(project: str = "", path: str = "", rows: list[dict] | None = None) -> dict | None:
    """The workspace a project key or folder names, else None."""
    rows = workspaces() if rows is None else rows
    real = ""
    if path:
        try:
            real = str(Path(os.path.expanduser(path)).resolve())
        except OSError:
            real = ""
    for w in rows:
        if (real and w["path"] == real) or (project and not path and w["project"] == project):
            return w
    return None
