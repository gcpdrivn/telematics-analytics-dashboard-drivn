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

from ingestion import bq_client
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
