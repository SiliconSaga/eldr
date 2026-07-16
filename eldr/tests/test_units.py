from eldr import units


def test_cm_to_ft():
    assert units.cm_to_ft(30.48) == 1.0


def test_sqcm_to_sqft():
    # 1 ft = 30.48 cm, so 1 ft^2 = 929.0304 cm^2
    assert abs(units.sqcm_to_sqft(929.0304) - 1.0) < 1e-9


def test_sensible_factor():
    assert units.SENSIBLE_FACTOR == 1.08
