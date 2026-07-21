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


def test_window_orientation_south(tmp_path):
    # The window sits on the y=500 wall (bottom of plan). Plan Y is down = south,
    # so with the default compass (northDirection 0) it faces South.
    p = tmp_path / "Home.xml"
    p.write_text(FIXTURE)
    env = geometry.extract_envelope(str(p))
    from eldr import units
    assert set(env.windows_by_orientation) == {"S"}
    assert abs(env.windows_by_orientation["S"] - units.sqcm_to_sqft(100 * 100)) < 1e-6


def test_window_orientation_rotated_compass(tmp_path):
    # northDirection = pi/2 rotates true-north 90deg clockwise, so the bottom
    # (plan-south) wall's window buckets to East. Guards the rotation sign + radians.
    p = tmp_path / "Home.xml"
    p.write_text(_fixture_with_compass("1.57079632679"))
    env = geometry.extract_envelope(str(p))
    assert set(env.windows_by_orientation) == {"E"}


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
