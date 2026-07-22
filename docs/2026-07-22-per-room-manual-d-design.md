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
- Parse `<room>` elements: `name`, `level`, polygon (`<point>` list) → `Room(name, level_id, area_ft2, centroid_cm, surfaces, volume_ft3, windows_by_bearing)`.
- **Attribute** each exterior/basement wall to the room whose polygon edge is nearest the wall midpoint (exterior walls border exactly one room, interior side). Windows → their host wall's room.
- **Ceiling/floor per room:** a top-level room gets `ceiling = its area`; a bottom-level room gets `floor = its area` (mirrors the single-zone model, which only counts top ceiling + bottom floor).
- **Infiltration per room:** proportional to room volume (`area × level height`).
- `Envelope.rooms: list[Room]` (empty when the model has no rooms → callers fall back to whole-house / hand-listed runs).

### 2. Loads — per-room (`loads.py`)
- Refactor the conduction + infiltration core to operate on `(surfaces, volume)`; whole-house and per-room both call it.
- `per_room_loads(env, sc) -> list[RoomLoad]` with `RoomLoad(name, heating_btuh, cooling_btuh, cfm)`; per-room design CFM = `max(heating, cooling-sensible) / (1.08 × supply ΔT)` (mirrors whole-house sizing basis).

### 3. Ducts from the model (`ductd.py` + `cli.py`)
- Find the unit furniture item; compute per-room TEL; derive the friction rate (or fall back).
- Build the run list from the rooms (name, CFM, length) and size each duct (existing `size_ducts`, extended to carry length + per-run pressure drop = `FR × TEL/100`).

### 4. Report
- A **per-room table** (the Manual J 1c section: room, heating, cooling, CFM).
- Manual D gains a **length** column and notes the derived friction rate + unit location.

## Scope / honesty
- Wall→room attribution is a geometric heuristic (nearest polygon edge); ambiguous cases (a room with no clear exterior wall, or overlapping polygons) fall back gracefully. Demo-grade.
- TEL uses a fitting fudge factor, not true fitting equivalent lengths. Noted in the report.
- Rooms with zero attributed exterior surface still get infiltration + (if applicable) ceiling/floor, so interior rooms get a small but non-zero load.
- Backward compatible: no rooms in the model, or no unit placed → the existing whole-house + hand-listed-runs paths still work.
