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
