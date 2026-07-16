"""Parse a Home.xml (read-only) into a single-zone envelope of Surfaces."""
from __future__ import annotations
from dataclasses import dataclass
import math
import xml.etree.ElementTree as ET
from eldr import units


@dataclass(frozen=True)
class Surface:
    category: str
    area_ft2: float


@dataclass(frozen=True)
class Envelope:
    surfaces: list[Surface]
    volume_ft3: float


def _f(el, attr):
    return float(el.get(attr))


def _wall_midpoint(w):
    return ((_f(w, "xStart") + _f(w, "xEnd")) / 2.0,
            (_f(w, "yStart") + _f(w, "yEnd")) / 2.0)


def _wall_length_cm(w):
    dx = _f(w, "xEnd") - _f(w, "xStart")
    dy = _f(w, "yEnd") - _f(w, "yStart")
    return (dx * dx + dy * dy) ** 0.5


def _point_seg_dist_cm(px, py, w):
    """Perpendicular distance (cm) from a point to a wall segment."""
    ax, ay = _f(w, "xStart"), _f(w, "yStart")
    bx, by = _f(w, "xEnd"), _f(w, "yEnd")
    dx, dy = bx - ax, by - ay
    seg2 = dx * dx + dy * dy
    if seg2 == 0.0:
        return ((px - ax) ** 2 + (py - ay) ** 2) ** 0.5
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg2))
    cx, cy = ax + t * dx, ay + t * dy
    return ((px - cx) ** 2 + (py - cy) ** 2) ** 0.5


def _projects_within_segment(px, py, w, margin=0.05):
    """True if the point's foot falls within the wall segment's span (overlap)."""
    ax, ay = _f(w, "xStart"), _f(w, "yStart")
    bx, by = _f(w, "xEnd"), _f(w, "yEnd")
    dx, dy = bx - ax, by - ay
    seg2 = dx * dx + dy * dy
    if seg2 == 0.0:
        return True
    t = ((px - ax) * dx + (py - ay) * dy) / seg2
    return -margin <= t <= 1.0 + margin


def _aligned_with_wall(opening_angle, w, tol=0.26):
    """True if the opening's angle is parallel to the wall direction (mod pi)."""
    wall_angle = math.atan2(_f(w, "yEnd") - _f(w, "yStart"),
                            _f(w, "xEnd") - _f(w, "xStart"))
    return abs(math.sin(opening_angle - wall_angle)) <= math.sin(tol)


def extract_envelope(home_xml_path: str) -> Envelope:
    root = ET.parse(home_xml_path).getroot()

    levels = {lv.get("id"): lv for lv in root.findall("level")}
    walls_by_level: dict[str, list] = {}
    for w in root.findall("wall"):
        walls_by_level.setdefault(w.get("level"), []).append(w)

    surfaces: list[Surface] = []
    # Track net exterior wall area per (level, category) so we can subtract openings.
    wall_area_cm2: dict[str, float] = {}      # key: wall id -> net gross area (cm^2)
    wall_category: dict[str, str] = {}         # wall id -> category
    level_extent: dict[str, tuple] = {}        # level -> (minx,maxx,miny,maxy)
    volume_ft3 = 0.0

    for level_id, walls in walls_by_level.items():
        xs = [x for w in walls for x in (_f(w, "xStart"), _f(w, "xEnd"))]
        ys = [y for w in walls for y in (_f(w, "yStart"), _f(w, "yEnd"))]
        minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
        level_extent[level_id] = (minx, maxx, miny, maxy)
        lv = levels[level_id]
        is_basement = (lv.get("name") or "").lower().startswith("basement")
        for w in walls:
            mx, my = _wall_midpoint(w)
            exterior = (abs(mx - minx) < 1.0 or abs(mx - maxx) < 1.0
                        or abs(my - miny) < 1.0 or abs(my - maxy) < 1.0)
            if not exterior:
                continue
            area = _wall_length_cm(w) * _f(w, "height")
            wall_area_cm2[w.get("id")] = area
            wall_category[w.get("id")] = "basement_wall" if is_basement else "exterior_wall"
        # volume from footprint x height
        footprint = (maxx - minx) * (maxy - miny)
        volume_ft3 += (units.cm_to_ft(maxx - minx) * units.cm_to_ft(maxy - miny)
                       * units.cm_to_ft(_f(lv, "height")))

    # An opening belongs to the envelope only if it sits on EXACTLY ONE exterior/
    # basement wall: within half-thickness + tolerance of the segment, projecting
    # within the segment's span, AND aligned with the wall's direction. Zero matches
    # = interior opening; >1 = ambiguous. Either way reject rather than guess a facade.
    # (SH3D stores no explicit host-wall reference, so this geometric match is the
    # best available; an interior wall parallel-and-adjacent to an exterior wall is
    # the residual case the re-measure/scan pass resolves.)
    OPENING_TOL_CM = 20.0

    def host_envelope_wall(dw):
        lid = dw.get("level")
        dx, dy = _f(dw, "x"), _f(dw, "y")
        ang = float(dw.get("angle", "0") or "0")
        hosts = []
        for w in walls_by_level.get(lid, []):
            wid = w.get("id")
            if wid not in wall_area_cm2:            # only envelope walls are candidates
                continue
            if _point_seg_dist_cm(dx, dy, w) > _f(w, "thickness") / 2.0 + OPENING_TOL_CM:
                continue                            # not on this wall's plane
            if not _projects_within_segment(dx, dy, w):
                continue                            # foot lies beyond the wall's span
            if not _aligned_with_wall(ang, w):
                continue                            # opening not parallel to the wall
            hosts.append(wid)
        # unique host -> assign; zero (interior) or >1 (ambiguous) -> reject
        return hosts[0] if len(hosts) == 1 else None

    for dw in root.findall("doorOrWindow"):
        host = host_envelope_wall(dw)
        if host is None:
            continue                                # interior opening -> not an envelope surface
        area_cm2 = _f(dw, "width") * _f(dw, "height")
        label = (dw.get("catalogId", "") + " " + (dw.get("name") or "")).lower()
        category = "window" if "window" in label else "door"
        surfaces.append(Surface(category, units.sqcm_to_sqft(area_cm2)))
        wall_area_cm2[host] = max(0.0, wall_area_cm2[host] - area_cm2)

    for wid, area_cm2 in wall_area_cm2.items():
        surfaces.append(Surface(wall_category[wid], units.sqcm_to_sqft(area_cm2)))

    # Ceiling on the highest level, floor on the lowest (by elevation).
    def level_elev(lid):
        return float(levels[lid].get("elevation"))

    if level_extent:
        levels_present = list(level_extent.keys())
        top = max(levels_present, key=level_elev)
        bot = min(levels_present, key=level_elev)
        for lid, cat in ((top, "ceiling"), (bot, "floor")):
            minx, maxx, miny, maxy = level_extent[lid]
            surfaces.append(Surface(cat, units.sqcm_to_sqft((maxx - minx) * (maxy - miny))))

    return Envelope(surfaces=surfaces, volume_ft3=volume_ft3)
