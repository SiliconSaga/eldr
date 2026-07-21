import pytest
from eldr import sizing, sidecar


def _sc(existing_tons=None):
    return sidecar.SideCar(
        assemblies={"exterior_wall": 0.1},
        design=sidecar.DesignConditions(70, 20, 50),
        infiltration_ach=0.5,
        existing_tons=existing_tons,
    )


def test_recommended_size_on_boundary():
    # 36,000 BTU/hr = 3.0 tons exactly -> rec meets it at 3.0 (0% over), next 3.5
    r = sizing.size_equipment(36000, _sc())
    assert r.basis == "heating"
    assert abs(r.load_tons - 3.0) < 1e-9
    assert r.rec_tons == 3.0
    assert abs(r.rec_oversize_pct) < 1e-9
    assert r.next_tons == 3.5


def test_recommended_size_non_boundary():
    # 38,808 BTU/hr = 3.234 tons (real Refrhus load): rec ceils up to 3.5, next 4.0
    r = sizing.size_equipment(38808, _sc())
    assert abs(r.load_tons - 3.234) < 0.01
    assert r.rec_tons == 3.5                       # smallest standard meeting the load
    assert 7 < r.rec_oversize_pct < 9              # 3.5 vs 3.234 ~ +8%
    assert r.next_tons == 4.0
    assert 22 < r.next_oversize_pct < 25           # 4.0 vs 3.234 ~ +24%


def test_cooling_drives_sizing_when_larger():
    # heating 36,000 (3.0 t) but cooling 48,000 (4.0 t) -> size on cooling
    r = sizing.size_equipment(36000, _sc(), cooling_btuh=48000)
    assert r.basis == "cooling"
    assert abs(r.load_tons - 4.0) < 1e-9
    assert r.rec_tons == 4.0


def test_heating_drives_when_cooling_smaller():
    r = sizing.size_equipment(36000, _sc(), cooling_btuh=24000)
    assert r.basis == "heating"
    assert abs(r.load_tons - 3.0) < 1e-9


@pytest.mark.parametrize("design_btuh", [0, -1, float("nan"), float("inf")])
def test_invalid_load_rejected(design_btuh):
    # non-positive or non-finite load would divide by zero / poison the result
    with pytest.raises(ValueError, match="load"):
        sizing.size_equipment(design_btuh, _sc(existing_tons=4.0))


def test_verdict_oversized():
    # load 3.0 tons, existing 4.0 -> +33% -> oversized
    r = sizing.size_equipment(36000, _sc(existing_tons=4.0))
    assert r.existing_tons == 4.0
    assert r.existing_oversize_pct is not None and r.existing_oversize_pct > 15
    assert r.verdict == "oversized"


def test_verdict_well_matched():
    r = sizing.size_equipment(36000, _sc(existing_tons=3.0))
    assert abs(r.existing_oversize_pct) < 1e-9
    assert r.verdict == "well-matched"


def test_verdict_undersized():
    # existing 2.5 vs 3.0 load -> -16.7% -> undersized
    r = sizing.size_equipment(36000, _sc(existing_tons=2.5))
    assert r.existing_oversize_pct < -10
    assert r.verdict == "undersized"


def test_no_existing_unit():
    r = sizing.size_equipment(36000, _sc())
    assert r.existing_tons is None
    assert r.existing_oversize_pct is None
    assert r.verdict == "no existing unit given"
