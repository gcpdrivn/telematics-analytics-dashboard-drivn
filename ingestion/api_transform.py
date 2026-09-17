"""Aggregates Fleetx History Report trips (per-trip records) into the same
per-(base_license_plate, report_date) shape transform.clean_utilization()
produces from Excel, so both sources can load into UTILIZATION_SCHEMA-shaped
tables and be compared directly.

A trip that runs past midnight (common for overnight buses/trucks that
never fully stop) is attributed entirely to its END date, not split across
the days it spans -- a deliberate simplicity-over-precision choice. (An
earlier version prorated distance/duration by elapsed time per day; this is
simpler and good enough given Excel itself has no equivalent notion of a
trip crossing midnight to begin with.)

Known gaps vs. the Excel source, left NULL rather than guessed -- the raw
trip records don't carry them: max_speed, average_speed, stoppages_count,
gps_disconnection_count, avg_daily_operating_hours, group_name, and
vehicle_type (History Report has vehicleMake/vehicleModel but no Bus/Truck
classification field -- that only comes from dim_vehicle, sourced from the
uploader file). DashCam-vs-OBD filtering already happened upstream, at
fleetx_id selection (fleetx_vehicle_map.py) -- there's no equivalent of
Excel's per-row 'Group Name' column to filter on here.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

# A single vehicle-day above this is not a real distance -- caught during
# Phase 2 validation: several newly-commissioned trucks had trip sessions
# that never closed (still "open" days/weeks later), dumping thousands of
# km onto one day. Nulled out the same way mileage.py nulls
# Mileage_Km_per_SoC >= 10.0 as a sensor error -- the row is kept, just this
# one figure is untrustworthy.
MAX_PLAUSIBLE_DAILY_DISTANCE_KM = 1500.0

FINAL_COLUMN_ORDER = [
    "license_plate_raw",
    "base_license_plate",
    "distance_km",
    "vehicle_name",
    "running_time_hours",
    "group_name",
    "gps_disconnection_count",
    "vehicle_type",
    "vehicle_maker",
    "vehicle_model",
    "avg_daily_operating_hours",
    "stoppages_count",
    "max_speed",
    "start_location",
    "end_location",
    "average_speed",
    "start_date",
    "end_date",
    "closing_odometer",
    "opening_odometer",
    "report_date",
    "source_file_name",
    "source_file_hash",
]

_REQUIRED_TRIP_COLUMNS = [
    "sDate", "eDate", "sOdo", "eOdo", "duration", "tripId",
    "vehicleNumber", "vehicleName", "vehicleMake", "vehicleModel",
    "sAddress", "eAddress",
]


def _ensure_columns(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    """Not every trip carries every field -- e.g. a still-in-progress or
    halt-segment trip can be missing eDate entirely, not just null."""
    missing = [c for c in columns if c not in df.columns]
    if missing:
        df = df.copy()
        for col in missing:
            df[col] = pd.NA
    return df


def _trip_distance_km(row: pd.Series) -> float:
    """Trips carry odometer readings, not a distance field -- 'mileage' is
    an efficiency rate (km/SoC or similar), not total km. Floored at 0 to
    guard against odometer resets/glitches rather than reporting negative
    distance. Missing either reading (e.g. a still-in-progress trip)
    contributes 0 rather than propagating NaN -- float('nan') is truthy in
    Python, so `x or 0` does NOT coalesce a NaN reading to 0."""
    e_odo, s_odo = row["eOdo"], row["sOdo"]
    if pd.isna(e_odo) or pd.isna(s_odo):
        return 0.0
    return max(float(e_odo) - float(s_odo), 0.0)


def aggregate_trips_to_daily(
    trips: list[dict[str, Any]], base_license_plate: str, fleetx_vehicle_id: int
) -> pd.DataFrame:
    """One row per calendar date the vehicle had at least one trip end, in
    the same shape as transform.clean_utilization()'s output."""
    if not trips:
        return pd.DataFrame(columns=FINAL_COLUMN_ORDER)

    df = pd.DataFrame(trips)
    df = _ensure_columns(df, _REQUIRED_TRIP_COLUMNS)
    df["sDate"] = pd.to_datetime(df["sDate"], errors="coerce")
    df["eDate"] = pd.to_datetime(df["eDate"], errors="coerce").fillna(df["sDate"])
    df["sOdo"] = pd.to_numeric(df["sOdo"], errors="coerce")
    df["eOdo"] = pd.to_numeric(df["eOdo"], errors="coerce")
    df["duration"] = pd.to_numeric(df["duration"], errors="coerce").fillna(0)

    # A trip with no end (or start) time at all can't be assigned a
    # report_date -- drop rather than guess.
    df = df[df["eDate"].notna()].copy()
    if df.empty:
        return pd.DataFrame(columns=FINAL_COLUMN_ORDER)

    # Whole trip attributed to its end date, not split across the days it
    # spans -- see module docstring.
    df["report_date"] = df["eDate"].dt.date
    df["distance_km"] = df.apply(_trip_distance_km, axis=1)

    rows = []
    for report_date, day in df.groupby("report_date"):
        day = day.sort_values("sDate")
        first, last = day.iloc[0], day.iloc[-1]
        day_trip_ids = sorted(day["tripId"].astype(str).unique())
        source_id = f"fleetx_api:{fleetx_vehicle_id}:{report_date}"
        source_hash = hashlib.sha256(
            json.dumps(day_trip_ids, sort_keys=True).encode()
        ).hexdigest()
        rows.append(
            {
                "license_plate_raw": first.get("vehicleNumber"),
                "base_license_plate": base_license_plate,
                "distance_km": round(float(day["distance_km"].sum()), 2),
                "vehicle_name": first.get("vehicleName"),
                "running_time_hours": round(float(day["duration"].sum()) / 3_600_000, 2),
                "group_name": None,
                "gps_disconnection_count": None,
                "vehicle_type": None,
                "vehicle_maker": first.get("vehicleMake"),
                "vehicle_model": first.get("vehicleModel"),
                "avg_daily_operating_hours": None,
                "stoppages_count": None,
                "max_speed": None,
                "start_location": first.get("sAddress"),
                "end_location": last.get("eAddress"),
                "average_speed": None,
                "start_date": first["sDate"],
                "end_date": last["eDate"],
                "closing_odometer": last.get("eOdo"),
                "opening_odometer": first.get("sOdo"),
                "report_date": report_date,
                "source_file_name": source_id,
                "source_file_hash": source_hash,
            }
        )

    result = pd.DataFrame(rows)[FINAL_COLUMN_ORDER].reset_index(drop=True)

    implausible = result["distance_km"] >= MAX_PLAUSIBLE_DAILY_DISTANCE_KM
    if implausible.any():
        for _, bad_row in result[implausible].iterrows():
            logger.warning(
                "%s on %s: distance_km=%.1f exceeds the %.0fkm/day plausibility "
                "cap -- nulled out, likely a stuck-open trip session.",
                bad_row["base_license_plate"], bad_row["report_date"],
                bad_row["distance_km"], MAX_PLAUSIBLE_DAILY_DISTANCE_KM,
            )
        result.loc[implausible, "distance_km"] = pd.NA

    return result
