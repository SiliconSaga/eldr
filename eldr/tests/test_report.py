from eldr import report, loads, sidecar, sizing


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
