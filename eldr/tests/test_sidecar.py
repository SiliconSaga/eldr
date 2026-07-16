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
