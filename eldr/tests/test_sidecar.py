import textwrap
import pytest
from eldr import sidecar


def _write(tmp_path, body):
    p = tmp_path / "sc.yaml"
    p.write_text(textwrap.dedent(body))
    return str(p)


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


def test_load_sidecar_ok(tmp_path):
    path = _write(tmp_path, """
        design:
          indoor_heating_f: 70
          outdoor_heating_99_f: 15
          supply_air_rise_f: 50
        infiltration:
          ach: 0.5
        assemblies:
          exterior_wall: 0.09
          window: 0.30
    """)
    sc = sidecar.load_sidecar(path)
    assert sc.design.heating_delta_t == 55
    assert sc.infiltration_ach == 0.5
    assert sc.assemblies["window"] == 0.30


def test_load_sidecar_missing_key(tmp_path):
    path = _write(tmp_path, "design:\n  indoor_heating_f: 70\n")
    with pytest.raises(ValueError):
        sidecar.load_sidecar(path)


def test_outdoor_design_temp_optional(tmp_path):
    # omitting outdoor design temps is allowed (they get looked up from lat/long)
    body = ("design:\n  indoor_heating_f: 70\n  supply_air_rise_f: 50\n"
            "infiltration:\n  ach: 0.5\nassemblies:\n  exterior_wall: 0.09\n"
            "cooling:\n  indoor_f: 75\n  shgc: 0.35\n  occupants: 3\n")
    sc = sidecar.load_sidecar(_write(tmp_path, body))
    assert sc.design.outdoor_heating_99_f is None
    assert sc.cooling.outdoor_1_f is None


def test_delta_properties_raise_when_unresolved(tmp_path):
    # a resolved-before-loads invariant: the delta properties fail clearly, not TypeError
    body = ("design:\n  indoor_heating_f: 70\n  supply_air_rise_f: 50\n"
            "infiltration:\n  ach: 0.5\nassemblies:\n  exterior_wall: 0.09\n"
            "cooling:\n  indoor_f: 75\n  shgc: 0.35\n  occupants: 3\n")
    sc = sidecar.load_sidecar(_write(tmp_path, body))
    with pytest.raises(ValueError, match="outdoor_heating_99_f"):
        _ = sc.design.heating_delta_t
    with pytest.raises(ValueError, match="outdoor_1_f"):
        _ = sc.cooling.cooling_delta_t


_VALID = """
    design:
      indoor_heating_f: 70
      outdoor_heating_99_f: 15
      supply_air_rise_f: 50
    infiltration:
      ach: 0.5
    assemblies:
      exterior_wall: 0.09
"""


def test_equipment_block_optional(tmp_path):
    # No equipment block -> existing_tons is None, still loads fine.
    sc = sidecar.load_sidecar(_write(tmp_path, _VALID))
    assert sc.existing_tons is None


def test_equipment_block_parsed(tmp_path):
    sc = sidecar.load_sidecar(_write(tmp_path, _VALID + "    equipment:\n      existing_tons: 4.0\n"))
    assert sc.existing_tons == 4.0


def test_equipment_block_rejects_bad_value(tmp_path):
    with pytest.raises(ValueError, match="existing_tons"):
        sidecar.load_sidecar(_write(tmp_path, _VALID + "    equipment:\n      existing_tons: -1\n"))


def test_equipment_block_rejects_non_mapping(tmp_path):
    with pytest.raises(ValueError, match="mapping"):
        sidecar.load_sidecar(_write(tmp_path, _VALID + "    equipment: 4\n"))


def test_equipment_block_rejects_boolean(tmp_path):
    # YAML true would coerce to 1.0 and masquerade as a real 1-ton unit
    with pytest.raises(ValueError, match="existing_tons"):
        sidecar.load_sidecar(_write(tmp_path, _VALID + "    equipment:\n      existing_tons: true\n"))


_COOLING = "    cooling:\n      indoor_f: 75\n      outdoor_1_f: 90\n      shgc: 0.35\n      occupants: 3\n"


def test_cooling_block_optional(tmp_path):
    sc = sidecar.load_sidecar(_write(tmp_path, _VALID))
    assert sc.cooling is None


def test_cooling_block_parsed(tmp_path):
    sc = sidecar.load_sidecar(_write(tmp_path, _VALID + _COOLING))
    assert sc.cooling is not None
    assert sc.cooling.cooling_delta_t == 15      # 90 - 75
    assert sc.cooling.shgc == 0.35


def test_cooling_block_rejects_bad_delta(tmp_path):
    bad = "    cooling:\n      indoor_f: 90\n      outdoor_1_f: 75\n      shgc: 0.35\n      occupants: 3\n"
    with pytest.raises(ValueError, match="outdoor_1_f"):
        sidecar.load_sidecar(_write(tmp_path, _VALID + bad))


def test_cooling_block_rejects_bad_shgc(tmp_path):
    bad = "    cooling:\n      indoor_f: 75\n      outdoor_1_f: 90\n      shgc: 1.5\n      occupants: 3\n"
    with pytest.raises(ValueError, match="shgc"):
        sidecar.load_sidecar(_write(tmp_path, _VALID + bad))


def test_cooling_block_rejects_empty_mapping(tmp_path):
    # an explicit `cooling: {}` must fail on missing keys, not silently disable cooling
    with pytest.raises(ValueError, match="cooling"):
        sidecar.load_sidecar(_write(tmp_path, _VALID + "    cooling: {}\n"))


def test_cooling_block_rejects_boolean(tmp_path):
    bad = "    cooling:\n      indoor_f: true\n      outdoor_1_f: 90\n      shgc: 0.35\n      occupants: 3\n"
    with pytest.raises(ValueError, match="indoor_f"):
        sidecar.load_sidecar(_write(tmp_path, _VALID + bad))


def test_cooling_attic_temp_optional(tmp_path):
    # the hot-attic override is opt-in; absent it, loads fall back to the sol-air estimate
    sc = sidecar.load_sidecar(_write(tmp_path, _VALID + _COOLING))
    assert sc.cooling.attic_temp_f is None


def test_explicit_attic_temp_overrides_sol_air(tmp_path):
    # re-indented to BASE_SIDECAR's level — `_write` dedents the concatenation as a whole
    sc = _write_and_load(tmp_path, BASE_SIDECAR + textwrap.indent(textwrap.dedent("""\
        cooling:
          indoor_f: 75
          outdoor_1_f: 89
          shgc: 0.3
          occupants: 4
          attic_temp_f: 130
        """), "    "))
    assert sc.cooling.attic_temp_f == 130.0


def test_cooling_attic_temp_must_exceed_indoor(tmp_path):
    # an "attic" colder than the house is not a hot attic; it is a typo or a wrong unit
    bad = _COOLING + "      attic_temp_f: 70\n"
    with pytest.raises(ValueError, match="attic_temp_f"):
        sidecar.load_sidecar(_write(tmp_path, _VALID + bad))


def test_cooling_attic_temp_equal_to_indoor_is_rejected(tmp_path):
    # the boundary itself: the check is `<=`, not `<` — an attic exactly at the setpoint
    # contributes no ceiling gain at all, which is never what the override was set for
    bad = _COOLING + "      attic_temp_f: 75\n"
    with pytest.raises(ValueError, match="attic_temp_f"):
        sidecar.load_sidecar(_write(tmp_path, _VALID + bad))


def test_cooling_attic_temp_must_be_finite(tmp_path):
    bad = _COOLING + "      attic_temp_f: .inf\n"
    with pytest.raises(ValueError, match="attic_temp_f"):
        sidecar.load_sidecar(_write(tmp_path, _VALID + bad))


_DUCTS = "    ducts:\n      friction_rate: 0.1\n      runs:\n        - {name: trunk, cfm: 720}\n        - {name: kids, cfm: 90}\n"


def test_ducts_block_optional(tmp_path):
    sc = sidecar.load_sidecar(_write(tmp_path, _VALID))
    assert sc.ducts is None


def test_ducts_block_parsed(tmp_path):
    sc = sidecar.load_sidecar(_write(tmp_path, _VALID + _DUCTS))
    assert sc.ducts is not None
    assert sc.ducts.friction_rate == 0.1
    assert [r.name for r in sc.ducts.runs] == ["trunk", "kids"]
    assert sc.ducts.runs[0].cfm == 720


def test_ducts_friction_rate_defaults(tmp_path):
    body = _VALID + "    ducts:\n      runs:\n        - {name: t, cfm: 100}\n"
    sc = sidecar.load_sidecar(_write(tmp_path, body))
    assert sc.ducts.friction_rate == 0.08


def test_ducts_rejects_bad_cfm(tmp_path):
    body = _VALID + "    ducts:\n      runs:\n        - {name: t, cfm: -5}\n"
    with pytest.raises(ValueError, match="cfm"):
        sidecar.load_sidecar(_write(tmp_path, body))


def test_ducts_rejects_empty_runs(tmp_path):
    body = _VALID + "    ducts:\n      runs: []\n"
    with pytest.raises(ValueError, match="runs"):
        sidecar.load_sidecar(_write(tmp_path, body))


def test_ducts_block_without_runs_ok(tmp_path):
    # runs are optional now (derived from the model); a config-only ducts block loads
    body = _VALID + "    ducts:\n      friction_rate: 0.09\n      unit_name: furnace\n"
    sc = sidecar.load_sidecar(_write(tmp_path, body))
    assert sc.ducts.runs == ()
    assert sc.ducts.friction_rate == 0.09
    assert sc.ducts.unit_name == "furnace"
    assert sc.ducts.available_static_pressure is None
    assert sc.ducts.fitting_factor == 1.5              # default


def test_ducts_defaults_when_bare(tmp_path):
    sc = sidecar.load_sidecar(_write(tmp_path, _VALID + "    ducts: {}\n"))
    assert sc.ducts.friction_rate == 0.08
    assert sc.ducts.unit_name == "air handler"


def test_ducts_parses_asp_and_fitting_factor(tmp_path):
    body = _VALID + ("    ducts:\n      available_static_pressure: 0.5\n"
                     "      fitting_factor: 2.0\n")
    sc = sidecar.load_sidecar(_write(tmp_path, body))
    assert sc.ducts.available_static_pressure == 0.5
    assert sc.ducts.fitting_factor == 2.0


def test_ducts_rejects_bad_asp(tmp_path):
    body = _VALID + "    ducts:\n      available_static_pressure: 0\n"
    with pytest.raises(ValueError, match="available_static_pressure"):
        sidecar.load_sidecar(_write(tmp_path, body))


def test_ducts_rejects_bad_fitting_factor(tmp_path):
    body = _VALID + "    ducts:\n      fitting_factor: -1\n"
    with pytest.raises(ValueError, match="fitting_factor"):
        sidecar.load_sidecar(_write(tmp_path, body))


def test_ducts_runs_non_mapping_item_rejected(tmp_path):
    # a scalar list item must raise a clean ValueError, not a TypeError/AttributeError
    body = _VALID + "    ducts:\n      runs:\n        - 42\n"
    with pytest.raises(ValueError, match="mapping"):
        sidecar.load_sidecar(_write(tmp_path, body))


def test_ground_temp_defaults(tmp_path):
    sc = sidecar.load_sidecar(_write(tmp_path, _VALID))
    assert sc.design.ground_temp_f == sidecar.DEFAULT_GROUND_TEMP_F


def test_ground_temp_parsed(tmp_path):
    body = """
        design:
          indoor_heating_f: 70
          outdoor_heating_99_f: 15
          supply_air_rise_f: 50
          ground_temp_f: 55
        infiltration:
          ach: 0.5
        assemblies:
          exterior_wall: 0.09
    """
    sc = sidecar.load_sidecar(_write(tmp_path, body))
    assert sc.design.ground_temp_f == 55


def test_load_sidecar_rejects_bad_values(tmp_path):
    base = """
        design:
          indoor_heating_f: 70
          outdoor_heating_99_f: {outdoor}
          supply_air_rise_f: {rise}
        infiltration:
          ach: {ach}
        assemblies:
          exterior_wall: {u}
    """
    # zero supply-air rise would divide by zero when sizing CFM
    with pytest.raises(ValueError, match="supply_air_rise_f"):
        sidecar.load_sidecar(_write(tmp_path, base.format(outdoor=15, rise=0, ach=0.5, u=0.09)))
    # non-positive heating delta (outdoor >= indoor)
    with pytest.raises(ValueError, match="indoor_heating_f"):
        sidecar.load_sidecar(_write(tmp_path, base.format(outdoor=70, rise=50, ach=0.5, u=0.09)))
    # negative ACH
    with pytest.raises(ValueError, match=r"infiltration\.ach"):
        sidecar.load_sidecar(_write(tmp_path, base.format(outdoor=15, rise=50, ach=-0.1, u=0.09)))
    # negative U-value (names the offending assembly)
    with pytest.raises(ValueError, match="exterior_wall"):
        sidecar.load_sidecar(_write(tmp_path, base.format(outdoor=15, rise=50, ach=0.5, u=-0.09)))
    # non-finite value (NaN/inf) rejected before the sign checks
    with pytest.raises(ValueError, match="finite"):
        sidecar.load_sidecar(_write(tmp_path, base.format(outdoor=15, rise=".nan", ach=0.5, u=0.09)))


def test_walls_block_optional(tmp_path):
    sc = sidecar.load_sidecar(_write(tmp_path, _VALID))
    assert sc.wall_boundaries == {}


def test_walls_block_parsed(tmp_path):
    body = _VALID + ("    walls:\n"
                     "      wall-abc: {boundary: buffer}\n"
                     "      wall-def: {boundary: exterior}\n")
    sc = sidecar.load_sidecar(_write(tmp_path, body))
    assert sc.wall_boundaries == {"wall-abc": "buffer", "wall-def": "exterior"}


def test_walls_rejects_bad_boundary(tmp_path):
    body = _VALID + "    walls:\n      wall-abc: {boundary: garage}\n"
    with pytest.raises(ValueError, match="boundary"):
        sidecar.load_sidecar(_write(tmp_path, body))


def test_walls_rejects_non_mapping_spec(tmp_path):
    body = _VALID + "    walls:\n      wall-abc: buffer\n"
    with pytest.raises(ValueError, match="mapping"):
        sidecar.load_sidecar(_write(tmp_path, body))


def test_walls_rejects_missing_boundary_key(tmp_path):
    body = _VALID + "    walls:\n      wall-abc: {}\n"
    with pytest.raises(ValueError, match="boundary"):
        sidecar.load_sidecar(_write(tmp_path, body))


def test_walls_rejects_unhashable_boundary(tmp_path):
    # an unhashable YAML value (list) must give a clean schema error, not a TypeError
    body = _VALID + "    walls:\n      wall-abc: {boundary: [a, b]}\n"
    with pytest.raises(ValueError, match="boundary"):
        sidecar.load_sidecar(_write(tmp_path, body))


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


def test_spaces_rejects_a_factor_above_one(tmp_path):
    """`factor` is documented as a FRACTION of the design ΔT, so 1.0 (the space tracks
    outdoor air) is the ceiling. `factor: 2` applied twice the full ΔT and read as an
    ordinary number on the way past. A space genuinely hotter than outdoor air — a sunlit
    attic — is declared with `summer_temp_f`, which says what the space IS; the engine
    still resolves factors above 1 from that route, and this does not touch it.
    """
    with pytest.raises(ValueError, match="between 0 and 1"):
        _write_and_load(tmp_path, BASE_SIDECAR
                        + "\n    spaces:\n      attic:\n        factor: 2\n")


def test_spaces_accepts_a_factor_of_exactly_one(tmp_path):
    """The bound is inclusive — `outdoor`'s own built-in policy is factor 1.0, so a
    side-car must be able to say the same thing about any other space."""
    sc = _write_and_load(tmp_path, BASE_SIDECAR
                         + "\n    spaces:\n      attic:\n        factor: 1\n")
    assert sc.spaces["attic"].factor == 1.0


def test_spaces_rejects_non_boolean_vented(tmp_path):
    with pytest.raises(ValueError, match="vented"):
        _write_and_load(tmp_path, BASE_SIDECAR
                        + "\n    spaces:\n      attic:\n        vented: sometimes\n")


def _attic_summer(tmp_path, temp, space="attic", cooling=_COOLING):
    return _write_and_load(tmp_path, BASE_SIDECAR + cooling
                           + f"    spaces:\n      {space}:\n        summer_temp_f: {temp}\n")


@pytest.mark.parametrize("temp", [70, 75], ids=["below-setpoint", "at-setpoint"])
def test_spaces_rejects_an_attic_summer_temp_at_or_below_the_setpoint(tmp_path, temp):
    """`cooling.attic_temp_f` has carried this bound since it was added; this is the SAME
    quantity by a stronger route (an observation outranks the design figure), and it was
    unguarded. The failure is silent, which is why it needs a bound rather than a warning:
    `spaces._factor` clamps at 0, so a 70°F "attic" against a 75°F setpoint does not
    produce a negative ΔT or an error — the ceiling's cooling gain simply vanishes, and
    the whole-house total comes out lower with nothing pointing at why.
    """
    with pytest.raises(ValueError, match=r"spaces\['attic'\].summer_temp_f"):
        _attic_summer(tmp_path, temp)


def test_spaces_accepts_an_attic_summer_temp_above_the_setpoint(tmp_path):
    """The other side of the bound — a hot attic is exactly what the field is for, so the
    guard must not be satisfiable by rejecting every `summer_temp_f` on the attic."""
    sc = _attic_summer(tmp_path, 130)
    assert sc.spaces["attic"].summer_temp_f == 130.0


@pytest.mark.parametrize("space", ["crawlspace", "garage"])
def test_spaces_allows_a_cool_non_attic_summer_temp(tmp_path, space):
    """Scoped to the attic ON PURPOSE. A crawlspace or garage sitting at or below the
    summer setpoint is an ordinary observation, not a typo: it genuinely contributes no
    cooling load, and `_factor`'s clamp to 0 is the right answer there. Widening the
    guard to every space would reject a correct side-car — which is why the attic-only
    scoping gets its own test rather than riding on the rejection case above.
    """
    sc = _attic_summer(tmp_path, 55, space=space)
    assert sc.spaces[space].summer_temp_f == 55.0


def test_spaces_attic_summer_temp_needs_no_cooling_block(tmp_path):
    """A heating-only side-car is valid input and has no summer setpoint to compare
    against, so the bound has nothing to say — it must skip, not raise or crash on the
    absent `cooling`."""
    sc = _attic_summer(tmp_path, 55, cooling="")
    assert sc.cooling is None
    assert sc.spaces["attic"].summer_temp_f == 55.0


def test_spaces_attic_summer_temp_must_still_be_finite(tmp_path):
    """The new bound does not supersede the pre-existing finite check, and cannot: `.inf`
    is greater than the setpoint, so the hot-attic comparison waves it through. Both
    guards have to stand, which is why this asserts the finite message specifically."""
    with pytest.raises(ValueError, match="must be a finite number"):
        _attic_summer(tmp_path, ".inf")


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


@pytest.mark.parametrize("key", ["below_void", "above_void"])
@pytest.mark.parametrize(
    "value",
    ["[a, b]", "{name: crawl}", "''", "'   '"],
    ids=["list", "mapping", "empty", "whitespace"],
)
def test_levels_rejects_a_void_that_is_not_a_space_name(tmp_path, key, value):
    """A void names a SPACE, which `spaces:` and the built-in policies are keyed by. These
    were coerced with `str()`, so a list or a mapping was accepted as a name; the
    stringified result matches no policy, so it fell all the way through to the bare 0.5
    buffer factor and produced a load computed from a typo, saying nothing.
    """
    with pytest.raises(ValueError, match=f"{key} must be a non-empty space name"):
        _write_and_load(tmp_path, BASE_SIDECAR
                        + f"\n    levels:\n      Main:\n        {key}: {value}\n")


def test_levels_accepts_an_ordinary_void_name(tmp_path):
    """The other side of the guard: a plain string still parses, untouched."""
    sc = _write_and_load(tmp_path, BASE_SIDECAR
                         + "\n    levels:\n      Main:\n        above_void: vaulted\n")
    assert sc.levels["Main"].above_void == "vaulted"


# --- assembly key grammar -------------------------------------------------------------
#
# An assembly key is either a bare category (`exterior_wall`) or a category with a named
# variant (`exterior_wall/r0`). The category is always the text before the first slash,
# which is what lets one key say both "what kind of surface" and "which of this house's
# several assemblies of that kind" without a second field.


def test_split_assembly_key_plain_category():
    assert sidecar.split_assembly_key("exterior_wall") == ("exterior_wall", None)


def test_split_assembly_key_variant():
    assert sidecar.split_assembly_key("exterior_wall/r0") == ("exterior_wall", "r0")


def test_split_assembly_key_splits_on_first_slash_only():
    """A variant name may itself contain slashes; only the first one delimits."""
    assert sidecar.split_assembly_key("window/low-e/2a") == ("window", "low-e/2a")


def test_split_assembly_key_empty_variant_is_a_variant():
    """`window/` is malformed rather than a bare category — it must not read as one,
    or a trailing slash would silently collapse a typo into the category default."""
    assert sidecar.split_assembly_key("window/") == ("window", "")


def test_variant_with_unknown_category_prefix_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="unknown category"):
        _write_and_load(tmp_path, BASE_SIDECAR + "\n      windwo/single: 0.9\n")


def test_known_category_variant_loads(tmp_path):
    sc = _write_and_load(tmp_path, BASE_SIDECAR + "\n      exterior_wall/r0: 0.24\n")
    assert sc.assemblies["exterior_wall/r0"] == 0.24
    assert sc.assemblies["exterior_wall"] == 0.09


def test_bare_unknown_key_still_allowed(tmp_path):
    """Only VARIANT keys are validated against the category list. A bare unknown key
    has always been accepted and ignored, and tightening that is a separate decision —
    it would break every side-car carrying a note-to-self key."""
    sc = _write_and_load(tmp_path, BASE_SIDECAR + "\n      some_future_category: 0.2\n")
    assert sc.assemblies["some_future_category"] == 0.2


def test_variant_u_value_is_validated_like_any_other(tmp_path):
    with pytest.raises(ValueError, match="U-value must be >= 0"):
        _write_and_load(tmp_path, BASE_SIDECAR + "\n      exterior_wall/r0: -0.1\n")
