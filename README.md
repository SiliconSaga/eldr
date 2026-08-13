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

Three other output modes:

```bash
.venv/bin/python -m eldr.cli MODEL --walls                 # list walls + boundaries, to hand-tag
.venv/bin/python -m eldr.cli MODEL SIDECAR --overview      # full narrative "demo overview" doc
.venv/bin/python -m eldr.cli MODEL SIDECAR --json          # the whole analysis as structured JSON
```

`--overview` renders the same numbers as the report, wrapped in a deterministic narrative (ACCA-chain intro, honesty caveats auto-selected from the model, roadmap) — so the demo write-up never drifts from the engine. `--json` emits the same computation as machine-readable data (design, loads by category, Manual S, every room, every duct run, plus `levels`, `spaces`, `voids` and `surfaces` — the level stack and the horizontal split, so a consumer never has to re-derive them) — for a UI, a spreadsheet, or grounding an "ask the house" chatbot in exact values. The output modes are mutually exclusive.

In the JSON, `spaces.<name>.cooling_factor` is the fraction of the cooling design ΔT the engine **applied**, which for the attic is not the fraction the side-car declared — see [Buffer spaces](#buffer-spaces-and-their-temperatures) below. `voids` is present even when empty, so a consumer can tell "no gaps" from "an export predating the key".

## The side-car

The model owns geometry; the side-car owns everything thermal. See [`eldr/example-sidecar.yaml`](eldr/example-sidecar.yaml) for a documented starting point. Blocks:

| Block | Required? | What it carries |
|---|---|---|
| `design` | yes | heating setpoint, 99% outdoor design temp, supply-air rise |
| `infiltration` | yes | whole-house air changes/hour (ACH) |
| `assemblies` | yes | U-value per surface category (`exterior_wall`, `basement_wall`, `window`, `door`, `ceiling`, `floor`, `buffer_wall`, `buffer_floor`, `exposed_floor`) |
| `equipment` | optional | `existing_tons` — enables the Manual S existing-unit check |
| `cooling` | optional | indoor/1% outdoor temps, window SHGC, occupants — enables the cooling load. `attic_temp_f` overrides the sol-air attic estimate |
| `ducts` | optional | friction rate, `unit_name`, `fitting_factor`, `available_static_pressure` — tunes Manual D. Runs derive per-room from the model; a hand-listed `runs` list is a fallback for models with no rooms |
| `walls` | optional | explicit per-wall boundary overrides, keyed by SH3D wall id: `{boundary: exterior\|ground\|buffer\|interior}`. Untagged walls are inferred. `buffer` (garage/crawl-adjacent) can *only* be set here. Use `--walls` to find the ids |
| `levels` | optional | per-level overrides, keyed by the level's SH3D **name**: `role` (`conditioned\|unconditioned\|ignore`), `height_ft` (correct a storey height SH3D defaulted), `below_void` / `above_void` (what an undrawn neighbour on that side means) |
| `spaces` | optional | temperature policy per buffer space (`attic`, `crawlspace`, `garage`, or any unconditioned level's name lowercased): `winter_temp_f`, `summer_temp_f`, `factor`, `vented` |

All values are validated (finite, physical) — a zero supply-air rise, negative U-value, or backwards ΔT is rejected with a clear message. An unknown level name in `levels` is a warning (renamed or typo'd); a `cooling.attic_temp_f` at or below the cooling setpoint is an error, because the whole point of that key is an attic hotter than the house.

### Assembly keys and their fallbacks

A side-car written before a category existed keeps working: an unset U-value borrows a related one. **Every borrow warns and is disclosed in the report's *Borrowed assembly U-values* table** — it is a stand-in, not a measurement, and `floor` in particular is usually a *slab's effective whole-area* U (the real loss is perimeter-edge), which can be an order of magnitude below a framed floor's.

| Key | The surface it covers | Borrows, if unset |
|---|---|---|
| `exterior_wall` | wall to outdoor air | — |
| `basement_wall` | below-grade wall (ground-coupled) | — |
| `window` / `door` | openings on envelope walls | — |
| `ceiling` | ceiling to an attic or other space above | — |
| `floor` | floor on grade (slab) | — |
| `buffer_wall` | wall to a garage / crawlspace | `exterior_wall` |
| `buffer_floor` | floor over a buffer space, drawn *or* undrawn | `exposed_floor`, then `floor` |
| `exposed_floor` | floor over open air (a cantilever or overhang) | `floor` |

**`exposed_floor` is provisional.** Nothing in the geometry infers it: the only way to produce one today is to declare `levels.<name>.below_void: outdoor`, which tells the resolver that an undrawn space beneath that level is open air rather than a crawlspace. Without that, an overhang resolves to `buffer_floor` over `crawlspace`. Treat the key as reserved for the cantilever case until real overhang detection lands.

### Buffer spaces and their temperatures

A *space* is a named unconditioned volume next to conditioned rooms — an attic, a crawlspace, an attached garage. Surfaces facing one see a **fraction of the design ΔT**, and that fraction is per space, not one constant for the house. Precedence, strongest first:

1. an observed design-day temperature (`winter_temp_f` / `summer_temp_f`) — an *observation*, so it outranks everything
2. an explicit `factor` (fraction of the design ΔT)
3. the `vented` shorthand — `true` → 1.0, `false` → 0.5
4. the space's built-in default (`attic`, `crawlspace` and `garage` default to unvented 0.5; `outdoor` to 1.0)

Temperature wins because "the crawl gets near freezing on the coldest days" is a stronger claim to hand an HVAC partner than "we assumed 50% of ΔT", and it short-circuits chains that would otherwise need modeling (a crawlspace partly vented to outdoors *and* open into the garage).

`factor` and `vented` are **winter** shorthands, and both cap a space at or below outdoor air — which is backwards for a sunlit attic in summer. So unless `spaces.attic.summer_temp_f` says otherwise, the attic's cooling ΔT comes from a real temperature: `cooling.attic_temp_f` if set, else a **sol-air estimate** of `outdoor_1_f + 50 °F × 0.85 roof absorptance`. On Refrhus that is 91 + 42.5 = **133.5 °F**, a cooling factor of 3.66 — the least obvious number the engine produces, and the reason a ceiling can load at several times the outdoor ΔT. The report's *Buffer spaces* table prints the temperature and factor each space actually got, and says whether it was observed or estimated. The estimate is deliberately crude: a flat solar uplift, no roof area, orientation, ventilation rate or radiant barrier. Set `cooling.attic_temp_f` to replace it with an observed value.

Note the built-in `attic` default of `vented: false` **diverges from typical ACCA practice**, which loads a vented attic at full outdoor temperature for heating. It follows the Refrhus owner's observation of their own house; declare `vented: true` to restore ACCA behavior. The report echoes which was used.

## How geometry maps to loads

- **Exterior vs interior walls** — a wall is on the envelope if a *conditioned* room sits on exactly one side (the room-polygon outline), so perimeter walls on an extension/wing are caught even off the level's bounding rectangle, and walls of unconditioned space (garage/crawlspace levels) are excluded. Levels with no rooms fall back to a bounding-box test. Basement-level walls are their own (ground-coupled) category. Any wall can be **explicitly tagged** in the `walls` block to override the inference. Caveat: conditioned interior space not yet drawn as a room reads as "outdoors" and over-counts until drawn.
- **Below-grade surfaces are ground-coupled** — `basement_wall` and `floor` use a ground ΔT (indoor − `design.ground_temp_f`, default 50°F) instead of the outdoor-air ΔT, because they lose heat to ~50°F soil, not design-cold air. In summer the soil is a heat sink, so they add no cooling load. This is why a partial basement stops dominating the load.
- **Buffer walls** — a wall tagged `buffer` (garage/crawlspace-adjacent) is loaded at **`BUFFER_FACTOR` = 50% of the design ΔT** — neither interior nor fully exterior, since the buffer floats between indoor and outdoor. Geometry can't infer this (it's often a cross-level, partial adjacency), so it's **tag-only**; whole-wall for now (no partial height/length split yet). A tagged wall carries no space name, so it keeps that flat 50%; the *horizontal* surfaces below do carry one and get that space's own policy instead — see [Buffer spaces and their temperatures](#buffer-spaces-and-their-temperatures), where an observed temperature outranks a factor, which outranks the `vented` shorthand, which outranks the default.
- **Ceilings and floors come from what is actually above and below** — for each room, Eldr rasterizes the room polygon and, cell by cell, scans up and down the level stack for the first level carrying a room over that cell. Adjacency is **XY footprint overlap**; elevation only *orders* the levels, so a garage that shares a storey's vertical span but not its footprint is correctly not adjacent. Each face then splits by what it found: a conditioned room above or below is **interior** and emits no surface at all; an unconditioned level's room emits `ceiling` or `buffer_floor` carrying that level's name **lowercased** as its space (an SH3D level named `Crawlspace` is the `crawlspace` you declare under `spaces:`); the lowest conditioned level's floor is `floor` (on grade). One room can therefore be part interior and part attic — a partial ceiling — and the whole-house totals are the sum of the per-room ones by construction. Slivers within a wall-thickness tolerance, and resolved regions under 2 ft², are discarded as misalignment noise.
- **Roomless levels are scaffolding** — a level with no rooms (a joist layer, a duct chase, a transition level) takes no part in the stack and contributes no volume. If it has walls, Eldr *warns* rather than guessing, because a real storey mid-modeling looks identical; `levels.<name>.role: ignore` acknowledges it and silences the message. The exception: a model with **no rooms anywhere** falls back wholesale to the legacy bounding-box envelope, so roomless models keep working exactly as before.
- **Nothing drawn below is a reported gap, not a silent assumption** — floor area with no level beneath it at all becomes `buffer_floor` over the space named by `levels.<name>.below_void` (default `crawlspace`), *and* is itemized room-by-room in the report's *Schematic gaps* block. Draw those spaces in Sweet Home 3D and the assumption is replaced by geometry. Set `below_void: outdoor` instead to model an overhang, which produces the provisional `exposed_floor` category. A void above defaults to `attic`, overridable with `above_void`.
- **Windows/doors** — an opening counts toward the envelope only when it sits unambiguously on one exterior wall (distance + overlap + orientation); interior openings are ignored.
- **Window orientation** — each window's compass facing comes from the model's compass `northDirection` + its wall angle. **Set `northDirection` from your survey** for true facing; until then the orientation split is provisional (the report says so).
- **Per-room loads (Manual J 1c)** — when the model has `<room>` polygons, each room gets its own load: exterior walls are split among the rooms they run behind (sampled along each wall), windows/doors are attributed by position, and top/bottom rooms get ceiling/floor. Rooms on garage/crawlspace levels are treated as unconditioned. Design CFM per room is the larger of its heating and cooling airflow.
- **Manual D from the model** — one branch per conditioned room plus a main trunk, sized round by the equal-friction method. Place a furniture item named "air handler" (override with `ducts.unit_name`) and each run gets a length (unit → room, Manhattan + vertical × a fitting factor); set `available_static_pressure` to derive the friction rate the ACCA way. Demo-grade: the fitting factor is not true fitting equivalent lengths.

### Known limitations of the level stack

- **Levels are keyed by name, and SH3D does not make names unique.** The report's *Level heights* table and the JSON `levels` / per-level volume maps both key off the level's SH3D name, so two levels sharing one name **collide into a single entry**. Eldr rejects duplicate names outright only when a `levels:` block is present (an entry could not address one of them unambiguously); with no `levels:` block they merge silently. Give every level a distinct name in Sweet Home 3D.
- **A level with rooms but no walls contributes 0 ft³.** The conditioned volume is accumulated while walking each level's *walls*, so a level with rooms drawn and no walls is never visited and reads "0 ft³" in the *Level heights* table — indistinguishable there from a level that legitimately holds no conditioned rooms. This is a pre-existing gap in the volume path that the new per-level table has made visible, not a regression; draw the level's walls and it resolves.

## Development

```bash
ws test eldr          # run the suite (uses the component .venv)
ws lint eldr          # (when a linter is wired)
```

Tests are TDD-first and live in [`eldr/tests/`](eldr/tests/). The engine is UI-agnostic and never writes the model — it only reads.

## Scope & roadmap

Built: heating, cooling (orientation-resolved solar), Manual S, direct `.sh3d` read, lat/long → design-station lookup, per-room loads (Manual J 1c), Manual D duct sizing from the model, and the level-stack model — partial ceilings resolved against what is actually above each room, buffer and exposed floors resolved against what is below, per-space temperature policies, hot-attic cooling gain, and the assumption echoes (level heights, space factors, borrowed U-values, schematic gaps) that keep all of it from being silent.

Ahead: a real attic energy balance (roof area, ventilation rate, radiant barrier) and orientation-aware sol-air, replacing the flat solar uplift; sloped-roof and knee-wall geometry; partial height/length splits within one wall; true fitting equivalent lengths (drop the fitting-factor fudge) and return-duct sizing; an interview skill that fills the side-car by asking the owner; a Sweet Home 3D plugin wrapping the same engine; and the path to ACCA-certifiable output. Design + phasing: `realm-siliconsaga` `docs/plans/2026-07-15-eldr-manual-j-design.md`; the level-stack cut is `docs/2026-08-13-level-stack-model-design.md`.
