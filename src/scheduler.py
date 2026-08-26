"""
scheduler.py — Greedy priority-based satellite contact window scheduler.

Each station has one antenna (no overlap at the same station).
A satellite cannot be booked at two stations simultaneously.
Windows are sorted by (priority asc, start_utc asc) and greedily
booked; anything that conflicts is dropped with a reason.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

import pandas as pd

# Allow running as a script from any working directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from stations import priority_for  # noqa: E402


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

class _Booking(NamedTuple):
    satellite: str
    start: datetime
    end: datetime


def _overlaps(a_start: datetime, a_end: datetime,
              b_start: datetime, b_end: datetime) -> bool:
    """Return True when two half-open intervals [a_start, a_end) overlap."""
    return a_start < b_end and b_start < a_end


def _overlap_seconds(a_start: datetime, a_end: datetime,
                     b_start: datetime, b_end: datetime) -> float:
    """Return the number of seconds two intervals overlap (0 if none)."""
    latest_start = max(a_start, b_start)
    earliest_end = min(a_end, b_end)
    delta = (earliest_end - latest_start).total_seconds()
    return max(0.0, delta)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def schedule(windows_df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Greedily schedule satellite contact windows respecting antenna constraints.

    Rules
    -----
    - One antenna per station: bookings at the same station must not overlap.
    - One contact per satellite at a time: a satellite cannot be booked at two
      stations simultaneously.
    - Candidate windows are sorted by (priority asc, start_utc asc).
    - The first window in that order is booked if it conflicts with nothing
      already booked; otherwise it is dropped and labelled with the conflicting
      satellite that overlaps the most.

    Parameters
    ----------
    windows_df:
        DataFrame with columns satellite, station, start_utc, end_utc,
        duration_min, max_elevation_deg.  start_utc/end_utc may be strings
        or datetimes.

    Returns
    -------
    (scheduled, dropped)
        scheduled — original columns plus ``priority`` (int).
        dropped   — original columns plus ``priority`` (int) and
                    ``blocked_by`` (str, satellite name).
    """
    df = windows_df.copy()

    # Parse timestamps to UTC-aware pandas Timestamps.
    for col in ("start_utc", "end_utc"):
        df[col] = pd.to_datetime(df[col], utc=True)

    # Attach priority and sort.
    df["priority"] = df["satellite"].map(priority_for)
    df = df.sort_values(["priority", "start_utc"], kind="stable").reset_index(drop=True)

    # station_bookings: station -> list of _Booking
    station_bookings: dict[str, list[_Booking]] = {}
    # sat_bookings: satellite -> list of _Booking
    sat_bookings: dict[str, list[_Booking]] = {}

    scheduled_rows: list[dict] = []
    dropped_rows: list[dict] = []

    out_cols = ["satellite", "station", "start_utc", "end_utc",
                "duration_min", "max_elevation_deg", "priority"]

    for row in df.itertuples(index=False):
        sat: str = row.satellite
        stn: str = row.station
        # Convert pandas Timestamp -> plain Python datetime for reliable arithmetic.
        t0: datetime = row.start_utc.to_pydatetime()
        t1: datetime = row.end_utc.to_pydatetime()
        priority: int = row.priority

        # --- Check for conflicts ---
        conflict_sat: str | None = None
        max_overlap: float = 0.0

        # 1. Station antenna conflict.
        for booking in station_bookings.get(stn, []):
            if _overlaps(t0, t1, booking.start, booking.end):
                ov = _overlap_seconds(t0, t1, booking.start, booking.end)
                if ov > max_overlap:
                    max_overlap = ov
                    conflict_sat = booking.satellite

        # 2. Satellite already in use at another station.
        for booking in sat_bookings.get(sat, []):
            if _overlaps(t0, t1, booking.start, booking.end):
                ov = _overlap_seconds(t0, t1, booking.start, booking.end)
                if ov > max_overlap:
                    max_overlap = ov
                    conflict_sat = booking.satellite

        base = {
            "satellite": sat,
            "station": stn,
            "start_utc": row.start_utc,
            "end_utc": row.end_utc,
            "duration_min": row.duration_min,
            "max_elevation_deg": row.max_elevation_deg,
            "priority": priority,
        }

        if conflict_sat is None:
            # Book it.
            booking = _Booking(sat, t0, t1)
            station_bookings.setdefault(stn, []).append(booking)
            sat_bookings.setdefault(sat, []).append(booking)
            scheduled_rows.append(base)
        else:
            dropped_rows.append({**base, "blocked_by": conflict_sat})

    # Build output DataFrames.
    scheduled = pd.DataFrame(scheduled_rows, columns=out_cols)
    dropped = pd.DataFrame(dropped_rows, columns=out_cols + ["blocked_by"])

    # Restore ISO 8601 strings for CSV friendliness.
    for frame in (scheduled, dropped):
        if not frame.empty:
            for col in ("start_utc", "end_utc"):
                frame[col] = frame[col].dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    return scheduled, dropped


# ---------------------------------------------------------------------------
# __main__
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    project_root = Path(__file__).resolve().parent.parent
    windows_path = project_root / "data" / "windows.csv"
    schedule_path = project_root / "data" / "schedule.csv"
    dropped_path = project_root / "data" / "dropped.csv"

    windows_df = pd.read_csv(windows_path)

    scheduled, dropped = schedule(windows_df)

    schedule_path.parent.mkdir(parents=True, exist_ok=True)
    scheduled.to_csv(schedule_path, index=False)
    dropped.to_csv(dropped_path, index=False)

    total = len(scheduled) + len(dropped)
    print(f"Scheduled : {len(scheduled):>4}  ({len(scheduled)/total*100:.1f}%)")
    print(f"Dropped   : {len(dropped):>4}  ({len(dropped)/total*100:.1f}%)")

    # Priority-1 hit rate.
    all_p1 = windows_df[windows_df["satellite"].map(priority_for) == 1]
    sched_p1 = scheduled[scheduled["priority"] == 1]
    if len(all_p1):
        pct = len(sched_p1) / len(all_p1) * 100
        print(f"Priority-1 scheduled: {len(sched_p1)}/{len(all_p1)}  ({pct:.1f}%)")
    else:
        print("No priority-1 windows in input.")
