# Eldr per-wall boundary conditions — design

**Status:** Approved (design) 2026-07-24, pending spec review → implementation plan. Replaces geometry-only boundary *inference* with an explicit, per-wall *source of truth*, with inference as the fallback.

## Goal

Make each wall's thermal boundary a **stated property**, not something re-derived by probing room polygons at classification time. This fixes the brittleness of geometric inference and — critically — lets us express a **buffer** boundary (a wall between conditioned space and a garage / crawlspace / attic), which inference cannot produce because it's often a cross-level, partially-overlapping adjacency the per-level room probe can't see.

Concrete driver: the Refrhus living-room west wall backs the (split-level) garage over part of its length and height, and open air over the rest. No room-adjacency test can classify that; an explicit tag can.

## Boundary vocabulary → treatment

Each wall may carry one boundary. Mapping to the existing ΔT/U machinery:

| Boundary | Heating ΔT | Cooling ΔT | U-category |
|---|---|---|---|
| `exterior` | outdoor ΔT | outdoor ΔT | `exterior_wall` |
| `ground` | ground ΔT (soil) | max(0, ground − indoor) — 0 unless soil is warmer than setpoint | `basement_wall` |
| `buffer` | **BUFFER_FACTOR × outdoor ΔT** | **BUFFER_FACTOR × cooling ΔT** | `buffer_wall` → falls back to `exterior_wall` U if unset |
| `interior` | — (excluded from envelope) | — | — |

Inference (untagged walls) can only ever yield `exterior`, `ground`, or excluded. **`buffer` comes only from an explicit tag.**

## Precedence: tag first, inference fallback

Per envelope wall, during classification:

1. If the wall id is in the side-car `walls` map → use that boundary. `interior` → drop; otherwise include with the mapped category. (A tag can force a wall *into* the envelope that inference would have dropped, or *out* of it.)
2. Else → today's conditioned-aware inference (a conditioned room on exactly one side → `exterior`/`ground`; else drop).

Per-room area attribution (nearest conditioned room, density-sampled) is unchanged — a buffer wall attributes to the conditioned room it borders.

## Side-car schema

```yaml
walls:                          # optional — explicit per-wall boundary overrides
  wall-ee233fd-...: { boundary: buffer }     # living-room W, backs the garage
  wall-7a1b-...:    { boundary: exterior }   # correct a misinferred wall
  wall-9c02-...:    { boundary: interior }   # force-exclude
```

- Keyed by the SH3D wall id (stable UUID; diffable; the model file is never touched).
- `boundary` must be one of `exterior | ground | buffer | interior`; anything else is a clear load-time error.
- A tag whose wall id isn't in the model → a **warning** (catches typos and walls that were redrawn under a new id), not a hard error.

## Buffer factor — prominent by design

- A single named constant, `BUFFER_DELTA_FRACTION = 0.5` (a garage floats ~midway between indoor and outdoor), with a loud comment. Demo-grade; a later knob can make it per-space.
- The **report echoes it** whenever any buffer wall exists: e.g. "_Buffer walls (garage/crawl-adjacent): 3, at 50% of design ΔT._" — so the assumption is never silent.
- Documented in the README and the example side-car.

## Discovery helper — `eldr walls <model>`

SH3D doesn't surface wall UUIDs, so tagging by id needs tooling. Add a subcommand that lists every wall:

```
id                level      endpoints (ft)          length   inferred
wall-ee233fd-...  Main       (23.2,27.0)-(23.2,42.9)  15.9 ft  exterior
...
```

You scan it, copy the ids of the garage-adjacent walls into the side-car, and tag them `buffer`. Shows the **inferred** boundary so you only override what's wrong or unseeable.

## Partial walls

- **Length variation** (a wall runs past the garage in plan): split it into segments **in SH3D** and tag each. No Eldr code — geometry lives in the model; Eldr keeps one boundary per wall surface.
- **Height / cross-level partial overlap** (garage backs only the lower ~3.6 ft of a Main-floor wall): **out of this cut.** Use a single dominant tag for now; a fractional/height split is the noted follow-up.

## Implementation layers

1. **sidecar.py** — parse + validate the `walls` map → `SideCar.wall_boundaries: dict[str, str]`.
2. **geometry.py** — `extract_envelope(home_path, wall_boundaries=None)`: apply the tag→category precedence above; keep the inference fallback and the area-split attribution. (Geometry gains only a small lookup, not a side-car dependency.)
3. **loads.py** — `buffer_wall` in the ΔT resolvers (`BUFFER_DELTA_FRACTION` × the mode's ΔT) and a `buffer_wall → exterior_wall` U fallback in `_conduction`.
4. **cli.py** — pass `sc.wall_boundaries` into `extract_envelope`; add the `walls` subcommand (argparse subparsers; the existing `eldr <home> <sidecar>` report path stays).
5. **report.py** — the prominent buffer-factor line when buffer walls are present.
6. **docs / example-sidecar** — the `walls` block + buffer-factor notes.

## Scope / deferred

- Height/area **fractional splits** within one wall (the garage's partial-height overlap).
- **Per-buffer-space temperatures** (garage vs. vented crawl vs. attic) — one factor for now.
- **Per-wall U overrides** in the tag (use the category's assembly U for now).

## Future (beyond Eldr — plugin era)

- **SH3D wall overlay:** a display mode coloring walls by boundary condition (or random colors to reveal where segments split). No such option exists in SH3D today; it would be a plugin-side feature, landing well after Eldr becomes a full SH3D plugin. Logged here so the idea isn't lost.
