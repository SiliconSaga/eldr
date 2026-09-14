# Per-surface assemblies — design

**Date:** 2026-08-18
**Status:** implemented — the engine, reporting and JSON export described below ship in this repository. Two items in *Deferred* remain deferred, and the `tag.py` writer lives in the Yggdrasil workspace rather than here (see the README).
**Predecessor:** `2026-08-13-level-stack-model-design.md`

## Why

`assemblies` holds one U-value per surface category, house-wide. Real houses are mixes. Refrhus carries 247.9 ft² of bare R-0 stud wall among 1,320.7 ft² of insulated wall, and 27.2 ft² of single-pane glazing among 152.5 ft² of glass — both measured, both on the professionals' Construction Details page.

A blended U gets the whole-house total right and every per-room number wrong. That was tolerable while Eldr reported one figure. It is not tolerable now, because the next feature is **Manual T** — register throw and spread against each room's design CFM — and Manual T exists to answer a live disagreement with an HVAC contractor about whether the south side of the second floor needs supply at all. The error a blended U introduces is not uniform noise: it concentrates in exactly the rooms under dispute, because those are the rooms with the old glazing and the uninsulated wall.

So per-room loads have to be able to say *this* wall is the uninsulated one before Manual T is worth building on top of them.

## What the file format actually supports

Verified against the mirrored upstream source rather than assumed, because the whole design rests on it.

**Arbitrary properties are legal on every object we care about.** `HomeXMLHandler.java:366` declares `<!ELEMENT wall (property*, texture?, texture?, baseboard?, baseboard?)>`, and the same `property*` appears on `home`, `level`, `room`, `pieceOfFurniture` and `doorOrWindow`. The element itself is `<!ELEMENT property EMPTY>` with `name`, `value` and an optional `type` of `STRING|CONTENT` (`:92`).

**They survive Sweet Home 3D's own open→save cycle.** This is the load-bearing fact. `HomeXMLHandler.java:1840` reads properties back generically with `object.setProperty(key, value)`, and `HomeXMLExporter.java:621` writes them back with `writeProperties(writer, wall)` — which iterates `object.getPropertyNames()` and emits every one (`:859`). Nothing is filtered against a known-key list in either direction. A property Eldr's tooling writes is still there after the owner opens the model, moves a wall and saves.

They are also written **sorted by name** (`:861`), so they are diff-stable and normalize-clean.

**They survive our own normalize step.** `normalize.sh` strips only lines matching `<property name='com.eteks.sweethome3d.(SweetHome3D|swing).`, the editor's window and viewport state. Any other namespace passes through untouched.

**But the Sweet Home 3D UI cannot edit them.** The only class in `com/eteks/sweethome3d/swing/` that touches `setProperty`/`getPropertyNames` is `PlanComponent.java`, which renders. Custom properties are a plugin-API surface, not a user-facing one. There is no dialog.

**And walls have no human-editable text at all.** The full wall attribute list (`:367`–`:385`) is ids, endpoints, heights, thickness, arc extent, pattern and colours. No `name`, no `description`.

**Furniture does.** `doorOrWindow` and `pieceOfFurniture` carry a `name` attribute, editable directly in the furniture list — the model already has `name='Door frame'` on one. Rooms carry `name` too.

That asymmetry decides the design: **windows and doors can be tagged by a human today with no tooling at all; walls need a writer.**

## Design

### Named assembly variants

A category key may carry a `/`-suffixed variant:

```yaml
assemblies:
  exterior_wall: 0.097          # the category default — unchanged meaning
  window: 0.550
  exterior_wall/r0: 0.240       # 2x4, R-0 cavity, R-0 board — the uninsulated sections
  window/single: 0.900          # clear single pane
  window/storm: 0.570           # single pane with storm
```

The text before the `/` names the category, so there is no separate `category:` field and validation falls out for free: a variant whose prefix is not a known category is an error at load.

Loading needs no schema change. `sidecar.py:247` already builds `assemblies` from every key present and validates U ≥ 0 — these keys parse today and are simply never consulted. The change is entirely in resolution.

> **Standing constraint — never substring-match an assembly key.** `exterior_wall/r0` *contains* `exterior_wall`, which is structurally the same trap as `"floor"` matching inside `"buffer_floor"` from the level-stack work. Every lookup splits on the first `/` and compares the parts exactly. This is a rule about how the code is written, not something a test can be relied on to catch, because the fixture that would catch it is the fixture nobody writes.

### Where a tag comes from

| Source | Written by | Applies to |
|---|---|---|
| `<property name='eldr.assembly' value='exterior_wall/r0'/>` | the tagging tool | walls, rooms, windows, doors |
| a bracketed token in the furniture `name` — `Bedroom window [window/single]` | **the owner, in SH3D's furniture list** | windows, doors |

Property wins when both are present.

**One property, any number of categories.** The value is a whitespace-separated list of assembly keys, and each key applies to the surfaces *of its own category* that the tagged object produces. This matters because objects do not map one-to-one onto surfaces: a wall yields wall surfaces, but a **room** yields a ceiling *and* a floor, and those are different assemblies. Because the category is already inside the key, one property disambiguates without needing a property name per category:

```xml
<room id='room-...' name='Play Room'>
  <property name='eldr.assembly' value='ceiling/r19 buffer_floor/uninsulated'/>
</room>
```

A key naming a category the object does not produce is a warning, not a silent no-op — that is how a `ceiling/r19` left on a basement room gets noticed.

Room tagging is not a nicety. The professionals' ceilings are three assemblies — 40.4 ft² at R-11, 541.2 ft² at R-13 and 965.7 ft² at R-19 — and Eldr resolves ceilings per room, so a room tag is the natural and only place that mix can be expressed. Ceilings are the second-largest remaining gap line at 3,458 BTU/hr.

The bracketed form exists because it is the only thing a human can do inside the application. It is made safe by being self-validating: a bracketed token counts as a tag **only if it contains a `/` and its prefix is a known category.** So `Window [kitchen]` is ordinary naming and is ignored in silence, `[window/single]` is a tag, and `[window/bogus]` warns — a known category with an undeclared variant is exactly the case worth complaining about.

The property name is `eldr.assembly`. It must not begin with `com.eteks.sweethome3d.` or `normalize.sh` will strip it.

### The tag selects a U, never a category

A tag chooses which U-value applies **within the category the geometry already resolved**. It cannot make a wall exterior, interior or buffer — `walls: {boundary: …}` remains the only mechanism for that, and it stays keyed by wall id.

Keeping the two orthogonal means a mis-tag can change a number but can never silently move a surface into or out of the envelope. It also preserves the founding separation: Sweet Home 3D owns geometry, the side-car owns thermal properties, and a tag is a pointer from one to the other rather than a third source of truth.

A tag whose category disagrees with its surface — a wall tagged `window/single` — is an error, reported and ignored, not silently applied.

### The resolution seam

Three small changes:

1. `geometry.Surface` gains `assembly: str | None`, alongside the existing per-surface `space` field it already carries for the same reason (`geometry.py:59`).
2. `geometry` populates it while parsing walls and openings, from the property or the bracketed name.
3. `loads._conduction` resolves per surface instead of per category. Today it calls `_u_value(s.category, assemblies)` (`loads.py:128`); it will resolve `s.assembly` first and fall back to `s.category`, preserving the existing borrow chain (`loads.py:144`) untouched for the fallback path.

`by_category` keeps aggregating by **category**. Every existing report table, every JSON key and every archived comparison stays directly comparable across this change; variants surface only in the new coverage block. That is deliberate — a feature that silently re-shaped the headline tables would make the run archive in `hoards/refrhus/hvac/eldr-runs/` incomparable across the boundary, and that archive is the project's memory.

### Assembly coverage report

A new echo block, in the same family as *Borrowed U-values*, *Schematic gaps*, *Buffer spaces* and *Level heights* — the assumption-disclosure pattern the level-stack work established.

```
### Assembly coverage

| Category      | Variant             |     Area | Surfaces |
|---------------|---------------------|---------:|---------:|
| exterior_wall | exterior_wall/r0    |    247.9 |        1 |
| exterior_wall | (untagged → 0.097)  |  1,072.8 |       14 |
| window        | window/single       |     18.1 |        3 |
| window        | window/storm        |      9.1 |        1 |
| window        | (untagged → 0.550)  |    119.1 |       10 |
```

This is the metadata-loss detector. There is no way to know a wall *used to be* tagged — when a wall is redrawn its id and its properties go with it — so the report cannot say "you lost a tag." What it can do is show the tagged area per variant, so a variant that quietly drops from 247.9 ft² to 120 ft² is visible on the next run. Paired with the existing stale-id warning at `geometry.py:472`, that covers both halves of the drift problem: a side-car entry pointing at a wall that no longer exists, and a wall that no longer points at its assembly.

Hard warnings alongside it: a tag naming an undeclared assembly key, and a tag whose category does not match its surface.

**Deliberately not included:** an `expect:` block pinning declared areas and warning on drift. It is a real idea and it is YAGNI until the coverage table has been lived with.

### The tagging tool

Lives in `realms/realm-siliconsaga/sweethome3d/` beside `pack.sh` / `unpack.sh` / `normalize.sh`. **Not in Eldr** — "the engine is UI-agnostic and never writes the model" is a stated invariant in the component README, it is why Eldr can be pointed at anyone's house without risk, and this feature is not worth spending it.

Two verbs:

- `list` — every wall and room with the context a human actually navigates by: for a wall, its level, length, orientation and the rooms it borders; for a room, its level, name and area. Each shows its current tag. The object id is present but is never what the human reads.
- `set` — stamps `eldr.assembly` on a wall or room chosen by index from that listing.

Windows and doors are deliberately **not** `set` targets. They are taggable by name in Sweet Home 3D itself, which is strictly better — the owner can see which window they are naming.

Idempotent, preserves everything else in the file, and leaves output that `normalize.sh` is happy with. Indices come from the immediately preceding `list` and are not stable across edits; that is acceptable for an interactive tag-then-verify loop and should be stated plainly in the help text rather than engineered around.

## Documentation and skills

The point of this section is that the schematic has outgrown what a person can hold in their head, and the agent is now the one expected to carry the whole picture. That makes the documentation a deliverable of this design, not follow-up work.

**`realms/realm-siliconsaga/.agent/skills/sweethome3d/SKILL.md`** — add the property facts established above (legal on wall/room/furniture/level, round-trip through SH3D, survive `normalize.sh`, not editable in the UI, walls have no name), the two tagging conventions, and the tagging tool. This is the skill an agent loads before touching a `.sh3d`, so it is where "you cannot type a tag onto a wall in the app" has to live.

**New skill — the envelope-reasoning gap.** Nothing currently tells an agent *what belongs in the thermal envelope*, and that is precisely the knowledge this session kept needing: the garage window sits on unconditioned space and must not count; a bricked-in basement window is opaque wall and should not be drawn as glass; interior doors do not belong to the envelope; a window in a well is thermally a window but gets no solar. That reasoning currently exists only in conversation. It should be a skill covering the operating loop end to end — edit the model, unpack, run Eldr, read the echo blocks, compare against the professionals' report, and know which divergences are deliberate.

**`components/eldr/README.md`** — a per-surface assemblies section covering variants, both tag sources, precedence, and the coverage block. The README is the engine's reference document and is already thorough; this extends it rather than restructuring it.

**`realms/realm-siliconsaga/sweethome3d/README.md`** — the tagging tool alongside the existing scripts.

**`hoards/refrhus/hvac/README.md`** — the divergence register is excellent and has gone partly stale. Occupants are reconciled at 5; the slab is corrected to U-0.020 and the line reading 1,866 against 807 is now 746; the sensible-cooling-infiltration bug it files as open was fixed in the `2026-08-14c` run. Most importantly its ceiling row attributes part of the gap to "our unvented-attic default halves the heating ΔT where they take the full one" — which reads as an Eldr error and is not one. **The attic is genuinely unvented, and Eldr already models the seasonal asymmetry correctly**: the `vented: false` shorthand gives a winter factor of 0.50, while summer is resolved from a sol-air attic temperature for a factor of 3.66. Being unvented helps in winter and hurts in summer, and the engine says so. The divergence from the professionals is deliberate and observation-backed, and the register should record it the way it already records the crawlspace temperature.

## Phasing

1. **Engine** — variant resolution, the `Surface.assembly` field, the coverage report, warnings. Fixture-driven, no model writes, no new dependencies.
2. **Tagging tool** — `list` and `set` in the realm sh3d tooling.
3. **Tag Refrhus** — the four un-replaced windows by furniture name, the R-0 wall sections by property. Re-run, archive the report, update the divergence register.
4. **Documentation and skills** — as above.

Independent of all four, and worth doing whenever convenient: the **schematic door cleanup**. Eldr counts seven doors totalling 132 ft² where the house has four envelope doors and the professionals counted 82.3 ft². Two of Eldr's are 8.1 and 6.4 ft², which is not door-sized. The garage door belongs outside the envelope entirely. This is model work with no engine dependency, and it currently pushes the door line 675 BTU/hr *above* the professionals' — the only line where Eldr overshoots.

## Testing hazards

The level-stack branch hit the same defect at least ten times: *a test asserting a value that a plausible wrong implementation would also produce.* The specific traps this feature invites:

- **A fixture whose variant U equals its category default.** It passes whether resolution works or not. Every fixture must use a variant U that differs unmistakably — a 2.5× difference, not a 5% one.
- **A fixture where only one surface exists in a category.** Tagged-versus-untagged aggregation cannot be wrong when there is nothing to aggregate against. Categories under test need at least one tagged and one untagged surface.
- **Substring collision.** A test that tags `exterior_wall/r0` and asserts the total will pass even if the lookup accidentally matched on the `exterior_wall` prefix, because both resolve to *a* number. Assert the resolved U per surface, not just the total.
- **The bracketed-name rule.** `[kitchen]` must be ignored and `[window/bogus]` must warn. A test covering only the happy path proves neither.

Verification means mutating the implementation and watching the test fail, and **mutation runs must be full-suite, never `-k`-filtered** — filtering to the new tests answers "does my test fail?" while appearing to answer "was this uncaught before?"

## Scope and deferred

- No grade-line split. `basement_wall` stays a single category at a single U until it exists; that remains the largest structural gap at 4,901 BTU/hr.
- No partial-height or partial-length splits within one wall. A wall is one assembly.
- No inference. Nothing guesses that a wall is uninsulated; a tag is always explicit.
- The tag cannot change a surface's category, only its U-value.
- `expect:` coverage pinning, as noted above.
