"""Resolve what sits above and below each room — pure geometry, no thermal knowledge.

Eldr used to assign a ceiling to the highest level and a floor to the lowest, both from
level bounding boxes. A house that grew in stages defeats that: a partial second floor
leaves most of the main level's ceiling facing the attic, and an extension over a
crawlspace has a floor the model never saw.

Two rules make this work on real models:

* **XY overlap decides adjacency; elevation only decides ordering.** Levels overlap
  vertically without being adjacent — an attached garage can span the basement AND the
  lower half of the main floor while sitting beside the house, not under it.
* **Roomless levels are scaffolding** (joists, duct chases) and take no part — but only
  when some other level has rooms, since a model with no rooms at all is a supported
  case with its own bounding-box fallback.
"""
from __future__ import annotations
from dataclasses import dataclass

# Void cells within this distance of the room's own outline are treated as wall
# misalignment rather than real exposure. Sized just over a typical framed wall
# thickness (7in = 17.8cm) — artifacts hug the boundary; real voids reach inside.
TOLERANCE_CM = 20.0
# A resolved region smaller than this is noise (a shared wall clipping the level
# below) and is redistributed across the surviving categories.
MIN_REGION_FT2 = 2.0
# Rasterization cell size. 15cm cells are ~0.24 ft^2 — far finer than MIN_REGION_FT2.
GRID_CM = 15.0


@dataclass(frozen=True)
class LevelInfo:
    id: str
    name: str
    elevation_cm: float
    elevation_index: int
    height_cm: float
    conditioned: bool


def ordered_levels(levels: list[LevelInfo]) -> list[LevelInfo]:
    """Levels bottom to top. Elevation ties break on elevationIndex — SH3D uses that
    to stack same-elevation levels (a garage and a crawlspace sharing a height)."""
    return sorted(levels, key=lambda l: (l.elevation_cm, l.elevation_index))


def scaffolding_ids(levels: list[LevelInfo],
                    rooms_by_level: dict[str, list[dict]]) -> frozenset[str]:
    """Levels that exist to hold geometry rather than space, so take no part in the stack.

    A level with no rooms qualifies — UNLESS no level in the model has rooms, in which
    case the model is simply roomless and the caller's bounding-box fallback owns it.
    """
    if not any(rooms_by_level.get(l.id) for l in levels):
        return frozenset()
    return frozenset(l.id for l in levels if not rooms_by_level.get(l.id))
