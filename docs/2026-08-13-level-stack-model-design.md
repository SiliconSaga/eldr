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

Voids cannot be promoted to surfaces silently — the schematic is a work in progress, and inventing load from unfinished geometry would be worse than under-counting. Eroding the void region away from the room's own outline separates the two failure modes:

| Main room | void @0cm | @10cm | @20cm | @30cm | @45cm |
|---|---:|---:|---:|---:|---:|
| Living room | 36.8 | 32.7 | 27.7 | 22.5 | 15.0 |
| Kitchen | 17.5 | 14.3 | 12.8 | 11.5 | 10.8 |
| Main Bed | 117.2 | 108.4 | 97.2 | 88.3 | 75.7 |
| Main Bath | 2.5 | 1.0 | 0.0 | 0.0 | 0.0 |
| Kids Room / closets / unnamed | ≤2.4 | ≤0.6 | 0.0 | 0.0 | 0.0 |

A 20 cm tolerance erases the genuine slivers — Main Bath, the unnamed fragments, every closet — because misalignment artifacts are thin bands roughly a wall-thickness wide hugging the room outline. But the Kitchen keeps 12.8 sqft even at 45 cm: that is not an edge band, it is an interior region where no basement or crawl polygon covers the Kitchen footprint. Erosion handles wall-thickness noise; it does not handle not-yet-tiled gaps.

Therefore: **erosion removes sliver noise, surviving voids become buffer floor, and every surviving void is itemized by room and area in the report.** A bogus 12.8 sqft Kitchen void stays visible and fixable rather than being quietly priced in. The Kitchen doubles as the regression signal — it should trend to ~0 as the walls are aligned, and the report says when it gets there.

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
4. Discard void cells within `tolerance_cm` of the room's own outline as misalignment.
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
- **Void tolerance:** a wall-thickness sliver is discarded; an interior blob survives and is reported.
- **Minimum region:** a 1 sqft resolved region is dropped and redistributed.
- **Partial ceiling:** a room half-covered by the level above gets ceiling for the uncovered half only.
- **Multi-level volume:** the regression test for the conditioned-volume fix.
- **Whole-house equals sum of per-room** for every horizontal surface.
- **Policy precedence:** temperature beats factor beats vented beats default; derived factor is correct.
- **Attic:** `attic_temp_f` overrides sol-air; heating uses the vented policy.

Integration-level, the Refrhus figures above are the acceptance targets: 458.8 sqft of ceiling to attic, 122.1 sqft of drawn crawl floor, ~120 sqft of surviving Main Bed void, and the Kitchen void visible as a schematic gap rather than absorbed into the load.

## Scope / deferred

- **Full ACCA CLTD/CLF** surface treatment. The policy interface is the seam it lands behind; this cut stays factor-based.
- **A real attic energy balance** (roof area, ventilation rate, radiant barrier). Sol-air or an explicit temperature for now.
- **Orientation-aware sol-air.** The estimate applies one flat solar uplift regardless of which way the roof planes face, so a north-facing slope and a south-facing one produce the same attic temperature. Weighting the uplift by roof-plane bearing is the same shape of change as the PV shading below — both are a fraction-of-roof-area weighting — and both want roof-plane geometry, so they belong together with sloped-roof support. Windows are already orientation-resolved to the exact degree; the roof is not, and the doc claimed otherwise until this was corrected.
- **Partial roof shading from PV panels.** Refrhus carries solar panels over some roof sections. Panels shade the deck beneath them, so a shaded section runs materially cooler than exposed shingle and the attic below sees a *blended* sol-air, not one uniform value — the effect is a fraction-of-roof-area weighting, in the same family as the partial-height wall overlap deferred in the wall-boundary design. The single `attic` space with one policy cannot express it. Two escape hatches exist meanwhile: set `cooling.attic_temp_f` to a blended observed value, or, once the roof is modeled with enough fidelity, split the attic into more than one named space. Wants roof-plane geometry, so it belongs with sloped-roof support rather than this cut.
- **The "devil's triangle"** — the unmodeled volume above the centre of the 2nd floor. Once drawn as its own level, the resolver picks it up with no engine change; until then it is attic by default.
- **Sloped-roof geometry** for the knee-wall attic. Treated as flat ceiling to attic.
- **`<property>` tags read from the model.** The owner's preferred long-term interface is tagging spaces in SH3D directly. Stock SH3D exposes no UI for custom properties — the only `<property>` elements in the model are SweetHome3D's own window-geometry settings — so tagging today means *names*, which is what `levels` and `spaces` key off. When Eldr becomes an `.sh3p` plugin, the plugin can write real properties and they become an additional, higher-precedence source behind the same policy interface.
- **Per-room conditioning.** Conditioning stays per level; a mixed-conditioning level needs a larger change.
- **Buffer openings** — unchanged from the wall-boundary design; a window in a buffer wall still uses full outdoor ΔT.
