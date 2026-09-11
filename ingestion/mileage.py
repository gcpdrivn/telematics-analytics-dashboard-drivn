"""Cleaning logic for the vehicle_mileage_soc.xlsx benchmark file.

Ports Step 5 of the cleaning pipeline in
Analysis/generate_presentation_report.py verbatim: readings >= 10.0 km/%SoC
are sensor/calibration errors and are nulled out. Rows the source workbook
already has blank (manually excluded upstream, per its Status/Raw_Note audit
trail) pass through unchanged -- this rule doesn't need to know about that
history, it just needs to match the numeric threshold the script uses.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from ingestion import bq_client
from ingestion.config import Settings

logger = logging.getLogger(__name__)

_COLUMN_RENAMES = {
    "Vehicle_Short_No": "vehicle_short_no",
    "License_Plate": "license_plate",
    "Mileage_Km_per_SoC": "mileage_km_per_soc",
    "Status": "status",
    "Raw_Note": "raw_note",
}

FINAL_COLUMN_ORDER = [
    "vehicle_short_no",
    "license_plate",
    "mileage_km_per_soc",
    "status",
    "raw_note",
]

_OUTLIER_THRESHOLD = 10.0


def read_and_clean_mileage_soc(path: Path) -> pd.DataFrame:
    df = pd.read_excel(path)
    df.columns = [str(c).strip() for c in df.columns]

    missing = set(_COLUMN_RENAMES) - set(df.columns)
    if missing:
        raise ValueError(f"'{path.name}' is missing expected columns: {missing}")

    df["License_Plate"] = df["License_Plate"].astype(str).str.strip()
    df.loc[df["Mileage_Km_per_SoC"] >= _OUTLIER_THRESHOLD, "Mileage_Km_per_SoC"] = pd.NA

    df = df.rename(columns=_COLUMN_RENAMES)
    return df[FINAL_COLUMN_ORDER].reset_index(drop=True)


def run(settings: Settings) -> int:
    """Clean and full-refresh load the mileage/SoC benchmark file. Returns
    the row count loaded."""
    if not settings.mileage_soc_file.exists():
        raise FileNotFoundError(
            f"Mileage/SoC file not found: {settings.mileage_soc_file}"
        )

    client = bq_client.get_client(settings)
    bq_client.ensure_schema(client, settings)

    df = read_and_clean_mileage_soc(settings.mileage_soc_file)
    bq_client.load_mileage_soc_rows(client, settings, df)

    logger.info(
        "Loaded %d row(s) from '%s' into %s",
        len(df),
        settings.mileage_soc_file.name,
        settings.mileage_soc_table_ref,
    )
    return len(df)
