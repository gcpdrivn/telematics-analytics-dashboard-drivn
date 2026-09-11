"""Loads BigQuery tables into pandas, with an in-process TTL cache.

Columns are renamed back to the originals used throughout
Analysis/generate_presentation_report.py (e.g. 'Base License Plate', not
'base_license_plate') so backend/metrics.py can be a near-verbatim port of
that script rather than a column-name-translated rewrite.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import threading
import time

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

CACHE_TTL_SECONDS = float(os.environ.get("BACKEND_CACHE_TTL_SECONDS", "300"))


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

    raw_df = _query_df(client, f"SELECT * FROM `{settings.utilization_table_ref}`")
    raw_df = raw_df.rename(columns=_UTILIZATION_RENAMES)

    dim_vehicle = _query_df(client, f"SELECT * FROM `{settings.dim_vehicle_table_ref}`")
    dim_customer = _query_df(client, f"SELECT * FROM `{settings.dim_customer_table_ref}`")
    mileage_df = _query_df(client, f"SELECT * FROM `{settings.mileage_soc_table_ref}`")

    return {
        "raw_utilization": raw_df,
        "dim_vehicle": dim_vehicle,
        "dim_customer": dim_customer,
        "mileage": mileage_df,
    }


def get_tables() -> dict[str, pd.DataFrame]:
    """Cached BigQuery reads (TTL: BACKEND_CACHE_TTL_SECONDS, default 300s)."""
    return _cache.get(_load_all)


def refresh() -> None:
    """Force the next request to re-query BigQuery and recompute derived data."""
    _cache.invalidate()
    _derived_cache.invalidate()


def get_clean_context(
    start_date: dt.date | None = None, end_date: dt.date | None = None
) -> dict:
    """Everything routers need: the cleaned/deduped/customer-filtered
    utilization frame, the dimension tables, the mileage lookup, the
    per-vehicle aggregation (active_stats), and the number of distinct report
    dates in scope (used as the KPI daily-average denominator instead of a
    literal 30).

    The unfiltered case (no start/end date -- the only case the frontend
    actually uses today) is itself cached: clean_and_join + build_active_stats
    involve several groupby().apply() calls that are ~0.4s combined, and every
    endpoint call was redoing them from scratch even when nothing had
    changed. A date-filtered request always recomputes, since tenure/active
    days depend on exactly which rows are in scope."""
    tables = get_tables()

    if start_date is None and end_date is None:
        derived = _derived_cache.get(lambda: _build_derived(tables))
        df_clean = derived["df_clean"]
        mileage_valid = derived["mileage_valid"]
        active_stats = derived["active_stats"]
        crosstab_matrix = derived["crosstab_matrix"]
    else:
        derived = _build_derived(tables)
        df_clean = derived["df_clean"]
        if start_date is not None:
            df_clean = df_clean[df_clean["Report Date"] >= pd.Timestamp(start_date)]
        if end_date is not None:
            df_clean = df_clean[df_clean["Report Date"] <= pd.Timestamp(end_date)]
        mileage_valid = derived["mileage_valid"]
        active_stats = metrics.build_active_stats(df_clean, mileage_valid)
        crosstab_matrix = metrics.build_crosstab_matrix(df_clean)

    observation_days = float(df_clean["Report Date"].dt.normalize().nunique()) or 1.0

    return {
        "df_clean": df_clean,
        "dim_customer": tables["dim_customer"],
        "dim_vehicle": tables["dim_vehicle"],
        "mileage_valid": mileage_valid,
        "active_stats": active_stats,
        "crosstab_matrix": crosstab_matrix,
        "observation_days": observation_days,
    }


def _build_derived(tables: dict[str, pd.DataFrame]) -> dict:
    df_clean = metrics.clean_and_join(
        tables["raw_utilization"], tables["dim_vehicle"], tables["dim_customer"]
    )
    mileage_valid = metrics.valid_mileage_map(tables["mileage"])
    active_stats = metrics.build_active_stats(df_clean, mileage_valid)
    # build_crosstab_matrix computes all 4 customer views (All/FreshBus/ZingBus/
    # BillionE) in one pass regardless of which one a request asks for, so it
    # belongs in the shared cache rather than being redone per customer filter.
    crosstab_matrix = metrics.build_crosstab_matrix(df_clean)
    return {
        "df_clean": df_clean,
        "mileage_valid": mileage_valid,
        "active_stats": active_stats,
        "crosstab_matrix": crosstab_matrix,
    }
