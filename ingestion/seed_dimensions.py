"""Seeds dim_customer / dim_vehicle from the same customer facts hardcoded in
Analysis/generate_presentation_report.py, so the backend can join against
real BigQuery tables instead of a Python dict.

FreshBus/ZingBus are the script's literal enumerated plate lists. BillionE is
NOT hardcoded here -- the script identifies it via
`Base License Plate.startswith('MH02')`, so this seed reproduces that as a
live query against utilization_daily (requires utilization data to already
be ingested) rather than a list that could silently miss a new truck.

Safe to re-run: both tables are WRITE_TRUNCATE (full replace), not append.
"""

from __future__ import annotations

import logging

import pandas as pd

from ingestion import bq_client, vehicle_master
from ingestion.config import Settings

logger = logging.getLogger(__name__)

FRESHBUS_PLATES = [
    "AP39WN7273", "AP39WN7275", "AP39WN7276", "AP39WN7280", "AP39WN7281",
    "AP39WN7301", "AP39WN7302", "AP39WN7305", "AP39WN7306", "AP39WN7322",
]

ZINGBUS_PLATES = [
    "DL1PD9284", "DL1PD9369", "DL1PD9317", "DL1PD9309", "DL1PD8669",
    "DL1PD8652", "HR55AY7626", "HR55AY9237", "DL1PD8523", "DL1PD8509",
    "DL01PD9317",  # legacy identifier some raw records use for DL1PD9317
]

BILLIONE_PLATE_PREFIX = "MH02"

def _clubbed_vehicle_type(vehicle_types: pd.Series) -> str | None:
    """Mirrors backend/metrics.py's clubbed_vehicle_type(): the modal
    Vehicle Type across a plate's reported rows, with Heavy Puller clubbed
    into Truck."""
    mode = vehicle_types.mode()
    if mode.empty:
        return None
    t = mode.iloc[0]
    return "Truck" if t == "Heavy Puller" else t


def _derive_type_model_by_plate(type_model_df: pd.DataFrame) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for plate, sub in type_model_df.groupby("base_license_plate"):
        model_series = sub["vehicle_model"].dropna()
        result[plate] = {
            "vehicle_type": _clubbed_vehicle_type(sub["vehicle_type"]),
            "vehicle_model": model_series.iloc[0] if not model_series.empty else None,
        }
    return result


DIM_CUSTOMERS = [
    {
        "customer_name": "FreshBus",
        "oem": "Azad (Bus)",
        "routes_description": "Guntur - Hyderabad, Guntur - Vizag",
    },
    {
        "customer_name": "ZingBus",
        "oem": "JBM / Azad (Bus)",
        "routes_description": "Delhi - Dehradun, Delhi - Amritsar",
    },
    {
        "customer_name": "BillionE",
        "oem": "TATA (Truck)",
        "routes_description": "Rajasthan - Surat",
    },
]


def run(settings: Settings) -> None:
    client = bq_client.get_client(settings)
    bq_client.ensure_schema(client, settings)

    billione_plates = bq_client.get_distinct_plates_by_prefix(
        client, settings, BILLIONE_PLATE_PREFIX
    )
    if not billione_plates:
        logger.warning(
            "No plates found matching '%s%%' in %s -- has utilization data "
            "been ingested yet? BillionE will be seeded with zero vehicles.",
            BILLIONE_PLATE_PREFIX,
            settings.utilization_table_ref,
        )

    vehicle_rows = (
        [{"base_license_plate": p, "customer_name": "FreshBus"} for p in FRESHBUS_PLATES]
        + [{"base_license_plate": p, "customer_name": "ZingBus"} for p in ZINGBUS_PLATES]
        + [{"base_license_plate": p, "customer_name": "BillionE"} for p in billione_plates]
    )

    # DIM_CUSTOMERS' oem is customer-level and still carries a "(Bus)"/"(Truck)"
    # annotation (e.g. "TATA (Truck)") -- clean it the same way the vehicle
    # master file's oem column is cleaned, so a vehicle falling back to this
    # (not yet in the master file) doesn't get an uncleaned value.
    customer_oem = {c["customer_name"]: vehicle_master.clean_oem(c["oem"]) for c in DIM_CUSTOMERS}

    # The vehicle master spreadsheet (hand-filled OEM/type/model/install date)
    # is the preferred source whenever a plate is in it, so a reseed never
    # wipes out manually entered data. Plates missing from it (e.g. a brand
    # new truck not yet added to the sheet) fall back to telemetry-derived
    # oem/type/model, with device_installation_date left NULL -- there's no
    # telemetry field that could supply it.
    master_by_plate: dict[str, dict] = {}
    if settings.dim_vehicle_master_file.exists():
        master_df = vehicle_master.read_and_clean_vehicle_master(settings.dim_vehicle_master_file)
        master_by_plate = master_df.set_index("base_license_plate").to_dict("index")
        logger.info(
            "Loaded %d row(s) from vehicle master file '%s'.",
            len(master_df),
            settings.dim_vehicle_master_file.name,
        )
    else:
        logger.warning(
            "Vehicle master file '%s' not found -- oem/vehicle_type/vehicle_model "
            "will be derived from telemetry, device_installation_date left NULL "
            "for every vehicle.",
            settings.dim_vehicle_master_file,
        )

    missing_plates = [
        r["base_license_plate"] for r in vehicle_rows if r["base_license_plate"] not in master_by_plate
    ]
    type_model_by_plate = _derive_type_model_by_plate(
        bq_client.get_vehicle_type_model_rows(client, settings, missing_plates)
    )

    for row in vehicle_rows:
        plate = row["base_license_plate"]
        master_row = master_by_plate.get(plate)
        if master_row is not None:
            if master_row["customer_name"] != row["customer_name"]:
                logger.warning(
                    "%s: vehicle master file assigns customer '%s' but the "
                    "roster assigns '%s' -- keeping the roster's customer, "
                    "using the master file's oem/type/model/install date.",
                    plate,
                    master_row["customer_name"],
                    row["customer_name"],
                )
            row["oem"] = master_row["oem"]
            row["vehicle_type"] = master_row["vehicle_type"]
            row["vehicle_model"] = master_row["vehicle_model"]
            row["device_installation_date"] = master_row["device_installation_date"]
        else:
            derived = type_model_by_plate.get(plate, {})
            row["oem"] = customer_oem.get(row["customer_name"])
            row["vehicle_type"] = derived.get("vehicle_type")
            row["vehicle_model"] = derived.get("vehicle_model")
            row["device_installation_date"] = None

    customer_df = pd.DataFrame(DIM_CUSTOMERS)
    vehicle_df = pd.DataFrame(vehicle_rows)

    bq_client.load_dim_customer_rows(client, settings, customer_df)
    bq_client.load_dim_vehicle_rows(client, settings, vehicle_df)

    logger.info(
        "Seeded %d customer(s) and %d vehicle(s) (FreshBus: %d, ZingBus: %d, BillionE: %d)",
        len(customer_df),
        len(vehicle_df),
        len(FRESHBUS_PLATES),
        len(ZINGBUS_PLATES),
        len(billione_plates),
    )
