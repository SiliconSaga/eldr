"""Manual S (demo-grade): turn the design load into an equipment-sizing verdict.

Sizes on the LARGER of heating and (when present) cooling — the honest Manual S
basis. Thresholds hardcoded; textbook-honest — the recommended size is the
smallest standard unit that *meets* the load (meet-or-exceed). Not ACCA-certified.
"""
from __future__ import annotations
from dataclasses import dataclass
import math
from eldr import sidecar

TONS_PER_BTUH = 1.0 / 12000.0     # 12,000 BTU/hr = 1 ton of capacity
STEP_TONS = 0.5                    # equipment comes in half-ton nominal sizes
OVERSIZE_PCT = 15.0               # existing unit above this -> short-cycling risk
UNDERSIZE_PCT = -10.0            # existing unit below this -> won't hold design temp


@dataclass(frozen=True)
class SizingResult:
    load_tons: float
    basis: str                      # which load drives sizing: "heating" or "cooling"
    rec_tons: float                 # smallest standard size that meets the load
    rec_oversize_pct: float         # how far rec_tons exceeds the load
    next_tons: float                # one size up from rec_tons
    next_oversize_pct: float
    existing_tons: float | None
    existing_oversize_pct: float | None
    verdict: str


def size_equipment(heating_btuh: float, sc: sidecar.SideCar,
                   cooling_btuh: float | None = None) -> SizingResult:
    """Size on the larger of heating/cooling: load -> recommended size, next size, verdict."""
    if not math.isfinite(heating_btuh) or heating_btuh <= 0:
        raise ValueError("heating load must be finite and > 0 to size equipment")
    if cooling_btuh is not None and (not math.isfinite(cooling_btuh) or cooling_btuh <= 0):
        raise ValueError("cooling load must be finite and > 0 to size equipment")
    if cooling_btuh is not None and cooling_btuh > heating_btuh:
        design_btuh, basis = cooling_btuh, "cooling"
    else:
        design_btuh, basis = heating_btuh, "heating"
    load_tons = design_btuh * TONS_PER_BTUH
    if not math.isfinite(load_tons) or load_tons <= 0:
        raise ValueError("design load must be finite and > 0 to size equipment")

    def oversize(size_tons: float) -> float:
        return (size_tons - load_tons) / load_tons * 100.0

    # Manual S: pick the smallest standard (half-ton) size that meets the load.
    rec_tons = math.ceil(load_tons / STEP_TONS) * STEP_TONS
    next_tons = rec_tons + STEP_TONS

    existing = sc.existing_tons
    if existing is None:
        verdict, existing_pct = "no existing unit given", None
    else:
        existing_pct = oversize(existing)
        if existing_pct > OVERSIZE_PCT:
            verdict = "oversized"
        elif existing_pct < UNDERSIZE_PCT:
            verdict = "undersized"
        else:
            verdict = "well-matched"

    return SizingResult(
        load_tons=load_tons,
        basis=basis,
        rec_tons=rec_tons,
        rec_oversize_pct=oversize(rec_tons),
        next_tons=next_tons,
        next_oversize_pct=oversize(next_tons),
        existing_tons=existing,
        existing_oversize_pct=existing_pct,
        verdict=verdict,
    )
