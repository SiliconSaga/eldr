import pytest
from eldr import loads, geometry, sidecar


def _sc():
    return sidecar.SideCar(
        assemblies={"exterior_wall": 0.1, "window": 0.3},
        design=sidecar.DesignConditions(indoor_heating_f=70, outdoor_heating_99_f=20,
                                        supply_air_rise_f=50),
        infiltration_ach=0.5,
    )


def test_heating_load_math():
    env = geometry.Envelope(
        surfaces=[geometry.Surface("exterior_wall", 1000.0),
                  geometry.Surface("window", 100.0)],
        volume_ft3=12000.0,
    )
    r = loads.heating_load(env, _sc())
    dt = 50.0
    conduction = 0.1 * 1000 * dt + 0.3 * 100 * dt      # 5000 + 1500 = 6500
    infil_cfm = 0.5 * 12000 / 60.0                       # 100 CFM
    infiltration = 1.08 * infil_cfm * dt                 # 5400
    assert abs(r.conduction_btuh - conduction) < 1e-6
    assert abs(r.infiltration_btuh - infiltration) < 1e-6
    assert abs(r.total_btuh - (conduction + infiltration)) < 1e-6
    # CFM sized on supply-air rise, not the design delta-T
    assert abs(r.cfm - r.total_btuh / (1.08 * 50)) < 1e-6
    assert abs(r.by_category["window"] - 1500) < 1e-6


def test_heating_load_missing_assembly():
    env = geometry.Envelope(surfaces=[geometry.Surface("mystery", 10.0)], volume_ft3=100.0)
    with pytest.raises(KeyError):
        loads.heating_load(env, _sc())
