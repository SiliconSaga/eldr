"""Phase 1 heating load: conduction (sum U*A*dT) + infiltration, and supply CFM."""
from __future__ import annotations
from dataclasses import dataclass
from eldr import geometry, sidecar, units


@dataclass(frozen=True)
class HeatingResult:
    conduction_btuh: float
    infiltration_btuh: float
    total_btuh: float
    cfm: float
    by_category: dict[str, float]


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
