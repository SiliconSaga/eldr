"""Climate design conditions from the model's lat/long.

A small, curated table of US design stations with approximate ASHRAE 99% heating
and 1% cooling design dry-bulb temperatures. `nearest_station` picks the closest
by great-circle distance so a side-car can omit outdoor design temps and let the
model's compass lat/long supply them. Values are demo-grade approximations —
replace with the ACCA/ASHRAE station table for certification.
"""
from __future__ import annotations
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class Station:
    name: str
    lat: float
    lon: float
    heating_99_f: float     # 99% heating design dry-bulb
    cooling_1_f: float      # 1% cooling design dry-bulb


# Approximate ASHRAE design temps (deg F) for a spread of US locations.
DESIGN_STATIONS = [
    Station("New York, NY", 40.78, -73.97, 15, 91),
    Station("Newark, NJ", 40.70, -74.17, 14, 91),
    Station("Boston, MA", 42.36, -71.01, 9, 88),
    Station("Philadelphia, PA", 39.87, -75.23, 14, 92),
    Station("Washington, DC", 38.85, -77.03, 17, 93),
    Station("Chicago, IL", 41.98, -87.90, -3, 91),
    Station("Minneapolis, MN", 44.88, -93.22, -11, 90),
    Station("Atlanta, GA", 33.63, -84.44, 23, 93),
    Station("Miami, FL", 25.79, -80.29, 47, 90),
    Station("Houston, TX", 29.98, -95.36, 31, 96),
    Station("Dallas, TX", 32.90, -97.03, 22, 99),
    Station("Denver, CO", 39.83, -104.66, 3, 90),
    Station("Phoenix, AZ", 33.43, -112.01, 37, 108),
    Station("Los Angeles, CA", 33.94, -118.40, 43, 83),
    Station("Seattle, WA", 47.44, -122.31, 27, 82),
    Station("Portland, OR", 45.60, -122.60, 25, 86),
]


def _haversine_mi(lat1, lon1, lat2, lon2) -> float:
    r = 3958.8  # Earth radius, miles
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def nearest_station(lat_deg: float, lon_deg: float) -> Station:
    """Return the design station nearest a lat/long (decimal degrees)."""
    return min(DESIGN_STATIONS,
               key=lambda s: _haversine_mi(lat_deg, lon_deg, s.lat, s.lon))
