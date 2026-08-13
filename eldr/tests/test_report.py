import pytest
from eldr import report, loads, sidecar, sizing, ductd, ductmodel, geometry, spaces


def _sc(existing_tons=None):
    return sidecar.SideCar(
        assemblies={"exterior_wall": 0.1},
        design=sidecar.DesignConditions(70, 20, 50),
        infiltration_ach=0.5,
        existing_tons=existing_tons,
    )


def _sc_cool():
    return sidecar.SideCar(
        assemblies={"exterior_wall": 0.1},
        design=sidecar.DesignConditions(70, 20, 50),
        infiltration_ach=0.5,
        cooling=sidecar.Cooling(indoor_f=75, outdoor_1_f=90, shgc=0.35, occupants=3),
    )


def _result():
    return loads.HeatingResult(conduction_btuh=6500.0, infiltration_btuh=5400.0,
                               total_btuh=11900.0, cfm=220.4,
                               by_category={"exterior_wall": 6500.0})


def _cooling():
    return loads.CoolingResult(sensible_btuh=20000.0, latent_btuh=4000.0, total_btuh=24000.0,
                               cfm=900.0, by_category={"window": 500.0, "solar-W": 1200.0,
                                                       "internal": 1890.0})


def test_render_heating_contains_totals():
    md = report.render_heating(_result(), _sc())
    assert "# Eldr — Heating Load" in md
    assert "11,900" in md          # total, thousands-separated
    assert "220" in md             # CFM
    assert "exterior_wall" in md
    assert "ΔT" in md and "50" in md


def test_render_shows_buffer_factor_when_present():
    r = loads.HeatingResult(conduction_btuh=1000.0, infiltration_btuh=500.0,
                            total_btuh=1500.0, cfm=30.0,
                            by_category={"exterior_wall": 700.0, "buffer_wall": 300.0})
    md = report.render_heating(r, _sc())
    assert "Buffer walls" in md
    assert "50%" in md             # BUFFER_FACTOR, prominent
    assert "buffer_wall" in md


def test_render_no_buffer_line_when_absent():
    assert "Buffer walls" not in report.render_heating(_result(), _sc())


def test_render_with_sizing_shows_manual_s():
    r = _result()
    s = sizing.size_equipment(r.total_btuh, _sc(existing_tons=4.0))
    md = report.render_heating(r, _sc(existing_tons=4.0), sizing=s)
    assert "## Manual S — Equipment Sizing" in md
    assert "tons" in md
    assert "4.0" in md              # existing unit
    assert "oversized" in md
    assert "⚠" in md               # warning for non-well-matched verdict


def test_render_without_sizing_omits_manual_s():
    md = report.render_heating(_result(), _sc())
    assert "Manual S" not in md


def test_render_with_ducts_shows_manual_d():
    dr = ductd.size_ducts([("main trunk", 720), ("bigtrunk", 2000)], friction_rate=0.08)
    md = report.render_heating(_result(), _sc(), ducts=dr)
    assert "## Manual D — Duct Sizing" in md
    assert "main trunk" in md
    assert "14″" in md               # 720 CFM -> 14" standard
    assert "⚠" in md                # bigtrunk exceeds the velocity threshold


def test_render_without_ducts_omits_manual_d():
    md = report.render_heating(_result(), _sc())
    assert "Manual D" not in md


def test_render_with_cooling_shows_section():
    r, c = _result(), _cooling()
    s = sizing.size_equipment(r.total_btuh, _sc_cool(), cooling_btuh=c.total_btuh)
    md = report.render_heating(r, _sc_cool(), sizing=s, cooling=c)
    assert "Cooling Load" in md
    assert "solar-W" in md          # orientation-resolved solar line
    assert "sensible" in md and "latent" in md
    # cooling (24k) > heating (11.9k) -> Manual S sizes on cooling
    assert "cooling" in md


def test_render_without_cooling_omits_section():
    md = report.render_heating(_result(), _sc())
    assert "Cooling Load" not in md


def test_render_cooling_without_conditions_errors():
    # a CoolingResult but a side-car with no cooling block -> clear error, not AttributeError
    with pytest.raises(ValueError, match="cooling"):
        report.render_heating(_result(), _sc(), cooling=_cooling())


def _plan(unit=None, derived=False, lengths=None, runs=None):
    room_loads = [
        loads.RoomLoad("Big Room", "L1", True, 6000.0, 2000.0, 111.0),
        loads.RoomLoad("Small", "L1", True, 500.0, 100.0, 9.0),
        loads.RoomLoad("Garage", "LG", False, 3000.0, 0.0, 55.0),   # excluded from table
    ]
    return ductmodel.DuctPlan(
        unit=unit, unit_name="air handler", friction_rate=0.08, derived=derived,
        available_static_pressure=0.5 if derived else None,
        worst_length_ft=70.0 if derived else None, room_loads=room_loads,
        runs=runs or [("main trunk", 120.0), ("Big Room", 111.0), ("Small", 9.0)],
        lengths=lengths)


def test_render_per_room_section():
    md = report.render_heating(_result(), _sc(), duct_plan=_plan())
    assert "Per-Room Loads (Manual J 1c)" in md
    assert "Big Room" in md
    assert "Garage" not in md            # unconditioned room excluded from the 1c table
    assert "220 CFM" in md               # whole-house cfm from the HeatingResult, in the gap note


def test_render_manual_d_without_unit_notes_absence():
    dr = ductd.size_ducts([("main trunk", 120), ("Big Room", 111)], friction_rate=0.08)
    md = report.render_heating(_result(), _sc(), ducts=dr, duct_plan=_plan())
    assert "## Manual D" in md
    assert "No unit found" in md
    assert "not derived" in md
    assert "Length" not in md            # no length column without a unit


def test_render_manual_d_with_unit_shows_lengths_and_derivation():
    unit = geometry.Furniture("Air Handler", 0.0, 0.0, "L1")
    dr = ductd.size_ducts([("main trunk", 120), ("Big Room", 111)], friction_rate=0.08,
                          lengths=[None, 42.0])
    md = report.render_heating(_result(), _sc(), ducts=dr,
                               duct_plan=_plan(unit=unit, derived=True, lengths=[None, 42.0]))
    assert "Air handler" in md
    assert "Length" in md and "Drop" in md
    assert "42 ft" in md
    assert "derived" in md               # friction rate note shows the derivation


# ---------------------------------------------------------------- assumptions echoes

def _env(surfaces=(), voids=None, level_heights=None, level_volumes=None):
    return geometry.Envelope(
        surfaces=list(surfaces), volume_ft3=10000.0,
        voids=dict(voids or {}), level_heights_ft=dict(level_heights or {}),
        level_volumes_ft3=dict(level_volumes or {}))


def _sc_attic(attic_temp_f=None, space_policies=None):
    """A side-car with cooling, so the summer column resolves. ΔT = 91-75 = 16°F."""
    return sidecar.SideCar(
        assemblies={"exterior_wall": 0.1, "ceiling": 0.03},
        design=sidecar.DesignConditions(70, 20, 50),
        infiltration_ach=0.5,
        cooling=sidecar.Cooling(indoor_f=75, outdoor_1_f=91, shgc=0.35, occupants=3,
                                attic_temp_f=attic_temp_f),
        spaces=dict(space_policies or {}),
    )


def _row(md, first_cell):
    """The cells of the one Markdown table row whose first cell is `first_cell`.

    Cell-level, not substring: the winter and summer factor columns can legitimately
    hold the same number, so `"0.50" in md` cannot tell "the report echoed the declared
    policy" apart from "the report echoed the policy the engine applied".
    """
    line = next(l for l in md.splitlines() if l.startswith(f"| {first_cell} |"))
    return [c.strip() for c in line.split("|")]


def test_report_warns_about_void_floor_area_by_room():
    # Insertion order is deliberately area-ASCENDING, so it fights the ranking assertion
    # below. With the rooms the other way round (as they first arrived here) a plain
    # `for name, area in env.voids.items()` passes the ordering check by coincidence.
    env = _env(voids={"Kitchen": 12.8, "Main Bed": 120.1})
    md = report.render_heating(_result(), _sc(), env=env)
    assert "no level drawn beneath" in md
    assert "Main Bed" in md and "120.1" in md
    assert "132.9" in md                      # the total, thousands-separated if needed
    # ranked by area, biggest first — not model/dict order
    assert md.index("Main Bed") < md.index("Kitchen")


def test_report_void_callout_lists_only_the_largest_rooms_and_counts_the_rest():
    """"Largest:" must mean it. Listing every room makes the label a lie on a real model."""
    env = _env(voids={"A": 1.0, "B": 2.0, "C": 3.0, "D": 4.0, "E": 5.0})
    md = report.render_heating(_result(), _sc(), env=env)
    callout = next(l for l in md.splitlines() if l.startswith("- Largest"))
    assert "3 of 5 rooms" in callout
    assert "E" in callout and "D" in callout and "C" in callout
    assert " A " not in callout and " B " not in callout
    assert "15.0 ft²" in md                   # the total still counts all five


def test_report_void_callout_uses_real_line_breaks_not_lazy_continuation():
    """Two-space-indented follow-on lines are Markdown lazy continuation: they render as
    one run-on paragraph, so the callout a reader sees is not the callout in the source."""
    env = _env(voids={"Main Bed": 120.1})
    md = report.render_heating(_result(), _sc(), env=env)
    assert "### Schematic gaps" in md         # its own heading, like its sibling blocks
    lines = md.splitlines()
    i = next(n for n, l in enumerate(lines) if l.startswith("⚠ **"))
    assert lines[i + 1] == ""                 # blank line closes the paragraph
    assert lines[i + 2].startswith("- ")      # a real list item, not a two-space indent
    assert lines[i + 3].startswith("- ")


def test_report_omits_the_void_warning_when_there_are_none():
    md = report.render_heating(_result(), _sc(), env=_env())
    assert "no level drawn beneath" not in md


def _sc_slab(**assemblies):
    """A side-car whose only floor assembly is the slab `floor`, so `buffer_floor` borrows."""
    return sidecar.SideCar(
        assemblies={"exterior_wall": 0.1, "floor": 0.02, **assemblies},
        design=sidecar.DesignConditions(70, 20, 50), infiltration_ach=0.5)


def _void_and_borrow_env():
    """A void of 149.5 ft² inside 293.5 ft² of buffer floor — the Refrhus shape, where the
    other 144.0 ft² is crawl floor that IS drawn. Deliberately unequal so a cross-reference
    quoting the void total instead of the borrowed total is visible."""
    return _env(voids={"Main Bed": 149.5},
                surfaces=[geometry.Surface("buffer_floor", 149.5, "crawlspace"),
                          geometry.Surface("buffer_floor", 144.0, "crawlspace")])


def test_report_says_the_void_area_is_inside_the_borrowed_area_not_beside_it():
    """The void warning and the borrow table cover the same floor in adjacent blocks. With
    no relation stated a reader sums them into one ~443 ft² problem; in fact the void
    nests inside the borrow. Only the code-font mismatch stopped them being joined."""
    md = report.render_heating(_result(), _sc_slab(), env=_void_and_borrow_env())
    callout = next(l for l in md.splitlines() if l.startswith("- Not a separate problem"))
    assert "293.5 ft²" in callout          # the BORROWED total, not the 149.5 void total
    assert "`buffer_floor`" in callout
    # and the warning names the same category in the same code font, so they join up
    warning = next(l for l in md.splitlines() if l.startswith("⚠ **"))
    assert "`buffer_floor`" in warning and "149.5 ft²" in warning


def test_report_omits_the_cross_reference_when_the_buffer_floor_u_is_declared():
    """No borrow table means nothing to point at — the cross-reference would dangle."""
    md = report.render_heating(_result(), _sc_slab(buffer_floor=0.521),
                               env=_void_and_borrow_env())
    assert "### Borrowed assembly U-values" not in md
    assert "Not a separate problem" not in md
    assert "no level drawn beneath" in md          # the void warning itself still stands


def _exposed_void_env():
    """The `below_void: outdoor` shape: the void resolved to `exposed_floor`, not
    `buffer_floor`. Verified against the real resolver — `geometry.CATEGORY_FOR_BELOW`
    maps the `outdoor` space to `exposed_floor`, and `spaces` gives `outdoor` factor 1.0.
    Nothing here is a `buffer_floor`, so a block that hardcodes the common category
    describes a surface this envelope does not contain."""
    return _env(voids={"Sunroom": 64.6},
                surfaces=[geometry.Surface("exposed_floor", 64.6, "outdoor")])


def test_report_names_the_category_the_void_actually_became_not_the_default():
    """`below_void: outdoor` makes the void an `exposed_floor` at the FULL outdoor ΔT.
    Claiming `buffer_floor` there is wrong twice over: wrong category, and "buffer" reads
    as the ~50% a buffer implies while the engine applied 1.0 — understating the load
    while sounding careful. The category is read off the envelope, never assumed."""
    md = report.render_heating(_result(), _sc_slab(), env=_exposed_void_env())
    warning = next(l for l in md.splitlines() if l.startswith("⚠ **"))
    assert "`exposed_floor`" in warning
    assert "buffer_floor" not in warning       # not even inside a hedging parenthetical
    assert "full** outdoor ΔT" in warning      # the TREATMENT, not just the name


def test_report_cross_references_the_borrow_on_the_exposed_floor_branch_too():
    """The bullet went silently absent on this branch while the borrow table right above
    showed `exposed_floor` borrowing the same severe slab donor — two blocks quoting
    64.6 ft² with no stated relation, which is the exact defect it exists to close."""
    md = report.render_heating(_result(), _sc_slab(), env=_exposed_void_env())
    # Cell-level, so this establishes the row is the BORROW table's — a document-wide
    # `"| \`exposed_floor\` |" in md` pins only a first cell and would be satisfied by
    # any table that happens to lead with that category.
    cells = _row(md, "`exposed_floor`")
    assert cells[2] == "64.6 ft²"                     # area
    assert cells[4] == "`assemblies.floor`"           # the severe slab donor
    assert "order of magnitude" in cells[5]           # and the severity note
    callout = next(l for l in md.splitlines() if l.startswith("- Not a separate problem"))
    assert "`exposed_floor`" in callout and "64.6 ft²" in callout


def test_report_makes_no_treatment_claim_when_it_cannot_name_the_category():
    """`below_void: ground` resolves a void to a ground-coupled `floor`, which is not a
    category this block can key off — nearly every model has an on-grade floor, so its
    presence proves nothing. Say nothing rather than guess: a wrong category here is
    also a wrong ΔT, and the phrasing sounds careful either way."""
    env = _env(voids={"Sunroom": 64.6},
               surfaces=[geometry.Surface("floor", 64.6, None)])
    md = report.render_heating(_result(), _sc_slab(), env=env)
    warning = next(l for l in md.splitlines() if l.startswith("⚠ **"))
    assert warning.endswith("has no level drawn beneath it**.")   # the gap, and no more
    assert "modeled as" not in warning
    assert "buffer" not in md.lower()
    assert "Not a separate problem" not in md         # nothing to cross-reference either


# `floor` is the one category deliberately left out of `_VOID_TREATMENT`, and the reason
# is in the comment above that table: nearly every model has an on-grade floor, so its
# presence in an envelope cannot show that a VOID became one. It is silent on purpose.
_INTENTIONALLY_SILENT_VOID_CATEGORIES = {"floor"}


def test_every_void_category_the_geometry_can_emit_is_explained_or_silent_on_purpose():
    """`report._VOID_TREATMENT` hand-mirrors `geometry.CATEGORY_FOR_BELOW` by copy — there
    is no constant to share, because one names categories and the other names what they
    mean to a reader. Renaming `exposed_floor` in `geometry` degrades the report to no
    claim at all, and this test says so *directly* instead of leaving it to be inferred.

    Honest scope, because the first version of this docstring overstated it: that rename
    is NOT silent today. `test_integration.test_below_void_outdoor_reaches_exposed_floor_
    and_the_report_says_so` already fails on it, measured. What that test cannot say is
    WHY it failed — it exercises one category end to end, so it reads as a broken
    `below_void: outdoor` path rather than as a table that has fallen out of sync, and it
    covers only the categories it happens to drive. This one enumerates every category
    `_horizontal_surfaces` can emit and names the invariant, so the next category added to
    `CATEGORY_FOR_BELOW` — which no integration test covers yet — is caught on arrival.

    Silence is the safe failure mode and stays allowed, but only for a category that is
    listed here as silent on purpose. A NEW one has to be a deliberate decision.

    Read through `_horizontal_surfaces` rather than off `CATEGORY_FOR_BELOW` alone, so the
    default branch's `buffer_floor` — a bare literal in that function — is covered too.
    """
    from eldr import stack
    below = {space: 1.0 for space in geometry.CATEGORY_FOR_BELOW}
    below["an-undrawn-space"] = 1.0          # the default branch: any space name at all
    split = stack.FaceSplit(room_id="r", below=below, above={}, void_below_ft2=1.0)
    emitted = {s.category for s in geometry._horizontal_surfaces(split)}
    assert len(emitted) > 1                  # the fixture reached more than one branch
    assert not (emitted - set(report._VOID_TREATMENT)
                - _INTENTIONALLY_SILENT_VOID_CATEGORIES)


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
    # and it is the WINTER cell that carries them, not some other 0.67 in the document
    cells = _row(md, "crawlspace")
    assert cells[2] == "0.67 → 38.0°F"        # the factor AND the ΔT it resolves to
    assert "32.0°F" in cells[3] and "winter_temp_f" in cells[3]


def test_report_echoes_level_heights():
    env = _env(level_heights={"Main": 8.0})
    md = report.render_heating(_result(), _sc(), env=env)
    assert "Main" in md and "8.0" in md
    assert _row(md, "Main")[2] == "8.0 ft"


def test_report_renders_the_cooling_factor_the_engine_applied_not_the_declared_one():
    """The trap this whole task turns on.

    `spaces.policy_for("attic", {})` is the bare unvented default, whose cooling factor is
    0.50 — the same number the winter column legitimately shows — while loads.py is
    meanwhile loading the ceiling at (130-75)/16 = 3.44. A report resolved through
    `spaces.cooling_factor` alone would be confidently wrong AND would still contain the
    string "0.50", so only the summer CELL can tell the two implementations apart.
    """
    env = _env(surfaces=[geometry.Surface("ceiling", 100.0, "attic")])
    md = report.render_heating(_result(), _sc_attic(attic_temp_f=130.0), env=env)
    cells = _row(md, "attic")
    assert cells[2] == "0.50 → 25.0°F"       # winter: the declared policy, correctly
    assert cells[4] == "3.44 → 55.0°F"       # summer: what loads.py actually applied
    assert "130" in cells[5] and "attic_temp_f" in cells[5]


def test_report_names_each_seasons_delta_t_in_the_column_header():
    """The two ΔTs differ by 3.4x and this block renders BEFORE the cooling section, so
    a bare "× ΔT" sends a reader to the heating ΔT in the header: 3.66 × 55 = 201°F
    instead of 58.5°F. Each column names its own, and each cell resolves it.

    Per cell, not per line: asserting both ΔT substrings against the whole header is
    order-blind, so swapping the two header f-strings — the single most likely way this
    breaks — would still pass, shipping a table whose winter column claims a 16°F ΔT
    beside a cell resolving 55°F."""
    env = _env(surfaces=[geometry.Surface("ceiling", 100.0, "attic")])
    md = report.render_heating(_result(), _sc_attic(), env=env)
    header = next(l for l in md.splitlines() if l.startswith("| Space |"))
    cells = [c.strip() for c in header.split("|")]
    assert cells[2] == "Winter factor (of 50°F ΔT)"      # heating: 70 - 20
    assert cells[4] == "Summer factor (of 16°F ΔT)"      # cooling: 91 - 75


def test_report_names_the_attic_temperature_and_calls_the_estimate_an_estimate():
    """133.5°F is the least obvious number in the engine — an outdoor temp plus a flat
    solar uplift. Printing only the factor would leave it invisible."""
    env = _env(surfaces=[geometry.Surface("ceiling", 100.0, "attic")])
    md = report.render_heating(_result(), _sc_attic(), env=env)
    cells = _row(md, "attic")
    assert cells[4] == "3.66 → 58.5°F"      # (133.5-75)/16, the sol-air estimate
    assert "133.5" in cells[5]
    assert "sol-air" in cells[5]


def test_report_marks_the_vented_shorthand_as_a_winter_rung_reused_for_summer():
    """`vented`/`factor` are documented WINTER shorthands. Echoing one bare in the summer
    column reads as a summer observation the modeler never made."""
    env = _env(surfaces=[geometry.Surface("buffer_floor", 100.0, "crawlspace")])
    md = report.render_heating(_result(), _sc_attic(), env=env)
    cells = _row(md, "crawlspace")
    assert "winter shorthand, reused for summer" in cells[5]
    assert "winter shorthand" not in cells[3]      # the winter column is not "reused"


def test_report_buffer_block_survives_a_heating_only_side_car():
    """`cooling:` is optional. Unguarded, `effective_cooling_policy` crashed on the attic
    and only the attic, so this passes on every other space while the report is broken."""
    env = _env(surfaces=[geometry.Surface("ceiling", 100.0, "attic"),
                         geometry.Surface("buffer_floor", 100.0, "crawlspace")])
    md = report.render_heating(_result(), _sc(), env=env)
    assert _row(md, "attic")[2] == "0.50 → 25.0°F"
    assert _row(md, "attic")[4] == "—"
    assert _row(md, "crawlspace")[4] == "—"
    assert _row(md, "attic")[5] == "no `cooling` block in the side-car"


def test_report_says_which_reason_left_the_summer_column_empty():
    """"no cooling block" also fired when a block existed with an unresolved outdoor
    temp — unreachable through `cli.run`, wrong for a direct `render_heating` caller."""
    sc = sidecar.SideCar(
        assemblies={"exterior_wall": 0.1},
        design=sidecar.DesignConditions(70, 20, 50),
        infiltration_ach=0.5,
        cooling=sidecar.Cooling(indoor_f=75, outdoor_1_f=None, shgc=0.35, occupants=3),
    )
    env = _env(surfaces=[geometry.Surface("ceiling", 100.0, "attic")])
    md = report.render_heating(_result(), sc, env=env)
    assert _row(md, "attic")[5] == "`cooling.outdoor_1_f` unresolved"


def test_report_omits_the_buffer_space_block_when_no_surface_faces_one():
    env = _env(surfaces=[geometry.Surface("exterior_wall", 100.0)])
    md = report.render_heating(_result(), _sc(), env=env)
    assert "Buffer spaces" not in md


def test_report_marks_a_side_car_height_override_apart_from_a_model_height():
    sc = sidecar.SideCar(
        assemblies={"exterior_wall": 0.1},
        design=sidecar.DesignConditions(70, 20, 50),
        infiltration_ach=0.5,
        levels={"Crawlspace": sidecar.LevelSpec(height_ft=4.0)},
    )
    env = _env(level_heights={"Main": 8.0, "Crawlspace": 4.0})
    md = report.render_heating(_result(), sc, env=env)
    assert _row(md, "Main")[3] == "model"
    assert _row(md, "Crawlspace")[3] == "side-car override"


def test_report_shows_the_volume_each_level_contributes_not_the_house_total():
    """A level that holds no conditioned rooms contributes nothing, and saying so is the
    point of the column. Echoing `volume_ft3` on every row would look plausible."""
    env = _env(level_heights={"Main": 8.0, "Garage": 10.0},
               level_volumes={"Main": 3000.0, "Garage": 0.0})
    md = report.render_heating(_result(), _sc(), env=env)
    assert _row(md, "Main")[4] == "3,000 ft³"
    assert _row(md, "Garage")[4] == "0 ft³"


def test_report_renders_none_of_the_assumption_blocks_without_an_envelope():
    md = report.render_heating(_result(), _sc())
    assert "Level heights" not in md
    assert "Buffer spaces" not in md
    assert "Borrowed assembly" not in md
    assert "no level drawn beneath" not in md


def test_report_discloses_a_borrowed_assembly_u_value_and_its_donor():
    """The bigger of the two errors over a void floor is the U-value, not the geometry:
    `buffer_floor` with no entry borrows the slab's effective whole-area number. Until
    now that was a stderr warning and never reached the document at all.
    """
    sc = sidecar.SideCar(
        assemblies={"exterior_wall": 0.1, "floor": 0.05},
        design=sidecar.DesignConditions(70, 20, 50),
        infiltration_ach=0.5,
    )
    env = _env(surfaces=[geometry.Surface("buffer_floor", 149.5, "crawlspace"),
                         geometry.Surface("exterior_wall", 800.0)])
    md = report.render_heating(_result(), sc, env=env)
    cells = _row(md, "`buffer_floor`")
    assert cells[2] == "149.5 ft²"                    # the area standing on the stand-in
    assert cells[3] == "0.05"                         # the borrowed number itself
    assert "`assemblies.floor`" in cells[4]           # the donor, named
    assert "order of magnitude" in cells[5]           # loads.py's own severity wording
    # a declared category must NOT appear — only borrows do
    assert "`exterior_wall`" not in md.split("### Borrowed assembly U-values")[1]


def test_report_sums_the_area_of_every_surface_sharing_a_borrowed_category():
    """One row per category, not per surface: the reader wants the exposure, not a list."""
    sc = sidecar.SideCar(
        assemblies={"exterior_wall": 0.1, "floor": 0.05},
        design=sidecar.DesignConditions(70, 20, 50),
        infiltration_ach=0.5,
    )
    env = _env(surfaces=[geometry.Surface("buffer_floor", 100.0, "crawlspace"),
                         geometry.Surface("buffer_floor", 49.5, "garage")])
    md = report.render_heating(_result(), sc, env=env)
    assert len([l for l in md.splitlines() if l.startswith("| `buffer_floor` |")]) == 1
    assert _row(md, "`buffer_floor`")[2] == "149.5 ft²"


def test_report_omits_the_borrow_block_when_every_assembly_is_declared():
    sc = sidecar.SideCar(
        assemblies={"exterior_wall": 0.1, "buffer_floor": 0.08, "floor": 0.05},
        design=sidecar.DesignConditions(70, 20, 50),
        infiltration_ach=0.5,
    )
    env = _env(surfaces=[geometry.Surface("buffer_floor", 149.5, "crawlspace")])
    md = report.render_heating(_result(), sc, env=env)
    assert "Borrowed assembly" not in md
