"""Derive Manual D inputs from the SH3D model itself.

Given the parsed model and side-car, this locates the air-handler unit (a placed
furniture item), computes each conditioned room's design CFM (Manual J 1c) and its
run length from the unit, adds a main trunk carrying the total, and — if available
static pressure is supplied — derives the design friction rate the ACCA way
(ASP x 100 / worst total effective length). No unit / no ASP -> it falls back to
the side-car friction rate and omits run lengths. Demo-grade; TEL uses a fitting
fudge factor, not true fitting equivalent lengths.
"""
from __future__ import annotations
from dataclasses import dataclass
from eldr import geometry, loads, sidecar, ductd, units

# Rooms below this design CFM (tiny closets / nooks) get no dedicated supply run.
MIN_RUN_CFM = 3.0
TRUNK_NAME = "main trunk"


@dataclass(frozen=True)
class DuctPlan:
    unit: geometry.Furniture | None          # located air handler, or None if not placed
    unit_name: str                           # the name substring searched for
    friction_rate: float                     # design FR used (derived or fallback)
    derived: bool                            # True if FR came from ASP / worst length
    available_static_pressure: float | None  # in.wc, if provided
    worst_length_ft: float | None            # longest run TEL, if a unit is placed
    room_loads: list[loads.RoomLoad]         # every room (for the Manual J 1c table)
    runs: list[tuple[str, float]]            # served runs (trunk first): (name, cfm)
    lengths: list[float | None] | None       # parallel run lengths, or None if no unit


def find_unit(env: geometry.Envelope, name_substr: str) -> geometry.Furniture | None:
    """First furniture item whose name contains `name_substr` (case-insensitive)."""
    ns = name_substr.lower()
    for f in env.furniture:
        if ns in f.name.lower():
            return f
    return None


def _dedupe(name: str, seen: dict[str, int]) -> str:
    """Disambiguate repeated room names ('Closet' -> 'Closet', 'Closet (2)', ...)."""
    seen[name] = seen.get(name, 0) + 1
    return name if seen[name] == 1 else f"{name} ({seen[name]})"


def plan_ducts(env: geometry.Envelope, sc: sidecar.SideCar) -> DuctPlan:
    """Build the model-derived duct plan (per-room CFM + lengths + friction rate)."""
    room_loads = loads.per_room_loads(env, sc)
    d = sc.ducts
    unit_name = d.unit_name if d is not None else "air handler"
    fallback_fr = d.friction_rate if d is not None else ductd.DEFAULT_FRICTION_RATE
    asp = d.available_static_pressure if d is not None else None
    fitting = d.fitting_factor if d is not None else 1.5

    unit = find_unit(env, unit_name)
    room_of = {id(rl): room for room, rl in zip(env.rooms, room_loads)}
    unit_elev = env.level_elevations.get(unit.level_id, 0.0) if unit is not None else None

    runs: list[tuple[str, float]] = []
    lengths: list[float | None] = []
    seen: dict[str, int] = {}
    for rl in room_loads:
        if not rl.conditioned or rl.cfm < MIN_RUN_CFM:
            continue
        length = None
        if unit is not None:
            room = room_of[id(rl)]
            cx, cy = room.centroid_cm
            horiz = abs(cx - unit.x_cm) + abs(cy - unit.y_cm)   # Manhattan, plan cm
            vert = abs(env.level_elevations.get(room.level_id, 0.0) - unit_elev)
            length = units.cm_to_ft(horiz + vert) * fitting
        runs.append((_dedupe(rl.name, seen), rl.cfm))
        lengths.append(length)

    total_cfm = sum(cfm for _, cfm in runs)
    if runs:                                     # trunk carries the whole served airflow
        runs.insert(0, (TRUNK_NAME, total_cfm))
        lengths.insert(0, None)

    worst = max((l for l in lengths if l is not None), default=None)
    derived = unit is not None and asp is not None and worst is not None and worst > 0
    friction_rate = (asp * 100.0 / worst) if derived else fallback_fr

    return DuctPlan(
        unit=unit, unit_name=unit_name, friction_rate=friction_rate, derived=derived,
        available_static_pressure=asp, worst_length_ft=worst, room_loads=room_loads,
        runs=runs, lengths=(lengths if unit is not None else None))


def size_from_plan(plan: DuctPlan) -> ductd.DuctResult | None:
    """Size the plan's runs into ducts (None when there are no served runs)."""
    if not plan.runs:
        return None
    return ductd.size_ducts(plan.runs, friction_rate=plan.friction_rate, lengths=plan.lengths)
