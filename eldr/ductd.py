"""Manual D (demo-grade): size round supply ducts by the equal-friction method.

Given each run's airflow (CFM) and a design friction rate, size a round duct and
report its velocity. Round-only. When a run's total effective length is supplied,
it also reports the friction pressure drop over that length. A full Manual D
derives the friction rate from available static pressure ÷ the worst run's
effective length (see ductmodel); here the rate is an input. Constants are demo-grade.
"""
from __future__ import annotations
from dataclasses import dataclass
import math

# Standard round duct sizes (inches).
STANDARD_SIZES = (4, 5, 6, 7, 8, 9, 10, 12, 14, 16, 18, 20, 22, 24)
DEFAULT_FRICTION_RATE = 0.08     # in.wc per 100 ft
VELOCITY_HIGH_FPM = 900          # above this a supply duct gets noisy
# Friction-chart fit for galvanized round duct: FR = 0.109136 * Q^1.9 / D^5.02
# (FR in in.wc/100ft, Q in CFM, D in inches). Solve for D given FR and Q.
_FRICTION_COEFF = 0.109136
_Q_EXP = 1.9
_D_EXP = 5.02


@dataclass(frozen=True)
class DuctRun:
    name: str
    cfm: float
    exact_dia_in: float          # unrounded diameter that meets the friction rate
    standard_dia_in: int         # next standard size up
    velocity_fpm: float          # air velocity at the standard size
    flag: str                    # "" or "high" (velocity over the noise threshold)
    length_ft: float | None = None          # total effective length, if known (from the model)
    pressure_drop_inwc: float | None = None  # friction_rate * length/100, if length known


@dataclass(frozen=True)
class DuctResult:
    friction_rate: float
    runs: tuple[DuctRun, ...]


def exact_diameter_in(cfm: float, friction_rate: float) -> float:
    """Unrounded round-duct diameter (in) that carries `cfm` at `friction_rate`."""
    return (_FRICTION_COEFF * cfm ** _Q_EXP / friction_rate) ** (1.0 / _D_EXP)


def _next_standard(dia_in: float) -> int:
    for s in STANDARD_SIZES:
        if s >= dia_in:
            return s
    return STANDARD_SIZES[-1]


def velocity_fpm(cfm: float, dia_in: float) -> float:
    """Air velocity (ft/min) for `cfm` through a round duct of diameter `dia_in`."""
    return 183.346 * cfm / (dia_in * dia_in)


def size_ducts(runs, friction_rate: float = DEFAULT_FRICTION_RATE,
               lengths=None) -> DuctResult:
    """Size each (name, cfm) run to a round duct at the given friction rate.

    `lengths` is an optional parallel list of total effective lengths (ft); when given,
    each run also reports its friction pressure drop (friction_rate * length / 100).
    """
    if not math.isfinite(friction_rate) or friction_rate <= 0:
        raise ValueError("friction_rate must be finite and > 0")
    runs = list(runs)
    if lengths is not None and len(lengths) != len(runs):
        raise ValueError("lengths must be parallel to runs")
    sized = []
    for i, (name, cfm) in enumerate(runs):
        if not math.isfinite(cfm) or cfm <= 0:
            raise ValueError(f"run '{name}': cfm must be finite and > 0")
        exact = exact_diameter_in(cfm, friction_rate)
        std = _next_standard(exact)
        vel = velocity_fpm(cfm, std)
        length = None if lengths is None else lengths[i]
        if length is not None and (not math.isfinite(length) or length < 0):
            raise ValueError(f"run '{name}': length must be finite and >= 0")
        drop = None if length is None else friction_rate * length / 100.0
        sized.append(DuctRun(name=name, cfm=cfm, exact_dia_in=exact,
                             standard_dia_in=std, velocity_fpm=vel,
                             flag="high" if vel > VELOCITY_HIGH_FPM else "",
                             length_ft=length, pressure_drop_inwc=drop))
    return DuctResult(friction_rate=friction_rate, runs=tuple(sized))
