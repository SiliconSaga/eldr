# Eldr per-room loads (Manual J 1c) + Manual D from the model — design

**Status:** Built 2026-07-21 (owner chose load-based per-room). Extends the standalone Manual D engine to read the SH3D model's rooms + unit location. All four layers landed; 103 tests green; verified end-to-end on the real Refrhus model.

## Goal

Read the SH3D model's **rooms** and a placed **air-handler unit**, compute each room's **own** heating/cooling load (proper per-room Manual J = "1c"), size its design CFM, and feed those per-room CFM + unit→room run lengths into Manual D — so the duct sizing reflects the actual house instead of a hand-listed run table.

## Decisions (owner-approved)

- **Per-room CFM = load-based** (not area-proportional): each room's load is computed from the exterior walls + windows + ceiling/floor that belong to *it*, so an exposed/glassy room gets more air than an interior room of equal size.
- **Unit location** comes from a furniture item whose name contains "air handler" (override via `ducts.unit_name`). Its (x, y, level) is the duct origin.
- **Run length** = unit → room centroid, Manhattan horizontal + vertical (level elevation delta), × a fitting factor → total effective length (TEL). The longest TEL + available static pressure **derive** the friction rate (`FR = ASP × 100 / TEL_ft`); falls back to the side-car `friction_rate` if no unit / no ASP.

## Architecture (layers, each tested)

### 1. Geometry — rooms + surface attribution (`geometry.py`)
- Parse `<room>` elements: `name`, `level`, polygon (`<point>` list) → `Room(name, level_id, area_ft2, centroid_cm, conditioned, surfaces, volume_ft3, windows_by_bearing)`. Degenerate (zero-area) polygons are skipped.
- **Exterior classification (polygon outline):** a wall is on the thermal envelope if it borders a room on **exactly one side** — tested by probing just past each wall face and checking the room polygons. This follows the real footprint outline, so perimeter walls on an extension/wing are caught even when they sit inside the level's bounding rectangle. Levels with no rooms fall back to the bounding-box edge test.
- **Per-room wall attribution (area split):** each envelope wall's gross area is split among the rooms it runs behind by sampling points along it (density-based, ~1 per 25 cm with a floor count, so a narrow room on a long facade isn't missed) and assigning each sample to the nearest room. Windows/doors are attributed to the nearest room by position.
- **Ceiling/floor per room:** a top-level room gets `ceiling = its area`; a bottom-level room gets `floor = its area` (mirrors the single-zone model, which only counts top ceiling + bottom floor).
- **Infiltration per room:** proportional to room volume (`area × level height`).
- Rooms on garage/crawlspace levels are flagged `conditioned = False`.
- `Envelope.rooms: list[Room]` (empty when the model has no rooms → callers fall back to whole-house / hand-listed runs), plus `furniture` and `level_elevations` for the duct layer.

### 2. Loads — per-room (`loads.py`)
- Refactor the conduction core to take a per-category ΔT resolver; whole-house and per-room both call it.
- **Ground-coupled below-grade surfaces:** `basement_wall` and `floor` see the ground ΔT (`indoor − ground_temp`, default 50 °F), not the outdoor-air ΔT — a below-grade wall against ~50 °F soil loses far less than one against 15 °F air. In cooling the soil is a sink, so those surfaces add zero gain.
- `per_room_loads(env, sc) -> list[RoomLoad]` with `RoomLoad(name, level_id, conditioned, heating_btuh, cooling_btuh, cfm)`; per-room design CFM = `max(heating_cfm, cooling_cfm)`, each airflow computed with its own supply-air ΔT (heating rise vs cooling 20 °F), so the duct is sized for the worse mode.

### 3. Ducts from the model (`ductd.py` + `cli.py`)
- Find the unit furniture item; compute per-room TEL; derive the friction rate (or fall back).
- Build the run list from the rooms (name, CFM, length) and size each duct (existing `size_ducts`, extended to carry length + per-run pressure drop = `FR × TEL/100`).

### 4. Report
- A **per-room table** (the Manual J 1c section: room, heating, cooling, CFM).
- Manual D gains a **length** column and notes the derived friction rate + unit location.

## Scope / honesty
- **Exterior classification depends on rooms being drawn.** The polygon-outline test treats "no room on this side" as outdoors. Interior space not yet drawn as a room (halls, stairs, a future walled-up utility void) makes its bordering walls read as exterior → the load can *over*-count there, the mirror of the old bounding-box *under*-count. It self-corrects as those spaces get drawn (then the wall has a room on both sides → interior). Uses the wall midpoint, so a wall partly interior / partly exterior is classified whole.
- Below-grade coupling is coarse: one ground temperature for all below-grade surfaces, and it doesn't yet distinguish a floor over the warm basement from an extension floor over crawlspace/slab (see the follow-up on zone-aware bottom boundaries). Interior masonry mass (e.g. a chimney) is not modeled.
- Per-room wall attribution is a density-sampled nearest-room split; a room with no bordering envelope wall still gets infiltration + (if applicable) ceiling/floor, so interior rooms get a small but non-zero load.
- TEL uses a fitting fudge factor, not true fitting equivalent lengths. Noted in the report.
- Backward compatible: no rooms in the model, or no unit placed → the existing whole-house + hand-listed-runs paths still work.
