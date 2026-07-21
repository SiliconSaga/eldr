from eldr import climate


def test_nearest_station_west_orange():
    # West Orange NJ (~40.71, -74.01) -> New York / Newark area
    s = climate.nearest_station(40.71, -74.01)
    assert ("NY" in s.name) or ("NJ" in s.name)
    assert 0 < s.heating_99_f < 30
    assert 85 < s.cooling_1_f < 100


def test_nearest_station_far():
    assert "Miami" in climate.nearest_station(25.8, -80.2).name
    assert "Phoenix" in climate.nearest_station(33.4, -112.0).name
