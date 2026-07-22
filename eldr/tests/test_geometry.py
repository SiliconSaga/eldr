import textwrap
import zipfile
import pytest
from eldr import geometry


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
    # Main is the top level -> its rooms get a ceiling, not a floor
    west = _rcat(_room(env, "West"))
    assert "ceiling" in west and "floor" not in west
    # Garage is the bottom level -> its room gets a floor, not a ceiling
    gar = _rcat(_room(env, "Gar"))
    assert "floor" in gar and "ceiling" not in gar
    # furniture + level elevations exposed
    assert any(f.name == "Air Handler" for f in env.furniture)
    assert env.level_elevations["L1"] == 100.0 and env.level_elevations["LG"] == 0.0


def test_no_rooms_leaves_rooms_empty(tmp_path):
    # the original single-box fixture has no <room> -> rooms is empty (backward compatible)
    p = tmp_path / "Home.xml"
    p.write_text(FIXTURE)
    env = geometry.extract_envelope(str(p))
    assert env.rooms == []
