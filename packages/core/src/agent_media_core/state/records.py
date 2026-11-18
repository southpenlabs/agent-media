"""The state store's history rows, typed (docs/proposals/2026-11-19-typed-records-and-settings.md).

Plain dicts at runtime, as StateStore has always returned them (TypedDict,
nothing converted); the types are for the checker in CI. A speech row is a
history row with ``sink == "speech"``; its ``extras`` say whose reply it was,
its clips and sentences, and whether it was heard.

``extras`` is parsed JSON, but a row whose JSON did not parse keeps the raw
string, and an old row may have none: read it with ``extras_of(row)``,
which is always a dict.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, TypedDict, Union


class SpeechExtras(TypedDict, total=False):
    """A speech row's ``extras``: the keys the readers rely on. Others are
    kept as they are; TypedDict only checks the ones named here."""

    # Whose reply.
    session: str
    source_session: str
    source_title: str
    title: str
    cwd: str
    kind: str                 # reply, notice, ask …
    dedup_key: str            # one reply's copies share it
    listener: bool            # the listener's own words, not a reply
    turn_by: str              # who the reply answered: owner, mail, notice …
    turn_at: float
    turn_id: str
    # How it was said.
    engine: str
    voice: str
    voice_from: str
    priority: str
    clip_uris: list[str]
    clip_sentences: list[str]
    clip_durations_s: list[float]
    clip_starts_s: list[float]
    clip_paragraph_idx: list[int]
    # Whether it was heard.
    held: bool                # kept to be played later, not spoken as it came
    held_why: str
    heard: bool
    muted: bool
    flushed: bool
    stopped_at: dict[str, Any]   # {"sentence", …}: stopped part way (session_feed._resume_of)
    watched_at: float
    # A chat with people (Matrix).
    matrix_event: str
    matrix_room: str
    mail_from: str
    # A question card.
    ask: Union[bool, list[Any]]
    ask_part: int
    ask_cue: bool
    ask_answerable_at: float
    tool_use_id: str


class HistoryRow(TypedDict):
    """One row of ``history`` (StateStore.recent_history, history_row)."""

    id: int
    sink: str
    uri: str
    started_at: float
    ended_at: Optional[float]
    target: Optional[str]
    source: Optional[str]
    content_type: Optional[str]
    text: Optional[str]
    extras: Union[SpeechExtras, str, None]


class MatrixSpeech(TypedDict):
    """StateStore.matrix_speech's value: a room message's newest speech row."""

    id: int
    started_at: float
    extras: SpeechExtras


def extras_of(row: Mapping[str, Any]) -> SpeechExtras:
    """A row's extras as a dict: {} for none or for JSON that did not parse."""
    ex = row.get("extras")
    return ex if isinstance(ex, dict) else {}  # type: ignore[return-value]
