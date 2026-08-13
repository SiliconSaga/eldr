import pytest
from eldr import report, loads, sidecar, sizing, ductd, ductmodel, geometry, spaces


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


def test_render_shows_buffer_factor_when_present():
    r = loads.HeatingResult(conduction_btuh=1000.0, infiltration_btuh=500.0,
                            total_btuh=1500.0, cfm=30.0,
                            by_category={"exterior_wall": 700.0, "buffer_wall": 300.0})
    md = report.render_heating(r, _sc())
    assert "Buffer walls" in md
    assert "50%" in md             # BUFFER_FACTOR, prominent
    assert "buffer_wall" in md


def test_render_no_buffer_line_when_absent():
    assert "Buffer walls" not in report.render_heating(_result(), _sc())


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


# ---------------------------------------------------------------- assumptions echoes

def _env(surfaces=(), voids=None, level_heights=None, level_volumes=None):
    return geometry.Envelope(
        surfaces=list(surfaces), volume_ft3=10000.0,
        voids=dict(voids or {}), level_heights_ft=dict(level_heights or {}),
        level_volumes_ft3=dict(level_volumes or {}))


def _sc_attic(attic_temp_f=None, space_policies=None):
    """A side-car with cooling, so the summer column resolves. ΔT = 91-75 = 16°F."""
    return sidecar.SideCar(
        assemblies={"exterior_wall": 0.1, "ceiling": 0.03},
        design=sidecar.DesignConditions(70, 20, 50),
        infiltration_ach=0.5,
        cooling=sidecar.Cooling(indoor_f=75, outdoor_1_f=91, shgc=0.35, occupants=3,
                                attic_temp_f=attic_temp_f),
        spaces=dict(space_policies or {}),
    )


def _row(md, first_cell):
    """The cells of the one Markdown table row whose first cell is `first_cell`.

    Cell-level, not substring: the winter and summer factor columns can legitimately
    hold the same number, so `"0.50" in md` cannot tell "the report echoed the declared
    policy" apart from "the report echoed the policy the engine applied".
    """
    line = next(l for l in md.splitlines() if l.startswith(f"| {first_cell} |"))
    return [c.strip() for c in line.split("|")]


def test_report_warns_about_void_floor_area_by_room():
    env = _env(voids={"Main Bed": 120.1, "Kitchen": 12.8})
    md = report.render_heating(_result(), _sc(), env=env)
    assert "no level drawn beneath" in md
    assert "Main Bed" in md and "120.1" in md
    assert "132.9" in md                      # the total, thousands-separated if needed
    # ranked by area, biggest first — not model/dict order
    assert md.index("Main Bed") < md.index("Kitchen")


def test_report_omits_the_void_warning_when_there_are_none():
    md = report.render_heating(_result(), _sc(), env=_env())
    assert "no level drawn beneath" not in md


def test_report_echoes_buffer_space_factors():
    sc = sidecar.SideCar(
        assemblies={"exterior_wall": 0.1, "buffer_floor": 0.5},
        design=sidecar.DesignConditions(70, 13, 50),
        infiltration_ach=0.5,
        spaces={"crawlspace": spaces.SpacePolicy("crawlspace", winter_temp_f=32.0)},
    )
    env = _env(surfaces=[geometry.Surface("buffer_floor", 100.0, "crawlspace")])
    md = report.render_heating(_result(), sc, env=env)
    assert "crawlspace" in md
    assert "32" in md            # the temperature the factor came from
    assert "0.67" in md          # (70-32)/(70-13), rounded for display
    # and it is the WINTER cell that carries them, not some other 0.67 in the document
    cells = _row(md, "crawlspace")
    assert cells[2] == "0.67 × ΔT"
    assert "32°F" in cells[3] and "winter_temp_f" in cells[3]


def test_report_echoes_level_heights():
    env = _env(level_heights={"Main": 8.0})
    md = report.render_heating(_result(), _sc(), env=env)
    assert "Main" in md and "8.0" in md
    assert _row(md, "Main")[2] == "8.0 ft"


def test_report_renders_the_cooling_factor_the_engine_applied_not_the_declared_one():
    """The trap this whole task turns on.

    `spaces.policy_for("attic", {})` is the bare unvented default, whose cooling factor is
    0.50 — the same number the winter column legitimately shows — while loads.py is
    meanwhile loading the ceiling at (130-75)/16 = 3.44. A report resolved through
    `spaces.cooling_factor` alone would be confidently wrong AND would still contain the
    string "0.50", so only the summer CELL can tell the two implementations apart.
    """
    env = _env(surfaces=[geometry.Surface("ceiling", 100.0, "attic")])
    md = report.render_heating(_result(), _sc_attic(attic_temp_f=130.0), env=env)
    cells = _row(md, "attic")
    assert cells[2] == "0.50 × ΔT"          # winter: the declared policy, correctly
    assert cells[4] == "3.44 × ΔT"          # summer: what loads.py actually applied
    assert "130" in cells[5] and "attic_temp_f" in cells[5]


def test_report_names_the_attic_temperature_and_calls_the_estimate_an_estimate():
    """133.5°F is the least obvious number in the engine — an outdoor temp plus a flat
    solar uplift. Printing only the factor would leave it invisible."""
    env = _env(surfaces=[geometry.Surface("ceiling", 100.0, "attic")])
    md = report.render_heating(_result(), _sc_attic(), env=env)
    cells = _row(md, "attic")
    assert cells[4] == "3.66 × ΔT"          # (133.5-75)/16, the sol-air estimate
    assert "133.5" in cells[5]
    assert "sol-air" in cells[5]


def test_report_buffer_block_survives_a_heating_only_side_car():
    """`cooling:` is optional. Unguarded, `effective_cooling_policy` crashed on the attic
    and only the attic, so this passes on every other space while the report is broken."""
    env = _env(surfaces=[geometry.Surface("ceiling", 100.0, "attic"),
                         geometry.Surface("buffer_floor", 100.0, "crawlspace")])
    md = report.render_heating(_result(), _sc(), env=env)
    assert _row(md, "attic")[2] == "0.50 × ΔT"
    assert _row(md, "attic")[4] == "—"
    assert _row(md, "crawlspace")[4] == "—"


def test_report_omits_the_buffer_space_block_when_no_surface_faces_one():
    env = _env(surfaces=[geometry.Surface("exterior_wall", 100.0)])
    md = report.render_heating(_result(), _sc(), env=env)
    assert "Buffer spaces" not in md


def test_report_marks_a_side_car_height_override_apart_from_a_model_height():
    sc = sidecar.SideCar(
        assemblies={"exterior_wall": 0.1},
        design=sidecar.DesignConditions(70, 20, 50),
        infiltration_ach=0.5,
        levels={"Crawlspace": sidecar.LevelSpec(height_ft=4.0)},
    )
    env = _env(level_heights={"Main": 8.0, "Crawlspace": 4.0})
    md = report.render_heating(_result(), sc, env=env)
    assert _row(md, "Main")[3] == "model"
    assert _row(md, "Crawlspace")[3] == "side-car override"


def test_report_shows_the_volume_each_level_contributes_not_the_house_total():
    """A level that holds no conditioned rooms contributes nothing, and saying so is the
    point of the column. Echoing `volume_ft3` on every row would look plausible."""
    env = _env(level_heights={"Main": 8.0, "Garage": 10.0},
               level_volumes={"Main": 3000.0, "Garage": 0.0})
    md = report.render_heating(_result(), _sc(), env=env)
    assert _row(md, "Main")[4] == "3,000 ft³"
    assert _row(md, "Garage")[4] == "0 ft³"


def test_report_renders_none_of_the_assumption_blocks_without_an_envelope():
    md = report.render_heating(_result(), _sc())
    assert "Level heights" not in md
    assert "Buffer spaces" not in md
    assert "no level drawn beneath" not in md
