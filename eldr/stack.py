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

from eldr import units

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


@dataclass(frozen=True)
class FaceSplit:
    """How one conditioned room's floor and ceiling divide by what they face.

    `below` / `above` map a category — "interior", "ground", or a space name — to area
    in ft^2. Each side sums to the room's own polygon area: every square foot of floor
    and ceiling faces something. `void_below_ft2` is the measured area that survived the
    misalignment tolerance with nothing drawn beneath it, reported so a schematic gap
    stays visible; it is not part of that sum's bookkeeping.
    """
    room_id: str
    below: dict[str, float]
    above: dict[str, float]
    void_below_ft2: float = 0.0


def _bbox(points):
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return min(xs), max(xs), min(ys), max(ys)


def _inside(x, y, points):
    """Ray-cast point-in-polygon; handles the non-convex room outlines SH3D allows."""
    hit = False
    n = len(points)
    for i in range(n):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % n]
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            hit = not hit
    return hit


def _seg_distance(px, py, x1, y1, x2, y2):
    dx, dy = x2 - x1, y2 - y1
    if dx == 0.0 and dy == 0.0:
        return ((px - x1) ** 2 + (py - y1) ** 2) ** 0.5
    t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)))
    return ((px - (x1 + t * dx)) ** 2 + (py - (y1 + t * dy)) ** 2) ** 0.5


def _edge_distance(px, py, points):
    n = len(points)
    return min(_seg_distance(px, py, *points[i], *points[(i + 1) % n]) for i in range(n))


def _covering_room(x, y, rooms):
    """The first room in `rooms` whose polygon covers (x, y), or None. Bounding-box
    pre-filtered — this runs once per raster cell per candidate level."""
    for rm in rooms:
        minx, maxx, miny, maxy = rm["_bbox"]
        if minx <= x <= maxx and miny <= y <= maxy and _inside(x, y, rm["points"]):
            return rm
    return None


def _space_name(level: LevelInfo) -> str:
    """An unconditioned level becomes a named buffer space — its own name, lowercased,
    so `spaces:` in the side-car keys off something the owner can see in SH3D."""
    return (level.name or "space").strip().lower()


def _tidy(areas: dict[str, float], min_region_ft2: float) -> dict[str, float]:
    """Drop noise regions and redistribute their area across the survivors.

    A shared wall clips a level below by a fraction of a square foot; that is an
    artifact of two polygons meeting, not a surface. If EVERY category is under the
    threshold the room is simply smaller than the threshold — keep the largest so a
    tiny closet still gets its ceiling.
    """
    if not areas:
        return {}
    kept = {k: v for k, v in areas.items() if v >= min_region_ft2}
    if not kept:
        top = max(areas, key=lambda k: areas[k])
        return {top: sum(areas.values())}
    total = sum(areas.values())
    scale = total / sum(kept.values())
    return {k: v * scale for k, v in kept.items()}


def _to_room_area(areas: dict[str, float], area_ft2: float) -> dict[str, float]:
    """Scale a face's categories so they sum to the room's own polygon area.

    The resolver decides how a floor or ceiling SPLITS, never how much of it there is —
    that is the shoelace area upstream. Two things otherwise leak area away: void cells
    inside the misalignment tolerance are dropped outright rather than reassigned, and a
    raster only approximates a polygon. Both are re-absorbed here, in proportion.
    """
    total = sum(areas.values())
    if not areas or total <= 0.0:
        return areas
    scale = area_ft2 / total
    return {k: v * scale for k, v in areas.items()}


def resolve_faces(levels: list[LevelInfo], rooms_by_level: dict[str, list[dict]], *,
                  below_void: str = "crawlspace", above_void: str = "attic",
                  level_voids: dict[str, tuple[str | None, str | None]] | None = None,
                  ignore_ids: frozenset[str] = frozenset(),
                  tolerance_cm: float = TOLERANCE_CM,
                  min_region_ft2: float = MIN_REGION_FT2,
                  grid_cm: float = GRID_CM) -> dict[str, FaceSplit]:
    """Resolve every conditioned room's floor and ceiling against the levels around it.

    `below_void` / `above_void` name the space an undrawn gap represents — a house's
    unmodeled space beneath is almost always crawlspace and above is almost always
    attic. `level_voids` overrides either per level id, for the model where one wing
    sits over open air while the rest sits over crawl.
    """
    skip = set(ignore_ids) | set(scaffolding_ids(levels, rooms_by_level))
    order = [l for l in ordered_levels(levels) if l.id not in skip]
    if not order:
        return {}

    by_id = {l.id: l for l in order}
    rooms = {l.id: [dict(rm, _bbox=_bbox(rm["points"])) for rm in rooms_by_level.get(l.id, [])]
             for l in order}
    lowest_conditioned = next((l.id for l in order if any(
        rm["conditioned"] for rm in rooms[l.id])), None)

    out: dict[str, FaceSplit] = {}

    for level in order:
        others_below = [l for l in order if (l.elevation_cm, l.elevation_index)
                        < (level.elevation_cm, level.elevation_index)][::-1]
        others_above = [l for l in order if (l.elevation_cm, l.elevation_index)
                        > (level.elevation_cm, level.elevation_index)]
        lv_below, lv_above = (level_voids or {}).get(level.id, (None, None))
        this_below_void = lv_below or below_void
        this_above_void = lv_above or above_void
        for room in rooms[level.id]:
            if not room["conditioned"]:
                continue
            below: dict[str, float] = {}
            above: dict[str, float] = {}
            void_cells = []
            minx, maxx, miny, maxy = room["_bbox"]
            # Whole number of cells covering the bounding box EXACTLY, each about
            # grid_cm across. Stepping a fixed grid_cm from minx instead would leave a
            # partial cell at the far edge that still counts as a whole one, inflating
            # the room and — worse — biasing a 50/50 split by a full column: 400cm at
            # 15cm is 26.67 columns, and the 27th lands wholly on one side.
            nx = max(1, int((maxx - minx) / grid_cm))
            ny = max(1, int((maxy - miny) / grid_cm))
            step_x = (maxx - minx) / nx
            step_y = (maxy - miny) / ny
            cell_ft2 = units.sqcm_to_sqft(step_x * step_y)
            for iy in range(ny):
                cy = miny + (iy + 0.5) * step_y
                for ix in range(nx):
                    cx = minx + (ix + 0.5) * step_x
                    if not _inside(cx, cy, room["points"]):
                        continue
                    for neighbours, bucket, is_below in (
                            (others_below, below, True),
                            (others_above, above, False)):
                        found = None
                        for cand in neighbours:
                            hit = _covering_room(cx, cy, rooms[cand.id])
                            if hit is not None:
                                found = ("interior" if hit["conditioned"]
                                         else _space_name(by_id[cand.id]))
                                break
                        if found is None:
                            if is_below and level.id == lowest_conditioned:
                                found = "ground"
                            elif is_below:
                                void_cells.append((cx, cy))
                                continue
                            else:
                                found = this_above_void
                        bucket[found] = bucket.get(found, 0.0) + cell_ft2

            real_void = [c for c in void_cells
                         if _edge_distance(c[0], c[1], room["points"]) > tolerance_cm]
            void_ft2 = len(real_void) * cell_ft2
            if void_ft2 > 0.0:
                below[this_below_void] = below.get(this_below_void, 0.0) + void_ft2

            # void_below_ft2 stays as MEASURED — it is the raster's own estimate of a
            # schematic gap, reported so the warning means something, not a share of a
            # normalized whole.
            area_ft2 = room["area_ft2"]
            out[room["id"]] = FaceSplit(
                room_id=room["id"],
                below=_to_room_area(_tidy(below, min_region_ft2), area_ft2),
                above=_to_room_area(_tidy(above, min_region_ft2), area_ft2),
                void_below_ft2=void_ft2,
            )
    return out
