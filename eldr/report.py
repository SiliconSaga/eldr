"""Render a HeatingResult (and optional Manual S sizing) as a Markdown report."""
from __future__ import annotations
from eldr import (loads, sidecar, spaces as spaces_mod, geometry as geometry_mod,
                  sizing as sizing_mod, climate as climate_mod,
                  ductd as ductd_mod, ductmodel as ductmodel_mod)


def render_heating(result: loads.HeatingResult, sc: sidecar.SideCar,
                   sizing: sizing_mod.SizingResult | None = None,
                   cooling: loads.CoolingResult | None = None,
                   station: climate_mod.Station | None = None,
                   ducts: ductd_mod.DuctResult | None = None,
                   duct_plan: ductmodel_mod.DuctPlan | None = None,
                   env: geometry_mod.Envelope | None = None) -> str:
    """Render the heating load (and optional cooling + Manual S sizing) as Markdown.

    `env` is optional so every existing call site keeps working; without it the report
    simply omits the assumption echoes (level heights, buffer-space factors, voids),
    which live on the Envelope rather than on the computed results.
    """
    d = sc.design
    if d.outdoor_heating_99_f is None:
        raise ValueError("report requires a resolved design.outdoor_heating_99_f "
                         "(run climate resolution or set it in the side-car)")
    lines = [
        "# Eldr — Heating Load (Phase 1, whole-house)",
        "",
        f"- Indoor / 99% outdoor design: **{d.indoor_heating_f:.0f}°F / {d.outdoor_heating_99_f:.0f}°F** "
        f"(ΔT = {d.heating_delta_t:.0f}°F)",
        f"- Infiltration: **{sc.infiltration_ach:.2f} ACH**",
    ]
    if station is not None:
        lines.append(f"- Design temps from nearest station: **{station.name}** "
                     f"(lat/long from the model — approximate; set your ACCA station for accuracy)")
    if loads.BUFFER_WALL_CATEGORY in result.by_category:
        lines.append(f"- ⚠ **Buffer walls** (garage/crawl-adjacent, `buffer_wall` below) are "
                     f"loaded at **{loads.BUFFER_FACTOR:.0%} of the design ΔT** — the buffer space "
                     f"floats between indoor and outdoor.")
    lines += [
        "",
        "| Component | Load (BTU/hr) |",
        "|---|---:|",
    ]
    for cat, q in sorted(result.by_category.items()):
        lines.append(f"| {cat} | {q:,.0f} |")
    lines.append(f"| infiltration | {result.infiltration_btuh:,.0f} |")
    lines.append(f"| **total** | **{result.total_btuh:,.0f}** |")
    lines += [
        "",
        f"**Supply airflow:** {result.cfm:,.0f} CFM "
        f"(at {d.supply_air_rise_f:.0f}°F supply-air rise)",
        "",
        "_Phase 1 whole-house estimate. Not ACCA-certified. Room-by-room to follow._",
    ]
    if env is not None:
        lines += _assumptions_section(env, sc)
    if cooling is not None:
        lines += _cooling_section(cooling, sc)
    if sizing is not None:
        lines += _manual_s_section(sizing)
    if duct_plan is not None:
        # Each room's design CFM is the larger of its heating and cooling airflow,
        # so the whole-house figure it gets compared against has to be the larger
        # of the two as well. Passing heating alone compares different quantities
        # and lets a cooling-dominant house look like it has undrawn rooms.
        design_cfm = max(result.cfm, cooling.cfm) if cooling is not None else result.cfm
        lines += _per_room_section(duct_plan, design_cfm)
    if ducts is not None:
        lines += _duct_section(ducts, duct_plan)
    return "\n".join(lines)


def _assumptions_section(env: geometry_mod.Envelope, sc: sidecar.SideCar) -> list[str]:
    """The assumptions the numbers above are standing on, echoed back.

    Five things the engine decided quietly and the reader cannot otherwise see: the
    storey height each level was given, the ΔT fraction each buffer space resolved to,
    which surfaces claimed an assembly of their own, any U-value that had to be borrowed
    from a related assembly, and any floor area modeled over a space nobody drew — then
    the open questions those decisions leave.
    """
    blocks = (_levels_block(env, sc) + _spaces_block(env, sc)
              + _coverage_block(env, sc)
              + _borrows_block(env, sc) + _voids_block(env, sc)
              + _open_questions_block(env, sc))
    if not blocks:
        return []
    return ["", "## Assumptions behind these numbers"] + blocks


def _levels_block(env: geometry_mod.Envelope, sc: sidecar.SideCar) -> list[str]:
    """Per-level storey height, where it came from, and the volume it contributed.

    Storey height is a silent multiplier on the infiltration term, and Sweet Home 3D
    hands every new level a default one — so a level nobody re-measured looks exactly
    like a level that was.
    """
    if not env.level_heights_ft:
        return []
    lines = ["", "### Level heights", "",
             "| Level | Height used | Source | Conditioned volume |",
             "|---|---:|---|---:|"]
    for name, height_ft in env.level_heights_ft.items():
        spec = sc.levels.get(name)
        source = ("side-car override" if spec is not None and spec.height_ft is not None
                  else "model")
        lines.append(f"| {name} | {height_ft:.1f} ft | {source} | "
                     f"{env.level_volumes_ft3.get(name, 0.0):,.0f} ft³ |")
    lines += [
        "",
        f"_Total conditioned volume **{env.volume_ft3:,.0f} ft³** — the infiltration "
        f"basis. Set `levels.<name>.height_ft` to correct a height Sweet Home 3D "
        f"defaulted. A level contributing 0 ft³ holds no conditioned rooms: a garage or "
        f"crawlspace, a level given `role: ignore`, or a roomless one Eldr treated as "
        f"joists/duct chase (which warns separately). Walls have nothing to do with it — "
        f"a level with conditioned rooms drawn on it contributes whether or not any wall "
        f"is drawn there._",
    ]
    return lines


def _spaces_block(env: geometry_mod.Envelope, sc: sidecar.SideCar) -> list[str]:
    """Every buffer space a surface faces, with the ΔT fraction the engine gave it.

    The summer FACTOR comes from `loads.applied_cooling_factor`, NOT from
    `spaces.policy_for` + `spaces.cooling_factor`. The attic's summer temperature is
    substituted at load time, so the declared policy is not the applied one: on Refrhus
    the declaration renders 0.50 while the engine used 3.66. The effective policy is
    still resolved separately here, but only to say in the *input* column WHERE that
    temperature came from — the number itself has one source.
    """
    names = sorted({s.space for s in env.surfaces if s.space is not None})
    if not names:
        return []
    d = sc.design
    winter_dt = d.heating_delta_t
    indoor_w = d.indoor_heating_f
    outdoor_w = indoor_w - winter_dt
    c = sc.cooling
    summer = c is not None and c.outdoor_1_f is not None
    # The two ΔTs differ by several times (55°F vs 16°F on Refrhus), and this block is
    # rendered BEFORE the cooling section — so at "3.66 × ΔT" the only ΔT the reader has
    # met is the heating one in the header, and 3.66 × 55 = 201°F. Both columns name
    # their own ΔT, and each cell resolves the multiplication itself.
    summer_dt = c.cooling_delta_t if summer else None
    summer_header = ("Summer factor" if summer_dt is None
                     else f"Summer factor (of {summer_dt:.0f}°F ΔT)")
    no_summer = ("no `cooling` block in the side-car" if c is None
                 else "`cooling.outdoor_1_f` unresolved")
    lines = ["", "### Buffer spaces", "",
             f"| Space | Winter factor (of {winter_dt:.0f}°F ΔT) | Winter input "
             f"| {summer_header} | Summer input |",
             "|---|---:|---|---:|---|"]
    for name in names:
        policy = spaces_mod.policy_for(name, sc.spaces)
        origin = ("side-car" if name in sc.spaces
                  else "built-in default" if name in spaces_mod.DEFAULT_POLICIES
                  else "no policy declared")
        winter = spaces_mod.heating_factor(policy, indoor_w, outdoor_w)
        if summer:
            factor = loads.applied_cooling_factor(name, sc)
            effective = loads.effective_cooling_policy(name, policy, c)
            summer_cell = f"{factor:.2f} → {factor * summer_dt:.1f}°F"
            summer_input = _summer_input(policy, effective, c, origin)
        else:
            summer_cell, summer_input = "—", no_summer
        lines.append(f"| {name} | {winter:.2f} → {winter * winter_dt:.1f}°F "
                     f"| {_winter_input(policy, origin)} | {summer_cell} | {summer_input} |")
    lines += [
        "",
        "_A surface facing a buffer space sees the ΔT shown, not the whole design ΔT; the "
        "arrow resolves the factor against that season's own design ΔT. A factor above 1 "
        "is not a bug: a sun-heated attic runs hotter than outdoor air, so the ceiling "
        "beneath it sees a larger ΔT than an exterior wall does._",
    ]
    return lines


def _winter_input(policy: spaces_mod.SpacePolicy, origin: str) -> str:
    """Which of the precedence rungs the winter factor actually came from."""
    if policy.winter_temp_f is not None:
        return f"`winter_temp_f` {policy.winter_temp_f:.1f}°F ({origin})"
    if policy.factor is not None:
        return f"`factor` {policy.factor:.2f} ({origin})"
    if policy.vented is not None:
        return f"`vented: {str(policy.vented).lower()}` ({origin})"
    return f"unvented fallback {spaces_mod.UNVENTED_FACTOR:.2f} ({origin})"


def _summer_input(policy: spaces_mod.SpacePolicy, effective: spaces_mod.SpacePolicy,
                  c: sidecar.Cooling, origin: str) -> str:
    """Same for summer — including the attic temperature the engine substituted.

    That temperature is the least visible number in the whole engine: an outdoor design
    temp plus a flat solar uplift, appearing nowhere in the side-car. Naming it, and
    saying whether it was observed or estimated, is most of the point of this block.
    """
    if policy.summer_temp_f is not None:
        return f"`summer_temp_f` {policy.summer_temp_f:.1f}°F ({origin})"
    if effective.summer_temp_f is not None:          # the hot-attic substitution fired
        if c.attic_temp_f is not None:
            return f"`cooling.attic_temp_f` {effective.summer_temp_f:.1f}°F attic air"
        uplift = spaces_mod.SOL_AIR_UPLIFT_F * spaces_mod.DEFAULT_ROOF_ABSORPTANCE
        return (f"sol-air estimate {effective.summer_temp_f:.1f}°F attic air "
                f"(outdoor {c.outdoor_1_f:.0f}°F + {uplift:.1f}°F roof gain) — "
                f"set `cooling.attic_temp_f` to replace it")
    # `factor` and `vented` are documented WINTER shorthands (see spaces.py). The engine
    # reusing them for summer is deliberate, but a cell that just echoes `vented: false`
    # invites the reader to think a summer observation was made. Say which it is.
    if policy.factor is not None:
        return f"`factor` {policy.factor:.2f} ({origin}) — winter shorthand, reused for summer"
    if policy.vented is not None:
        return (f"`vented: {str(policy.vented).lower()}` ({origin}) — winter shorthand, "
                f"reused for summer")
    return (f"unvented fallback {spaces_mod.UNVENTED_FACTOR:.2f} ({origin}) — winter "
            f"shorthand, reused for summer")


def _coverage_block(env: geometry_mod.Envelope, sc: sidecar.SideCar) -> list[str]:
    """Which surfaces claimed an assembly of their own, and which took the default.

    Emitted only for categories that actually MIX, because a category whose surfaces are
    all untagged says nothing worth a row and would bury the one that does.
    """
    rows = loads.assembly_coverage(env.surfaces, sc.assemblies)
    if not rows:
        return []
    lines = ["", "### Assembly coverage", "",
             "| Category | Assembly | U used | Area | Surfaces |",
             "|---|---|---:|---:|---:|"]
    for r in rows:
        label = f"`{r.assembly}`" if r.assembly else "_(untagged — category default)_"
        lines.append(f"| `{r.category}` | {label} | {r.u_value:g} "
                     f"| {r.area_ft2:,.1f} ft² | {r.count} |")
    lines += [
        "",
        "_A surface may name a variant of its category — `exterior_wall/r0` for the "
        "uninsulated sections of a wall that is mostly insulated — and take that U-value "
        "instead of the category default. The whole-house tables above stay keyed by "
        "category, so this is the only place the split is visible._",
        "",
        "_Read this table across runs, not just within one. Redrawing a wall in Sweet "
        "Home 3D gives it a new id and drops its properties, so nothing can report that a "
        "tag was lost — but a variant's area shrinking between two runs says it plainly._",
    ]
    return lines


def _borrows_block(env: geometry_mod.Envelope, sc: sidecar.SideCar) -> list[str]:
    """U-values the engine had to borrow from a related assembly, and from what.

    Until now a borrow existed only as a Python warning on stderr, so it never reached
    the document. On Refrhus that hid the LARGER of the two errors over the same void
    floor: the geometry gap is disclosed below, while the U-value standing in for it is
    a slab's effective whole-area number, an order of magnitude off a framed floor.
    """
    # Per SURFACE, not per category: a surface carrying a declared same-category variant
    # takes that U-value directly and borrows nothing, even where the bare category is
    # unset. Aggregating by category alone would report the whole category's area as
    # borrowed when some — or all — of it resolved exactly.
    borrows: dict[str, tuple[loads.AssemblyBorrow, float]] = {}
    for s in env.surfaces:
        borrow = loads.surface_borrow(s, sc.assemblies)
        if borrow is None:
            continue
        if s.category in borrows:
            borrows[s.category] = (borrows[s.category][0],
                                   borrows[s.category][1] + s.area_ft2)
        else:
            borrows[s.category] = (borrow, s.area_ft2)
    if not borrows:
        return []
    lines = ["", "### Borrowed assembly U-values", "",
             "| Category | Area | U used | Borrowed from | Why that is a stand-in |",
             "|---|---:|---:|---|---|"]
    for category in sorted(borrows):
        borrow, area_ft2 = borrows[category]
        lines.append(f"| `{category}` | {area_ft2:,.1f} ft² | {borrow.u_value:g} "
                     f"| `assemblies.{borrow.donor}` | {borrow.note} |")
    lines += [
        "",
        "_These surfaces are loaded at a number nobody measured for them. A borrow keeps "
        "the engine usable on a side-car written before the category existed, but it is a "
        "stand-in: declare `assemblies.<category>` to replace it with a real value._",
    ]
    return lines


# How many void rooms the callout names before it stops listing and starts counting.
_VOID_TOP_N = 3

# What a floor face over an UNDRAWN space can become, and what each one means thermally.
#
# Naming a category unconditionally was wrong on the `outdoor` branch in two directions
# at once — wrong category, and "buffer" reading as the 50% a buffer implies while the
# engine had actually applied the `outdoor` policy's 1.0. The reassuring parenthetical
# hedged the space NAME but not the TREATMENT, so it understated the load while sounding
# careful. Hence: this table is the ONLY source of the treatment text, so a caller that
# names a category without it states LESS than this block does, never something different
# — which is the property that matters, and is all that is actually enforced. (`overview`
# already names the category alone on the `outdoor` branch without stating the ΔT, so
# "no caller can state one without the other" was never true.) A category absent from
# this table produces NO claim at all.
#
# `floor` is deliberately absent even though `below_void: ground` reaches it
# (`geometry.CATEGORY_FOR_BELOW`). A ground-coupled gap is a gap in the DRAWING whose
# thermal treatment is the ordinary on-grade one every house already gets — there is no
# buffer fraction and no full-ΔT surprise to warn a reader about, so the callout has
# nothing to add beyond the gap itself. Saying nothing is a deliberate entry in this
# table's contract, pinned by a test; it is not an oversight.
#
# Caveat worth knowing: this table hand-mirrors `geometry.CATEGORY_FOR_BELOW`'s values by
# copy, not by import — `geometry` names the categories, this names what they mean to a
# reader, and there is no single constant to share. A new mapping there needs an entry
# here, or its treatment simply goes unstated (silent, not wrong).
_VOID_TREATMENT = {
    "buffer_floor": "over the undrawn space below (`crawlspace`, unless a level's "
                    "`below_void` names another), at that space's own fraction of the "
                    "design ΔT",
    "exposed_floor": "over open air (a level's `below_void: outdoor`), at the **full** "
                     "outdoor ΔT, not a buffer fraction",
}


def void_categories(env: geometry_mod.Envelope) -> list[str]:
    """Which categories THESE voids resolved to, in a stable order; may be empty.

    Public because `overview` renders the same claim in its own words and must not
    re-derive it: the first fix here left `overview` with a hardcoded fallback naming the
    very category the fix removed, so two modules disagreeing about this derivation is
    not hypothetical — it already happened once.

    Read off `env.voids`, never off `env.surfaces`. Asking the envelope's whole surface set
    answers "which void categories does this model contain ANYWHERE", which is a different
    question: a `below_void: ground` gap on a house with a drawn crawlspace came back
    `buffer_floor`, and an `outdoor` gap beside a drawn crawlspace came back as both.
    """
    present = {v.category for v in env.voids.values()}
    return [c for c in _VOID_TREATMENT if c in present]


def _voids_block(env: geometry_mod.Envelope, sc: sidecar.SideCar) -> list[str]:
    """Conditioned floor with nothing drawn beneath it — a schematic gap, not a result."""
    if not env.voids:
        return []
    ranked = sorted(env.voids.items(), key=lambda kv: kv[1].area_ft2, reverse=True)
    shown = ranked[:_VOID_TOP_N]
    largest = ", ".join(f"{name} {void.area_ft2:,.1f} ft²" for name, void in shown)
    of_n = f" ({len(shown)} of {len(ranked)} rooms)" if len(ranked) > len(shown) else ""
    categories = void_categories(env)
    # No horizontal surfaces to read the category off (a caller rendering a bare
    # Envelope): say nothing about the treatment rather than assert the default.
    modeled = ("" if not categories else " — modeled as "
               + "; ".join(f"`{c}` {_VOID_TREATMENT[c]}" for c in categories))
    lines = [
        "",
        "### Schematic gaps",
        "",
        f"⚠ **{sum(v.area_ft2 for v in env.voids.values()):,.1f} ft² of conditioned floor "
        f"has no level drawn beneath it**{modeled}.",
        "",
        # Bullets, not indented continuation lines: two-space indents are Markdown lazy
        # continuation and collapse the whole callout into one run-on paragraph.
        f"- Largest{of_n}: {largest}.",
        "- Draw those spaces in Sweet Home 3D to replace the assumption with geometry.",
        # The same root cause — space that exists but was never drawn as a room — with a
        # different symptom, and the one place a reader would actually notice it. The area
        # is unmeasurable (undrawn space leaves nothing to measure), so this states the
        # mechanism and the direction only, never a figure.
        "- Undrawn rooms distort the WALLS too, not just the floor: a wall is on the "
        "envelope when a conditioned room sits on exactly one side of it, so floor area "
        "left undrawn on a level makes the walls bordering it read as facing outdoors. "
        "Every such wall is counted as exterior, which inflates the envelope wall area and "
        "the conduction above with it, until the rooms are drawn.",
    ]
    # This block and the borrow table above describe the SAME floor — a void becomes one
    # of these categories, whose U-value is then borrowed. Laid out as two adjacent
    # blocks quoting two different areas with no relation stated, the natural reading is
    # two separate problems that sum; in fact the smaller nests inside the larger. Say
    # so — for whichever category is in play, not just for the common one.
    for category in categories:
        borrowed_area = sum(s.area_ft2 for s in env.surfaces if s.category == category)
        # The void's OWN share of that row, not the whole gap total. On a model where the
        # gaps resolved to more than one category, quoting the whole total against each row
        # would claim the same square footage twice over; on a single-category model it is
        # the same number, so this reads correctly either way.
        void_area = sum(v.area_ft2 for v in env.voids.values() if v.category == category)
        if loads.assembly_borrow(category, sc.assemblies) is not None:
            lines.append(
                f"- Not a separate problem from the one above: the {void_area:,.1f} ft² of "
                f"that gap which resolved to `{category}` is inside the "
                f"{borrowed_area:,.1f} ft² on the `{category}` row of *Borrowed assembly "
                f"U-values* (which spans every `{category}` surface, drawn or not), so "
                f"the two figures nest rather than add.")
    return lines


def _open_questions_block(env: geometry_mod.Envelope, sc: sidecar.SideCar) -> list[str]:
    """Places where the engine's own conventions could reasonably be read another way.

    Not a defect list and not a hedge — each entry names a choice Eldr made, states the
    assumption about the INPUTS that choice rests on, and says which way the number moves
    if that assumption does not hold. That is something the engine can say about itself;
    anything phrased as agreement or disagreement with a particular third-party report
    would be a claim about a document this program has never read.

    Every entry is gated on the model actually containing the thing it describes, for the
    same reason the borrow and void blocks are: a caveat about below-grade walls on a house
    with no basement teaches the reader to skim the section.
    """
    below_grade = {s.category for s in env.surfaces} & loads.GROUND_COUPLED_CATEGORIES
    bullets = []
    if below_grade:
        cats = ", ".join(f"`{c}`" for c in sorted(below_grade))
        bullets += [
            f"- **Below-grade surfaces ({cats}) are loaded at the full outdoor design ΔT**, "
            f"the way Manual J loads them. That assumes the U-value declared for them is a "
            f"Manual J *effective* below-grade value — the assembly plus the resistance of "
            f"the path through the soil out to grade. If a bare wall-assembly U was "
            f"supplied instead, the soil is missing from the calculation entirely and these "
            f"rows overstate the heat loss.",
            "- **One U-value per category, whatever the depth.** In Manual J practice the "
            "effective below-grade U falls as the average depth below grade rises, because "
            "the soil path gets longer — the same construction is tabulated at several "
            "U-values for that reason. Eldr applies one `assemblies` number to every "
            "surface in the category, so a wall that is part shallow and part deep gets "
            "whichever single value was declared.",
            "- **Below-grade surfaces contribute nothing to the cooling load**, because "
            "soil below the summer setpoint is a heat sink rather than a source. Some "
            "Manual J implementations do carry a small below-grade cooling load; against "
            "one of those, these rows will read low.",
        ]
    if "basement_wall" in below_grade:
        bullets.append(
            "- **Eldr has no grade line.** Every wall on a basement level is classed "
            "`basement_wall` over its whole height. Manual J practice splits the same "
            "physical wall at grade: the buried portion gets the depth-dependent effective "
            "U above, and the portion standing proud of grade is an ordinary above-grade "
            "wall. If part of this model's basement wall is above grade, it is currently "
            "carrying the below-grade assembly. Eldr cannot make that split — the reader "
            "has to, either by drawing the wall as two segments or by declaring a "
            "`basement_wall` U that averages the two.")
    if env.level_heights_ft:
        bullets.append(
            "- **A wall's own height overrides its level's.** Wall AREA comes from each "
            "wall's drawn height, not from the storey height in the *Level heights* table "
            "above — that height sets the volume behind the infiltration term and nothing "
            "else. The two can disagree with nothing in the model looking out of place, so "
            "it is worth checking whenever a wall row reads larger or smaller than expected.")
    if not bullets:
        return []
    return ["", "### Open questions", "",
            "Neither errors nor warnings — assumptions in the numbers above that a careful "
            "reader should check against their own house before trusting the total.",
            ""] + bullets


def _per_room_section(plan: ductmodel_mod.DuctPlan, whole_house_cfm: float) -> list[str]:
    """Build the Manual J 1c per-room table (heating, cooling-sensible, design CFM)."""
    served = [rl for rl in plan.room_loads
              if rl.conditioned and rl.cfm >= ductmodel_mod.MIN_RUN_CFM]
    served.sort(key=lambda rl: rl.cfm, reverse=True)
    room_cfm = sum(rl.cfm for rl in served)
    lines = [
        "",
        "## Eldr — Per-Room Loads (Manual J 1c)",
        "",
        "| Room | Heating (BTU/hr) | Cooling sens. (BTU/hr) | Design CFM |",
        "|---|---:|---:|---:|",
    ]
    for rl in served:
        lines.append(f"| {rl.name} | {rl.heating_btuh:,.0f} | {rl.cooling_btuh:,.0f} "
                     f"| {rl.cfm:,.0f} |")
    # Total the values as DISPLAYED, so the column visibly adds up. Summing the
    # unrounded figures instead leaves a total that disagrees with its own rows
    # by a few CFM and reads as an arithmetic error.
    shown_cfm = sum(round(rl.cfm) for rl in served)
    lines.append(f"| **{len(served)} rooms** | | | **{shown_cfm:,.0f}** |")

    # Conditioned rooms that exist but fall under the run threshold. They are real
    # space, so the "nothing left over" claim must not be made while any survive.
    below_threshold = [rl for rl in plan.room_loads
                       if rl.conditioned and rl.cfm < ductmodel_mod.MIN_RUN_CFM]

    note = (
        "_Each room's load is from the exterior walls, windows, doors and ceiling/floor "
        "attributed to it, plus infiltration on its own volume; design CFM is the larger "
        "of heating/cooling airflow. "
    )
    gap = whole_house_cfm - room_cfm
    if gap >= 1:
        note += (
            f"Served rooms sum to **{shown_cfm:,.0f} CFM** against the whole-house "
            f"**{whole_house_cfm:,.0f} CFM** — the shortfall is space not carried here: "
            f"floor area not yet drawn as rooms (halls, stairs, unfinished), plus "
            f"{len(below_threshold)} conditioned room(s) below the "
            f"{ductmodel_mod.MIN_RUN_CFM:.0f}-CFM run threshold. Draw more rooms and it "
            f"closes._"
        )
    elif gap <= -1:
        # Not an error, and not undrawn space: each room takes the larger of its own
        # heating and cooling airflow, and the two peak in different rooms. Summing
        # per-room maxima therefore exceeds the whole-house maximum by construction.
        note += (
            f"Served rooms sum to **{shown_cfm:,.0f} CFM**, *above* the whole-house "
            f"**{whole_house_cfm:,.0f} CFM**. That is expected rather than an "
            f"inconsistency: each room takes the larger of its own heating and cooling "
            f"airflow, and those do not peak in the same rooms, so per-room maxima add "
            f"up to more than the whole-house maximum. Size equipment on the whole-house "
            f"figure and branches on the room figures._"
        )
    else:
        note += (
            f"Served rooms account for the whole-house **{whole_house_cfm:,.0f} CFM**"
        )
        if below_threshold:
            note += (
                f", leaving only {len(below_threshold)} conditioned room(s) below the "
                f"{ductmodel_mod.MIN_RUN_CFM:.0f}-CFM run threshold"
            )
        else:
            note += " with nothing left over, so every conditioned space is drawn as a room"
        note += "."
        # The column totals the rounded rows so it adds up on the page; the
        # whole-house figure rounds once, at the end. Say so rather than leave
        # a 1-CFM difference looking like an arithmetic slip.
        if round(shown_cfm) != round(whole_house_cfm):
            note += (
                f" The column totals **{shown_cfm:,.0f}** because each row is rounded "
                f"before summing, while the whole-house figure rounds once at the end._"
            )
        else:
            note += "_"
    lines += ["", note]
    return lines


def _duct_section(dr: ductd_mod.DuctResult,
                  plan: ductmodel_mod.DuctPlan | None = None) -> list[str]:
    """Build the Manual D markdown lines (round duct size + velocity, and length if known)."""
    has_len = any(r.length_ft is not None for r in dr.runs)
    if plan is not None and plan.derived:
        fr_note = (f"**{dr.friction_rate:.3g} in.wc / 100 ft** "
                   f"(derived: {plan.available_static_pressure:.2g} in.wc × 100 ÷ "
                   f"{plan.worst_length_ft:,.0f} ft worst run)")
    else:
        fr_note = (f"**{dr.friction_rate:.3g} in.wc / 100 ft** "
                   f"(design rate — not derived from static pressure)")
    lines = [
        "",
        "## Manual D — Duct Sizing (round, equal-friction)",
        "",
        f"- Friction rate: {fr_note}",
    ]
    if plan is not None and plan.unit is not None:
        lines.append(f"- Air handler: **{plan.unit.name}** — run length = unit → room "
                     f"(Manhattan + vertical) × fitting factor.")
    elif plan is not None:
        lines.append(f"- _No unit found (searched furniture for “{plan.unit_name}”) — "
                     f"place one to get run lengths and a derived friction rate._")
    if has_len:
        lines += ["", "| Run | CFM | Exact dia | Duct | Velocity | Length | Drop |",
                  "|---|---:|---:|---:|---:|---:|---:|"]
        for r in dr.runs:
            flag = f" ⚠{r.flag}" if r.flag else ""
            length = "—" if r.length_ft is None else f"{r.length_ft:,.0f} ft"
            drop = "—" if r.pressure_drop_inwc is None else f"{r.pressure_drop_inwc:.3f}″"
            lines.append(f"| {r.name} | {r.cfm:,.0f} | {r.exact_dia_in:.1f}″ | "
                         f"**{r.standard_dia_in}″** | {r.velocity_fpm:,.0f} fpm{flag} "
                         f"| {length} | {drop} |")
    else:
        lines += ["", "| Run | CFM | Exact dia | Duct | Velocity |",
                  "|---|---:|---:|---:|---:|"]
        for r in dr.runs:
            flag = f" ⚠{r.flag}" if r.flag else ""
            lines.append(f"| {r.name} | {r.cfm:,.0f} | {r.exact_dia_in:.1f}″ | "
                         f"**{r.standard_dia_in}″** | {r.velocity_fpm:,.0f} fpm{flag} |")
    lines += [
        "",
        "_**Analysis, not a duct schedule.** These sizes come from each room's own load "
        "with no trunk hierarchy, no reducing runs and no installed layout — they answer "
        "\"how big would a dedicated duct to this room have to be\", which is a useful "
        "cross-check and not a thing anyone builds. Where a project keeps a hand-authored "
        "register schedule, that schedule is the authority for what gets installed._",
        "",
        "_Round duct, equal-friction, demo-grade. Total effective length uses a fitting "
        "fudge factor, not true fitting equivalent lengths; a full Manual D adds those and "
        "rectangular/oval sizing via equivalent diameter._",
    ]
    return lines


def _cooling_section(c: loads.CoolingResult, sc: sidecar.SideCar) -> list[str]:
    """Build the cooling-load markdown lines (sensible breakdown + latent + total)."""
    cd = sc.cooling
    if cd is None:
        raise ValueError("cooling report requires cooling conditions in the side-car")
    if cd.outdoor_1_f is None:
        raise ValueError("cooling report requires a resolved cooling.outdoor_1_f")
    lines = [
        "",
        "## Eldr — Cooling Load (Manual J 1b, whole-house)",
        "",
        f"- Indoor / 1% outdoor design: **{cd.indoor_f:.0f}°F / {cd.outdoor_1_f:.0f}°F** "
        f"(ΔT = {cd.cooling_delta_t:.0f}°F) · SHGC **{cd.shgc:.2f}** · {cd.occupants:.0f} occupants",
        "",
        "| Component | Load (BTU/hr) |",
        "|---|---:|",
    ]
    for cat, q in sorted(c.by_category.items()):
        lines.append(f"| {cat} | {q:,.0f} |")
    # Its own row, in the same place the heating table puts it: after the surface
    # categories, before the summary rows. Infiltration is not a surface, so it has no
    # `by_category` key to sort in among them — and folding it into `sensible` would leave
    # the itemised rows failing to add up to the total the note below claims they sum to.
    lines.append(f"| infiltration | {c.infiltration_btuh:,.0f} |")
    lines += [
        f"| **sensible** | **{c.sensible_btuh:,.0f}** |",
        f"| latent | {c.latent_btuh:,.0f} |",
        f"| **total** | **{c.total_btuh:,.0f}** |",
        "",
        f"**Supply airflow:** {c.cfm:,.0f} CFM",
        # Blank line, not a bare newline: two adjacent lines are ONE Markdown paragraph, so
        # without it the rendered document reads "...476 CFM **Sensible heat ratio:** 0.75".
        "",
        f"**Sensible heat ratio:** {c.sensible_btuh / c.total_btuh:.2f} "
        f"(sensible ÷ total) — how much of the job is temperature rather than moisture. "
        f"A standard Manual J figure, directly comparable against any professional report, "
        f"and an equipment-selection input: the lower it runs, the more of the load is "
        f"dehumidification, which calls for a coil that stays wet rather than a bigger one.",
        "",
        # The three summary rows sat flush against the component rows above them with
        # nothing to say they were a different KIND of number, so the natural reading was
        # "sensible, latent and total are three more components" — and then "why is the
        # airflow not sized on the biggest one?"
        "_Those last three rows are not three more components. Every itemised row above "
        "them is sensible heat, and they sum to **sensible** — the heat that has to leave "
        "to hold the dry-bulb setpoint. **Latent** is a separate quantity: moisture, from "
        "the occupants and from the humidity the infiltrating air carries in. That is why "
        "it has no component breakdown — no wall, window or roof contributes to it. "
        "**Total** is simply the two added. Supply airflow is sized on **sensible** alone, "
        "not on the total, which is why the CFM does not come off the bottom line: air "
        "carries the sensible load by temperature difference, while the latent load leaves "
        "as condensate at the coil rather than by moving more air._",
        "",
        "_Solar reads each window's exact bearing (grouped for display by nearest "
        "8-point, e.g. `solar-SW`), from the model's compass `northDirection`._",
    ]
    return lines


def _manual_s_section(s: sizing_mod.SizingResult) -> list[str]:
    """Build the Manual S markdown lines from a SizingResult."""
    lines = [
        "",
        "## Manual S — Equipment Sizing",
        "",
        f"- Design load ({s.basis}): **{s.load_tons / sizing_mod.TONS_PER_BTUH:,.0f} BTU/hr "
        f"= {s.load_tons:.1f} tons**",
        f"- Recommended (smallest size that meets the load): "
        f"**{s.rec_tons:.1f} tons** ({s.rec_oversize_pct:+.0f}% vs load)",
        f"- Next size up: **{s.next_tons:.1f} tons** ({s.next_oversize_pct:+.0f}% vs load)",
    ]
    if s.existing_tons is None:
        lines.append("- Existing unit: _not provided — add `equipment.existing_tons` to compare_")
    else:
        flag = "" if s.verdict == "well-matched" else " ⚠"
        lines.append(
            f"- Existing unit: **{s.existing_tons:.1f} tons** → "
            f"{s.existing_oversize_pct:+.0f}% vs load → **{s.verdict}**{flag}")
        if s.verdict == "oversized":
            lines.append("  - _short-cycling, poor humidity control, added wear_")
        elif s.verdict == "undersized":
            lines.append("  - _may not hold indoor temp at design conditions_")
    lines += [
        "",
        f"_Demo estimate, not ACCA-certified. Sized on the larger of heating/cooling (here: {s.basis})._",
    ]
    return lines
