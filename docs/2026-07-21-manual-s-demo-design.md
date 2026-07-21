# Eldr Manual S — equipment sizing (demo-grade) design

**Status:** Approved 2026-07-21, building. Extends the Phase-1a heating engine. Companion to the Eldr design (`realm-siliconsaga` `docs/plans/2026-07-15-eldr-manual-j-design.md`), which places Manual S after Manual J in the ACCA chain (J = loads → S = equipment selection → D = ducts).

## Purpose

Turn Eldr's whole-house heating load into an **equipment-sizing recommendation** with an **existing-unit check** — the "your SH3D model just sized your equipment, for free" story for an HVAC-pro demo. Accuracy is explicitly *not* the goal here; the pipeline (model → load → sizing verdict) is the wow. Heating-basis, because Phase-1a is heating-only; cooling (1b) refines it later, and final sizing uses the larger of heating/cooling.

## Scope

Demo-grade but **textbook-honest** — no logic bent to flatter the demo. **In:** load → tons → the smallest standard equipment size that *meets* the load (real Manual S: meet-or-exceed), how oversized that pick and the next size up each are, and an oversizing verdict against a named existing unit. **Out (YAGNI):** equipment model/database lookup, SEER/HSPF, balance-point / aux-heat math, latent loads, cooling. Named as future work, not built.

The demo "wow" is **changing the house on paper and re-running** (e.g. drag an exterior wall deep into the backyard → the model jumps in size → the load and recommended equipment jump with it), not any rounding trick.

## Architecture

One new pure module, one optional side-car block, one report section. No I/O outside the existing CLI.

### `eldr/sizing.py`

- `SizingResult` dataclass: `load_tons`, `rec_tons`, `rec_oversize_pct`, `next_tons`, `next_oversize_pct`, `existing_tons: float | None`, `existing_oversize_pct: float | None`, `verdict: str`.
- `size_equipment(result: loads.HeatingResult, sc: sidecar.SideCar) -> SizingResult`.

Logic (thresholds hardcoded — demo-reasonable):
- `TONS_PER_BTUH = 1 / 12000`; `load_tons = result.total_btuh * TONS_PER_BTUH`. Reject non-finite or `<= 0` load with a `ValueError` before dividing.
- `oversize(size) = (size - load_tons) / load_tons * 100` — a helper.
- **Recommended size** `rec_tons = ceil(load_tons / 0.5) * 0.5` — the smallest 0.5-ton standard that meets the load (Manual S: meet-or-exceed). `rec_oversize_pct = oversize(rec_tons)`.
- **Next size up** `next_tons = rec_tons + 0.5`; `next_oversize_pct = oversize(next_tons)` — the "and one size larger is this far off" line.
- Existing-unit check (only when the side-car names one): `existing_oversize_pct = oversize(existing_tons)`; verdict `> 15%` → `"oversized"` (short-cycling / humidity / wear), `< -10%` → `"undersized"` (won't hold design temp), else `"well-matched"`.
- No existing unit → `existing_tons = existing_oversize_pct = None`, `verdict = "no existing unit given"`.

### Side-car — optional `equipment:` block

```yaml
equipment:
  existing_tons: 4.0    # current unit nominal tonnage; omit to skip the comparison
```

`load_sidecar` parses it when present (a new optional field on `SideCar`, default `None`); absence is not an error. No new physical-value validation beyond "if present, must be a finite number > 0" (reuse the existing finite/positive guards' style).

### `report.py`

Append a `## Manual S — Equipment Sizing` section after the heating table: load in BTU/hr and tons; the recommended size + its % over load; the next size up + its % over; and — when present — the existing unit + its % + a ⚠ line for a non-"well-matched" verdict. Footer caveat: "Heating-basis, demo estimate; cooling (1b) to follow — final sizing uses the larger of heating/cooling."

## Data flow

`cli.run` already does geometry → sidecar → heating_load → report. Add: `sizing.size_equipment(result, sc)` between the load and the report, and pass the SizingResult into `report.render_heating` (renamed/extended to render both sections, or a thin `render_report` wrapper — implementer's call, keep it one report string).

## Testing

`ws test eldr`. New `test_sizing.py`: tons math; the recommended (ceil-to-standard) size + next-size-up on a non-boundary load; each of the three verdict bands; the missing-equipment case; a zero/negative-load rejection. Extend `test_report.py` to assert the Manual S section renders (recommended + next-size lines present; existing-unit ⚠ line present when a unit is given). Extend `test_sidecar.py` for the optional `equipment` block (present, absent, non-mapping, and invalid-value rejection).

## Delivery

Built on a branch, `ws test eldr` green, demo-runnable via `ws exec eldr .venv/bin/python -m eldr.cli … example-sidecar.yaml` (add `equipment.existing_tons` to the example side-car so the demo shows the oversizing catch). Then `ws cr` → PR (routine component workflow). Build-first so it's demo-ready regardless of review timing.
