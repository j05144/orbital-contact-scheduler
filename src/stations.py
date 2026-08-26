STATIONS = [
    {"name": "Svalbard", "lat": 78.229, "lon": 15.407, "elevation_m": 458},
    {"name": "Kiruna",   "lat": 67.857, "lon": 20.964, "elevation_m": 391},
]

SATELLITE_PRIORITY = {
    "METOP-B": 1,
    "NOAA 20 (JPSS-1)": 1,
    "SUOMI NPP": 2,
    "METOP-C": 2,
    "ISS (ZARYA)": 3,
}

DEFAULT_PRIORITY = 3


def priority_for(satellite_name: str) -> int:
    """Look up a satellite's priority, tolerating whitespace in TLE names."""
    return SATELLITE_PRIORITY.get(satellite_name.strip(), DEFAULT_PRIORITY)

ANTENNA_TURNAROUND_MIN = 15