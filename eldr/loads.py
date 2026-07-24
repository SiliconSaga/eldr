"""Phase 1 loads: heating (conduction + infiltration) and cooling (Manual J 1b).

Cooling is orientation-resolved: window solar gain reads each window's exact true
compass bearing (continuous), so west/east glass loads more than north. Constants
are demo-grade, hardcoded.
"""
from __future__ import annotations
from collections.abc import Callable
from dataclasses import dataclass
import bisect
from eldr import geometry, sidecar, units

# Peak solar heat gain (BTU/hr per ft^2 of glass) at the four cardinal facings;
# any bearing between them is linearly interpolated (demo-grade).
_SOLAR_ANCHORS = [(0.0, 20.0), (90.0, 75.0), (180.0, 45.0), (270.0, 75.0), (360.0, 20.0)]
_OCTANTS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")
INTERNAL_SENSIBLE_PER_OCCUPANT = 230.0   # BTU/hr sensible per person
INTERNAL_LATENT_PER_OCCUPANT = 200.0    # BTU/hr latent per person
APPLIANCE_SENSIBLE_BTUH = 1200.0        # lights + appliances baseline
LATENT_GRAINS_DIFF = 30.0               # indoor/outdoor humidity ratio diff (grains/lb)
COOLING_SUPPLY_DT_F = 20.0              # supply-air below room, for cooling CFM
# Surfaces coupled to soil rather than outdoor air — they see the ground ΔT, not the
# air ΔT (a basement wall against 50°F soil loses far less than one against 15°F air).
GROUND_COUPLED_CATEGORIES = frozenset({"basement_wall", "floor"})


@dataclass(frozen=True)
class HeatingResult:
    conduction_btuh: float
    infiltration_btuh: float
    total_btuh: float
    cfm: float
    by_category: dict[str, float]


@dataclass(frozen=True)
class CoolingResult:
    sensible_btuh: float
    latent_btuh: float
    total_btuh: float
    cfm: float
    by_category: dict[str, float]      # conduction cats + solar-<orient> + internal


@dataclass(frozen=True)
class RoomLoad:
    """Per-room (Manual J 1c) loads and design airflow."""
    name: str
    level_id: str
    conditioned: bool
    heating_btuh: float
    cooling_btuh: float                # sensible (the CFM-sizing basis)
    cfm: float


def _conduction(surfaces, assemblies, dt_for):
    """UA·ΔT conduction over a surface list; `dt_for(category)` gives the ΔT to use.

    Per-category ΔT lets below-grade surfaces use the ground ΔT while everything
    else uses the outdoor-air ΔT. Returns (total, by_category).
    """
    by_category: dict[str, float] = {}
    total = 0.0
    for s in surfaces:
        if s.category not in assemblies:
            raise KeyError(f"no assembly U-value for category '{s.category}' in side-car")
        q = assemblies[s.category] * s.area_ft2 * dt_for(s.category)
        by_category[s.category] = by_category.get(s.category, 0.0) + q
        total += q
    return total, by_category


def _heating_dt_for(design) -> Callable[[str], float]:
    """ΔT resolver for heating: ground ΔT below grade, outdoor-air ΔT elsewhere."""
    air, ground = design.heating_delta_t, design.ground_heating_delta_t
    return lambda cat: ground if cat in GROUND_COUPLED_CATEGORIES else air


def _cooling_dt_for(design, cooling) -> Callable[[str], float]:
    """ΔT resolver for cooling: outdoor-air ΔT above grade; below grade the surface
    sees the soil, so its ΔT is (ground - indoor), clamped at 0. Normally the soil is
    cooler than the setpoint (a sink -> 0); a warmer configured ground adds real gain."""
    air = cooling.cooling_delta_t
    ground = max(0.0, design.ground_temp_f - cooling.indoor_f)
    return lambda cat: ground if cat in GROUND_COUPLED_CATEGORIES else air


def heating_load(env: geometry.Envelope, sc: sidecar.SideCar) -> HeatingResult:
    dt = sc.design.heating_delta_t
    conduction, by_category = _conduction(env.surfaces, sc.assemblies, _heating_dt_for(sc.design))

    infil_cfm = sc.infiltration_ach * env.volume_ft3 / 60.0
    infiltration = units.SENSIBLE_FACTOR * infil_cfm * dt

    total = conduction + infiltration
    cfm = total / (units.SENSIBLE_FACTOR * sc.design.supply_air_rise_f)
    return HeatingResult(conduction, infiltration, total, cfm, by_category)


def solar_hgf(bearing_deg: float) -> float:
    """Solar heat gain factor (BTU/hr/ft^2) at a true compass bearing, interpolated."""
    b = bearing_deg % 360.0
    i = bisect.bisect_right([a for a, _ in _SOLAR_ANCHORS], b) - 1
    (b0, f0), (b1, f1) = _SOLAR_ANCHORS[i], _SOLAR_ANCHORS[i + 1]
    return f0 + (f1 - f0) * (b - b0) / (b1 - b0)


def octant(bearing_deg: float) -> str:
    """Nearest 8-point compass label ('N'/'NE'/.../'NW') for a bearing, for display."""
    return _OCTANTS[int((bearing_deg % 360.0 + 22.5) // 45.0) % 8]


def cooling_load(env: geometry.Envelope, sc: sidecar.SideCar) -> CoolingResult:
    """Sensible (conduction + orientation-resolved solar + internal) + latent cooling load."""
    if sc.cooling is None:
        raise ValueError("cooling requires a `cooling` block in the side-car")
    c = sc.cooling
    conduction, by_category = _conduction(env.surfaces, sc.assemblies, _cooling_dt_for(sc.design, c))

    # Solar gain per window, using its exact bearing; grouped for display by octant.
    solar = 0.0
    for bearing, area_ft2 in env.windows_by_bearing.items():
        q = area_ft2 * c.shgc * solar_hgf(bearing)
        key = f"solar-{octant(bearing)}"
        by_category[key] = by_category.get(key, 0.0) + q
        solar += q

    internal_sensible = c.occupants * INTERNAL_SENSIBLE_PER_OCCUPANT + APPLIANCE_SENSIBLE_BTUH
    by_category["internal"] = internal_sensible

    sensible = conduction + solar + internal_sensible

    infil_cfm = sc.infiltration_ach * env.volume_ft3 / 60.0
    latent = (c.occupants * INTERNAL_LATENT_PER_OCCUPANT
              + 0.68 * infil_cfm * LATENT_GRAINS_DIFF)

    total = sensible + latent
    cfm = sensible / (units.SENSIBLE_FACTOR * COOLING_SUPPLY_DT_F)
    return CoolingResult(sensible, latent, total, cfm, by_category)


def per_room_loads(env: geometry.Envelope, sc: sidecar.SideCar) -> list[RoomLoad]:
    """Per-room heating + (optional) cooling-sensible loads and design CFM (Manual J 1c).

    Each room's load comes from the exterior walls, windows, doors and ceiling/floor
    attributed to *it* (plus infiltration on its own volume). Design CFM is the larger
    of the heating and cooling airflows — each sized at its own supply-air ΔT — so a
    duct is sized for the worse mode. Internal (occupant + appliance) sensible gain is
    shared across conditioned rooms by floor area; unconditioned rooms get none.
    """
    heat_dt = sc.design.heating_delta_t
    heat_dt_for = _heating_dt_for(sc.design)
    cool = sc.cooling
    cool_dt_for = _cooling_dt_for(sc.design, cool) if cool is not None else None
    cond_area = sum(r.area_ft2 for r in env.rooms if r.conditioned) or 1.0
    internal_total = (cool.occupants * INTERNAL_SENSIBLE_PER_OCCUPANT
                      + APPLIANCE_SENSIBLE_BTUH) if cool is not None else 0.0

    out: list[RoomLoad] = []
    for r in env.rooms:
        h_cond, _ = _conduction(r.surfaces, sc.assemblies, heat_dt_for)
        h_infil = units.SENSIBLE_FACTOR * (sc.infiltration_ach * r.volume_ft3 / 60.0) * heat_dt
        heating = h_cond + h_infil

        cooling = 0.0
        if cool is not None:
            c_cond, _ = _conduction(r.surfaces, sc.assemblies, cool_dt_for)
            solar = sum(area * cool.shgc * solar_hgf(b)
                        for b, area in r.windows_by_bearing.items())
            internal = internal_total * (r.area_ft2 / cond_area) if r.conditioned else 0.0
            cooling = c_cond + solar + internal

        cfm_heat = heating / (units.SENSIBLE_FACTOR * sc.design.supply_air_rise_f)
        cfm_cool = (cooling / (units.SENSIBLE_FACTOR * COOLING_SUPPLY_DT_F)
                    if cool is not None else 0.0)
        out.append(RoomLoad(name=r.name, level_id=r.level_id, conditioned=r.conditioned,
                            heating_btuh=heating, cooling_btuh=cooling,
                            cfm=max(cfm_heat, cfm_cool)))
    return out
