"""
alternatives.py — Find the next available pass for each dropped contact.

For each dropped pass, the alternative is the same satellite's earliest
pass (by pass_id order) that starts strictly after the dropped pass's
earliest_start_utc and is either:
  - already scheduled (appears in schedule.csv), or
  - schedulable: at least one of its candidate stations has a free antenna
    (checked against the booked schedule plus any alternatives already
    assigned in this pass).

Columns added to dropped.csv:
    alt_pass_id, alt_station, alt_start_utc, delay_min, has_alternative

alt_station is the station from which the alternative pass would be
(or is) served:
  - for already-scheduled passes: the booked station.
  - for schedulable passes: the best free candidate (highest max_elevation_deg).
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import NamedTuple

import pandas as pd

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from scheduler import group_passes  # noqa: E402
from stations import ANTENNA_TURNAROUND_MIN  # noqa: E402

_TURNAROUND = timedelta(minutes=ANTENNA_TURNAROUND_MIN)


# ---------------------------------------------------------------------------
# Internal helpers  (mirrors scheduler.py — kept local to avoid coupling)
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
                     turnaround: timedelta) -> bool:
    """Return True when [t0,t1] is within *turnaround* of booking [b_start,b_end]."""
    return t0 < b_end + turnaround and b_start < t1 + turnaround


def _build_registries(
    schedule_df: pd.DataFrame,
) -> tuple[dict[str, list[_Booking]], dict[str, list[_Booking]]]:
    """Build station and satellite booking registries from schedule_df."""
    station_bookings: dict[str, list[_Booking]] = {}
    sat_bookings:     dict[str, list[_Booking]] = {}
    for row in schedule_df.itertuples(index=False):
        b = _Booking(row.satellite, row.start_utc, row.end_utc)
        station_bookings.setdefault(row.station, []).append(b)
        sat_bookings.setdefault(row.satellite, []).append(b)
    return station_bookings, sat_bookings


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def find_alternatives(
    dropped_df: pd.DataFrame,
    schedule_df: pd.DataFrame,
    windows_df: pd.DataFrame,
) -> pd.DataFrame:
    """Attach alternative-pass columns to each dropped pass.

    For each dropped row the function finds the earliest pass of the same
    satellite (in pass_id order, i.e. chronological) that begins strictly
    after the dropped pass's ``earliest_start_utc`` and that is either
    already scheduled or has at least one free station.

    Alternatives already assigned earlier in the loop are registered into
    the booking tables so they are not double-assigned.

    Parameters
    ----------
    dropped_df:
        DataFrame produced by :func:`scheduler.schedule`; must have columns
        pass_id, satellite, earliest_start_utc.
    schedule_df:
        Booked contacts (data/schedule.csv); must have columns satellite,
        station, start_utc, end_utc, pass_id.
    windows_df:
        Full candidate set (data/windows.csv).

    Returns
    -------
    Copy of ``dropped_df`` with five additional columns:
        alt_pass_id, alt_station, alt_start_utc, delay_min, has_alternative.
    """
    # --- Parse timestamps ---
    dropped_df  = dropped_df.copy()
    schedule_df = schedule_df.copy()
    windows_df  = windows_df.copy()

    schedule_df["start_utc"] = pd.to_datetime(schedule_df["start_utc"], utc=True)
    schedule_df["end_utc"]   = pd.to_datetime(schedule_df["end_utc"],   utc=True)

    dropped_df["earliest_start_utc"] = pd.to_datetime(
        dropped_df["earliest_start_utc"], utc=True
    )

    # --- Group all windows into passes (reuse scheduler logic) ---
    passes_df = group_passes(windows_df)
    for col in ("start_utc", "end_utc"):
        passes_df[col] = pd.to_datetime(passes_df[col], utc=True)

    # Build a per-satellite dict: pass_id → list of candidate window rows,
    # sorted by pass start then best elevation desc within each pass.
    # pass_order maps pass_id → (satellite, earliest_start_utc) for sorting.
    pass_meta: dict[str, dict] = {}          # pass_id → {sat, earliest, candidates}
    for pid, grp in passes_df.groupby("pass_id", sort=False):
        pass_meta[pid] = {
            "satellite":      grp["satellite"].iloc[0],
            "earliest_start": grp["start_utc"].min(),
            # candidates: sorted by elevation desc for "best free station" pick
            "candidates":     grp.sort_values("max_elevation_deg",
                                              ascending=False).to_dict("records"),
        }

    # Group pass_ids by satellite, ordered chronologically.
    sat_passes: dict[str, list[str]] = {}
    for pid, meta in sorted(pass_meta.items(),
                            key=lambda kv: kv[1]["earliest_start"]):
        sat_passes.setdefault(meta["satellite"], []).append(pid)

    # Build a fast lookup: pass_id → booked station (from schedule).
    scheduled_pass: dict[str, str] = {}
    for row in schedule_df.itertuples(index=False):
        if hasattr(row, "pass_id"):
            scheduled_pass[row.pass_id] = row.station

    # --- Booking registries seeded from the existing schedule ---
    station_bookings, sat_bookings = _build_registries(schedule_df)

    # --- Main loop ---
    alt_pass_ids:  list[str]        = []
    alt_stations:  list[str]        = []
    alt_starts:    list[str]        = []
    delay_mins:    list[float|None] = []
    has_alts:      list[bool]       = []

    for row in dropped_df.itertuples(index=False):
        sat: str            = row.satellite
        dropped_start: pd.Timestamp = row.earliest_start_utc

        found_pid:     str | None = None
        found_station: str | None = None
        found_start:   pd.Timestamp | None = None

        for pid in sat_passes.get(sat, []):
            meta = pass_meta[pid]
            if meta["earliest_start"] <= dropped_start:
                continue  # must be strictly after

            # Case A: already scheduled — free, just report it.
            if pid in scheduled_pass:
                found_pid     = pid
                found_station = scheduled_pass[pid]
                # Look up the actual start_utc from schedule_df.
                sched_row = schedule_df[schedule_df["pass_id"] == pid].iloc[0]
                found_start = sched_row["start_utc"]
                break

            # Case B: unscheduled pass — check if any candidate is free.
            for cand in meta["candidates"]:
                t0: pd.Timestamp = cand["start_utc"]
                t1: pd.Timestamp = cand["end_utc"]
                stn: str         = cand["station"]

                stn_free = not any(
                    _station_blocked(t0, t1, b.start, b.end, _TURNAROUND)
                    for b in station_bookings.get(stn, [])
                )
                sat_free = not any(
                    _overlaps(t0, t1, b.start, b.end)
                    for b in sat_bookings.get(sat, [])
                )
                if stn_free and sat_free:
                    found_pid     = pid
                    found_station = stn
                    found_start   = t0
                    break
            if found_pid is not None:
                break

        if found_pid is not None and found_start is not None:
            # Register this alternative to prevent double-assignment.
            if found_pid not in scheduled_pass:
                # Find the actual window row for the chosen station.
                for cand in pass_meta[found_pid]["candidates"]:
                    if cand["station"] == found_station:
                        b = _Booking(sat,
                                     pd.Timestamp(cand["start_utc"]),
                                     pd.Timestamp(cand["end_utc"]))
                        station_bookings.setdefault(found_station, []).append(b)
                        sat_bookings.setdefault(sat, []).append(b)
                        break

            delay = round(
                (found_start - dropped_start).total_seconds() / 60, 1
            )
            alt_pass_ids.append(found_pid)
            alt_stations.append(found_station)
            alt_starts.append(found_start.strftime("%Y-%m-%dT%H:%M:%SZ"))
            delay_mins.append(delay)
            has_alts.append(True)
        else:
            alt_pass_ids.append("")
            alt_stations.append("")
            alt_starts.append("")
            delay_mins.append(None)
            has_alts.append(False)

    # Restore ISO strings on the dropped timestamp column.
    result = dropped_df.copy()
    result["earliest_start_utc"] = result["earliest_start_utc"].dt.strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    result["alt_pass_id"]    = alt_pass_ids
    result["alt_station"]    = alt_stations
    result["alt_start_utc"]  = alt_starts
    result["delay_min"]      = delay_mins
    result["has_alternative"] = has_alts
    return result


# ---------------------------------------------------------------------------
# __main__
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    project_root = Path(__file__).resolve().parent.parent
    dropped_path  = project_root / "data" / "dropped.csv"
    schedule_path = project_root / "data" / "schedule.csv"
    windows_path  = project_root / "data" / "windows.csv"

    dropped_df  = pd.read_csv(dropped_path)
    schedule_df = pd.read_csv(schedule_path)
    windows_df  = pd.read_csv(windows_path)

    result = find_alternatives(dropped_df, schedule_df, windows_df)
    result.to_csv(dropped_path, index=False)

    with_alt    = result["has_alternative"].sum()
    without_alt = (~result["has_alternative"]).sum()
    delays      = result.loc[result["has_alternative"], "delay_min"]
    median_delay = delays.median() if not delays.empty else None

    print(f"Dropped passes with alternative : {with_alt}")
    print(f"Dropped passes without          : {without_alt}")
    if median_delay is not None:
        print(f"Median delay_min                : {median_delay:.1f}")
    else:
        print("Median delay_min                : n/a")
