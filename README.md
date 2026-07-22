# Eldr — Manual J heat-load engine for Sweet Home 3D

**Eldr** ("fire"; also reads as "Elder") turns a [Sweet Home 3D](https://www.sweethome3d.com/) house model into HVAC load numbers: whole-house **heating** and **cooling** loads (Manual J), plus an **equipment-sizing** recommendation (Manual S). It reads the model directly — no manual takeoff — and pairs the geometry SH3D already knows with a small side-car of thermal inputs it can't.

> **Status:** demo-grade, not ACCA-certified. The pipeline (model → load → size) is real and the math is honest; the *inputs* (assemblies, design temps, infiltration) start as estimates and get sharper as the house is measured. Certifiable output is the roadmap goal, not today's claim.

## What it does

- **Manual J heating** — `Σ U·A·ΔT` over the envelope + infiltration, and the supply CFM.
- **Manual J cooling (1b)** — conduction + **orientation-resolved solar gain** (west/east glass loads more than north, computed from the model's compass) + internal gains, plus latent.
- **Manual S** — the smallest standard equipment size that *meets* the design load (the larger of heating/cooling), the next size up, and a verdict against your existing unit (oversized / undersized / well-matched).

## Requirements

- Python 3.11+
- A local virtualenv with `pyyaml` + `defusedxml` (+ `pytest` for the suite; the `ws test eldr` adapter uses it):

  ```bash
  cd components/eldr
  python3 -m venv .venv
  .venv/bin/pip install pyyaml defusedxml pytest
  ```

## Usage

Point Eldr at a model (an exploded `Home.xml` **or** a packed `.sh3d` — it reads the zip directly) and a side-car YAML:

```bash
cd components/eldr
.venv/bin/python -m eldr.cli ../../hoards/refrhus/Refrhus.sh3d eldr/example-sidecar.yaml
```

It prints a Markdown report (heating table, cooling table, Manual S sizing, per-room loads, and Manual D duct sizing). The demo loop is: **edit the house in SH3D → save → re-run** and watch the numbers move.

## The side-car

The model owns geometry; the side-car owns everything thermal. See [`eldr/example-sidecar.yaml`](eldr/example-sidecar.yaml) for a documented starting point. Blocks:

| Block | Required? | What it carries |
|---|---|---|
| `design` | yes | heating setpoint, 99% outdoor design temp, supply-air rise |
| `infiltration` | yes | whole-house air changes/hour (ACH) |
| `assemblies` | yes | U-value per surface category (`exterior_wall`, `basement_wall`, `window`, `door`, `ceiling`, `floor`) |
| `equipment` | optional | `existing_tons` — enables the Manual S existing-unit check |
| `cooling` | optional | indoor/1% outdoor temps, window SHGC, occupants — enables the cooling load |
| `ducts` | optional | friction rate, `unit_name`, `fitting_factor`, `available_static_pressure` — tunes Manual D. Runs derive per-room from the model; a hand-listed `runs` list is a fallback for models with no rooms |

All values are validated (finite, physical) — a zero supply-air rise, negative U-value, or backwards ΔT is rejected with a clear message.

## How geometry maps to loads

- **Exterior vs interior walls** — classified geometrically (perimeter walls of each level). Basement-level exterior walls are their own category.
- **Windows/doors** — an opening counts toward the envelope only when it sits unambiguously on one exterior wall (distance + overlap + orientation); interior openings are ignored.
- **Window orientation** — each window's compass facing comes from the model's compass `northDirection` + its wall angle. **Set `northDirection` from your survey** for true facing; until then the orientation split is provisional (the report says so).
- **Per-room loads (Manual J 1c)** — when the model has `<room>` polygons, each room gets its own load: exterior walls are split among the rooms they run behind (sampled along each wall), windows/doors are attributed by position, and top/bottom rooms get ceiling/floor. Rooms on garage/crawlspace levels are treated as unconditioned. Design CFM per room is the larger of its heating and cooling airflow.
- **Manual D from the model** — one branch per conditioned room plus a main trunk, sized round by the equal-friction method. Place a furniture item named "air handler" (override with `ducts.unit_name`) and each run gets a length (unit → room, Manhattan + vertical × a fitting factor); set `available_static_pressure` to derive the friction rate the ACCA way. Demo-grade: the fitting factor is not true fitting equivalent lengths.

## Development

```bash
ws test eldr          # run the suite (uses the component .venv)
ws lint eldr          # (when a linter is wired)
```

Tests are TDD-first and live in [`eldr/tests/`](eldr/tests/). The engine is UI-agnostic and never writes the model — it only reads.

## Scope & roadmap

Built: heating, cooling (orientation-resolved solar), Manual S, direct `.sh3d` read, lat/long → design-station lookup, per-room loads (Manual J 1c), and Manual D duct sizing from the model.

Ahead: true fitting equivalent lengths (drop the fitting-factor fudge) and return-duct sizing; an interview skill that fills the side-car by asking the owner; a Sweet Home 3D plugin wrapping the same engine; and the path to ACCA-certifiable output. Design + phasing: `realm-siliconsaga` `docs/plans/2026-07-15-eldr-manual-j-design.md`.
