"""Render a HeatingResult as a readable Markdown report."""
from __future__ import annotations
from eldr import loads, sidecar


def render_heating(result: loads.HeatingResult, sc: sidecar.SideCar) -> str:
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
        "_Phase 1 whole-house estimate. Not ACCA-certified. Room-by-room + cooling to follow._",
    ]
    return "\n".join(lines)
