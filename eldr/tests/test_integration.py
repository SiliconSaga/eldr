import textwrap
import pytest
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
    home = tmp_path / "Home.xml"
    home.write_text(FIXTURE)
    sc = tmp_path / "sc.yaml"
    sc.write_text(SIDECAR)
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
    home = tmp_path / "Home.xml"
    home.write_text(FIXTURE_GEO)
    sc = tmp_path / "sc.yaml"
    sc.write_text(SIDECAR_NO_TEMP)
    md = cli.run(str(home), str(sc))
    assert "nearest station" in md          # the lookup note rendered
    assert ("NY" in md) or ("NJ" in md)     # West Orange -> NYC/Newark


def test_missing_temp_and_no_latlong_errors(tmp_path):
    home = tmp_path / "Home.xml"
    home.write_text(FIXTURE)                # no compass
    sc = tmp_path / "sc.yaml"
    sc.write_text(SIDECAR_NO_TEMP)
    import pytest
    with pytest.raises(ValueError, match="lat/long"):
        cli.run(str(home), str(sc))


# A two-room model with a placed air handler -> the full Manual J 1c + model-derived
# Manual D path runs end-to-end through the CLI.
FIXTURE_ROOMS = textwrap.dedent("""\
<?xml version='1.0'?>
<home version='7400' name='t' wallHeight='300'>
  <level id='L1' name='Main' elevation='0.0' floorThickness='12.0' height='300' elevationIndex='0'/>
  <wall id='w-n' level='L1' xStart='0' yStart='0' xEnd='1000' yEnd='0' height='300' thickness='10'/>
  <wall id='w-s' level='L1' xStart='0' yStart='500' xEnd='1000' yEnd='500' height='300' thickness='10'/>
  <wall id='w-w' level='L1' xStart='0' yStart='0' xEnd='0' yEnd='500' height='300' thickness='10'/>
  <wall id='w-e' level='L1' xStart='1000' yStart='0' xEnd='1000' yEnd='500' height='300' thickness='10'/>
  <wall id='w-int' level='L1' xStart='500' yStart='0' xEnd='500' yEnd='500' height='300' thickness='10'/>
  <room id='r-w' level='L1' name='West'><point x='0' y='0'/><point x='500' y='0'/><point x='500' y='500'/><point x='0' y='500'/></room>
  <room id='r-e' level='L1' name='East'><point x='500' y='0'/><point x='1000' y='0'/><point x='1000' y='500'/><point x='500' y='500'/></room>
  <pieceOfFurniture id='ah1' level='L1' name='Air Handler' x='250' y='250' width='60' depth='60' height='90'/>
</home>
""")


def test_end_to_end_per_room_and_ducts(tmp_path):
    home = tmp_path / "Home.xml"
    home.write_text(FIXTURE_ROOMS)
    sc = tmp_path / "sc.yaml"
    sc.write_text(SIDECAR)
    md = cli.run(str(home), str(sc))
    assert "Per-Room Loads (Manual J 1c)" in md
    assert "West" in md and "East" in md
    assert "## Manual D" in md
    assert "main trunk" in md
    assert "Air handler" in md            # a unit is placed -> length column + note
    assert "Length" in md


def test_list_walls_renders(tmp_path):
    home = tmp_path / "Home.xml"
    home.write_text(FIXTURE_ROOMS)
    md = cli.list_walls(str(home))
    assert "Eldr — walls" in md
    assert "w-int" in md and "boundary" in md


def test_wall_tag_flows_through_to_report(tmp_path):
    # a `walls` tag turns the interior partition into a buffer wall in the report
    home = tmp_path / "Home.xml"
    home.write_text(FIXTURE_ROOMS)
    sc = tmp_path / "sc.yaml"
    sc.write_text(SIDECAR + "walls:\n  w-int: {boundary: buffer}\n")
    md = cli.run(str(home), str(sc))
    assert "buffer_wall" in md


# A conditioned storey sitting over an unconditioned garage: the resolver gives its
# rooms a `buffer_floor`, a category no side-car in this repo declares an assembly for.
FIXTURE_OVER_GARAGE = textwrap.dedent("""\
<?xml version='1.0'?>
<home version='7400' name='t' wallHeight='300'>
  <level id='LG' name='Garage' elevation='0.0' floorThickness='12.0' height='300' elevationIndex='0'/>
  <level id='L1' name='Main' elevation='300.0' floorThickness='12.0' height='300' elevationIndex='1'/>
  <wall id='g-n' level='LG' xStart='0' yStart='0' xEnd='500' yEnd='0' height='300' thickness='10'/>
  <wall id='g-s' level='LG' xStart='0' yStart='500' xEnd='500' yEnd='500' height='300' thickness='10'/>
  <wall id='g-w' level='LG' xStart='0' yStart='0' xEnd='0' yEnd='500' height='300' thickness='10'/>
  <wall id='g-e' level='LG' xStart='500' yStart='0' xEnd='500' yEnd='500' height='300' thickness='10'/>
  <wall id='m-n' level='L1' xStart='0' yStart='0' xEnd='500' yEnd='0' height='300' thickness='10'/>
  <wall id='m-s' level='L1' xStart='0' yStart='500' xEnd='500' yEnd='500' height='300' thickness='10'/>
  <wall id='m-w' level='L1' xStart='0' yStart='0' xEnd='0' yEnd='500' height='300' thickness='10'/>
  <wall id='m-e' level='L1' xStart='500' yStart='0' xEnd='500' yEnd='500' height='300' thickness='10'/>
  <room id='r-gar' level='LG' name='Gar'>
    <point x='0' y='0'/><point x='500' y='0'/><point x='500' y='500'/><point x='0' y='500'/>
  </room>
  <room id='r-main' level='L1' name='Living'>
    <point x='0' y='0'/><point x='500' y='0'/><point x='500' y='500'/><point x='0' y='500'/>
  </room>
</home>
""")


def test_buffer_floor_without_an_assembly_borrows_the_floor_u(tmp_path):
    """The flipped marker: this model used to raise, and must now produce a load.

    `SIDECAR` declares no `buffer_floor` and no `exposed_floor`, so the U-value walks
    the fallback chain `buffer_floor -> exposed_floor -> floor` and lands on `floor`
    (0.05). The ΔT is the garage's, not outdoor air's: no `spaces:` block is declared,
    so `garage` takes its built-in unvented default of half the design ΔT.
    """
    from eldr import geometry, loads, sidecar as sidecar_mod, spaces
    home = tmp_path / "Home.xml"
    home.write_text(FIXTURE_OVER_GARAGE)
    sc = tmp_path / "sc.yaml"
    sc.write_text(SIDECAR)                       # declares exterior_wall/window/ceiling/floor
    md = cli.run(str(home), str(sc))             # the path that raised before Task 6
    assert "buffer_floor" in md

    parsed = sidecar_mod.load_sidecar(str(sc))
    env = geometry.extract_envelope(str(home), parsed.wall_boundaries, parsed.levels)
    area = sum(s.area_ft2 for s in env.surfaces if s.category == "buffer_floor")
    r = loads.heating_load(env, parsed)
    expected = 0.05 * area * parsed.design.heating_delta_t * spaces.UNVENTED_FACTOR
    assert abs(r.by_category["buffer_floor"] - expected) < 1e-6


def test_buffer_floor_is_actually_what_that_model_produces(tmp_path):
    """Pins the precondition the test above depends on but cannot itself check.

    That test's expected ΔT is the *garage's* unvented default, which only holds because
    these surfaces carry `space == "garage"`. If the resolver kept emitting `buffer_floor`
    but attributed it to a different space — or to none — the expectation there would
    silently start describing the wrong physics. This keeps the attribution honest."""
    from eldr import geometry
    home = tmp_path / "Home.xml"
    home.write_text(FIXTURE_OVER_GARAGE)
    env = geometry.extract_envelope(str(home))
    buffer_floors = [s for s in env.surfaces if s.category == "buffer_floor"]
    assert buffer_floors and all(s.space == "garage" for s in buffer_floors)


def test_below_void_outdoor_reaches_exposed_floor_and_the_report_says_so(tmp_path):
    """The only documented route to `exposed_floor`, driven end to end.

    Two things at once, because neither is worth much alone. First that the route is
    real: `levels.Main.below_void: outdoor` makes the undrawn area an `exposed_floor`
    facing the `outdoor` space, whose built-in policy is factor 1.0 — the FULL design ΔT,
    not a buffer's half. That is what makes the second half matter: the report's void
    warning used to name `buffer_floor` unconditionally, so on this path it described a
    surface the envelope does not contain, at half the ΔT the engine actually applied.

    Without this test the report-level tests would be checking a hand-built Envelope
    against a hand-built expectation, with nothing to say the resolver produces that
    shape at all.
    """
    from eldr import geometry, loads, sidecar as sidecar_mod, report
    from eldr.tests.fixtures import VOID_BELOW_FIXTURE
    home = tmp_path / "Home.xml"
    home.write_text(VOID_BELOW_FIXTURE)
    sc = tmp_path / "sc.yaml"
    sc.write_text(SIDECAR + "  basement_wall: 0.20\nlevels:\n  Main:\n"
                            "    below_void: outdoor\n")
    parsed = sidecar_mod.load_sidecar(str(sc))
    env = geometry.extract_envelope(str(home), parsed.wall_boundaries, parsed.levels)

    exposed = [s for s in env.surfaces if s.category == "exposed_floor"]
    assert exposed and all(s.space == "outdoor" for s in exposed)
    assert not any(s.category == "buffer_floor" for s in env.surfaces)
    assert env.voids                                  # still reported as a schematic gap

    # the full ΔT, not a buffer fraction — the claim the old warning contradicted
    with pytest.warns(UserWarning):                   # exposed_floor borrows `floor`
        r = loads.heating_load(env, parsed)
    area = sum(s.area_ft2 for s in exposed)
    assert abs(r.by_category["exposed_floor"]
               - 0.05 * area * parsed.design.heating_delta_t) < 1e-6

    md = report.render_heating(r, parsed, env=env)   # rendering resolves no U-values
    warning = next(l for l in md.splitlines() if l.startswith("⚠ **"))
    assert "`exposed_floor`" in warning and "buffer_floor" not in warning


def test_cli_walls_and_overview_mutually_exclusive():
    with pytest.raises(SystemExit):
        cli.main(["home.xml", "--walls", "--overview"])


def test_cli_overview_without_sidecar_errors(tmp_path):
    home = tmp_path / "Home.xml"
    home.write_text(FIXTURE_ROOMS)
    with pytest.raises(SystemExit):
        cli.main([str(home), "--overview"])


def test_cli_json_and_overview_mutually_exclusive():
    with pytest.raises(SystemExit):
        cli.main(["home.xml", "sc.yaml", "--json", "--overview"])


def test_cli_json_prints_valid_json(tmp_path, capsys):
    import json
    home = tmp_path / "Home.xml"
    home.write_text(FIXTURE_ROOMS)
    sc = tmp_path / "sc.yaml"
    sc.write_text(SIDECAR)
    cli.main([str(home), str(sc), "--json"])
    data = json.loads(capsys.readouterr().out)
    assert data["heating"]["total_btuh"] > 0
    assert isinstance(data["rooms"], list)
