# Eldr Manual S — equipment sizing (demo-grade) design

**Status:** Approved 2026-07-21, building. Extends the Phase-1a heating engine. Companion to the Eldr design (`realm-siliconsaga` `docs/plans/2026-07-15-eldr-manual-j-design.md`), which places Manual S after Manual J in the ACCA chain (J = loads → S = equipment selection → D = ducts).

## Purpose

Turn Eldr's whole-house heating load into an **equipment-sizing recommendation** with an **existing-unit check** — the "your SH3D model just sized your equipment, for free" story for an HVAC-pro demo. Accuracy is explicitly *not* the goal here; the pipeline (model → load → sizing verdict) is the wow. Heating-basis, because Phase-1a is heating-only; cooling (1b) refines it later, and final sizing uses the larger of heating/cooling.

## Scope

Demo-grade. **In:** load → tons → a recommended nominal band → an oversizing verdict against a named existing unit. **Out (YAGNI):** equipment model/database lookup, SEER/HSPF, balance-point / aux-heat math, latent loads, cooling. These are named as future work, not built.

## Architecture

One new pure module, one optional side-car block, one report section. No I/O outside the existing CLI.

### `eldr/sizing.py`

- `SizingResult` dataclass: `load_tons: float`, `rec_low_tons: float`, `rec_high_tons: float`, `existing_tons: float | None`, `oversize_pct: float | None`, `verdict: str`.
- `size_equipment(result: loads.HeatingResult, sc: sidecar.SideCar) -> SizingResult`.

Logic (all thresholds hardcoded — demo-reasonable, owner-approved):
- `TONS_PER_BTUH = 1 / 12000`; `load_tons = result.total_btuh * TONS_PER_BTUH`.
- Recommended band: `rec_low = ceil(load_tons / 0.5) * 0.5` (nearest half-ton at or above the load), `rec_high = rec_low + 0.5` (one nominal step of headroom).
- Existing-unit check (only when the side-car names one):
  - `oversize_pct = (existing_tons - load_tons) / load_tons * 100`.
  - Verdict bands: `> 15%` → `"oversized"` (short-cycling / humidity / wear); `< -10%` → `"undersized"` (won't hold design temp); else `"well-matched"`.
- No existing unit → `existing_tons = oversize_pct = None`, `verdict = "no existing unit given"`.

### Side-car — optional `equipment:` block

```yaml
equipment:
  existing_tons: 4.0    # current unit nominal tonnage; omit to skip the comparison
```

`load_sidecar` parses it when present (a new optional field on `SideCar`, default `None`); absence is not an error. No new physical-value validation beyond "if present, must be a finite number > 0" (reuse the existing finite/positive guards' style).

### `report.py`

Append a `## Manual S — Equipment Sizing` section after the heating table: load in BTU/hr and tons, the recommended band, and — when present — the existing unit + oversize % + a ⚠ line for a non-"well-matched" verdict. Footer caveat: "Heating-basis, demo estimate; cooling (1b) to follow — final sizing uses the larger of heating/cooling."

## Data flow

`cli.run` already does geometry → sidecar → heating_load → report. Add: `sizing.size_equipment(result, sc)` between the load and the report, and pass the SizingResult into `report.render_heating` (renamed/extended to render both sections, or a thin `render_report` wrapper — implementer's call, keep it one report string).

## Testing

`ws test eldr`. New `test_sizing.py`: tons math; each of the three verdict bands; the missing-equipment case. Extend `test_report.py` to assert the Manual S section renders (recommended band present; existing-unit ⚠ line present when a unit is given). Extend `test_sidecar.py` for the optional `equipment` block (present, absent, and invalid-value rejection).

## Delivery

Built on a branch, `ws test eldr` green, demo-runnable via `ws exec eldr .venv/bin/python -m eldr.cli … example-sidecar.yaml` (add `equipment.existing_tons` to the example side-car so the demo shows the oversizing catch). Then `ws cr` → PR (routine component workflow). Build-first so it's demo-ready regardless of review timing.
