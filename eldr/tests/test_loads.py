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


def _room(name, surfaces, volume, conditioned=True, windows=None, level="L1"):
    return geometry.Room(name=name, level_id=level, area_ft2=200.0, centroid_cm=(0.0, 0.0),
                         conditioned=conditioned, surfaces=surfaces, volume_ft3=volume,
                         windows_by_bearing=windows or {})


def test_per_room_heating_matches_core():
    # a room's heating load is conduction over its surfaces + infiltration on its volume
    wall = _room("Wall", [geometry.Surface("exterior_wall", 1000.0)], 12000.0)
    env = geometry.Envelope(surfaces=[], volume_ft3=0.0, rooms=[wall])
    (rl,) = loads.per_room_loads(env, _sc())
    dt = 50.0
    conduction = 0.1 * 1000 * dt                        # 5000
    infil = 1.08 * (0.5 * 12000 / 60.0) * dt            # 5400
    assert abs(rl.heating_btuh - (conduction + infil)) < 1e-6
    # heating-only side-car -> cooling 0, CFM sized on heating
    assert rl.cooling_btuh == 0.0
    assert abs(rl.cfm - rl.heating_btuh / (1.08 * 50)) < 1e-6


def test_per_room_interior_room_gets_infiltration_only():
    # a room with no exterior surfaces still gets a (small) infiltration load
    interior = _room("Interior", [], 6000.0)
    env = geometry.Envelope(surfaces=[], volume_ft3=0.0, rooms=[interior])
    (rl,) = loads.per_room_loads(env, _sc())
    assert rl.heating_btuh > 0
    assert abs(rl.heating_btuh - 1.08 * (0.5 * 6000 / 60.0) * 50) < 1e-6


def test_per_room_cfm_takes_larger_of_heat_cool():
    # a glassy room where cooling airflow can dominate; CFM = max(heat, cool)
    glassy = _room("Sun", [geometry.Surface("window", 200.0)], 4000.0,
                   windows={270: 200.0})               # due-West glass, high solar
    env = geometry.Envelope(surfaces=[], volume_ft3=0.0, rooms=[glassy])
    (rl,) = loads.per_room_loads(env, _sc_cool())
    cfm_heat = rl.heating_btuh / (1.08 * 50)
    cfm_cool = rl.cooling_btuh / (1.08 * loads.COOLING_SUPPLY_DT_F)
    assert abs(rl.cfm - max(cfm_heat, cfm_cool)) < 1e-6


def _sc_ground():
    return sidecar.SideCar(
        assemblies={"exterior_wall": 0.1, "basement_wall": 0.2, "floor": 0.05},
        design=sidecar.DesignConditions(70, 15, 50, ground_temp_f=50),   # air ΔT 55, ground ΔT 20
        infiltration_ach=0.0,
        cooling=sidecar.Cooling(indoor_f=75, outdoor_1_f=95, shgc=0.4, occupants=0),
    )


def test_below_grade_uses_ground_delta_t():
    # basement_wall + floor are ground-coupled (ΔT 20); exterior_wall sees air ΔT 55
    env = geometry.Envelope(surfaces=[geometry.Surface("exterior_wall", 100.0),
                                      geometry.Surface("basement_wall", 100.0),
                                      geometry.Surface("floor", 100.0)], volume_ft3=0.0)
    r = loads.heating_load(env, _sc_ground())
    assert abs(r.by_category["exterior_wall"] - 0.1 * 100 * 55) < 1e-6
    assert abs(r.by_category["basement_wall"] - 0.2 * 100 * 20) < 1e-6   # ground ΔT, not 55
    assert abs(r.by_category["floor"] - 0.05 * 100 * 20) < 1e-6


def test_below_grade_no_cooling_gain():
    # in summer the soil is cooler than indoors -> below-grade surfaces add no cooling load
    env = geometry.Envelope(surfaces=[geometry.Surface("basement_wall", 100.0),
                                      geometry.Surface("floor", 100.0),
                                      geometry.Surface("exterior_wall", 100.0)], volume_ft3=0.0)
    r = loads.cooling_load(env, _sc_ground())
    assert r.by_category["basement_wall"] == 0.0
    assert r.by_category["floor"] == 0.0
    assert r.by_category["exterior_wall"] > 0.0


def test_ground_delta_t_clamped_at_zero():
    # if soil is warmer than the heating setpoint, a below-grade wall isn't a heat loss
    d = sidecar.DesignConditions(65, 15, 50, ground_temp_f=70)
    assert d.ground_heating_delta_t == 0.0


def test_below_grade_warm_ground_adds_cooling():
    # a configured soil warmer than the cooling setpoint DOES drive below-grade gain
    # (ground 90 > indoor 75 -> ΔT 15), not the usual clamp-to-zero
    env = geometry.Envelope(surfaces=[geometry.Surface("basement_wall", 100.0)], volume_ft3=0.0)
    sc = sidecar.SideCar(
        assemblies={"basement_wall": 0.2},
        design=sidecar.DesignConditions(70, 15, 50, ground_temp_f=90),
        infiltration_ach=0.0,
        cooling=sidecar.Cooling(indoor_f=75, outdoor_1_f=95, shgc=0.4, occupants=0),
    )
    r = loads.cooling_load(env, sc)
    assert abs(r.by_category["basement_wall"] - 0.2 * 100 * 15) < 1e-6


def test_per_room_internal_only_for_conditioned():
    # internal (occupant/appliance) sensible is shared across conditioned rooms by
    # area; an unconditioned room gets none
    cond = _room("Cond", [], 1000.0, conditioned=True)
    unc = _room("Unc", [], 1000.0, conditioned=False)
    env = geometry.Envelope(surfaces=[], volume_ft3=0.0, rooms=[cond, unc])
    cr, ur = loads.per_room_loads(env, _sc_cool())
    # both have identical geometry; the conditioned room's cooling includes internal gain
    assert cr.cooling_btuh > ur.cooling_btuh
