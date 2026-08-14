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


# Two levels where the upper one overhangs the lower by 600cm — the overhang has nothing
# drawn beneath it, which is the schematic gap the void warning exists to name. The lower
# level is NOT called "Basement": that name would turn its walls into `basement_wall`, a
# category SIDECAR deliberately doesn't carry. The two `elevationIndex` values are distinct
# and match the stack order, as Sweet Home 3D writes them: the ordering here holds on
# elevation alone, so equal indices would have made the fixture unlike real model output
# while looking correct.
VOID_FIXTURE = textwrap.dedent("""\
<?xml version='1.0'?>
<home version='7400' name='t' wallHeight='300'>
  <compass x='0' y='0' diameter='100' latitude='0.7105963' longitude='-1.2916551'/>
  <level id='LL' name='Lower' elevation='0.0' floorThickness='12.0' height='200' elevationIndex='0'/>
  <level id='LM' name='Main' elevation='212.0' floorThickness='12.0' height='250' elevationIndex='1'/>
  <wall id='l-n' level='LL' xStart='0' yStart='0' xEnd='400' yEnd='0' height='200' thickness='10'/>
  <wall id='l-s' level='LL' xStart='0' yStart='300' xEnd='400' yEnd='300' height='200' thickness='10'/>
  <wall id='l-w' level='LL' xStart='0' yStart='0' xEnd='0' yEnd='300' height='200' thickness='10'/>
  <wall id='l-e' level='LL' xStart='400' yStart='0' xEnd='400' yEnd='300' height='200' thickness='10'/>
  <wall id='m-n' level='LM' xStart='0' yStart='0' xEnd='1000' yEnd='0' height='250' thickness='10'/>
  <wall id='m-s' level='LM' xStart='0' yStart='300' xEnd='1000' yEnd='300' height='250' thickness='10'/>
  <wall id='m-w' level='LM' xStart='0' yStart='0' xEnd='0' yEnd='300' height='250' thickness='10'/>
  <wall id='m-e' level='LM' xStart='1000' yStart='0' xEnd='1000' yEnd='300' height='250' thickness='10'/>
  <room id='rl' level='LL' name='Den'>
    <point x='0' y='0'/><point x='400' y='0'/><point x='400' y='300'/><point x='0' y='300'/>
  </room>
  <room id='rm' level='LM' name='Great Room'>
    <point x='0' y='0'/><point x='1000' y='0'/><point x='1000' y='300'/><point x='0' y='300'/>
  </room>
</home>
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


def _row(md, first_cell):
    line = next(l for l in md.splitlines() if l.startswith(f"| {first_cell} |"))
    return [c.strip() for c in line.split("|")]


def test_overview_mirrors_the_level_and_space_assumptions(tmp_path):
    """The narrative renders from the same cli.analyze pipeline, so the assumption blocks
    must arrive with the numbers rather than being re-stated in prose beside them."""
    home = _write(tmp_path, "Home.xml", FIXTURE)
    sc = _write(tmp_path, "sc.yaml", SIDECAR)
    md = overview.render_overview(home, sc)
    assert "Level heights" in md
    assert _row(md, "Main")[2] == "9.8 ft"        # 300cm, straight off the SH3D level
    assert "Buffer spaces" in md
    # sol-air 132.5°F against the 15°F cooling ΔT -> 3.83, NOT the declared default's 0.50
    attic = _row(md, "attic")
    assert attic[2] == "0.50 → 27.5°F"       # 55°F heating ΔT
    assert attic[4] == "3.83 → 57.5°F"       # 15°F cooling ΔT — not 3.83 x 55
    assert "132.5" in attic[5] and "sol-air" in attic[5]


def test_overview_carries_the_void_warning_into_both_report_and_caveats(tmp_path):
    home = _write(tmp_path, "Home.xml", VOID_FIXTURE)
    sc = _write(tmp_path, "sc.yaml", SIDECAR)
    md = overview.render_overview(home, sc)
    assert "no level drawn beneath" in md
    assert "Great Room" in md
    honesty = md.split("## What's demo-grade today")[1]
    assert "drawn beneath" in honesty
    # the void floor's U-value is borrowed too, and that is the larger of the two errors
    assert "### Borrowed assembly U-values" in md
    assert "borrowed, not declared" in honesty and "`buffer_floor`" in honesty


def test_overview_omits_the_void_caveat_when_the_model_has_no_gap(tmp_path):
    home = _write(tmp_path, "Home.xml", FIXTURE)
    sc = _write(tmp_path, "sc.yaml", SIDECAR)
    md = overview.render_overview(home, sc)
    assert "drawn beneath" not in md


def _honesty(tmp_path, sidecar_body, fixture=VOID_FIXTURE):
    home = _write(tmp_path, "Home.xml", fixture)
    sc = _write(tmp_path, "sc.yaml", sidecar_body)
    return overview.render_overview(home, sc).split("## What's demo-grade today")[1]


def test_borrowed_bullet_inflects_both_verbs_for_one_category(tmp_path):
    """The bullet carries TWO verbs, "has" and "stands in", and only the first was being
    inflected — so a single borrowed category read "`buffer_floor` has no `assemblies`
    entry and stand in on ...". Asserting on the leading verb alone would not have caught
    it, which is how it shipped."""
    honesty = _honesty(tmp_path, SIDECAR)
    assert "`buffer_floor` has no `assemblies` entry and stands in on a related" in honesty


def test_void_caveat_names_no_category_when_the_envelope_cannot_supply_one(tmp_path):
    """The sibling-module half of the void-category fix.

    `report` deliberately makes no treatment claim when it cannot name the category, but
    this bullet kept an `or "buffer floor"` fallback — the exact string the fix removed.
    It is reachable: `below_void` is a free string and `below_void: ground` resolves the
    void to a ground-coupled `floor`, so the bullet claimed a buffer at half the ΔT for
    an area the engine had coupled to soil. The gap is still reported; only the
    unsupportable clause goes."""
    honesty = _honesty(tmp_path, SIDECAR + "levels:\n  Main:\n    below_void: ground\n")
    bullet = next(l for l in honesty.splitlines() if "drawn beneath it" in l)
    assert "buffer floor" not in bullet and "`buffer_floor`" not in bullet
    assert "modeled as" not in bullet
    assert "gap in the drawing" in bullet          # the caveat itself still stands


def test_void_caveat_names_the_category_when_the_envelope_does_supply_one(tmp_path):
    """The other branch, so the test above cannot be satisfied by dropping the clause
    unconditionally — which would lose a real disclosure on every normal model."""
    honesty = _honesty(tmp_path, SIDECAR)
    bullet = next(l for l in honesty.splitlines() if "drawn beneath it" in l)
    assert "modeled as `buffer_floor` over undrawn space" in bullet


def test_borrowed_bullet_inflects_both_verbs_for_several_categories(tmp_path):
    """Tagging a wall `buffer` adds a second borrowed category (`buffer_wall` borrows
    `exterior_wall`), so the same sentence must go plural in both places at once."""
    honesty = _honesty(tmp_path, SIDECAR + "walls:\n  m-w: {boundary: buffer}\n")
    assert "`buffer_floor`, `buffer_wall` have no `assemblies` entry and stand in" in honesty
    assert "stands in" not in honesty      # "stand in" is not a substring of "stands in"
