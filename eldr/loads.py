"""Phase 1 loads: heating (conduction + infiltration) and cooling (Manual J 1b).

Cooling is orientation-resolved: window solar gain is bucketed by compass facing,
so west/east glass loads more than north. Constants are demo-grade, hardcoded.
"""
from __future__ import annotations
from dataclasses import dataclass
from eldr import geometry, sidecar, units

# Peak solar heat gain by window facing, BTU/hr per ft^2 of glass (demo-grade).
SOLAR_HGF = {"N": 20.0, "E": 75.0, "S": 45.0, "W": 75.0}
INTERNAL_SENSIBLE_PER_OCCUPANT = 230.0   # BTU/hr sensible per person
INTERNAL_LATENT_PER_OCCUPANT = 200.0    # BTU/hr latent per person
APPLIANCE_SENSIBLE_BTUH = 1200.0        # lights + appliances baseline
LATENT_GRAINS_DIFF = 30.0               # indoor/outdoor humidity ratio diff (grains/lb)
COOLING_SUPPLY_DT_F = 20.0              # supply-air below room, for cooling CFM


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


def heating_load(env: geometry.Envelope, sc: sidecar.SideCar) -> HeatingResult:
    dt = sc.design.heating_delta_t
    by_category: dict[str, float] = {}
    conduction = 0.0
    for s in env.surfaces:
        if s.category not in sc.assemblies:
            raise KeyError(f"no assembly U-value for category '{s.category}' in side-car")
        q = sc.assemblies[s.category] * s.area_ft2 * dt
        by_category[s.category] = by_category.get(s.category, 0.0) + q
        conduction += q

    infil_cfm = sc.infiltration_ach * env.volume_ft3 / 60.0
    infiltration = units.SENSIBLE_FACTOR * infil_cfm * dt

    total = conduction + infiltration
    cfm = total / (units.SENSIBLE_FACTOR * sc.design.supply_air_rise_f)
    return HeatingResult(conduction, infiltration, total, cfm, by_category)


def cooling_load(env: geometry.Envelope, sc: sidecar.SideCar) -> CoolingResult:
    """Sensible (conduction + orientation-resolved solar + internal) + latent cooling load."""
    if sc.cooling is None:
        raise ValueError("cooling requires a `cooling` block in the side-car")
    c = sc.cooling
    dt = c.cooling_delta_t
    by_category: dict[str, float] = {}

    conduction = 0.0
    for s in env.surfaces:
        if s.category not in sc.assemblies:
            raise KeyError(f"no assembly U-value for category '{s.category}' in side-car")
        q = sc.assemblies[s.category] * s.area_ft2 * dt
        by_category[s.category] = by_category.get(s.category, 0.0) + q
        conduction += q

    solar = 0.0
    for orient, area_ft2 in env.windows_by_orientation.items():
        q = area_ft2 * c.shgc * SOLAR_HGF.get(orient, SOLAR_HGF["S"])
        by_category[f"solar-{orient}"] = q
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
