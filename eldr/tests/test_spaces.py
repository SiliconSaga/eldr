import pytest
from eldr import spaces


def test_temperature_beats_factor_and_vented():
    """An observed design-day temperature is the most defensible input, so it wins."""
    p = spaces.SpacePolicy(name="crawlspace", winter_temp_f=32.0, factor=0.9, vented=True)
    # (70 - 32) / (70 - 13) = 0.6666...
    assert abs(spaces.heating_factor(p, indoor_f=70.0, outdoor_f=13.0) - 38.0 / 57.0) < 1e-9


def test_factor_beats_vented():
    p = spaces.SpacePolicy(name="garage", factor=0.25, vented=True)
    assert spaces.heating_factor(p, 70.0, 13.0) == 0.25


def test_vented_shorthand():
    vented = spaces.SpacePolicy(name="crawlspace", vented=True)
    sealed = spaces.SpacePolicy(name="crawlspace", vented=False)
    assert spaces.heating_factor(vented, 70.0, 13.0) == spaces.VENTED_FACTOR
    assert spaces.heating_factor(sealed, 70.0, 13.0) == spaces.UNVENTED_FACTOR


def test_bare_policy_falls_back_to_unvented_default():
    p = spaces.SpacePolicy(name="whatever")
    assert spaces.heating_factor(p, 70.0, 13.0) == spaces.UNVENTED_FACTOR


def test_cooling_attic_hotter_than_outdoor_exceeds_one():
    """The whole point of a hot attic: its delta-T is LARGER than the outdoor one,
    so the cooling factor must not be clamped at 1."""
    p = spaces.SpacePolicy(name="attic", summer_temp_f=130.0)
    # (130 - 75) / (89 - 75) = 3.928...
    assert spaces.cooling_factor(p, indoor_f=75.0, outdoor_f=89.0) == pytest.approx(55.0 / 14.0)


def test_factors_clamp_at_zero_not_negative():
    """A buffer warmer than indoors in winter gains heat; a heating load must not
    count that as a negative loss."""
    p = spaces.SpacePolicy(name="crawlspace", winter_temp_f=80.0)
    assert spaces.heating_factor(p, 70.0, 13.0) == 0.0
    q = spaces.SpacePolicy(name="crawlspace", summer_temp_f=60.0)
    assert spaces.cooling_factor(q, 75.0, 89.0) == 0.0


def test_degenerate_delta_t_yields_zero():
    """Indoor == outdoor would divide by zero; there is no load to apportion."""
    p = spaces.SpacePolicy(name="attic", winter_temp_f=50.0)
    assert spaces.heating_factor(p, 70.0, 70.0) == 0.0


def test_policy_for_uses_declared_then_default():
    declared = {"attic": spaces.SpacePolicy(name="attic", vented=True)}
    assert spaces.policy_for("attic", declared).vented is True
    # crawlspace is not declared -> the built-in default, which exists and is unvented
    assert spaces.policy_for("crawlspace", declared).name == "crawlspace"
    assert spaces.heating_factor(spaces.policy_for("crawlspace", declared), 70.0, 13.0) == 0.5
    # a name with no default at all still resolves to a usable policy
    assert spaces.policy_for("mystery", {}).name == "mystery"
