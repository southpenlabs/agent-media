"""Sessions, typed: sessiond's record of a headless session and the rows
of `/sessions/state` (docs/proposals/2026-11-19-typed-records-and-settings.md;
contract §6.1 and §17).

Plain dicts at runtime as before (TypedDict, nothing converted); the types
are for the checker in CI.
"""

from __future__ import annotations

from typing import Any, Literal, Optional, TypedDict, Union

#: What a session is doing (contract §6.1): answered and waiting on you,
#: at work, or stopped on a dialog.
State = Literal["working", "waiting", "approval"]


class _StateRowBase(TypedDict):
    session: str
    state: State
    mem_mb: Optional[int]
    title: str


class StateRow(_StateRowBase, total=False):
    """One of `/sessions/state`'s `sessions`."""

    background: dict[str, int]  # {"agents", "commands"}: a waiting row's work still running
    driver: Literal["headless"]
    host: str                 # a headless session run on another host


class Host(TypedDict, total=False):
    """`/sessions/state`'s `host`: the machine's memory (procmem.host_mem)
    and what the listed sessions hold."""

    mem_total_mb: int
    mem_available_mb: int
    swap_total_mb: int
    swap_free_mb: int
    mem_stall_pct: float
    strained: Optional[bool]  # None: the stall is not known (memwatch.strained)
    sessions_mem_mb: int


class SessionRecord(TypedDict, total=False):
    """sessiond's record of a headless session, `<state>/sessiond/<id>.json`
    (sessiond.records)."""

    session: str              # the thread's id
    native_session: str       # the harness's own id for it
    agent: str                # claude, codex, pi, opencode, antigravity …
    driver: str
    cwd: str
    workspace: str
    host: str                 # run on another host (roadmap 56)
    remote_cwd: str
    model: str
    init_model: str
    mode: str
    init_mode: str
    permissions: str
    permissions_default: bool
    server_permissions: str
    owner_server: str
    system_prompt_file: str
    # What it is doing.
    state: str                # sessiond's own (driver.headless.contract_state maps it)
    since: float
    created: float
    last_event_at: float
    pid: Optional[int]
    exit_code: Optional[int]
    turns: int
    seq: int
    seq_seen: int
    first_text: str
    last_text: str
    last_result: Optional[dict[str, Any]]
    tasks: int
    task_list: list[dict[str, Any]]
    # Messages waiting, held or lost.
    pending: list[Any]
    queued: list[Any]
    lost: list[Any]
    held: Union[dict[str, Any], list[Any]]
    cut_off: Optional[dict[str, Any]]
    quota_resumed: float
    interrupt_killed: list[Any]
    copy: dict[str, Any]
