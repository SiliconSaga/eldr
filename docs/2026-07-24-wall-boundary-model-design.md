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
| `buffer` | **BUFFER_FACTOR × outdoor ΔT** | **BUFFER_FACTOR × cooling ΔT** | `buffer_wall` → borrows `exterior_wall`'s U if unset, with a warning (see below) |
| `interior` | — (excluded from envelope) | — | — |

Inference (untagged walls) can only ever yield `exterior`, `ground`, or excluded. **`buffer` comes only from an explicit tag.**

**Amended by the level-stack cut (2026-08-13):** the U-value fallback is no longer a plain convenience. Every borrow now *announces itself* — a runtime warning naming both categories and how far off that particular pairing can be, plus a *Borrowed assembly U-values* block in the report listing the recipient, the area involved, the U used and the donor. The chain also grew: `buffer_floor → exposed_floor → floor` and `exposed_floor → floor` joined `buffer_wall → exterior_wall`. The reason is that `floor` in a typical side-car is a slab's *effective whole-area* U (the real loss is perimeter-edge, so the number is deliberately tiny) while the borrowers are genuine framed assemblies — an order of magnitude apart. A borrow turns a loud failure into a confident wrong number, so the disclosure is what keeps it usable. `buffer_wall → exterior_wall` stays the mild case (same construction, different boundary) and the warning says so rather than crying wolf.

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

- A single named constant, `BUFFER_FACTOR = 0.5` (a garage floats ~midway between indoor and outdoor), with a loud comment. Demo-grade; a later knob can make it per-space.
- The **report echoes it** whenever any buffer wall exists: e.g. "_Buffer walls (garage/crawl-adjacent): 3, at 50% of design ΔT._" — so the assumption is never silent.
- Documented in the README and the example side-car.

## Discovery helper — `eldr <model> [<sidecar>] --walls`

SH3D doesn't surface wall UUIDs, so tagging by id needs tooling. The `--walls` flag lists every wall with its **resolved** boundary (a side-car tag if present, else the inferred value). Pass the side-car too to see current tags reflected:

```text
| id                 | level | endpoints (ft)          | length  | boundary          |
| wall-ee233fd-...    | Main  | (23.2, 27.0) → (23.2, 42.9) | 15.9 ft | exterior          |
| wall-7a1b-...       | Main  | (10.0, 5.0) → (18.0, 5.0)   | 8.0 ft  | **buffer** (tagged) |
```

You scan it, copy the ids of the garage-adjacent walls into the side-car, and tag them `buffer`. The **boundary** column shows what Eldr resolved, so you only override what's wrong or unseeable.

## Partial walls

- **Length variation** (a wall runs past the garage in plan): split it into segments **in SH3D** and tag each. No Eldr code — geometry lives in the model; Eldr keeps one boundary per wall surface.
- **Height / cross-level partial overlap** (garage backs only the lower ~3.6 ft of a Main-floor wall): **out of this cut.** Use a single dominant tag for now; a fractional/height split is the noted follow-up.

## Implementation layers

1. **sidecar.py** — parse + validate the `walls` map → `SideCar.wall_boundaries: dict[str, str]`.
2. **geometry.py** — `extract_envelope(home_path, wall_boundaries=None)`: apply the tag→category precedence above; keep the inference fallback and the area-split attribution. (Geometry gains only a small lookup, not a side-car dependency.)
3. **loads.py** — `buffer_wall` in the ΔT resolvers (`BUFFER_FACTOR` × the mode's ΔT) and a `buffer_wall → exterior_wall` U fallback in `_conduction`. (Since the level-stack cut this fallback is a *disclosed borrow*, resolved once in `loads.assembly_borrow` and shared by the warning and the report block — see the amendment above.)
4. **cli.py** — pass `sc.wall_boundaries` into `extract_envelope`; add the `--walls` flag (the existing `eldr <home> <sidecar>` report path stays).
5. **report.py** — the prominent buffer-factor line when buffer walls are present.
6. **docs / example-sidecar** — the `walls` block + buffer-factor notes.

## Scope / deferred

- Height/area **fractional splits** within one wall (the garage's partial-height overlap).
- ~~**Per-buffer-space temperatures** (garage vs. vented crawl vs. attic) — one factor for now.~~ **Delivered** by the level-stack cut: `spaces.py` and the side-car `spaces:` block. Horizontal surfaces carry the space they face and get its own policy; a tagged `buffer_wall` still carries no space name and keeps the flat `BUFFER_FACTOR`.
- **Per-wall U overrides** in the tag (use the category's assembly U for now).
- **Buffer openings** — a window/door *in* a buffer wall still uses the opening's own
  ΔT (full outdoor) and, for glazing, solar gain. The opaque `buffer_wall` gets the
  buffer factor; its openings don't yet inherit it (they'd also need solar suppressed
  for a window into a garage). The realistic case is the house↔garage door; small-area,
  and buffer walls are tag-only/rare — so this is a follow-up, not a blocker.

## Added mid-build: deterministic overview generator

Folded in alongside this feature (owner request): a reproducible replacement for the
hand-authored demo-overview doc. `eldr <home> <sidecar> --overview` (and
`overview.render_overview`) assembles the full narrative — ACCA-chain intro, "how
detailed it gets", the engine's own report body (identical numbers, via the shared
`cli.analyze`), auto-selected honesty caveats (buffer walls present? no unit? nearest
station?), an ACCA-compliance path, and a roadmap. Only the framing prose is templated;
every figure comes from the pipeline, so the write-up can't drift from the engine. The
old hand-made `docs/2026-07-22-demo-overview.md` is retired.

`cli.analyze()` was extracted as the single pipeline both the report and the overview
render from.

## Future (beyond Eldr — plugin era)

- **SH3D wall overlay:** a display mode coloring walls by boundary condition (or random colors to reveal where segments split). No such option exists in SH3D today; it would be a plugin-side feature, landing well after Eldr becomes a full SH3D plugin. Logged here so the idea isn't lost.
