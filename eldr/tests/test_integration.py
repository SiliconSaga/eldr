import textwrap
from eldr import cli

FIXTURE = textwrap.dedent("""\
<?xml version='1.0'?>
<home version='7400' name='t' wallHeight='300'>
  <level id='L1' name='Main' elevation='0.0' floorThickness='12.0' height='300' elevationIndex='0'/>
  <wall id='w-n' level='L1' xStart='0' yStart='0' xEnd='1000' yEnd='0' height='300' thickness='10'/>
  <wall id='w-s' level='L1' xStart='0' yStart='500' xEnd='1000' yEnd='500' height='300' thickness='10'/>
  <wall id='w-w' level='L1' xStart='0' yStart='0' xEnd='0' yEnd='500' height='300' thickness='10'/>
  <wall id='w-e' level='L1' xStart='1000' yStart='0' xEnd='1000' yEnd='500' height='300' thickness='10'/>
  <doorOrWindow id='win1' level='L1' catalogId='eTeks#window' name='Window' x='500' y='500' width='100' height='100'/>
</home>
""")

SIDECAR = textwrap.dedent("""\
design:
  indoor_heating_f: 70
  outdoor_heating_99_f: 15
  supply_air_rise_f: 50
infiltration:
  ach: 0.5
assemblies:
  exterior_wall: 0.09
  window: 0.30
  ceiling: 0.026
  floor: 0.05
""")


def test_end_to_end(tmp_path):
    home = tmp_path / "Home.xml"; home.write_text(FIXTURE)
    sc = tmp_path / "sc.yaml"; sc.write_text(SIDECAR)
    md = cli.run(str(home), str(sc))
    assert "# Eldr — Heating Load" in md
    assert "total" in md
    assert "CFM" in md


# A model with a compass lat/long (West Orange NJ) but a side-car omitting the
# outdoor design temp -> the temp is looked up from the nearest station.
FIXTURE_GEO = FIXTURE.replace(
    "<home version='7400' name='t' wallHeight='300'>",
    "<home version='7400' name='t' wallHeight='300'>\n"
    "  <compass x='0' y='0' diameter='100' latitude='0.7105963' longitude='-1.2916551'/>",
)
SIDECAR_NO_TEMP = textwrap.dedent("""\
design:
  indoor_heating_f: 70
  supply_air_rise_f: 50
infiltration:
  ach: 0.5
assemblies:
  exterior_wall: 0.09
  window: 0.30
  ceiling: 0.026
  floor: 0.05
""")


def test_climate_lookup_fills_design_temp(tmp_path):
    home = tmp_path / "Home.xml"; home.write_text(FIXTURE_GEO)
    sc = tmp_path / "sc.yaml"; sc.write_text(SIDECAR_NO_TEMP)
    md = cli.run(str(home), str(sc))
    assert "nearest station" in md          # the lookup note rendered
    assert ("NY" in md) or ("NJ" in md)     # West Orange -> NYC/Newark


def test_missing_temp_and_no_latlong_errors(tmp_path):
    home = tmp_path / "Home.xml"; home.write_text(FIXTURE)   # no compass
    sc = tmp_path / "sc.yaml"; sc.write_text(SIDECAR_NO_TEMP)
    import pytest
    with pytest.raises(ValueError, match="lat/long"):
        cli.run(str(home), str(sc))
