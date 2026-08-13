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
    """The Main Bed extension: undrawn crawlspace reaching well inside the room."""
    levels = [_lv("LB", "Basement", 0.0), _lv("LM", "Main", 250.0)]
    rooms = {"LB": [_room("rb", "LB", 0, 0, 200, 300)],
             "LM": [_room("rm", "LM", 0, 0, 400, 300)]}
    faces = _resolve(levels, rooms)
    assert "crawlspace" in faces["rm"].below
    assert faces["rm"].void_below_ft2 > 0.0


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
    levels = [_lv("LB", "Basement", 0.0), _lv("LT", "transition", 200.0),
              _lv("LM", "Main", 250.0)]
    rooms = {"LB": [_room("rb", "LB", 0, 0, 400, 300)], "LT": [],
             "LM": [_room("rm", "LM", 0, 0, 400, 300)]}
    assert set(_resolve(levels, rooms)["rm"].below) == {"interior"}


def test_unconditioned_rooms_get_no_faces():
    levels = [_lv("LG", "Garage", 0.0, conditioned=False)]
    rooms = {"LG": [_room("rg", "LG", 0, 0, 400, 300, conditioned=False)]}
    assert _resolve(levels, rooms) == {}
