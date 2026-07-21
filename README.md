# Eldr — Manual J heat-load engine for Sweet Home 3D

**Eldr** ("fire"; also reads as "Elder") turns a [Sweet Home 3D](https://www.sweethome3d.com/) house model into HVAC load numbers: whole-house **heating** and **cooling** loads (Manual J), plus an **equipment-sizing** recommendation (Manual S). It reads the model directly — no manual takeoff — and pairs the geometry SH3D already knows with a small side-car of thermal inputs it can't.

> **Status:** demo-grade, not ACCA-certified. The pipeline (model → load → size) is real and the math is honest; the *inputs* (assemblies, design temps, infiltration) start as estimates and get sharper as the house is measured. Certifiable output is the roadmap goal, not today's claim.

## What it does

- **Manual J heating** — `Σ U·A·ΔT` over the envelope + infiltration, and the supply CFM.
- **Manual J cooling (1b)** — conduction + **orientation-resolved solar gain** (west/east glass loads more than north, computed from the model's compass) + internal gains, plus latent.
- **Manual S** — the smallest standard equipment size that *meets* the design load (the larger of heating/cooling), the next size up, and a verdict against your existing unit (oversized / undersized / well-matched).

## Requirements

- Python 3.11+
- A local virtualenv with `pytest` + `pyyaml` (the `ws test eldr` adapter uses it):
  ```bash
  cd components/eldr
  python3 -m venv .venv
  .venv/bin/pip install pytest pyyaml
  ```

## Usage

Point Eldr at a model (an exploded `Home.xml` **or** a packed `.sh3d` — it reads the zip directly) and a side-car YAML:

```bash
cd components/eldr
.venv/bin/python -m eldr.cli ../../hoards/refrhus/Refrhus.sh3d eldr/example-sidecar.yaml
```

It prints a Markdown report (heating table, cooling table, Manual S sizing). The demo loop is: **edit the house in SH3D → save → re-run** and watch the numbers move.

## The side-car

The model owns geometry; the side-car owns everything thermal. See [`eldr/example-sidecar.yaml`](eldr/example-sidecar.yaml) for a documented starting point. Blocks:

| Block | Required? | What it carries |
|---|---|---|
| `design` | yes | heating setpoint, 99% outdoor design temp, supply-air rise |
| `infiltration` | yes | whole-house air changes/hour (ACH) |
| `assemblies` | yes | U-value per surface category (`exterior_wall`, `basement_wall`, `window`, `door`, `ceiling`, `floor`) |
| `equipment` | optional | `existing_tons` — enables the Manual S existing-unit check |
| `cooling` | optional | indoor/1% outdoor temps, window SHGC, occupants — enables the cooling load |

All values are validated (finite, physical) — a zero supply-air rise, negative U-value, or backwards ΔT is rejected with a clear message.

## How geometry maps to loads

- **Exterior vs interior walls** — classified geometrically (perimeter walls of each level). Basement-level exterior walls are their own category.
- **Windows/doors** — an opening counts toward the envelope only when it sits unambiguously on one exterior wall (distance + overlap + orientation); interior openings are ignored.
- **Window orientation** — each window's compass facing comes from the model's compass `northDirection` + its wall angle. **Set `northDirection` from your survey** for true facing; until then the orientation split is provisional (the report says so).

## Development

```bash
ws test eldr          # run the suite (uses the component .venv)
ws lint eldr          # (when a linter is wired)
```

Tests are TDD-first and live in [`eldr/tests/`](eldr/tests/). The engine is UI-agnostic and never writes the model — it only reads.

## Scope & roadmap

Built: heating, cooling (orientation-resolved solar), Manual S, direct `.sh3d` read.

Ahead: per-room zoning + per-room CFM (Manual J 1c) → Manual D duct design; lat/long → ASHRAE design-station lookup (drop the hardcoded design temps); an interview skill that fills the side-car by asking the owner; a Sweet Home 3D plugin wrapping the same engine; and the path to ACCA-certifiable output. Design + phasing: `realm-siliconsaga` `docs/plans/2026-07-15-eldr-manual-j-design.md`.
