import pytest
from eldr import ductmodel, geometry, sidecar


def _sc(**ducts):
    return sidecar.SideCar(
        assemblies={"exterior_wall": 0.1, "window": 0.3},
        design=sidecar.DesignConditions(70, 20, 50),
        infiltration_ach=0.5,
        ducts=sidecar.Ducts(friction_rate=0.08, **ducts),
    )


def _room(name, vol, conditioned=True, centroid=(0.0, 0.0), level="L1"):
    # ~big infiltration volume so each conditioned room clears MIN_RUN_CFM
    return geometry.Room(name=name, level_id=level, area_ft2=200.0, centroid_cm=centroid,
                         conditioned=conditioned, surfaces=[], volume_ft3=vol,
                         windows_by_bearing={})


def _env(rooms, furniture=None, elevations=None):
    return geometry.Envelope(surfaces=[], volume_ft3=0.0, rooms=rooms,
                             furniture=furniture or [],
                             level_elevations=elevations or {"L1": 0.0})


def test_find_unit_matches_substring_case_insensitive():
    env = _env([], furniture=[geometry.Furniture("The Air Handler unit", 0, 0, "L1")])
    assert ductmodel.find_unit(env, "air handler") is not None
    assert ductmodel.find_unit(env, "furnace") is None


def test_plan_without_unit_has_no_lengths_and_falls_back():
    env = _env([_room("A", 12000.0), _room("B", 12000.0)])
    plan = ductmodel.plan_ducts(env, _sc())
    assert plan.unit is None
    assert plan.lengths is None
    assert plan.derived is False
    assert plan.friction_rate == 0.08                 # side-car fallback
    # trunk is first and carries the sum of the branch CFM
    assert plan.runs[0][0] == ductmodel.TRUNK_NAME
    assert abs(plan.runs[0][1] - sum(c for _, c in plan.runs[1:])) < 1e-6


def test_plan_skips_unconditioned_and_tiny_rooms():
    env = _env([_room("Big", 12000.0),
                _room("Garage", 12000.0, conditioned=False),
                _room("Nook", 1.0)])              # ~0 CFM -> below MIN_RUN_CFM
    plan = ductmodel.plan_ducts(env, _sc())
    served = [name for name, _ in plan.runs if name != ductmodel.TRUNK_NAME]
    assert served == ["Big"]


def test_plan_dedupes_repeated_room_names():
    env = _env([_room("Closet", 12000.0), _room("Closet", 12000.0)])
    plan = ductmodel.plan_ducts(env, _sc())
    names = [name for name, _ in plan.runs if name != ductmodel.TRUNK_NAME]
    assert names == ["Closet", "Closet (2)"]


def test_plan_with_unit_computes_lengths():
    unit = geometry.Furniture("Air Handler", 0.0, 0.0, "L1")
    # room centroid 100cm east + on a level 200cm above the unit
    env = _env([_room("Far", 12000.0, centroid=(100.0, 0.0), level="L2")],
               furniture=[unit], elevations={"L1": 0.0, "L2": 200.0})
    plan = ductmodel.plan_ducts(env, _sc(fitting_factor=2.0))
    assert plan.unit is unit
    # branch length = (|100-0| horiz + |200-0| vert) cm -> ft, x fitting 2.0
    from eldr import units
    (far_len,) = [l for (name, _), l in zip(plan.runs, plan.lengths)
                  if name == "Far"]
    assert abs(far_len - units.cm_to_ft(300.0) * 2.0) < 1e-6


def test_plan_derives_friction_when_asp_set():
    unit = geometry.Furniture("Air Handler", 0.0, 0.0, "L1")
    env = _env([_room("Far", 12000.0, centroid=(100.0, 0.0))],
               furniture=[unit], elevations={"L1": 0.0})
    plan = ductmodel.plan_ducts(env, _sc(available_static_pressure=0.5, fitting_factor=1.0))
    assert plan.derived is True
    # FR = ASP * 100 / worst length
    assert abs(plan.friction_rate - 0.5 * 100.0 / plan.worst_length_ft) < 1e-6


def test_size_from_plan_empty_is_none():
    env = _env([_room("Garage", 12000.0, conditioned=False)])   # nothing served
    plan = ductmodel.plan_ducts(env, _sc())
    assert plan.runs == []
    assert ductmodel.size_from_plan(plan) is None
