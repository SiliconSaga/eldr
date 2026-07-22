"""Render a HeatingResult (and optional Manual S sizing) as a Markdown report."""
from __future__ import annotations
from eldr import (loads, sidecar, sizing as sizing_mod, climate as climate_mod,
                  ductd as ductd_mod, ductmodel as ductmodel_mod)


def render_heating(result: loads.HeatingResult, sc: sidecar.SideCar,
                   sizing: sizing_mod.SizingResult | None = None,
                   cooling: loads.CoolingResult | None = None,
                   station: climate_mod.Station | None = None,
                   ducts: ductd_mod.DuctResult | None = None,
                   duct_plan: ductmodel_mod.DuctPlan | None = None) -> str:
    """Render the heating load (and optional cooling + Manual S sizing) as Markdown."""
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
    if cooling is not None:
        lines += _cooling_section(cooling, sc)
    if sizing is not None:
        lines += _manual_s_section(sizing)
    if duct_plan is not None:
        lines += _per_room_section(duct_plan, result.cfm)
    if ducts is not None:
        lines += _duct_section(ducts, duct_plan)
    return "\n".join(lines)


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
    lines.append(f"| **{len(served)} rooms** | | | **{room_cfm:,.0f}** |")
    lines += [
        "",
        f"_Each room's load is from the exterior walls, windows, doors and ceiling/floor "
        f"attributed to it, plus infiltration on its own volume; design CFM is the larger "
        f"of heating/cooling airflow. Rooms sum to **{room_cfm:,.0f} CFM** vs the whole-house "
        f"**{whole_house_cfm:,.0f} CFM** — the gap is floor area not yet drawn as rooms "
        f"(halls, stairs, unfinished space). Draw more rooms and it closes._",
    ]
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
            flag = " ⚠" if r.flag == "high" else ""
            length = "—" if r.length_ft is None else f"{r.length_ft:,.0f} ft"
            drop = "—" if r.pressure_drop_inwc is None else f"{r.pressure_drop_inwc:.3f}″"
            lines.append(f"| {r.name} | {r.cfm:,.0f} | {r.exact_dia_in:.1f}″ | "
                         f"**{r.standard_dia_in}″** | {r.velocity_fpm:,.0f} fpm{flag} "
                         f"| {length} | {drop} |")
    else:
        lines += ["", "| Run | CFM | Exact dia | Duct | Velocity |",
                  "|---|---:|---:|---:|---:|"]
        for r in dr.runs:
            flag = " ⚠" if r.flag == "high" else ""
            lines.append(f"| {r.name} | {r.cfm:,.0f} | {r.exact_dia_in:.1f}″ | "
                         f"**{r.standard_dia_in}″** | {r.velocity_fpm:,.0f} fpm{flag} |")
    lines += [
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
    lines += [
        f"| **sensible** | **{c.sensible_btuh:,.0f}** |",
        f"| latent | {c.latent_btuh:,.0f} |",
        f"| **total** | **{c.total_btuh:,.0f}** |",
        "",
        f"**Supply airflow:** {c.cfm:,.0f} CFM",
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
