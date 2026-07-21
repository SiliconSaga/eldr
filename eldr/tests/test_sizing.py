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


def test_tons_and_recommended_band():
    # 36,000 BTU/hr = 3.0 tons exactly -> band brackets it in a half-ton window
    r = sizing.size_equipment(_result(36000), _sc())
    assert abs(r.load_tons - 3.0) < 1e-9
    assert r.rec_low_tons == 3.0
    assert r.rec_high_tons == 3.5


def test_verdict_oversized():
    # load 3.0 tons, existing 4.0 -> +33% -> oversized
    r = sizing.size_equipment(_result(36000), _sc(existing_tons=4.0))
    assert r.existing_tons == 4.0
    assert r.oversize_pct is not None and r.oversize_pct > 15
    assert r.verdict == "oversized"


def test_verdict_well_matched():
    r = sizing.size_equipment(_result(36000), _sc(existing_tons=3.0))
    assert abs(r.oversize_pct) < 1e-9
    assert r.verdict == "well-matched"


def test_verdict_undersized():
    # existing 2.5 vs 3.0 load -> -16.7% -> undersized
    r = sizing.size_equipment(_result(36000), _sc(existing_tons=2.5))
    assert r.oversize_pct < -10
    assert r.verdict == "undersized"


def test_no_existing_unit():
    r = sizing.size_equipment(_result(36000), _sc())
    assert r.existing_tons is None
    assert r.oversize_pct is None
    assert r.verdict == "no existing unit given"
