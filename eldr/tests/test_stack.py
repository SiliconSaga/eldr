import pytest

from eldr import stack


def _lv(lid, name, elev, idx=0, height=250.0, conditioned=True):
    return stack.LevelInfo(id=lid, name=name, elevation_cm=elev, elevation_index=idx,
                           height_cm=height, conditioned=conditioned)


def test_orders_by_elevation():
    levels = [_lv("LM", "Main", 255.84), _lv("LB", "Basement", 0.0), _lv("L2", "2nd", 511.68)]
    assert [l.id for l in stack.ordered_levels(levels)] == ["LB", "LM", "L2"]


def test_ties_break_on_elevation_index():
    """Refrhus has Garage and Crawlspace both at 121.92cm, separated only by index."""
    levels = [_lv("LC", "Crawlspace", 121.92, idx=1), _lv("LG", "Garage", 121.92, idx=0)]
    assert [l.id for l in stack.ordered_levels(levels)] == ["LG", "LC"]


def test_roomless_level_is_scaffolding_when_others_have_rooms():
    """The basement-main-transition level holds joists and duct runs, not space."""
    levels = [_lv("LB", "Basement", 0.0), _lv("LT", "transition", 213.36), _lv("LM", "Main", 255.84)]
    rooms = {"LB": [{"id": "r1"}], "LM": [{"id": "r2"}]}
    assert stack.scaffolding_ids(levels, rooms) == frozenset({"LT"})


def test_no_scaffolding_when_the_model_has_no_rooms_at_all():
    """Eldr supports roomless models via the bounding-box fallback. Without this
    guard every level would be 'scaffolding' and the house would lose its floor,
    ceiling and volume entirely."""
    levels = [_lv("LM", "Main", 0.0)]
    assert stack.scaffolding_ids(levels, {}) == frozenset()


def test_level_with_only_empty_room_list_is_scaffolding():
    levels = [_lv("LM", "Main", 0.0), _lv("LT", "t", 250.0)]
    rooms = {"LM": [{"id": "r"}], "LT": []}
    assert stack.scaffolding_ids(levels, rooms) == frozenset({"LT"})


def _room(rid, lid, x0, y0, x1, y1, conditioned=True, name=None):
    from eldr import units
    return {"id": rid, "name": name or rid, "level_id": lid,
            "points": [(x0, y0), (x1, y0), (x1, y1), (x0, y1)],
            "area_ft2": units.sqcm_to_sqft((x1 - x0) * (y1 - y0)),
            "conditioned": conditioned}


def _resolve(levels, rooms, **kw):
    return stack.resolve_faces(levels, rooms, below_void="crawlspace", above_void="attic", **kw)


def test_conditioned_over_conditioned_is_interior():
    levels = [_lv("LB", "Basement", 0.0), _lv("LM", "Main", 250.0)]
    rooms = {"LB": [_room("rb", "LB", 0, 0, 400, 300)],
             "LM": [_room("rm", "LM", 0, 0, 400, 300)]}
    faces = _resolve(levels, rooms)
    assert set(faces["rm"].below) == {"interior"}
    assert set(faces["rb"].below) == {"ground"}      # lowest conditioned level -> slab


def test_lowest_level_floor_is_ground():
    levels = [_lv("LM", "Main", 0.0)]
    rooms = {"LM": [_room("rm", "LM", 0, 0, 400, 300)]}
    faces = _resolve(levels, rooms)
    assert set(faces["rm"].below) == {"ground"}


def test_vertical_overlap_without_footprint_overlap_is_not_adjacency():
    """A garage spanning the main floor's vertical range but sitting BESIDE it must
    not become the floor below. This is the Refrhus garage: 4-14ft, crossing Main's
    8.39-16.39ft, yet entirely elsewhere in plan."""
    levels = [_lv("LG", "Garage", 120.0, height=300.0, conditioned=False),
              _lv("LM", "Main", 250.0)]
    rooms = {"LG": [_room("rg", "LG", 500, 0, 900, 300, conditioned=False)],
             "LM": [_room("rm", "LM", 0, 0, 400, 300)]}
    faces = _resolve(levels, rooms)
    assert "garage" not in faces["rm"].below
    assert set(faces["rm"].below) == {"ground"}   # nothing below in plan -> it IS the lowest here


def test_area_splits_across_two_levels_below():
    """The Kitchen shape: half over the basement, half over the crawlspace."""
    levels = [_lv("LB", "Basement", 0.0), _lv("LC", "Crawl", 0.0, idx=1, conditioned=False),
              _lv("LM", "Main", 250.0)]
    rooms = {"LB": [_room("rb", "LB", 0, 0, 200, 300)],
             "LC": [_room("rc", "LC", 200, 0, 400, 300, conditioned=False)],
             "LM": [_room("rm", "LM", 0, 0, 400, 300)]}
    below = _resolve(levels, rooms)["rm"].below
    assert abs(below["interior"] - below["crawl"]) < 1.0     # halves, within a cell
    assert set(below) == {"interior", "crawl"}


def test_partial_ceiling_gets_attic_for_the_uncovered_half():
    levels = [_lv("LM", "Main", 0.0), _lv("L2", "2nd", 250.0)]
    rooms = {"LM": [_room("rm", "LM", 0, 0, 400, 300)],
             "L2": [_room("r2", "L2", 0, 0, 200, 300)]}
    above = _resolve(levels, rooms)["rm"].above
    assert set(above) == {"interior", "attic"}
    assert abs(above["interior"] - above["attic"]) < 1.0


def test_top_level_ceiling_is_all_attic():
    levels = [_lv("LM", "Main", 0.0)]
    rooms = {"LM": [_room("rm", "LM", 0, 0, 400, 300)]}
    assert set(_resolve(levels, rooms)["rm"].above) == {"attic"}


def test_void_sliver_along_the_edge_is_discarded():
    """A 10cm misalignment band around the room is wall-thickness noise, not exposure."""
    levels = [_lv("LB", "Basement", 0.0), _lv("LM", "Main", 250.0)]
    rooms = {"LB": [_room("rb", "LB", 10, 10, 390, 290)],
             "LM": [_room("rm", "LM", 0, 0, 400, 300)]}
    faces = _resolve(levels, rooms)
    assert "crawlspace" not in faces["rm"].below
    assert faces["rm"].void_below_ft2 == 0.0


def test_interior_void_blob_survives_and_becomes_buffer():
    """The Main Bed extension: undrawn crawlspace reaching well inside the room.

    The basement covers exactly half, so the split must come out near even. Eroding the
    void against the ROOM's outline instead of its own used to shave a tolerance-wide
    band off the void's three room-edge sides and hand it back proportionally, landing at
    70.55/58.62 — a one-directional bias that understates precisely the crawlspace-facing
    floor this resolver exists to find. Slack is left for the raster: an exact 50/50 is
    not owed, only an unbiased one.
    """
    levels = [_lv("LB", "Basement", 0.0), _lv("LM", "Main", 250.0)]
    rooms = {"LB": [_room("rb", "LB", 0, 0, 200, 300)],
             "LM": [_room("rm", "LM", 0, 0, 400, 300)]}
    faces = _resolve(levels, rooms)
    below = faces["rm"].below
    assert "crawlspace" in below
    assert faces["rm"].void_below_ft2 > 0.0
    assert abs(below["interior"] - below["crawlspace"]) < 3.0


def test_tiny_region_is_dropped_and_redistributed():
    """The 1.0 sqft the Refrhus garage clips under Main is an artifact, not a surface."""
    levels = [_lv("LB", "Basement", 0.0), _lv("LG", "Garage", 0.0, idx=1, conditioned=False),
              _lv("LM", "Main", 250.0)]
    rooms = {"LB": [_room("rb", "LB", 0, 0, 400, 300)],
             "LG": [_room("rg", "LG", 0, 0, 30, 30, conditioned=False)],
             "LM": [_room("rm", "LM", 0, 0, 400, 300)]}
    below = _resolve(levels, rooms)["rm"].below
    assert "garage" not in below
    assert set(below) == {"interior"}


def test_scaffolding_level_is_invisible_to_the_stack():
    """End-to-end only: this passes even without the scaffolding filter, because a
    scaffolding level is roomless by definition and a roomless level covers no cell
    anyway. What the filter actually protects is the ordering (a roomless level must not
    be the 'nearest' anything) — pinned directly by the scaffolding_ids tests above."""
    levels = [_lv("LB", "Basement", 0.0), _lv("LT", "transition", 200.0),
              _lv("LM", "Main", 250.0)]
    rooms = {"LB": [_room("rb", "LB", 0, 0, 400, 300)], "LT": [],
             "LM": [_room("rm", "LM", 0, 0, 400, 300)]}
    assert set(_resolve(levels, rooms)["rm"].below) == {"interior"}


def test_face_areas_sum_to_the_room_polygon_area():
    """Every square foot of floor and ceiling faces something. The resolver splits
    the room's area; it never re-measures it. Pins against both the eroded-band
    loss and raster discretization drift."""
    levels = [_lv("LB", "Basement", 0.0), _lv("LM", "Main", 250.0)]
    rooms = {"LB": [_room("rb", "LB", 10, 10, 190, 290)],
             "LM": [_room("rm", "LM", 0, 0, 400, 300)]}
    faces = _resolve(levels, rooms)["rm"]
    area = rooms["LM"][0]["area_ft2"]
    assert sum(faces.below.values()) == pytest.approx(area, rel=1e-9)
    assert sum(faces.above.values()) == pytest.approx(area, rel=1e-9)


# An L whose every corner lands off the raster: 437 x 283 overall with a 234 x 152 notch.
#
# `resolve_faces` divides each room's bounding box into a WHOLE number of cells, so an
# axis-aligned RECTANGLE fills its bbox exactly and its raster area equals its shoelace
# area to the last bit — 0.000% drift. That makes a rectangle useless for testing
# `_to_room_area`: normalization is a no-op on it, and replacing the function with
# `return areas` passes. Only a shape whose edges cut through cells can disagree, and
# this one is 2.65% short.
_ODD_ELL_POINTS = [(0, 0), (437, 0), (437, 131), (203, 131), (203, 283), (0, 283)]


def _odd_ell(rid="rm", lid="LM"):
    from eldr import units
    return {"id": rid, "name": rid, "level_id": lid, "points": list(_ODD_ELL_POINTS),
            "area_ft2": units.sqcm_to_sqft(437 * 131 + 203 * (283 - 131)),
            "conditioned": True}


def test_face_areas_sum_to_the_polygon_area_a_raster_cannot_reach():
    """`_to_room_area` is the branch's headline invariant, and a rectangle cannot test it.

    Companion to `test_face_areas_sum_to_the_room_polygon_area` above, which uses a
    rectangle: there the raster already lands exactly on the polygon area, so the
    normalization it claims to pin is a no-op and the assertion holds with
    `_to_room_area` replaced by `return areas`. This L cannot be rastered exactly, so the
    same assertion only holds if the scaling really runs.

    `void_below_ft2` is the proof the fixture is not another no-op: it is reported as
    MEASURED and never normalized, so with the whole floor over nothing it *is* the raw
    raster area — and it comes out 2.65% below the polygon area rather than equal to it.
    """
    room = _odd_ell()
    levels = [_lv("LB", "Basement", 0.0), _lv("LM", "Main", 250.0)]
    rooms = {"LB": [_room("rb", "LB", 1000, 0, 1400, 300)],     # beside, never beneath
             "LM": [room]}
    faces = _resolve(levels, rooms)["rm"]
    area = room["area_ft2"]
    assert abs(faces.void_below_ft2 / area - 1) > 0.02      # a rectangle drifts 0.000%
    assert sum(faces.below.values()) == pytest.approx(area, rel=1e-12)
    assert sum(faces.above.values()) == pytest.approx(area, rel=1e-12)


def test_a_split_face_is_normalized_across_its_categories_not_onto_one():
    """The same un-rasterable L, half over a basement: two categories, and the drift is
    re-absorbed in proportion rather than dumped on either one.

    A single-category fixture cannot tell `{k: v * area / total}` apart from
    `{k: area for k in areas}` — both make the sum come out right when there is only one
    k. The split does, and it is the shape every real room has.
    """
    room = _odd_ell()
    levels = [_lv("LB", "Basement", 0.0), _lv("LM", "Main", 250.0)]
    rooms = {"LB": [_room("rb", "LB", 0, 0, 203, 283)], "LM": [room]}
    faces = _resolve(levels, rooms)["rm"]
    area = room["area_ft2"]
    assert set(faces.below) == {"interior", "crawlspace"}
    assert sum(faces.below.values()) == pytest.approx(area, rel=1e-12)
    assert faces.below["interior"] == pytest.approx(61.30107, rel=1e-4)
    assert faces.below["crawlspace"] == pytest.approx(33.53221, rel=1e-4)


# A void strip along the room's east edge, `gap_cm` wide, with the basement drawn right up
# to it. At GRID_CM=15 the room's 400cm span becomes 26 columns of 15.38cm, so 30cm is two
# columns and 46cm is three — the tightest bracket the grid allows.
#
# Two columns has no cell far enough from real coverage to seed an eroded core at a 20cm
# tolerance, so the strip dissolves entirely; three columns does, so it opens back up and
# survives. Drop the tolerance to 0 and the two-column strip survives too; raise it to 45
# and the three-column strip dies. The constant is therefore held from both sides.
#
# GRID_CM is pinned by the same pair, because the two are coupled: at grid 5 the
# two-column case becomes six columns and survives, at grid 60 the three-column case
# stops resolving a void cell at all.
@pytest.mark.parametrize("gap_cm, absorbed", [(30, True), (46, False)],
                         ids=["inside-the-tolerance", "outside-it"])
def test_void_tolerance_is_pinned_from_both_sides(gap_cm, absorbed):
    """`TOLERANCE_CM` and `GRID_CM` decide how much of a real house's floor is itemized as
    a schematic gap rather than billed as load, and until now nothing held either one:
    0, 45 and a 12x grid change all passed. On Refrhus those choices move the void
    itemization from 2 rooms to 4 and the drawn-crawl area by up to 16 ft².

    Both are captured as DEFAULT ARGUMENTS of `resolve_faces`, bound at import, so
    monkeypatching the module attribute changes nothing — they have to be read off the
    module and passed explicitly for a test to bind to the shipped values at all.
    """
    levels = [_lv("LB", "Basement", 0.0), _lv("LM", "Main", 250.0)]
    rooms = {"LB": [_room("rb", "LB", 0, 0, 400 - gap_cm, 300)],
             "LM": [_room("rm", "LM", 0, 0, 400, 300)]}
    faces = _resolve(levels, rooms,
                     tolerance_cm=stack.TOLERANCE_CM, grid_cm=stack.GRID_CM)["rm"]
    if absorbed:
        assert set(faces.below) == {"interior"}
        assert faces.void_below_ft2 == 0.0
    else:
        assert set(faces.below) == {"interior", "crawlspace"}
        assert faces.void_below_ft2 > 0.0


def test_non_convex_room_excludes_its_own_notch():
    """A room is not its bounding box. This L keeps its notch clear of the only thing
    drawn below, which sits entirely inside that notch — so the L faces nothing, and a
    bounding-box test would instead hand it a quarter of a floor it does not have."""
    from eldr import units
    ell = {"id": "rm", "name": "rm", "level_id": "LM",
           "points": [(0, 0), (400, 0), (400, 150), (200, 150), (200, 300), (0, 300)],
           "area_ft2": units.sqcm_to_sqft(400 * 300 - 200 * 150), "conditioned": True}
    levels = [_lv("LB", "Basement", 0.0), _lv("LM", "Main", 250.0)]
    rooms = {"LB": [_room("rb", "LB", 200, 150, 400, 300)], "LM": [ell]}
    below = _resolve(levels, rooms)["rm"].below
    assert set(below) == {"crawlspace"}
    assert "interior" not in below


def test_nearest_level_below_wins():
    """Two levels both cover the cell; the floor faces the closer one. Elevation only
    orders the search — but the order has to run outward from the room."""
    levels = [_lv("LD", "Cellar", 0.0, conditioned=False),
              _lv("LC", "Crawl", 250.0, conditioned=False),
              _lv("LM", "Main", 500.0)]
    rooms = {"LD": [_room("rd", "LD", 0, 0, 400, 300, conditioned=False)],
             "LC": [_room("rc", "LC", 0, 0, 400, 300, conditioned=False)],
             "LM": [_room("rm", "LM", 0, 0, 400, 300)]}
    assert set(_resolve(levels, rooms)["rm"].below) == {"crawl"}


def test_nearest_level_above_wins():
    levels = [_lv("LM", "Main", 0.0),
              _lv("LL", "Loft", 250.0, conditioned=False),
              _lv("LU", "Cupola", 500.0, conditioned=False)]
    rooms = {"LM": [_room("rm", "LM", 0, 0, 400, 300)],
             "LL": [_room("rl", "LL", 0, 0, 400, 300, conditioned=False)],
             "LU": [_room("ru", "LU", 0, 0, 400, 300, conditioned=False)]}
    assert set(_resolve(levels, rooms)["rm"].above) == {"loft"}


def test_the_rooms_conditioning_decides_not_its_levels():
    """Conditioning is a property of the room found under the cell, not of the level
    holding it — a level can carry a mix, and the sidecar keys buffer spaces off the
    level name regardless."""
    levels = [_lv("LB", "Cellar", 0.0), _lv("LM", "Main", 250.0)]
    rooms = {"LB": [_room("rb", "LB", 0, 0, 400, 300, conditioned=False)],
             "LM": [_room("rm", "LM", 0, 0, 400, 300)]}
    assert set(_resolve(levels, rooms)["rm"].below) == {"cellar"}


def test_room_smaller_than_a_grid_cell_still_rasterizes():
    """A 7cm closet is degenerate for the raster but real for the model. Without the
    minimum of one cell it resolves to nothing at all and loses both faces."""
    levels = [_lv("LM", "Main", 0.0)]
    rooms = {"LM": [_room("rm", "LM", 0, 0, 7, 7)]}
    faces = _resolve(levels, rooms)["rm"]
    area = rooms["LM"][0]["area_ft2"]
    assert faces.below == pytest.approx({"ground": area})
    assert faces.above == pytest.approx({"attic": area})


def test_room_below_the_noise_threshold_keeps_its_largest_face():
    """Every category under min_region_ft2 means the ROOM is small, not that its faces
    are noise. Dropping them all would leave a real room with no floor."""
    levels = [_lv("LB", "Basement", 0.0), _lv("LC", "Crawl", 0.0, idx=1, conditioned=False),
              _lv("LM", "Main", 250.0)]
    rooms = {"LB": [_room("rb", "LB", 0, 0, 30, 40)],
             "LC": [_room("rc", "LC", 30, 0, 45, 40, conditioned=False)],
             "LM": [_room("rm", "LM", 0, 0, 45, 40)]}
    area = rooms["LM"][0]["area_ft2"]
    assert area < stack.MIN_REGION_FT2          # the whole room is below the threshold
    assert _resolve(levels, rooms)["rm"].below == pytest.approx({"interior": area})


def test_level_voids_override_both_faces():
    """One wing over open air while the rest sits over crawl."""
    levels = [_lv("LB", "Basement", 0.0), _lv("LM", "Main", 250.0)]
    rooms = {"LB": [_room("rb", "LB", 0, 0, 400, 300)],
             "LM": [_room("rm", "LM", 500, 0, 900, 300)]}      # beside, nothing above or below
    faces = _resolve(levels, rooms, level_voids={"LM": ("dirt", "vaulted")})["rm"]
    assert set(faces.below) == {"dirt"}
    assert set(faces.above) == {"vaulted"}


def test_level_voids_partial_falls_through_to_the_house_default():
    levels = [_lv("LB", "Basement", 0.0), _lv("LM", "Main", 250.0)]
    rooms = {"LB": [_room("rb", "LB", 0, 0, 400, 300)],
             "LM": [_room("rm", "LM", 500, 0, 900, 300)]}
    faces = _resolve(levels, rooms, level_voids={"LM": (None, "vaulted")})["rm"]
    assert set(faces.below) == {"crawlspace"}      # house default
    assert set(faces.above) == {"vaulted"}         # override


def test_ignore_ids_re_elects_the_lowest_conditioned_level():
    """Dropping the basement makes Main the lowest conditioned level, so its floor
    becomes slab-on-grade rather than a void over the level that is no longer there."""
    levels = [_lv("LB", "Basement", 0.0), _lv("LM", "Main", 250.0)]
    rooms = {"LB": [_room("rb", "LB", 0, 0, 400, 300)],
             "LM": [_room("rm", "LM", 0, 0, 400, 300)]}
    assert set(_resolve(levels, rooms)["rm"].below) == {"interior"}
    faces = _resolve(levels, rooms, ignore_ids=frozenset({"LB"}))
    assert set(faces) == {"rm"}
    assert set(faces["rm"].below) == {"ground"}


def test_narrow_room_over_nothing_keeps_its_whole_floor():
    """A room narrower than twice the tolerance is entirely inside its own erosion band.
    Measuring the band against the room's outline used to discard every void cell and
    leave the floor with no categories at all."""
    levels = [_lv("LB", "Basement", 0.0), _lv("LM", "Main", 250.0)]
    rooms = {"LB": [_room("rb", "LB", 0, 0, 400, 300)],
             "LM": [_room("rm", "LM", 1000, 0, 1039, 300)]}     # 39cm wide, beside it all
    faces = _resolve(levels, rooms)["rm"]
    area = rooms["LM"][0]["area_ft2"]
    assert faces.below == pytest.approx({"crawlspace": area})
    assert faces.void_below_ft2 == pytest.approx(area)


def test_unconditioned_rooms_get_no_faces():
    levels = [_lv("LG", "Garage", 0.0, conditioned=False)]
    rooms = {"LG": [_room("rg", "LG", 0, 0, 400, 300, conditioned=False)]}
    assert _resolve(levels, rooms) == {}


def test_whitespace_only_level_name_still_gets_a_space_name():
    """A blank SH3D level name must not strip down to "". The above-face resolves with
    `found or this_above_void`, so a falsy "" would silently become `attic`; the
    below-face tests `is not None`, so it would keep a nameless category instead. Both
    are wrong, and they disagree — the fallback has to survive the strip."""
    levels = [_lv("LU", "   ", 0.0, conditioned=False),
              _lv("LM", "Main", 250.0),
              _lv("LA", "\t\n", 500.0, conditioned=False)]
    rooms = {"LU": [_room("ru", "LU", 0, 0, 400, 300, conditioned=False)],
             "LM": [_room("rm", "LM", 0, 0, 400, 300)],
             "LA": [_room("ra", "LA", 0, 0, 400, 300, conditioned=False)]}
    faces = _resolve(levels, rooms)
    assert set(faces["rm"].below) == {"space"}
    assert set(faces["rm"].above) == {"space"}      # NOT "attic"
