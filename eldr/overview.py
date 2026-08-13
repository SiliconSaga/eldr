"""Render the full narrative "demo overview" deterministically.

The numbers and tables come from the same pipeline the report uses (via cli.analyze),
so this never drifts from the engine. Only the framing prose is templated here — the
house-specific figures are the embedded report body, and the honesty caveats are
auto-selected from what's actually in the model.
"""
from __future__ import annotations
import re
from eldr import loads, report

_INTRO = """# Eldr — Manual J / S / D from a 3D model

Eldr reads a Sweet Home 3D house model and computes residential HVAC loads and duct
sizing along the ACCA chain — heating and cooling loads, equipment sizing, and duct
design. The model owns the geometry; a small "side-car" file owns the thermal
assumptions (insulation, setpoints, infiltration, soil temp, per-wall boundaries).
Edit the house, re-run, watch the numbers move.

These are **demo-grade estimates, not an ACCA-certified design** — the honesty
section marks exactly where the line is."""

_ACCA_CHAIN = """## The ACCA chain, implemented

| Step | Manual | What it answers |
|---|---|---|
| Loads | **Manual J** | winter heat loss / summer heat gain |
| Equipment | **Manual S** | what size unit the load calls for; is the existing one right |
| Distribution | **Manual D** | how big each supply duct needs to be |"""

_HOW_DETAILED = f"""## How detailed it gets

- **Solar by the exact degree** — every window's true compass facing (from the model's
  `northDirection`) sets its solar gain; SW/SE glass loads differently.
- **Climate from the model's coordinates** — nearest design-weather station, automatic.
- **The real footprint** — exterior walls follow the room-polygon outline, so a wall on
  an extension/wing is caught even off the bounding rectangle; unconditioned
  garage/crawlspace walls are excluded.
- **Below-grade is ground-coupled** — basement walls + slab see ~50 °F soil, not design
  air, so a partial basement stops dominating the load.
- **Buffer walls** — a wall to a garage/crawlspace is neither interior nor fully
  exterior; tagged `buffer`, it's loaded at **{loads.BUFFER_FACTOR:.0%} of the design
  ΔT**.
- **Per-room loads (Manual J 1c)** — each room's own walls/windows/doors/ceiling/floor.
- **Place the air handler** — each supply run gets a routed length; with the blower's
  static pressure, the friction rate is derived the ACCA way."""

_ACCA_PATH = """## What it would take to aspire to ACCA compliance

ACCA (Air Conditioning Contractors of America) runs a software-approval program against
their manuals. Moving from demo-grade toward that bar needs: the full Manual J 8th-ed
procedure (detailed fenestration, infiltration by tightness class, duct gains, internal-
gain schedules); certified ASHRAE design conditions for the address; real building data
(a construction takeoff + a blower-door test); a zone-aware bottom boundary and true
fitting equivalent lengths; then validation against ACCA's reference suite. The
architecture is already the right shape — geometry in the model, thermal in the side-car,
each Manual its own tested module — so the path is "add fidelity," not "rewrite.\""""

_ROADMAP = """## Near-term roadmap

- Zone-aware bottom boundary (basement slab vs. crawl vs. slab-on-grade extensions).
- True fitting equivalent lengths + return-duct sizing.
- Per-wall height/area splits (a wall that's part exterior, part buffer).
- Attic / knee-wall geometry; an interview step for the side-car; a Sweet Home 3D plugin."""

_FOOTER = ("*Eldr is read-only — it never modifies the house model. Estimates here are "
           "for demonstration and are not a substitute for a certified Manual J/S/D design.*")


def _honesty(a) -> str:
    """Auto-select the caveats that actually apply to this model + side-car."""
    bullets = [
        "**Assemblies are assumptions, not a takeoff** — the side-car U-values, not "
        "measured construction. Real numbers move the loads.",
        f"**Infiltration is an estimate** ({a.sc.infiltration_ach:.2f} ACH) — a blower-door "
        "test is the real input.",
    ]
    if a.station is not None:
        bullets.append(f"**Design weather is nearest-station** ({a.station.name}), not the "
                       "certified ASHRAE station for the address.")
    bullets.append("**Below-grade coupling is coarse** — one ground temperature; it doesn't "
                   "yet split a floor over the warm basement from one over a crawlspace.")
    if loads.BUFFER_WALL_CATEGORY in a.heating.by_category:
        bullets.append(f"**Buffer walls** use a flat {loads.BUFFER_FACTOR:.0%} of the design "
                       "ΔT (one factor for all buffers) and a whole-wall tag — no partial "
                       "height/length split yet.")
    borrowed = sorted({s.category for s in a.env.surfaces
                       if loads.assembly_borrow(s.category, a.sc.assemblies) is not None})
    if borrowed:
        # BOTH verbs agree, not just the first. The bullet has two of them ("has ... and
        # stands in"), and inflecting only the leading one shipped "`buffer_floor` has no
        # `assemblies` entry and stand in on ...". It survived because the report that was
        # supposed to expose it pasted the corrected "stands in" instead of the output.
        has, stands = ("has", "stands") if len(borrowed) == 1 else ("have", "stand")
        bullets.append("**Some U-values are borrowed, not declared** — "
                       + ", ".join(f"`{c}`" for c in borrowed)
                       + f" {has} no `assemblies` entry and {stands} in on a related "
                       "assembly's number; the *Borrowed assembly U-values* table above "
                       "names each donor and how far off it can be.")
    if a.env.level_heights_ft:
        bullets.append("**Storey heights are whatever the model says** — Sweet Home 3D gives "
                       "each level a default height, and a level nobody re-measured looks "
                       "identical to one that was; the *Level heights* table above shows what "
                       "each level used and what volume it contributed.")
    if any(s.space is not None for s in a.env.surfaces):
        bullets.append("**Buffer-space temperatures are policy, not measurement** — an attic "
                       "with no observed summer temperature gets a sol-air estimate (outdoor "
                       "air plus a flat solar uplift, no roof geometry or ventilation rate); "
                       "the *Buffer spaces* table above prints the factor and temperature "
                       "each surface actually got.")
    if a.env.voids:
        # The category in code font, matching the borrow bullet above it, so a reader can
        # see the two caveats are about the same floor rather than two floors — and read
        # off the ENVELOPE, not hardcoded: `below_void: outdoor` makes it `exposed_floor`
        # at the full outdoor ΔT, and claiming "buffer" there understates the load.
        as_what = ", ".join(f"`{c}`" for c in report._void_categories(a.env)) or "buffer floor"
        bullets.append(f"**{sum(a.env.voids.values()):,.1f} ft² of conditioned floor has no "
                       f"level drawn beneath it** — modeled as {as_what} over undrawn "
                       "space. That is a gap in the drawing, not a measurement; draw those "
                       "spaces and the assumption is replaced by geometry.")
    if a.duct_plan is not None and a.duct_plan.unit is None:
        bullets.append("**No air handler placed** — duct runs have no lengths and the "
                       "friction rate isn't derived; place one named per `ducts.unit_name`.")
    if a.ducts is not None:
        bullets.append("**Duct run lengths use a fitting fudge factor**, not true fitting "
                       "equivalent lengths; round duct only; no return-side sizing yet.")
    bullets.append("**The envelope follows drawn rooms** — interior space not yet drawn as a "
                   "room reads as outdoors and can over-count until it's drawn.")
    return "## What's demo-grade today (the honest line)\n\n" + "\n".join(f"- {b}" for b in bullets)


def render_overview(home_path: str, sidecar_path: str) -> str:
    """Assemble the full narrative overview: framing + the engine's report + honesty."""
    from eldr import cli   # lazy: cli imports overview only inside main()
    a = cli.analyze(home_path, sidecar_path)
    body = report.render_heating(a.heating, a.sc, sizing=a.sizing, cooling=a.cooling,
                                 station=a.station, ducts=a.ducts, duct_plan=a.duct_plan,
                                 env=a.env)
    # Demote the report's leading H1 so the whole overview nests under one title; its
    # other sections are already ## and sit consistently beneath it. Match the first
    # H1 line by shape (not exact text), so a future report-title tweak can't slip a
    # nested H1 back in.
    body = re.sub(r"\A# .*", "## Your house, by the numbers — whole-house loads (Manual J)",
                  body, count=1)
    return "\n\n".join([
        _INTRO,
        _ACCA_CHAIN,
        _HOW_DETAILED,
        body,
        _honesty(a),
        _ACCA_PATH,
        _ROADMAP,
        _FOOTER,
    ])
