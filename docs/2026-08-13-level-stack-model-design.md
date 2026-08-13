# Eldr level-stack surface model — design

**Status:** Approved (design) 2026-08-13, pending spec review → implementation plan. Replaces the "ceiling on the top level, floor on the bottom level" stack assumption with resolved per-room adjacency, and generalizes the single `BUFFER_FACTOR` into per-space temperature policies. Directly delivers the follow-up deferred in [`2026-07-24-wall-boundary-model-design.md`](2026-07-24-wall-boundary-model-design.md) § Scope/deferred: *"Per-buffer-space temperatures (garage vs. vented crawl vs. attic) — one factor for now."*

## Goal

Make the **horizontal** envelope as accurate as the wall-boundary work made the vertical one. Today a room gets a ceiling only if it sits on the highest level and a floor only if it sits on the lowest, and both use the level's *bounding box*. In a house that grew in stages — a partial second floor, a crawlspace under one extension, a garage beside rather than below — that model silently omits most of the horizontal envelope.

Validated against the professional Manual J for Refrhus (`hoards/refrhus/eldr-vs-manualj-2026-08.md`), the two missing surfaces are the largest remaining conductive gaps: floors 762 vs their 8,608 BTU/hr, ceilings 1,465 vs their 5,008.

## What the model actually looks like

Measured from `Refrhus.sh3d` — this is the shape the design must survive, not a hypothetical:

| Level | Spans | Height | Rooms | Contents |
|---|---|---|---|---|
| Basement | 0.00 – 7.00 ft | 7.00 | 3 | Utility, Future Media, Bathroom |
| Garage | 4.00 – 14.00 ft | 10.00 | 7 | Garage ×3 (duplicates) + Driveway, Porch, Walkway, Driveway Extended |
| Crawlspace | 4.00 – 8.00 ft | 4.00 | 1 | Crawlspace (137 sqft) |
| basement-main-transition | 7.00 – 8.00 ft | 1.00 | **0** | 33 furniture (joists), no walls |
| Main | 8.39 – 16.39 ft | 8.00 | 9 | Kitchen, Main Bed, Living room, … (940 sqft) |
| 2nd floor | 16.79 – 24.79 ft | 8.00 | 6 | Office, Play Room, … (517 sqft) |

Four properties fall out of this, and each one constrains the design:

**Levels overlap vertically and it means nothing.** The Garage spans 4–14 ft, overlapping the Basement, the Crawlspace, the transition layer, and the lower 5.6 ft of Main. Any adjacency rule based on vertical spans concludes the garage sits below Main. It does not — it sits beside it. **XY footprint overlap decides adjacency; elevation only decides ordering.**

**Levels can share an elevation.** Garage and Crawlspace are both at 121.92 cm, distinguished only by `elevationIndex` (0 and 1). Ordering must be `(elevation, elevationIndex)`.

**Some levels are modeling scaffolding.** `basement-main-transition` exists solely to hold joists and duct runs: 0 rooms, 0 walls, 33 furniture pieces. It must not participate in the stack, and must not contribute volume.

**Elevations are derived; heights are softer.** Main's 255.84 cm is exactly Basement top + transition + `floorThickness`, and the 2nd floor is exactly Main top + `floorThickness` — SH3D computed these as levels were stacked, and they are self-consistent. The **heights** are mixed provenance: Main and 2nd floor are both 8.00 ft, SH3D's imperial default, while Basement (7), Garage (10) and Crawl (4) are clearly deliberate. Ordering is trustworthy; heights need to be visible and overridable.

## Resolved adjacency — the measurements to hit

Rasterized XY overlap of Main's rooms against the levels below and above:

| Main room | area | over basement | over crawl | over nothing | under 2nd floor | **direct to attic** |
|---|---:|---:|---:|---:|---:|---:|
| Living room | 286.1 | 248.8 | 0.0 | 36.4 | 185.6 | 100.5 |
| Kitchen | 243.6 | 98.1 | 122.1 | 22.5 | 36.5 | 207.1 |
| Main Bed | 233.5 | 113.3 | 0.0 | 120.1 | 160.2 | 73.3 |
| Kids Room | 75.6 | 75.6 | — | — | 36.0 | 39.6 |
| Main Closet | 44.9 | 44.9 | — | — | 39.2 | 5.7 |
| Main Bath | 33.0 | 30.6 | — | 2.5 | 10.4 | 22.6 |
| Closet | 12.9 | 12.9 | — | — | 3.9 | 9.0 |

Whole-level: Main is 939.9 sqft, of which 631.8 over basement, 122.1 over crawl, 1.0 over garage, 184.1 over nothing. Its ceiling is 481.1 sqft under the 2nd floor and **458.8 sqft direct to attic** — none of which is modeled today.

(This table was rasterized at a 10 cm grid, the erosion table below at 5 cm, so room areas differ between them by a few tenths of a square foot. Neither is the model's authoritative area; they are adjacency measurements.)

Two findings drive the hard parts of the design.

**The garage picks up 1.0 sqft under Main.** Noise from a shared wall, not an adjacency — despite 5.6 ft of vertical overlap. Confirms the XY rule, and shows that tiny resolved regions are artifacts.

**184.1 sqft of Main sits over nothing.** The owner confirms the Kitchen is entirely over basement and crawl, so its 22.5 sqft is wall misalignment. The Main Bed's 120.1 sqft is real: crawlspace that exists under the extension but was never drawn. Note that drawn crawl under Main (122.1) plus the Main Bed void (120.1) is 242 sqft, against the professional report's 264 sqft of uninsulated floor over crawlspace — the two independent sources agree, which is why the void must be modeled rather than skipped.

## Void regions: misalignment vs. reality

Voids cannot be promoted to surfaces silently — the schematic is a work in progress, and inventing load from unfinished geometry would be worse than under-counting. The design phase measured erosion of the void region away from the room's own outline, which separates the two failure modes:

| Main room | void @0cm | @10cm | @20cm | @30cm | @45cm |
|---|---:|---:|---:|---:|---:|
| Living room | 36.8 | 32.7 | 27.7 | 22.5 | 15.0 |
| Kitchen | 17.5 | 14.3 | 12.8 | 11.5 | 10.8 |
| Main Bed | 117.2 | 108.4 | 97.2 | 88.3 | 75.7 |
| Main Bath | 2.5 | 1.0 | 0.0 | 0.0 | 0.0 |
| Kids Room / closets / unnamed | ≤2.4 | ≤0.6 | 0.0 | 0.0 | 0.0 |

A 20 cm tolerance erases the genuine slivers — Main Bath, the unnamed fragments, every closet — because misalignment artifacts are thin bands roughly a wall-thickness wide hugging the room outline. But the Kitchen keeps 12.8 sqft even at 45 cm: that is not an edge band, it is an interior region where no basement or crawl polygon covers the Kitchen footprint. Erosion handles wall-thickness noise; it does not handle not-yet-tiled gaps.

Therefore: **the tolerance removes sliver noise, surviving voids become buffer floor, and every surviving void is itemized by room and area in the report** — so a bogus void stays visible and fixable rather than being quietly priced in. That intent stands; the operator that delivers it changed during implementation, and the next section records what that cost.

### What actually shipped, and what it cost

**The tolerance is measured against the VOID's own boundary, not the room's outline, and it is a morphological opening rather than a plain erosion** (`stack._settle_voids`). This is a deliberate change made during implementation, and the table above is the design-phase measurement of an algorithm that is no longer the one running. Two problems forced it:

- **Measuring against the room's outline is one-directional.** It eats a tolerance-wide band off every side of a *real* void that happens to touch the room's edge — which is most of them — and hands that area to whatever is drawn beside it. On the Main Bed that biased the split to 70.55/58.62 where the geometry says 50/50: a 9.2% misallocation, understating precisely the crawlspace-facing floor this resolver exists to find. Erosion against the void's own boundary has no such preferred direction.
- **A plain erosion still shaves the rim** where a real void meets real coverage, with the same one-directional loss. So the eroded core is dilated back over the void by the same tolerance — an opening — which deletes thin artifacts outright while leaving fat voids whole. A room narrower than twice the tolerance also stops losing its floor entirely, which the room-outline version did.

The consequence for this document: **the Kitchen regression signal did not survive.** Measured on the real model, the Kitchen's gap is 3.54 sqft at zero tolerance and gone at 20 — absorbed into `interior` rather than itemized:

```
tol= 0  {'Kitchen': 3.54, 'Living room': 39.91, 'Main Bed': 113.89}
tol=20  {'Living room': 39.41, 'Main Bed': 110.09}   <- as shipped
```

Note the design table's 17.5 sqft at zero tolerance against the 3.54 measured here: that difference is the raster, not the operator — the design measurements were taken at a 5 cm grid and the engine ships at 15 cm. What is left at the shipped grid is thin enough that an opening deletes it, which is what an opening is for. So the doc's promise that the Kitchen "should trend to ~0 as the walls are aligned, and the report says when it gets there" is **not delivered**: the Kitchen reads 0 today whether the walls are aligned or not, and its gap is priced into the load instead of being itemized.

**The trade was accepted, and it is the right way round.** The signal it cost is one room's diagnostic on one house, worth a few sqft of visibility. The bias it fixed was a systematic 9.2% understatement of buffer-floor area on every void that touches a room edge, on every model — a wrong *number*, not a missing hint. The Main Bed (110.09 sqft) and Living room (39.41 sqft) still carry the itemization, so the schematic gap the block exists to surface is still surfaced; it is the small-scatter end of the range that has gone quiet. Recovering it would mean reporting pre-opening void area alongside the post-opening one, which is a reporting change rather than an algorithm change and is listed as deferred below.

## Buffer-space policy

The single `BUFFER_FACTOR = 0.5` becomes a per-space policy. A **space** is a named unconditioned volume adjacent to conditioned rooms: `attic`, `crawlspace`, `garage`, or any unconditioned level's name.

Each space resolves a heating and a cooling ΔT fraction, in this precedence order:

1. **`winter_temp_f` / `summer_temp_f`** — the space's design-day temperature. Preferred, because it is an *observation*, not a model, and it short-circuits chains that would otherwise need modeling. The factor is derived: `(indoor − space) / (indoor − outdoor)`.
2. **`factor`** — an explicit fraction of design ΔT, for spaces that can only be estimated.
3. **`vented: true|false`** — shorthand for factor 1.0 / 0.5.
4. **Per-space-type default** when nothing is declared.

The Refrhus crawlspace is the motivating case: not deliberately vented, but with a crack to the outside, open into the garage (itself a buffer), and observed to get cold without going much below freezing on the coldest days. Modeling that chain honestly is hard; stating it is easy. `winter_temp_f: 32` on a 13 °F design day with 70 °F indoors yields a factor of 0.67 — between a sealed 0.5 and the professionals' implied 1.0, and defensible to an HVAC partner in one sentence.

**The report always echoes both the temperature and the derived factor**, extending the existing buffer-factor echo, so no assumption is silent.

### The attic

The attic is a buffer space like any other, with two additions:

- **Cooling:** a void *above* the space engages solar. `cooling.attic_temp_f` sets it explicitly; otherwise a sol-air estimate. This is the 4,406 vs 373 BTU/hr cooling gap. **As built the estimate is `outdoor_1_f + 50 °F × roof absorptance` (0.85 default) and ignores orientation entirely** — 133.5 °F on Refrhus. This design originally said "roof absorptance and the compass"; the compass never entered the implementation, and orientation-aware sol-air is listed as deferred below rather than silently claimed here.
- **Heating:** default `vented: false` → factor 0.5, per the owner's observation that the attic has no visible ridge or soffit ventilation and does not get outdoor-cold.

**Known divergence, recorded deliberately:** ACCA practice generally loads a *vented* attic at full outdoor temperature for heating, which is what the professional report appears to do. Defaulting to 0.5 moves Eldr's heating ceiling line further from theirs, not closer. This is the owner's call on their own house and is correct for an unvented attic; `vented: true` restores ACCA behavior, and the report echoes which was used. Flagged here so the reconciliation document does not read it as a regression.

## Surface vocabulary → treatment

Extending the wall-boundary table to horizontal surfaces:

| Surface | Heating ΔT | Cooling ΔT | Assembly key |
|---|---|---|---|
| `floor` | ground ΔT (soil) | max(0, ground − indoor) | `floor` |
| `buffer_floor` | space factor × outdoor ΔT | space factor × cooling ΔT | `buffer_floor` → falls back to `exposed_floor`, then `floor` |
| `exposed_floor` | outdoor ΔT | outdoor ΔT | `exposed_floor` → falls back to `floor` |
| `ceiling` | attic policy ΔT | attic policy ΔT (sol-air) | `ceiling` |
| *interior* | — (no surface emitted) | — | — |

A conditioned room above another conditioned room emits **no** horizontal surface — that is the interior case, and it is why the resolver must distinguish "conditioned below" from "nothing below."

`Surface` gains an optional `space: str | None` field carrying *which* buffer space the surface faces, because a category alone can no longer determine ΔT once policies are per-space — a `buffer_floor` over the crawl and one over the garage are the same category with different factors. The ΔT resolvers in `loads.py` therefore take the `Surface` rather than its category string. The field defaults to `None`, so every existing construction site keeps working.

Assembly fallbacks matter for compatibility: an existing side-car declaring only `floor` keeps working, and a buffer floor borrows that U rather than erroring. The Refrhus reconciliation will set `buffer_floor` explicitly to the professionals' measured U-0.521 for the uninsulated crawl floor, against `floor`'s slab U-0.020 — a 26× difference, which is exactly why they need separate keys.

## `stack.py` — adjacency resolution

A new module holding **pure geometry, no thermal knowledge**, so it can be tested independently of loads.

```python
resolve_stack(levels, rooms, tolerance_cm=20.0, min_region_ft2=2.0) -> StackResolution
```

Algorithm, per conditioned room, for its bottom face and again for its top face:

1. Order levels by `(elevation, elevationIndex)`. Drop **scaffolding levels** and any level the side-car marks `role: ignore`.

   A scaffolding level is one with no rooms — **but only when some other level in the model does have rooms.** The guard matters: Eldr supports models with no rooms at all (the bounding-box fallback documented in the README, and the shape of the existing test fixture), and without it every level in such a model would be classified as scaffolding, yielding a house with no floor, no ceiling and no volume. So: if the model has no rooms anywhere, the whole stack resolver stands down and the legacy bounding-box path handles the envelope exactly as it does today. Room-bearing models get the new resolution; roomless ones are untouched.
2. Rasterize the room polygon. For each cell, scan outward through the ordered levels (down for the floor face, up for the ceiling face) and take the **first** level carrying a room that covers that cell. Scanning past non-overlapping levels is what makes the garage's vertical overlap harmless.
3. Classify each cell by what it found: a conditioned room → `interior`; an unconditioned level's room → that level's space name; nothing at all → `void`.
4. Separate real exposure from misalignment among the void cells, by a **morphological opening of the void region measured against its own boundary** — erode by `tolerance_cm`, then dilate the surviving core back over the void by the same amount. Whatever the opening deletes is reassigned to the category of the nearest cell that *was* drawn, so no area is lost. See § *What actually shipped* above for why the tolerance is not measured against the room's outline, which is what earlier drafts of this document specified.

   `tolerance_cm` and the raster's `grid_cm` are **coupled** by this: the gap metric reaches one cell further than the tolerance itself, so the width that dissolves completely scales roughly as `2 × (tolerance_cm + one cell)`. That is a rule of thumb for which knob to reach for, not a threshold — the real cutoff is a band, because the metric is discrete on a per-room grid. Grid size is therefore no longer a pure discretization knob; refining it also narrows what counts as an artifact.
5. Aggregate cells into areas per category. Drop any category under `min_region_ft2` and redistribute proportionally — this is what removes the garage's 1.0 sqft artifact. If *every* category for a face falls under the threshold (a room smaller than the threshold itself), keep the largest rather than emitting nothing, so a tiny closet still gets its ceiling.
6. The lowest conditioned level's floor face resolves to `ground` (slab), not `void`.

Undrawn space defaults: a void **below** resolves to the `crawlspace` space, a void **above** to `attic` — the overwhelmingly common cases for a house, both overridable per level. The report warns on void area regardless, so the default is never silent.

Rasterization is an implementation detail chosen for robustness with non-convex polygons; the grid is fine enough that it does not affect reported figures at the tolerances above. Exact polygon clipping is a valid later swap behind the same signature.

## Side-car schema

```yaml
levels:                          # optional — per-level overrides, keyed by SH3D level name
  Main:
    height_ft: 8.0               # override a suspect default; report echoes what was used
    below_void: crawlspace       # what an undrawn space beneath this level means
  basement-main-transition:
    role: ignore                 # belt-and-braces; roomless levels are skipped anyway
  Garage:
    role: unconditioned          # replaces the level-name matching in _unconditioned_level()

spaces:                          # optional — buffer-space temperature policies
  crawlspace:
    winter_temp_f: 32            # observed; derived factor appears in the report
  attic:
    vented: false                # shorthand → factor 0.5
  garage:
    factor: 0.5

cooling:
  attic_temp_f: 130              # optional; overrides the sol-air estimate

assemblies:
  buffer_floor: { u: 0.521 }     # uninsulated floor over the crawl
```

Every block is optional and every existing side-car keeps working unchanged. `role` must be one of `conditioned | unconditioned | ignore`. Note that there is deliberately **no `buffer` role**: buffer-ness is a property of a *space's temperature policy*, not of a level. An unconditioned level simply becomes a named space, and how it is treated thermally is decided in `spaces:`. Collapsing those two concepts into one enum is what makes "is the garage a buffer or is it unconditioned?" an unanswerable question; keeping them separate makes it two independent, answerable ones.

An unknown level name is a **warning**, matching how the `walls` block treats an unknown wall id.

Level names are not unique in SH3D the way wall ids are. A duplicate name is a load-time error naming the collision, rather than a silent first-match.

## Whole-house and per-room unification

Today `geometry.py:487-489` builds the whole-house ceiling and floor from level **bounding boxes**, while `geometry.py:508-511` builds per-room surfaces from room **polygons**. The two disagree, and the bounding box is why the whole-house ceiling misses the 458.8 sqft to attic.

Both move onto `stack.py`, with whole-house surfaces defined as the sum of the per-room resolution. This makes the whole-house and per-room totals reconcile by construction, which is worth an explicit test.

## Conditioned volume

The uncommitted fix already in `geometry.py:405-418` — conditioned-room area × height, skipping unconditioned levels — lands here with the regression test it never got. It belongs in this change because it touches the same volume path the level work does.

The roomless-level fallback in that fix needs one correction: it currently adds bounding-box volume for any level with no rooms, which is precisely the scaffolding case. The transition layer escapes today only because it also has no walls and so never enters `level_extent`. Add a wall to it to model a duct chase and it would inject a bogus 1 ft × footprint of volume. Scaffolding levels must be excluded from volume explicitly, not by accident.

## Reporting

- Per-level echo: name, height used, whether the height came from the model or a side-car override, and the volume contributed.
- Buffer-space echo: each space in play with its resolved factor and, where declared, the temperature it came from.
- **Void warning**: itemized by room and area, phrased as a schematic gap — "184 sqft of conditioned floor has no level drawn beneath it; modeled as buffer floor. Largest: Main Bed 120.1 sqft."
- Surface summary: ceiling area split between interior, attic, and buffer; floor area split between ground, buffer, and exposed.

## Implementation layers

1. **`stack.py`** — new: ordering, scaffolding detection, rasterized adjacency, tolerance and minimum-region filtering. No dependency on side-car or loads.
2. **`spaces.py`** — new: buffer-space policy resolution (temperature → factor precedence), shared by heating and cooling.
3. **`sidecar.py`** — parse and validate `levels` and `spaces`; `cooling.attic_temp_f`; the new assembly keys with their fallbacks.
4. **`geometry.py`** — call the resolver for both whole-house and per-room surfaces; fix the volume path's scaffolding handling; retire the bounding-box ceiling/floor.
5. **`loads.py`** — `buffer_floor` / `exposed_floor` in the ΔT resolvers; ceiling ΔT sourced from the attic policy rather than outdoor; assembly fallback chain.
6. **`report.py`** / **`overview.py`** — the echoes and the void warning.
7. **`jsonexport.py`** — surface splits and space policies in the structured output.
8. **README + `example-sidecar.yaml`** — the new blocks.

## Testing

TDD throughout, following the existing suite's shape. The cases that matter:

- **Ordering:** two levels sharing an elevation resolve by `elevationIndex`.
- **Scaffolding:** a roomless level is skipped for adjacency *and* volume — including the variant that carries a wall, which is the case that currently leaks.
- **Roomless model:** a model with no rooms anywhere keeps the legacy bounding-box envelope unchanged. This is a regression guard on existing behavior, not new capability.
- **Vertical overlap is not adjacency:** a garage-shaped level overlapping a conditioned level's vertical span but not its footprint contributes nothing.
- **Area split:** one room over two different levels splits proportionally (the Kitchen shape).
- **Interior suppression:** conditioned over conditioned emits no surface.
- **Void tolerance:** a wall-thickness sliver is discarded; an interior blob survives and is reported. Bracketed from *both* sides — a gap just inside the tolerance must dissolve and one just outside it must survive — because `TOLERANCE_CM` and `GRID_CM` are captured as default arguments of `resolve_faces` and have to be read off the module and passed explicitly for a test to bind to the shipped values at all.
- **Face normalization:** each face sums to the room's own polygon area, on a fixture whose raster area *cannot* equal it. An axis-aligned rectangle is useless here: `resolve_faces` divides each room's bbox into a whole number of cells, so a rectangle's raster area equals its shoelace area exactly and the normalization step is a no-op on it.
- **Minimum region:** a 1 sqft resolved region is dropped and redistributed.
- **Partial ceiling:** a room half-covered by the level above gets ceiling for the uncovered half only.
- **Multi-level volume:** the regression test for the conditioned-volume fix.
- **Whole-house equals sum of per-room** for every horizontal surface.
- **Policy precedence:** temperature beats factor beats vented beats default; derived factor is correct.
- **Attic:** `attic_temp_f` overrides sol-air; heating uses the vented policy.

Integration-level, the Refrhus figures above are the acceptance targets: 458.8 sqft of ceiling to attic, 122.1 sqft of drawn crawl floor, and ~110 sqft of surviving Main Bed void. The fourth original target — the Kitchen void visible as a schematic gap rather than absorbed into the load — was **dropped**, not met: see § *What actually shipped* for the algorithm change that cost it and why that trade was taken.

## Corroboration worth keeping

The floor line is the largest single gap against the professional Manual J, and it is worth recording that the residual is traceable to *inputs*, not to the engine. Taking the professionals' own measured **U-0.521** for the uninsulated floor over the crawl, their implied full outdoor ΔT (55 °F, i.e. treating the crawl as outdoor air rather than a buffer), and the resolver's 293.5 sqft of crawl-facing floor:

```
0.521 × 293.5 × 55 = 8,410 BTU/hr    vs. their 8,608 BTU/hr floor line
```

Within 2.3% on an area Eldr resolved from the model independently of their takeoff. So the gap between Eldr's floor line and theirs is two side-car inputs — `assemblies.buffer_floor` (currently borrowing the slab's U-0.05) and the crawlspace's temperature policy (currently 0.5 of ΔT rather than their implied 1.0) — and not a defect in the resolution. Setting those two makes the lines meet; the reason they are not set is that the owner's own observation of the crawl disagrees with the professionals' assumption, which is a modeling decision, not an arithmetic one.

## Scope / deferred

- **Above-face misalignment settling.** The below-face void settling has no above-face counterpart, so ceiling cells resolve against whatever is drawn above with no artifact handling at all. On Refrhus that leaves roughly 19 sqft of impossible basement-room ceiling inside the ~989 sqft of ceiling reported. Deliberately not fixed in this cut: it is worth about **14 BTU/hr**, and a resolver change at this point costs more risk than it buys. The shape of the fix is to run the same opening on the above face, which means teaching `resolve_faces` to raster the above face into cells rather than accumulating areas directly.
- **The pre-opening void area, reported alongside the post-opening one.** The morphological opening that removed the resolver's directional bias also removed the small-scatter end of the void itemization — the Kitchen regression signal this document originally promised (see § *What actually shipped*). Reporting both the raw and the settled void area per room restores the signal without touching the algorithm: the raw figure is what trends to zero as the walls are aligned, and the settled figure stays the one that drives load. Reporting change, not an engine change.
- **A level with conditioned rooms but no walls contributes 0 ft³.** The volume loop is keyed on `walls_by_level`, so such a level is never visited: it reads "0 ft³" in the *Level heights* table, indistinguishable there from a level that legitimately holds no conditioned rooms, and it silently drops its share of the infiltration term as well (infiltration is sized on that same volume). Pre-existing, made visible rather than caused by this cut. The fix is to drive the volume loop off the levels themselves and use walls only for the roomless bounding-box fallback.
- **No packaging metadata and no `ws` run adapter.** The component has no `pyproject.toml`/`setup.py` and no `ws run eldr`, so running an analysis on the real model needs an explicit `PYTHONPATH` pointing at the component root. The cost is not inconvenience: **it is why a broken primary path went unnoticed for five tasks.** The only routine execution of this engine is its unit tests, so nothing exercises `cli.analyze` end to end unless a human remembers the incantation. Packaging metadata plus a `ws` adapter that runs the real model against `hoards/refrhus/` would make a real-model run the cheap default it should be.
- **Scaffolding walls are still billed to the envelope.** Roomless levels are excluded from the stack and from volume, but the `continue` that drops them sits *after* the wall loop, so their walls still contribute `exterior_wall` area. A duct chase drawn with walls therefore adds envelope wall area while adding no volume — the two halves of the scaffolding exclusion disagree. Refrhus's transition level has no walls, which is the only reason this is invisible today; it is exactly the case the `_SCAFFOLD_WARN_FT2` warning exists to announce.
- **`loads.ATTIC_SPACE` and `stack.resolve_faces`'s `above_void="attic"` default are independent literals.** Both spell `"attic"`, and the hot-attic cooling substitution only fires when they agree — change one and every ceiling silently drops from the sol-air temperature to the bare unvented 0.5, with no error and no failing test. Nothing pins their agreement. Either share one constant or add a test that asserts a default-resolved ceiling gets the attic treatment.
- **`void_categories`' mixed-envelope limitation belongs in the README.** It is recorded in `report.void_categories`' docstring and now in README § *Known limitations of the level stack*; the underlying fix — carrying the resolved category on the void itself, so the report attributes per void rather than per envelope — is the deferred work.
- **`geometry.extract_envelope` wants breaking up.** Over 300 lines with four nested closures over the enclosing scope, doing level parsing, wall categorization, opening attribution, per-room attribution, volume accumulation and stack resolution in one body. A real finding at the wrong moment: this branch is nine tasks deep and the function is the highest-traffic code in the component, so a structural refactor here would be reviewed as part of a change that is about something else. Worth doing as its own cut, with the existing tests as the harness.
- **Full ACCA CLTD/CLF** surface treatment. The policy interface is the seam it lands behind; this cut stays factor-based.
- **A real attic energy balance** (roof area, ventilation rate, radiant barrier). Sol-air or an explicit temperature for now.
- **Orientation-aware sol-air.** The estimate applies one flat solar uplift regardless of which way the roof planes face, so a north-facing slope and a south-facing one produce the same attic temperature. Weighting the uplift by roof-plane bearing is the same shape of change as the PV shading below — both are a fraction-of-roof-area weighting — and both want roof-plane geometry, so they belong together with sloped-roof support. Windows are already orientation-resolved to the exact degree; the roof is not, and the doc claimed otherwise until this was corrected.
- **Partial roof shading from PV panels.** Refrhus carries solar panels over some roof sections. Panels shade the deck beneath them, so a shaded section runs materially cooler than exposed shingle and the attic below sees a *blended* sol-air, not one uniform value — the effect is a fraction-of-roof-area weighting, in the same family as the partial-height wall overlap deferred in the wall-boundary design. The single `attic` space with one policy cannot express it. Two escape hatches exist meanwhile: set `cooling.attic_temp_f` to a blended observed value, or, once the roof is modeled with enough fidelity, split the attic into more than one named space. Wants roof-plane geometry, so it belongs with sloped-roof support rather than this cut.
- **The "devil's triangle"** — the unmodeled volume above the centre of the 2nd floor. Once drawn as its own level, the resolver picks it up with no engine change; until then it is attic by default.
- **Sloped-roof geometry** for the knee-wall attic. Treated as flat ceiling to attic.
- **`<property>` tags read from the model.** The owner's preferred long-term interface is tagging spaces in SH3D directly. Stock SH3D exposes no UI for custom properties — the only `<property>` elements in the model are SweetHome3D's own window-geometry settings — so tagging today means *names*, which is what `levels` and `spaces` key off. When Eldr becomes an `.sh3p` plugin, the plugin can write real properties and they become an additional, higher-precedence source behind the same policy interface.
- **Per-room conditioning.** Conditioning stays per level; a mixed-conditioning level needs a larger change.
- **Buffer openings** — unchanged from the wall-boundary design; a window in a buffer wall still uses full outdoor ΔT.
