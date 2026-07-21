import textwrap
import pytest
from eldr import sidecar


def _write(tmp_path, body):
    p = tmp_path / "sc.yaml"
    p.write_text(textwrap.dedent(body))
    return str(p)


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
