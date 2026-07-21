"""Render a HeatingResult (and optional Manual S sizing) as a Markdown report."""
from __future__ import annotations
from eldr import loads, sidecar, sizing as sizing_mod


def render_heating(result: loads.HeatingResult, sc: sidecar.SideCar,
                   sizing: sizing_mod.SizingResult | None = None,
                   cooling: loads.CoolingResult | None = None) -> str:
    """Render the heating load (and optional cooling + Manual S sizing) as Markdown."""
    d = sc.design
    lines = [
        "# Eldr — Heating Load (Phase 1, whole-house)",
        "",
        f"- Indoor / 99% outdoor design: **{d.indoor_heating_f:.0f}°F / {d.outdoor_heating_99_f:.0f}°F** "
        f"(ΔT = {d.heating_delta_t:.0f}°F)",
        f"- Infiltration: **{sc.infiltration_ach:.2f} ACH**",
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
    return "\n".join(lines)


def _cooling_section(c: loads.CoolingResult, sc: sidecar.SideCar) -> list[str]:
    """Build the cooling-load markdown lines (sensible breakdown + latent + total)."""
    cd = sc.cooling
    if cd is None:
        raise ValueError("cooling report requires cooling conditions in the side-car")
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
        "_Solar is orientation-resolved (`solar-N/E/S/W`). Orientation reflects the "
        "model's compass `northDirection` — set it from the survey for true facing._",
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
