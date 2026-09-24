"""Loads BigQuery tables into pandas, with an in-process TTL cache.

Columns are renamed back to the originals used throughout
Analysis/generate_presentation_report.py (e.g. 'Base License Plate', not
'base_license_plate') so backend/metrics.py can be a near-verbatim port of
that script rather than a column-name-translated rewrite.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
from google.cloud import bigquery
from google.oauth2 import service_account

from backend import metrics
from ingestion.config import Settings, load_settings

_UTILIZATION_RENAMES = {
    "license_plate_raw": "License Plate / Vehicle Number",
    "base_license_plate": "Base License Plate",
    "distance_km": "Distance",
    "vehicle_name": "Vehicle Name",
    "running_time_hours": "Running Time (in hours)",
    "group_name": "Group Name",
    "gps_disconnection_count": "GPS Disconnection count",
    "vehicle_type": "Vehicle Type",
    "vehicle_maker": "Vehicle Maker",
    "vehicle_model": "Vehicle Model",
    "avg_daily_operating_hours": "Avg. Daily Operating Time (in hours)",
    "stoppages_count": "Stoppages Count",
    "max_speed": "Max Speed",
    "start_location": "Start Location",
    "end_location": "End Location",
    "average_speed": "Average Speed",
    "start_date": "Start Date",
    "end_date": "End Date",
    "closing_odometer": "Closing Odometer",
    "opening_odometer": "Opening Odometer",
    "report_date": "Report Date",
}

_ODOMETER_RESOLVED_RENAMES = {
    "base_license_plate": "Base License Plate",
    "report_date": "Report Date",
    "distance_by_odometer": "Distance By Odometer",
    "boundary_gap_distance": "Boundary Gap Distance",
}

CACHE_TTL_SECONDS = float(os.environ.get("BACKEND_CACHE_TTL_SECONDS", "300"))

# Which utilization table the dashboard reads: "excel" (utilization_daily,
# the default -- what's been live all along) or "api" (utilization_daily_api,
# the Fleetx-API-sourced shadow table from the Excel->API migration). Both
# share UTILIZATION_SCHEMA exactly, so switching is just picking a table --
# no other backend code needs to change. Not a per-request toggle: pick one,
# restart the service (Cloud Run picks up the new env var on redeploy), and
# the source in use is visible at GET /api/health.
_VALID_UTILIZATION_SOURCES = {"excel", "api"}
UTILIZATION_SOURCE = os.environ.get("UTILIZATION_SOURCE", "excel").strip().lower()
if UTILIZATION_SOURCE not in _VALID_UTILIZATION_SOURCES:
    raise RuntimeError(
        f"UTILIZATION_SOURCE={UTILIZATION_SOURCE!r} is invalid -- must be one of "
        f"{sorted(_VALID_UTILIZATION_SOURCES)}."
    )

# Which daily-distance number every KPI/chart/uptime-classification in this
# backend uses: "reported" (default -- the Distance field as telemetry
# reported it) or "odometer" (distance_by_odometer from
# odometer_daily_resolved, a backfilled/fault-filtered number derived from
# Closing/Opening Odometer -- see ingestion/odometer_resolver.py). Same rule
# as UTILIZATION_SOURCE above: not a per-request toggle, read once at
# startup, restart the service to change it. Active source is visible at
# GET /api/health.
_VALID_DISTANCE_SOURCES = {"reported", "odometer"}
DISTANCE_SOURCE = os.environ.get("DISTANCE_SOURCE", "reported").strip().lower()
if DISTANCE_SOURCE not in _VALID_DISTANCE_SOURCES:
    raise RuntimeError(
        f"DISTANCE_SOURCE={DISTANCE_SOURCE!r} is invalid -- must be one of "
        f"{sorted(_VALID_DISTANCE_SOURCES)}."
    )

logger = logging.getLogger(__name__)


class _TTLCache:
    def __init__(self, ttl_seconds: float):
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        self._data: dict[str, pd.DataFrame] | None = None
        self._loaded_at: float = 0.0

    def get(self, loader) -> dict[str, pd.DataFrame]:
        with self._lock:
            now = time.monotonic()
            if self._data is None or (now - self._loaded_at) > self._ttl:
                self._data = loader()
                self._loaded_at = now
            return self._data

    def invalidate(self) -> None:
        with self._lock:
            self._data = None


_cache = _TTLCache(CACHE_TTL_SECONDS)
_derived_cache = _TTLCache(CACHE_TTL_SECONDS)
_crosstab_cache = _TTLCache(CACHE_TTL_SECONDS)
_client: bigquery.Client | None = None
_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = load_settings()
    return _settings


def get_client() -> bigquery.Client:
    """Local dev and Cloud Run both resolve BigQuery credentials via ADC
    (a personal `gcloud auth application-default login` locally, an attached
    service account on Cloud Run) -- no code path needed for either. Netlify
    Functions has no attached-identity mechanism, so it needs an explicit
    service-account key. Set GCP_SERVICE_ACCOUNT_JSON (the key file's full
    JSON contents, as a single env var) only in that environment; leave it
    unset everywhere else and ADC keeps working exactly as before."""
    global _client
    if _client is None:
        sa_json = os.environ.get("GCP_SERVICE_ACCOUNT_JSON")
        if sa_json:
            credentials = service_account.Credentials.from_service_account_info(
                json.loads(sa_json)
            )
            _client = bigquery.Client(
                project=get_settings().gcp_project_id, credentials=credentials
            )
        else:
            _client = bigquery.Client(project=get_settings().gcp_project_id)
    return _client


def _query_df(client: bigquery.Client, sql: str) -> pd.DataFrame:
    """Builds a DataFrame from the row iterator directly instead of
    `QueryJob.to_dataframe()`. That convenience method hard-requires
    `db-dtypes`, which hard-requires `pyarrow` (152MB) -- fine for local/
    Cloud Run use, but blows well past Netlify Functions' 250MB unzipped
    limit. Row values already come back as native Python types (str, float,
    datetime.date/datetime, etc.) from the REST API, so this needs no extra
    dependency at all; metrics.clean_and_join() already calls
    pd.to_datetime() explicitly on the date columns regardless of whatever
    dtype pandas infers here."""
    rows = client.query(sql).result()
    return pd.DataFrame([dict(row) for row in rows])


def _load_all() -> dict[str, pd.DataFrame]:
    settings = get_settings()
    client = get_client()

    utilization_table_ref = (
        settings.utilization_api_table_ref
        if UTILIZATION_SOURCE == "api"
        else settings.utilization_table_ref
    )
    # Today is always a partial day (the vehicle's day isn't over yet), so a
    # KPI built from it looks like a data dip rather than what it is --
    # excluded here, at the source query, rather than trusted to whatever
    # date range a caller happens to pass. "Today" is IST: report_date is an
    # IST calendar date (from Fleetx trip timestamps / Excel exports), and
    # BigQuery's CURRENT_DATE() defaults to UTC, which would cut off up to
    # 5.5 hours into the wrong day.
    utilization_sql = (
        f"SELECT * FROM `{utilization_table_ref}` "
        "WHERE report_date < CURRENT_DATE('Asia/Kolkata')"
    )

    # These queries are independent, but run one at a time added up to ~5s of
    # pure network/query-planning round-trips on a cold cache -- BigQuery's
    # Python client releases the GIL during the network wait, so a thread per
    # query turns that sum into just the slowest one. The odometer query only
    # joins the batch when DISTANCE_SOURCE actually needs it -- no point
    # paying for a 5th round-trip in "reported" mode.
    with ThreadPoolExecutor(max_workers=5) as pool:
        raw_future = pool.submit(_query_df, client, utilization_sql)
        dim_vehicle_future = pool.submit(
            _query_df, client, f"SELECT * FROM `{settings.dim_vehicle_table_ref}`"
        )
        dim_customer_future = pool.submit(
            _query_df, client, f"SELECT * FROM `{settings.dim_customer_table_ref}`"
        )
        mileage_future = pool.submit(
            _query_df, client, f"SELECT * FROM `{settings.mileage_soc_table_ref}`"
        )
        odometer_future = (
            pool.submit(
                _query_df,
                client,
                "SELECT base_license_plate, report_date, distance_by_odometer, "
                "boundary_gap_distance "
                f"FROM `{settings.odometer_resolved_table_ref}`",
            )
            if DISTANCE_SOURCE == "odometer"
            else None
        )
        raw_df = raw_future.result().rename(columns=_UTILIZATION_RENAMES)
        dim_vehicle = dim_vehicle_future.result()
        dim_customer = dim_customer_future.result()
        mileage_df = mileage_future.result()
        odometer_df = (
            odometer_future.result().rename(columns=_ODOMETER_RESOLVED_RENAMES)
            if odometer_future is not None
            else None
        )

    return {
        "raw_utilization": raw_df,
        "dim_vehicle": dim_vehicle,
        "dim_customer": dim_customer,
        "mileage": mileage_df,
        "odometer_resolved": odometer_df,
    }


def get_tables() -> dict[str, pd.DataFrame]:
    """Cached BigQuery reads (TTL: BACKEND_CACHE_TTL_SECONDS, default 300s)."""
    return _cache.get(_load_all)


def refresh() -> None:
    """Force the next request to re-query BigQuery and recompute derived data."""
    _cache.invalidate()
    _derived_cache.invalidate()
    _crosstab_cache.invalidate()


def _apply_distance_source(df_clean: pd.DataFrame, odometer_df: pd.DataFrame) -> pd.DataFrame:
    """Overwrites df_clean's Distance column with the resolved odometer-based
    distance, in place of the raw reported Distance field -- the single
    substitution point every KPI/chart/uptime-classification consumer
    downstream inherits automatically (see the plan this implements).
    Prefers the resolved value but falls back to the original reported
    Distance for any row the resolver hasn't covered (a day landed after the
    last `resolve-odometer` run, or a genuinely UNRESOLVED row) rather than
    ever leaving a hole."""
    odometer_df = odometer_df.copy()
    odometer_df["Report Date"] = pd.to_datetime(odometer_df["Report Date"])
    merged = df_clean.merge(
        odometer_df, on=["Base License Plate", "Report Date"], how="left"
    )
    resolved = merged["Distance By Odometer"]
    fallback_count = int(resolved.isna().sum())
    if fallback_count:
        logger.warning(
            "%d/%d row(s) (%.1f%%) had no resolved odometer distance -- using "
            "reported Distance for those. Run `uv run resolve-odometer` if "
            "this looks stale.",
            fallback_count, len(merged), 100.0 * fallback_count / len(merged),
        )
    df_clean = df_clean.copy()
    df_clean["Distance"] = resolved.where(resolved.notna(), df_clean["Distance"]).to_numpy()
    # Kept as its own column (never folded into "Distance") -- it isn't
    # attributable to a specific day, so it shouldn't distort daily rates,
    # active-day counts, or crosstab bands. Only summed at the KPI level
    # (generate_kpis_for_scope) into the cumulative totals.
    df_clean["Boundary Gap Distance"] = merged["Boundary Gap Distance"].to_numpy()
    return df_clean


def _clean_df(tables: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, dict]:
    df_clean = metrics.clean_and_join(
        tables["raw_utilization"], tables["dim_vehicle"], tables["dim_customer"]
    )
    if DISTANCE_SOURCE == "odometer":
        df_clean = _apply_distance_source(df_clean, tables["odometer_resolved"])
    else:
        # Boundary-gap distance is derived from odometer readings, so it's
        # only meaningful/fetched in odometer mode -- build_active_stats
        # still expects the column to exist either way, just summing to 0
        # here rather than enriching a "reported"-mode total with a number
        # the dashboard isn't otherwise trusting.
        df_clean["Boundary Gap Distance"] = float("nan")
    mileage_valid = metrics.valid_mileage_map(tables["mileage"])
    return df_clean, mileage_valid


def _filter_by_date(
    df_clean: pd.DataFrame, start_date: dt.date | None, end_date: dt.date | None
) -> pd.DataFrame:
    if start_date is not None:
        df_clean = df_clean[df_clean["Report Date"] >= pd.Timestamp(start_date)]
    if end_date is not None:
        df_clean = df_clean[df_clean["Report Date"] <= pd.Timestamp(end_date)]
    return df_clean


def get_clean_context(
    start_date: dt.date | None = None, end_date: dt.date | None = None
) -> dict:
    """Everything routers need except the crosstab matrix (see
    get_crosstab_matrix): the cleaned/deduped/customer-filtered utilization
    frame, the dimension tables, the mileage lookup, the per-vehicle
    aggregation (active_stats), and the number of distinct report dates in
    scope (used as the KPI daily-average denominator instead of a literal
    30).

    The unfiltered case (no start/end date -- the only case the frontend
    actually uses today) is itself cached: clean_and_join + build_active_stats
    involve several groupby().apply() calls, and every endpoint call was
    redoing them from scratch even when nothing had changed. A date-filtered
    request always recomputes, since tenure/active days depend on exactly
    which rows are in scope."""
    tables = get_tables()

    if start_date is None and end_date is None:
        derived = _derived_cache.get(lambda: _build_derived(tables))
        df_clean = derived["df_clean"]
        mileage_valid = derived["mileage_valid"]
        active_stats = derived["active_stats"]
        observation_days = derived["observation_days"]
    else:
        df_clean, mileage_valid = _clean_df(tables)
        df_clean = _filter_by_date(df_clean, start_date, end_date)
        # Recomputed for the filtered window, not reused from the unfiltered
        # `derived` bundle -- Bus tenure (build_active_stats) depends on this
        # being the size of the window actually in scope here.
        observation_days = float(df_clean["Report Date"].dt.normalize().nunique()) or 1.0
        active_stats = metrics.build_active_stats(df_clean, mileage_valid, observation_days)

    return {
        "df_clean": df_clean,
        "dim_customer": tables["dim_customer"],
        "dim_vehicle": tables["dim_vehicle"],
        "mileage_valid": mileage_valid,
        "active_stats": active_stats,
        "observation_days": observation_days,
    }


def get_crosstab_matrix(
    start_date: dt.date | None = None, end_date: dt.date | None = None
) -> dict:
    """Separate from get_clean_context: build_crosstab_matrix is the one
    expensive, crosstab-router-only computation in this module (an O(dates x
    vehicles) per-row loop) -- every other endpoint (kpi-summary, customers,
    vehicles, trajectories, uptime) never touches its result, so it doesn't
    belong in the shared context every one of them pays for."""
    tables = get_tables()
    if start_date is None and end_date is None:
        return _crosstab_cache.get(
            lambda: metrics.build_crosstab_matrix(_clean_df(tables)[0])
        )
    df_clean, _ = _clean_df(tables)
    df_clean = _filter_by_date(df_clean, start_date, end_date)
    return metrics.build_crosstab_matrix(df_clean)


def _build_derived(tables: dict[str, pd.DataFrame]) -> dict:
    df_clean, mileage_valid = _clean_df(tables)
    observation_days = float(df_clean["Report Date"].dt.normalize().nunique()) or 1.0
    active_stats = metrics.build_active_stats(df_clean, mileage_valid, observation_days)
    return {
        "df_clean": df_clean,
        "mileage_valid": mileage_valid,
        "active_stats": active_stats,
        "observation_days": observation_days,
    }
