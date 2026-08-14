import json
import textwrap
import pytest
from eldr import cli, jsonexport, loads
from eldr.tests.fixtures import MULTI_LEVEL_FIXTURE, VOID_BELOW_FIXTURE

FIXTURE = textwrap.dedent("""\
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

COOLING = "cooling:\n  indoor_f: 75\n  outdoor_1_f: 90\n  shgc: 0.35\n  occupants: 3\n"


def _paths(tmp_path, sidecar_body):
    home = tmp_path / "Home.xml"
    home.write_text(FIXTURE)
    sc = tmp_path / "sc.yaml"
    sc.write_text(sidecar_body)
    return str(home), str(sc)


def test_json_structure_and_roundtrip(tmp_path):
    home, sc = _paths(tmp_path, SIDECAR + COOLING)
    text = jsonexport.render_json(cli.analyze(home, sc))
    data = json.loads(text)   # valid JSON
    assert {"design", "station", "infiltration_ach", "heating", "cooling",
            "equipment_sizing", "rooms", "ducts",
            "levels", "spaces", "voids", "surfaces"} <= data.keys()
    assert data["design"]["heating_delta_t_f"] == 55       # 70 - 15
    assert data["heating"]["by_category"]["exterior_wall"] > 0
    names = [r["name"] for r in data["rooms"]]
    assert "West" in names and "East" in names
    assert data["cooling"] is not None
    assert data["ducts"]["unit_placed"] is True
    assert any(run["name"] == "main trunk" for run in data["ducts"]["runs"])


def test_json_numbers_match_engine(tmp_path):
    # the JSON must carry the engine's exact values (that's the whole point)
    home, sc = _paths(tmp_path, SIDECAR + COOLING)
    a = cli.analyze(home, sc)
    data = jsonexport.analysis_to_dict(a)
    engine = {r.name: r for r in loads.per_room_loads(a.env, a.sc)}
    west = next(r for r in data["rooms"] if r["name"] == "West")
    assert west["heating_btuh"] == engine["West"].heating_btuh
    assert west["cooling_sensible_btuh"] == engine["West"].cooling_btuh
    assert data["heating"]["total_btuh"] == a.heating.total_btuh


def test_json_cooling_carries_the_sensible_infiltration_term(tmp_path):
    """The export is the machine-readable twin of the report, so the cooling block must
    carry `infiltration_btuh` beside `sensible_btuh` exactly as the heating block already
    carries it — otherwise a consumer can see the term in the rendered table and not in
    the data, and the two renderings disagree about what the load is made of.

    The value is checked against the physics (1.08 x CFM x the SUMMER ΔT of 15°F), not
    just against the engine attribute: comparing the export to the engine would pass on
    any number the engine happened to produce, including the winter ΔT's.
    """
    home, sc = _paths(tmp_path, SIDECAR + COOLING)
    a = cli.analyze(home, sc)
    data = jsonexport.analysis_to_dict(a)
    infil_cfm = a.sc.infiltration_ach * a.env.volume_ft3 / 60.0
    assert data["cooling"]["infiltration_btuh"] == pytest.approx(1.08 * infil_cfm * 15.0)
    assert data["cooling"]["infiltration_btuh"] == a.cooling.infiltration_btuh
    # and it is a real part of sensible, not a decorative extra
    assert data["cooling"]["sensible_btuh"] > data["cooling"]["infiltration_btuh"] > 0
    # the winter term is a different number and lives in its own block
    assert data["heating"]["infiltration_btuh"] == pytest.approx(1.08 * infil_cfm * 55.0)


def test_json_cooling_null_without_block(tmp_path):
    home, sc = _paths(tmp_path, SIDECAR)   # no cooling block
    data = jsonexport.analysis_to_dict(cli.analyze(home, sc))
    assert data["cooling"] is None
    assert data["station"] is None         # outdoor temps set -> no station lookup


# The multi-level fixture carries a `Basement` level, whose walls are their own category.
STACK_SIDECAR = SIDECAR.replace("  window: 0.30\n", "  basement_wall: 0.20\n  window: 0.30\n")

# Two spaces with four deliberately unequal factors, none of them 0.5 (the unvented
# default), 1.0 (the vented shorthand) or a design ΔT:
#   attic     winter 26°F -> (26-70)/(15-70) = 0.80; summer undeclared -> the engine
#             substitutes the sol-air attic temp, 90 + 50*0.85 = 132.5°F -> 3.83
#   crawlspace winter 48°F -> 0.40; summer 84°F -> (84-75)/(90-75) = 0.60
# So an export that read heating where it meant cooling, resolved the cooling factor from
# the DECLARED policy (0.50 for the attic), applied the hot-attic substitution to every
# space, or keyed either dict wrongly, lands on a number no assertion here accepts.
STACK_SPACES = textwrap.dedent("""\
spaces:
  attic:
    winter_temp_f: 26
  crawlspace:
    winter_temp_f: 48
    summer_temp_f: 84
""")

# The attic half on its own, for the fixture that has no crawlspace surface to bind to.
# `MULTI_LEVEL_FIXTURE` puts Main exactly over the Basement, so nothing faces a crawl —
# and `loads` now warns about a `spaces:` key that binds to nothing, which would be a
# true statement about a side-car this test does not mean to make.
ATTIC_SPACE_ONLY = textwrap.dedent("""\
spaces:
  attic:
    winter_temp_f: 26
""")


def _stack_paths(tmp_path, fixture, sidecar_body):
    home = tmp_path / "Home.xml"
    home.write_text(fixture)
    sc = tmp_path / "sc.yaml"
    sc.write_text(sidecar_body)
    return str(home), str(sc)


def test_json_carries_levels_spaces_voids_and_surfaces(tmp_path):
    """The horizontal split and the policies behind it, inspectable without re-deriving."""
    home, sc = _stack_paths(tmp_path, VOID_BELOW_FIXTURE,
                            STACK_SIDECAR + COOLING + STACK_SPACES)
    a = cli.analyze(home, sc)
    payload = json.loads(jsonexport.render_json(a))

    # every level, keyed by name, with the height that level actually used. The three
    # differ (200 / 300 / 250 cm), so a map that lost its keying can't read correct.
    assert set(payload["levels"]) == {"Basement", "Garage", "Main"}
    assert payload["levels"]["Main"]["height_ft"] == pytest.approx(8.2021, abs=1e-3)
    assert payload["levels"]["Basement"]["height_ft"] == pytest.approx(6.5617, abs=1e-3)
    assert payload["levels"] == {
        name: {"height_ft": pytest.approx(h)} for name, h in a.env.level_heights_ft.items()}

    # both spaces a surface faces, and only those
    assert set(payload["spaces"]) == {"attic", "crawlspace"}
    assert payload["spaces"]["attic"]["heating_factor"] == pytest.approx(0.80)
    assert payload["spaces"]["attic"]["cooling_factor"] == pytest.approx(3.8333, abs=1e-4)
    assert payload["spaces"]["crawlspace"]["heating_factor"] == pytest.approx(0.40)
    assert payload["spaces"]["crawlspace"]["cooling_factor"] == pytest.approx(0.60)

    # the void, itemized by room exactly as the report warns about it — area AND the
    # category that gap resolved to, so a consumer never has to infer the second from the
    # surfaces array (which answers a different question on a multi-`below_void` model).
    assert payload["voids"] == {"Living room": {
        "area_ft2": pytest.approx(a.env.voids["Living room"].area_ft2),
        "category": "buffer_floor"}}
    assert payload["voids"]["Living room"]["area_ft2"] > 60.0

    # the surfaces array, with the space each horizontal faces. Categories compared whole:
    # `floor` is a substring of `buffer_floor`, and they are different boundaries.
    assert len(payload["surfaces"]) == len(a.env.surfaces)
    assert {(s["category"], s["space"]) for s in payload["surfaces"]} == {
        ("exterior_wall", None), ("basement_wall", None), ("floor", None),
        ("buffer_floor", "crawlspace"), ("ceiling", "attic")}
    ceiling = next(s for s in payload["surfaces"] if s["category"] == "ceiling")
    assert ceiling["area_ft2"] == pytest.approx(
        sum(s.area_ft2 for s in a.env.surfaces if s.category == "ceiling"))
    # An independent anchor, not a second reading of the same envelope: the Living room
    # is 400 x 300 cm = 120,000 cm² = 129.17 ft², and its floor is exactly half of that
    # over the shrunken Basement. Without a literal here, an export that scaled or
    # truncated every area would satisfy every other assertion in this file.
    assert ceiling["area_ft2"] == pytest.approx(129.17, abs=0.01)
    void_floor = next(s for s in payload["surfaces"] if s["category"] == "buffer_floor")
    assert void_floor["area_ft2"] == pytest.approx(64.58, abs=0.01)


def test_json_attic_cooling_factor_is_the_applied_one_not_the_declared_one(tmp_path):
    """The single trap in this export. `spaces.policy_for` returns what the side-car
    DECLARED; for an attic with no `summer_temp_f` that resolves to the unvented 0.5,
    while `loads.effective_cooling_policy` substitutes the hot-attic temperature the
    engine actually loaded the ceiling at. Here `cooling.attic_temp_f` pins that at
    123°F -> (123-75)/(90-75) = 3.2, a number the declared policy cannot produce.
    """
    body = (STACK_SIDECAR + COOLING + "  attic_temp_f: 123\n"
            + "spaces:\n  attic:\n    winter_temp_f: 26\n")
    home, sc = _stack_paths(tmp_path, MULTI_LEVEL_FIXTURE, body)
    a = cli.analyze(home, sc)
    payload = jsonexport.analysis_to_dict(a)
    assert payload["spaces"]["attic"]["cooling_factor"] == pytest.approx(3.2)
    # and the winter factor genuinely IS the declared policy's — only summer is substituted
    assert payload["spaces"]["attic"]["heating_factor"] == pytest.approx(0.80)


def test_json_voids_present_but_empty_when_nothing_is_undrawn(tmp_path):
    """A consumer must be able to tell "no gaps" from "an export predating the key"."""
    home, sc = _stack_paths(tmp_path, MULTI_LEVEL_FIXTURE, STACK_SIDECAR + COOLING)
    payload = jsonexport.analysis_to_dict(cli.analyze(home, sc))
    assert payload["voids"] == {}
    # Main sits exactly over Basement, so that floor face is interior and emits nothing
    assert not any(s["category"] == "buffer_floor" for s in payload["surfaces"])


def test_json_cooling_factor_is_null_without_a_cooling_block(tmp_path):
    """A heating-only side-car has no summer to resolve — null, not a fabricated factor."""
    home, sc = _stack_paths(tmp_path, MULTI_LEVEL_FIXTURE, STACK_SIDECAR + ATTIC_SPACE_ONLY)
    payload = jsonexport.analysis_to_dict(cli.analyze(home, sc))
    assert payload["spaces"]["attic"]["cooling_factor"] is None
    assert payload["spaces"]["attic"]["heating_factor"] == pytest.approx(0.80)
