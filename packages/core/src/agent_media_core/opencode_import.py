"""`opencode import <file>` for opencode 2, which has no such command.

txcript (convert.py) writes a chat for opencode as a v1 export file and
runs `opencode import <file>`; opencode 2 dropped that command and imports
over its server instead (`POST /api/experimental/session/import`, which
"accepts transcripts exported by earlier releases"). convert.py puts a
stand-in `opencode` ahead on txcript's PATH (`shim_dir`): `import` comes
here, anything else goes to the real opencode. The id opencode 2 gives the
copy is printed, and written to $MEDIA_OPENCODE_IMPORTED when that is set,
so convert reports the session that exists rather than the one txcript
named. David, 16 Nov 2026: "Not copied — error: cannot convert to
opencode: `opencode import` failed: ERROR Unexpected positional argument".
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path


def import_file(path: str) -> str:
    """Import a v1 export file into opencode 2; the new session's id.
    Raises RuntimeError with opencode's words."""
    from . import opencode_api

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "info" not in data or "messages" not in data:
        raise RuntimeError("not an opencode export (no info and messages)")
    srv = opencode_api.service(start=True)
    if srv is None:
        raise RuntimeError("opencode's background server is not running and did not start")
    old = data.get("info") if isinstance(data.get("info"), dict) else {}
    folder = str(old.get("directory") or "") or os.getcwd()
    info = v2_info(srv, old, folder)
    body = {"info": info, "messages": v2_messages(data["messages"], info.get("model")),
            "location": {"directory": folder}}
    try:
        got = srv.call("POST", "/api/experimental/session/import", body, timeout=120)
    except opencode_api.ApiError as e:
        raise RuntimeError(str(e)) from None
    sid = str((got or {}).get("id") or "") if isinstance(got, dict) else ""
    if not sid:
        raise RuntimeError("opencode imported nothing it could name")
    title = str(old.get("title") or "")
    if title and not (got or {}).get("title"):
        try:
            srv.call("PATCH", f"/api/session/{sid}", {"title": title})
        except opencode_api.ApiError:
            pass
    return sid


#: v1 tool names as v2 calls them.
_V2_TOOL = {"bash": "shell", "task": "subagent", "write": "edit", "patch": "edit"}
#: The finish reasons v2's import takes.
_V2_FINISH = frozenset({"stop", "length", "tool-calls", "content-filter", "error", "unknown"})


def v2_messages(msgs: list, model: dict | None = None) -> list[dict]:
    """A v1 export's messages (`{info, parts}`) as v2's: a user message is
    its typed words; an assistant step its text, reasoning and tool calls
    (a tool's output as its text content; one never finished is an error).
    v1's step markers, patches and snapshots have no place in v2's and go."""
    out: list[dict] = []
    last_model = model or {"id": "unknown", "providerID": "unknown"}
    for m in msgs if isinstance(msgs, list) else []:
        info = m.get("info") if isinstance(m, dict) and isinstance(m.get("info"), dict) else {}
        parts = [p for p in (m.get("parts") or []) if isinstance(p, dict)]
        mid = str(info.get("id") or "")
        if not mid.startswith("msg_"):
            continue
        t = info.get("time") if isinstance(info.get("time"), dict) else {}
        created = int(t.get("created") or 0)
        if info.get("role") == "user":
            text = "\n".join(str(p.get("text") or "") for p in parts
                             if p.get("type") == "text" and not p.get("synthetic")).strip()
            out.append({"id": mid, "time": {"created": created}, "type": "user", "text": text})
            mm = info.get("model") if isinstance(info.get("model"), dict) else {}
            if mm.get("modelID") and mm.get("providerID"):
                last_model = {"id": str(mm["modelID"]), "providerID": str(mm["providerID"])}
            continue
        if info.get("role") != "assistant":
            continue
        if info.get("modelID") and info.get("providerID"):
            last_model = {"id": str(info["modelID"]), "providerID": str(info["providerID"])}
        content: list[dict] = []
        for p in parts:
            kind = p.get("type")
            if kind in ("text", "reasoning"):
                content.append({"type": kind, "text": str(p.get("text") or "")})
            elif kind == "tool":
                st = p.get("state") if isinstance(p.get("state"), dict) else {}
                pt = st.get("time") if isinstance(st.get("time"), dict) else {}
                name = str(p.get("tool") or "")
                inp = st.get("input") if isinstance(st.get("input"), dict) else {}
                if st.get("status") == "completed":
                    state = {"status": "completed", "input": inp,
                             "content": [{"type": "text", "text": str(st.get("output") or "")}],
                             "metadata": st.get("metadata") if isinstance(st.get("metadata"), dict) else {}}
                else:
                    state = {"status": "error", "input": inp,
                             "error": {"type": "tool.error",
                                       "message": str(st.get("error") or "did not finish")}}
                content.append({"type": "tool", "id": str(p.get("callID") or p.get("id") or ""),
                                "name": _V2_TOOL.get(name, name), "state": state,
                                "time": {"created": int(pt.get("start") or created),
                                         **({"completed": int(pt["end"])} if pt.get("end") else {})}})
        step: dict = {"id": mid, "time": {"created": created}, "type": "assistant",
                      "agent": str(info.get("agent") or info.get("mode") or "build"),
                      "model": last_model, "content": content}
        if t.get("completed"):
            step["time"]["completed"] = int(t["completed"])
        if info.get("finish"):
            # v2 keeps its own few; another (a provider's own word) is its raw one.
            fin = str(info["finish"])
            if fin in _V2_FINISH:
                step["finish"] = fin
            else:
                step["finish"], step["rawFinish"] = "unknown", fin
        out.append(step)
    return out


def v2_info(srv, old: dict, folder: str) -> dict:
    """A v1 export's session `info` as v2's import wants it: its own
    `Session.Info` (the folder's project id, zero cost and tokens, the
    times, the location); the messages it takes as v1 wrote them."""
    import urllib.parse

    from . import opencode_api

    try:
        loc = srv.call("GET", "/api/location?" + urllib.parse.urlencode(
            {"location[directory]": folder}), timeout=30) or {}
    except opencode_api.ApiError as e:
        raise RuntimeError(f"opencode does not know the folder {folder}: {e}") from None
    project = str(((loc.get("project") or {}) if isinstance(loc, dict) else {}).get("id") or "")
    if not project:
        raise RuntimeError(f"opencode has no project for {folder}")
    t = old.get("time") if isinstance(old.get("time"), dict) else {}
    now = int(__import__("time").time() * 1000)
    info = {"id": str(old.get("id") or ""), "projectID": project, "cost": 0,
            "tokens": {"input": 0, "output": 0, "reasoning": 0, "cache": {"read": 0, "write": 0}},
            "time": {"created": int(t.get("created") or now), "updated": int(t.get("updated") or now)},
            "location": {"directory": folder}}
    m = old.get("model") if isinstance(old.get("model"), dict) else {}
    mid = m.get("id") or m.get("modelID")
    if mid and m.get("providerID"):
        info["model"] = {"id": str(mid), "providerID": str(m["providerID"])}
    if not info["id"]:
        info.pop("id")
    return info


def shim_dir() -> str:
    """A folder holding a stand-in `opencode` for txcript to find first."""
    d = Path(tempfile.mkdtemp(prefix="am-opencode-shim-"))
    real = _real_opencode()
    script = d / "opencode"
    script.write_text(
        "#!/bin/sh\n"
        "# agent-media: opencode 2 has no `import`; see agent_media_core/opencode_import.py\n"
        'if [ "$1" = "import" ]; then\n'
        f'  exec "{sys.executable}" -m agent_media_core.opencode_import "$2"\n'
        "fi\n"
        f'exec "{real}" "$@"\n')
    script.chmod(0o755)
    return str(d)


def _real_opencode() -> str:
    from . import harnesses

    return harnesses.program("opencode") or "opencode"


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not args:
        print("usage: python -m agent_media_core.opencode_import <export.json>", file=sys.stderr)
        return 2
    try:
        sid = import_file(args[0])
    except (OSError, ValueError, RuntimeError) as e:
        print(f"opencode 2 import failed: {e}", file=sys.stderr)
        return 1
    note = os.environ.get("MEDIA_OPENCODE_IMPORTED")
    if note:
        try:
            Path(note).write_text(sid, encoding="utf-8")
        except OSError:
            pass
    print(f"Imported session: {sid}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
