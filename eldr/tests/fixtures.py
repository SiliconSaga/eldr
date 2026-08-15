"""Model fixtures shared by more than one test module.

A fixture lives here once a second module needs it. `MULTI_LEVEL_FIXTURE` is the
stack-resolution workhorse — three levels whose footprints deliberately disagree — and
both the geometry tests and the JSON-export tests drive it end to end. Copying it a
third time is the point at which duplication stops being cheaper than a module.
"""
import textwrap

# Three levels with deliberately different footprints and storey heights:
#   Basement (LB) 0-400 x 0-300 @ elevation 0, height 200cm — conditioned
#   Garage   (LG) 500-900 x 0-300 @ elevation 0, height 300cm — unconditioned by name
#   Main     (LM) 0-400 x 0-300 @ elevation 212, height 250cm — conditioned
# Main sits exactly over Basement (so its floor is interior and there is no void), the
# Garage overlaps Main's vertical span but not its footprint (so it is not adjacency),
# and Main's ceiling faces the undrawn `attic`. The three heights are all different, so
# a level map keyed or looked up wrongly cannot coincidentally read correct.
MULTI_LEVEL_FIXTURE = textwrap.dedent("""\
<?xml version='1.0'?>
<home version='7400' name='t' wallHeight='300'>
  <level id='LB' name='Basement' elevation='0.0' floorThickness='12.0' height='200' elevationIndex='0'/>
  <level id='LG' name='Garage' elevation='0.0' floorThickness='12.0' height='300' elevationIndex='1'/>
  <level id='LM' name='Main' elevation='212.0' floorThickness='12.0' height='250' elevationIndex='0'/>
  <wall id='b-n' level='LB' xStart='0' yStart='0' xEnd='400' yEnd='0' height='200' thickness='10'/>
  <wall id='b-s' level='LB' xStart='0' yStart='300' xEnd='400' yEnd='300' height='200' thickness='10'/>
  <wall id='b-w' level='LB' xStart='0' yStart='0' xEnd='0' yEnd='300' height='200' thickness='10'/>
  <wall id='b-e' level='LB' xStart='400' yStart='0' xEnd='400' yEnd='300' height='200' thickness='10'/>
  <wall id='g-n' level='LG' xStart='500' yStart='0' xEnd='900' yEnd='0' height='300' thickness='10'/>
  <wall id='g-s' level='LG' xStart='500' yStart='300' xEnd='900' yEnd='300' height='300' thickness='10'/>
  <wall id='g-w' level='LG' xStart='500' yStart='0' xEnd='500' yEnd='300' height='300' thickness='10'/>
  <wall id='g-e' level='LG' xStart='900' yStart='0' xEnd='900' yEnd='300' height='300' thickness='10'/>
  <wall id='m-n' level='LM' xStart='0' yStart='0' xEnd='400' yEnd='0' height='250' thickness='10'/>
  <wall id='m-s' level='LM' xStart='0' yStart='300' xEnd='400' yEnd='300' height='250' thickness='10'/>
  <wall id='m-w' level='LM' xStart='0' yStart='0' xEnd='0' yEnd='300' height='250' thickness='10'/>
  <wall id='m-e' level='LM' xStart='400' yStart='0' xEnd='400' yEnd='300' height='250' thickness='10'/>
  <room id='rb' level='LB' name='Basement Room'>
    <point x='0' y='0'/><point x='400' y='0'/><point x='400' y='300'/><point x='0' y='300'/>
  </room>
  <room id='rg' level='LG' name='Garage'>
    <point x='500' y='0'/><point x='900' y='0'/><point x='900' y='300'/><point x='500' y='300'/>
  </room>
  <room id='rm' level='LM' name='Living room'>
    <point x='0' y='0'/><point x='400' y='0'/><point x='400' y='300'/><point x='0' y='300'/>
  </room>
</home>
""")

# The same model with the Basement room shrunk to the western half of its level, so the
# eastern half of Main's floor has nothing drawn beneath it at all. That is the void
# case: Main keeps its full footprint, the Garage still does not overlap it, and the
# uncovered strip becomes `buffer_floor` over the undrawn space plus a reported void.
VOID_BELOW_FIXTURE = MULTI_LEVEL_FIXTURE.replace(
    "<point x='0' y='0'/><point x='400' y='0'/><point x='400' y='300'/><point x='0' y='300'/>\n"
    "  </room>\n"
    "  <room id='rg'",
    "<point x='0' y='0'/><point x='200' y='0'/><point x='200' y='300'/><point x='0' y='300'/>\n"
    "  </room>\n"
    "  <room id='rg'")
