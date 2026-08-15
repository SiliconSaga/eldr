# Eldr Level-Stack Surface Model Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resolve each conditioned room's floor and ceiling against what actually sits above and below it, and give every buffer space its own temperature policy, so the horizontal envelope stops being "ceiling on the top level, floor on the bottom level, both from bounding boxes."

**Architecture:** Two new pure modules — `stack.py` (geometry: ordering, scaffolding detection, rasterized XY adjacency with tolerance filtering) and `spaces.py` (thermal: buffer-space temperature policies). `geometry.py` calls the resolver for both whole-house and per-room surfaces; `loads.py` reads per-surface space identity to pick a ΔT. Everything is additive to the side-car: an existing side-car produces the same numbers except where the design deliberately changes them.

**Tech Stack:** Python 3.13, pytest, PyYAML, defusedxml. No new dependencies — rasterization is hand-rolled to avoid adding shapely.

**Spec:** [`2026-08-13-level-stack-model-design.md`](2026-08-13-level-stack-model-design.md)

## Global Constraints

- Run tests with `ws test eldr` from the workspace root — never raw `pytest` (the component has its own `.venv`).
- Commit with `ws commit eldr .commits/<name>.md` using a bodyfile — never raw `git add` / `git commit`.
- Prose in Markdown is never hard-wrapped: one line per paragraph and per bullet.
- No new third-party dependencies.
- Every side-car block added here is **optional**; an existing side-car must keep loading unchanged.
- Units: SH3D plan coordinates are centimetres; loads are in ft², ft³ and BTU/hr. Convert via `eldr.units`.
- Erosion tolerance default `20.0` cm; minimum resolved region default `2.0` ft²; raster grid default `15.0` cm. These are module constants, not magic numbers at call sites.
- `BUFFER_FACTOR = 0.5` in `loads.py` stays the *unvented default*; it moves behind the policy layer rather than being deleted.

---

### Task 1: Conditioned-volume fix + multi-level regression test

The fix is already applied uncommitted in `eldr/geometry.py:405-418` from the 2026-08-12 validation session; it has never had a test. Land it first, on its own, so the rest of the work builds on a committed baseline.

**Files:**
- Modify: `eldr/geometry.py:405-418` (already edited in the working tree — verify it matches below)
- Test: `eldr/tests/test_geometry.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Envelope.volume_ft3` counts conditioned room area × level height only. Task 5 extends the same code path to exclude scaffolding levels.

- [ ] **Step 1: Write the failing test**

Add to `eldr/tests/test_geometry.py`:

```python
MULTI_LEVEL_FIXTURE = textwrap.dedent("""\
<?xml version='1.0'?>
<home version='7400' name='t' wallHeight='300'>
  <level id='LB' name='Basement' elevation='0.0' floorThickness='12.0' height='200' elevationIndex='0'/>
  <level id='LG' name='Garage' elevation='0.0' floorThickness='12.0' height='300' elevationIndex='1'/>
  <level id='LM' name='Main' elevation='212.0' floorThickness='12.0' height='250' elevationIndex='0'/>
  <wall id='b-n' level='LB' xStart='0' yStart='0' xEnd='400' yEnd='0' height='200' thickness='10'/>
  <wall id='b-s' level='LB' xStart='0' yStart='300' xEnd='400' yEnd='300' height='200' thickness='10'/>
  <wall id='b-w' level='LB' xStart='0' yStart='0' xEnd='0' yEnd='300' height='200' thickness='10'/>
  <wall id='b-e' level='LB' xStart='400' yStart='0' xEnd='400' yEnd='300' height='200' thickness='10'/>
  <wall id='g-n' level='LG' xStart='500' yStart='0' xEnd='900' yEnd='0' height='300' thickness='10'/>
  <wall id='g-s' level='LG' xStart='500' yStart='300' xEnd='900' yEnd='300' height='300' thickness='10'/>
  <wall id='g-w' level='LG' xStart='500' yStart='0' xEnd='500' yEnd='300' height='300' thickness='10'/>
  <wall id='g-e' level='LG' xStart='900' yStart='0' xEnd='900' yEnd='300' height='300' thickness='10'/>
  <wall id='m-n' level='LM' xStart='0' yStart='0' xEnd='400' yEnd='0' height='250' thickness='10'/>
  <wall id='m-s' level='LM' xStart='0' yStart='300' xEnd='400' yEnd='300' height='250' thickness='10'/>
  <wall id='m-w' level='LM' xStart='0' yStart='0' xEnd='0' yEnd='300' height='250' thickness='10'/>
  <wall id='m-e' level='LM' xStart='400' yStart='0' xEnd='400' yEnd='300' height='250' thickness='10'/>
  <room id='rb' level='LB' name='Basement Room'>
    <point x='0' y='0'/><point x='400' y='0'/><point x='400' y='300'/><point x='0' y='300'/>
  </room>
  <room id='rg' level='LG' name='Garage'>
    <point x='500' y='0'/><point x='900' y='0'/><point x='900' y='300'/><point x='500' y='300'/>
  </room>
  <room id='rm' level='LM' name='Living room'>
    <point x='0' y='0'/><point x='400' y='0'/><point x='400' y='300'/><point x='0' y='300'/>
  </room>
</home>
""")


def test_volume_counts_conditioned_rooms_only(tmp_path):
    """The garage level must not inflate the infiltration volume.

    Before the fix, volume came from each level's wall bounding box, so the
    garage (a whole extra 400x300 footprint at 300cm) was counted as if it were
    conditioned space the air leaks into -- roughly doubling infiltration.
    """
    p = tmp_path / "Home.xml"
    p.write_text(MULTI_LEVEL_FIXTURE)
    env = geometry.extract_envelope(str(p))
    from eldr import units
    expected = (units.sqcm_to_sqft(400 * 300) * units.cm_to_ft(200)      # basement
                + units.sqcm_to_sqft(400 * 300) * units.cm_to_ft(250))   # main
    assert abs(env.volume_ft3 - expected) < 1e-6
```

- [ ] **Step 2: Run the test to verify it fails if the fix is reverted**

Run: `ws test eldr -k test_volume_counts_conditioned_rooms_only`

The working tree already carries the fix, so this should PASS immediately. To confirm the test actually pins the behavior, temporarily revert the hunk with `git -C components/eldr stash`, re-run, observe FAIL (the garage's 400×300×300 volume is included), then `git -C components/eldr stash pop`.

Expected when reverted: FAIL, actual volume larger than expected by the garage's contribution.

- [ ] **Step 3: Verify the implementation matches the spec**

`eldr/geometry.py` around line 405 should read:

```python
        # Conditioned volume for infiltration: sum conditioned-room floor area x height.
        # A level with only UNconditioned rooms (garage/crawlspace) contributes nothing —
        # it isn't part of the conditioned envelope the air leaks into. A roomless level
        # falls back to the bounding box (matches the wall-inference fallback), so a
        # conditioned floor not yet drawn as rooms still counts.
        height_ft = units.cm_to_ft(_f(lv, "height"))
        cond_area_ft2 = sum(r["area_ft2"] for r in conditioned_here)
        if cond_area_ft2 > 0:
            volume_ft3 += cond_area_ft2 * height_ft
        elif not rooms_here:
            volume_ft3 += units.cm_to_ft(maxx - minx) * units.cm_to_ft(maxy - miny) * height_ft
```

- [ ] **Step 4: Run the full suite**

Run: `ws test eldr`
Expected: 145 passed (144 existing + the new one).

- [ ] **Step 5: Commit**

Write `.commits/eldr-volume-fix.md`:

```markdown
---
message: "fix(eldr): infiltration volume counts conditioned rooms only"
add:
  - eldr/geometry.py
  - eldr/tests/test_geometry.py
---

Volume came from each level's wall bounding box, so unconditioned levels — the garage, the crawlspace — were counted as space the air leaks into. On Refrhus that made the infiltration volume roughly twice its true size.

Found by cross-checking against the HVAC company's Manual J: with their design temps and blower-door ACH applied, our infiltration read 25,355 BTU/hr against their 13,154. Correcting the volume brings it to 14,352 — the remaining gap to their number is under 10%, and the whole-house discrepancy moves entirely into the conductive lines.

The multi-level regression test pins it with a garage level sized to double the volume if it ever leaks back in.
```

Then: `ws commit eldr .commits/eldr-volume-fix.md`

---

### Task 2: `spaces.py` — buffer-space temperature policies

**Files:**
- Create: `eldr/spaces.py`
- Create: `eldr/tests/test_spaces.py`
- Modify: `eldr/sidecar.py` (add the `spaces` block)
- Modify: `eldr/tests/test_sidecar.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `spaces.SpacePolicy(name: str, winter_temp_f: float | None, summer_temp_f: float | None, factor: float | None, vented: bool | None)`
  - `spaces.heating_factor(policy, indoor_f, outdoor_f) -> float`
  - `spaces.cooling_factor(policy, indoor_f, outdoor_f) -> float`
  - `spaces.policy_for(name: str, declared: dict[str, SpacePolicy]) -> SpacePolicy`
  - `spaces.DEFAULT_POLICIES: dict[str, SpacePolicy]`, `spaces.UNVENTED_FACTOR = 0.5`, `spaces.VENTED_FACTOR = 1.0`
  - `sidecar.SideCar.spaces: dict[str, SpacePolicy]`

- [ ] **Step 1: Write the failing tests**

Create `eldr/tests/test_spaces.py`:

```python
import pytest
from eldr import spaces


def test_temperature_beats_factor_and_vented():
    """An observed design-day temperature is the most defensible input, so it wins."""
    p = spaces.SpacePolicy(name="crawlspace", winter_temp_f=32.0, factor=0.9, vented=True)
    # (70 - 32) / (70 - 13) = 0.6666...
    assert abs(spaces.heating_factor(p, indoor_f=70.0, outdoor_f=13.0) - 38.0 / 57.0) < 1e-9


def test_factor_beats_vented():
    p = spaces.SpacePolicy(name="garage", factor=0.25, vented=True)
    assert spaces.heating_factor(p, 70.0, 13.0) == 0.25


def test_vented_shorthand():
    vented = spaces.SpacePolicy(name="crawlspace", vented=True)
    sealed = spaces.SpacePolicy(name="crawlspace", vented=False)
    assert spaces.heating_factor(vented, 70.0, 13.0) == spaces.VENTED_FACTOR
    assert spaces.heating_factor(sealed, 70.0, 13.0) == spaces.UNVENTED_FACTOR


def test_bare_policy_falls_back_to_unvented_default():
    p = spaces.SpacePolicy(name="whatever")
    assert spaces.heating_factor(p, 70.0, 13.0) == spaces.UNVENTED_FACTOR


def test_cooling_attic_hotter_than_outdoor_exceeds_one():
    """The whole point of a hot attic: its delta-T is LARGER than the outdoor one,
    so the cooling factor must not be clamped at 1."""
    p = spaces.SpacePolicy(name="attic", summer_temp_f=130.0)
    # (130 - 75) / (89 - 75) = 3.928...
    assert spaces.cooling_factor(p, indoor_f=75.0, outdoor_f=89.0) == pytest.approx(55.0 / 14.0)


def test_factors_clamp_at_zero_not_negative():
    """A buffer warmer than indoors in winter gains heat; a heating load must not
    count that as a negative loss."""
    p = spaces.SpacePolicy(name="crawlspace", winter_temp_f=80.0)
    assert spaces.heating_factor(p, 70.0, 13.0) == 0.0
    q = spaces.SpacePolicy(name="crawlspace", summer_temp_f=60.0)
    assert spaces.cooling_factor(q, 75.0, 89.0) == 0.0


def test_degenerate_delta_t_yields_zero():
    """Indoor == outdoor would divide by zero; there is no load to apportion."""
    p = spaces.SpacePolicy(name="attic", winter_temp_f=50.0)
    assert spaces.heating_factor(p, 70.0, 70.0) == 0.0


def test_policy_for_uses_declared_then_default():
    declared = {"attic": spaces.SpacePolicy(name="attic", vented=True)}
    assert spaces.policy_for("attic", declared).vented is True
    # crawlspace is not declared -> the built-in default, which exists and is unvented
    assert spaces.policy_for("crawlspace", declared).name == "crawlspace"
    assert spaces.heating_factor(spaces.policy_for("crawlspace", declared), 70.0, 13.0) == 0.5
    # a name with no default at all still resolves to a usable policy
    assert spaces.policy_for("mystery", {}).name == "mystery"
```

- [ ] **Step 2: Run to verify failure**

Run: `ws test eldr -k test_spaces`
Expected: FAIL — `ModuleNotFoundError: No module named 'eldr.spaces'`

- [ ] **Step 3: Implement `eldr/spaces.py`**

```python
"""Buffer-space temperature policies.

A *space* is a named unconditioned volume adjacent to conditioned rooms — an attic, a
crawlspace, an attached garage. A surface facing one sees only part of the design ΔT,
because the space floats somewhere between indoors and outdoors.

Precedence, most to least defensible:
  1. an observed design-day temperature (winter_temp_f / summer_temp_f)
  2. an explicit factor (fraction of design ΔT)
  3. the vented shorthand (vented -> 1.0, sealed -> 0.5)
  4. the space's built-in default

Temperature wins because it is an *observation*, not a model: "the crawl gets near
freezing on the coldest days" is a stronger claim to hand an HVAC partner than "we
assumed 50% of ΔT", and it short-circuits chains that would otherwise need modeling
(this crawlspace is partly vented to outdoors AND open into the garage).
"""
from __future__ import annotations
from dataclasses import dataclass

# A buffer sitting midway between indoor and outdoor. This is the long-standing
# BUFFER_FACTOR from loads.py, now the *default* rather than the only option.
UNVENTED_FACTOR = 0.5
# A vented space tracks outdoor air closely enough to load at the full ΔT.
VENTED_FACTOR = 1.0


@dataclass(frozen=True)
class SpacePolicy:
    name: str
    winter_temp_f: float | None = None
    summer_temp_f: float | None = None
    factor: float | None = None
    vented: bool | None = None


# Built-in defaults. `attic` is unvented per the Refrhus owner's observation (no ridge
# or soffit ventilation visible, and it does not get outdoor-cold). NOTE this diverges
# from typical ACCA practice, which loads a vented attic at full outdoor temperature for
# heating — declare `vented: true` to restore that. The report echoes which was used.
DEFAULT_POLICIES: dict[str, SpacePolicy] = {
    "attic": SpacePolicy(name="attic", vented=False),
    "crawlspace": SpacePolicy(name="crawlspace", vented=False),
    "garage": SpacePolicy(name="garage", vented=False),
    "outdoor": SpacePolicy(name="outdoor", factor=1.0),
}


def policy_for(name: str, declared: dict[str, SpacePolicy]) -> SpacePolicy:
    """The policy for a space name: side-car declaration, else built-in default, else bare."""
    if name in declared:
        return declared[name]
    if name in DEFAULT_POLICIES:
        return DEFAULT_POLICIES[name]
    return SpacePolicy(name=name)


def _factor(policy: SpacePolicy, space_temp_f: float | None,
            inner_f: float, outer_f: float) -> float:
    """Resolve a ΔT fraction, applying the precedence order.

    Clamped at 0 (a buffer on the wrong side of the setpoint contributes no load in
    this mode) but deliberately NOT clamped above 1: a sun-heated attic is hotter than
    outdoor air, so its cooling ΔT legitimately exceeds the outdoor one.
    """
    if space_temp_f is not None:
        span = outer_f - inner_f
        if span == 0.0:
            return 0.0
        return max(0.0, (space_temp_f - inner_f) / span)
    if policy.factor is not None:
        return policy.factor
    if policy.vented is not None:
        return VENTED_FACTOR if policy.vented else UNVENTED_FACTOR
    return UNVENTED_FACTOR


def heating_factor(policy: SpacePolicy, indoor_f: float, outdoor_f: float) -> float:
    """Fraction of the heating design ΔT a surface facing this space sees."""
    return _factor(policy, policy.winter_temp_f, indoor_f, outdoor_f)


def cooling_factor(policy: SpacePolicy, indoor_f: float, outdoor_f: float) -> float:
    """Fraction of the cooling design ΔT a surface facing this space sees."""
    return _factor(policy, policy.summer_temp_f, indoor_f, outdoor_f)
```

Note on `_factor`'s sign handling: heating passes `inner=indoor, outer=outdoor` (indoor > outdoor), cooling passes `inner=indoor, outer=outdoor` (outdoor > indoor). In both modes the expression `(space - inner) / (outer - inner)` yields the fraction of the design span at which the space sits, so one formula serves both.

- [ ] **Step 4: Run to verify pass**

Run: `ws test eldr -k test_spaces`
Expected: PASS (9 tests).

- [ ] **Step 5: Write the failing side-car test**

`test_sidecar.py` currently has one helper, `_write(tmp_path, body) -> path`, and each test inlines a full side-car. The new blocks are all additive, so add a shared base plus a load helper at the top of the file, just after `_write`:

```python
BASE_SIDECAR = """
    design:
      indoor_heating_f: 70
      outdoor_heating_99_f: 15
      supply_air_rise_f: 50
    infiltration:
      ach: 0.5
    assemblies:
      exterior_wall: 0.09
      window: 0.30
"""


def _write_and_load(tmp_path, body):
    return sidecar.load_sidecar(_write(tmp_path, body))
```

`_write` already runs `textwrap.dedent`, so appended blocks must use the same 4-space base indentation as `BASE_SIDECAR`. Then add:

```python
def test_spaces_block_optional(tmp_path):
    assert _write_and_load(tmp_path, BASE_SIDECAR).spaces == {}


def test_spaces_block_parsed(tmp_path):
    sc = _write_and_load(tmp_path, BASE_SIDECAR + """
    spaces:
      crawlspace:
        winter_temp_f: 32
      attic:
        vented: false
      garage:
        factor: 0.25
""")
    assert sc.spaces["crawlspace"].winter_temp_f == 32.0
    assert sc.spaces["attic"].vented is False
    assert sc.spaces["garage"].factor == 0.25


def test_spaces_rejects_non_mapping_spec(tmp_path):
    with pytest.raises(ValueError, match=r"spaces\['attic'\]"):
        _write_and_load(tmp_path, BASE_SIDECAR + "\n    spaces:\n      attic: 0.5\n")


def test_spaces_rejects_bad_factor(tmp_path):
    with pytest.raises(ValueError, match="factor"):
        _write_and_load(tmp_path, BASE_SIDECAR + "\n    spaces:\n      attic:\n        factor: -1\n")


def test_spaces_rejects_non_boolean_vented(tmp_path):
    with pytest.raises(ValueError, match="vented"):
        _write_and_load(tmp_path, BASE_SIDECAR
                        + "\n    spaces:\n      attic:\n        vented: sometimes\n")
```

- [ ] **Step 6: Run to verify failure**

Run: `ws test eldr -k test_spaces_block`
Expected: FAIL — `AttributeError: 'SideCar' object has no attribute 'spaces'`

- [ ] **Step 7: Implement side-car parsing**

In `eldr/sidecar.py`, add the import and field:

```python
from eldr import ductd, spaces as spaces_mod
```

Add to the `SideCar` dataclass, after `wall_boundaries`:

```python
    # buffer-space temperature policies, keyed by space name (attic / crawlspace / ...)
    spaces: dict[str, spaces_mod.SpacePolicy] = field(default_factory=dict)
```

Add this parsing block in `load_sidecar`, immediately after the `walls_raw` block:

```python
    spaces_raw = raw.get("spaces")
    if spaces_raw is not None and not isinstance(spaces_raw, dict):
        raise ValueError("spaces must be a mapping of space-name -> policy")
    space_policies: dict[str, spaces_mod.SpacePolicy] = {}
    for name, spec in (spaces_raw or {}).items():
        if not isinstance(spec, dict):
            raise ValueError(f"spaces['{name}'] must be a mapping "
                             f"(winter_temp_f / summer_temp_f / factor / vented)")
        vented = spec.get("vented")
        if vented is not None and not isinstance(vented, bool):
            raise ValueError(f"spaces['{name}'].vented must be true or false")
        space_policies[str(name)] = spaces_mod.SpacePolicy(
            name=str(name),
            winter_temp_f=_optional_number(spec, "winter_temp_f", f"spaces['{name}']"),
            summer_temp_f=_optional_number(spec, "summer_temp_f", f"spaces['{name}']"),
            factor=_optional_number(spec, "factor", f"spaces['{name}']"),
            vented=vented,
        )
```

Pass `spaces=space_policies` into the `SideCar(...)` construction.

Add to `_validate`, inside the existing validation body:

```python
    for name, p in sc.spaces.items():
        for label, val in (("winter_temp_f", p.winter_temp_f), ("summer_temp_f", p.summer_temp_f),
                           ("factor", p.factor)):
            if val is not None and not math.isfinite(val):
                raise ValueError(f"spaces['{name}'].{label} must be a finite number")
        if p.factor is not None and p.factor < 0:
            raise ValueError(f"spaces['{name}'].factor must be >= 0")
```

- [ ] **Step 8: Run the full suite**

Run: `ws test eldr`
Expected: all pass — the new field is defaulted, so nothing existing changes.

- [ ] **Step 9: Commit**

Write `.commits/eldr-spaces.md` with message `feat(eldr): per-space buffer temperature policies` and `add:` listing `eldr/spaces.py`, `eldr/sidecar.py`, `eldr/tests/test_spaces.py`, `eldr/tests/test_sidecar.py`. Body: explain that the single `BUFFER_FACTOR` could not express a vented crawl vs a sealed attic vs a garage, that temperature-first precedence exists because an observation beats a model, and that this delivers the follow-up deferred in the wall-boundary design.

Then: `ws commit eldr .commits/eldr-spaces.md`

---

### Task 3: `stack.py` — level ordering and scaffolding detection

**Files:**
- Create: `eldr/stack.py`
- Create: `eldr/tests/test_stack.py`

**Interfaces:**
- Consumes: nothing (pure — no XML, no side-car).
- Produces:
  - `stack.LevelInfo(id: str, name: str, elevation_cm: float, elevation_index: int, height_cm: float, conditioned: bool)`
  - `stack.ordered_levels(levels: list[LevelInfo]) -> list[LevelInfo]`
  - `stack.scaffolding_ids(levels, rooms_by_level: dict[str, list[dict]]) -> frozenset[str]`
  - `stack.TOLERANCE_CM = 20.0`, `stack.MIN_REGION_FT2 = 2.0`, `stack.GRID_CM = 15.0`

- [ ] **Step 1: Write the failing tests**

Create `eldr/tests/test_stack.py`:

```python
from eldr import stack


def _lv(lid, name, elev, idx=0, height=250.0, conditioned=True):
    return stack.LevelInfo(id=lid, name=name, elevation_cm=elev, elevation_index=idx,
                           height_cm=height, conditioned=conditioned)


def test_orders_by_elevation():
    levels = [_lv("LM", "Main", 255.84), _lv("LB", "Basement", 0.0), _lv("L2", "2nd", 511.68)]
    assert [l.id for l in stack.ordered_levels(levels)] == ["LB", "LM", "L2"]


def test_ties_break_on_elevation_index():
    """Refrhus has Garage and Crawlspace both at 121.92cm, separated only by index."""
    levels = [_lv("LC", "Crawlspace", 121.92, idx=1), _lv("LG", "Garage", 121.92, idx=0)]
    assert [l.id for l in stack.ordered_levels(levels)] == ["LG", "LC"]


def test_roomless_level_is_scaffolding_when_others_have_rooms():
    """The basement-main-transition level holds joists and duct runs, not space."""
    levels = [_lv("LB", "Basement", 0.0), _lv("LT", "transition", 213.36), _lv("LM", "Main", 255.84)]
    rooms = {"LB": [{"id": "r1"}], "LM": [{"id": "r2"}]}
    assert stack.scaffolding_ids(levels, rooms) == frozenset({"LT"})


def test_no_scaffolding_when_the_model_has_no_rooms_at_all():
    """Eldr supports roomless models via the bounding-box fallback. Without this
    guard every level would be 'scaffolding' and the house would lose its floor,
    ceiling and volume entirely."""
    levels = [_lv("LM", "Main", 0.0)]
    assert stack.scaffolding_ids(levels, {}) == frozenset()


def test_level_with_only_empty_room_list_is_scaffolding():
    levels = [_lv("LM", "Main", 0.0), _lv("LT", "t", 250.0)]
    rooms = {"LM": [{"id": "r"}], "LT": []}
    assert stack.scaffolding_ids(levels, rooms) == frozenset({"LT"})
```

- [ ] **Step 2: Run to verify failure**

Run: `ws test eldr -k test_stack`
Expected: FAIL — `ModuleNotFoundError: No module named 'eldr.stack'`

- [ ] **Step 3: Implement the first half of `eldr/stack.py`**

```python
"""Resolve what sits above and below each room — pure geometry, no thermal knowledge.

Eldr used to assign a ceiling to the highest level and a floor to the lowest, both from
level bounding boxes. A house that grew in stages defeats that: a partial second floor
leaves most of the main level's ceiling facing the attic, and an extension over a
crawlspace has a floor the model never saw.

Two rules make this work on real models:

* **XY overlap decides adjacency; elevation only decides ordering.** Levels overlap
  vertically without being adjacent — an attached garage can span the basement AND the
  lower half of the main floor while sitting beside the house, not under it.
* **Roomless levels are scaffolding** (joists, duct chases) and take no part — but only
  when some other level has rooms, since a model with no rooms at all is a supported
  case with its own bounding-box fallback.
"""
from __future__ import annotations
from dataclasses import dataclass

# Void cells within this distance of the room's own outline are treated as wall
# misalignment rather than real exposure. Sized just over a typical framed wall
# thickness (7in = 17.8cm) — artifacts hug the boundary; real voids reach inside.
TOLERANCE_CM = 20.0
# A resolved region smaller than this is noise (a shared wall clipping the level
# below) and is redistributed across the surviving categories.
MIN_REGION_FT2 = 2.0
# Rasterization cell size. 15cm cells are ~0.24 ft^2 — far finer than MIN_REGION_FT2.
GRID_CM = 15.0


@dataclass(frozen=True)
class LevelInfo:
    id: str
    name: str
    elevation_cm: float
    elevation_index: int
    height_cm: float
    conditioned: bool


def ordered_levels(levels: list[LevelInfo]) -> list[LevelInfo]:
    """Levels bottom to top. Elevation ties break on elevationIndex — SH3D uses that
    to stack same-elevation levels (a garage and a crawlspace sharing a height)."""
    return sorted(levels, key=lambda l: (l.elevation_cm, l.elevation_index))


def scaffolding_ids(levels: list[LevelInfo],
                    rooms_by_level: dict[str, list[dict]]) -> frozenset[str]:
    """Levels that exist to hold geometry rather than space, so take no part in the stack.

    A level with no rooms qualifies — UNLESS no level in the model has rooms, in which
    case the model is simply roomless and the caller's bounding-box fallback owns it.
    """
    if not any(rooms_by_level.get(l.id) for l in levels):
        return frozenset()
    return frozenset(l.id for l in levels if not rooms_by_level.get(l.id))
```

- [ ] **Step 4: Run to verify pass**

Run: `ws test eldr -k test_stack`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

`.commits/eldr-stack-ordering.md`, message `feat(eldr): level ordering + scaffolding detection`, adding `eldr/stack.py` and `eldr/tests/test_stack.py`.

---

### Task 4: `stack.py` — rasterized adjacency resolution

**Files:**
- Modify: `eldr/stack.py`
- Modify: `eldr/tests/test_stack.py`

**Interfaces:**
- Consumes: `LevelInfo`, `ordered_levels`, `scaffolding_ids` from Task 3.
- Produces:
  - `stack.resolve_faces(levels, rooms_by_level, *, below_void="crawlspace", above_void="attic", level_voids=None, ignore_ids=frozenset(), tolerance_cm=TOLERANCE_CM, min_region_ft2=MIN_REGION_FT2, grid_cm=GRID_CM) -> dict[str, FaceSplit]`
  - `level_voids: dict[str, tuple[str | None, str | None]] | None` — per-level `(below, above)` overrides keyed by level id; either element may be `None` to fall through to the house default.
  - `stack.FaceSplit(room_id: str, below: dict[str, float], above: dict[str, float], void_below_ft2: float)`
  - Category keys in `below` / `above`: `"interior"`, `"ground"`, or a space name.

Room records are the dicts `geometry._parse_rooms` already produces — keys `id`, `name`, `level_id`, `points`, `area_ft2`, `conditioned`.

- [ ] **Step 1: Write the failing tests**

Add to `eldr/tests/test_stack.py`:

```python
def _room(rid, lid, x0, y0, x1, y1, conditioned=True, name=None):
    from eldr import units
    return {"id": rid, "name": name or rid, "level_id": lid,
            "points": [(x0, y0), (x1, y0), (x1, y1), (x0, y1)],
            "area_ft2": units.sqcm_to_sqft((x1 - x0) * (y1 - y0)),
            "conditioned": conditioned}


def _resolve(levels, rooms, **kw):
    return stack.resolve_faces(levels, rooms, below_void="crawlspace", above_void="attic", **kw)


def test_conditioned_over_conditioned_is_interior():
    levels = [_lv("LB", "Basement", 0.0), _lv("LM", "Main", 250.0)]
    rooms = {"LB": [_room("rb", "LB", 0, 0, 400, 300)],
             "LM": [_room("rm", "LM", 0, 0, 400, 300)]}
    faces = _resolve(levels, rooms)
    assert set(faces["rm"].below) == {"interior"}
    assert set(faces["rb"].below) == {"ground"}      # lowest conditioned level -> slab


def test_lowest_level_floor_is_ground():
    levels = [_lv("LM", "Main", 0.0)]
    rooms = {"LM": [_room("rm", "LM", 0, 0, 400, 300)]}
    faces = _resolve(levels, rooms)
    assert set(faces["rm"].below) == {"ground"}


def test_vertical_overlap_without_footprint_overlap_is_not_adjacency():
    """A garage spanning the main floor's vertical range but sitting BESIDE it must
    not become the floor below. This is the Refrhus garage: 4-14ft, crossing Main's
    8.39-16.39ft, yet entirely elsewhere in plan."""
    levels = [_lv("LG", "Garage", 120.0, height=300.0, conditioned=False),
              _lv("LM", "Main", 250.0)]
    rooms = {"LG": [_room("rg", "LG", 500, 0, 900, 300, conditioned=False)],
             "LM": [_room("rm", "LM", 0, 0, 400, 300)]}
    faces = _resolve(levels, rooms)
    assert "garage" not in faces["rm"].below
    assert set(faces["rm"].below) == {"ground"}   # nothing below in plan -> it IS the lowest here


def test_area_splits_across_two_levels_below():
    """The Kitchen shape: half over the basement, half over the crawlspace."""
    levels = [_lv("LB", "Basement", 0.0), _lv("LC", "Crawl", 0.0, idx=1, conditioned=False),
              _lv("LM", "Main", 250.0)]
    rooms = {"LB": [_room("rb", "LB", 0, 0, 200, 300)],
             "LC": [_room("rc", "LC", 200, 0, 400, 300, conditioned=False)],
             "LM": [_room("rm", "LM", 0, 0, 400, 300)]}
    below = _resolve(levels, rooms)["rm"].below
    assert abs(below["interior"] - below["crawl"]) < 1.0     # halves, within a cell
    assert set(below) == {"interior", "crawl"}


def test_partial_ceiling_gets_attic_for_the_uncovered_half():
    levels = [_lv("LM", "Main", 0.0), _lv("L2", "2nd", 250.0)]
    rooms = {"LM": [_room("rm", "LM", 0, 0, 400, 300)],
             "L2": [_room("r2", "L2", 0, 0, 200, 300)]}
    above = _resolve(levels, rooms)["rm"].above
    assert set(above) == {"interior", "attic"}
    assert abs(above["interior"] - above["attic"]) < 1.0


def test_top_level_ceiling_is_all_attic():
    levels = [_lv("LM", "Main", 0.0)]
    rooms = {"LM": [_room("rm", "LM", 0, 0, 400, 300)]}
    assert set(_resolve(levels, rooms)["rm"].above) == {"attic"}


def test_void_sliver_along_the_edge_is_discarded():
    """A 10cm misalignment band around the room is wall-thickness noise, not exposure."""
    levels = [_lv("LB", "Basement", 0.0), _lv("LM", "Main", 250.0)]
    rooms = {"LB": [_room("rb", "LB", 10, 10, 390, 290)],
             "LM": [_room("rm", "LM", 0, 0, 400, 300)]}
    faces = _resolve(levels, rooms)
    assert "crawlspace" not in faces["rm"].below
    assert faces["rm"].void_below_ft2 == 0.0


def test_interior_void_blob_survives_and_becomes_buffer():
    """The Main Bed extension: undrawn crawlspace reaching well inside the room."""
    levels = [_lv("LB", "Basement", 0.0), _lv("LM", "Main", 250.0)]
    rooms = {"LB": [_room("rb", "LB", 0, 0, 200, 300)],
             "LM": [_room("rm", "LM", 0, 0, 400, 300)]}
    faces = _resolve(levels, rooms)
    assert "crawlspace" in faces["rm"].below
    assert faces["rm"].void_below_ft2 > 0.0


def test_tiny_region_is_dropped_and_redistributed():
    """The 1.0 sqft the Refrhus garage clips under Main is an artifact, not a surface."""
    levels = [_lv("LB", "Basement", 0.0), _lv("LG", "Garage", 0.0, idx=1, conditioned=False),
              _lv("LM", "Main", 250.0)]
    rooms = {"LB": [_room("rb", "LB", 0, 0, 400, 300)],
             "LG": [_room("rg", "LG", 0, 0, 30, 30, conditioned=False)],
             "LM": [_room("rm", "LM", 0, 0, 400, 300)]}
    below = _resolve(levels, rooms)["rm"].below
    assert "garage" not in below
    assert set(below) == {"interior"}


def test_scaffolding_level_is_invisible_to_the_stack():
    levels = [_lv("LB", "Basement", 0.0), _lv("LT", "transition", 200.0),
              _lv("LM", "Main", 250.0)]
    rooms = {"LB": [_room("rb", "LB", 0, 0, 400, 300)], "LT": [],
             "LM": [_room("rm", "LM", 0, 0, 400, 300)]}
    assert set(_resolve(levels, rooms)["rm"].below) == {"interior"}


def test_unconditioned_rooms_get_no_faces():
    levels = [_lv("LG", "Garage", 0.0, conditioned=False)]
    rooms = {"LG": [_room("rg", "LG", 0, 0, 400, 300, conditioned=False)]}
    assert _resolve(levels, rooms) == {}
```

- [ ] **Step 2: Run to verify failure**

Run: `ws test eldr -k test_stack`
Expected: FAIL — `AttributeError: module 'eldr.stack' has no attribute 'resolve_faces'`

- [ ] **Step 3: Implement the resolver**

Append to `eldr/stack.py`:

```python
from eldr import units


@dataclass(frozen=True)
class FaceSplit:
    """How one conditioned room's floor and ceiling divide by what they face.

    `below` / `above` map a category — "interior", "ground", or a space name — to area
    in ft^2. `void_below_ft2` is the area that survived the misalignment tolerance with
    nothing drawn beneath it, reported so a schematic gap stays visible.
    """
    room_id: str
    below: dict[str, float]
    above: dict[str, float]
    void_below_ft2: float = 0.0


def _bbox(points):
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return min(xs), max(xs), min(ys), max(ys)


def _inside(x, y, points):
    """Ray-cast point-in-polygon; handles the non-convex room outlines SH3D allows."""
    hit = False
    n = len(points)
    for i in range(n):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % n]
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            hit = not hit
    return hit


def _seg_distance(px, py, x1, y1, x2, y2):
    dx, dy = x2 - x1, y2 - y1
    if dx == 0.0 and dy == 0.0:
        return ((px - x1) ** 2 + (py - y1) ** 2) ** 0.5
    t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)))
    return ((px - (x1 + t * dx)) ** 2 + (py - (y1 + t * dy)) ** 2) ** 0.5


def _edge_distance(px, py, points):
    n = len(points)
    return min(_seg_distance(px, py, *points[i], *points[(i + 1) % n]) for i in range(n))


def _covering_room(x, y, rooms):
    """The first room in `rooms` whose polygon covers (x, y), or None. Bounding-box
    pre-filtered — this runs once per raster cell per candidate level."""
    for rm in rooms:
        minx, maxx, miny, maxy = rm["_bbox"]
        if minx <= x <= maxx and miny <= y <= maxy and _inside(x, y, rm["points"]):
            return rm
    return None


def _space_name(level: LevelInfo) -> str:
    """An unconditioned level becomes a named buffer space — its own name, lowercased,
    so `spaces:` in the side-car keys off something the owner can see in SH3D."""
    return (level.name or "space").strip().lower()


def _tidy(areas: dict[str, float], min_region_ft2: float) -> dict[str, float]:
    """Drop noise regions and redistribute their area across the survivors.

    A shared wall clips a level below by a fraction of a square foot; that is an
    artifact of two polygons meeting, not a surface. If EVERY category is under the
    threshold the room is simply smaller than the threshold — keep the largest so a
    tiny closet still gets its ceiling.
    """
    if not areas:
        return {}
    kept = {k: v for k, v in areas.items() if v >= min_region_ft2}
    if not kept:
        top = max(areas, key=lambda k: areas[k])
        return {top: sum(areas.values())}
    total = sum(areas.values())
    scale = total / sum(kept.values())
    return {k: v * scale for k, v in kept.items()}


def resolve_faces(levels: list[LevelInfo], rooms_by_level: dict[str, list[dict]], *,
                  below_void: str = "crawlspace", above_void: str = "attic",
                  level_voids: dict[str, tuple[str | None, str | None]] | None = None,
                  ignore_ids: frozenset[str] = frozenset(),
                  tolerance_cm: float = TOLERANCE_CM,
                  min_region_ft2: float = MIN_REGION_FT2,
                  grid_cm: float = GRID_CM) -> dict[str, FaceSplit]:
    """Resolve every conditioned room's floor and ceiling against the levels around it.

    `below_void` / `above_void` name the space an undrawn gap represents — a house's
    unmodeled space beneath is almost always crawlspace and above is almost always
    attic. `level_voids` overrides either per level id, for the model where one wing
    sits over open air while the rest sits over crawl.
    """
    skip = set(ignore_ids) | set(scaffolding_ids(levels, rooms_by_level))
    order = [l for l in ordered_levels(levels) if l.id not in skip]
    if not order:
        return {}

    by_id = {l.id: l for l in order}
    rooms = {l.id: [dict(rm, _bbox=_bbox(rm["points"])) for rm in rooms_by_level.get(l.id, [])]
             for l in order}
    lowest_conditioned = next((l.id for l in order if any(
        rm["conditioned"] for rm in rooms[l.id])), None)

    cell_ft2 = units.sqcm_to_sqft(grid_cm * grid_cm)
    out: dict[str, FaceSplit] = {}

    for level in order:
        others_below = [l for l in order if (l.elevation_cm, l.elevation_index)
                        < (level.elevation_cm, level.elevation_index)][::-1]
        others_above = [l for l in order if (l.elevation_cm, l.elevation_index)
                        > (level.elevation_cm, level.elevation_index)]
        lv_below, lv_above = (level_voids or {}).get(level.id, (None, None))
        this_below_void = lv_below or below_void
        this_above_void = lv_above or above_void
        for room in rooms[level.id]:
            if not room["conditioned"]:
                continue
            below: dict[str, float] = {}
            above: dict[str, float] = {}
            void_cells = []
            minx, maxx, miny, maxy = room["_bbox"]
            y = miny
            while y < maxy:
                x = minx
                while x < maxx:
                    cx, cy = x + grid_cm / 2.0, y + grid_cm / 2.0
                    x += grid_cm
                    if not _inside(cx, cy, room["points"]):
                        continue
                    for neighbours, bucket, is_below in (
                            (others_below, below, True),
                            (others_above, above, False)):
                        found = None
                        for cand in neighbours:
                            hit = _covering_room(cx, cy, rooms[cand.id])
                            if hit is not None:
                                found = ("interior" if hit["conditioned"]
                                         else _space_name(by_id[cand.id]))
                                break
                        if found is None:
                            if is_below and level.id == lowest_conditioned:
                                found = "ground"
                            elif is_below:
                                void_cells.append((cx, cy))
                                continue
                            else:
                                found = this_above_void
                        bucket[found] = bucket.get(found, 0.0) + cell_ft2
                y += grid_cm

            real_void = [c for c in void_cells
                         if _edge_distance(c[0], c[1], room["points"]) > tolerance_cm]
            void_ft2 = len(real_void) * cell_ft2
            if void_ft2 > 0.0:
                below[this_below_void] = below.get(this_below_void, 0.0) + void_ft2

            out[room["id"]] = FaceSplit(
                room_id=room["id"],
                below=_tidy(below, min_region_ft2),
                above=_tidy(above, min_region_ft2),
                void_below_ft2=void_ft2,
            )
    return out
```

Note on the sliver rule: void cells inside the tolerance band are dropped entirely rather than reassigned, so a misaligned room's floor area sums slightly below its polygon area. `_tidy` then rescales the surviving categories back to the room's resolved total, which is why the discarded band does not silently shrink the floor.

- [ ] **Step 4: Run to verify pass**

Run: `ws test eldr -k test_stack`
Expected: PASS (17 tests).

- [ ] **Step 5: Check the runtime cost against the real model**

Rasterizing every room twice is the most expensive thing Eldr does. Confirm it stays negligible:

```bash
components/eldr/.venv/bin/python -c "
import time
from eldr import geometry
t=time.time(); geometry.extract_envelope('hoards/refrhus/Refrhus.sh3d'); print(time.time()-t)
"
```

Expected: under 2 seconds. If it exceeds that, raise `GRID_CM` to 20.0 — cells stay far below `MIN_REGION_FT2` and the reported areas move by well under a square foot. Do not add a dependency to solve this.

- [ ] **Step 6: Commit**

`.commits/eldr-stack-resolve.md`, message `feat(eldr): resolve room floor/ceiling adjacency by XY overlap`.

---

### Task 5: Side-car `levels:` block + wire the resolver into `geometry.py`

This is the task that changes the numbers. It is deliberately one unit: the resolver has no effect until geometry consumes it, and reviewing "geometry now emits different surfaces" separately from "here is where they come from" would be reviewing half a change.

**Files:**
- Modify: `eldr/sidecar.py`
- Modify: `eldr/geometry.py:58-62` (Surface), `:287-318` (conditioning), `:371-419` (volume), `:478-518` (surfaces)
- Modify: `eldr/tests/test_sidecar.py`, `eldr/tests/test_geometry.py`

**Interfaces:**
- Consumes: `stack.resolve_faces`, `stack.LevelInfo`, `sidecar.SideCar.spaces`.
- Produces:
  - `geometry.Surface(category, area_ft2, space=None)` — new optional third field.
  - `geometry.extract_envelope(home_path, wall_boundaries=None, levels=None)` — new optional `levels` argument taking `sidecar.SideCar.levels`.
  - `sidecar.LevelSpec(role: str | None, height_ft: float | None, below_void: str | None, above_void: str | None)` and `SideCar.levels: dict[str, LevelSpec]`.
  - `Envelope.voids: dict[str, float]` — room name -> void ft², for the report.
  - New surface categories: `buffer_floor`, `exposed_floor`. `ceiling` now carries `space`.

- [ ] **Step 1: Write the failing side-car tests**

Add to `eldr/tests/test_sidecar.py`:

```python
def test_levels_block_optional(tmp_path):
    assert _write_and_load(tmp_path, BASE_SIDECAR).levels == {}


def test_levels_block_parsed(tmp_path):
    sc = _write_and_load(tmp_path, BASE_SIDECAR + """
    levels:
      Main:
        height_ft: 8.5
        below_void: crawlspace
      Garage:
        role: unconditioned
      scaffold:
        role: ignore
""")
    assert sc.levels["Main"].height_ft == 8.5
    assert sc.levels["Main"].below_void == "crawlspace"
    assert sc.levels["Garage"].role == "unconditioned"
    assert sc.levels["scaffold"].role == "ignore"


def test_levels_rejects_unknown_role(tmp_path):
    with pytest.raises(ValueError, match="role"):
        _write_and_load(tmp_path, BASE_SIDECAR + "\n    levels:\n      Main:\n        role: buffer\n")


def test_levels_rejects_bad_height(tmp_path):
    with pytest.raises(ValueError, match="height_ft"):
        _write_and_load(tmp_path, BASE_SIDECAR
                        + "\n    levels:\n      Main:\n        height_ft: 0\n")
```

Note the third test: `buffer` is deliberately *not* a role. Buffer-ness is a property of a space's temperature policy, not of a level — keeping them separate is what makes "is the garage buffer or unconditioned?" two answerable questions instead of one confused enum.

- [ ] **Step 2: Run to verify failure**

Run: `ws test eldr -k test_levels_block`
Expected: FAIL — `AttributeError: 'SideCar' object has no attribute 'levels'`

- [ ] **Step 3: Implement side-car `levels:` parsing**

In `eldr/sidecar.py`, add near `WALL_BOUNDARIES`:

```python
# Roles a level may be assigned. Deliberately NO `buffer`: buffer-ness belongs to a
# space's temperature policy (see spaces.py), not to a level. An unconditioned level
# simply becomes a named space; how it is treated thermally is decided in `spaces:`.
LEVEL_ROLES = frozenset({"conditioned", "unconditioned", "ignore"})


@dataclass(frozen=True)
class LevelSpec:
    role: str | None = None
    height_ft: float | None = None
    below_void: str | None = None
    above_void: str | None = None
```

Add `levels: dict[str, LevelSpec] = field(default_factory=dict)` to `SideCar`, and this parsing block after the `spaces_raw` block:

```python
    levels_raw = raw.get("levels")
    if levels_raw is not None and not isinstance(levels_raw, dict):
        raise ValueError("levels must be a mapping of level-name -> spec")
    level_specs: dict[str, LevelSpec] = {}
    for name, spec in (levels_raw or {}).items():
        if not isinstance(spec, dict):
            raise ValueError(f"levels['{name}'] must be a mapping "
                             f"(role / height_ft / below_void / above_void)")
        role = spec.get("role")
        if role is not None and (not isinstance(role, str) or role not in LEVEL_ROLES):
            raise ValueError(f"levels['{name}'].role must be one of {sorted(LEVEL_ROLES)} "
                             f"(got {role!r})")
        height_ft = _optional_number(spec, "height_ft", f"levels['{name}']")
        if height_ft is not None and (not math.isfinite(height_ft) or height_ft <= 0):
            raise ValueError(f"levels['{name}'].height_ft must be a finite number > 0")
        level_specs[str(name)] = LevelSpec(
            role=role, height_ft=height_ft,
            below_void=None if spec.get("below_void") is None else str(spec["below_void"]),
            above_void=None if spec.get("above_void") is None else str(spec["above_void"]),
        )
```

Pass `levels=level_specs` into the `SideCar(...)` construction.

- [ ] **Step 4: Run to verify pass**

Run: `ws test eldr -k test_levels_block`
Expected: PASS (4 tests).

- [ ] **Step 5: Write the failing geometry tests**

Add to `eldr/tests/test_geometry.py` (reusing `MULTI_LEVEL_FIXTURE` from Task 1):

```python
def test_main_floor_over_basement_emits_no_surface(tmp_path):
    """Conditioned over conditioned is interior — no horizontal surface at all."""
    p = tmp_path / "Home.xml"
    p.write_text(MULTI_LEVEL_FIXTURE)
    env = geometry.extract_envelope(str(p))
    main = next(r for r in env.rooms if r.name == "Living room")
    assert not [s for s in main.surfaces if s.category in ("floor", "buffer_floor")]


def test_top_room_ceiling_faces_the_attic(tmp_path):
    p = tmp_path / "Home.xml"
    p.write_text(MULTI_LEVEL_FIXTURE)
    env = geometry.extract_envelope(str(p))
    main = next(r for r in env.rooms if r.name == "Living room")
    ceil = next(s for s in main.surfaces if s.category == "ceiling")
    assert ceil.space == "attic"


def test_whole_house_horizontals_equal_the_sum_of_room_horizontals(tmp_path):
    """Whole-house surfaces used level bounding boxes while per-room used polygons,
    so the two disagreed. They are now the same resolution by construction."""
    p = tmp_path / "Home.xml"
    p.write_text(MULTI_LEVEL_FIXTURE)
    env = geometry.extract_envelope(str(p))
    horizontals = {"ceiling", "floor", "buffer_floor", "exposed_floor"}
    for cat in horizontals:
        whole = sum(s.area_ft2 for s in env.surfaces if s.category == cat)
        rooms = sum(s.area_ft2 for r in env.rooms for s in r.surfaces if s.category == cat)
        assert abs(whole - rooms) < 1e-6, cat


def test_roomless_model_keeps_the_bounding_box_envelope(tmp_path):
    """Regression guard: the legacy fallback must survive the stack model."""
    p = tmp_path / "Home.xml"
    p.write_text(FIXTURE)                     # the original walls-only fixture
    env = geometry.extract_envelope(str(p))
    cats = _by_cat(env)
    from eldr import units
    foot = units.sqcm_to_sqft(1000 * 500)
    assert abs(cats["ceiling"] - foot) < 1e-6
    assert abs(cats["floor"] - foot) < 1e-6


def test_sidecar_level_height_override_changes_volume(tmp_path):
    from eldr import sidecar as sc_mod
    p = tmp_path / "Home.xml"
    p.write_text(MULTI_LEVEL_FIXTURE)
    base = geometry.extract_envelope(str(p))
    tall = geometry.extract_envelope(
        str(p), levels={"Main": sc_mod.LevelSpec(height_ft=20.0)})
    assert tall.volume_ft3 > base.volume_ft3


def test_scaffolding_level_with_a_wall_adds_no_volume(tmp_path):
    """A roomless level carrying a wall (a modeled duct chase) must not inject
    bounding-box volume into infiltration."""
    p = tmp_path / "Home.xml"
    p.write_text(MULTI_LEVEL_FIXTURE.replace(
        "  <room id='rb'",
        "  <level id='LT' name='transition' elevation='210.0' floorThickness='2.0'"
        " height='30' elevationIndex='0'/>\n"
        "  <wall id='t-n' level='LT' xStart='0' yStart='0' xEnd='400' yEnd='0'"
        " height='30' thickness='10'/>\n"
        "  <room id='rb'"))
    env = geometry.extract_envelope(str(p))
    from eldr import units
    expected = (units.sqcm_to_sqft(400 * 300) * units.cm_to_ft(200)
                + units.sqcm_to_sqft(400 * 300) * units.cm_to_ft(250))
    assert abs(env.volume_ft3 - expected) < 1e-6
```

- [ ] **Step 6: Run to verify failure**

Run: `ws test eldr -k "test_main_floor_over_basement or test_scaffolding_level_with_a_wall"`
Expected: FAIL — the floor surface is still emitted for the top level and the scaffolding wall still contributes volume.

- [ ] **Step 7: Implement the geometry wiring**

In `eldr/geometry.py`:

Extend `Surface` (line 58):

```python
@dataclass(frozen=True)
class Surface:
    category: str
    area_ft2: float
    # Which buffer space this surface faces, when it faces one. A category alone can't
    # set ΔT once policies are per-space: a buffer_floor over the crawl and one over the
    # garage share a category but not a temperature.
    space: str | None = None
```

Add two fields to `Envelope`, both consumed by the report in Task 8:

```python
    # room name -> floor area (ft^2) with nothing drawn beneath it, after the
    # misalignment tolerance. Surfaced as a schematic-gap warning, never silently.
    voids: dict[str, float] = field(default_factory=dict)
    # level name -> the height (ft) actually used, so a wrong SH3D default is visible.
    level_heights_ft: dict[str, float] = field(default_factory=dict)
```

Change the signature and conditioning:

```python
def extract_envelope(home_path: str, wall_boundaries: dict[str, str] | None = None,
                     levels: dict | None = None) -> Envelope:
```

Replace `_unconditioned_level`'s sole use so a side-car `role` wins over the name heuristic. Add near it:

```python
def _level_conditioned(level_name, spec):
    """Side-car role first, then the name heuristic it replaces."""
    if spec is not None and spec.role is not None:
        return spec.role == "conditioned"
    return not _unconditioned_level(level_name)
```

Thread `levels` (the side-car specs, keyed by level *name*) into `_parse_rooms` so the `conditioned` flag uses `_level_conditioned`.

**Rename the existing local first.** `extract_envelope` already binds a local `levels` at line 346 (`levels = {lv.get("id"): lv for lv in root.findall("level")}`), which the new parameter shadows. Rename that local to `levels_xml` throughout the function — including the `_parse_rooms(root, levels)` call and the `levels[level_id]` / `levels.get(lid)` lookups — before adding anything else. Do this as its own mechanical pass and re-run the suite, so a rename bug can't be confused with a logic bug.

Side-car level specs are keyed by level *name*, but SH3D does not enforce unique names. Reject collisions rather than silently taking the first match — add right after the rename:

```python
    _names = [lv.get("name") or "" for lv in levels_xml.values()]
    _dupes = sorted({n for n in _names if _names.count(n) > 1})
    if _dupes and levels:
        raise ValueError(f"level name(s) {_dupes} appear more than once in the model, so a "
                         f"side-car `levels` entry can't address one unambiguously — "
                         f"rename them in Sweet Home 3D")
```

Guarding on `if levels` keeps a duplicate-named model working for callers that pass no `levels` block at all — the collision only matters when something addresses a level by name.

Replace the whole-house ceiling/floor block (current lines 478-489) and the per-room `if lid == top` / `if lid == bot` block (508-511) with resolver-driven surfaces:

```python
    level_specs = levels or {}

    def _spec(lid):
        lv = levels_xml.get(lid)
        return level_specs.get((lv.get("name") or "") if lv is not None else "")

    def _voids(lid):
        """A level's notion of what an undrawn neighbour means; None falls through."""
        spec = _spec(lid)
        return (None, None) if spec is None else (spec.below_void, spec.above_void)

    infos = []
    for lid, lv in levels_xml.items():
        spec = _spec(lid)
        height_cm = _f(lv, "height")
        if spec is not None and spec.height_ft is not None:
            height_cm = spec.height_ft * 30.48
        infos.append(stack.LevelInfo(
            id=lid, name=lv.get("name") or "",
            elevation_cm=_f(lv, "elevation"),
            elevation_index=int(lv.get("elevationIndex") or 0),
            height_cm=height_cm,
            conditioned=_level_conditioned(lv.get("name"), spec)))

    ignore_ids = frozenset(l.id for l in infos
                           if (_spec(l.id) is not None and _spec(l.id).role == "ignore"))
    faces = stack.resolve_faces(
        infos, rooms_by_level,
        level_voids={l.id: _voids(l.id) for l in infos},
        ignore_ids=ignore_ids)
```

Where `_void_name` is a small helper reading a per-level override with the documented default. Then, per room, translate its `FaceSplit` into surfaces:

```python
CATEGORY_FOR_BELOW = {"ground": "floor", "outdoor": "exposed_floor"}


def _horizontal_surfaces(split):
    """FaceSplit -> Surfaces. 'interior' emits nothing: conditioned-over-conditioned
    is not part of the thermal envelope."""
    out = []
    for space, area in split.below.items():
        if space == "interior":
            continue
        cat = CATEGORY_FOR_BELOW.get(space, "buffer_floor")
        out.append(Surface(cat, area, None if cat == "floor" else space))
    for space, area in split.above.items():
        if space == "interior":
            continue
        out.append(Surface("ceiling", area, space))
    return out
```

Per-room: `surfs.extend(_horizontal_surfaces(faces[rid]))` when `rid in faces`. Whole-house: extend `surfaces` with the same lists, so the two are identical by construction. Populate `voids` from each split's `void_below_ft2` keyed by room name.

**Keep the legacy path** when `faces` is empty (a roomless model): the existing bounding-box ceiling/floor code runs unchanged. Guard it with `if not faces:` rather than deleting it.

For volume, exclude scaffolding explicitly rather than relying on the roomless level also lacking walls:

```python
        if level_id in scaffolding:
            continue          # joists / duct chases: geometry, not conditioned space
```

placed at the top of the per-level loop body's volume section, with `scaffolding = stack.scaffolding_ids(infos, rooms_by_level)` computed before the loop. Use the spec-overridden height for volume too.

- [ ] **Step 8: Run the full suite and fix the expected fallout**

Run: `ws test eldr`

Expect failures in tests that assert whole-house `floor` / `ceiling` areas for room-bearing fixtures, because those surfaces now come from room polygons rather than level bounding boxes. For each failure, decide deliberately:

- If the fixture's rooms fill the level, the numbers should be unchanged — a failure means a real bug, so debug rather than update.
- If the rooms do not fill the level, the new (smaller, polygon-based) number is correct. Update the expectation and add a one-line comment saying why it changed.

Do **not** bulk-update expectations to whatever the code prints. Each changed number needs a reason.

- [ ] **Step 9: Commit**

`.commits/eldr-stack-wiring.md`, message `feat(eldr): resolved floors and ceilings replace the bounding-box stack`.

---

### Task 6: `loads.py` — per-space ΔT and assembly fallbacks

**Files:**
- Modify: `eldr/loads.py:24-34` (constants), `:66-108` (conduction + ΔT resolvers)
- Modify: `eldr/tests/test_loads.py`

**Interfaces:**
- Consumes: `Surface.space`, `sidecar.SideCar.spaces`, `spaces.heating_factor` / `cooling_factor`.
- Produces: `_conduction(surfaces, assemblies, dt_for)` where `dt_for` now takes a `Surface`, not a category string. `loads.BUFFER_FACTOR` retained as the unvented default via `spaces.UNVENTED_FACTOR`.

- [ ] **Step 1: Write the failing tests**

Add to `eldr/tests/test_loads.py`:

```python
def test_buffer_floor_uses_its_space_policy():
    """Two buffer floors, same category, different spaces -> different ΔT."""
    env = _envelope([geometry.Surface("buffer_floor", 100.0, "crawlspace"),
                     geometry.Surface("buffer_floor", 100.0, "garage")])
    sc = _sidecar(assemblies={"buffer_floor": 0.5},
                  spaces={"crawlspace": spaces.SpacePolicy("crawlspace", winter_temp_f=32.0),
                          "garage": spaces.SpacePolicy("garage", factor=0.25)})
    res = loads.heating_load(env, sc)
    dt = sc.design.heating_delta_t
    expected = 0.5 * 100.0 * dt * (38.0 / 57.0) + 0.5 * 100.0 * dt * 0.25
    assert abs(res.conduction_btuh - expected) < 1e-6


def test_ceiling_uses_the_attic_policy_not_outdoor_air():
    env = _envelope([geometry.Surface("ceiling", 100.0, "attic")])
    sc = _sidecar(assemblies={"ceiling": 0.03},
                  spaces={"attic": spaces.SpacePolicy("attic", vented=False)})
    res = loads.heating_load(env, sc)
    assert abs(res.conduction_btuh - 0.03 * 100.0 * sc.design.heating_delta_t * 0.5) < 1e-6


def test_vented_attic_restores_full_outdoor_delta_t():
    env = _envelope([geometry.Surface("ceiling", 100.0, "attic")])
    sc = _sidecar(assemblies={"ceiling": 0.03},
                  spaces={"attic": spaces.SpacePolicy("attic", vented=True)})
    res = loads.heating_load(env, sc)
    assert abs(res.conduction_btuh - 0.03 * 100.0 * sc.design.heating_delta_t) < 1e-6


def test_buffer_floor_u_falls_back_through_exposed_floor_to_floor():
    env = _envelope([geometry.Surface("buffer_floor", 100.0, "crawlspace")])
    sc = _sidecar(assemblies={"floor": 0.02})     # neither buffer_floor nor exposed_floor
    res = loads.heating_load(env, sc)
    assert res.conduction_btuh > 0.0


def test_surface_without_space_keeps_the_plain_outdoor_delta_t():
    """Walls and windows carry no space and must be untouched by this change."""
    env = _envelope([geometry.Surface("exterior_wall", 100.0)])
    sc = _sidecar(assemblies={"exterior_wall": 0.08})
    res = loads.heating_load(env, sc)
    assert abs(res.conduction_btuh - 0.08 * 100.0 * sc.design.heating_delta_t) < 1e-6
```

`test_loads.py` has a `_sc()` helper but builds envelopes inline, and does not import `spaces`. Add the import (`from eldr import loads, geometry, sidecar, spaces`) and these two helpers alongside the existing `_sc()`:

```python
def _envelope(surfaces):
    return geometry.Envelope(surfaces=list(surfaces), volume_ft3=0.0)


def _sidecar(assemblies, spaces=None, cooling=None):
    return sidecar.SideCar(
        assemblies=dict(assemblies),
        design=sidecar.DesignConditions(indoor_heating_f=70, outdoor_heating_99_f=13,
                                        supply_air_rise_f=50),
        infiltration_ach=0.0,          # isolate conduction — no infiltration term
        spaces=dict(spaces or {}),
        cooling=cooling,
    )
```

Note `_sidecar` shadows the `spaces` module inside its own body via the parameter name; that is contained to the signature, and the tests below pass `spaces=` as a keyword. Keep the module import name `spaces` for use in the test bodies themselves.

- [ ] **Step 2: Run to verify failure**

Run: `ws test eldr -k "buffer_floor or attic_policy or vented_attic"`
Expected: FAIL — `KeyError: no assembly U-value for category 'buffer_floor'`.

- [ ] **Step 3: Implement**

In `eldr/loads.py`:

```python
from eldr import geometry, sidecar, spaces, units

# Surfaces whose U-value may borrow a related category's assembly when unset.
_U_FALLBACKS = {
    BUFFER_WALL_CATEGORY: ("exterior_wall",),
    "buffer_floor": ("exposed_floor", "floor"),
    "exposed_floor": ("floor",),
}
```

Replace `_u_value`:

```python
def _u_value(category, assemblies):
    """U-value for a surface category, borrowing a related assembly when unset."""
    if category in assemblies:
        return assemblies[category]
    for alt in _U_FALLBACKS.get(category, ()):
        if alt in assemblies:
            return assemblies[alt]
    raise KeyError(f"no assembly U-value for category '{category}' in side-car")
```

Change `_conduction` to pass the whole surface:

```python
    for s in surfaces:
        u = _u_value(s.category, assemblies)
        q = u * s.area_ft2 * dt_for(s)
```

Replace both ΔT resolvers so they read `Surface.space`:

```python
def _heating_dt_for(design, declared_spaces) -> Callable[[geometry.Surface], float]:
    """ΔT resolver for heating: ground ΔT below grade, the space's own factor for any
    surface facing a buffer space, outdoor-air ΔT elsewhere."""
    air, ground = design.heating_delta_t, design.ground_heating_delta_t
    indoor, outdoor = design.indoor_heating_f, design.indoor_heating_f - air

    def dt(s):
        if s.category in GROUND_COUPLED_CATEGORIES:
            return ground
        if s.space is not None:
            policy = spaces.policy_for(s.space, declared_spaces)
            return spaces.heating_factor(policy, indoor, outdoor) * air
        if s.category == BUFFER_WALL_CATEGORY:
            return BUFFER_FACTOR * air
        return air
    return dt


def _cooling_dt_for(design, cooling, declared_spaces) -> Callable[[geometry.Surface], float]:
    """ΔT resolver for cooling. A space's factor may exceed 1: a sun-heated attic runs
    hotter than outdoor air, so its ceiling sees a LARGER ΔT than the outdoor design one."""
    air = cooling.cooling_delta_t
    ground = max(0.0, design.ground_temp_f - cooling.indoor_f)
    indoor, outdoor = cooling.indoor_f, cooling.indoor_f + air

    def dt(s):
        if s.category in GROUND_COUPLED_CATEGORIES:
            return ground
        if s.space is not None:
            policy = spaces.policy_for(s.space, declared_spaces)
            return spaces.cooling_factor(policy, indoor, outdoor) * air
        if s.category == BUFFER_WALL_CATEGORY:
            return BUFFER_FACTOR * air
        return air
    return dt
```

Update all four call sites (`heating_load`, `cooling_load`, and both in `per_room_loads`) to pass `sc.spaces`.

- [ ] **Step 4: Run the full suite**

Run: `ws test eldr`

Ceiling loads now use the attic policy (0.5 by default) instead of full outdoor ΔT, so fixtures asserting ceiling conduction will shift. This is the intended, owner-approved change. Update each affected expectation and note in a comment that the ceiling faces an unvented attic at `UNVENTED_FACTOR`.

- [ ] **Step 5: Commit**

`.commits/eldr-loads-spaces.md`, message `feat(eldr): surfaces take ΔT from their buffer space`.

---

### Task 7: Hot-attic cooling — `cooling.attic_temp_f` and sol-air

**Files:**
- Modify: `eldr/sidecar.py` (the `cooling` block), `eldr/spaces.py` (sol-air default)
- Modify: `eldr/tests/test_spaces.py`, `eldr/tests/test_sidecar.py`, `eldr/tests/test_loads.py`

**Interfaces:**
- Consumes: `spaces.cooling_factor`, `Cooling`.
- Produces: `sidecar.Cooling.attic_temp_f: float | None`; `spaces.sol_air_attic_temp_f(outdoor_f, absorptance=0.85) -> float`.

- [ ] **Step 1: Write the failing tests**

```python
def test_sol_air_attic_is_much_hotter_than_outdoor():
    t = spaces.sol_air_attic_temp_f(outdoor_f=89.0)
    assert 115.0 <= t <= 145.0


def test_explicit_attic_temp_overrides_sol_air(tmp_path):
    sc = _write_and_load(tmp_path, BASE_SIDECAR + textwrap.dedent("""\
        cooling:
          indoor_f: 75
          outdoor_1_f: 89
          shgc: 0.3
          occupants: 4
          attic_temp_f: 130
        """))
    assert sc.cooling.attic_temp_f == 130.0
```

Plus this loads test, pinning that the ceiling uses the attic's ΔT rather than the outdoor one:

```python
def test_ceiling_cooling_uses_attic_temp_not_outdoor():
    """The hot-attic correction: at 130°F attic vs 89°F outdoor and 75°F indoors, the
    ceiling sees (130-75) instead of (89-75) — nearly 4x the gain. This is the
    373 -> ~4,400 BTU/hr gap against the professional Manual J."""
    env = _envelope([geometry.Surface("ceiling", 100.0, "attic")])
    sc = sidecar.SideCar(
        assemblies={"ceiling": 0.03},
        design=sidecar.DesignConditions(70, 15, 50),
        infiltration_ach=0.0,
        cooling=sidecar.Cooling(indoor_f=75, outdoor_1_f=89, shgc=0.3, occupants=0,
                                attic_temp_f=130.0),
    )
    res = loads.cooling_load(env, sc)
    assert res.by_category["ceiling"] == pytest.approx(0.03 * 100.0 * (130.0 - 75.0))
```

- [ ] **Step 2: Run to verify failure**

Run: `ws test eldr -k "sol_air or attic_temp"`
Expected: FAIL — attribute and function do not exist.

- [ ] **Step 3: Implement**

Add to `eldr/spaces.py`:

```python
# A sun-heated attic runs far above outdoor air. Demo-grade: a flat solar uplift scaled
# by roof absorptance, NOT a real energy balance (no roof area, ventilation rate or
# radiant barrier). Dark asphalt shingle ~0.85. Refrhus carries PV over part of its
# roof, which shades the deck beneath — see the design doc's deferred note; until roof
# planes are modeled, set cooling.attic_temp_f to a blended observed value instead.
SOL_AIR_UPLIFT_F = 50.0
DEFAULT_ROOF_ABSORPTANCE = 0.85


def sol_air_attic_temp_f(outdoor_f: float, absorptance: float = DEFAULT_ROOF_ABSORPTANCE) -> float:
    """Estimated peak attic air temperature from roof solar gain."""
    return outdoor_f + SOL_AIR_UPLIFT_F * absorptance
```

In `sidecar.py`, add `attic_temp_f: float | None = None` to `Cooling`, parse it with `_optional_number`, and validate it is finite and above `indoor_f` when set.

In `loads._cooling_dt_for`, before consulting the declared policy, resolve the attic's summer temperature: if the space is `attic` and no explicit `summer_temp_f` is declared, use `cooling.attic_temp_f` when set, else `spaces.sol_air_attic_temp_f(outdoor)`. Implement by constructing an effective policy:

```python
        if s.space == "attic" and policy.summer_temp_f is None:
            attic_f = (cooling.attic_temp_f if cooling.attic_temp_f is not None
                       else spaces.sol_air_attic_temp_f(outdoor))
            policy = dataclasses.replace(policy, summer_temp_f=attic_f)
```

- [ ] **Step 4: Run the suite**

Run: `ws test eldr`
Expected: pass, with cooling ceiling figures rising sharply — that is the 373 → ~4,400 BTU/hr correction this task exists for.

- [ ] **Step 5: Commit**

`.commits/eldr-hot-attic.md`, message `feat(eldr): hot-attic cooling gain via sol-air or an explicit temperature`.

---

### Task 8: Reporting — echoes and the void warning

**Files:**
- Modify: `eldr/report.py`, `eldr/overview.py`
- Modify: `eldr/tests/test_report.py`, `eldr/tests/test_overview.py`

**Interfaces:**
- Consumes: `Envelope.voids`, `Surface.space`, `SideCar.spaces`, `SideCar.levels`.
- Produces: report sections only; no new public functions.

- [ ] **Step 1: Write the failing tests**

`report.render_heating` currently takes a `HeatingResult` and a `SideCar` but never the `Envelope`, so it has no access to voids, level heights or per-surface spaces. Add an optional `env: geometry.Envelope | None = None` keyword — optional so every existing call site and test keeps working, and the new sections simply don't render without it.

Add to `eldr/tests/test_report.py`, reusing its `_sc()` / `_result()` helpers. Extend its import line to include `spaces` (it already imports `geometry` and `sidecar`):

```python
def _env(surfaces=(), voids=None, level_heights=None):
    return geometry.Envelope(
        surfaces=list(surfaces), volume_ft3=10000.0,
        voids=dict(voids or {}), level_heights_ft=dict(level_heights or {}))


def test_report_warns_about_void_floor_area_by_room():
    env = _env(voids={"Main Bed": 120.1, "Kitchen": 12.8})
    md = report.render_heating(_result(), _sc(), env=env)
    assert "no level drawn beneath" in md
    assert "Main Bed" in md and "120.1" in md
    assert "132.9" in md                      # the total, thousands-separated if needed


def test_report_omits_the_void_warning_when_there_are_none():
    md = report.render_heating(_result(), _sc(), env=_env())
    assert "no level drawn beneath" not in md


def test_report_echoes_buffer_space_factors():
    sc = sidecar.SideCar(
        assemblies={"exterior_wall": 0.1, "buffer_floor": 0.5},
        design=sidecar.DesignConditions(70, 13, 50),
        infiltration_ach=0.5,
        spaces={"crawlspace": spaces.SpacePolicy("crawlspace", winter_temp_f=32.0)},
    )
    env = _env(surfaces=[geometry.Surface("buffer_floor", 100.0, "crawlspace")])
    md = report.render_heating(_result(), sc, env=env)
    assert "crawlspace" in md
    assert "32" in md            # the temperature the factor came from
    assert "0.67" in md          # (70-32)/(70-13), rounded for display


def test_report_echoes_level_heights():
    env = _env(level_heights={"Main": 8.0})
    md = report.render_heating(_result(), _sc(), env=env)
    assert "Main" in md and "8.0" in md
```

Both `Envelope.voids` and `Envelope.level_heights_ft` were added in Task 5; this task only reads them.

- [ ] **Step 2: Run to verify failure**

Run: `ws test eldr -k "void or echoes"`
Expected: FAIL — substrings absent.

- [ ] **Step 3: Implement**

Add three blocks to `report.render`:

- **Levels**: name, height used, `(model)` or `(side-car override)`, and volume contributed.
- **Buffer spaces**, whenever any surface carries a space: the space, its resolved heating and cooling factors, and the input they came from (`winter_temp_f 32°F`, `factor 0.25`, `vented: false`).
- **Void warning**, whenever `Envelope.voids` is non-empty: total area, then the rooms sorted by area descending. Phrase it as a schematic gap, e.g.

  ```
  ⚠ 132.9 sqft of conditioned floor has no level drawn beneath it; modeled as buffer floor over `crawlspace`.
    Largest: Main Bed 120.1 sqft, Kitchen 12.8 sqft.
    Draw those spaces in SH3D to replace the assumption with geometry.
  ```

Mirror all three into `overview.py`'s narrative, which renders from the same `cli.analyze` pipeline, so the write-up cannot drift from the engine.

- [ ] **Step 4: Run the suite**

Run: `ws test eldr`

- [ ] **Step 5: Commit**

`.commits/eldr-report-echoes.md`, message `feat(eldr): report level heights, space factors and schematic voids`.

---

### Task 9: JSON export, README, example side-car

**Files:**
- Modify: `eldr/jsonexport.py`, `README.md`, `eldr/example-sidecar.yaml`
- Modify: `eldr/tests/test_jsonexport.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `levels`, `spaces` and `voids` keys in the JSON payload; `Surface.space` on exported surfaces.

- [ ] **Step 1: Write the failing test**

`test_jsonexport.py` already has `_paths(tmp_path, sidecar_body)` returning `(home, sidecar)` paths and drives the real pipeline through `cli.analyze`. Follow that, using a two-level fixture so there is something to resolve:

```python
def test_json_carries_levels_spaces_and_voids(tmp_path):
    home = tmp_path / "Home.xml"
    home.write_text(MULTI_LEVEL_FIXTURE)          # copy from test_geometry.py
    sc = tmp_path / "sc.yaml"
    sc.write_text(SIDECAR + "spaces:\n  attic:\n    vented: false\n")
    a = cli.analyze(str(home), str(sc))
    payload = json.loads(jsonexport.render_json(a))

    assert payload["levels"]["Main"]["height_ft"] > 0
    assert payload["spaces"]["attic"]["heating_factor"] == 0.5
    assert "voids" in payload                     # present even when empty
    ceiling = next(s for s in payload["surfaces"] if s["category"] == "ceiling")
    assert ceiling["space"] == "attic"
```

`MULTI_LEVEL_FIXTURE` is defined in `test_geometry.py` in Task 1. Rather than duplicating it, move it into a shared `eldr/tests/fixtures.py` when this task needs it, and import it from both test modules — a third copy is the point at which duplication stops being cheaper than a module.

- [ ] **Step 2: Run to verify failure**

Run: `ws test eldr -k test_json_carries`
Expected: FAIL — `KeyError: 'voids'`

- [ ] **Step 3: Implement**

`analysis_to_dict` currently exports `design`, `station`, `infiltration_ach`, `heating`, `cooling`, `equipment_sizing`, `rooms` and the duct keys — there is **no** surfaces array yet. Add four new top-level keys and leave every existing key and shape untouched, since the export is consumed downstream:

- `"levels"`: `{level_name: {"height_ft": float}}` from `Envelope.level_heights_ft`.
- `"spaces"`: `{space_name: {"heating_factor": float, "cooling_factor": float}}` for every space actually referenced by a surface, resolved through `spaces.policy_for`.
- `"voids"`: `{room_name: area_ft2}` from `Envelope.voids` — present even when empty, so a consumer can distinguish "no gaps" from "old export".
- `"surfaces"`: `[{"category": str, "area_ft2": float, "space": str | None}, ...]` from `Envelope.surfaces`. New, and the reason the whole-house horizontal split becomes inspectable without re-deriving it.

- [ ] **Step 4: Update the docs**

In `README.md`:

- Add `levels` and `spaces` rows to the side-car table.
- Rewrite the "How geometry maps to loads" bullets for ceilings and floors: state that adjacency comes from XY overlap with elevation only ordering, that roomless levels are scaffolding (with the roomless-model exception), and that a void below is modeled as buffer floor and reported.
- Extend the buffer-walls bullet to mention per-space policies and the temperature-first precedence.
- Update "Scope & roadmap": move partial ceilings, exposed floors and hot-attic gain into Built.

In `eldr/example-sidecar.yaml`, add commented `levels:` and `spaces:` blocks and the `buffer_floor` / `exposed_floor` assembly keys, each with a one-line explanation.

- [ ] **Step 5: Run the full suite**

Run: `ws test eldr`
Expected: all green.

- [ ] **Step 6: Verify against the real model**

```bash
components/eldr/.venv/bin/python -m eldr.cli hoards/refrhus/Refrhus.sh3d hoards/refrhus/eldr-sidecar.yaml
```

Sanity-check against the design doc's acceptance targets: ceiling area near 976 ft² total (≈517 second floor + ≈459 to attic), crawl-facing floor near 122 ft², and a reported void near 120 ft² dominated by Main Bed. The **Kitchen void is expected to be absent**, not present: the shipped resolver settles voids by a morphological opening measured against the void's own boundary, which dissolves scatter that small entirely. That is a deliberate trade recorded in the design doc's *What actually shipped* section — it removed a systematic understatement of buffer-floor area at the cost of the small-scatter end of the itemization — so this criterion is met by the Kitchen gap NOT appearing. Figures will not match exactly — the side-car still carries the old assemblies until the hoard is updated — but the *areas* should land close. Investigate any that do not.

- [ ] **Step 7: Commit**

`.commits/eldr-docs-json.md`, message `docs(eldr): document the level-stack model; export levels, spaces and voids`.

---

## After the plan

The engine work ends here. Two follow-ups belong to the hoard, not this branch, and should be a separate change on `Cervator/refrhus`:

- Adopt the professionals' measured assemblies in `hoards/refrhus/eldr-sidecar.yaml` — mixed window U-values, R-11/R-19 ceilings, the U-0.521 uninsulated `buffer_floor`, `spaces.crawlspace.winter_temp_f: 32` — and commit the four currently-untracked files (`eldr-sidecar.yaml`, `eldr-report.md`, `eldr-overview.md`, `eldr-vs-manualj-2026-08.md`).
- Re-run the comparison and update `eldr-vs-manualj-2026-08.md` with a new column. Expect the floor and ceiling lines to move most; expect the heating ceiling line to sit *below* the professionals' because of the deliberate unvented-attic divergence recorded in the design doc.
