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
        windows_by_bearing={270: 100.0},                  # due West
    )
    r = loads.cooling_load(env, _sc_cool())
    dt = 20.0                                              # 95 - 75
    conduction = 0.1 * 1000 * dt + 0.3 * 100 * dt         # 2000 + 600 = 2600
    solar = 100.0 * 0.4 * loads.solar_hgf(270)            # 100*0.4*75 = 3000
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


def test_solar_hgf_interpolates():
    # cardinals hit the anchors exactly
    assert loads.solar_hgf(0) == 20
    assert loads.solar_hgf(90) == 75
    assert loads.solar_hgf(180) == 45
    assert loads.solar_hgf(270) == 75
    # SE (135) is halfway between E(75) and S(45) -> 60
    assert abs(loads.solar_hgf(135) - 60.0) < 1e-9
    # SW (225) is halfway between S(45) and W(75) -> 60; and it wraps at 360
    assert abs(loads.solar_hgf(225) - 60.0) < 1e-9
    assert loads.solar_hgf(360) == loads.solar_hgf(0)


def test_octant_labels():
    assert loads.octant(0) == "N"
    assert loads.octant(90) == "E"
    assert loads.octant(135) == "SE"
    assert loads.octant(222) == "SW"
    assert loads.octant(359) == "N"      # wraps back to N


def test_cooling_solar_groups_by_octant():
    # two walls at ~SE bearings both land under solar-SE and sum
    env = geometry.Envelope(surfaces=[], volume_ft3=1000.0,
                            windows_by_bearing={130: 50.0, 140: 50.0})
    r = loads.cooling_load(env, _sc_cool())
    assert "solar-SE" in r.by_category
    assert abs(r.by_category["solar-SE"]
               - (50 * 0.4 * loads.solar_hgf(130) + 50 * 0.4 * loads.solar_hgf(140))) < 1e-6
