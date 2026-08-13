import textwrap
import warnings
import zipfile
import pytest
from eldr import geometry
from eldr.tests.fixtures import MULTI_LEVEL_FIXTURE


def _fixture_with_compass(north_dir):
    # Same one-room box, plus a compass so orientation exercises the rotation.
    return FIXTURE.replace(
        "<home version='7400' name='t' wallHeight='300'>",
        f"<home version='7400' name='t' wallHeight='300'>\n"
        f"  <compass x='0' y='0' diameter='100' northDirection='{north_dir}'/>",
    )

# A one-level box: 4 exterior walls (1000cm x 500cm room, height 300cm) with one
# 100cm x 100cm window on the south wall; an interior partition wall + door; and a
# second interior door placed near an exterior wall but oriented across it. Only the
# window counts toward the envelope -- both interior doors are rejected.
FIXTURE = textwrap.dedent("""\
<?xml version='1.0'?>
<home version='7400' name='t' wallHeight='300'>
  <level id='L1' name='Main' elevation='0.0' floorThickness='12.0' height='300' elevationIndex='0'/>
  <wall id='w-n' level='L1' xStart='0' yStart='0' xEnd='1000' yEnd='0' height='300' thickness='10'/>
  <wall id='w-s' level='L1' xStart='0' yStart='500' xEnd='1000' yEnd='500' height='300' thickness='10'/>
  <wall id='w-w' level='L1' xStart='0' yStart='0' xEnd='0' yEnd='500' height='300' thickness='10'/>
  <wall id='w-e' level='L1' xStart='1000' yStart='0' xEnd='1000' yEnd='500' height='300' thickness='10'/>
  <wall id='w-int' level='L1' xStart='500' yStart='0' xEnd='500' yEnd='500' height='300' thickness='10'/>
  <doorOrWindow id='win1' level='L1' catalogId='eTeks#window' name='Window' x='500' y='500' width='100' height='100'/>
  <doorOrWindow id='door-int' level='L1' catalogId='eTeks#doorFrame' name='Door frame' x='500' y='250' width='90' height='200'/>
  <doorOrWindow id='perp-int' level='L1' catalogId='eTeks#doorFrame' name='Door frame' x='50' y='20' angle='1.5707964' width='80' height='200'/>
</home>
""")


def _by_cat(env):
    out = {}
    for s in env.surfaces:
        out[s.category] = out.get(s.category, 0.0) + s.area_ft2
    return out


def test_extract_envelope_areas(tmp_path):
    p = tmp_path / "Home.xml"
    p.write_text(FIXTURE)
    env = geometry.extract_envelope(str(p))
    cats = _by_cat(env)
    from eldr import units
    # 4 exterior walls: two 1000x300 + two 500x300 = (2*300000 + 2*150000) cm^2 gross
    gross_wall = units.sqcm_to_sqft(2 * 1000 * 300 + 2 * 500 * 300)
    window = units.sqcm_to_sqft(100 * 100)
    assert abs(cats["window"] - window) < 1e-6
    # exterior wall area is net of the window
    assert abs(cats["exterior_wall"] - (gross_wall - window)) < 1e-6
    # ceiling & floor each = footprint 1000x500
    foot = units.sqcm_to_sqft(1000 * 500)
    assert abs(cats["ceiling"] - foot) < 1e-6
    assert abs(cats["floor"] - foot) < 1e-6
    # volume = 1000 x 500 x 300 cm^3 -> ft^3
    assert abs(env.volume_ft3 - (units.cm_to_ft(1000) * units.cm_to_ft(500) * units.cm_to_ft(300))) < 1e-6
    # the interior door is ignored (no envelope door surface, exterior walls unchanged)
    assert "door" not in cats


def test_window_bearing_south(tmp_path):
    # The window sits on the y=500 wall (bottom of plan). Plan Y is down = south,
    # so with the default compass (northDirection 0) it faces bearing 180 (South).
    p = tmp_path / "Home.xml"
    p.write_text(FIXTURE)
    env = geometry.extract_envelope(str(p))
    from eldr import units
    (bearing, area), = env.windows_by_bearing.items()   # exactly one wall has glass
    assert abs(bearing - 180) < 1e-6
    assert abs(area - units.sqcm_to_sqft(100 * 100)) < 1e-6


def test_window_bearing_rotated_compass(tmp_path):
    # northDirection = pi/2 rotates true-north 90deg clockwise, so the bottom
    # (plan-south) wall's window faces bearing 90 (East). Guards the rotation sign.
    p = tmp_path / "Home.xml"
    p.write_text(_fixture_with_compass("1.57079632679"))
    env = geometry.extract_envelope(str(p))
    keys = list(env.windows_by_bearing)
    assert len(keys) == 1
    assert abs(keys[0] - 90) < 1e-6


def test_compass_latlong_to_degrees(tmp_path):
    # SH3D stores lat/long in radians; eldr exposes degrees.
    p = tmp_path / "Home.xml"
    p.write_text(FIXTURE.replace(
        "<home version='7400' name='t' wallHeight='300'>",
        "<home version='7400' name='t' wallHeight='300'>\n"
        "  <compass x='0' y='0' diameter='100' latitude='0.7105963' longitude='-1.2916551'/>",
    ))
    env = geometry.extract_envelope(str(p))
    assert env.latitude is not None and abs(env.latitude - 40.71) < 0.1
    assert env.longitude is not None and abs(env.longitude - -74.0) < 0.1


@pytest.mark.parametrize("coords", [
    "latitude='3.0' longitude='0'",     # lat ~172deg -> out of range
    "latitude='0' longitude='4.0'",     # lon ~229deg -> out of range
    "latitude='nan' longitude='0'",     # non-finite latitude
    "latitude='0' longitude='inf'",     # non-finite longitude
])
def test_invalid_compass_coords_rejected(tmp_path, coords):
    p = tmp_path / "Home.xml"
    p.write_text(FIXTURE.replace(
        "<home version='7400' name='t' wallHeight='300'>",
        "<home version='7400' name='t' wallHeight='300'>\n"
        f"  <compass x='0' y='0' diameter='100' {coords}/>",
    ))
    with pytest.raises(ValueError, match="itude"):   # latitude or longitude
        geometry.extract_envelope(str(p))


def test_empty_compass_coords_treated_as_absent(tmp_path):
    # empty lat/long attrs are treated as absent (like northDirection), not an error
    p = tmp_path / "Home.xml"
    p.write_text(FIXTURE.replace(
        "<home version='7400' name='t' wallHeight='300'>",
        "<home version='7400' name='t' wallHeight='300'>\n"
        "  <compass x='0' y='0' diameter='100' latitude='' longitude=''/>",
    ))
    env = geometry.extract_envelope(str(p))   # no crash
    assert env.latitude is None
    assert env.longitude is None


def test_extract_envelope_from_sh3d(tmp_path):
    # A .sh3d is a ZIP whose Home.xml is authoritative — eldr reads it directly.
    sh3d = tmp_path / "House.sh3d"
    with zipfile.ZipFile(sh3d, "w") as z:
        z.writestr("Home.xml", FIXTURE)
    env = geometry.extract_envelope(str(sh3d))
    from eldr import units
    cats = _by_cat(env)
    assert abs(cats["window"] - units.sqcm_to_sqft(100 * 100)) < 1e-6
    assert abs(env.volume_ft3 - (units.cm_to_ft(1000) * units.cm_to_ft(500) * units.cm_to_ft(300))) < 1e-6


def test_zip_without_home_xml_errors(tmp_path):
    # is_zipfile() is true for many non-.sh3d zips — give a clear error.
    z = tmp_path / "notahouse.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("other.txt", "nope")
    with pytest.raises(ValueError, match=r"Home\.xml"):
        geometry.extract_envelope(str(z))


# Two conditioned rooms (West/East) sharing a north facade on the top level "Main",
# plus one unconditioned "Gar" room on the lower "Garage" level, a west-wall window,
# and a placed air handler. Exercises room parsing, wall-area splitting, per-room
# ceiling/floor, the conditioned flag, furniture, and level elevations.
ROOM_FIXTURE = textwrap.dedent("""\
<?xml version='1.0'?>
<home version='7400' name='t' wallHeight='300'>
  <level id='L1' name='Main' elevation='100.0' floorThickness='12.0' height='300' elevationIndex='1'/>
  <level id='LG' name='Garage' elevation='0.0' floorThickness='12.0' height='300' elevationIndex='0'/>
  <wall id='w-n' level='L1' xStart='0' yStart='0' xEnd='1000' yEnd='0' height='300' thickness='10'/>
  <wall id='w-s' level='L1' xStart='0' yStart='500' xEnd='1000' yEnd='500' height='300' thickness='10'/>
  <wall id='w-w' level='L1' xStart='0' yStart='0' xEnd='0' yEnd='500' height='300' thickness='10'/>
  <wall id='w-e' level='L1' xStart='1000' yStart='0' xEnd='1000' yEnd='500' height='300' thickness='10'/>
  <wall id='w-int' level='L1' xStart='500' yStart='0' xEnd='500' yEnd='500' height='300' thickness='10'/>
  <wall id='g-n' level='LG' xStart='0' yStart='0' xEnd='600' yEnd='0' height='300' thickness='10'/>
  <wall id='g-s' level='LG' xStart='0' yStart='600' xEnd='600' yEnd='600' height='300' thickness='10'/>
  <wall id='g-w' level='LG' xStart='0' yStart='0' xEnd='0' yEnd='600' height='300' thickness='10'/>
  <wall id='g-e' level='LG' xStart='600' yStart='0' xEnd='600' yEnd='600' height='300' thickness='10'/>
  <doorOrWindow id='win1' level='L1' catalogId='eTeks#window' name='Window' x='0' y='250' angle='1.5707964' width='100' height='100'/>
  <room id='r-west' level='L1' name='West'>
    <point x='0' y='0'/><point x='500' y='0'/><point x='500' y='500'/><point x='0' y='500'/>
  </room>
  <room id='r-east' level='L1' name='East'>
    <point x='500' y='0'/><point x='1000' y='0'/><point x='1000' y='500'/><point x='500' y='500'/>
  </room>
  <room id='r-gar' level='LG' name='Gar'>
    <point x='0' y='0'/><point x='600' y='0'/><point x='600' y='600'/><point x='0' y='600'/>
  </room>
  <pieceOfFurniture id='ah1' level='L1' name='Air Handler' x='250' y='250' width='60' depth='60' height='90'/>
</home>
""")


def _room(env, name):
    return next(r for r in env.rooms if r.name == name)


def _rcat(room):
    out = {}
    for s in room.surfaces:
        out[s.category] = out.get(s.category, 0.0) + s.area_ft2
    return out


def test_rooms_parsed_with_conditioned_flag(tmp_path):
    p = tmp_path / "Home.xml"
    p.write_text(ROOM_FIXTURE)
    env = geometry.extract_envelope(str(p))
    from eldr import units
    assert {r.name for r in env.rooms} == {"West", "East", "Gar"}
    # West/East on Main are conditioned; Gar on the garage level is not.
    assert _room(env, "West").conditioned and _room(env, "East").conditioned
    assert not _room(env, "Gar").conditioned
    # each Main room is a 500x500 cm square
    assert abs(_room(env, "West").area_ft2 - units.sqcm_to_sqft(500 * 500)) < 1e-6


def test_room_wall_area_split_and_window(tmp_path):
    p = tmp_path / "Home.xml"
    p.write_text(ROOM_FIXTURE)
    env = geometry.extract_envelope(str(p))
    from eldr import units
    west, east = _rcat(_room(env, "West")), _rcat(_room(env, "East"))
    # the north facade (spanning both) is split, so both rooms get exterior wall
    assert west["exterior_wall"] > 0 and east["exterior_wall"] > 0
    # only the West room holds the window (attributed by position)
    assert abs(west["window"] - units.sqcm_to_sqft(100 * 100)) < 1e-6
    assert "window" not in east
    # every room's walls sum to ~ the whole-house exterior wall (net of the window);
    # the split conserves area (Gar's garage-box walls are attributed to Gar).
    whole = sum(s.area_ft2 for s in env.surfaces if s.category == "exterior_wall")
    per_room = sum(v for r in env.rooms for k, v in _rcat(r).items() if k == "exterior_wall")
    assert abs(per_room - whole) < 1e-6


def test_room_ceiling_floor_and_metadata(tmp_path):
    p = tmp_path / "Home.xml"
    p.write_text(ROOM_FIXTURE)
    env = geometry.extract_envelope(str(p))
    # Nothing is drawn above Main -> its rooms face the attic; West sits over the
    # garage, so its floor is a `buffer_floor`, never a ground-coupled `floor`.
    west = _rcat(_room(env, "West"))
    assert "ceiling" in west and "floor" not in west
    assert abs(west["buffer_floor"] - west["ceiling"]) < 1e-6
    # CHANGED (stack model): the garage room is unconditioned, so the resolver gives it
    # no faces at all — its slab was never a surface of the *conditioned* envelope.
    gar = _rcat(_room(env, "Gar"))
    assert gar == {}
    # furniture + level elevations exposed
    assert any(f.name == "Air Handler" for f in env.furniture)
    assert env.level_elevations["L1"] == 100.0 and env.level_elevations["LG"] == 0.0


def test_unconditioned_room_walls_excluded_from_envelope(tmp_path):
    # a garage/crawlspace-level room contributes NO wall to the conditioned envelope
    p = tmp_path / "Home.xml"
    p.write_text(ROOM_FIXTURE)
    env = geometry.extract_envelope(str(p))
    gar = _rcat(_room(env, "Gar"))
    assert "exterior_wall" not in gar and "basement_wall" not in gar   # its walls dropped
    # CHANGED (stack model): its floor is dropped too. The garage is unconditioned, so
    # the conditioned rooms *above* it now carry that boundary as a `buffer_floor`.
    assert "floor" not in gar
    assert sum(v for r in env.rooms if r.conditioned
               for k, v in _rcat(r).items() if k == "buffer_floor") > 0
    # whole-house exterior wall == the conditioned rooms' walls only (garage excluded)
    whole = sum(s.area_ft2 for s in env.surfaces if s.category == "exterior_wall")
    cond = sum(v for r in env.rooms if r.conditioned
               for k, v in _rcat(r).items() if k == "exterior_wall")
    assert whole > 0 and abs(whole - cond) < 1e-6


def test_wall_tag_buffer_forces_into_envelope(tmp_path):
    # the interior partition, tagged `buffer`, is pulled into the envelope as buffer_wall
    from eldr import units
    p = tmp_path / "Home.xml"
    p.write_text(ROOM_FIXTURE)
    assert "buffer_wall" not in _by_cat(geometry.extract_envelope(str(p)))
    tagged = _by_cat(geometry.extract_envelope(str(p), {"w-int": "buffer"}))
    assert abs(tagged["buffer_wall"] - units.sqcm_to_sqft(500 * 300)) < 1e-6


def test_wall_tag_interior_excludes(tmp_path):
    # tagging an exterior wall `interior` drops it from the envelope
    from eldr import units
    p = tmp_path / "Home.xml"
    p.write_text(ROOM_FIXTURE)
    base = _by_cat(geometry.extract_envelope(str(p)))
    tagged = _by_cat(geometry.extract_envelope(str(p), {"w-n": "interior"}))
    assert abs((base["exterior_wall"] - tagged["exterior_wall"])
               - units.sqcm_to_sqft(1000 * 300)) < 1e-6


def test_wall_tag_exterior_forces_include(tmp_path):
    # tagging the interior partition `exterior` forces it into exterior_wall
    from eldr import units
    p = tmp_path / "Home.xml"
    p.write_text(ROOM_FIXTURE)
    base = _by_cat(geometry.extract_envelope(str(p)))
    tagged = _by_cat(geometry.extract_envelope(str(p), {"w-int": "exterior"}))
    assert abs((tagged["exterior_wall"] - base["exterior_wall"])
               - units.sqcm_to_sqft(500 * 300)) < 1e-6


def test_wall_tag_unknown_id_warns(tmp_path):
    p = tmp_path / "Home.xml"
    p.write_text(ROOM_FIXTURE)
    with pytest.warns(UserWarning, match="unknown wall ids"):
        geometry.extract_envelope(str(p), {"wall-nope": "buffer"})


def test_wall_inventory_resolves_boundaries(tmp_path):
    p = tmp_path / "Home.xml"
    p.write_text(ROOM_FIXTURE)
    inv = {w.id: w for w in geometry.wall_inventory(str(p))}
    assert inv["w-int"].boundary == "interior"    # partition between West and East
    assert inv["w-n"].boundary == "exterior"      # perimeter, conditioned one side
    assert inv["g-n"].boundary == "interior"      # garage wall — unconditioned, not envelope
    assert not inv["w-n"].tagged
    # an explicit tag shows through, flagged as tagged
    tagged = {w.id: w for w in geometry.wall_inventory(str(p), {"w-int": "buffer"})}
    assert tagged["w-int"].boundary == "buffer" and tagged["w-int"].tagged


def test_wall_inventory_unknown_id_warns(tmp_path):
    # the listing (`--walls`) is the mode meant to fix tags, so it must flag stale ids
    p = tmp_path / "Home.xml"
    p.write_text(ROOM_FIXTURE)
    with pytest.warns(UserWarning, match="unknown wall ids"):
        geometry.wall_inventory(str(p), {"wall-nope": "buffer"})


def test_invalid_boundary_value_raises_valueerror(tmp_path):
    # a programmatic caller passing a bad boundary gets a schema error, not a KeyError
    p = tmp_path / "Home.xml"
    p.write_text(ROOM_FIXTURE)
    with pytest.raises(ValueError, match="invalid wall boundary"):
        geometry.extract_envelope(str(p), {"w-int": "garage"})


def test_no_rooms_leaves_rooms_empty(tmp_path):
    # the original single-box fixture has no <room> -> rooms is empty (backward compatible)
    p = tmp_path / "Home.xml"
    p.write_text(FIXTURE)
    env = geometry.extract_envelope(str(p))
    assert env.rooms == []


# An L/T footprint: West room (top-left, y 0..500) and East room (right, y 0..1000).
# The East room extends further south, so the level's bounding box reaches y=1000 —
# yet West's south wall at y=500 is a real perimeter wall (nothing conditioned south
# of it). The bounding-box test would miss it; the polygon-outline test must catch it.
LSHAPE_FIXTURE = textwrap.dedent("""\
<?xml version='1.0'?>
<home version='7400' name='t' wallHeight='300'>
  <level id='L1' name='Main' elevation='0.0' floorThickness='12.0' height='300' elevationIndex='0'/>
  <wall id='wn-w' level='L1' xStart='0' yStart='0' xEnd='500' yEnd='0' height='300' thickness='10'/>
  <wall id='wn-e' level='L1' xStart='500' yStart='0' xEnd='1000' yEnd='0' height='300' thickness='10'/>
  <wall id='w-w' level='L1' xStart='0' yStart='0' xEnd='0' yEnd='500' height='300' thickness='10'/>
  <wall id='ws-w' level='L1' xStart='0' yStart='500' xEnd='500' yEnd='500' height='300' thickness='10'/>
  <wall id='w-mid' level='L1' xStart='500' yStart='0' xEnd='500' yEnd='500' height='300' thickness='10'/>
  <wall id='w-e-lo' level='L1' xStart='500' yStart='500' xEnd='500' yEnd='1000' height='300' thickness='10'/>
  <wall id='w-e' level='L1' xStart='1000' yStart='0' xEnd='1000' yEnd='1000' height='300' thickness='10'/>
  <wall id='ws-e' level='L1' xStart='500' yStart='1000' xEnd='1000' yEnd='1000' height='300' thickness='10'/>
  <room id='r-w' level='L1' name='West'><point x='0' y='0'/><point x='500' y='0'/><point x='500' y='500'/><point x='0' y='500'/></room>
  <room id='r-e' level='L1' name='East'><point x='500' y='0'/><point x='1000' y='0'/><point x='1000' y='1000'/><point x='500' y='1000'/></room>
</home>
""")


def test_polygon_outline_catches_setback_perimeter_wall(tmp_path):
    p = tmp_path / "Home.xml"
    p.write_text(LSHAPE_FIXTURE)
    env = geometry.extract_envelope(str(p))
    from eldr import units
    west = _rcat(_room(env, "West"))
    # West's three real perimeter sides (north + west + the SET-BACK south wall) are
    # all exterior — more than the two the bounding-box edge test would have found.
    assert abs(west["exterior_wall"] - units.sqcm_to_sqft(3 * 500 * 300)) < 1e-6
    # the shared West/East partition (w-mid) is interior -> excluded from the envelope.
    # Whole-house exterior wall == the true perimeter length (4000 cm) × height.
    whole = sum(s.area_ft2 for s in env.surfaces if s.category == "exterior_wall")
    assert abs(whole - units.sqcm_to_sqft(4000 * 300)) < 1e-6


# A 100 cm room at the end of a 2000 cm exterior wall — a fixed 7-sample split would
# miss it entirely (first sample lands at x≈143). Density-based sampling must give it
# a share of that facade.
NARROW_FIXTURE = textwrap.dedent("""\
<?xml version='1.0'?>
<home version='7400' name='t' wallHeight='300'>
  <level id='L1' name='Main' elevation='0.0' floorThickness='12.0' height='300' elevationIndex='0'/>
  <wall id='w-n' level='L1' xStart='0' yStart='0' xEnd='2000' yEnd='0' height='300' thickness='10'/>
  <wall id='w-s' level='L1' xStart='0' yStart='500' xEnd='2000' yEnd='500' height='300' thickness='10'/>
  <wall id='w-w' level='L1' xStart='0' yStart='0' xEnd='0' yEnd='500' height='300' thickness='10'/>
  <wall id='w-e' level='L1' xStart='2000' yStart='0' xEnd='2000' yEnd='500' height='300' thickness='10'/>
  <wall id='w-mid' level='L1' xStart='100' yStart='0' xEnd='100' yEnd='500' height='300' thickness='10'/>
  <room id='r-narrow' level='L1' name='Narrow'><point x='0' y='0'/><point x='100' y='0'/><point x='100' y='500'/><point x='0' y='500'/></room>
  <room id='r-wide' level='L1' name='Wide'><point x='100' y='0'/><point x='2000' y='0'/><point x='2000' y='500'/><point x='100' y='500'/></room>
</home>
""")


def test_grouped_furniture_is_found(tmp_path):
    # a pieceOfFurniture nested in a <furnitureGroup> must still be scanned (iter, not findall)
    xml = ROOM_FIXTURE.replace(
        "<pieceOfFurniture id='ah1' level='L1' name='Air Handler' x='250' y='250' width='60' depth='60' height='90'/>",
        "<furnitureGroup id='g1' level='L1' name='Mechanicals'>"
        "<pieceOfFurniture id='ah1' level='L1' name='Air Handler' x='250' y='250' width='60' depth='60' height='90'/>"
        "</furnitureGroup>")
    p = tmp_path / "Home.xml"
    p.write_text(xml)
    env = geometry.extract_envelope(str(p))
    assert any(f.name == "Air Handler" for f in env.furniture)


def test_adaptive_sampling_gives_narrow_room_its_facade_share(tmp_path):
    p = tmp_path / "Home.xml"
    p.write_text(NARROW_FIXTURE)
    env = geometry.extract_envelope(str(p))
    from eldr import units
    narrow = _rcat(_room(env, "Narrow"))
    # its own west wall is 500×300; density sampling must add a slice of the north
    # facade on top, so its exterior wall exceeds the west-wall-only area.
    assert narrow["exterior_wall"] > units.sqcm_to_sqft(500 * 300)


def test_volume_counts_conditioned_rooms_only(tmp_path):
    """The garage level must not inflate the infiltration volume.

    Before the fix, volume came from each level's wall bounding box, so the
    garage (a whole extra 400x300 footprint at 300cm) was counted as if it were
    conditioned space the air leaks into -- roughly doubling infiltration.
    """
    p = tmp_path / "Home.xml"
    p.write_text(MULTI_LEVEL_FIXTURE)
    env = geometry.extract_envelope(str(p))
    from eldr import units
    expected = (units.sqcm_to_sqft(400 * 300) * units.cm_to_ft(200)      # basement
                + units.sqcm_to_sqft(400 * 300) * units.cm_to_ft(250))   # main
    assert abs(env.volume_ft3 - expected) < 1e-6


def test_main_floor_over_basement_emits_no_surface(tmp_path):
    """Conditioned over conditioned is interior — no horizontal surface at all."""
    p = tmp_path / "Home.xml"
    p.write_text(MULTI_LEVEL_FIXTURE)
    env = geometry.extract_envelope(str(p))
    main = next(r for r in env.rooms if r.name == "Living room")
    assert not [s for s in main.surfaces if s.category in ("floor", "buffer_floor")]


def test_top_room_ceiling_faces_the_attic(tmp_path):
    p = tmp_path / "Home.xml"
    p.write_text(MULTI_LEVEL_FIXTURE)
    env = geometry.extract_envelope(str(p))
    main = next(r for r in env.rooms if r.name == "Living room")
    ceil = next(s for s in main.surfaces if s.category == "ceiling")
    assert ceil.space == "attic"


@pytest.mark.parametrize("fixture", [
    # Rooms fill their levels, so the OLD bounding-box code agreed here too. Kept as the
    # control: it must stay in agreement, and it proves the resolver didn't break the
    # easy case while fixing the hard one.
    MULTI_LEVEL_FIXTURE,
    # The discriminating case. An L-shaped level's bounding box (1000x1000) swallows the
    # empty notch, so the old whole-house ceiling/floor read 1076.4 ft^2 against 807.3
    # ft^2 of actual room polygon — a 269 ft^2 phantom surface on a 807 ft^2 house. This
    # parameter fails against the bounding-box model and passes against the resolver.
    LSHAPE_FIXTURE,
], ids=["rooms-fill-the-level", "L-shaped-level-with-a-notch"])
def test_whole_house_horizontals_equal_the_sum_of_room_horizontals(tmp_path, fixture):
    """Whole-house surfaces used level bounding boxes while per-room used polygons,
    so the two disagreed. They are now the same resolution by construction."""
    p = tmp_path / "Home.xml"
    p.write_text(fixture)
    env = geometry.extract_envelope(str(p))
    horizontals = {"ceiling", "floor", "buffer_floor", "exposed_floor"}
    for cat in horizontals:
        whole = sum(s.area_ft2 for s in env.surfaces if s.category == cat)
        rooms = sum(s.area_ft2 for r in env.rooms for s in r.surfaces if s.category == cat)
        assert abs(whole - rooms) < 1e-6, cat


def test_roomless_model_keeps_the_bounding_box_envelope(tmp_path):
    """Regression guard: the legacy fallback must survive the stack model."""
    p = tmp_path / "Home.xml"
    p.write_text(FIXTURE)                     # the original walls-only fixture
    env = geometry.extract_envelope(str(p))
    cats = _by_cat(env)
    from eldr import units
    foot = units.sqcm_to_sqft(1000 * 500)
    assert abs(cats["ceiling"] - foot) < 1e-6
    assert abs(cats["floor"] - foot) < 1e-6


def test_sidecar_level_name_not_in_the_model_warns(tmp_path):
    """A `levels` entry addresses a level by name, so a typo binds to nothing. Silence
    is the dangerous outcome: a mistyped `role: ignore` on a duct chase looks handled
    while quietly leaving its phantom volume in place."""
    from eldr import sidecar as sc_mod
    p = tmp_path / "Home.xml"
    p.write_text(MULTI_LEVEL_FIXTURE)
    with pytest.warns(UserWarning, match="levels` reference level names not in the model"):
        geometry.extract_envelope(str(p), levels={"Mian": sc_mod.LevelSpec(role="ignore")})


def test_sidecar_level_name_present_in_the_model_does_not_warn(tmp_path):
    from eldr import sidecar as sc_mod
    p = tmp_path / "Home.xml"
    p.write_text(MULTI_LEVEL_FIXTURE)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        geometry.extract_envelope(str(p), levels={"Main": sc_mod.LevelSpec(height_ft=9.0)})


def _two_level_fixture(lower_name):
    """Main (400x300 @212cm) sitting exactly on `lower_name` (400x300 @0cm), both roomed.

    What the lower level IS decides Main's whole floor: conditioned below means Main's
    floor is interior and emits nothing, unconditioned below means it is a `buffer_floor`
    facing that level's name. So the two roles produce disjoint surface sets — there is no
    reading of the output that is compatible with both.
    """
    return textwrap.dedent(f"""\
    <?xml version='1.0'?>
    <home version='7400' name='t' wallHeight='300'>
      <level id='LL' name='{lower_name}' elevation='0.0' floorThickness='12.0' height='200' elevationIndex='0'/>
      <level id='LM' name='Main' elevation='212.0' floorThickness='12.0' height='250' elevationIndex='0'/>
      <wall id='l-n' level='LL' xStart='0' yStart='0' xEnd='400' yEnd='0' height='200' thickness='10'/>
      <wall id='l-s' level='LL' xStart='0' yStart='300' xEnd='400' yEnd='300' height='200' thickness='10'/>
      <wall id='l-w' level='LL' xStart='0' yStart='0' xEnd='0' yEnd='300' height='200' thickness='10'/>
      <wall id='l-e' level='LL' xStart='400' yStart='0' xEnd='400' yEnd='300' height='200' thickness='10'/>
      <wall id='m-n' level='LM' xStart='0' yStart='0' xEnd='400' yEnd='0' height='250' thickness='10'/>
      <wall id='m-s' level='LM' xStart='0' yStart='300' xEnd='400' yEnd='300' height='250' thickness='10'/>
      <wall id='m-w' level='LM' xStart='0' yStart='0' xEnd='0' yEnd='300' height='250' thickness='10'/>
      <wall id='m-e' level='LM' xStart='400' yStart='0' xEnd='400' yEnd='300' height='250' thickness='10'/>
      <room id='rl' level='LL' name='Lower Room'>
        <point x='0' y='0'/><point x='400' y='0'/><point x='400' y='300'/><point x='0' y='300'/>
      </room>
      <room id='rm' level='LM' name='Living room'>
        <point x='0' y='0'/><point x='400' y='0'/><point x='400' y='300'/><point x='0' y='300'/>
      </room>
    </home>
    """)


def _spaces_faced(env, category):
    """The space names the envelope's surfaces of `category` face."""
    return {s.space for s in env.surfaces if s.category == category}


def test_role_unconditioned_overrides_a_name_the_heuristic_reads_as_conditioned(tmp_path):
    """`role:` is the documented override for the one decision that drives the entire
    horizontal envelope, and nothing exercised it — making the side-car branch of
    `_level_conditioned` `if False:` passed the whole suite.

    "Crawl Space" is the fixture because the heuristic it replaces is a bare prefix match
    on `crawlspace`, so a SPACE in the name is enough to defeat it: SH3D lets you name a
    level anything, and the level's role is exactly the thing the modeler cannot be asked
    to encode in its name. Untouched, that level reads as conditioned and Main's floor
    vanishes into `interior`; the override has to bring back 400x300 of buffer floor.
    """
    from eldr import sidecar as sc_mod
    from eldr import units
    p = tmp_path / "Home.xml"
    p.write_text(_two_level_fixture("Crawl Space"))
    foot = units.sqcm_to_sqft(400 * 300)

    heuristic = _by_cat(geometry.extract_envelope(str(p)))
    assert "buffer_floor" not in heuristic        # read as conditioned -> Main sits on interior
    assert heuristic["floor"] == pytest.approx(foot)   # the lower level got the slab

    env = geometry.extract_envelope(
        str(p), levels={"Crawl Space": sc_mod.LevelSpec(role="unconditioned")})
    cats = _by_cat(env)
    assert cats["buffer_floor"] == pytest.approx(foot)
    assert _spaces_faced(env, "buffer_floor") == {"crawl space"}
    assert "floor" not in cats                    # no conditioned room on grade any more


def test_role_conditioned_pulls_a_level_the_heuristic_excludes_back_into_the_envelope(
        tmp_path):
    """The other direction, and the one a `role: conditioned` entry exists for: a level
    the name heuristic throws out. `Garage` matches the prefix, so by default Main's floor
    is a buffer floor over it; declaring it conditioned makes it a storey — Main's floor
    becomes interior and the garage level takes the slab instead.

    Both directions are needed. A test of only one passes with the branch hard-wired to
    the answer that direction wants.
    """
    from eldr import sidecar as sc_mod
    from eldr import units
    p = tmp_path / "Home.xml"
    p.write_text(_two_level_fixture("Garage"))
    foot = units.sqcm_to_sqft(400 * 300)

    heuristic = geometry.extract_envelope(str(p))
    assert _by_cat(heuristic)["buffer_floor"] == pytest.approx(foot)
    assert _spaces_faced(heuristic, "buffer_floor") == {"garage"}

    cats = _by_cat(geometry.extract_envelope(
        str(p), levels={"Garage": sc_mod.LevelSpec(role="conditioned")}))
    assert "buffer_floor" not in cats
    assert cats["floor"] == pytest.approx(foot)


def test_sidecar_level_height_override_changes_volume(tmp_path):
    from eldr import sidecar as sc_mod
    p = tmp_path / "Home.xml"
    p.write_text(MULTI_LEVEL_FIXTURE)
    base = geometry.extract_envelope(str(p))
    tall = geometry.extract_envelope(
        str(p), levels={"Main": sc_mod.LevelSpec(height_ft=20.0)})
    assert tall.volume_ft3 > base.volume_ft3


def test_scaffolding_level_with_a_wall_adds_no_volume(tmp_path):
    """A roomless level carrying a wall (a modeled duct chase) must not inject
    bounding-box volume into infiltration.

    The chase needs TWO non-parallel walls: a single wall's bounding box has zero
    area, so it would contribute nothing anyway and the test could never fail.
    """
    p = tmp_path / "Home.xml"
    p.write_text(MULTI_LEVEL_FIXTURE.replace(
        "  <room id='rb'",
        "  <level id='LT' name='transition' elevation='210.0' floorThickness='2.0'"
        " height='30' elevationIndex='0'/>\n"
        "  <wall id='t-n' level='LT' xStart='0' yStart='0' xEnd='400' yEnd='0'"
        " height='30' thickness='10'/>\n"
        "  <wall id='t-e' level='LT' xStart='400' yStart='0' xEnd='400' yEnd='300'"
        " height='30' thickness='10'/>\n"
        "  <room id='rb'"))
    with pytest.warns(UserWarning, match="walls but no rooms"):
        env = geometry.extract_envelope(str(p))
    from eldr import units
    expected = (units.sqcm_to_sqft(400 * 300) * units.cm_to_ft(200)
                + units.sqcm_to_sqft(400 * 300) * units.cm_to_ft(250))
    assert abs(env.volume_ft3 - expected) < 1e-6


def test_walled_roomless_level_warns_instead_of_guessing(tmp_path):
    """Dropping a roomless level is right for a duct chase and wrong for a storey whose
    rooms aren't drawn yet — and the two are indistinguishable mid-modeling. Eldr takes
    the safe route (exclude it) but must SAY so: silently zeroing the infiltration term
    is the failure mode that hides. Nothing here guesses which kind of level it is.
    """
    p = tmp_path / "Home.xml"
    # Every storey walled, no rooms anywhere except the basement -> Main is dropped.
    p.write_text(MULTI_LEVEL_FIXTURE.replace(
        "  <room id='rm' level='LM' name='Living room'>\n"
        "    <point x='0' y='0'/><point x='400' y='0'/>"
        "<point x='400' y='300'/><point x='0' y='300'/>\n"
        "  </room>\n", ""))
    with pytest.warns(UserWarning, match=r"'Main' has walls but no rooms"):
        env = geometry.extract_envelope(str(p))
    from eldr import units
    # Only the basement survives; Main's 400x300x250 is gone from infiltration entirely.
    assert abs(env.volume_ft3
               - units.sqcm_to_sqft(400 * 300) * units.cm_to_ft(200)) < 1e-6


def test_a_lone_wall_on_a_roomless_level_is_too_slight_to_warn(tmp_path):
    """The noise floor: one wall spans zero footprint, so there is nothing to report."""
    p = tmp_path / "Home.xml"
    p.write_text(MULTI_LEVEL_FIXTURE.replace(
        "  <room id='rb'",
        "  <level id='LT' name='transition' elevation='210.0' floorThickness='2.0'"
        " height='30' elevationIndex='0'/>\n"
        "  <wall id='t-n' level='LT' xStart='0' yStart='0' xEnd='400' yEnd='0'"
        " height='30' thickness='10'/>\n"
        "  <room id='rb'"))
    with warnings.catch_warnings():
        warnings.simplefilter("error")       # any warning at all fails the test
        geometry.extract_envelope(str(p))


def _with_chase(span_cm):
    """MULTI_LEVEL_FIXTURE plus a roomless level whose two walls span a `span_cm` square."""
    return MULTI_LEVEL_FIXTURE.replace(
        "  <room id='rb'",
        f"  <level id='LT' name='transition' elevation='210.0' floorThickness='2.0'"
        f" height='30' elevationIndex='0'/>\n"
        f"  <wall id='t-n' level='LT' xStart='0' yStart='0' xEnd='{span_cm}' yEnd='0'"
        f" height='30' thickness='10'/>\n"
        f"  <wall id='t-e' level='LT' xStart='{span_cm}' yStart='0' xEnd='{span_cm}'"
        f" yEnd='{span_cm}' height='30' thickness='10'/>\n"
        f"  <room id='rb'")


@pytest.mark.parametrize("span_cm, expect_warning", [(95, False), (98, True)],
                         ids=["just-under-the-noise-floor", "just-over-it"])
def test_scaffold_warning_threshold_is_pinned_from_both_sides(tmp_path, span_cm,
                                                              expect_warning):
    """_SCAFFOLD_WARN_FT2 is 10 ft^2, and until now nothing held it there.

    Its only other test uses a footprint of exactly ZERO area (a single wall), which a
    threshold of 0.0001 ft^2 would pass just as happily. A 95cm square is 9.71 ft^2 and a
    98cm square is 10.34 ft^2, so the two cases together bracket the constant: lowering it
    breaks the first, raising it breaks the second.
    """
    p = tmp_path / "Home.xml"
    p.write_text(_with_chase(span_cm))
    if expect_warning:
        with pytest.warns(UserWarning, match="walls but no rooms"):
            geometry.extract_envelope(str(p))
    else:
        with warnings.catch_warnings():
            warnings.simplefilter("error")   # any warning at all fails the test
            geometry.extract_envelope(str(p))


def test_role_ignore_acknowledges_a_roomless_level_and_silences_the_warning(tmp_path):
    """`role: ignore` is the modeler saying "yes, that really is a duct chase".

    Without this, a level big enough to clear the noise floor warned on every single run
    and the only way to stop it was to delete the walls — i.e. to damage the model to
    quiet a message about the model. The level must stay excluded from the volume: the
    warning goes away, the exclusion it announced does not.
    """
    from eldr import sidecar as sc_mod
    from eldr import units
    p = tmp_path / "Home.xml"
    p.write_text(_with_chase(98))                     # over the noise floor -> would warn
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        env = geometry.extract_envelope(
            str(p), levels={"transition": sc_mod.LevelSpec(role="ignore")})
    expected = (units.sqcm_to_sqft(400 * 300) * units.cm_to_ft(200)      # basement
                + units.sqcm_to_sqft(400 * 300) * units.cm_to_ft(250))   # main
    assert abs(env.volume_ft3 - expected) < 1e-6


def test_role_ignore_on_one_level_does_not_silence_another(tmp_path):
    """The acknowledgement is per level, not a global mute."""
    from eldr import sidecar as sc_mod
    p = tmp_path / "Home.xml"
    p.write_text(_with_chase(98).replace(
        "  <room id='rm' level='LM' name='Living room'>\n"
        "    <point x='0' y='0'/><point x='400' y='0'/>"
        "<point x='400' y='300'/><point x='0' y='300'/>\n"
        "  </room>\n", ""))
    with pytest.warns(UserWarning, match=r"'Main' has walls but no rooms"):
        geometry.extract_envelope(
            str(p), levels={"transition": sc_mod.LevelSpec(role="ignore")})


def test_level_volumes_attribute_the_conditioned_volume_per_level(tmp_path):
    """The report echoes what each level contributed; the sum must be the whole house."""
    from eldr import units
    p = tmp_path / "Home.xml"
    p.write_text(MULTI_LEVEL_FIXTURE)
    env = geometry.extract_envelope(str(p))
    assert env.level_volumes_ft3["Garage"] == 0.0        # unconditioned -> contributes none
    assert env.level_volumes_ft3["Basement"] == pytest.approx(
        units.sqcm_to_sqft(400 * 300) * units.cm_to_ft(200))
    assert env.level_volumes_ft3["Main"] == pytest.approx(
        units.sqcm_to_sqft(400 * 300) * units.cm_to_ft(250))
    assert sum(env.level_volumes_ft3.values()) == pytest.approx(env.volume_ft3)
