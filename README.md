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

The four level-stack keys in the JSON:

- `levels` — `{level_name: {height_ft}}`, the storey height each level actually used.
- `spaces` — `{space_name: {heating_factor, cooling_factor}}`. `cooling_factor` is the fraction of the cooling design ΔT the engine **applied**, which for the attic is not the fraction the side-car declared — see [Buffer spaces](#buffer-spaces-and-their-temperatures) below. `heating_factor` *is* the declared policy's; only summer is substituted. `cooling_factor` is `null` exactly when the side-car has no `cooling:` block (a heating-only run) — with a `cooling:` block it is always a number, because the pipeline resolves `outdoor_1_f` from the model's lat/long or raises before the export runs.
- `voids` — `{room_name: {area_ft2, category}}`, present even when empty, so a consumer can tell "no gaps" from "an export predating the key". `category` is the surface category *that* gap resolved to (`buffer_floor` / `exposed_floor` / `floor`), carried from the resolver rather than inferred from `surfaces` — which answers "what does this envelope contain anywhere", a different question. It is `null` only when two rooms sharing a name resolved to different categories and merged, so no single category describes the entry.
- `surfaces` — `[{category, area_ft2, space}]`, one entry per envelope surface. `space` is `null` for anything not facing a buffer space (every wall, window, door, and an on-grade `floor`).

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
| `basement_wall` | below-grade wall — **effective** U, soil path included | — |
| `window` / `door` | openings on envelope walls | — |
| `ceiling` | ceiling to an attic or other space above | — |
| `floor` | floor on grade (slab) — **effective** U, soil path included | — |
| `buffer_wall` | wall to a garage / crawlspace | `exterior_wall` |
| `buffer_floor` | floor over a buffer space, drawn *or* undrawn | `exposed_floor`, then `floor` |
| `exposed_floor` | floor over open air (a cantilever or overhang) | `floor` |

**`exposed_floor` is provisional.** Nothing in the geometry infers it: the only way to produce one today is to declare `levels.<name>.below_void: outdoor`, which tells the resolver that an undrawn space beneath that level is open air rather than a crawlspace. Without that, an overhang resolves to `buffer_floor` over `crawlspace`. Treat the key as reserved for the cantilever case until real overhang detection lands.

### Below-grade U-values are effective values

**`basement_wall` and `floor` are the one place where the number you declare is not the assembly's own U.** They must be Manual J **effective below-grade U-values** — the construction's resistance *plus* the resistance of the heat path through the soil out to grade. Eldr then loads them at the full outdoor design ΔT, exactly like an exterior wall, because that is where Manual J puts the soil: in the assembly, not in the ΔT.

You can see the convention in any certified report. Bare 8-inch stone/brick, which on its own is nearer U-1.0, is listed below grade at **U-0.293** at an average depth of 3 ft and **U-0.297** at 4 ft — the same wall, two U-values, because the soil path lengthens with depth. Divide those rows' heating HTMs by their U-values and every one comes back to the full outdoor design ΔT.

**If you declare a bare-wall U here, Eldr will overstate your heating load, and nothing will complain.** That is the trade for behaving the way a certifiable calculation behaves. Sources for a real number: the ACCA Manual J below-grade tables (indexed by construction *and* average depth below grade), or the corresponding rows of an existing professional report on the same house. `design.ground_temp_f` is **not** a substitute — it is read by the cooling path only, and no longer affects heating at all.

Two limits worth knowing before you pick a value. Eldr applies **one U per category regardless of depth**, so a wall that is part shallow and part deep needs a single averaged number. And Eldr has **no grade line**: every wall on a basement level is `basement_wall` over its full drawn height, while Manual J would split that wall and count the part standing above grade as an ordinary above-grade wall. If a meaningful share of your basement wall is above grade, either draw it as two segments or pick a U that averages the two. Both limits are restated in the report's *Open questions* block whenever the model has below-grade surfaces.

### Buffer spaces and their temperatures

A *space* is a named unconditioned volume next to conditioned rooms — an attic, a crawlspace, an attached garage. Surfaces facing one see a **fraction of the design ΔT**, and that fraction is per space, not one constant for the house. Precedence, strongest first:

1. an observed design-day temperature (`winter_temp_f` / `summer_temp_f`) — an *observation*, so it outranks everything
2. an explicit `factor` (fraction of the design ΔT, so **0–1**; a space hotter than outdoor air is declared with `summer_temp_f`, not here, and a `factor` above 1 is rejected at load)
3. the `vented` shorthand — `true` → 1.0, `false` → 0.5
4. the space's built-in default (`attic`, `crawlspace` and `garage` default to unvented 0.5; `outdoor` to 1.0)

Temperature wins because "the crawl gets near freezing on the coldest days" is a stronger claim to hand an HVAC partner than "we assumed 50% of ΔT", and it short-circuits chains that would otherwise need modeling (a crawlspace partly vented to outdoors *and* open into the garage).

**A space name Eldr does not recognize falls silently to rung 4.** Unlike an unknown level name in `levels:`, which warns, a space that matches neither a `spaces:` entry nor a built-in default just gets a bare policy — so a `below_void: crawlspce` typo, or an unconditioned level renamed in SH3D, quietly loads at 0.5 with nothing said. The report's *Buffer spaces* table is the check: its **Winter input** column reads "no policy declared" for exactly this case, so scan it for a space name you did not expect.

`factor` and `vented` are **winter** shorthands, and both cap a space at or below outdoor air — which is backwards for a sunlit attic in summer. So unless `spaces.attic.summer_temp_f` says otherwise, the attic's cooling ΔT comes from a real temperature: `cooling.attic_temp_f` if set, else a **sol-air estimate** of `outdoor_1_f + 50 °F × 0.85 roof absorptance`. On Refrhus that is 91 + 42.5 = **133.5 °F**, a cooling factor of 3.66 — the least obvious number the engine produces, and the reason a ceiling can load at several times the outdoor ΔT. The report's *Buffer spaces* table prints the temperature and factor each space actually got, and says whether it was observed or estimated. The estimate is deliberately crude: a flat solar uplift, no roof area, orientation, ventilation rate or radiant barrier. Set `cooling.attic_temp_f` to replace it with an observed value.

Note the built-in `attic` default of `vented: false` **diverges from typical ACCA practice**, which loads a vented attic at full outdoor temperature for heating. It follows the Refrhus owner's observation of their own house; declare `vented: true` to restore ACCA behavior. The report echoes which was used.

## How geometry maps to loads

- **Exterior vs interior walls** — a wall is on the envelope if a *conditioned* room sits on exactly one side (the room-polygon outline), so perimeter walls on an extension/wing are caught even off the level's bounding rectangle, and walls of unconditioned space (garage/crawlspace levels) are excluded. Levels with no rooms fall back to a bounding-box test. Basement-level walls are their own (ground-coupled) category. Any wall can be **explicitly tagged** in the `walls` block to override the inference. Caveat: conditioned interior space not yet drawn as a room reads as "outdoors" and over-counts until drawn.
- **Below-grade surfaces carry the full outdoor ΔT in winter, and no load at all in summer** — `basement_wall` and `floor` are loaded at the same `indoor − outdoor_heating_99_f` ΔT as an exterior wall, because in Manual J the soil path belongs to the *assembly*, not to the ΔT. See [Below-grade U-values are effective values](#below-grade-u-values-are-effective-values) — this is the one modeling rule that silently misprices a basement if you feed it the wrong kind of U. Cooling is different and stays ground-coupled: `max(0, design.ground_temp_f − cooling.indoor_f)`, which is zero for any normal house, so below-grade surfaces add no summer gain. A certified report agrees on both counts — its below-grade heating HTMs divide out to the full design ΔT, and its basement-slab cooling HTM is 0.00.
- **Buffer walls** — a wall tagged `buffer` (garage/crawlspace-adjacent) is loaded at **`BUFFER_FACTOR` = 50% of the design ΔT** — neither interior nor fully exterior, since the buffer floats between indoor and outdoor. Geometry can't infer this (it's often a cross-level, partial adjacency), so it's **tag-only**; whole-wall for now (no partial height/length split yet). A tagged wall carries no space name, so it keeps that flat 50%; the *horizontal* surfaces below do carry one and get that space's own policy instead — see [Buffer spaces and their temperatures](#buffer-spaces-and-their-temperatures), where an observed temperature outranks a factor, which outranks the `vented` shorthand, which outranks the default.
- **Ceilings and floors come from what is actually above and below** — for each room, Eldr rasterizes the room polygon and, cell by cell, scans up and down the level stack for the first level carrying a room over that cell. Adjacency is **XY footprint overlap**; elevation only *orders* the levels, so a garage that shares a storey's vertical span but not its footprint is correctly not adjacent. Each face then splits by what it found: a conditioned room above or below is **interior** and emits no surface at all; an unconditioned level's room emits `ceiling` or `buffer_floor` carrying that level's name **lowercased** as its space (an SH3D level named `Crawlspace` is the `crawlspace` you declare under `spaces:`); the lowest conditioned level's floor is `floor` (on grade). One room can therefore be part interior and part attic — a partial ceiling — and the whole-house totals are the sum of the per-room ones by construction. Slivers within a wall-thickness tolerance, and resolved regions under 2 ft², are discarded as misalignment noise.
- **Roomless levels are scaffolding** — a level with no rooms (a joist layer, a duct chase, a transition level) takes no part in the stack and contributes no volume. If it has walls, Eldr *warns* rather than guessing, because a real storey mid-modeling looks identical; `levels.<name>.role: ignore` acknowledges it and silences the message. The exception: a model with **no rooms anywhere** falls back wholesale to the legacy bounding-box envelope, so roomless models keep working exactly as before.
- **Nothing drawn below is a reported gap, not a silent assumption** — floor area with no level beneath it at all becomes `buffer_floor` over the space named by `levels.<name>.below_void` (default `crawlspace`), *and* is itemized room-by-room in the report's *Schematic gaps* block. Draw those spaces in Sweet Home 3D and the assumption is replaced by geometry. Set `below_void: outdoor` instead to model an overhang, which produces the provisional `exposed_floor` category. `below_void: ground` is legal too and resolves the void to a ground-coupled `floor` — the right answer for a slab-on-grade wing that was never drawn as its own level, and the one value the *Schematic gaps* block deliberately makes **no** treatment claim about, because its treatment is the ordinary on-grade one and there is nothing to warn a reader about. Each gap carries the category it resolved to, so a model mixing `below_void`s gets each gap described by its own. On the **lowest conditioned level** an undrawn gap defaults to `ground` (the house sits on grade) — an explicit `below_void` there overrides that, which is how a house on piers gets its `exposed_floor`. A void above defaults to `attic`, overridable with `above_void`.
- **Windows/doors** — an opening counts toward the envelope only when it sits unambiguously on one exterior wall (distance + overlap + orientation); interior openings are ignored.
- **Window orientation** — each window's compass facing comes from the model's compass `northDirection` + its wall angle. **Set `northDirection` from your survey** for true facing; until then the orientation split is provisional (the report says so).
- **Per-room loads (Manual J 1c)** — when the model has `<room>` polygons, each room gets its own load: exterior walls are split among the rooms they run behind (sampled along each wall), windows/doors are attributed by position, and top/bottom rooms get ceiling/floor. Rooms on garage/crawlspace levels are treated as unconditioned. Design CFM per room is the larger of its heating and cooling airflow.
- **Manual D from the model** — one branch per conditioned room plus a main trunk, sized round by the equal-friction method. Place a furniture item named "air handler" (override with `ducts.unit_name`) and each run gets a length (unit → room, Manhattan + vertical × a fitting factor); set `available_static_pressure` to derive the friction rate the ACCA way. Demo-grade: the fitting factor is not true fitting equivalent lengths.

### Known limitations of the level stack

- **Levels are keyed by name, and SH3D does not make names unique.** The report's *Level heights* table and the JSON `levels` / per-level volume maps both key off the level's SH3D name, so two levels sharing one name **collide into a single entry**. Eldr rejects duplicate names outright only when a `levels:` block is present (an entry could not address one of them unambiguously); with no `levels:` block they merge silently. Give every level a distinct name in Sweet Home 3D.
- **A level with rooms and no walls has no envelope surfaces of its own.** Its conditioned volume is counted (volume follows rooms, not walls), but walls are what produce wall/window/door surfaces, so such a level contributes conduction only through its resolved floor and ceiling. That is correct for an open loft and wrong for a storey whose walls simply have not been drawn yet — and the model looks the same either way, so nothing can warn about it. Check the *Level heights* table against what you expect the level to hold.

## Development

```bash
ws test eldr          # run the suite (uses the component .venv)
ws lint eldr          # (when a linter is wired)
```

Tests are TDD-first and live in [`eldr/tests/`](eldr/tests/). The engine is UI-agnostic and never writes the model — it only reads.

## Scope & roadmap

Built: heating, cooling (orientation-resolved solar), Manual S, direct `.sh3d` read, lat/long → design-station lookup, per-room loads (Manual J 1c), Manual D duct sizing from the model, and the level-stack model — partial ceilings resolved against what is actually above each room, buffer floors resolved against what is below (and exposed floors, but **provisionally** — see above: nothing infers one, so it needs an explicit `below_void: outdoor`), per-space temperature policies, hot-attic cooling gain, and the assumption echoes (level heights, space factors, borrowed U-values, schematic gaps) that keep all of it from being silent.

Ahead: a real attic energy balance (roof area, ventilation rate, radiant barrier) and orientation-aware sol-air, replacing the flat solar uplift; sloped-roof and knee-wall geometry; partial height/length splits within one wall; true fitting equivalent lengths (drop the fitting-factor fudge) and return-duct sizing; an interview skill that fills the side-car by asking the owner; a Sweet Home 3D plugin wrapping the same engine; and the path to ACCA-certifiable output. Design + phasing: `realm-siliconsaga` `docs/plans/2026-07-15-eldr-manual-j-design.md`; the level-stack cut is `docs/2026-08-13-level-stack-model-design.md`.
