"""Buffer-space temperature policies.

A *space* is a named unconditioned volume adjacent to conditioned rooms — an attic, a
crawlspace, an attached garage. A surface facing one sees only part of the design ΔT,
because the space floats somewhere between indoors and outdoors.

Precedence, most to least defensible:
  1. an observed design-day temperature (winter_temp_f / summer_temp_f)
  2. an explicit factor (fraction of design ΔT)
  3. the vented shorthand (vented -> 1.0, sealed -> 0.5)
  4. the space's built-in default

Temperature wins because it is an *observation*, not a model: "the crawl gets near
freezing on the coldest days" is a stronger claim to hand an HVAC partner than "we
assumed 50% of ΔT", and it short-circuits chains that would otherwise need modeling
(this crawlspace is partly vented to outdoors AND open into the garage).
"""
from __future__ import annotations
from dataclasses import dataclass

# A buffer sitting midway between indoor and outdoor. This is the long-standing
# BUFFER_FACTOR from loads.py, now the *default* rather than the only option.
UNVENTED_FACTOR = 0.5
# A vented space tracks outdoor air closely enough to load at the full ΔT.
VENTED_FACTOR = 1.0

# A sun-heated attic runs far above outdoor air. Demo-grade: a flat solar uplift scaled
# by roof absorptance, NOT a real energy balance (no roof area, ventilation rate or
# radiant barrier). Dark asphalt shingle ~0.85. Refrhus carries PV over part of its
# roof, which shades the deck beneath — see the design doc's deferred note; until roof
# planes are modeled, set cooling.attic_temp_f to a blended observed value instead.
SOL_AIR_UPLIFT_F = 50.0
DEFAULT_ROOF_ABSORPTANCE = 0.85


def sol_air_attic_temp_f(outdoor_f: float, absorptance: float = DEFAULT_ROOF_ABSORPTANCE) -> float:
    """Estimated peak attic air temperature from roof solar gain."""
    return outdoor_f + SOL_AIR_UPLIFT_F * absorptance


@dataclass(frozen=True)
class SpacePolicy:
    name: str
    winter_temp_f: float | None = None
    summer_temp_f: float | None = None
    factor: float | None = None
    vented: bool | None = None


# Built-in defaults. `attic` is unvented per the Refrhus owner's observation (no ridge
# or soffit ventilation visible, and it does not get outdoor-cold). NOTE this diverges
# from typical ACCA practice, which loads a vented attic at full outdoor temperature for
# heating — declare `vented: true` to restore that. The report echoes which was used.
# `vented`/`factor` are WINTER shorthands: in summer an attic with no declared
# `summer_temp_f` is resolved by loads.py to a hot-attic temperature (see
# `sol_air_attic_temp_f`), because venting lowers a sunlit attic without making it track
# outdoor air. Declare `summer_temp_f` to override that.
DEFAULT_POLICIES: dict[str, SpacePolicy] = {
    "attic": SpacePolicy(name="attic", vented=False),
    "crawlspace": SpacePolicy(name="crawlspace", vented=False),
    "garage": SpacePolicy(name="garage", vented=False),
    "outdoor": SpacePolicy(name="outdoor", factor=1.0),
}


def policy_for(name: str, declared: dict[str, SpacePolicy]) -> SpacePolicy:
    """The policy for a space name: side-car declaration, else built-in default, else bare."""
    if name in declared:
        return declared[name]
    if name in DEFAULT_POLICIES:
        return DEFAULT_POLICIES[name]
    return SpacePolicy(name=name)


def _factor(policy: SpacePolicy, space_temp_f: float | None,
            inner_f: float, outer_f: float) -> float:
    """Resolve a ΔT fraction, applying the precedence order.

    Clamped at 0 (a buffer on the wrong side of the setpoint contributes no load in
    this mode) but deliberately NOT clamped above 1: a sun-heated attic is hotter than
    outdoor air, so its cooling ΔT legitimately exceeds the outdoor one.
    """
    if space_temp_f is not None:
        span = outer_f - inner_f
        if span == 0.0:
            return 0.0
        return max(0.0, (space_temp_f - inner_f) / span)
    if policy.factor is not None:
        return policy.factor
    if policy.vented is not None:
        return VENTED_FACTOR if policy.vented else UNVENTED_FACTOR
    return UNVENTED_FACTOR


def heating_factor(policy: SpacePolicy, indoor_f: float, outdoor_f: float) -> float:
    """Fraction of the heating design ΔT a surface facing this space sees."""
    return _factor(policy, policy.winter_temp_f, indoor_f, outdoor_f)


def cooling_factor(policy: SpacePolicy, indoor_f: float, outdoor_f: float) -> float:
    """Fraction of the cooling design ΔT a surface facing this space sees.

    Report / export consumers must pass the policy returned by
    `loads.effective_cooling_policy`, never a bare `policy_for` result: the attic's summer
    temperature is substituted at load time, so a *declared* policy rendered straight from
    here shows a factor the engine never used (0.5 against an applied 3.66, on Refrhus).
    Named here because someone rendering a cooling factor starts at `spaces`, not `loads`.
    """
    return _factor(policy, policy.summer_temp_f, indoor_f, outdoor_f)
