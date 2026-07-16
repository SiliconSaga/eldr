from eldr import report, loads, sidecar


def test_render_heating_contains_totals():
    sc = sidecar.SideCar(
        assemblies={"exterior_wall": 0.1},
        design=sidecar.DesignConditions(70, 20, 50),
        infiltration_ach=0.5,
    )
    r = loads.HeatingResult(conduction_btuh=6500.0, infiltration_btuh=5400.0,
                            total_btuh=11900.0, cfm=220.4,
                            by_category={"exterior_wall": 6500.0})
    md = report.render_heating(r, sc)
    assert "# Eldr — Heating Load" in md
    assert "11,900" in md          # total, thousands-separated
    assert "220" in md             # CFM
    assert "exterior_wall" in md
    assert "ΔT" in md and "50" in md
