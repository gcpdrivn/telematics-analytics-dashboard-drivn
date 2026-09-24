"""Syncs dim_customer / dim_vehicle from the Fleetx vehicle export
(Vehicle_Update_uploader.xlsx), safely:

1. Validates the export (readable, expected columns, non-empty, at least one
   vehicle carrying a known customer tag) -- aborts otherwise.
2. Plans the complete target state (dimension_sync.plan_vehicles): customer
   from each vehicle's Fleetx tag, hand-entered data preserved, vehicles
   missing from the export planned as deactivated rather than deleted.
3. Prints a preview of every add / change / deactivation / warning.
4. Writes nothing without explicit confirmation (an interactive "yes", or
   --yes); --dry-run stops after the preview.
5. Snapshots both tables (30-day BigQuery snapshots) before writing.
6. Applies the plan as an atomic MERGE -- rows are only ever inserted or
   updated, never deleted.
7. Immediately backfills the full history of every added/reactivated vehicle
   (vehicle_backfill.backfill), so they show on the dashboard right away.
"""

from __future__ import annotations

import datetime as dt
import logging
import sys
from typing import Callable

import pandas as pd
from google.api_core.exceptions import NotFound

from ingestion import bq_client, dimension_sync, fleetx_vehicle_map, vehicle_backfill, vehicle_master
from ingestion.config import Settings
from ingestion.odometer_resolver import GARBAGE_ABS_THRESHOLD_KM, OVERFLOW_SENTINEL_KM
from ingestion.schema import DIM_CUSTOMER_SCHEMA, DIM_VEHICLE_SCHEMA

logger = logging.getLogger(__name__)

BACKUP_RETENTION_DAYS = 30

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


def _sanitize_odometer(series: pd.Series) -> pd.Series:
    """Nulls a reading that matches the known device-firmware overflow
    sentinel or is otherwise absurdly large -- same two constants
    ingestion/odometer_resolver.py uses for the same purpose, reused here
    rather than redefined."""
    is_sentinel = (series - OVERFLOW_SENTINEL_KM).abs() < 1.0
    is_garbage = series > GARBAGE_ABS_THRESHOLD_KM
    return series.where(~(is_sentinel | is_garbage))


def _derive_starting_odometer_by_plate(odometer_df: pd.DataFrame) -> dict[str, float | None]:
    """Each vehicle's first-ever valid odometer reading -- its pre-existing
    mileage from before this fleet's telemetry began. Prefers Opening
    Odometer (the reading at the very start of that first valid day);
    Closing Odometer only if Opening never has a valid reading at all."""
    result: dict[str, float | None] = {}
    df = odometer_df.copy()
    df["opening_odometer"] = _sanitize_odometer(df["opening_odometer"])
    df["closing_odometer"] = _sanitize_odometer(df["closing_odometer"])
    for plate, sub in df.sort_values("report_date").groupby("base_license_plate"):
        opening = sub["opening_odometer"].dropna()
        if not opening.empty:
            result[plate] = float(opening.iloc[0])
            continue
        closing = sub["closing_odometer"].dropna()
        result[plate] = float(closing.iloc[0]) if not closing.empty else None
    return result


class SyncAborted(RuntimeError):
    """Raised when the sync refuses to run (bad input) or isn't confirmed.
    Nothing has been written when this is raised."""


def _read_current(client, table_ref: str, dry_run: bool) -> pd.DataFrame:
    try:
        return bq_client.get_table_rows(client, table_ref)
    except NotFound:
        if dry_run:
            return pd.DataFrame()
        raise


def _vehicle_load_frame(rows: pd.DataFrame) -> pd.DataFrame:
    df = rows.copy()
    df["fleetx_id"] = df["fleetx_id"].astype("Int64")
    df["starting_odometer"] = df["starting_odometer"].astype("float64")
    df["is_active"] = df["is_active"].astype("boolean")
    for col in ("first_seen_at", "deactivated_at"):
        df[col] = pd.to_datetime(df[col], utc=True)
    return df


def _confirm(
    plan: dimension_sync.Plan,
    assume_yes: bool,
    interactive: bool,
    prompt: Callable[[str], str],
) -> None:
    if assume_yes:
        logger.info("--yes given: applying without prompting.")
        return
    if not interactive:
        raise SyncAborted(
            "Changes need explicit confirmation, but there's no terminal to ask on. "
            "Review the preview above and re-run with --yes to apply it."
        )
    question = "Apply these changes? Type 'yes' to continue: "
    if plan.deactivated:
        question = (
            f"{len(plan.deactivated)} vehicle(s) will be DEACTIVATED. "
            "Type 'yes' to apply everything above: "
        )
    if prompt(question).strip().lower() != "yes":
        raise SyncAborted("Not confirmed -- nothing was written.")


def _refresh_starting_odometer(
    client,
    settings: Settings,
    planned: pd.DataFrame,
    rows_by_plate: dict[str, int],
    master: dict[str, dict] | None = None,
) -> None:
    """starting_odometer is derived from utilization_daily_api, which had no
    rows for a brand-new vehicle when the plan was made -- re-derive it now
    that the backfill has loaded its history, and write just those rows, so
    the next sync doesn't report it as a change."""
    master = master or {}
    # A manual Starting Odometer in the master sheet is already in `planned`.
    plates = [
        p for p, n in rows_by_plate.items()
        if n and pd.isna((master.get(p) or {}).get("starting_odometer"))
    ]
    if not plates:
        return
    derived = _derive_starting_odometer_by_plate(bq_client.get_odometer_rows_by_plate(client, settings))
    rows = planned[planned["base_license_plate"].isin(plates)].copy()
    rows["starting_odometer"] = [
        derived.get(p) if derived.get(p) is not None else cur
        for p, cur in zip(rows["base_license_plate"], rows["starting_odometer"])
    ]
    changed = rows[rows["starting_odometer"].notna()]
    if changed.empty:
        return
    bq_client.merge_rows(
        client, settings.dim_vehicle_table_ref, _vehicle_load_frame(changed), DIM_VEHICLE_SCHEMA, "base_license_plate"
    )


def run(
    settings: Settings,
    *,
    dry_run: bool = False,
    assume_yes: bool = False,
    interactive: bool | None = None,
    prompt: Callable[[str], str] = input,
    client=None,
    backfill: bool = True,
    backfill_fn: Callable | None = None,
) -> str:
    """Returns 'no-changes', 'dry-run' or 'applied'; raises SyncAborted (or
    fleetx_vehicle_map.ExportFileError) without writing anything otherwise."""
    if interactive is None:
        interactive = sys.stdin.isatty()
    client = client or bq_client.get_client(settings)

    try:
        export, skipped_non_plate = fleetx_vehicle_map.read_export_vehicles(
            settings.fleetx_vehicle_map_file
        )
    except fleetx_vehicle_map.ExportFileError as exc:
        raise SyncAborted(f"Vehicle export rejected: {exc}") from exc
    if not any(dimension_sync.map_tag(t) for v in export.values() for t in v.tags):
        raise SyncAborted(
            f"No vehicle in '{settings.fleetx_vehicle_map_file.name}' carries a known customer "
            "tag -- refusing to sync against it (wrong or corrupt export?)."
        )

    if not dry_run:
        # Non-destructive: creates missing tables / adds new NULLABLE columns.
        bq_client.ensure_schema(client, settings)
    current_vehicles = _read_current(client, settings.dim_vehicle_table_ref, dry_run)
    current_customers = _read_current(client, settings.dim_customer_table_ref, dry_run)

    master: dict[str, dict] = {}
    if settings.dim_vehicle_master_file.exists():
        master_df = vehicle_master.read_and_clean_vehicle_master(settings.dim_vehicle_master_file)
        master = master_df.set_index("base_license_plate").to_dict("index")
    else:
        logger.warning(
            "Vehicle master file '%s' not found -- relying on existing table values and derived data.",
            settings.dim_vehicle_master_file,
        )

    candidate_plates = sorted(
        set(export) | set(current_vehicles.get("base_license_plate", pd.Series(dtype=str)).astype(str))
    )
    telemetry = _derive_type_model_by_plate(
        bq_client.get_vehicle_type_model_rows(
            client, settings, [p for p in candidate_plates if p not in master]
        )
    )
    starting_odometer = _derive_starting_odometer_by_plate(
        bq_client.get_odometer_rows_by_plate(client, settings)
    )

    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    vehicle_plan = dimension_sync.plan_vehicles(
        dimension_sync.VehicleInputs(
            current=current_vehicles,
            export=export,
            master=master,
            telemetry_type_model=telemetry,
            starting_odometer=starting_odometer,
            fleetx_id_overrides=FLEETX_ID_MANUAL_OVERRIDES,
        ),
        now,
    )
    customer_plan = dimension_sync.plan_customers(
        current_customers, set(vehicle_plan.rows["customer_name"].dropna())
    )

    active = current_vehicles.get("is_active", pd.Series(dtype=object))
    print(
        dimension_sync.render_plan(
            vehicle_plan,
            customer_plan,
            {
                "export_file": settings.fleetx_vehicle_map_file,
                "export_vehicles": len(export),
                "export_skipped_non_plate": skipped_non_plate,
                "current_total": len(current_vehicles),
                "current_active": len(current_vehicles) - int(active.map(lambda v: v is False).sum()),
            },
        )
    )

    if not vehicle_plan.has_changes and not customer_plan.has_changes:
        return "no-changes"
    if dry_run:
        print("\nDry run -- nothing written.")
        return "dry-run"

    _confirm(vehicle_plan, assume_yes, interactive, prompt)

    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    backups = []
    for table_ref, current in (
        (settings.dim_customer_table_ref, current_customers),
        (settings.dim_vehicle_table_ref, current_vehicles),
    ):
        if not current.empty:
            backups.append(
                (table_ref, bq_client.snapshot_table(client, table_ref, stamp, BACKUP_RETENTION_DAYS))
            )

    bq_client.merge_rows(
        client, settings.dim_customer_table_ref, customer_plan.rows, DIM_CUSTOMER_SCHEMA, "customer_name"
    )
    bq_client.merge_rows(
        client,
        settings.dim_vehicle_table_ref,
        _vehicle_load_frame(vehicle_plan.rows),
        DIM_VEHICLE_SCHEMA,
        "base_license_plate",
    )

    print(
        f"\nApplied: {len(vehicle_plan.added)} added, {len(vehicle_plan.changed)} changed, "
        f"{len(vehicle_plan.deactivated)} deactivated, {len(vehicle_plan.reactivated)} reactivated, "
        f"{len(customer_plan.added)} customer(s) added."
    )
    if backups:
        print(f"Backups (kept {BACKUP_RETENTION_DAYS} days). To undo, run in BigQuery:")
        for table_ref, snapshot_ref in backups:
            print(f"  CREATE OR REPLACE TABLE `{table_ref}` CLONE `{snapshot_ref}`;")

    rows = vehicle_plan.rows.set_index("base_license_plate")
    onboarded = vehicle_plan.added + vehicle_plan.reactivated
    to_backfill = [p for p in onboarded if pd.notna(rows.loc[p, "fleetx_id"])]
    no_device = sorted(set(onboarded) - set(to_backfill))
    if no_device:
        print(f"\nNo Fleetx device resolved for {', '.join(no_device)} -- no history to pull.")
    if to_backfill and backfill:
        print(f"\nPulling history for {len(to_backfill)} new/reactivated vehicle(s)...")
        backfill_fn = backfill_fn or vehicle_backfill.backfill
        report = backfill_fn(settings, to_backfill, client=client)
        print(report.summary())
        _refresh_starting_odometer(client, settings, vehicle_plan.rows, report.rows_by_plate, master)
    elif to_backfill:
        print(
            "\nHistory not pulled (--no-backfill). To pull it: uv run backfill-vehicles "
            + " ".join(to_backfill)
        )
    return "applied"
