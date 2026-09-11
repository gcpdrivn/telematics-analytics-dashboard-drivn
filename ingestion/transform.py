"""Cleaning/normalization logic for raw utilization Excel reports.

Ports the transformations developed in Analysis/data_consolidation_v1.ipynb,
adapted to run per-file (so each file can be dedup-checked independently)
and to retain the original, uncleaned license plate alongside the derived
'base' plate.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

_STRIP_KEYWORDS = ("OBD", "API", "CAM")

# raw Excel column name -> our BigQuery/snake_case column name
_COLUMN_RENAMES = {
    "Distance": "distance_km",
    "Vehicle Name": "vehicle_name",
    "Running Time": "running_time_hours",
    "Group Name": "group_name",
    "GPS Disconnection count": "gps_disconnection_count",
    "Vehicle Type": "vehicle_type",
    "Vehicle Maker": "vehicle_maker",
    "Vehicle Model": "vehicle_model",
    "Avg. Daily Operating Time": "avg_daily_operating_hours",
    "Stoppages Count": "stoppages_count",
    "Max Speed": "max_speed",
    "Start Location": "start_location",
    "End Location": "end_location",
    "Average Speed": "average_speed",
    "Start Date": "start_date",
    "End Date": "end_date",
    "Closing Odometer": "closing_odometer",
    "Opening Odometer": "opening_odometer",
    "Report Date": "report_date",
}

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


def _ensure_columns(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    """Some report exports omit whole columns (e.g. odometer readings)
    rather than leaving them blank. Add any missing ones as all-NaN so
    downstream parsing/renaming can treat every file the same way."""
    missing = [c for c in columns if c not in df.columns]
    if missing:
        df = df.copy()
        for col in missing:
            df[col] = pd.NA
    return df


def _remove_keywords(val: object) -> object:
    if pd.isna(val):
        return val
    text = str(val)
    for kw in _STRIP_KEYWORDS:
        text = text.replace(kw, "")
    return text.strip()


def read_utilization_file(path: Path) -> pd.DataFrame:
    """Read one raw utilization Excel report into a DataFrame, tagged with
    its inferred report date. Mirrors dfXlsxFiles() from the notebook, but
    for a single file."""
    df = pd.read_excel(path)
    df.columns = [str(c).strip() for c in df.columns]

    if "Start Date" not in df.columns:
        raise ValueError(
            f"'{path.name}' has no 'Start Date' column; cannot infer report date."
        )

    start_dates = pd.to_datetime(df["Start Date"], errors="coerce")
    unique_dates = start_dates.dt.date.dropna()
    if unique_dates.empty:
        raise ValueError(f"'{path.name}' has no parseable dates in 'Start Date'.")

    df["Report Date"] = unique_dates.mode()[0]
    return df


def clean_utilization(
    df: pd.DataFrame, source_file_name: str, source_file_hash: str
) -> pd.DataFrame:
    """Apply the notebook's cleaning pipeline to one raw utilization
    DataFrame and return it in the final BigQuery-ready shape."""
    df = _ensure_columns(df, list(_COLUMN_RENAMES.keys()))

    plate_col = "License Plate / Vehicle Number"
    if plate_col not in df.columns:
        raise ValueError(f"'{source_file_name}' has no '{plate_col}' column.")
    df["license_plate_raw"] = df[plate_col].astype(str)
    df["base_license_plate"] = (
        df["license_plate_raw"].str.split("_").str[0].apply(_remove_keywords)
    )

    # Vehicle Name is numeric in some files, alphanumeric in others; force a
    # single consistent dtype so per-file loads share one BigQuery schema.
    df["Vehicle Name"] = df["Vehicle Name"].astype(str)

    df["Running Time"] = (
        pd.to_timedelta(df["Running Time"]).dt.total_seconds() / 3600
    ).round(2)
    df["Avg. Daily Operating Time"] = (
        pd.to_timedelta(df["Avg. Daily Operating Time"]).dt.total_seconds() / 3600
    ).round(2)
    df["GPS Disconnection count"] = (
        pd.to_numeric(df["GPS Disconnection count"], errors="coerce")
        .fillna(0)
        .astype("Int64")
    )
    df["Report Date"] = pd.to_datetime(df["Report Date"]).dt.date

    # DashCam-sourced rows report unreliable utilization stats (sparse
    # Group Name / GPS metadata) and are excluded, per the notebook.
    df = df[df["Group Name"] != "DashCam"].copy()

    df = df.rename(columns=_COLUMN_RENAMES)
    df["source_file_name"] = source_file_name
    df["source_file_hash"] = source_file_hash

    missing = set(FINAL_COLUMN_ORDER) - set(df.columns)
    if missing:
        raise ValueError(f"'{source_file_name}' is missing expected columns: {missing}")

    return df[FINAL_COLUMN_ORDER].reset_index(drop=True)
