import pytest
from eldr import ductd


def test_sizes_trunk_and_branch():
    r = ductd.size_ducts([("trunk", 720), ("kids", 90)], friction_rate=0.08)
    assert r.friction_rate == 0.08
    trunk, kids = r.runs
    # 720 CFM @ 0.08 in.wc/100ft -> ~12.8" exact -> 14" standard, ~673 fpm
    assert 12.5 < trunk.exact_dia_in < 13.1
    assert trunk.standard_dia_in == 14
    assert 650 < trunk.velocity_fpm < 700
    assert trunk.flag == ""
    # 90 CFM -> ~5.8" -> 6" standard, ~458 fpm
    assert kids.standard_dia_in == 6
    assert 440 < kids.velocity_fpm < 480


def test_rounds_up_to_next_standard():
    # a diameter just over 8" must go to the next standard (9"), never down
    r = ductd.size_ducts([("x", 300)], friction_rate=0.08)
    (run,) = r.runs
    assert run.standard_dia_in in ductd.STANDARD_SIZES
    assert run.standard_dia_in >= run.exact_dia_in


def test_flags_high_velocity():
    # a big trunk pushes velocity over the noise threshold
    (run,) = ductd.size_ducts([("bigtrunk", 2000)], friction_rate=0.08).runs
    assert run.velocity_fpm > 900
    assert run.flag == "high"


def test_flags_oversize_beyond_max_standard():
    # a run needing more than the largest standard size is flagged (the reported
    # velocity is optimistic because the size is capped)
    (run,) = ductd.size_ducts([("huge", 20000)], friction_rate=0.08).runs
    assert run.exact_dia_in > ductd.STANDARD_SIZES[-1]
    assert run.standard_dia_in == ductd.STANDARD_SIZES[-1]
    assert run.flag == "oversize"


def test_rejects_bad_inputs():
    with pytest.raises(ValueError, match="friction_rate"):
        ductd.size_ducts([("t", 100)], friction_rate=0)
    with pytest.raises(ValueError, match="cfm"):
        ductd.size_ducts([("t", 0)], friction_rate=0.08)
    with pytest.raises(ValueError, match="cfm"):
        ductd.size_ducts([("t", float("nan"))], friction_rate=0.08)


def test_lengths_add_pressure_drop():
    # a parallel lengths list attaches TEL + friction pressure drop per run
    r = ductd.size_ducts([("trunk", 720), ("kids", 90)], friction_rate=0.08,
                         lengths=[None, 50.0])
    trunk, kids = r.runs
    assert trunk.length_ft is None and trunk.pressure_drop_inwc is None
    assert kids.length_ft == 50.0
    assert abs(kids.pressure_drop_inwc - 0.08 * 50.0 / 100.0) < 1e-9   # 0.04 in.wc


def test_lengths_must_be_parallel():
    with pytest.raises(ValueError, match="parallel"):
        ductd.size_ducts([("a", 100), ("b", 200)], lengths=[10.0])


def test_negative_length_rejected():
    with pytest.raises(ValueError, match="length"):
        ductd.size_ducts([("a", 100)], lengths=[-5.0])


def test_lengths_none_keeps_legacy_shape():
    # no lengths -> length/drop stay None (backward compatible)
    (run,) = ductd.size_ducts([("a", 100)]).runs
    assert run.length_ft is None and run.pressure_drop_inwc is None
