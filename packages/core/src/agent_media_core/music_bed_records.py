"""The music bed's records, typed: what music-bed-favourites.json and
music-bed-vocal.json hold (docs/proposals/2026-11-19-typed-records-and-settings.md,
the pilot).

They stay dicts on disk and in memory (TypedDict, no conversion), so
nothing that reads or writes them changes; the types are for the checker
in CI (mypy, over the modules listed in packages/core/pyproject.toml).
They tell apart the two things called ``key``: a record's own ``key`` is
its favourite's key (``str``, music_bed_favourites.key_for), and the
``key`` in its ``features`` is the musical key music_bed_grid measures
(``MusicalKey``). Writing one where the other belongs is the bug 4b1324ad
repaired (agent mail #1044), and is now a type error.
"""

from __future__ import annotations

from typing import Any, Optional, TypedDict


class MusicalKey(TypedDict):
    """music_bed_sections.py's key: ``{"tonic": "F#", "mode": "minor",
    "name": "F# minor", "camelot": "11A", "r": 0.54}``."""

    tonic: str
    mode: str
    name: str
    camelot: str
    r: float


class BeatGrid(TypedDict):
    bpm: float
    offset: float
    confidence: float


class Measure(TypedDict, total=False):
    """The key-and-beat measure (music_bed_grid.keep): each null when the
    file could not be told, ``grid_v`` the measure's version."""

    key: Optional[MusicalKey]
    beats: Optional[BeatGrid]
    grid_v: int


class TrackFeatures(Measure, total=False):
    """A bed track's ``features``: music_bed_tempo's measure and the grid's."""

    lufs: float
    bpm: float
    tempo: float
    energy: float
    onsets: float


class _FavouriteBase(TypedDict):
    key: str
    artist: str
    title: str
    id: str
    dur: int
    score: float


# "from" is a keyword, so the rest is spelled the functional way.
_FavouriteMore = TypedDict("_FavouriteMore", {
    "from": list[str],     # where it was found: liked, played, suggested …
    "start": float,        # a song of a mix: its chapter of the mix's video
    "end": float,
    "song_key": str,
}, total=False)


class Favourite(_FavouriteBase, _FavouriteMore):
    """One of his favourites, as gather() ranks them (``favourites``)."""


class BedTrack(TypedDict, total=False):
    """A favourite's instrumental, made and kept (``tracks``)."""

    key: str               # its favourite's key
    file: str              # under the speech audio dir
    title: str
    artist: str
    method: str            # "official" or "separated"
    original_id: str
    instrumental_id: str
    made: str              # YYYY-MM-DD
    added: float
    bytes: int
    features: TrackFeatures
    lufs_target: float
    karaoke: bool
    original_start: Optional[float]
    original_end: Optional[float]
    song_key: str


class FavouritesIndex(TypedDict, total=False):
    """music-bed-favourites.json (music_bed_favourites.load fills the
    first five)."""

    favourites: list[Favourite]
    tracks: list[BedTrack]
    state: dict[str, dict[str, Any]]
    nights: dict[str, Any]
    lookups: dict[str, Any]
    favourites_at: float
    pass_at: float
    renown: dict[str, Any]
    skipped: dict[str, Any]    # {"at", "reason"}: why separating was skipped last


class VocalOriginal(TypedDict, total=False):
    """An original the outro sings from (music_bed_vocal), by its
    favourite's key in music-bed-vocal.json."""

    key: str               # its favourite's key, never the musical key
    file: str
    bed: str               # the favourites bed file it goes with
    lufs: float
    aligned: bool
    orig_s: float
    inst_s: float
    bounds: list[Any]
    choruses: list[Any]
    made: float
    features: Measure


VocalIndex = dict[str, VocalOriginal]
