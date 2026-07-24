import pytest
from eldr import report, loads, sidecar, sizing, ductd, ductmodel, geometry


def _sc(existing_tons=None):
    return sidecar.SideCar(
        assemblies={"exterior_wall": 0.1},
        design=sidecar.DesignConditions(70, 20, 50),
        infiltration_ach=0.5,
        existing_tons=existing_tons,
    )


def _sc_cool():
    return sidecar.SideCar(
        assemblies={"exterior_wall": 0.1},
        design=sidecar.DesignConditions(70, 20, 50),
        infiltration_ach=0.5,
        cooling=sidecar.Cooling(indoor_f=75, outdoor_1_f=90, shgc=0.35, occupants=3),
    )


def _result():
    return loads.HeatingResult(conduction_btuh=6500.0, infiltration_btuh=5400.0,
                               total_btuh=11900.0, cfm=220.4,
                               by_category={"exterior_wall": 6500.0})


def _cooling():
    return loads.CoolingResult(sensible_btuh=20000.0, latent_btuh=4000.0, total_btuh=24000.0,
                               cfm=900.0, by_category={"window": 500.0, "solar-W": 1200.0,
                                                       "internal": 1890.0})


def test_render_heating_contains_totals():
    md = report.render_heating(_result(), _sc())
    assert "# Eldr — Heating Load" in md
    assert "11,900" in md          # total, thousands-separated
    assert "220" in md             # CFM
    assert "exterior_wall" in md
    assert "ΔT" in md and "50" in md


def test_render_with_sizing_shows_manual_s():
    r = _result()
    s = sizing.size_equipment(r.total_btuh, _sc(existing_tons=4.0))
    md = report.render_heating(r, _sc(existing_tons=4.0), sizing=s)
    assert "## Manual S — Equipment Sizing" in md
    assert "tons" in md
    assert "4.0" in md              # existing unit
    assert "oversized" in md
    assert "⚠" in md               # warning for non-well-matched verdict


def test_render_without_sizing_omits_manual_s():
    md = report.render_heating(_result(), _sc())
    assert "Manual S" not in md


def test_render_with_ducts_shows_manual_d():
    dr = ductd.size_ducts([("main trunk", 720), ("bigtrunk", 2000)], friction_rate=0.08)
    md = report.render_heating(_result(), _sc(), ducts=dr)
    assert "## Manual D — Duct Sizing" in md
    assert "main trunk" in md
    assert "14″" in md               # 720 CFM -> 14" standard
    assert "⚠" in md                # bigtrunk exceeds the velocity threshold


def test_render_without_ducts_omits_manual_d():
    md = report.render_heating(_result(), _sc())
    assert "Manual D" not in md


def test_render_with_cooling_shows_section():
    r, c = _result(), _cooling()
    s = sizing.size_equipment(r.total_btuh, _sc_cool(), cooling_btuh=c.total_btuh)
    md = report.render_heating(r, _sc_cool(), sizing=s, cooling=c)
    assert "Cooling Load" in md
    assert "solar-W" in md          # orientation-resolved solar line
    assert "sensible" in md and "latent" in md
    # cooling (24k) > heating (11.9k) -> Manual S sizes on cooling
    assert "cooling" in md


def test_render_without_cooling_omits_section():
    md = report.render_heating(_result(), _sc())
    assert "Cooling Load" not in md


def test_render_cooling_without_conditions_errors():
    # a CoolingResult but a side-car with no cooling block -> clear error, not AttributeError
    with pytest.raises(ValueError, match="cooling"):
        report.render_heating(_result(), _sc(), cooling=_cooling())


def _plan(unit=None, derived=False, lengths=None, runs=None):
    room_loads = [
        loads.RoomLoad("Big Room", "L1", True, 6000.0, 2000.0, 111.0),
        loads.RoomLoad("Small", "L1", True, 500.0, 100.0, 9.0),
        loads.RoomLoad("Garage", "LG", False, 3000.0, 0.0, 55.0),   # excluded from table
    ]
    return ductmodel.DuctPlan(
        unit=unit, unit_name="air handler", friction_rate=0.08, derived=derived,
        available_static_pressure=0.5 if derived else None,
        worst_length_ft=70.0 if derived else None, room_loads=room_loads,
        runs=runs or [("main trunk", 120.0), ("Big Room", 111.0), ("Small", 9.0)],
        lengths=lengths)


def test_render_per_room_section():
    md = report.render_heating(_result(), _sc(), duct_plan=_plan())
    assert "Per-Room Loads (Manual J 1c)" in md
    assert "Big Room" in md
    assert "Garage" not in md            # unconditioned room excluded from the 1c table
    assert "220 CFM" in md               # whole-house cfm from the HeatingResult, in the gap note


def test_render_manual_d_without_unit_notes_absence():
    dr = ductd.size_ducts([("main trunk", 120), ("Big Room", 111)], friction_rate=0.08)
    md = report.render_heating(_result(), _sc(), ducts=dr, duct_plan=_plan())
    assert "## Manual D" in md
    assert "No unit found" in md
    assert "not derived" in md
    assert "Length" not in md            # no length column without a unit


def test_render_manual_d_with_unit_shows_lengths_and_derivation():
    unit = geometry.Furniture("Air Handler", 0.0, 0.0, "L1")
    dr = ductd.size_ducts([("main trunk", 120), ("Big Room", 111)], friction_rate=0.08,
                          lengths=[None, 42.0])
    md = report.render_heating(_result(), _sc(), ducts=dr,
                               duct_plan=_plan(unit=unit, derived=True, lengths=[None, 42.0]))
    assert "Air handler" in md
    assert "Length" in md and "Drop" in md
    assert "42 ft" in md
    assert "derived" in md               # friction rate note shows the derivation
