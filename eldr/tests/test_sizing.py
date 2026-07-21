import pytest
from eldr import sizing, loads, sidecar


def _result(total_btuh):
    # Only total_btuh matters to sizing; other fields are placeholders.
    return loads.HeatingResult(
        conduction_btuh=total_btuh, infiltration_btuh=0.0,
        total_btuh=total_btuh, cfm=0.0, by_category={},
    )


def _sc(existing_tons=None):
    return sidecar.SideCar(
        assemblies={"exterior_wall": 0.1},
        design=sidecar.DesignConditions(70, 20, 50),
        infiltration_ach=0.5,
        existing_tons=existing_tons,
    )


def test_recommended_size_on_boundary():
    # 36,000 BTU/hr = 3.0 tons exactly -> rec meets it at 3.0 (0% over), next 3.5
    r = sizing.size_equipment(_result(36000), _sc())
    assert abs(r.load_tons - 3.0) < 1e-9
    assert r.rec_tons == 3.0
    assert abs(r.rec_oversize_pct) < 1e-9
    assert r.next_tons == 3.5


def test_recommended_size_non_boundary():
    # 38,808 BTU/hr = 3.234 tons (real Refrhus load): rec ceils up to 3.5, next 4.0
    r = sizing.size_equipment(_result(38808), _sc())
    assert abs(r.load_tons - 3.234) < 0.01
    assert r.rec_tons == 3.5                       # smallest standard meeting the load
    assert 7 < r.rec_oversize_pct < 9              # 3.5 vs 3.234 ~ +8%
    assert r.next_tons == 4.0
    assert 22 < r.next_oversize_pct < 25           # 4.0 vs 3.234 ~ +24%


def test_zero_load_rejected():
    # zero load + an existing unit would divide by zero; reject up front
    with pytest.raises(ValueError, match="load"):
        sizing.size_equipment(_result(0), _sc(existing_tons=4.0))


def test_verdict_oversized():
    # load 3.0 tons, existing 4.0 -> +33% -> oversized
    r = sizing.size_equipment(_result(36000), _sc(existing_tons=4.0))
    assert r.existing_tons == 4.0
    assert r.existing_oversize_pct is not None and r.existing_oversize_pct > 15
    assert r.verdict == "oversized"


def test_verdict_well_matched():
    r = sizing.size_equipment(_result(36000), _sc(existing_tons=3.0))
    assert abs(r.existing_oversize_pct) < 1e-9
    assert r.verdict == "well-matched"


def test_verdict_undersized():
    # existing 2.5 vs 3.0 load -> -16.7% -> undersized
    r = sizing.size_equipment(_result(36000), _sc(existing_tons=2.5))
    assert r.existing_oversize_pct < -10
    assert r.verdict == "undersized"


def test_no_existing_unit():
    r = sizing.size_equipment(_result(36000), _sc())
    assert r.existing_tons is None
    assert r.existing_oversize_pct is None
    assert r.verdict == "no existing unit given"
