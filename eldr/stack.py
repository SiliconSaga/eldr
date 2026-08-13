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

# How far into a VOID REGION the misalignment tolerance reaches. A void cell within
# this distance of real coverage is treated as wall misalignment rather than real
# exposure. Sized just over a typical framed wall thickness (7in = 17.8cm) — an
# artifact is at most a wall thick, so it dissolves entirely; a real void is fat, so
# its core is out of reach and survives. (This is measured against the void's own
# boundary, never against the room's outline — see _settle_voids.)
TOLERANCE_CM = 20.0
# A resolved region smaller than this is noise (a shared wall clipping the level
# below) and is redistributed across the surviving categories.
MIN_REGION_FT2 = 2.0
# Rasterization cell size. 15cm cells are ~0.24 ft^2 — far finer than MIN_REGION_FT2.
#
# TOLERANCE_CM and GRID_CM are COUPLED, which they did not use to be. _settle_voids
# does a morphological opening (erode, then dilate), and the gap metric reaches one cell
# further than the tolerance itself, so the width that dissolves completely scales
# roughly as
#     2 x (TOLERANCE_CM + one cell)
# — on the order of 70cm at the current settings, ~80cm at GRID_CM=20, ~60cm at
# GRID_CM=10. Treat that as a rule of thumb for which knob to reach for, NOT as a
# threshold: the real cutoff is a band, not a number. It falls out of a discrete metric
# on a grid whose cells are sized per room (each room's bbox is divided into a whole
# number of cells), so it varies with a void's shape and where it lands relative to the
# cell centres, and it does not move cleanly monotonically with grid size. If a
# particular void's fate matters, measure it — don't compute it from the formula.
#
# The point that does survive: grid size was once a pure discretization knob. Refining
# it now also narrows what counts as an artifact, so move it deliberately, not for speed.
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


def _nearest_within(ix, iy, candidates, step_x, step_y, tolerance_cm):
    """The cell of `candidates` nearest to cell (ix, iy), or None past `tolerance_cm`.

    Distance is measured between the cells themselves — the Euclidean gap between their
    extents — not between their centres, and that distinction is load-bearing. A cell
    only samples what is under its centre, so centre-to-centre overstates how far a cell
    is from what is drawn beside it by up to a full cell. On a one-cell-wide misalignment
    ring the corner cells sit 21.5cm diagonally from the nearest covered centre, past a
    20cm tolerance, and would read as genuine void while their neighbours all dissolve —
    the ring loses its sides and keeps its corners. Touching cells have a gap of zero.

    Only the index window the tolerance can reach is scanned, so settling the voids stays
    linear in cell count rather than quadratic. Ties break on centre distance, which is
    what makes an orthogonal neighbour outrank a diagonal one when both touch.
    """
    if not candidates:
        return None
    rx = int(tolerance_cm / step_x) + 2      # +1 for the gap metric's extra reach, +1 to round up
    ry = int(tolerance_cm / step_y) + 2
    best = None
    best_rank = None
    for jy in range(iy - ry, iy + ry + 1):
        for jx in range(ix - rx, ix + rx + 1):
            if (jx, jy) not in candidates:
                continue
            gx = max(0, abs(jx - ix) - 1) * step_x
            gy = max(0, abs(jy - iy) - 1) * step_y
            gap = (gx * gx + gy * gy) ** 0.5
            if gap > tolerance_cm:
                continue
            dx, dy = (jx - ix) * step_x, (jy - iy) * step_y
            rank = (gap, (dx * dx + dy * dy) ** 0.5)
            if best_rank is None or rank < best_rank:
                best_rank = rank
                best = (jx, jy)
    return best


def _settle_voids(cells, step_x, step_y, cell_ft2, tolerance_cm, void_name):
    """Separate real exposure from wall misalignment among a floor's uncovered cells.

    `cells` maps a raster index to the category resolved beneath it, or None where
    nothing was drawn. Returns (areas by category, measured area of surviving void).

    The tolerance is applied to the VOID REGION's own boundary, never to the room's
    outline. A misalignment artifact is at most a wall thickness wide, so every one of
    its cells sits within tolerance of real coverage and the whole artifact is reabsorbed
    into whatever is drawn beside it. A genuine void — undrawn crawlspace under an
    extension — is fat, so its core is out of reach and survives. Measuring against the
    room's outline instead would eat a tolerance-wide band off every side of a real void
    that happens to touch the room's edge, which is most of them.

    Erosion alone would still shave the rim where a real void meets real coverage, and
    that loss is one-directional: it always moves area from the void to a neighbour,
    understating exactly the crawlspace-facing floor this resolver exists to find. So the
    eroded core is dilated back over the void by the same tolerance — a morphological
    opening — which deletes thin artifacts outright while leaving fat voids whole.

    Nothing is discarded. A reassigned cell goes to the category of the nearest covered
    cell, because misalignment means it belongs to whatever it was mismeasured against.
    A room's floor therefore cannot lose area, however badly its walls are aligned.
    """
    void = {k for k, v in cells.items() if v is None}
    covered = {k for k, v in cells.items() if v is not None}
    core = {k for k in void
            if _nearest_within(k[0], k[1], covered, step_x, step_y, tolerance_cm) is None}
    survives = {k for k in void
                if _nearest_within(k[0], k[1], core, step_x, step_y, tolerance_cm) is not None}

    areas: dict[str, float] = {}
    for key, cat in cells.items():
        if cat is None:
            if key in survives:
                cat = void_name
            else:
                # Not in the core and not near it: an artifact. It belongs to the nearest
                # thing actually drawn, which exists — that is why it failed the core test.
                cat = cells[_nearest_within(key[0], key[1], covered,
                                            step_x, step_y, tolerance_cm)]
        areas[cat] = areas.get(cat, 0.0) + cell_ft2
    return areas, len(survives) * cell_ft2


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
    so `spaces:` in the side-car keys off something the owner can see in SH3D.

    The fallback is re-checked AFTER stripping: a whitespace-only SH3D name would
    otherwise yield "", and an empty category is wrong in two different directions —
    the above-face's `found or this_above_void` would quietly relabel it `attic`, while
    the below-face's `is not None` test would keep it as a nameless space.
    """
    return (level.name or "").strip().lower() or "space"


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
    that is the shoelace area upstream. No cell is discarded on the way here: an
    artifact void is reassigned to what it was mismeasured against, not dropped. What
    this re-absorbs is the remaining discretization drift — a raster of whole cells only
    approximates a polygon's edges — spread across the categories in proportion.
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
            below_cells: dict[tuple[int, int], str | None] = {}   # None marks a void cell
            above: dict[str, float] = {}
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
                    for neighbours, is_below in ((others_below, True),
                                                 (others_above, False)):
                        found = None
                        for cand in neighbours:
                            hit = _covering_room(cx, cy, rooms[cand.id])
                            if hit is not None:
                                # The ROOM's flag decides, not its level's: a level marked
                                # unconditioned can still hold a conditioned room.
                                found = ("interior" if hit["conditioned"]
                                         else _space_name(by_id[cand.id]))
                                break
                        if is_below:
                            if found is None and level.id == lowest_conditioned:
                                found = "ground"
                            below_cells[(ix, iy)] = found      # None => nothing drawn
                        else:
                            found = found or this_above_void
                            above[found] = above.get(found, 0.0) + cell_ft2

            below, void_ft2 = _settle_voids(below_cells, step_x, step_y, cell_ft2,
                                            tolerance_cm, this_below_void)

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
