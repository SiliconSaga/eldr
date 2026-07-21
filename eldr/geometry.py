"""Parse a Home.xml (read-only) into a single-zone envelope of Surfaces.

Accepts either an exploded `Home.xml` or a packed `.sh3d` (a ZIP whose
`Home.xml` entry is authoritative) — so the CLI can point straight at a file
saved from Sweet Home 3D with no unpack step.
"""
from __future__ import annotations
from dataclasses import dataclass, field
import math
import os
import zipfile
from xml.etree.ElementTree import Element
import defusedxml.ElementTree as DET
from eldr import units

# Cap the parsed Home.xml — a .sh3d/Home.xml can be third-party input, so bound it
# (with defusedxml) against zip-bomb / billion-laughs style attacks.
MAX_HOME_XML_BYTES = 64 * 1024 * 1024


def _read_home_root(path: str) -> Element:
    """Return the <home> XML root from an exploded Home.xml or a packed .sh3d (ZIP).

    Hardened: defusedxml parser + a size cap, since the model can be third-party input.
    """
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            try:
                info = z.getinfo("Home.xml")
            except KeyError:
                raise ValueError(
                    f"{path} is a ZIP but has no Home.xml — not a Sweet Home 3D .sh3d") from None
            if info.file_size > MAX_HOME_XML_BYTES:
                raise ValueError(f"Home.xml in {path} is too large ({info.file_size} bytes)")
            with z.open("Home.xml") as f:
                return DET.parse(f).getroot()
    if os.path.getsize(path) > MAX_HOME_XML_BYTES:
        raise ValueError(f"{path} is too large ({os.path.getsize(path)} bytes)")
    return DET.parse(path).getroot()


@dataclass(frozen=True)
class Surface:
    category: str
    area_ft2: float


@dataclass(frozen=True)
class Envelope:
    surfaces: list[Surface]
    volume_ft3: float
    # window area (ft^2) keyed by true compass bearing in whole degrees (0=N, 90=E,
    # clockwise). Continuous — solar gain reads the exact bearing, not a bucket.
    # Reflects the home's compass northDirection (0 by default until set from a survey).
    windows_by_bearing: dict[int, float] = field(default_factory=dict)
    # decimal degrees from the home's compass (N positive, E positive); None if absent.
    latitude: float | None = None
    longitude: float | None = None


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


def _window_bearing(w, cx, cy, north_dir):
    """True compass bearing (degrees, 0=N clockwise) a window in wall `w` faces.

    Its outward normal is the wall normal pointing away from the level centroid
    (cx, cy). Plan Y is down, so plan-north is -Y and east is +X; the home's
    compass `north_dir` (radians, clockwise from -Y) rotates that to true north.
    """
    ax, ay = _f(w, "xStart"), _f(w, "yStart")
    bx, by = _f(w, "xEnd"), _f(w, "yEnd")
    dx, dy = bx - ax, by - ay
    n1, n2 = (-dy, dx), (dy, -dx)                 # the two wall normals
    ox, oy = (ax + bx) / 2 - cx, (ay + by) / 2 - cy   # centroid -> wall (outward-ish)
    nx, ny = n1 if (n1[0] * ox + n1[1] * oy) > 0 else n2
    plan_bearing = math.atan2(nx, -ny)            # clockwise from plan-north (-Y)
    return math.degrees(plan_bearing - north_dir) % 360.0


def extract_envelope(home_path: str) -> Envelope:
    """Parse an exploded Home.xml or a packed .sh3d into a single-zone Envelope."""
    root = _read_home_root(home_path)

    compass = root.find("compass")
    north_dir = float(compass.get("northDirection", "0") or "0") if compass is not None else 0.0
    # SH3D stores compass latitude/longitude in radians; expose them in degrees.
    latitude = longitude = None
    if compass is not None:
        lat_rad, lon_rad = compass.get("latitude"), compass.get("longitude")
        latitude = math.degrees(float(lat_rad)) if lat_rad is not None else None
        longitude = math.degrees(float(lon_rad)) if lon_rad is not None else None

    levels = {lv.get("id"): lv for lv in root.findall("level")}
    walls_by_level: dict[str, list] = {}
    for w in root.findall("wall"):
        walls_by_level.setdefault(w.get("level"), []).append(w)
    wall_by_id = {w.get("id"): w for ws in walls_by_level.values() for w in ws}

    surfaces: list[Surface] = []
    windows_by_bearing: dict[int, float] = {}
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
        area_ft2 = units.sqcm_to_sqft(area_cm2)
        label = (dw.get("catalogId", "") + " " + (dw.get("name") or "")).lower()
        category = "window" if "window" in label else "door"
        surfaces.append(Surface(category, area_ft2))
        wall_area_cm2[host] = max(0.0, wall_area_cm2[host] - area_cm2)
        if category == "window":
            minx, maxx, miny, maxy = level_extent[dw.get("level")]
            bearing = _window_bearing(wall_by_id[host], (minx + maxx) / 2, (miny + maxy) / 2, north_dir)
            key = round(bearing) % 360
            windows_by_bearing[key] = windows_by_bearing.get(key, 0.0) + area_ft2

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

    return Envelope(surfaces=surfaces, volume_ft3=volume_ft3,
                    windows_by_bearing=windows_by_bearing,
                    latitude=latitude, longitude=longitude)
