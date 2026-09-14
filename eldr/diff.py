"""Compare two `--json` runs and say what changed, in Markdown.

Built for a pull-request comment, but the value is not CI-specific: the same
command answers "what did that edit actually do" at a desk, which is the
question a single tracked report makes askable in the first place.

Two rules shape the output.

**Lead with the design conditions.** If the station or a design temperature
moved, every component moves with it, and a reader who does not know that reads
a dozen rows as a dozen findings. That case is called out first and the rest is
framed as its consequence.

**Report what moved, not everything.** A row has to clear both an absolute and a
relative threshold to appear. Totals, tonnage and the sizing verdict are always
shown, because those are the numbers a decision hangs on.
"""
from __future__ import annotations

# A component row must move by at least this much BOTH ways to be worth a line.
# Absolute alone floods the table on a big house; relative alone reports a 0.4
# BTU/hr wall as "+50%".
MIN_ABS_BTUH = 50.0
MIN_REL = 0.01
MIN_ABS_CFM = 2.0
MAX_ROOM_ROWS = 12


def _pct(before: float, after: float) -> str:
    if before == 0:
        return "new" if after else "—"
    return f"{(after - before) / abs(before) * 100:+.1f}%"


def _significant(before: float, after: float,
                 min_abs: float = MIN_ABS_BTUH, min_rel: float = MIN_REL) -> bool:
    delta = abs(after - before)
    if delta < min_abs:
        return False
    return before == 0 or delta / abs(before) >= min_rel


def _row(label: str, before: float, after: float, unit: str = "") -> str:
    return (f"| {label} | {before:,.0f}{unit} | {after:,.0f}{unit} "
            f"| {after - before:+,.0f} | {_pct(before, after)} |")


def _get(d: dict, *path, default=None):
    for key in path:
        if not isinstance(d, dict) or key not in d or d[key] is None:
            return default
        d = d[key]
    return d


def _conditions_changed(before: dict, after: dict) -> list[str]:
    """Design-condition changes, which explain any across-the-board movement."""
    checks = [
        ("Design station", _get(before, "station", "name"), _get(after, "station", "name")),
        ("Heating ΔT", _get(before, "design", "heating_delta_t_f"),
         _get(after, "design", "heating_delta_t_f")),
        ("Supply-air rise", _get(before, "design", "supply_air_rise_f"),
         _get(after, "design", "supply_air_rise_f")),
        ("Infiltration ACH", before.get("infiltration_ach"), after.get("infiltration_ach")),
        ("Cooling ΔT", _get(before, "cooling", "cooling_delta_t_f"),
         _get(after, "cooling", "cooling_delta_t_f")),
        ("Occupants", _get(before, "cooling", "occupants"),
         _get(after, "cooling", "occupants")),
    ]
    return [f"**{label}**: {b} → {a}" for label, b, a in checks
            if b is not None and a is not None and b != a]


def _component_table(before: dict, after: dict, key: str, title: str) -> list[str]:
    b_cats = _get(before, key, "by_category", default={}) or {}
    a_cats = _get(after, key, "by_category", default={}) or {}
    b_total = _get(before, key, "total_btuh", default=0.0)
    a_total = _get(after, key, "total_btuh", default=0.0)
    if b_total == a_total and b_cats == a_cats:
        return []

    rows = []
    for cat in sorted(set(b_cats) | set(a_cats)):
        b, a = b_cats.get(cat, 0.0), a_cats.get(cat, 0.0)
        if _significant(b, a):
            rows.append(_row(f"`{cat}`", b, a))
    b_inf = _get(before, key, "infiltration_btuh", default=0.0)
    a_inf = _get(after, key, "infiltration_btuh", default=0.0)
    if _significant(b_inf, a_inf):
        rows.append(_row("infiltration", b_inf, a_inf))

    lines = [f"### {title}", "",
             "| Component | Before | After | Δ | |", "|---|---:|---:|---:|---:|"]
    lines += rows
    lines.append(f"| **Total** | **{b_total:,.0f}** | **{a_total:,.0f}** "
                 f"| **{a_total - b_total:+,.0f}** | **{_pct(b_total, a_total)}** |")
    b_cfm = _get(before, key, "cfm", default=0.0)
    a_cfm = _get(after, key, "cfm", default=0.0)
    if _significant(b_cfm, a_cfm, MIN_ABS_CFM):
        lines.append(_row("Design airflow", b_cfm, a_cfm, " CFM"))
    if not rows:
        lines.insert(1, "")
        lines.insert(2, "_No component moved past the reporting threshold._")
    lines.append("")
    return lines


def _sizing_lines(before: dict, after: dict) -> list[str]:
    b, a = before.get("equipment_sizing") or {}, after.get("equipment_sizing") or {}
    if not b or not a:
        return []
    out = []
    bt, at = b.get("design_load_tons"), a.get("design_load_tons")
    if bt is not None and at is not None and round(bt, 1) != round(at, 1):
        out.append(f"- Design load **{bt:.1f} → {at:.1f} tons**")
    br, ar = b.get("recommended_tons"), a.get("recommended_tons")
    if br != ar:
        out.append(f"- **Recommended size {br} → {ar} tons**")
    bv, av = b.get("verdict"), a.get("verdict")
    if bv != av:
        out.append(f"- Existing unit reads **{bv} → {av}**")
    if not out:
        return []
    return ["### Equipment sizing", ""] + out + [""]


def _room_lines(before: dict, after: dict) -> list[str]:
    b_rooms = {r["name"]: r for r in before.get("rooms") or []}
    a_rooms = {r["name"]: r for r in after.get("rooms") or []}
    added = sorted(set(a_rooms) - set(b_rooms))
    removed = sorted(set(b_rooms) - set(a_rooms))

    moved = []
    for name in sorted(set(b_rooms) & set(a_rooms)):
        b, a = b_rooms[name]["design_cfm"], a_rooms[name]["design_cfm"]
        if _significant(b, a, MIN_ABS_CFM):
            moved.append((abs(a - b), name, b, a))
    moved.sort(reverse=True)

    if not (added or removed or moved):
        return []
    out = ["### Rooms", ""]
    if added:
        out.append(f"- **Added**: {', '.join(added)}")
    if removed:
        out.append(f"- **Removed**: {', '.join(removed)}")
    if moved:
        if added or removed:
            out.append("")
        out += ["| Room | Before | After | Δ | |", "|---|---:|---:|---:|---:|"]
        for _, name, b, a in moved[:MAX_ROOM_ROWS]:
            out.append(_row(name, b, a, " CFM"))
        if len(moved) > MAX_ROOM_ROWS:
            out.append("")
            out.append(f"_and {len(moved) - MAX_ROOM_ROWS} more room(s) past the "
                       f"threshold, not shown._")
    out.append("")
    return out


def render_diff(before: dict, after: dict, title: str = "Eldr — load change") -> str:
    """Markdown describing what moved between two `--json` runs."""
    lines = [f"## {title}", ""]

    conditions = _conditions_changed(before, after)
    if conditions:
        lines += [
            "⚠️ **Design conditions changed.** Every component below moves with them, "
            "so read the totals rather than hunting for a cause in each row.",
            "",
        ]
        lines += [f"- {c}" for c in conditions]
        lines.append("")

    body = (_component_table(before, after, "heating", "Heating")
            + _component_table(before, after, "cooling", "Cooling")
            + _sizing_lines(before, after)
            + _room_lines(before, after))

    if not body:
        lines.append("**No change.** Loads, equipment sizing and per-room airflow are "
                     "identical to the base.")
        return "\n".join(lines)

    lines += body
    lines.append("<sub>Generated by `eldr diff`. Rows appear only when they move by at "
                 f"least {MIN_ABS_BTUH:.0f} BTU/hr (or {MIN_ABS_CFM:.0f} CFM) "
                 f"and {MIN_REL:.0%}.</sub>")
    return "\n".join(lines)
