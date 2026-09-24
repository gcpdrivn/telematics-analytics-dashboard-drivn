"""Pulls the full history of specific vehicles right after they're onboarded
(or reactivated) by seed-dimensions, so they appear on the dashboard at once
instead of accumulating one day per nightly run.

Steps, for the given plates only:
1. api_pipeline.run(plates=...) over [start, end], in CHUNK_DAYS windows --
   each chunk replaces only these vehicles' rows (see
   bq_client.load_utilization_api_rows), never anyone else's.
2. odometer_resolver.run() -- a full recompute (cheap), so odometer-based
   distance covers the new rows.
3. POST <BACKEND_URL>/api/admin/refresh-cache, so the live dashboard drops
   its cached tables now rather than after the TTL.
A failure in step 2 or 3 is reported, not raised: the data is already loaded
and each step can be re-run on its own.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

import requests

from ingestion import api_pipeline, bq_client, odometer_resolver
from ingestion.config import Settings

logger = logging.getLogger(__name__)

# Keeps each Fleetx History Report request to about a month of trips.
CHUNK_DAYS = 31

# Used only if utilization_daily_api is empty (nothing to align to).
DEFAULT_LOOKBACK_DAYS = 180


@dataclass
class BackfillReport:
    plates: list[str]
    start_date: dt.date
    end_date: dt.date
    rows_by_plate: dict[str, int] = field(default_factory=dict)
    failed: dict[str, str] = field(default_factory=dict)  # plate -> last error
    odometer_resolved: bool = False
    cache_refreshed: bool = False
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [f"Backfill {self.start_date}..{self.end_date} for {len(self.plates)} vehicle(s):"]
        for plate in self.plates:
            if plate in self.failed:
                lines.append(f"  {plate:<12} FAILED: {self.failed[plate]}")
            else:
                n = self.rows_by_plate.get(plate, 0)
                lines.append(f"  {plate:<12} {n} day(s) loaded" + ("" if n else " (no trips in range)"))
        lines.extend(f"  note: {n}" for n in self.notes)
        return "\n".join(lines)


def yesterday_ist() -> dt.date:
    return (dt.datetime.now(ZoneInfo("Asia/Kolkata")) - dt.timedelta(days=1)).date()


def _chunks(start: dt.date, end: dt.date, days: int):
    cur = start
    while cur <= end:
        chunk_end = min(cur + dt.timedelta(days=days - 1), end)
        yield cur, chunk_end
        cur = chunk_end + dt.timedelta(days=1)


def refresh_dashboard_cache(settings: Settings) -> bool:
    if not settings.backend_url:
        return False
    resp = requests.post(f"{settings.backend_url}/api/admin/refresh-cache", timeout=30)
    resp.raise_for_status()
    return True


def backfill(
    settings: Settings,
    plates: Collection[str],
    *,
    start_date: dt.date | None = None,
    end_date: dt.date | None = None,
    client=None,
    run_pipeline: Callable = api_pipeline.run,
    resolve_odometer: Callable = odometer_resolver.run,
    refresh_cache: Callable = refresh_dashboard_cache,
) -> BackfillReport:
    plates = sorted(set(plates))
    end_date = end_date or yesterday_ist()
    if start_date is None:
        client = client or bq_client.get_client(settings)
        start_date = bq_client.get_utilization_api_min_date(client, settings) or (
            end_date - dt.timedelta(days=DEFAULT_LOOKBACK_DAYS)
        )
    report = BackfillReport(plates=plates, start_date=start_date, end_date=end_date)
    if not plates or start_date > end_date:
        return report

    for chunk_start, chunk_end in _chunks(start_date, end_date, CHUNK_DAYS):
        logger.info("Backfilling %s for %s..%s", ", ".join(plates), chunk_start, chunk_end)
        for r in run_pipeline(settings, start_date=chunk_start, end_date=chunk_end, plates=plates):
            if r.status == "failed":
                report.failed[r.base_license_plate] = f"{chunk_start}..{chunk_end}: {r.error}"
            else:
                report.rows_by_plate[r.base_license_plate] = (
                    report.rows_by_plate.get(r.base_license_plate, 0) + r.row_count
                )

    if not any(report.rows_by_plate.values()):
        report.notes.append("no trip data found -- odometer resolve and cache refresh skipped.")
        return report

    try:
        resolve_odometer(settings)
        report.odometer_resolved = True
    except Exception as exc:  # noqa: BLE001 -- reported, re-runnable on its own
        logger.exception("resolve-odometer failed after backfill")
        report.notes.append(f"resolve-odometer failed ({exc}) -- run `uv run resolve-odometer`.")

    try:
        report.cache_refreshed = refresh_cache(settings)
        if not report.cache_refreshed:
            report.notes.append(
                "BACKEND_URL not set -- the dashboard shows the new data after its cache TTL (5 min)."
            )
    except Exception as exc:  # noqa: BLE001
        logger.exception("dashboard cache refresh failed")
        report.notes.append(f"dashboard cache refresh failed ({exc}) -- it updates after the TTL.")
    if report.failed:
        report.notes.append(
            "retry failed vehicles with: uv run backfill-vehicles " + " ".join(sorted(report.failed))
        )
    return report
