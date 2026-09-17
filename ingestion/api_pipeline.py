"""Orchestrates the Fleetx-API shadow ingestion run (Phase 1 of the Excel->
API migration): pulls History Report trips for every dim_vehicle row with a
resolved fleetx_id, aggregates them to daily rows, and loads them into
utilization_daily_api. Does not touch utilization_daily, the Excel pipeline,
or anything backend/ reads -- entirely a parallel, throwaway-and-rerunnable
table for validating the API source before any cutover.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass

import pandas as pd
import requests

from ingestion import api_transform, bq_client, fleetx_client
from ingestion.config import Settings

logger = logging.getLogger(__name__)


@dataclass
class VehicleResult:
    base_license_plate: str
    fleetx_id: int
    status: str  # "loaded" | "no_trips" | "failed"
    row_count: int = 0
    error: str | None = None


def _to_epoch_ms(d: dt.date) -> int:
    return int(dt.datetime.combine(d, dt.time.min).timestamp() * 1000)


def run(
    settings: Settings,
    *,
    start_date: dt.date,
    end_date: dt.date,
    dry_run: bool = False,
) -> list[VehicleResult]:
    client = bq_client.get_client(settings)
    if not dry_run:
        bq_client.ensure_utilization_api_table(client, settings)

    vehicles = bq_client.get_vehicle_fleetx_ids(client, settings)
    logger.info(
        "Pulling trips for %d vehicle(s), %s to %s", len(vehicles), start_date, end_date
    )

    token = fleetx_client.login()
    from_ms = _to_epoch_ms(start_date)
    to_ms = _to_epoch_ms(end_date + dt.timedelta(days=1))

    results: list[VehicleResult] = []
    daily_frames: list[pd.DataFrame] = []

    for plate, fleetx_id in vehicles:
        try:
            trips = fleetx_client.get_trips(token, fleetx_id, from_ms, to_ms)
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 401:
                # Token can expire mid-run for a large fleet -- relogin once.
                logger.info("Access token expired mid-run, re-authenticating.")
                token = fleetx_client.login()
                try:
                    trips = fleetx_client.get_trips(token, fleetx_id, from_ms, to_ms)
                except requests.RequestException as retry_exc:
                    logger.exception("Failed to fetch trips for %s after re-login", plate)
                    results.append(
                        VehicleResult(plate, fleetx_id, "failed", error=str(retry_exc))
                    )
                    continue
            else:
                logger.exception("Failed to fetch trips for %s", plate)
                results.append(VehicleResult(plate, fleetx_id, "failed", error=str(exc)))
                continue
        except requests.RequestException as exc:
            logger.exception("Failed to fetch trips for %s", plate)
            results.append(VehicleResult(plate, fleetx_id, "failed", error=str(exc)))
            continue

        daily_df = api_transform.aggregate_trips_to_daily(trips, plate, fleetx_id)
        if daily_df.empty:
            results.append(VehicleResult(plate, fleetx_id, "no_trips"))
            continue

        daily_frames.append(daily_df)
        results.append(VehicleResult(plate, fleetx_id, "loaded", row_count=len(daily_df)))

    if not daily_frames:
        logger.warning("No trips found for any vehicle in the requested range.")
        return results

    combined = pd.concat(daily_frames, ignore_index=True)

    if dry_run:
        logger.info("[dry-run] Would load %d row(s) into %s", len(combined), settings.utilization_api_table_ref)
        return results

    bq_client.load_utilization_api_rows(client, settings, combined)
    logger.info("Loaded %d row(s) into %s", len(combined), settings.utilization_api_table_ref)
    return results
