"""
windows.py — Compute satellite contact windows for a set of ground stations.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from skyfield.api import Loader, wgs84


def load_satellites(tle_path: str) -> list:
    """Parse a TLE file and return a list of EarthSatellite objects.

    Parameters
    ----------
    tle_path:
        Path to a text file containing standard 3-line TLE records.

    Returns
    -------
    list of skyfield EarthSatellite
    """
    load = Loader(".")
    return load.tle_file(tle_path)


def compute_windows(
    satellites: list,
    stations: list[dict[str, Any]],
    start_utc: datetime,
    hours: float = 48,
    min_elevation_deg: float = 10.0,
) -> pd.DataFrame:
    """Compute satellite visibility windows for every satellite/station pair.

    Passes are assembled from rise (0), culmination (1), and set (2) events
    returned by ``find_events``.  Partial passes at either edge of the time
    range are discarded.

    Parameters
    ----------
    satellites:
        List of skyfield EarthSatellite objects (e.g. from :func:`load_satellites`).
    stations:
        List of station dicts with keys ``name``, ``lat``, ``lon``,
        ``elevation_m``.
    start_utc:
        Window start as a timezone-aware (or naive UTC) datetime.
    hours:
        Duration of the search window in hours (default 48).
    min_elevation_deg:
        Minimum elevation in degrees for event detection (default 10.0).

    Returns
    -------
    pandas.DataFrame with columns:
        satellite, station, start_utc, end_utc, duration_min, max_elevation_deg
    Sorted by start_utc ascending.
    """
    load = Loader(".")
    ts = load.timescale()

    # Convert start_utc to a skyfield Time
    if start_utc.tzinfo is None:
        start_utc = start_utc.replace(tzinfo=timezone.utc)
    t0 = ts.from_datetime(start_utc)

    end_dt = start_utc.replace(
        microsecond=0
    ).__class__.fromtimestamp(
        start_utc.timestamp() + hours * 3600, tz=timezone.utc
    )
    t1 = ts.from_datetime(end_dt)

    rows: list[dict] = []

    for sat in satellites:
        sat_name = sat.name.strip()
        for station in stations:
            topos = wgs84.latlon(
                latitude_degrees=station["lat"],
                longitude_degrees=station["lon"],
                elevation_m=station["elevation_m"],
            )

            try:
                times, events = sat.find_events(
                    topos, t0, t1, altitude_degrees=min_elevation_deg
                )
            except Exception:
                continue

            if len(events) == 0:
                continue

            # Walk through the event stream and collect complete 0-1-2 triples.
            # A "complete pass" starts with a rise (0) and ends with a set (2).
            # We skip any leading culmination-only or set events, and we drop
            # any trailing rise (and optional culmination) with no matching set.
            i = 0
            n = len(events)

            # Advance past any leading partial pass (events before the first rise)
            while i < n and events[i] != 0:
                i += 1

            while i < n:
                # Expect: rise=0
                if events[i] != 0:
                    i += 1
                    continue

                rise_time = times[i]
                i += 1

                # Collect optional culmination (1)
                culm_time = None
                if i < n and events[i] == 1:
                    culm_time = times[i]
                    i += 1

                # Expect set (2); if missing, this is a partial pass at the end
                if i >= n or events[i] != 2:
                    break  # no matching set — drop remainder

                set_time = times[i]
                i += 1

                # Compute max elevation at culmination if we have one,
                # otherwise use the midpoint between rise and set
                if culm_time is not None:
                    peak_time = culm_time
                else:
                    mid_tt = (rise_time.tt + set_time.tt) / 2
                    peak_time = ts.tt_jd(mid_tt)

                diff = sat - topos
                alt, _, _ = diff.at(peak_time).altaz()
                max_el = round(alt.degrees, 1)

                rise_iso = rise_time.utc_iso()
                set_iso = set_time.utc_iso()

                duration = round(
                    (set_time.tt - rise_time.tt) * 24 * 60, 1
                )

                rows.append(
                    {
                        "satellite": sat_name,
                        "station": station["name"],
                        "start_utc": rise_iso,
                        "end_utc": set_iso,
                        "duration_min": duration,
                        "max_elevation_deg": max_el,
                    }
                )

    df = pd.DataFrame(
        rows,
        columns=[
            "satellite",
            "station",
            "start_utc",
            "end_utc",
            "duration_min",
            "max_elevation_deg",
        ],
    )
    if not df.empty:
        df = df.sort_values("start_utc").reset_index(drop=True)
    return df


if __name__ == "__main__":
    import sys
    from pathlib import Path

    # Resolve paths relative to the project root (one level up from src/)
    project_root = Path(__file__).resolve().parent.parent
    tle_path = str(project_root / "data" / "tle.txt")
    csv_path = project_root / "data" / "windows.csv"

    # Import stations from the sibling module
    sys.path.insert(0, str(project_root / "src"))
    from stations import STATIONS  # noqa: E402

    sats = load_satellites(tle_path)
    now_utc = datetime.now(tz=timezone.utc)

    df = compute_windows(sats, STATIONS, start_utc=now_utc, hours=48)

    csv_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(csv_path, index=False)

    print(f"Wrote {len(df)} rows to {csv_path}")
    print(df.head())
