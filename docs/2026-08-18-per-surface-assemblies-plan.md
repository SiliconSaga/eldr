# Per-surface assemblies — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an individual wall, room, window or door declare which assembly it is, so per-room loads stop assuming one U-value per category house-wide.

**Architecture:** A side-car assembly key may carry a `/`-suffixed variant (`exterior_wall/r0`). Objects in `Home.xml` point at one via an `eldr.assembly` property, or — for furniture — a bracketed token in the editable `name`. `geometry.Surface` gains an `assembly` field; `loads._conduction` resolves it before falling back to the category. Aggregation stays keyed by category so every existing table and archived run remains comparable.

**Tech Stack:** Python 3.11+, stdlib + `pyyaml` + `defusedxml`. Tests are pytest via `ws test eldr`. The tagging tool is bash + python in the realm sh3d tooling.

**Spec:** `components/eldr/docs/2026-08-18-per-surface-assemblies-design.md`

## Global Constraints

- **Never substring-match an assembly key.** Split on the first `/` and compare parts exactly. `exterior_wall/r0` contains `exterior_wall`; this is the `"floor"`-inside-`"buffer_floor"` trap from the level-stack work.
- **Eldr never writes the model.** All tagging is read-only from the engine's side; the writer lives in the realm tooling.
- **A tag selects a U within an already-resolved category.** It can never change what kind of surface something is. `walls: {boundary: …}` remains the only category-changing mechanism.
- **`by_category` aggregation stays keyed by category**, so existing report tables, JSON keys and archived runs stay comparable across this change.
- **Property name is `eldr.assembly`.** It must never begin with `com.eteks.sweethome3d.` or `normalize.sh` strips it.
- Fixtures must use variant U-values that differ from their category default by a wide margin (2×+), never a few percent.
- Run the suite with `ws test eldr`. Mutation checks must be **full-suite, never `-k`-filtered**.

---

### Task 1: Assembly key grammar

**Files:**
- Modify: `eldr/sidecar.py`
- Test: `eldr/tests/test_sidecar.py`

**Interfaces:**
- Produces: `sidecar.CATEGORIES: frozenset[str]`; `sidecar.split_assembly_key(key: str) -> tuple[str, str | None]` returning `(category, variant)`.

- [ ] **Step 1: Write the failing tests**

```python
def test_split_assembly_key_plain_category():
    assert sidecar.split_assembly_key("exterior_wall") == ("exterior_wall", None)

def test_split_assembly_key_variant():
    assert sidecar.split_assembly_key("exterior_wall/r0") == ("exterior_wall", "r0")

def test_split_assembly_key_splits_on_first_slash_only():
    assert sidecar.split_assembly_key("window/low-e/2a") == ("window", "low-e/2a")

def test_variant_with_unknown_category_prefix_is_rejected():
    with pytest.raises(ValueError, match="unknown category"):
        sidecar.load_sidecar_dict({**MINIMAL, "assemblies": {
            "exterior_wall": 0.09, "windwo/single": 0.9}})

def test_known_category_variant_loads():
    sc = sidecar.load_sidecar_dict({**MINIMAL, "assemblies": {
        "exterior_wall": 0.09, "exterior_wall/r0": 0.24}})
    assert sc.assemblies["exterior_wall/r0"] == 0.24
```

- [ ] **Step 2: Run to verify they fail**

Run: `ws test eldr`
Expected: FAIL — `split_assembly_key` does not exist.

- [ ] **Step 3: Implement**

```python
CATEGORIES = frozenset({
    "exterior_wall", "basement_wall", "window", "door",
    "ceiling", "floor", "buffer_wall", "buffer_floor", "exposed_floor",
})

def split_assembly_key(key: str) -> tuple[str, str | None]:
    """(category, variant) for an assembly key. Splits on the FIRST '/' only.

    Never substring-match an assembly key — `exterior_wall/r0` contains
    `exterior_wall`, and matching on that is the bug this function exists to
    prevent. Callers compare the returned parts exactly.
    """
    category, sep, variant = key.partition("/")
    return (category, variant if sep else None)
```

In the validation pass, after `assemblies` is built:

```python
for key in sc.assemblies:
    category, variant = split_assembly_key(key)
    if variant is not None and category not in CATEGORIES:
        raise ValueError(
            f"assemblies['{key}']: unknown category '{category}' — a variant key is "
            f"'<category>/<name>' and the category must be one of {sorted(CATEGORIES)}")
```

- [ ] **Step 4: Run to verify they pass**

Run: `ws test eldr` — Expected: PASS

- [ ] **Step 5: Commit** via `ws commit eldr .commits/<name>.md`

---

### Task 2: Reading tags out of `Home.xml`

**Files:**
- Modify: `eldr/geometry.py`
- Test: `eldr/tests/test_geometry.py`

**Interfaces:**
- Produces: `geometry._element_assembly_tags(elem) -> list[str]`; `geometry._name_assembly_tag(name: str | None) -> list[str]`.

Both return assembly keys. Neither validates against the side-car — that is Task 6's job, so parsing stays independent of what happens to be declared.

- [ ] **Step 1: Write the failing tests**

```python
def _elem(xml):
    return ElementTree.fromstring(xml)

def test_property_tag_read():
    e = _elem("<wall><property name='eldr.assembly' value='exterior_wall/r0'/></wall>")
    assert geometry._element_assembly_tags(e) == ["exterior_wall/r0"]

def test_property_tag_whitespace_separated_list():
    e = _elem("<room><property name='eldr.assembly' "
              "value='ceiling/r19  buffer_floor/none'/></room>")
    assert geometry._element_assembly_tags(e) == ["ceiling/r19", "buffer_floor/none"]

def test_unrelated_property_ignored():
    e = _elem("<wall><property name='com.eteks.sweethome3d.SweetHome3D.FrameX' "
              "value='25'/></wall>")
    assert geometry._element_assembly_tags(e) == []

def test_bracketed_name_tag():
    assert geometry._name_assembly_tag("Bedroom window [window/single]") == ["window/single"]

def test_bracketed_token_without_slash_is_not_a_tag():
    assert geometry._name_assembly_tag("Window [kitchen]") == []

def test_bracketed_token_with_unknown_category_is_not_a_tag():
    assert geometry._name_assembly_tag("Window [wibble/single]") == []

def test_no_name_is_no_tag():
    assert geometry._name_assembly_tag(None) == []
```

- [ ] **Step 2: Run to verify they fail**

Run: `ws test eldr` — Expected: FAIL, functions undefined.

- [ ] **Step 3: Implement**

```python
ASSEMBLY_PROPERTY = "eldr.assembly"
_BRACKETED = re.compile(r"\[([^\[\]]+)\]")

def _element_assembly_tags(elem) -> list[str]:
    """Assembly keys declared by `<property name='eldr.assembly' value='...'/>`.

    The value is a whitespace-separated list, because one object can produce
    surfaces in several categories — a room has both a ceiling and a floor.
    """
    for p in elem.findall("property"):
        if p.get("name") == ASSEMBLY_PROPERTY:
            return (p.get("value") or "").split()
    return []

def _name_assembly_tag(name: str | None) -> list[str]:
    """Assembly keys declared by a bracketed token in a furniture name.

    A token counts only when it contains '/' AND its category prefix is real.
    That is what makes the convention safe to overlay on free-form names: an
    ordinary `[kitchen]` is invisible here, so nobody's naming breaks.
    """
    if not name:
        return []
    out = []
    for token in _BRACKETED.findall(name):
        token = token.strip()
        category, variant = sidecar.split_assembly_key(token)
        if variant is not None and category in sidecar.CATEGORIES:
            out.append(token)
    return out
```

- [ ] **Step 4: Run to verify they pass** — `ws test eldr`

- [ ] **Step 5: Commit**

---

### Task 3: `Surface.assembly` and U resolution

**Files:**
- Modify: `eldr/geometry.py:59` (the `Surface` dataclass), `eldr/loads.py:117` (`_conduction`), `eldr/loads.py:163` (`_u_value`)
- Test: `eldr/tests/test_loads.py`

**Interfaces:**
- Consumes: `sidecar.split_assembly_key` (Task 1).
- Produces: `geometry.Surface(category, area_ft2, space=None, assembly=None)`; `loads._u_value(surface, assemblies)` — **note the signature change from `(category, assemblies)`**.

- [ ] **Step 1: Write the failing tests**

Use a 2.5× spread so a fixture cannot pass by coincidence.

```python
ASM = {"exterior_wall": 0.10, "exterior_wall/r0": 0.25}

def test_untagged_surface_uses_category_default():
    s = geometry.Surface("exterior_wall", 100.0)
    assert loads._u_value(s, ASM) == 0.10

def test_tagged_surface_uses_its_variant():
    s = geometry.Surface("exterior_wall", 100.0, assembly="exterior_wall/r0")
    assert loads._u_value(s, ASM) == 0.25

def test_mixed_surfaces_aggregate_under_one_category():
    surfs = [geometry.Surface("exterior_wall", 100.0),
             geometry.Surface("exterior_wall", 100.0, assembly="exterior_wall/r0")]
    total, by_cat = loads._conduction(surfs, ASM, lambda s: 10.0)
    assert total == pytest.approx(100 * 0.10 * 10 + 100 * 0.25 * 10)
    assert set(by_cat) == {"exterior_wall"}          # variants do NOT split the table

def test_tag_naming_undeclared_variant_warns_and_falls_back():
    s = geometry.Surface("exterior_wall", 100.0, assembly="exterior_wall/nope")
    with pytest.warns(UserWarning, match="undeclared assembly"):
        assert loads._u_value(s, ASM) == 0.10

def test_tag_from_another_category_is_rejected():
    s = geometry.Surface("exterior_wall", 100.0, assembly="window/single")
    with pytest.warns(UserWarning, match="category"):
        assert loads._u_value(s, {**ASM, "window/single": 0.9}) == 0.10
```

- [ ] **Step 2: Run to verify they fail** — `ws test eldr`

- [ ] **Step 3: Implement**

Add the field:

```python
@dataclass(frozen=True)
class Surface:
    category: str
    area_ft2: float
    space: str | None = None
    # The assembly key this surface declared, if it declared one. Selects a U
    # *within* `category` — it never changes the category. None means "use the
    # category default", which is what every surface did before tagging existed.
    assembly: str | None = None
```

Rewrite `_u_value` to take the surface:

```python
def _u_value(surface, assemblies):
    if surface.assembly is not None:
        category, variant = sidecar.split_assembly_key(surface.assembly)
        if category != surface.category:
            warnings.warn(
                f"surface tagged `{surface.assembly}` but its category is "
                f"'{surface.category}' — a tag selects a U-value within a category, it "
                f"cannot change one; ignoring the tag", stacklevel=4)
        elif surface.assembly in assemblies:
            return assemblies[surface.assembly]
        else:
            warnings.warn(
                f"surface tagged `{surface.assembly}` but the side-car declares no such "
                f"undeclared assembly key — falling back to `{surface.category}`",
                stacklevel=4)
    return _category_u_value(surface.category, assemblies)
```

Rename the existing body to `_category_u_value(category, assemblies)`, unchanged, so the borrow chain is untouched. Update `_conduction` line 128 to `u = _u_value(s, assemblies)`.

- [ ] **Step 4: Run to verify they pass** — `ws test eldr`

- [ ] **Step 5: Mutation check.** Change `_u_value` to ignore `surface.assembly` entirely. Run the **full** suite. At least `test_tagged_surface_uses_its_variant` and `test_mixed_surfaces_aggregate_under_one_category` must fail. Revert.

- [ ] **Step 6: Commit**

---

### Task 4: Tag whole-house wall and opening surfaces

**Files:**
- Modify: `eldr/geometry.py:648` (openings), `:668` (walls)
- Test: `eldr/tests/test_geometry.py`

**Interfaces:**
- Consumes: `_element_assembly_tags`, `_name_assembly_tag` (Task 2); `Surface.assembly` (Task 3).

A wall or an opening produces surfaces in exactly one category, so it takes the **first** tag matching that category and ignores the rest.

- [ ] **Step 1: Write the failing test** — build a fixture with two exterior walls, one carrying `<property name='eldr.assembly' value='exterior_wall/r0'/>`, and assert the envelope's wall surfaces carry `assembly` on exactly one of them. Add a second test where a `doorOrWindow` is named `Window [window/single]` and assert its surface carries that assembly.

- [ ] **Step 2: Run to verify it fails** — `ws test eldr`

- [ ] **Step 3: Implement.** Add a helper and use it at both sites:

```python
def _tag_for(elem, category: str) -> str | None:
    """The one assembly key this element declares for `category`, or None.

    Property beats name. Keys for other categories are ignored here — a room
    legitimately carries several and each lands on its own surfaces.
    """
    for key in _element_assembly_tags(elem) or _name_assembly_tag(elem.get("name")):
        if sidecar.split_assembly_key(key)[0] == category:
            return key
    return None
```

At `:648`: `surfaces.append(Surface(category, area_ft2, assembly=_tag_for(dw, category)))`, and record it in a `opening_assembly[dw_id]` map for the per-room pass. At `:668`: build `wall_assembly[wid] = _tag_for(wall_by_id[wid], wall_category[wid])` during the wall walk and pass it through.

- [ ] **Step 4: Run to verify it passes** — `ws test eldr`

- [ ] **Step 5: Commit**

---

### Task 5: Tag per-room surfaces

**Files:**
- Modify: `eldr/geometry.py` — `room_gross_wall`, `room_windows`, `room_doors` accumulation (~`:565`, `:655`) and the per-room assembly loop (`:706`–`:722`)
- Test: `eldr/tests/test_geometry.py`

This is the substantive task. Per-room surfaces currently aggregate by **category** — `room_gross_wall[rid][cat]`, one `Surface("window", wtot)` for all of a room's windows. Tagging requires those to split by assembly as well, or a tagged window in a room disappears into an untagged average and the per-room number this whole feature exists for is still wrong.

**Interfaces:**
- Produces: per-room `Surface` lists whose entries carry `assembly`, with whole-house totals still equal to the sum of per-room ones.

- [ ] **Step 1: Write the failing tests**

```python
def test_room_windows_split_by_assembly():
    # a room with one tagged and one untagged window emits TWO window surfaces
    env = geometry.extract_envelope(FIXTURE_MIXED_GLAZING)
    room = next(r for r in env.rooms if r.name == "Bedroom")
    windows = [s for s in room.surfaces if s.category == "window"]
    assert sorted(s.assembly for s in windows) == [None, "window/single"]

def test_whole_house_still_equals_sum_of_rooms():
    env = geometry.extract_envelope(FIXTURE_MIXED_GLAZING)
    per_room = sum(s.area_ft2 for r in env.rooms for s in r.surfaces
                   if s.category == "window")
    whole = sum(s.area_ft2 for s in env.surfaces if s.category == "window")
    assert per_room == pytest.approx(whole)
```

The fixture must contain **at least one tagged and one untagged surface in the same category in the same room** — a category with only one surface cannot detect a broken split.

- [ ] **Step 2: Run to verify they fail** — `ws test eldr`

- [ ] **Step 3: Implement.** Re-key the three accumulators from `category` to `(category, assembly)`:

- `room_gross_wall[rid]` becomes `dict[tuple[str, str | None], float]`, fed with `wall_assembly[wid]` from Task 4.
- `room_windows[rid]` currently keys on bearing for the solar split; add a parallel `room_window_assembly[rid]: dict[str | None, float]` rather than making the bearing key a tuple — bearing and assembly are independent concerns and solar does not care about U.
- `room_doors[rid]` becomes `dict[str | None, float]`.

Then in the per-room loop, emit one `Surface` per `(category, assembly)` pair instead of one per category. **Keep the opening-netting behaviour identical**: openings still net out of gross wall in category order with the leftover spilling forward, because changing that alongside the tagging change would make a regression impossible to attribute.

Horizontals from `_horizontal_surfaces(faces[rid])` take the room element's own tags, one per category — this is what makes `ceiling/r19` on a room work.

- [ ] **Step 4: Run to verify they pass** — `ws test eldr`

- [ ] **Step 5: Mutation check.** Make the per-room emitter drop `assembly` (pass `None`). Full suite. `test_room_windows_split_by_assembly` must fail while `test_whole_house_still_equals_sum_of_rooms` still passes — proving the second test does not accidentally cover the first. Revert.

- [ ] **Step 6: Commit**

---

### Task 6: Assembly coverage report and warnings

**Files:**
- Modify: `eldr/report.py`, `eldr/loads.py`
- Test: `eldr/tests/test_report.py`

**Interfaces:**
- Produces: `loads.assembly_coverage(surfaces, assemblies) -> list[CoverageRow]` with `category`, `assembly` (None for untagged), `u_value`, `area_ft2`, `count`; `report.render_assembly_coverage(rows) -> str`.

- [ ] **Step 1: Write the failing tests** — assert a mixed surface list produces one row per `(category, assembly)`, that the untagged row reports the category default U, that rows are ordered category-then-variant with untagged last, and that a category with no tagged surfaces at all emits **no** section (the block is only interesting where there is a mix).

- [ ] **Step 2: Run to verify they fail** — `ws test eldr`

- [ ] **Step 3: Implement.** Render as designed:

```
### Assembly coverage

| Category      | Variant             |     Area | Surfaces |
|---------------|---------------------|---------:|---------:|
| exterior_wall | exterior_wall/r0    |    247.9 |        1 |
| exterior_wall | (untagged → 0.097)  |  1,072.8 |       14 |
```

Follow the existing echo blocks (*Borrowed U-values*, *Schematic gaps*) for placement and the explanatory italic paragraph beneath. That paragraph must say what the table is **for**: a redrawn wall loses its id and its properties together, so a variant's area silently shrinking is the only available signal that a tag was lost.

- [ ] **Step 4: Run to verify they pass** — `ws test eldr`

- [ ] **Step 5: Commit**

---

### Task 7: Carry assemblies into the JSON export

**Files:**
- Modify: `eldr/jsonexport.py`
- Test: `eldr/tests/test_jsonexport.py`

- [ ] **Step 1: Write the failing test** — each entry in `surfaces[]` gains an `assembly` key, `null` when untagged; a new top-level `assembly_coverage` array mirrors the report rows.

- [ ] **Step 2: Run to verify it fails** — `ws test eldr`

- [ ] **Step 3: Implement.** Add `assembly` to the surface dicts and the coverage array. Document both in the README's JSON-keys list in Task 10.

- [ ] **Step 4: Run to verify it passes** — `ws test eldr`

- [ ] **Step 5: Commit**

---

### Task 8: The tagging tool

**Files:**
- Create: `realms/realm-siliconsaga/sweethome3d/tag.sh`
- Modify: `realms/realm-siliconsaga/sweethome3d/README.md`

Runs against an **exploded** tree (`sh3d-internals/`), not a packed `.sh3d`, so it composes with the existing unpack → edit → pack loop and its output is diffable before anything is repacked.

- [ ] **Step 1: Implement `list`** — every wall and room, with level, and for walls length/orientation/bordering rooms, for rooms name/area; plus the current tag. Print a stable 1-based index per line.

- [ ] **Step 2: Verify by hand** against `hoards/refrhus/sh3d-internals`, confirming the index count matches the wall and room counts Eldr reports.

- [ ] **Step 3: Implement `set <index> <assembly-key>`** — inserts or replaces the `eldr.assembly` property child. Must preserve every other child and attribute, and emit the property sorted among siblings the way `HomeXMLExporter` does.

- [ ] **Step 4: Round-trip test** — `set` a tag, run `normalize.sh`, confirm the tag survives; `set` the same tag again and confirm the file is byte-identical (idempotent); confirm `pack.sh` → open in SH3D → save → `unpack.sh` still shows the tag. **The last one needs the owner** — it is the only step requiring the GUI.

- [ ] **Step 5: Add `--help`** stating plainly that indices come from the immediately preceding `list` and are not stable across edits.

- [ ] **Step 6: Commit** to the realm.

---

### Task 9: Tag Refrhus for real

**Files:**
- Modify: `hoards/refrhus/sh3d-internals/Home.xml`, `hoards/refrhus/eldr-sidecar.yaml`
- Create: `hoards/refrhus/hvac/eldr-runs/<date>-eldr-report-per-surface.md`

Depends on the owner's hit-list answers — several assignments are house knowledge, not derivable.

- [ ] **Step 1: Declare the variants** in the side-car from the professionals' page: `exterior_wall/r0: 0.240`, `exterior_wall/r13: 0.091`, `buffer_wall: 0.097`, `window/single: 0.900`, `window/storm: 0.570`, `ceiling/r11: 0.081`, `ceiling/r13: 0.070`, `ceiling/r19: 0.049`. Keep each category's plain key as the majority assembly.

- [ ] **Step 2: Tag the windows** by name in SH3D — owner-dependent, see the hit-list.

- [ ] **Step 3: Tag the wall sections and room ceilings** with `tag.sh` — owner-dependent.

- [ ] **Step 4: Re-run, archive the report, and update `hoards/refrhus/hvac/README.md`'s divergence register** with the new per-line gaps.

- [ ] **Step 5: Commit** to `refrhus`.

---

### Task 10: Documentation and skills

**Files:**
- Modify: `components/eldr/README.md`
- Modify: `realms/realm-siliconsaga/.agent/skills/sweethome3d/SKILL.md`
- Create: `realms/realm-siliconsaga/.agent/skills/sh3d-envelope/SKILL.md`
- Modify: `hoards/refrhus/hvac/README.md`

- [ ] **Step 1: Eldr README** — a *Per-surface assemblies* section: variant keys, both tag sources and their precedence, the room-tag-covers-several-categories rule, the coverage block, and the two new JSON keys.

- [ ] **Step 2: `sweethome3d` skill** — the verified format facts: `property*` is legal on wall/room/level/furniture; properties round-trip through SH3D's own save (`HomeXMLExporter.java:621`, `HomeXMLHandler.java:1840`); they survive `normalize.sh`; **the SH3D UI cannot edit them**; walls have no `name` so they need `tag.sh` while furniture and rooms can be tagged in the app.

- [ ] **Step 3: New `sh3d-envelope` skill** — what belongs in the thermal envelope and why, which is the knowledge that currently exists only in conversation: unconditioned space (a garage window contributes nothing); a bricked-in window is opaque wall and should not be drawn as glass; interior doors are not envelope doors; a window in a well is thermally a window with no solar; undrawn floor area makes bordering walls read as exterior; and the operating loop — edit, unpack, run Eldr, read the echo blocks, compare against the professionals' report, know which divergences are deliberate.

- [ ] **Step 4: `hvac/README.md`** — refresh the divergence register: occupants reconciled at 5, the slab corrected to U-0.020 (the 1,866-vs-807 line is now 746), the sensible-cooling-infiltration bug closed in the `2026-08-14c` run, and the ceiling row's attic claim corrected — the attic is genuinely unvented and Eldr already models the asymmetry (winter 0.50, summer sol-air 3.66), so that divergence is deliberate and observation-backed rather than an engine error.

- [ ] **Step 5: Commit** each repo separately.

---

## Self-review

**Spec coverage.** Variants → T1. Tag sources and precedence → T2, T4. Whitespace-separated multi-category values → T2, T5. Tag-selects-U-not-category → T3. Resolution seam → T3. `by_category` unchanged → T3. Coverage report → T6. JSON → T7. Tagging tool, `list`/`set`, not-for-furniture → T8. Docs and skills, including the new envelope skill and the `hvac/README.md` corrections → T10. Phasing matches the spec's four phases plus the independent door cleanup, which is owner work and lives in the hit-list rather than a task.

**Deliberately not in this plan:** the schematic door cleanup (model work, no engine dependency, owner-gated) and the `expect:` coverage-pinning block (declared YAGNI in the spec).

**Type consistency.** `split_assembly_key` returns `(category, variant|None)` and is used that way in T2, T3, T4. `Surface(category, area_ft2, space=None, assembly=None)` — keyword `assembly` throughout. `_u_value` takes a **surface**, not a category, from T3 onward; the old category-keyed body survives as `_category_u_value` and keeps the borrow chain intact.
