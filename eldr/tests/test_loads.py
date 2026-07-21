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


def _sc_cool():
    return sidecar.SideCar(
        assemblies={"exterior_wall": 0.1, "window": 0.3},
        design=sidecar.DesignConditions(70, 20, 50),
        infiltration_ach=0.5,
        cooling=sidecar.Cooling(indoor_f=75, outdoor_1_f=95, shgc=0.4, occupants=2),
    )


def test_cooling_load_math():
    env = geometry.Envelope(
        surfaces=[geometry.Surface("exterior_wall", 1000.0), geometry.Surface("window", 100.0)],
        volume_ft3=12000.0,
        windows_by_orientation={"W": 100.0},
    )
    r = loads.cooling_load(env, _sc_cool())
    dt = 20.0                                              # 95 - 75
    conduction = 0.1 * 1000 * dt + 0.3 * 100 * dt         # 2000 + 600 = 2600
    solar = 100.0 * 0.4 * loads.SOLAR_HGF["W"]            # 100*0.4*75 = 3000
    internal = 2 * loads.INTERNAL_SENSIBLE_PER_OCCUPANT + loads.APPLIANCE_SENSIBLE_BTUH  # 460+1200=1660
    sensible = conduction + solar + internal
    assert abs(r.by_category["solar-W"] - solar) < 1e-6
    assert abs(r.sensible_btuh - sensible) < 1e-6
    infil_cfm = 0.5 * 12000 / 60.0                        # 100 CFM
    latent = 2 * loads.INTERNAL_LATENT_PER_OCCUPANT + 0.68 * infil_cfm * loads.LATENT_GRAINS_DIFF
    assert abs(r.latent_btuh - latent) < 1e-6
    assert abs(r.total_btuh - (sensible + latent)) < 1e-6
    assert abs(r.cfm - sensible / (1.08 * loads.COOLING_SUPPLY_DT_F)) < 1e-6


def test_cooling_load_requires_cooling_block():
    env = geometry.Envelope(surfaces=[], volume_ft3=100.0)
    with pytest.raises(ValueError, match="cooling"):
        loads.cooling_load(env, _sc())
