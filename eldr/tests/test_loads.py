import warnings

import pytest
from eldr import loads, geometry, sidecar, spaces


def _sc():
    return sidecar.SideCar(
        assemblies={"exterior_wall": 0.1, "window": 0.3},
        design=sidecar.DesignConditions(indoor_heating_f=70, outdoor_heating_99_f=20,
                                        supply_air_rise_f=50),
        infiltration_ach=0.5,
    )


def _envelope(surfaces):
    return geometry.Envelope(surfaces=list(surfaces), volume_ft3=0.0)


def _sidecar(assemblies, spaces=None, cooling=None):
    return sidecar.SideCar(
        assemblies=dict(assemblies),
        design=sidecar.DesignConditions(indoor_heating_f=70, outdoor_heating_99_f=13,
                                        supply_air_rise_f=50),
        infiltration_ach=0.0,          # isolate conduction — no infiltration term
        spaces=dict(spaces or {}),
        cooling=cooling,
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


def test_buffer_wall_heating_fraction_and_u_fallback():
    # buffer wall: no buffer_wall U -> falls back to exterior_wall U; dT = BUFFER_FACTOR x air
    env = geometry.Envelope(surfaces=[geometry.Surface("buffer_wall", 100.0)], volume_ft3=0.0)
    sc = sidecar.SideCar(
        assemblies={"exterior_wall": 0.1},
        design=sidecar.DesignConditions(70, 20, 50),   # air ΔT 50
        infiltration_ach=0.0,
    )
    # the severity note is donor-specific: a buffer wall and an exterior wall are the same
    # construction with a different boundary, so this borrow must NOT claim the
    # order-of-magnitude gap that only the slab-vs-framed-floor borrow earns.
    with pytest.warns(UserWarning, match="different boundary"):
        r = loads.heating_load(env, sc)
    assert abs(r.by_category["buffer_wall"] - 0.1 * 100 * (loads.BUFFER_FACTOR * 50)) < 1e-6


def test_buffer_wall_explicit_u_wins():
    env = geometry.Envelope(surfaces=[geometry.Surface("buffer_wall", 100.0)], volume_ft3=0.0)
    sc = sidecar.SideCar(
        assemblies={"exterior_wall": 0.1, "buffer_wall": 0.25},
        design=sidecar.DesignConditions(70, 20, 50),
        infiltration_ach=0.0,
    )
    r = loads.heating_load(env, sc)
    assert abs(r.by_category["buffer_wall"] - 0.25 * 100 * (loads.BUFFER_FACTOR * 50)) < 1e-6


def test_buffer_wall_cooling_fraction():
    env = geometry.Envelope(surfaces=[geometry.Surface("buffer_wall", 100.0)], volume_ft3=0.0)
    sc = sidecar.SideCar(
        assemblies={"exterior_wall": 0.1},
        design=sidecar.DesignConditions(70, 20, 50),
        infiltration_ach=0.0,
        cooling=sidecar.Cooling(indoor_f=75, outdoor_1_f=95, shgc=0.4, occupants=0),
    )
    r = loads.cooling_load(env, sc)   # cooling ΔT = 20
    assert abs(r.by_category["buffer_wall"] - 0.1 * 100 * (loads.BUFFER_FACTOR * 20)) < 1e-6


def test_buffer_floor_uses_its_space_policy():
    """Two buffer floors, same category, different spaces -> different ΔT."""
    env = _envelope([geometry.Surface("buffer_floor", 100.0, "crawlspace"),
                     geometry.Surface("buffer_floor", 100.0, "garage")])
    sc = _sidecar(assemblies={"buffer_floor": 0.5},
                  spaces={"crawlspace": spaces.SpacePolicy("crawlspace", winter_temp_f=32.0),
                          "garage": spaces.SpacePolicy("garage", factor=0.25)})
    res = loads.heating_load(env, sc)
    dt = sc.design.heating_delta_t
    expected = 0.5 * 100.0 * dt * (38.0 / 57.0) + 0.5 * 100.0 * dt * 0.25
    assert abs(res.conduction_btuh - expected) < 1e-6


def test_a_spaces_key_that_binds_to_nothing_warns():
    """`spaces:` was the only side-car block that swallowed a typo in silence.

    `walls:` warns on an unknown wall id and `levels:` on an unknown level name; a
    `spaces:` key matching no resolved space just never fires, and the surface quietly
    loads at the built-in default. The numbers here are chosen so the difference is not
    subtle: `crawl_space` against a surface facing `crawlspace` is 1425.0 BTU/hr at the
    unvented default of 0.5, against 1900.0 if the declared 32 °F had been honoured.
    """
    env = _envelope([geometry.Surface("buffer_floor", 100.0, "crawlspace")])
    sc = _sidecar(assemblies={"buffer_floor": 0.5},
                  spaces={"crawl_space": spaces.SpacePolicy("crawl_space",
                                                            winter_temp_f=32.0)})
    with pytest.warns(UserWarning, match=r"crawl_space"):
        res = loads.heating_load(env, sc)
    dt = sc.design.heating_delta_t                        # 70 - 13 = 57
    assert res.conduction_btuh == pytest.approx(0.5 * 100.0 * dt * 0.5)      # 1425.0
    assert res.conduction_btuh != pytest.approx(0.5 * 100.0 * dt * (38.0 / 57.0))


def test_a_spaces_key_bound_only_by_a_per_room_surface_does_not_warn():
    """The whole-house surface list is not the whole answer: `env.rooms` carries its own,
    independently built surfaces. A space reaching only that path is bound, not a typo."""
    env = geometry.Envelope(
        surfaces=[geometry.Surface("exterior_wall", 100.0)], volume_ft3=0.0,
        rooms=[geometry.Room(name="r", level_id="LM", area_ft2=100.0, centroid_cm=(0, 0),
                             conditioned=True,
                             surfaces=[geometry.Surface("buffer_floor", 100.0,
                                                        "crawlspace")])])
    sc = _sidecar(assemblies={"exterior_wall": 0.1, "buffer_floor": 0.5},
                  spaces={"crawlspace": spaces.SpacePolicy("crawlspace",
                                                           winter_temp_f=32.0)})
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        loads.heating_load(env, sc)


def test_every_spaces_key_binding_stays_silent():
    """The other half of the bracket: the warning must not fire on a correct side-car."""
    env = _envelope([geometry.Surface("buffer_floor", 100.0, "crawlspace"),
                     geometry.Surface("ceiling", 100.0, "attic")])
    sc = _sidecar(assemblies={"buffer_floor": 0.5, "ceiling": 0.03},
                  spaces={"crawlspace": spaces.SpacePolicy("crawlspace", winter_temp_f=32.0),
                          "attic": spaces.SpacePolicy("attic", vented=False)})
    with warnings.catch_warnings():
        warnings.simplefilter("error")           # any warning at all fails the test
        loads.heating_load(env, sc)


def test_the_unbound_spaces_warning_reaches_cooling_and_per_room_too():
    """All three public entry points read `spaces:`, so all three have to say so — a
    cooling-only or duct-only run must not be the quiet path back to the old behaviour."""
    env = geometry.Envelope(
        surfaces=[geometry.Surface("ceiling", 100.0, "attic")], volume_ft3=0.0,
        rooms=[geometry.Room(name="r", level_id="LM", area_ft2=100.0, centroid_cm=(0, 0),
                             conditioned=True,
                             surfaces=[geometry.Surface("ceiling", 100.0, "attic")])])
    sc = _sidecar(assemblies={"ceiling": 0.03},
                  spaces={"attik": spaces.SpacePolicy("attik", vented=False)},
                  cooling=_cooling())
    with pytest.warns(UserWarning, match=r"attik"):
        loads.cooling_load(env, sc)
    with pytest.warns(UserWarning, match=r"attik"):
        loads.per_room_loads(env, sc)


def test_ceiling_uses_the_attic_policy_not_outdoor_air():
    env = _envelope([geometry.Surface("ceiling", 100.0, "attic")])
    sc = _sidecar(assemblies={"ceiling": 0.03},
                  spaces={"attic": spaces.SpacePolicy("attic", vented=False)})
    res = loads.heating_load(env, sc)
    assert abs(res.conduction_btuh - 0.03 * 100.0 * sc.design.heating_delta_t * 0.5) < 1e-6


def test_vented_attic_restores_full_outdoor_delta_t():
    env = _envelope([geometry.Surface("ceiling", 100.0, "attic")])
    sc = _sidecar(assemblies={"ceiling": 0.03},
                  spaces={"attic": spaces.SpacePolicy("attic", vented=True)})
    res = loads.heating_load(env, sc)
    assert abs(res.conduction_btuh - 0.03 * 100.0 * sc.design.heating_delta_t) < 1e-6


def _cooling(indoor_f=75, outdoor_1_f=95, shgc=0.4, occupants=0, attic_temp_f=None):
    return sidecar.Cooling(indoor_f=indoor_f, outdoor_1_f=outdoor_1_f,
                           shgc=shgc, occupants=occupants, attic_temp_f=attic_temp_f)


def test_cooling_ceiling_uses_the_attic_policy_and_may_exceed_outdoor_delta_t():
    """The cooling mirror of the attic test, and the design's headline cooling feature.

    A sun-loaded attic runs HOTTER than outdoor air, so its factor is deliberately not
    clamped at 1 and the ceiling sees a LARGER ΔT than the outdoor design one. Asserting
    the exact value also pins that the cooling resolver calls `spaces.cooling_factor` and
    not its two-lines-away twin `heating_factor` — which reads `winter_temp_f`, finds
    none here, and would silently fall back to the flat unvented 0.5.
    """
    env = _envelope([geometry.Surface("ceiling", 100.0, "attic")])
    sc = _sidecar(assemblies={"ceiling": 0.03},
                  spaces={"attic": spaces.SpacePolicy("attic", summer_temp_f=130.0)},
                  cooling=_cooling())
    air = sc.cooling.cooling_delta_t                       # 95 - 75 = 20
    factor = (130.0 - 75.0) / air                          # 2.75 — above 1, on purpose
    assert factor > 1.0
    res = loads.cooling_load(env, sc)
    assert abs(res.by_category["ceiling"] - 0.03 * 100.0 * air * factor) < 1e-6
    # and it is strictly more than the plain outdoor ΔT would give
    assert res.by_category["ceiling"] > 0.03 * 100.0 * air


def test_ceiling_cooling_uses_attic_temp_not_outdoor():
    """The hot-attic correction: at 130°F attic vs 89°F outdoor and 75°F indoors, the
    ceiling sees (130-75) instead of (89-75) — nearly 4x the gain. This is the
    373 -> ~4,400 BTU/hr gap against the professional Manual J."""
    env = _envelope([geometry.Surface("ceiling", 100.0, "attic")])
    sc = sidecar.SideCar(
        assemblies={"ceiling": 0.03},
        design=sidecar.DesignConditions(70, 15, 50),
        infiltration_ach=0.0,
        cooling=sidecar.Cooling(indoor_f=75, outdoor_1_f=89, shgc=0.3, occupants=0,
                                attic_temp_f=130.0),
    )
    res = loads.cooling_load(env, sc)
    assert res.by_category["ceiling"] == pytest.approx(0.03 * 100.0 * (130.0 - 75.0))


def test_ceiling_cooling_falls_back_to_sol_air_when_nothing_is_declared():
    """Level 3 of the precedence: no `spaces.attic.summer_temp_f`, no `cooling.attic_temp_f`.

    Outdoor 95°F -> sol-air 95 + 50*0.85 = 137.5°F, so ΔT 62.5 and the gain is 187.5.
    Every wrong resolution lands somewhere else: the outdoor ΔT gives 60, the unvented
    default halves it to 30, and forgetting to scale the uplift by absorptance gives 210.
    """
    env = _envelope([geometry.Surface("ceiling", 100.0, "attic")])
    sc = _sidecar(assemblies={"ceiling": 0.03}, cooling=_cooling(outdoor_1_f=95))
    res = loads.cooling_load(env, sc)
    assert res.by_category["ceiling"] == pytest.approx(0.03 * 100.0 * (137.5 - 75.0))


def test_declared_attic_summer_temp_beats_cooling_attic_temp():
    """Level 1 beats level 2: an observed attic temperature outranks the side-car's
    whole-house override, which in turn outranks the sol-air estimate.

    105 / 130 / 137.5 are held far apart on purpose — the three candidate ΔTs are 30,
    55 and 62.5, so a resolver that picks the wrong one cannot land on 90 by accident.
    """
    env = _envelope([geometry.Surface("ceiling", 100.0, "attic")])
    sc = _sidecar(assemblies={"ceiling": 0.03},
                  spaces={"attic": spaces.SpacePolicy("attic", summer_temp_f=105.0)},
                  cooling=_cooling(outdoor_1_f=95, attic_temp_f=130.0))
    res = loads.cooling_load(env, sc)
    assert res.by_category["ceiling"] == pytest.approx(0.03 * 100.0 * (105.0 - 75.0))


def test_cooling_attic_temp_beats_sol_air():
    """Level 2 beats level 3, with the two well separated: an explicit 118°F attic gives
    ΔT 43, where the sol-air estimate for this outdoor temp would give 62.5 and the
    plain outdoor ΔT 20."""
    env = _envelope([geometry.Surface("ceiling", 100.0, "attic")])
    sc = _sidecar(assemblies={"ceiling": 0.03}, cooling=_cooling(outdoor_1_f=95,
                                                                attic_temp_f=118.0))
    res = loads.cooling_load(env, sc)
    assert res.by_category["ceiling"] == pytest.approx(0.03 * 100.0 * (118.0 - 75.0))


def test_hot_attic_applies_only_to_the_attic_space():
    """The space gate is `== "attic"`, not `in`, and `sub_attic` is what proves it.

    A crawlspace would be a toothless fixture here: `"attic" in "crawlspace"` is False,
    so the substring bug this branch has already been bitten by once (`"floor"` inside
    `"buffer_floor"`) would sail past it. `sub_attic` CONTAINS "attic", so it fails
    loudly under an `in` gate while staying an ordinary unvented space under `==`.
    """
    env = _envelope([geometry.Surface("ceiling", 100.0, "sub_attic"),
                     geometry.Surface("buffer_floor", 100.0, "crawlspace")])
    sc = _sidecar(assemblies={"ceiling": 0.03, "buffer_floor": 0.5},
                  cooling=_cooling(outdoor_1_f=95, attic_temp_f=130.0))
    res = loads.cooling_load(env, sc)
    air = sc.cooling.cooling_delta_t                       # 20
    # 30, not the 165 an `in` gate would give by applying the 130°F attic temperature
    assert res.by_category["ceiling"] == pytest.approx(0.03 * 100.0 * air
                                                       * spaces.UNVENTED_FACTOR)
    assert res.by_category["buffer_floor"] == pytest.approx(0.5 * 100.0 * air
                                                            * spaces.UNVENTED_FACTOR)


def test_effective_cooling_policy_is_the_shared_resolution_point():
    """Task 8's report and Task 9's JSON export must render the policy that was USED.

    Resolving `spaces.policy_for("attic", {})` alone gives the bare unvented default and
    a cooling factor of 0.5, while the engine loads the ceiling at 2.75 — so this helper
    is the single place both the engine and any reporting consumer must go through.
    """
    c = _cooling(outdoor_1_f=95, attic_temp_f=130.0)
    declared = spaces.policy_for("attic", {})
    assert declared.summer_temp_f is None                  # nothing declared to render
    eff = loads.effective_cooling_policy("attic", declared, c)
    assert eff.summer_temp_f == 130.0
    assert eff.name == "attic" and eff.vented is False     # the rest of the policy survives
    # and the factor a report would print now matches what the engine actually applied
    assert spaces.cooling_factor(eff, 75.0, 95.0) == pytest.approx((130.0 - 75.0) / 20.0)


def test_effective_cooling_policy_leaves_other_spaces_alone():
    c = _cooling(outdoor_1_f=95, attic_temp_f=130.0)
    for name in ("crawlspace", "garage", "sub_attic", "attic_2"):
        declared = spaces.policy_for(name, {})
        assert loads.effective_cooling_policy(name, declared, c) is declared


def test_effective_cooling_policy_defers_to_a_declared_summer_temp():
    c = _cooling(outdoor_1_f=95, attic_temp_f=130.0)
    declared = spaces.SpacePolicy("attic", summer_temp_f=105.0)
    assert loads.effective_cooling_policy("attic", declared, c) is declared


def test_effective_cooling_policy_without_a_cooling_block_is_a_no_op():
    """The `cooling` block is OPTIONAL, so a heating-only side-car is ordinary input.

    Unguarded, the attic branch dereferenced it and raised AttributeError — for the attic
    ALONE, while every other space returned fine. A report rendering a mixed set of spaces
    would have crashed on exactly one row and passed its own tests on all the others.
    """
    for name in ("attic", "crawlspace"):
        declared = spaces.policy_for(name, {})
        assert loads.effective_cooling_policy(name, declared, None) is declared


def test_effective_cooling_policy_takes_the_outdoor_temp_from_the_cooling_block():
    """The outdoor temp is DERIVED from `cooling`, never passed alongside it.

    The removed `outdoor_f` parameter accepted the *heating* design temp without
    complaint (13°F gave a 55.5°F "attic"), which is precisely the consumer divergence
    this helper exists to prevent. 95°F is the only value it can now use: sol-air 137.5°F.
    """
    eff = loads.effective_cooling_policy("attic", spaces.policy_for("attic", {}),
                                         _cooling(outdoor_1_f=95))
    assert eff.summer_temp_f == pytest.approx(137.5)
    assert eff.summer_temp_f == pytest.approx(spaces.sol_air_attic_temp_f(95.0))


def test_effective_cooling_policy_needs_a_resolved_outdoor_temp_for_the_estimate():
    """An unresolved `outdoor_1_f` must say so, not hand `None` to the sol-air arithmetic."""
    with pytest.raises(ValueError, match="outdoor_1_f"):
        loads.effective_cooling_policy("attic", spaces.policy_for("attic", {}),
                                       _cooling(outdoor_1_f=None))


def test_hot_attic_does_not_touch_the_heating_side():
    """`cooling.attic_temp_f` is a summer observation; winter still uses the attic's own
    policy (unvented -> half the heating ΔT), not 130°F."""
    env = _envelope([geometry.Surface("ceiling", 100.0, "attic")])
    sc = _sidecar(assemblies={"ceiling": 0.03},
                  cooling=_cooling(outdoor_1_f=95, attic_temp_f=130.0))
    res = loads.heating_load(env, sc)
    assert res.conduction_btuh == pytest.approx(
        0.03 * 100.0 * sc.design.heating_delta_t * spaces.UNVENTED_FACTOR)


def test_vented_attic_still_gets_the_hot_attic_treatment_in_cooling():
    """`vented` is a winter shorthand. Venting cools an attic but does not make it track
    outdoor air on a sunny design day, so summer still uses the sol-air estimate; the
    escape hatch is an observed `summer_temp_f`, not `vented: true`.
    """
    env = _envelope([geometry.Surface("ceiling", 100.0, "attic")])
    sc = _sidecar(assemblies={"ceiling": 0.03},
                  spaces={"attic": spaces.SpacePolicy("attic", vented=True)},
                  cooling=_cooling(outdoor_1_f=95))
    res = loads.cooling_load(env, sc)
    assert res.by_category["ceiling"] == pytest.approx(0.03 * 100.0 * (137.5 - 75.0))
    # and heating is untouched: vented -> the full outdoor ΔT there
    heat = loads.heating_load(env, sc)
    assert heat.conduction_btuh == pytest.approx(0.03 * 100.0 * sc.design.heating_delta_t)


def test_declared_attic_factor_is_also_a_winter_only_shorthand():
    """The `factor` twin of the vented case, pinned because it is the same override.

    An explicit `factor: 0.25` is a stronger-looking declaration than `vented`, so it is
    worth stating outright that it too is outranked in summer by the hot-attic
    resolution — 0.25 is chosen so the overridden answer (187.5, sol-air) and the
    obeyed one (15) cannot be confused.
    """
    env = _envelope([geometry.Surface("ceiling", 100.0, "attic")])
    sc = _sidecar(assemblies={"ceiling": 0.03},
                  spaces={"attic": spaces.SpacePolicy("attic", factor=0.25)},
                  cooling=_cooling(outdoor_1_f=95))
    res = loads.cooling_load(env, sc)
    assert res.by_category["ceiling"] == pytest.approx(0.03 * 100.0 * (137.5 - 75.0))
    # winter still obeys it: 0.25 of the heating ΔT, not the unvented 0.5
    heat = loads.heating_load(env, sc)
    assert heat.conduction_btuh == pytest.approx(
        0.03 * 100.0 * sc.design.heating_delta_t * 0.25)


def test_cooling_buffer_floor_uses_its_space_policy():
    """Cooling counterpart of the heating per-space test: same category, two spaces."""
    env = _envelope([geometry.Surface("buffer_floor", 100.0, "crawlspace"),
                     geometry.Surface("buffer_floor", 100.0, "garage")])
    sc = _sidecar(assemblies={"buffer_floor": 0.5},
                  spaces={"crawlspace": spaces.SpacePolicy("crawlspace", summer_temp_f=90.0),
                          "garage": spaces.SpacePolicy("garage", factor=0.25)},
                  cooling=_cooling())
    air = sc.cooling.cooling_delta_t                       # 95 - 75 = 20
    # 0.75 is deliberately not 0.5: reading `winter_temp_f` instead would find none and
    # fall back to the flat unvented default, so this also catches the factor-fn swap.
    expected = (0.5 * 100.0 * air * 0.75                   # crawl at 90°F -> (90-75)/20
                + 0.5 * 100.0 * air * 0.25)                # garage's declared factor
    res = loads.cooling_load(env, sc)
    assert abs(res.by_category["buffer_floor"] - expected) < 1e-6


def test_cooling_surface_without_space_keeps_the_plain_outdoor_delta_t():
    env = _envelope([geometry.Surface("exterior_wall", 100.0)])
    sc = _sidecar(assemblies={"exterior_wall": 0.08}, cooling=_cooling())
    res = loads.cooling_load(env, sc)
    assert abs(res.by_category["exterior_wall"] - 0.08 * 100.0 * sc.cooling.cooling_delta_t) < 1e-6


def test_buffer_floor_u_prefers_exposed_floor_over_floor():
    """The chain is ordered, not a set: with both declared, the nearer relative wins."""
    env = _envelope([geometry.Surface("buffer_floor", 100.0, "crawlspace")])
    sc = _sidecar(assemblies={"exposed_floor": 0.07, "floor": 0.02})   # no buffer_floor
    with pytest.warns(UserWarning, match="exposed_floor"):
        res = loads.heating_load(env, sc)
    dt = sc.design.heating_delta_t * spaces.UNVENTED_FACTOR
    assert abs(res.conduction_btuh - 0.07 * 100.0 * dt) < 1e-6         # not floor's 0.02


def test_buffer_floor_u_falls_back_through_exposed_floor_to_floor():
    """The far end of the chain — what this project's own Refrhus side-car actually hits."""
    env = _envelope([geometry.Surface("buffer_floor", 100.0, "crawlspace")])
    sc = _sidecar(assemblies={"floor": 0.02})     # neither buffer_floor nor exposed_floor
    # match the donor's U-value, not its name: "floor" is a substring of "buffer_floor",
    # so a message naming only the recipient would satisfy match="floor" vacuously.
    with pytest.warns(UserWarning, match=r"U-value \(0\.02\)"):
        res = loads.heating_load(env, sc)
    dt = sc.design.heating_delta_t * spaces.UNVENTED_FACTOR
    assert abs(res.conduction_btuh - 0.02 * 100.0 * dt) < 1e-6


def test_assembly_borrow_reports_the_same_decision_the_engine_makes():
    """The report renders borrows; `_u_value` applies them. One resolver, or they drift.

    `buffer_floor` falls back through `exposed_floor` BEFORE `floor`, so a fixture that
    declares only `floor` cannot tell a correct chain walk from one that stops at the
    first entry it finds. Both donors are present here and the nearer one must win.
    """
    assemblies = {"floor": 0.05, "exposed_floor": 0.09}
    borrow = loads.assembly_borrow("buffer_floor", assemblies)
    assert borrow.donor == "exposed_floor" and borrow.u_value == 0.09
    assert "framed floors" in borrow.note                  # the pair-specific severity
    # and the number it reports is the one the engine actually loads at
    env = _envelope([geometry.Surface("buffer_floor", 100.0, "crawlspace")])
    sc = _sidecar(assemblies=assemblies)
    with pytest.warns(UserWarning):
        res = loads.heating_load(env, sc)
    assert res.conduction_btuh == pytest.approx(
        borrow.u_value * 100.0 * sc.design.heating_delta_t * spaces.UNVENTED_FACTOR)


def test_assembly_borrow_is_none_when_declared_or_unborrowable():
    assert loads.assembly_borrow("buffer_floor", {"buffer_floor": 0.08}) is None
    assert loads.assembly_borrow("window", {"floor": 0.05}) is None   # no fallback chain


def test_borrowing_an_assembly_u_warns_naming_both_categories():
    """A borrow is a stand-in, not a measurement, and must never be silent."""
    env = _envelope([geometry.Surface("buffer_floor", 100.0, "crawlspace")])
    sc = _sidecar(assemblies={"floor": 0.02})
    with pytest.warns(UserWarning) as rec:
        loads.heating_load(env, sc)
    msg = str(rec[0].message)
    # The donor must be identified by something that is NOT a substring of the recipient:
    # asserting `"floor" in msg` passes on any message naming only `buffer_floor`. Its
    # U-value is the sharp test — it can only have come from reading the donor entry.
    assert "buffer_floor" in msg                           # the recipient
    assert "`floor`" in msg                                # the donor, backticked so the
    #                                                        substring of the recipient
    #                                                        (`buffer_floor`) cannot satisfy it
    assert "0.02" in msg                                   # the donor's U-value, verbatim


def test_declared_assembly_does_not_warn():
    """The complement: declaring the assembly borrows nothing, so it must stay quiet."""
    import warnings as _warnings
    env = _envelope([geometry.Surface("buffer_floor", 100.0, "crawlspace")])
    sc = _sidecar(assemblies={"buffer_floor": 0.5, "floor": 0.02})
    with _warnings.catch_warnings():
        _warnings.simplefilter("error")               # any warning becomes a failure
        res = loads.heating_load(env, sc)
    dt = sc.design.heating_delta_t * spaces.UNVENTED_FACTOR
    assert abs(res.conduction_btuh - 0.5 * 100.0 * dt) < 1e-6


def test_surface_without_space_keeps_the_plain_outdoor_delta_t():
    """Walls and windows carry no space and must be untouched by this change."""
    env = _envelope([geometry.Surface("exterior_wall", 100.0)])
    sc = _sidecar(assemblies={"exterior_wall": 0.08})
    res = loads.heating_load(env, sc)
    assert abs(res.conduction_btuh - 0.08 * 100.0 * sc.design.heating_delta_t) < 1e-6


def test_per_room_internal_only_for_conditioned():
    # internal (occupant/appliance) sensible is shared across conditioned rooms by
    # area; an unconditioned room gets none
    cond = _room("Cond", [], 1000.0, conditioned=True)
    unc = _room("Unc", [], 1000.0, conditioned=False)
    env = geometry.Envelope(surfaces=[], volume_ft3=0.0, rooms=[cond, unc])
    cr, ur = loads.per_room_loads(env, _sc_cool())
    # both have identical geometry; the conditioned room's cooling includes internal gain
    assert cr.cooling_btuh > ur.cooling_btuh
