"""Phase 1 loads: heating (conduction + infiltration) and cooling (Manual J 1b).

Cooling is orientation-resolved: window solar gain reads each window's exact true
compass bearing (continuous), so west/east glass loads more than north. Constants
are demo-grade, hardcoded.
"""
from __future__ import annotations
from collections.abc import Callable
from dataclasses import dataclass
import bisect
import dataclasses
import warnings
from eldr import geometry, sidecar, spaces, units

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

# ============================ BUFFER WALLS ============================
# A `buffer_wall` faces a semi-conditioned buffer space (an attached garage, a vented
# crawlspace) rather than the outdoors. It's neither exterior nor interior: the buffer
# floats between indoor and outdoor, so the wall sees only a FRACTION of the design ΔT.
# BUFFER_FACTOR is that fraction (0.5 ≈ the buffer sits midway). Demo-grade, one factor
# for all buffers. A `buffer_wall` with no assembly U falls back to `exterior_wall`.
BUFFER_WALL_CATEGORY = "buffer_wall"
BUFFER_FACTOR = spaces.UNVENTED_FACTOR
# =====================================================================

# Surfaces whose U-value may borrow a related category's assembly when unset. The chain
# keeps the engine usable on a side-car written before these categories existed, but a
# borrow is a STAND-IN, not a measurement, and every one of them warns — see `_u_value`.
_U_FALLBACKS = {
    BUFFER_WALL_CATEGORY: ("exterior_wall",),
    "buffer_floor": ("exposed_floor", "floor"),
    "exposed_floor": ("floor",),
}

# How wrong a borrow can be, per (recipient, donor) pair — the warning says so rather
# than repeating one blanket claim. Anything borrowing `floor` is the severe case: a
# side-car's `floor` is a slab's *effective* whole-area U (deliberately tiny, because the
# real loss is perimeter-edge), while the borrower is a genuine framed assembly. A buffer
# wall borrowing an exterior wall is the mild case — same construction, different
# boundary — so claiming an order of magnitude there would cry wolf.
_BORROW_SEVERITY = {
    ("buffer_floor", "floor"): "a slab's effective whole-area U can be an order of "
                               "magnitude below a framed floor's",
    ("exposed_floor", "floor"): "a slab's effective whole-area U can be an order of "
                                "magnitude below a framed floor's",
    ("buffer_floor", "exposed_floor"): "both are framed floors, but over spaces at "
                                       "different temperatures",
    (BUFFER_WALL_CATEGORY, "exterior_wall"): "same construction, different boundary — "
                                             "usually close, but not measured",
}
_BORROW_SEVERITY_DEFAULT = "they are related but not equivalent"


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
    """UA·ΔT conduction over a surface list; `dt_for(surface)` gives the ΔT to use.

    The resolver takes the whole surface, not just its category: below-grade surfaces
    use the ground ΔT, a surface facing a buffer space uses that space's own factor
    (two `buffer_floor`s over different spaces differ), and everything else uses the
    outdoor-air ΔT. Returns (total, by_category).
    """
    by_category: dict[str, float] = {}
    total = 0.0
    for s in surfaces:
        u = _u_value(s.category, assemblies)
        q = u * s.area_ft2 * dt_for(s)
        by_category[s.category] = by_category.get(s.category, 0.0) + q
        total += q
    return total, by_category


@dataclass(frozen=True)
class AssemblyBorrow:
    """A U-value a surface category had to borrow because the side-car declares none."""
    category: str          # the recipient — what the surface actually is
    donor: str             # the assembly whose U-value stood in for it
    u_value: float         # the borrowed number
    note: str              # how wrong this particular pairing can be


def assembly_borrow(category: str, assemblies: dict[str, float]) -> AssemblyBorrow | None:
    """The borrow `_u_value` would make for `category`, or None if it needs none.

    The single resolution point for the fallback chain, shared by the runtime warning and
    by the report's disclosure. A consumer re-walking `_U_FALLBACKS` itself would be one
    more copy of a rule that has already changed twice — and a report describing a borrow
    the engine did not make is the same class of bug as a report describing a cooling
    factor the engine did not apply.
    """
    if category in assemblies:
        return None
    for alt in _U_FALLBACKS.get(category, ()):
        if alt in assemblies:
            return AssemblyBorrow(
                category=category, donor=alt, u_value=assemblies[alt],
                note=_BORROW_SEVERITY.get((category, alt), _BORROW_SEVERITY_DEFAULT))
    return None


def _u_value(category, assemblies):
    """U-value for a surface category, borrowing a related assembly when unset.

    A borrow is announced, never silent. The categories are related but not
    interchangeable, and the gap can be an order of magnitude: `floor` in a typical
    side-car is a slab's *effective* U (the real loss is perimeter-edge, so the
    whole-area number is deliberately tiny), while a framed `buffer_floor` over a
    crawlspace is a genuine assembly measured many times higher. Borrowing turns a loud
    failure into a confident wrong number, so the warning is what keeps it honest.
    """
    if category in assemblies:
        return assemblies[category]
    borrow = assembly_borrow(category, assemblies)
    if borrow is not None:
        # stacklevel=4 lands on the *caller's* line: this frame, then `_conduction`,
        # then the public entry point (heating_load / cooling_load / per_room_loads —
        # every `_conduction` call site is one of those three, so the depth is
        # uniform). geometry.py's warns use stacklevel=2 for the same reason; they
        # just sit one frame below their public API instead of three.
        warnings.warn(
            f"no `assemblies.{borrow.category}` in the side-car — borrowing "
            f"`{borrow.donor}`'s U-value ({borrow.u_value}) as a stand-in "
            f"({borrow.note}); declare `assemblies.{borrow.category}` for a real number.",
            stacklevel=4)
        return borrow.u_value
    raise KeyError(f"no assembly U-value for category '{category}' in side-car")


def _heating_dt_for(design, declared_spaces) -> Callable[[geometry.Surface], float]:
    """ΔT resolver for heating: ground ΔT below grade, the space's own factor for any
    surface facing a buffer space, outdoor-air ΔT elsewhere."""
    air, ground = design.heating_delta_t, design.ground_heating_delta_t
    indoor, outdoor = design.indoor_heating_f, design.indoor_heating_f - air

    def dt(s):
        if s.category in GROUND_COUPLED_CATEGORIES:
            return ground
        if s.space is not None:
            policy = spaces.policy_for(s.space, declared_spaces)
            return spaces.heating_factor(policy, indoor, outdoor) * air
        if s.category == BUFFER_WALL_CATEGORY:
            return BUFFER_FACTOR * air
        return air
    return dt


ATTIC_SPACE = "attic"


def effective_cooling_policy(space: str, policy: spaces.SpacePolicy,
                             cooling: sidecar.Cooling | None) -> spaces.SpacePolicy:
    """The policy the cooling resolver ACTUALLY applies, with the attic's summer
    temperature filled in.

    ANY consumer that reports, renders or exports a cooling factor must resolve through
    this, not through `spaces.policy_for` alone. `policy_for` returns what the side-car
    *declared*; for the attic that is usually nothing at all, and the bare unvented
    default yields a cooling factor of 0.5 — while the engine is meanwhile loading the
    ceiling at several times the outdoor ΔT (on Refrhus, 3.66). A report showing the
    declared policy would be confidently wrong, and its own tests would pass while it was.

    The substitution is the hot attic: `vented`/`factor` are winter shorthands and both
    cap the attic at or below outdoor air, which is backwards for a sunlit summer day.
    So unless an observed `summer_temp_f` says otherwise, the attic gets a real
    temperature — the side-car's `cooling.attic_temp_f`, else the sol-air estimate.
    Every other space is returned untouched: a crawlspace or garage sees no roof sun.

    The outdoor design temperature is taken from `cooling.outdoor_1_f` rather than passed
    in beside it. There is only one correct value, so a parameter would be nothing but a
    second channel for a consumer to diverge on — and it demonstrably was one: handing it
    the *heating* outdoor temp was silently accepted. A helper whose whole purpose is
    preventing divergence must not offer a way to diverge.

    `cooling=None` — a heating-only side-car, which the schema explicitly allows — has no
    summer to resolve, so the policy comes back untouched rather than raising.
    """
    if cooling is None or space != ATTIC_SPACE or policy.summer_temp_f is not None:
        return policy
    if cooling.attic_temp_f is not None:
        return dataclasses.replace(policy, summer_temp_f=cooling.attic_temp_f)
    if cooling.outdoor_1_f is None:
        raise ValueError("the sol-air attic estimate needs a resolved cooling.outdoor_1_f "
                         "— set it in the side-car or provide the model's lat/long")
    return dataclasses.replace(
        policy, summer_temp_f=spaces.sol_air_attic_temp_f(cooling.outdoor_1_f))


def applied_cooling_factor(space: str, sc: sidecar.SideCar) -> float | None:
    """The cooling ΔT fraction the engine ACTUALLY applies to a surface facing `space`.

    The whole composition behind that number in one call — `policy_for`, then
    `effective_cooling_policy`, then `cooling_factor` against the cooling block's own
    indoor/outdoor pair. This is the seam every consumer should use.

    `effective_cooling_policy` exists to stop a consumer diverging from the engine, but
    on its own it only removes one of the three steps a consumer has to get right: the
    same three-step composition was spelled out at three call sites, and two of them
    already wrote the outer temperature differently (`indoor_f + cooling_delta_t` versus
    `outdoor_1_f` — algebraically identical, so not a bug, but a second spelling is a
    second thing to keep in sync). A fourth consumer can still get it wrong, which is
    precisely the failure this family of helpers exists to prevent.

    None when there is no summer to resolve — no `cooling` block, or an unresolved
    `cooling.outdoor_1_f`. A heating-only side-car has no cooling factor, and inventing
    one would be the same class of confidently-wrong number as the declared-policy bug.
    """
    c = sc.cooling
    if c is None or c.outdoor_1_f is None:
        return None
    policy = effective_cooling_policy(space, spaces.policy_for(space, sc.spaces), c)
    return spaces.cooling_factor(policy, c.indoor_f, c.outdoor_1_f)


def _cooling_dt_for(sc: sidecar.SideCar) -> Callable[[geometry.Surface], float]:
    """ΔT resolver for cooling. A space's factor may exceed 1: a sun-heated attic runs
    hotter than outdoor air, so its ceiling sees a LARGER ΔT than the outdoor design one.

    Attic temperature precedence, strongest first: an observed `spaces.attic.summer_temp_f`,
    then the side-car's `cooling.attic_temp_f`, then the sol-air estimate — all resolved by
    `applied_cooling_factor`, the seam reporting consumers share with this resolver.
    """
    cooling = sc.cooling
    air = cooling.cooling_delta_t        # raises if outdoor_1_f is unresolved
    ground = max(0.0, sc.design.ground_temp_f - cooling.indoor_f)

    def dt(s):
        if s.category in GROUND_COUPLED_CATEGORIES:
            return ground
        if s.space is not None:
            # never None here: `cooling_delta_t` above already required a resolved
            # outdoor temp, which is the only thing that makes the helper return None.
            return applied_cooling_factor(s.space, sc) * air
        if s.category == BUFFER_WALL_CATEGORY:
            return BUFFER_FACTOR * air
        return air
    return dt


def heating_load(env: geometry.Envelope, sc: sidecar.SideCar) -> HeatingResult:
    dt = sc.design.heating_delta_t
    conduction, by_category = _conduction(env.surfaces, sc.assemblies,
                                          _heating_dt_for(sc.design, sc.spaces))

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
    conduction, by_category = _conduction(env.surfaces, sc.assemblies, _cooling_dt_for(sc))

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
    heat_dt_for = _heating_dt_for(sc.design, sc.spaces)
    cool = sc.cooling
    cool_dt_for = _cooling_dt_for(sc) if cool is not None else None
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
