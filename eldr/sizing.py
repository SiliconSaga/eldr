"""Manual S (demo-grade): turn a heating load into an equipment-sizing verdict.

Heating-basis, thresholds hardcoded. Not ACCA-certified — the pipeline
(model -> load -> size) is the point; cooling (1b) refines it later.
"""
from __future__ import annotations
from dataclasses import dataclass
import math
from eldr import loads, sidecar

TONS_PER_BTUH = 1.0 / 12000.0     # 12,000 BTU/hr = 1 ton of capacity
STEP_TONS = 0.5                    # equipment comes in half-ton nominal sizes
OVERSIZE_PCT = 15.0               # above this -> short-cycling risk
UNDERSIZE_PCT = -10.0            # below this -> won't hold design temp


@dataclass(frozen=True)
class SizingResult:
    load_tons: float
    rec_low_tons: float
    rec_high_tons: float
    existing_tons: float | None
    oversize_pct: float | None
    verdict: str


def size_equipment(result: loads.HeatingResult, sc: sidecar.SideCar) -> SizingResult:
    load_tons = result.total_btuh * TONS_PER_BTUH
    # Bracket the load in a half-ton window: floor to the nearest 0.5, plus one step.
    rec_low = math.floor(load_tons / STEP_TONS) * STEP_TONS
    rec_high = rec_low + STEP_TONS

    existing = sc.existing_tons
    if existing is None:
        return SizingResult(load_tons, rec_low, rec_high, None, None, "no existing unit given")

    oversize_pct = (existing - load_tons) / load_tons * 100.0
    if oversize_pct > OVERSIZE_PCT:
        verdict = "oversized"
    elif oversize_pct < UNDERSIZE_PCT:
        verdict = "undersized"
    else:
        verdict = "well-matched"
    return SizingResult(load_tons, rec_low, rec_high, existing, oversize_pct, verdict)
