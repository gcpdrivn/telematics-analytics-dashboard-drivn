"""Orchestrates the Fleetx-API ingestion run: pulls History Report trips for
every dim_vehicle row with a resolved fleetx_id, aggregates them to daily
rows, and loads them into utilization_daily_api. Never touches
utilization_daily or the Excel pipeline.

utilization_daily_api is the live dashboard's data source as of the
Excel->API cutover (UTILIZATION_SOURCE=api in production) -- this is no
longer a throwaway validation table. Safe to re-run for any --from/--to
range: load_utilization_api_rows() only replaces rows within that range,
so a daily run (yesterday only) or an ad-hoc backfill never touches data
outside what it was actually asked to load.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Collection
from dataclasses import dataclass

import pandas as pd
import requests

from ingestion import api_transform, bq_client, fleetx_client, fleetx_vehicle_map
from ingestion.config import Settings

logger = logging.getLogger(__name__)

# A trip ending right around midnight can occasionally land on the calendar
# day after `end_date` (a day-boundary/timezone rounding quirk in how the
# from/to epoch window lines up with Fleetx's own local-time bookkeeping,
# not a bug in a specific trip). This is the only legitimate spillover --
# bounds both how far the loaded rows and the delete window are allowed to
# extend past what was actually requested.
DATE_TOLERANCE = dt.timedelta(days=1)


@dataclass
class VehicleResult:
    base_license_plate: str
    fleetx_id: int
    status: str  # "loaded" | "no_trips" | "failed"
    row_count: int = 0
    error: str | None = None


def _to_epoch_ms(d: dt.date) -> int:
    return int(dt.datetime.combine(d, dt.time.min).timestamp() * 1000)


def _fetch_trips_relogin_once(
    token: str, fleetx_id: int, from_ms: int, to_ms: int
) -> tuple[list[dict], str]:
    """Calls get_trips, re-authenticating once and retrying if the token
    expired mid-run (common for a large fleet's full pull). Lets any other
    RequestException propagate to the caller."""
    try:
        return fleetx_client.get_trips(token, fleetx_id, from_ms, to_ms), token
    except requests.HTTPError as exc:
        if exc.response is None or exc.response.status_code != 401:
            raise
        logger.info("Access token expired mid-run, re-authenticating.")
        token = fleetx_client.login()
        return fleetx_client.get_trips(token, fleetx_id, from_ms, to_ms), token


def run(
    settings: Settings,
    *,
    start_date: dt.date,
    end_date: dt.date,
    dry_run: bool = False,
    plates: Collection[str] | None = None,
) -> list[VehicleResult]:
    """`plates` limits the run to those vehicles (a per-vehicle backfill):
    only they are fetched, and the scoped replace in BigQuery deletes only
    their rows -- and only for the ones whose fetch succeeded, so a failed
    fetch never blanks a vehicle's existing data."""
    client = bq_client.get_client(settings)
    if not dry_run:
        bq_client.ensure_utilization_api_table(client, settings)

    vehicles = bq_client.get_vehicle_fleetx_ids(client, settings)
    if plates is not None:
        wanted = set(plates)
        vehicles = [(p, fid) for p, fid in vehicles if p in wanted]
        missing = sorted(wanted - {p for p, _ in vehicles})
        if missing:
            logger.warning(
                "No active dim_vehicle row with a fleetx_id for %s -- not fetched.", ", ".join(missing)
            )
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
            trips, token = _fetch_trips_relogin_once(token, fleetx_id, from_ms, to_ms)
        except requests.RequestException as exc:
            logger.exception("Failed to fetch trips for %s", plate)
            results.append(VehicleResult(plate, fleetx_id, "failed", error=str(exc)))
            continue

        # A resolved fleetx_id with zero trips over the whole range can mean
        # the device is genuinely idle -- but it can also mean a stale
        # admin-side device label (found for DL1PD9284: its labeled 'OBD'
        # device is dead, while its API/AIS140 device is active). Try the
        # vehicle's other known non-DashCam devices before giving up.
        used_fleetx_id = fleetx_id
        if not trips and settings.fleetx_vehicle_map_file.exists():
            candidates = fleetx_vehicle_map.non_dashcam_candidate_ids(
                settings.fleetx_vehicle_map_file, plate
            )
            for alt_id in candidates:
                if alt_id == fleetx_id:
                    continue
                try:
                    alt_trips, token = _fetch_trips_relogin_once(token, alt_id, from_ms, to_ms)
                except requests.RequestException:
                    continue
                if alt_trips:
                    logger.warning(
                        "%s: primary fleetx_id=%d returned 0 trips -- falling back to "
                        "fleetx_id=%d for this run, which has data. Fix dim_vehicle.fleetx_id "
                        "at the source (correct the device grouping and re-run "
                        "seed-dimensions) so future runs don't need this fallback.",
                        plate, fleetx_id, alt_id,
                    )
                    trips = alt_trips
                    used_fleetx_id = alt_id
                    break

        daily_df = api_transform.aggregate_trips_to_daily(trips, plate, used_fleetx_id)
        if daily_df.empty:
            results.append(VehicleResult(plate, fleetx_id, "no_trips"))
            continue

        daily_frames.append(daily_df)
        results.append(VehicleResult(plate, fleetx_id, "loaded", row_count=len(daily_df)))

    if not daily_frames:
        logger.warning("No trips found for any vehicle in the requested range.")
        return results

    combined = pd.concat(daily_frames, ignore_index=True)

    # A trip that never closes (confirmed live: HR55BE9129's trip
    # 03038698 from 2026-08-17 has eDate=null and a duration that's still
    # growing every time it's queried) comes back from Fleetx on EVERY
    # request for that vehicle, regardless of the from/to window asked
    # for -- not just the one it actually happened on. aggregate_trips_to_
    # daily() falls back to sDate when eDate is missing, so it lands on
    # its original (real) date, which can be months before `start_date`.
    # Bounded here rather than trusted: report_date must fall within the
    # requested range plus DATE_TOLERANCE (the one legitimate case of a
    # trip landing a day past end_date -- see _to_epoch_ms). Anything
    # further out is dropped, not loaded -- and load_utilization_api_rows'
    # delete window uses this same fixed bound, not whatever range the
    # fetched data happens to span, so a stuck trip like this one can
    # never again cause a delete far wider than what was actually asked
    # for (it did, once, in production -- see git history).
    in_range = (combined["report_date"] >= start_date) & (
        combined["report_date"] <= end_date + DATE_TOLERANCE
    )
    if (~in_range).any():
        dropped = combined.loc[~in_range, ["base_license_plate", "report_date"]]
        for _, row in dropped.drop_duplicates().iterrows():
            logger.warning(
                "%s: dropping a row dated %s -- outside the requested %s..%s "
                "range (+%d day tolerance), likely a stuck-open trip Fleetx "
                "keeps returning regardless of the query window.",
                row["base_license_plate"], row["report_date"], start_date, end_date,
                DATE_TOLERANCE.days,
            )
        combined = combined[in_range]

    if combined.empty:
        logger.warning("No in-range trips left for any vehicle after date-bounds filtering.")
        return results

    if dry_run:
        logger.info("[dry-run] Would load %d row(s) into %s", len(combined), settings.utilization_api_table_ref)
        return results

    fetched_plates = None
    if plates is not None:
        fetched_plates = [r.base_license_plate for r in results if r.status != "failed"]
    bq_client.load_utilization_api_rows(
        client, settings, combined, start_date, end_date + DATE_TOLERANCE, plates=fetched_plates
    )
    logger.info("Loaded %d row(s) into %s", len(combined), settings.utilization_api_table_ref)
    return results
