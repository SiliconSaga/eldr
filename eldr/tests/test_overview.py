import textwrap
from eldr import overview

FIXTURE = textwrap.dedent("""\
<?xml version='1.0'?>
<home version='7400' name='t' wallHeight='300'>
  <compass x='0' y='0' diameter='100' latitude='0.7105963' longitude='-1.2916551'/>
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
cooling:
  indoor_f: 75
  outdoor_1_f: 90
  shgc: 0.35
  occupants: 3
""")


def _write(tmp_path, name, body):
    p = tmp_path / name
    p.write_text(body)
    return str(p)


def test_overview_frames_and_embeds_numbers(tmp_path):
    home = _write(tmp_path, "Home.xml", FIXTURE)
    sc = _write(tmp_path, "sc.yaml", SIDECAR)
    md = overview.render_overview(home, sc)
    # single top-level title, static framing, and the honesty + roadmap sections
    assert md.startswith("# Eldr — Manual J / S / D from a 3D model")
    assert "## The ACCA chain, implemented" in md
    assert "## How detailed it gets" in md
    assert "## What's demo-grade today" in md
    assert "roadmap" in md.lower()
    # the engine's numbers are embedded, and the report's own H1 was demoted away
    assert "**Supply airflow:**" in md
    assert "Manual D — Duct Sizing" in md
    assert "# Eldr — Heating Load" not in md


def test_overview_buffer_and_no_extra_unit_note(tmp_path):
    # tagging a wall buffer surfaces the buffer honesty + factor; a unit IS placed so
    # the "no air handler" caveat must NOT appear
    home = _write(tmp_path, "Home.xml", FIXTURE)
    sc = _write(tmp_path, "sc.yaml", SIDECAR + "walls:\n  w-int: {boundary: buffer}\n")
    md = overview.render_overview(home, sc)
    assert "Buffer walls" in md and "50%" in md
    assert "No air handler placed" not in md
