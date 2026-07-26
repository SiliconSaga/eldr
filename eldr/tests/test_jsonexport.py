import json
import textwrap
from eldr import cli, jsonexport, loads

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
            "equipment_sizing", "rooms", "ducts"} <= data.keys()
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


def test_json_cooling_null_without_block(tmp_path):
    home, sc = _paths(tmp_path, SIDECAR)   # no cooling block
    data = jsonexport.analysis_to_dict(cli.analyze(home, sc))
    assert data["cooling"] is None
    assert data["station"] is None         # outdoor temps set -> no station lookup
