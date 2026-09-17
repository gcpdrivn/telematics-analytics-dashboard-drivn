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

from ingestion import bq_client, fleetx_vehicle_map, vehicle_master
from ingestion.config import Settings

logger = logging.getLogger(__name__)

FRESHBUS_PLATES = [
    "AP39WN7273", "AP39WN7275", "AP39WN7276", "AP39WN7280", "AP39WN7281",
    "AP39WN7301", "AP39WN7302", "AP39WN7305", "AP39WN7306", "AP39WN7322",
]

ZINGBUS_PLATES = [
    "DL1PD9284", "DL1PD9369", "DL1PD9309", "DL1PD8669",
    "DL1PD8652", "HR55AY7626", "HR55AY9237", "DL1PD8523", "DL1PD8509",
    "DL01PD9317",
]

BILLIONE_PLATE_PREFIX = "MH02"

# DL1PD9284's uploader-labeled 'OBD' device (2494780) is confirmed dead --
# zero trips over a 46-day live check, while its API/AIS140 device
# (2543818) is active. The label is stale, not the resolution logic, so
# this overrides the file-based pick rather than fixing fleetx_vehicle_map's
# general rule. api_pipeline.py also has a live fallback for this class of
# issue on any *other* vehicle; this fixes the known case at the source so
# every run doesn't pay for the extra live probe.
FLEETX_ID_MANUAL_OVERRIDES = {"DL1PD9284": 2543818}

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

_KNOWN_CUSTOMER_NAMES = {c["customer_name"] for c in DIM_CUSTOMERS}

# The uploader file's free-text 'tags' column doesn't match these customers'
# canonical dim_customer names -- fold known spelling/casing variants into
# the existing customer instead of creating a duplicate (e.g. a vehicle
# tagged "BILLION ELECTRIC MOBILITY" is BillionE, not a new customer).
_CUSTOMER_TAG_ALIASES = {
    "FRESHBUS": "FreshBus",
    "ZINGBUS": "ZingBus",
    "BILLION ELECTRIC MOBILITY": "BillionE",
    "BILLIONE": "BillionE",
}


def _canonical_customer_name(tag: str) -> str:
    return _CUSTOMER_TAG_ALIASES.get(tag.upper(), tag)


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

    # New vehicles present in the Fleetx uploader export but not in any known
    # roster -- mostly a large BillionE truck expansion plus two new
    # customers (AVG LOGISTICS, SWITCHLABS). Onboarded now so the API
    # pipeline covers them from day one, since they have no Excel history to
    # backfill. A handful of otherwise-valid new plates have no customer tag
    # filled in on the Fleetx side (and vehicles not yet plated at all --
    # chassis numbers, VINs -- are never discovered here in the first
    # place); both are skipped rather than guessed.
    if settings.fleetx_vehicle_map_file.exists():
        known_plates = {r["base_license_plate"] for r in vehicle_rows}
        discovered = fleetx_vehicle_map.discover_new_vehicles(
            settings.fleetx_vehicle_map_file, known_plates
        )
        onboarded = 0
        for plate, resolution in discovered.items():
            if resolution.tags is None:
                logger.warning(
                    "%s: new plate found in the uploader file but has no "
                    "customer tag -- skipped, not onboarded.",
                    plate,
                )
                continue
            customer_name = _canonical_customer_name(resolution.tags)
            oem = vehicle_master.clean_oem(resolution.vehicle_maker)
            vehicle_rows.append({"base_license_plate": plate, "customer_name": customer_name})
            master_by_plate[plate] = {
                "customer_name": customer_name,
                "oem": oem,
                "vehicle_type": resolution.vehicle_type,
                "vehicle_model": resolution.vehicle_model,
                "device_installation_date": None,
            }
            onboarded += 1
        logger.info("Onboarded %d new vehicle(s) from the uploader file.", onboarded)

    missing_plates = [
        r["base_license_plate"] for r in vehicle_rows if r["base_license_plate"] not in master_by_plate
    ]
    type_model_by_plate = _derive_type_model_by_plate(
        bq_client.get_vehicle_type_model_rows(client, settings, missing_plates)
    )

    # Fleetx vehicleId per plate (for calling the Fleetx API), resolved from
    # the uploader file's per-device 'group' the same way transform.py picks
    # OBD over DashCam rows -- see fleetx_vehicle_map.py for why Realtime
    # Analytics' own 'merged' vehicleId can't be used for this instead.
    fleetx_id_by_plate: dict[str, int | None] = {}
    if settings.fleetx_vehicle_map_file.exists():
        all_plates = [r["base_license_plate"] for r in vehicle_rows]
        resolutions = fleetx_vehicle_map.resolve_fleetx_ids(
            settings.fleetx_vehicle_map_file, all_plates
        )
        for plate, resolution in resolutions.items():
            fleetx_id_by_plate[plate] = resolution.fleetx_id
            if resolution.fleetx_id is None:
                logger.warning(
                    "%s: could not resolve a Fleetx vehicleId (%s) -- fleetx_id left NULL.",
                    plate,
                    resolution.reason,
                )
        for plate, override_id in FLEETX_ID_MANUAL_OVERRIDES.items():
            if plate in fleetx_id_by_plate and fleetx_id_by_plate[plate] != override_id:
                logger.info(
                    "%s: fleetx_id manually overridden to %d (was %s).",
                    plate, override_id, fleetx_id_by_plate[plate],
                )
                fleetx_id_by_plate[plate] = override_id

        # The MH02-prefix match is a proxy for "a BillionE truck we don't
        # have a manual list for" -- it isn't actually customer-specific, so
        # a different customer's fleet sharing the same plate series fools
        # it. Confirmed for MH02GS5194-5198: swept into BillionE by prefix
        # (they already had Excel history under it), but the uploader file
        # explicitly tags them SWITCHLABS. Correct customer_name from that
        # tag when it disagrees -- FreshBus/ZingBus are deliberate manual
        # rosters and not touched here.
        billione_plate_set = set(billione_plates)
        for row in vehicle_rows:
            plate = row["base_license_plate"]
            if plate not in billione_plate_set:
                continue
            resolution = resolutions.get(plate)
            if resolution is None or resolution.tags is None:
                continue
            corrected = _canonical_customer_name(resolution.tags)
            if corrected != row["customer_name"]:
                logger.warning(
                    "%s: dynamically assigned to BillionE via the '%s' plate "
                    "prefix, but the uploader file tags it '%s' -- correcting "
                    "customer_name to '%s'.",
                    plate, BILLIONE_PLATE_PREFIX, resolution.tags, corrected,
                )
                row["customer_name"] = corrected
    else:
        logger.warning(
            "Fleetx vehicle map file '%s' not found -- fleetx_id left NULL for every vehicle.",
            settings.fleetx_vehicle_map_file,
        )

    for row in vehicle_rows:
        row["fleetx_id"] = fleetx_id_by_plate.get(row["base_license_plate"])
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

    # Collected once, here, from the final vehicle_rows -- after the
    # BillionE-prefix correction above and the oem merge just above -- so a
    # customer discovered either via a brand-new plate or via a corrected
    # customer_name on an already-known plate (like SWITCHLABS) is handled
    # the same way, from real per-vehicle oem data rather than two separate
    # partial tracking passes.
    new_customer_names = sorted(
        {r["customer_name"] for r in vehicle_rows if r["customer_name"] not in _KNOWN_CUSTOMER_NAMES}
    )
    new_customer_rows = []
    for name in new_customer_names:
        oems = [r["oem"] for r in vehicle_rows if r["customer_name"] == name and r["oem"]]
        new_customer_rows.append(
            {
                "customer_name": name,
                "oem": pd.Series(oems).mode().iloc[0] if oems else None,
                "routes_description": None,
            }
        )

    customer_df = pd.DataFrame(DIM_CUSTOMERS + new_customer_rows)
    vehicle_df = pd.DataFrame(vehicle_rows)

    bq_client.load_dim_customer_rows(client, settings, customer_df)
    bq_client.load_dim_vehicle_rows(client, settings, vehicle_df)

    logger.info(
        "Seeded %d customer(s) (%d new) and %d vehicle(s) "
        "(FreshBus: %d, ZingBus: %d, BillionE: %d, other new: %d)",
        len(customer_df),
        len(new_customer_rows),
        len(vehicle_df),
        len(FRESHBUS_PLATES),
        len(ZINGBUS_PLATES),
        len(billione_plates),
        len(vehicle_df) - len(FRESHBUS_PLATES) - len(ZINGBUS_PLATES) - len(billione_plates),
    )
