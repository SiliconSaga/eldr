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
class Furniture:
    """A placed furniture item — enough to locate an air-handler unit (plan cm)."""
    name: str
    x_cm: float
    y_cm: float
    level_id: str


@dataclass(frozen=True)
class Room:
    """A per-room sub-envelope for Manual J 1c (load-based per-room CFM)."""
    name: str
    level_id: str
    area_ft2: float
    centroid_cm: tuple[float, float]              # (x, y) in plan cm
    conditioned: bool                             # False on garage/crawlspace levels
    surfaces: list[Surface] = field(default_factory=list)      # its attributed envelope
    volume_ft3: float = 0.0
    windows_by_bearing: dict[float, float] = field(default_factory=dict)


@dataclass(frozen=True)
class Envelope:
    surfaces: list[Surface]
    volume_ft3: float
    # window area (ft^2) keyed by exact true compass bearing in degrees (0=N, 90=E,
    # clockwise). Continuous — solar gain reads the exact bearing, not a bucket;
    # windows on one wall share the identical computed bearing so they group cleanly.
    # Reflects the home's compass northDirection (0 by default until set from a survey).
    windows_by_bearing: dict[float, float] = field(default_factory=dict)
    # decimal degrees from the home's compass (N positive, E positive); None if absent.
    latitude: float | None = None
    longitude: float | None = None
    # per-room sub-envelopes (empty if the model has no rooms) — for Manual J 1c.
    rooms: list[Room] = field(default_factory=list)
    # placed furniture (to locate an air-handler unit) and per-level base elevation (cm).
    furniture: list[Furniture] = field(default_factory=list)
    level_elevations: dict[str, float] = field(default_factory=dict)


def _f(el, attr):
    return float(el.get(attr))


def _polygon_area_centroid(points):
    """Shoelace area (cm^2, absolute) + centroid (cm) of a polygon [(x, y), ...]."""
    a2 = cx = cy = 0.0
    n = len(points)
    for i in range(n):
        x0, y0 = points[i]
        x1, y1 = points[(i + 1) % n]
        cross = x0 * y1 - x1 * y0
        a2 += cross
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
    if a2 == 0.0:                                  # degenerate -> vertex average
        return 0.0, (sum(p[0] for p in points) / n, sum(p[1] for p in points) / n)
    return abs(a2 / 2.0), (cx / (3.0 * a2), cy / (3.0 * a2))


def _dist_point_to_polygon_cm(px, py, points):
    """Min distance (cm) from a point to any edge of a polygon [(x, y), ...]."""
    best = float("inf")
    n = len(points)
    for i in range(n):
        ax, ay = points[i]
        bx, by = points[(i + 1) % n]
        dx, dy = bx - ax, by - ay
        seg2 = dx * dx + dy * dy
        if seg2 == 0.0:
            d = ((px - ax) ** 2 + (py - ay) ** 2) ** 0.5
        else:
            t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg2))
            d = ((px - (ax + t * dx)) ** 2 + (py - (ay + t * dy)) ** 2) ** 0.5
        best = min(best, d)
    return best


# Spacing (cm) between sample points when splitting a wall's area among the rooms it
# runs behind. Density-based rather than a fixed count, so a narrow room along a long
# facade still gets sampled (bounded miss). Clamped to at least _WALL_MIN_SAMPLES.
_WALL_SAMPLE_SPACING_CM = 25.0
_WALL_MIN_SAMPLES = 7


def _wall_samples(w):
    """Number of split-samples for a wall — one per ~25 cm, at least a floor count."""
    return max(_WALL_MIN_SAMPLES, math.ceil(_wall_length_cm(w) / _WALL_SAMPLE_SPACING_CM))


def _sample_segment(w, k):
    """k points at the centers of k equal pieces of a wall (no endpoints)."""
    ax, ay = _f(w, "xStart"), _f(w, "yStart")
    bx, by = _f(w, "xEnd"), _f(w, "yEnd")
    return [(ax + (bx - ax) * (i + 0.5) / k, ay + (by - ay) * (i + 0.5) / k)
            for i in range(k)]


def _point_in_polygon(px, py, points):
    """Ray-cast test: is point (px, py) inside polygon [(x, y), ...]?"""
    inside = False
    n = len(points)
    j = n - 1
    for i in range(n):
        xi, yi = points[i]
        xj, yj = points[j]
        if (yi > py) != (yj > py):
            x_cross = xi + (xj - xi) * (py - yi) / (yj - yi)
            if px < x_cross:
                inside = not inside
        j = i
    return inside


# How far (cm) past a wall face to probe for a room on that side, when classifying a
# wall as envelope vs partition — clears the wall's half-thickness plus a margin.
_SIDE_PROBE_MARGIN_CM = 15.0


def _conditioned_room_on_sides(w, rooms):
    """(left, right): does a *conditioned* room sit on each side of wall `w`?

    Probes a point just past each wall face (offset along the wall normal) and tests
    it against the conditioned room polygons. A wall with a conditioned room on exactly
    one side is on the thermal envelope; conditioned on both = interior partition;
    neither (e.g. a garage/crawlspace wall) is not part of the conditioned envelope.
    """
    ax, ay = _f(w, "xStart"), _f(w, "yStart")
    bx, by = _f(w, "xEnd"), _f(w, "yEnd")
    dx, dy = bx - ax, by - ay
    length = (dx * dx + dy * dy) ** 0.5
    if length == 0.0:
        return False, False
    nx, ny = -dy / length, dx / length            # unit normal
    mx, my = (ax + bx) / 2.0, (ay + by) / 2.0
    off = _f(w, "thickness") / 2.0 + _SIDE_PROBE_MARGIN_CM
    p_left = (mx + nx * off, my + ny * off)
    p_right = (mx - nx * off, my - ny * off)
    cond = [r for r in rooms if r["conditioned"]]
    left_in = any(_point_in_polygon(p_left[0], p_left[1], r["points"]) for r in cond)
    right_in = any(_point_in_polygon(p_right[0], p_right[1], r["points"]) for r in cond)
    return left_in, right_in


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


def _unconditioned_level(name):
    """A level whose rooms carry no supply air (garage / crawlspace)."""
    n = (name or "").lower()
    return n.startswith("garage") or n.startswith("crawlspace")


def _parse_rooms(root, levels):
    """Parse <room> polygons into raw room records grouped by level.

    Returns {level_id: [ {id, name, level_id, points, area_ft2, centroid_cm,
    conditioned}, ... ]}. Rooms with fewer than 3 points are skipped (degenerate).
    """
    by_level: dict[str, list] = {}
    for r in root.findall("room"):
        pts = [(_f(p, "x"), _f(p, "y")) for p in r.findall("point")]
        if len(pts) < 3:
            continue
        lid = r.get("level")
        area_cm2, centroid = _polygon_area_centroid(pts)
        if area_cm2 <= 0.0:                    # collinear / repeated points -> degenerate
            continue
        lv = levels.get(lid)
        by_level.setdefault(lid, []).append({
            "id": r.get("id"),
            "name": r.get("name") or "(unnamed)",
            "level_id": lid,
            "points": pts,
            "area_ft2": units.sqcm_to_sqft(area_cm2),
            "centroid_cm": centroid,
            "conditioned": not _unconditioned_level(lv.get("name") if lv is not None else None),
        })
    return by_level


def extract_envelope(home_path: str) -> Envelope:
    """Parse an exploded Home.xml or a packed .sh3d into a single-zone Envelope."""
    root = _read_home_root(home_path)

    compass = root.find("compass")
    north_dir = float(compass.get("northDirection", "0") or "0") if compass is not None else 0.0
    # SH3D stores compass latitude/longitude in radians; expose them in degrees.
    latitude = longitude = None
    if compass is not None:
        # `or None` treats empty-string attrs as absent, matching northDirection above.
        lat_rad, lon_rad = compass.get("latitude") or None, compass.get("longitude") or None
        latitude = math.degrees(float(lat_rad)) if lat_rad is not None else None
        longitude = math.degrees(float(lon_rad)) if lon_rad is not None else None
        if latitude is not None and (not math.isfinite(latitude) or not -90 <= latitude <= 90):
            raise ValueError("compass latitude must be finite and between -90 and 90")
        if longitude is not None and (not math.isfinite(longitude) or not -180 <= longitude <= 180):
            raise ValueError("compass longitude must be finite and between -180 and 180")

    levels = {lv.get("id"): lv for lv in root.findall("level")}
    walls_by_level: dict[str, list] = {}
    for w in root.findall("wall"):
        walls_by_level.setdefault(w.get("level"), []).append(w)
    wall_by_id = {w.get("id"): w for ws in walls_by_level.values() for w in ws}

    rooms_by_level = _parse_rooms(root, levels)
    room_by_id = {rm["id"]: rm for lst in rooms_by_level.values() for rm in lst}
    room_gross_wall: dict[str, dict[str, float]] = {rid: {} for rid in room_by_id}   # ft^2 by cat
    room_openings: dict[str, float] = {rid: 0.0 for rid in room_by_id}               # ft^2 total
    room_doors: dict[str, float] = {rid: 0.0 for rid in room_by_id}                  # ft^2
    room_windows: dict[str, dict[float, float]] = {rid: {} for rid in room_by_id}    # ft^2 by bearing

    surfaces: list[Surface] = []
    windows_by_bearing: dict[float, float] = {}
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
        rooms_here = rooms_by_level.get(level_id, [])
        conditioned_here = [r for r in rooms_here if r["conditioned"]]
        for w in walls:
            # A wall is on the thermal envelope if a *conditioned* room sits on exactly
            # one side of it (conditioned inside, outdoors out). Following the room-
            # polygon outline catches perimeter walls on an extension/wing that sit
            # inside the level's bounding rectangle, and excludes walls of unconditioned
            # space (garage/crawlspace) entirely. Levels with no rooms fall back to the
            # bounding-box edge test.
            if rooms_here:
                left_in, right_in = _conditioned_room_on_sides(w, rooms_here)
                exterior = left_in != right_in
            else:
                mx, my = _wall_midpoint(w)
                exterior = (abs(mx - minx) < 1.0 or abs(mx - maxx) < 1.0
                            or abs(my - miny) < 1.0 or abs(my - maxy) < 1.0)
            if not exterior:
                continue
            cat = "basement_wall" if is_basement else "exterior_wall"
            area = _wall_length_cm(w) * _f(w, "height")
            wall_area_cm2[w.get("id")] = area
            wall_category[w.get("id")] = cat
            # Split the wall's gross area among the conditioned rooms it runs behind:
            # sample along it, assign each point to the nearest conditioned room. A
            # facade shared by several rooms is divided; a corner-to-corner wall lands
            # wholly in one.
            if conditioned_here:
                k = _wall_samples(w)
                share = units.sqcm_to_sqft(area) / k
                for sx, sy in _sample_segment(w, k):
                    rid = min(conditioned_here,
                              key=lambda r: _dist_point_to_polygon_cm(sx, sy, r["points"]))["id"]
                    g = room_gross_wall[rid]
                    g[cat] = g.get(cat, 0.0) + share
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
        # Attribute the opening to the nearest room on its level, by its own position.
        rooms_here = rooms_by_level.get(dw.get("level"), [])
        rid = None
        if rooms_here:
            dx, dy = _f(dw, "x"), _f(dw, "y")
            rid = min(rooms_here,
                      key=lambda r: _dist_point_to_polygon_cm(dx, dy, r["points"]))["id"]
            room_openings[rid] += area_ft2
            if category == "door":
                room_doors[rid] += area_ft2
        if category == "window":
            minx, maxx, miny, maxy = level_extent[dw.get("level")]
            key = _window_bearing(wall_by_id[host], (minx + maxx) / 2, (miny + maxy) / 2, north_dir)
            windows_by_bearing[key] = windows_by_bearing.get(key, 0.0) + area_ft2
            if rid is not None:
                room_windows[rid][key] = room_windows[rid].get(key, 0.0) + area_ft2

    for wid, area_cm2 in wall_area_cm2.items():
        surfaces.append(Surface(wall_category[wid], units.sqcm_to_sqft(area_cm2)))

    # Ceiling on the highest level, floor on the lowest (by elevation).
    def level_elev(lid):
        return float(levels[lid].get("elevation"))

    top = bot = None
    if level_extent:
        levels_present = list(level_extent.keys())
        top = max(levels_present, key=level_elev)
        bot = min(levels_present, key=level_elev)
        for lid, cat in ((top, "ceiling"), (bot, "floor")):
            minx, maxx, miny, maxy = level_extent[lid]
            surfaces.append(Surface(cat, units.sqcm_to_sqft((maxx - minx) * (maxy - miny))))

    # Assemble each room's sub-envelope. Net wall = its gross wall share minus its own
    # openings; ceiling/floor mirror the whole-house top-ceiling / bottom-floor model.
    rooms: list[Room] = []
    for rid, rm in room_by_id.items():
        lid = rm["level_id"]
        surfs: list[Surface] = []
        openings = room_openings[rid]
        for scat, gross in room_gross_wall[rid].items():
            net = max(0.0, gross - openings)
            openings = max(0.0, openings - gross)   # spill leftover to the next category
            if net > 0.0:
                surfs.append(Surface(scat, net))
        wtot = sum(room_windows[rid].values())
        if wtot > 0.0:
            surfs.append(Surface("window", wtot))
        if room_doors[rid] > 0.0:
            surfs.append(Surface("door", room_doors[rid]))
        if lid == top:
            surfs.append(Surface("ceiling", rm["area_ft2"]))
        if lid == bot:
            surfs.append(Surface("floor", rm["area_ft2"]))
        lv = levels.get(lid)
        height_ft = units.cm_to_ft(_f(lv, "height")) if lv is not None else 0.0
        rooms.append(Room(
            name=rm["name"], level_id=lid, area_ft2=rm["area_ft2"],
            centroid_cm=rm["centroid_cm"], conditioned=rm["conditioned"],
            surfaces=surfs, volume_ft3=rm["area_ft2"] * height_ft,
            windows_by_bearing=room_windows[rid]))

    furniture = [
        Furniture(name=f.get("name") or "", x_cm=_f(f, "x"), y_cm=_f(f, "y"),
                  level_id=f.get("level"))
        # iter (not findall) so furniture nested in a <furnitureGroup> is included —
        # SH3D gives grouped pieces their own absolute x/y/level.
        for f in root.iter("pieceOfFurniture")
        if f.get("x") is not None and f.get("y") is not None
    ]
    level_elevations = {lid: float(lv.get("elevation") or 0.0) for lid, lv in levels.items()}

    return Envelope(surfaces=surfaces, volume_ft3=volume_ft3,
                    windows_by_bearing=windows_by_bearing,
                    latitude=latitude, longitude=longitude, rooms=rooms,
                    furniture=furniture, level_elevations=level_elevations)
