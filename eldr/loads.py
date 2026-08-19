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
# Surfaces that sit against soil rather than outdoor air.
#
# HEATING loads them at the FULL outdoor design ΔT, exactly like an exterior wall. That is
# not an oversight: in Manual J the soil path lives in the *assembly*, not in the ΔT. A
# below-grade U-value is an EFFECTIVE one — the wall's own resistance plus the resistance
# of the path through the soil to grade — which is why a certified report lists bare 8"
# masonry at U-0.29 when the masonry alone is nearer U-1.0, and why the same construction
# gets a lower U the deeper it sits (a longer soil path). Applying a ground ΔT on top of
# such a U discounts the same soil twice; running the certified numbers backwards, every
# below-grade row on a professional report divides out to the full outdoor design ΔT.
# The side-car owns the soil, therefore, and `assemblies.basement_wall` / `assemblies.floor`
# must be effective below-grade values — see README § Modeling decisions.
#
# COOLING is genuinely different and keeps its ground coupling: soil sitting *below* the
# summer setpoint is a heat sink, not a source, so these surfaces are held at
# max(0, ground − indoor) — zero for any normal house. Certified reports agree: a basement
# slab's cooling HTM is 0.00.
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
    # Sensible infiltration, listed before the aggregate exactly as `HeatingResult` lists
    # `conduction_btuh` and `infiltration_btuh` before `total_btuh`: it is a COMPONENT of
    # `sensible_btuh`, not an addition to it. It stays a named field rather than a
    # `by_category` entry because `by_category` is the *surface* breakdown — every other
    # key in it is a category with an area and a U-value, or a solar octant — and heating
    # already draws the line in that same place.
    infiltration_btuh: float
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

    The resolver takes the whole surface, not just its category: a surface facing a buffer
    space uses that space's own factor (two `buffer_floor`s over different spaces differ),
    below-grade surfaces are ground-coupled in summer and air-coupled in winter, and
    everything else uses the outdoor-air ΔT. Returns (total, by_category).
    """
    by_category: dict[str, float] = {}
    total = 0.0
    for s in surfaces:
        u = _u_value(s, assemblies)
        q = u * s.area_ft2 * dt_for(s)
        by_category[s.category] = by_category.get(s.category, 0.0) + q
        total += q
    return total, by_category


@dataclass(frozen=True)
class CoverageRow:
    """One (category, assembly) bucket of the envelope, for the coverage table."""
    category: str
    assembly: str | None      # None = the untagged remainder of the category
    u_value: float
    area_ft2: float
    count: int


def assembly_coverage(surfaces, assemblies) -> list[CoverageRow]:
    """Area and surface count per (category, assembly), for categories that MIX.

    A category whose surfaces are all untagged is omitted: the table exists to show a
    split, and listing every category unsplit would bury the one that matters.

    This is also the only detector available for a LOST tag. When a wall is redrawn in
    Sweet Home 3D its id and its properties go with it, so nothing can report "this used
    to be tagged" — but a variant's area shrinking between two runs is visible here, and
    the archived runs are what make that comparison possible.
    """
    buckets: dict[tuple[str, str | None], tuple[float, int]] = {}
    for s in surfaces:
        key = (s.category, s.assembly)
        area, count = buckets.get(key, (0.0, 0))
        buckets[key] = (area + s.area_ft2, count + 1)
    mixed = {cat for cat, asm in buckets if asm is not None}
    rows = []
    for (category, assembly), (area, count) in buckets.items():
        if category not in mixed:
            continue
        with warnings.catch_warnings():        # the table reports; it does not re-warn
            warnings.simplefilter("ignore")
            u = _u_value(geometry.Surface(category, 0.0, assembly=assembly), assemblies)
        rows.append(CoverageRow(category, assembly, u, area, count))
    # Category, then variants alphabetically, then the untagged remainder LAST — the
    # remainder is the baseline the variants are exceptions to, and reads better beneath
    # them than above them.
    return sorted(rows, key=lambda r: (r.category, r.assembly is None, r.assembly or ""))


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


def _u_value(surface, assemblies):
    """U-value for a surface: its own assembly if it declared one, else its category's.

    A tag selects among the several assemblies of ONE category — `exterior_wall/r0` for
    the uninsulated sections of a wall that is mostly R-11. It deliberately cannot change
    the category: that would let a mis-tag move a surface into or out of the envelope
    silently, where a wrong U-value only makes a number wrong.

    Both failure modes fall back to the category rather than raising, because a stale tag
    on one wall should not stop the whole house computing — but neither is silent.
    """
    if surface.assembly is not None:
        category, _variant = sidecar.split_assembly_key(surface.assembly)
        if category != surface.category:
            warnings.warn(
                f"surface tagged `{surface.assembly}` but it is a '{surface.category}' — "
                f"a tag selects a U-value within a category, it cannot change one; "
                f"ignoring the tag and using `{surface.category}`", stacklevel=4)
        elif surface.assembly in assemblies:
            return assemblies[surface.assembly]
        else:
            warnings.warn(
                f"surface tagged `{surface.assembly}` but the side-car declares no such "
                f"assembly — falling back to `{surface.category}`; declare "
                f"`assemblies.{surface.assembly}` or fix the tag", stacklevel=4)
    return _category_u_value(surface.category, assemblies)


def _category_u_value(category, assemblies):
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
    """ΔT resolver for heating: the space's own factor for any surface facing a buffer
    space, outdoor-air ΔT everywhere else — below-grade surfaces included."""
    air = design.heating_delta_t
    indoor, outdoor = design.indoor_heating_f, design.indoor_heating_f - air

    def dt(s):
        if s.category in GROUND_COUPLED_CATEGORIES:
            # The full air ΔT, deliberately — the soil path is in the assembly U, see
            # GROUND_COUPLED_CATEGORIES. Written as its own branch rather than left to
            # fall through so that it wins over the buffer-space branch below exactly as
            # it does in `_cooling_dt_for`: the two resolvers must never disagree about
            # what a below-grade surface IS, only about the ΔT it sees.
            return air
        if s.space is not None:
            policy = spaces.policy_for(s.space, declared_spaces)
            return spaces.heating_factor(policy, indoor, outdoor) * air
        if s.category == BUFFER_WALL_CATEGORY:
            return BUFFER_FACTOR * air
        return air
    return dt


# Re-exported, not re-declared: `sidecar` needs the same name for its hot-attic bound and
# cannot import this module, so the value lives in `spaces`. Consumers keep using
# `loads.ATTIC_SPACE`, which is where the substitution below reads as belonging.
ATTIC_SPACE = spaces.ATTIC_SPACE


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


def unbound_spaces(env: geometry.Envelope, declared: dict) -> list[str]:
    """Declared `spaces:` keys that no surface in this envelope faces, sorted.

    A space is bound by NAME, and the name is produced by the geometry — an unconditioned
    level's own SH3D name lowercased, or a level's `below_void` / `above_void`. So a
    `crawl_space:` entry against a level called `Crawlspace` binds to nothing and the
    policy it declares is never consulted; the surface falls back to the built-in default
    and the load comes out at 0.5 of ΔT while the side-car says 32 °F.

    The room sub-envelopes are searched too. They carry the same spaces as the whole-house
    surface list today, but they are a second, independently built list, and a space that
    reaches only the per-room path is declared for a reason.
    """
    faced = {s.space for s in env.surfaces if s.space is not None}
    faced.update(s.space for r in env.rooms for s in r.surfaces if s.space is not None)
    return sorted(set(declared) - faced)


def _warn_unbound_spaces(env: geometry.Envelope, declared: dict) -> None:
    """Warn once per load call about `spaces:` keys that bind to nothing.

    Mirrors the unknown-wall-id and unknown-level-name warnings in `geometry`, which is
    the precedent: both name a side-car key that addresses something absent from the
    model. `spaces:` was the odd one out, and it is the block most likely to be
    hand-copied out of the README with a name of the reader's own invention.

    It lives here rather than in `sidecar` because the resolved space NAMES do not exist
    until the envelope does — a side-car cannot be checked against the model it has not
    been paired with yet — and here rather than in `geometry` because `geometry` must not
    depend on `sidecar`.
    """
    unbound = unbound_spaces(env, declared)
    if unbound:
        # stacklevel=3: this frame, then the public entry point, landing on ITS caller.
        warnings.warn(
            f"side-car `spaces` declare names no surface in this model faces (renamed or "
            f"typo'd?): {unbound} — their policies are ignored and those surfaces load at "
            f"the built-in default instead. Spaces in play: "
            f"{sorted({s.space for s in env.surfaces if s.space is not None})}",
            stacklevel=3)


def heating_load(env: geometry.Envelope, sc: sidecar.SideCar) -> HeatingResult:
    _warn_unbound_spaces(env, sc.spaces)
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
    _warn_unbound_spaces(env, sc.spaces)
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

    # ONE infiltration airflow, feeding both halves of the load. Outdoor air arriving at
    # the 1% design temperature into a cooler house brings sensible heat as well as
    # moisture, and for a long time only the moisture reached this function: the sensible
    # side was simply absent, which understated the cooling load, the SHR and — because
    # design airflow is sized on sensible alone — every CFM downstream of it. `heating_load`
    # has carried the identical term since the first cut; this is the same physics with
    # the summer ΔT substituted for the winter one.
    infil_cfm = sc.infiltration_ach * env.volume_ft3 / 60.0
    infiltration = units.SENSIBLE_FACTOR * infil_cfm * c.cooling_delta_t

    sensible = conduction + solar + internal_sensible + infiltration
    latent = (c.occupants * INTERNAL_LATENT_PER_OCCUPANT
              + 0.68 * infil_cfm * LATENT_GRAINS_DIFF)

    total = sensible + latent
    cfm = sensible / (units.SENSIBLE_FACTOR * COOLING_SUPPLY_DT_F)
    return CoolingResult(infiltration, sensible, latent, total, cfm, by_category)


def per_room_loads(env: geometry.Envelope, sc: sidecar.SideCar) -> list[RoomLoad]:
    """Per-room heating + (optional) cooling-sensible loads and design CFM (Manual J 1c).

    Each room's load comes from the exterior walls, windows, doors and ceiling/floor
    attributed to *it* (plus infiltration on its own volume). Design CFM is the larger
    of the heating and cooling airflows — each sized at its own supply-air ΔT — so a
    duct is sized for the worse mode. Internal (occupant + appliance) sensible gain is
    shared across conditioned rooms by floor area; unconditioned rooms get none.
    """
    _warn_unbound_spaces(env, sc.spaces)
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
            # The room's OWN air, on the room's own volume, mirroring `h_infil` above —
            # ungated on `conditioned` for the same reason the heating line is. This is the
            # per-room half of the same missing term, and the one with teeth: cooling CFM
            # is sized on this number, and duct diameters are sized on that CFM, so leaving
            # it out sized ducts for an airflow the house never asks for.
            c_infil = (units.SENSIBLE_FACTOR * (sc.infiltration_ach * r.volume_ft3 / 60.0)
                       * cool.cooling_delta_t)
            cooling = c_cond + solar + internal + c_infil

        cfm_heat = heating / (units.SENSIBLE_FACTOR * sc.design.supply_air_rise_f)
        cfm_cool = (cooling / (units.SENSIBLE_FACTOR * COOLING_SUPPLY_DT_F)
                    if cool is not None else 0.0)
        out.append(RoomLoad(name=r.name, level_id=r.level_id, conditioned=r.conditioned,
                            heating_btuh=heating, cooling_btuh=cooling,
                            cfm=max(cfm_heat, cfm_cool)))
    return out
