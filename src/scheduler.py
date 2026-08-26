"""
scheduler.py — Pass-aware greedy priority scheduler for satellite contacts.

Step 1: Group windows (rows in windows.csv) into orbital passes.
  Two rows for the same satellite belong to the same pass when their
  time intervals overlap OR their start times are within 20 minutes of
  each other.  Each group is assigned a pass_id of the form
  "<SATELLITE>-<N>" (zero-padded, sequential per satellite).

Step 2: Schedule passes.
  Passes are ordered by (priority asc, earliest_start asc).  For each
  pass the candidate stations are tried best-first by max_elevation_deg.
  The first station whose antenna is free for the pass window is booked.
  If no station is free the whole pass is dropped.

Outputs
-------
data/schedule.csv  — one booked row per pass
data/dropped.csv   — one dropped row per pass
"""

from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path
from typing import NamedTuple

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stations import priority_for, ANTENNA_TURNAROUND_MIN  # noqa: E402

# Maximum gap between consecutive windows (by start_utc) of the same
# satellite that still classifies them as the same orbital pass.
_PASS_GAP = timedelta(minutes=20)

# Turnaround added to every station booking's end time when checking
# whether the antenna is free for a new contact.
_TURNAROUND = timedelta(minutes=ANTENNA_TURNAROUND_MIN)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

class _Booking(NamedTuple):
    satellite: str
    start: pd.Timestamp
    end: pd.Timestamp


def _overlaps(a0: pd.Timestamp, a1: pd.Timestamp,
              b0: pd.Timestamp, b1: pd.Timestamp) -> bool:
    """Return True when two half-open intervals overlap."""
    return a0 < b1 and b0 < a1


def _station_blocked(t0: pd.Timestamp, t1: pd.Timestamp,
                     b_start: pd.Timestamp, b_end: pd.Timestamp,
                     turnaround: "timedelta") -> bool:
    """Return True when a candidate window [t0,t1] cannot follow or precede a
    station booking [b_start, b_end] given the required turnaround gap.

    The antenna is blocked whenever the gap between the two windows is less
    than *turnaround* in either direction — i.e. the candidate and the booking
    are closer than *turnaround* minutes apart (or overlap).
    """
    return t0 < b_end + turnaround and b_start < t1 + turnaround


def _overlap_seconds(a0: pd.Timestamp, a1: pd.Timestamp,
                     b0: pd.Timestamp, b1: pd.Timestamp) -> float:
    """Return the overlap in seconds between two intervals (0 if none)."""
    return max(0.0, (min(a1, b1) - max(a0, b0)).total_seconds())


# ---------------------------------------------------------------------------
# Step 1 — pass grouping
# ---------------------------------------------------------------------------

def group_passes(windows_df: pd.DataFrame) -> pd.DataFrame:
    """Assign a pass_id to every row in *windows_df*.

    Two rows for the same satellite are in the same pass when either their
    intervals overlap OR their start times are within 20 minutes of each other.
    Rows are processed in start_utc order per satellite; the pass envelope
    (earliest start, latest end) expands as members are added.

    Parameters
    ----------
    windows_df:
        DataFrame with at least columns satellite, start_utc, end_utc.
        Timestamps may be strings or datetimes.

    Returns
    -------
    Copy of *windows_df* with an additional ``pass_id`` column.
    """
    df = windows_df.copy()
    for col in ("start_utc", "end_utc"):
        df[col] = pd.to_datetime(df[col], utc=True)

    df = df.sort_values(["satellite", "start_utc"]).reset_index(drop=True)
    pass_ids: list[str] = [""] * len(df)

    for sat, grp in df.groupby("satellite", sort=False):
        idx = grp.index.tolist()
        pass_num = 0
        # Envelope of the current open pass.
        env_start: pd.Timestamp = grp.loc[idx[0], "start_utc"]
        env_end: pd.Timestamp = grp.loc[idx[0], "end_utc"]

        pass_ids[idx[0]] = f"{sat}-{pass_num:03d}"

        for i in idx[1:]:
            row_start: pd.Timestamp = grp.loc[i, "start_utc"]
            row_end: pd.Timestamp = grp.loc[i, "end_utc"]

            # Same pass: overlaps OR starts within _PASS_GAP of env_start.
            same = (
                _overlaps(env_start, env_end, row_start, row_end)
                or (row_start - env_start) < _PASS_GAP
            )
            if same:
                env_end = max(env_end, row_end)
            else:
                pass_num += 1
                env_start = row_start
                env_end = row_end

            pass_ids[i] = f"{sat}-{pass_num:03d}"

    df["pass_id"] = pass_ids
    return df


# ---------------------------------------------------------------------------
# Step 2 — schedule
# ---------------------------------------------------------------------------

def schedule(windows_df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Schedule passes greedily by priority then earliest start.

    Parameters
    ----------
    windows_df:
        DataFrame with columns satellite, station, start_utc, end_utc,
        duration_min, max_elevation_deg.

    Returns
    -------
    (scheduled, dropped)
        scheduled — pass_id, satellite, station, start_utc, end_utc,
                    duration_min, max_elevation_deg, priority
        dropped   — pass_id, satellite, priority, earliest_start_utc,
                    candidate_stations, blocked_by
    """
    df = group_passes(windows_df)
    df["priority"] = df["satellite"].map(priority_for)

    # Build a pass-level summary: one row per pass_id.
    passes = (
        df.groupby("pass_id", sort=False)
        .apply(
            lambda g: pd.Series({
                "satellite":       g["satellite"].iloc[0],
                "priority":        g["priority"].iloc[0],
                "earliest_start":  g["start_utc"].min(),
                # Candidates sorted best-first (highest elevation first).
                "candidates":      g.sort_values("max_elevation_deg",
                                                 ascending=False)
                                    .to_dict("records"),
            })
        )
        .reset_index()
    )

    # Sort passes: priority asc, then earliest_start asc.
    passes = passes.sort_values(
        ["priority", "earliest_start"], kind="stable"
    ).reset_index(drop=True)

    # Booking registries: station → list[_Booking], satellite → list[_Booking]
    station_bookings: dict[str, list[_Booking]] = {}
    sat_bookings:     dict[str, list[_Booking]] = {}

    sched_rows:   list[dict] = []
    dropped_rows: list[dict] = []

    for prow in passes.itertuples(index=False):
        booked = False

        for cand in prow.candidates:
            sat: str          = cand["satellite"]
            stn: str          = cand["station"]
            t0:  pd.Timestamp = cand["start_utc"]
            t1:  pd.Timestamp = cand["end_utc"]

            # Check station antenna conflict: the gap between the candidate
            # and every existing booking must be >= TURNAROUND in both directions.
            stn_free = not any(
                _station_blocked(t0, t1, b.start, b.end, _TURNAROUND)
                for b in station_bookings.get(stn, [])
            )
            # Check satellite double-booking.
            sat_free = not any(
                _overlaps(t0, t1, b.start, b.end)
                for b in sat_bookings.get(sat, [])
            )

            if stn_free and sat_free:
                booking = _Booking(sat, t0, t1)
                station_bookings.setdefault(stn, []).append(booking)
                sat_bookings.setdefault(sat, []).append(booking)
                sched_rows.append({
                    "pass_id":          prow.pass_id,
                    "satellite":        sat,
                    "station":          stn,
                    "start_utc":        t0.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "end_utc":          t1.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "duration_min":     cand["duration_min"],
                    "max_elevation_deg": cand["max_elevation_deg"],
                    "priority":         cand["priority"],
                })
                booked = True
                break

        if not booked:
            # Find blocked_by: the satellite causing the most cumulative
            # overlap across all candidate windows of this pass.
            overlap_by: dict[str, float] = {}
            for cand in prow.candidates:
                t0 = cand["start_utc"]
                t1 = cand["end_utc"]
                stn = cand["station"]
                for b in station_bookings.get(stn, []):
                    ov = _overlap_seconds(t0, t1 + _TURNAROUND,
                                          b.start, b.end + _TURNAROUND)
                    if ov > 0:
                        overlap_by[b.satellite] = overlap_by.get(b.satellite, 0.0) + ov

            blocked_by = max(overlap_by, key=lambda k: overlap_by[k]) \
                if overlap_by else ""

            candidate_stations = ", ".join(
                dict.fromkeys(c["station"] for c in prow.candidates)
            )
            dropped_rows.append({
                "pass_id":             prow.pass_id,
                "satellite":           prow.satellite,
                "priority":            prow.priority,
                "earliest_start_utc":  prow.earliest_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "candidate_stations":  candidate_stations,
                "blocked_by":          blocked_by,
            })

    scheduled = pd.DataFrame(sched_rows, columns=[
        "pass_id", "satellite", "station", "start_utc", "end_utc",
        "duration_min", "max_elevation_deg", "priority",
    ])
    dropped = pd.DataFrame(dropped_rows, columns=[
        "pass_id", "satellite", "priority", "earliest_start_utc",
        "candidate_stations", "blocked_by",
    ])
    return scheduled, dropped


# ---------------------------------------------------------------------------
# __main__
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    project_root = Path(__file__).resolve().parent.parent
    windows_path  = project_root / "data" / "windows.csv"
    schedule_path = project_root / "data" / "schedule.csv"
    dropped_path  = project_root / "data" / "dropped.csv"

    windows_df = pd.read_csv(windows_path)
    scheduled, dropped = schedule(windows_df)

    schedule_path.parent.mkdir(parents=True, exist_ok=True)
    scheduled.to_csv(schedule_path, index=False)
    dropped.to_csv(dropped_path, index=False)

    total = len(scheduled) + len(dropped)
    all_p1 = len(scheduled[scheduled["priority"] == 1]) \
           + len(dropped[dropped["priority"] == 1])

    print(f"Passes total     : {total}")
    print(f"Scheduled        : {len(scheduled)}  "
          f"({len(scheduled)/total*100:.1f}%)")
    print(f"Dropped          : {len(dropped)}  "
          f"({len(dropped)/total*100:.1f}%)")
    if all_p1:
        sched_p1 = len(scheduled[scheduled["priority"] == 1])
        print(f"Priority-1 rate  : {sched_p1}/{all_p1}  "
              f"({sched_p1/all_p1*100:.1f}%)")
    else:
        print("Priority-1 rate  : n/a")
