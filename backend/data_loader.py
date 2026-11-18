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
from pathlib import Path

import pandas as pd
from google.api_core.exceptions import NotFound
from google.cloud import bigquery
from google.oauth2 import service_account

from backend import metrics
from ingestion.api_transform import MAX_PLAUSIBLE_DAILY_DISTANCE_KM
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

# LOCAL-ONLY overlay: extra vehicles that exist in Fleetx without a number
# plate, built by local/build_extra_vehicles.py into pickles and merged in
# here. Off unless LOCAL_EXTRA_VEHICLES_DIR is set (only in a local .env --
# never on Cloud Run), so the deployed backend is unaffected.
_EXTRA_DIR = os.environ.get("LOCAL_EXTRA_VEHICLES_DIR", "").strip()
_BASE_FIXED_FLEET_SIZE = dict(metrics.FIXED_FLEET_SIZE)


def _extra(name: str) -> pd.DataFrame | None:
    if not _EXTRA_DIR:
        return None
    path = Path(_EXTRA_DIR)
    if not path.is_absolute():
        path = Path(__file__).resolve().parent.parent / path
    file = path / f"{name}.pkl"
    return pd.read_pickle(file) if file.exists() else None


# LOCAL-ONLY "Fleetx mirror": local/build_fleetx_mirror.py writes
# local/fleetx/{daily,energy,live}.pkl so the localhost dashboard shows the
# numbers the Fleetx app shows -- daily km = sum of Fleetx's own trip
# distances, odometer/SoH = Fleetx's merged vehicle view, km/kWh = Fleetx's
# own figures (copied as-is). Used only when LOCAL_FLEETX_DIR is set (a local
# .env line, never on Cloud Run), so the deployed backend is unaffected.
_FLEETX_DIR = os.environ.get("LOCAL_FLEETX_DIR", "").strip()
ENERGY_WINDOWS = (3, 7, 15)


def _fleetx(name: str) -> pd.DataFrame | None:
    if not _FLEETX_DIR:
        return None
    path = Path(_FLEETX_DIR)
    if not path.is_absolute():
        path = Path(__file__).resolve().parent.parent / path
    file = path / f"{name}.pkl"
    return pd.read_pickle(file) if file.exists() else None


def apply_fleetx_distance(df_clean: pd.DataFrame) -> pd.DataFrame:
    """Daily Distance = Fleetx's own trip-distance total for that vehicle-day
    (what Fleetx's History 'Daily KM's' shows), in place of the odometer
    difference. No-op unless the local Fleetx mirror is on."""
    daily = _fleetx("daily")
    if daily is None or daily.empty:
        return df_clean
    km = {(p, pd.Timestamp(d)): v for p, d, v in zip(daily["base_license_plate"], daily["date"], daily["km"])}
    df_clean = df_clean.copy()
    keys = zip(df_clean["Base License Plate"], df_clean["Report Date"].dt.normalize())
    fleetx_km = pd.Series([km.get(k) for k in keys], index=df_clean.index, dtype="float64")
    has = fleetx_km.notna()
    df_clean["Distance"] = fleetx_km.where(has, df_clean["Distance"])
    # Same plausibility cap as the ingestion pipeline: a day total at or above
    # it is a device fault (e.g. a 2,097,151.9 km overflow), blanked -- also
    # for the few days Fleetx's trip list lacks, which keep the stored value.
    df_clean.loc[df_clean["Distance"] >= MAX_PLAUSIBLE_DAILY_DISTANCE_KM, "Distance"] = float("nan")
    # Fleetx's totals carry no odometer boundary-gap distance.
    df_clean["Boundary Gap Distance"] = float("nan")
    return df_clean


def _with_energy(active_stats: pd.DataFrame) -> pd.DataFrame:
    """Adds the Fleetx mirror's per-vehicle figures: km/kWh (lifetime and
    last 3/7/15 days) and the odometer as the app shows it."""
    energy, daily, live = _fleetx("energy"), _fleetx("daily"), _fleetx("live")
    if energy is None:
        return active_stats
    active_stats = active_stats.copy()
    plates = active_stats["Base License Plate"]
    e = energy.set_index("base_license_plate")
    active_stats["energy_kwh"] = plates.map(e["energy_kwh"])
    active_stats["energy_km_per_kwh"] = plates.map(e["energy_km_per_kwh"])
    # Last 3 / 7 / 15 days, Fleetx's formula: all trip km / kWh of the trips
    # with an energy reading, ending on the last complete day in the file.
    if daily is not None and not daily.empty:
        end = max(daily["date"])
        windows: dict[str, dict] = {}
        for n in ENERGY_WINDOWS:
            part = daily[daily["date"] > end - dt.timedelta(days=n)]
            sums = part.groupby("base_license_plate")[["km", "kwh"]].sum()
            for plate, row in sums.iterrows():
                windows.setdefault(plate, {})[str(n)] = {
                    "km": round(float(row["km"]), 1),
                    "kwh": round(float(row["kwh"]), 1),
                    "km_per_kwh": round(float(row["km"] / row["kwh"]), 3) if row["kwh"] > 0 else None,
                }
        active_stats["energy_windows"] = plates.map(lambda p: windows.get(p))
        active_stats["energy_window_end"] = end.isoformat()
    # Odometer = the app's vehicle card (merged live view). A reading far
    # below the vehicle's own history (e.g. a merged view briefly showing
    # 46 km for a 12,000 km truck) is a Fleetx glitch: keep history then.
    if live is not None and not live.empty:
        fx_odo = plates.map(live.set_index("base_license_plate")["odometer_km"])
        hist = active_stats["final_odometer"]
        glitch = fx_odo.notna() & hist.notna() & (hist > 1000) & (fx_odo < 0.5 * hist)
        use = fx_odo.notna() & (fx_odo > 0) & ~glitch
        active_stats["final_odometer"] = fx_odo.where(use, hist)
        active_stats["live_odometer_km"] = active_stats["final_odometer"]
    return active_stats


def fleetx_odometer_total() -> dict | None:
    """Whole-fleet odometer = sum of the app's per-vehicle odometers (local
    Fleetx mirror only; None = use the normal calculation)."""
    live = _fleetx("live")
    if live is None or live.empty:
        return None
    stats = get_clean_context()["active_stats"]
    as_of = pd.to_datetime(live["reported_at"]).max()
    return {
        "total_odometer": round(float(stats["final_odometer"].sum(skipna=True))),
        "as_of_date": as_of.tz_convert("Asia/Kolkata").strftime("%Y-%m-%d") if pd.notna(as_of) else "",
    }


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
_soh_cache = _TTLCache(CACHE_TTL_SECONDS)
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

    # LOCAL-ONLY overlay (see _extra) -- a no-op unless LOCAL_EXTRA_VEHICLES_DIR is set.
    extra_util, extra_dim, extra_odo = _extra("utilization"), _extra("dim_vehicle"), _extra("odometer_resolved")
    if extra_util is not None and extra_dim is not None:
        raw_df = pd.concat([raw_df, extra_util.rename(columns=_UTILIZATION_RENAMES)], ignore_index=True)
        dim_vehicle = pd.concat([dim_vehicle, extra_dim], ignore_index=True)
        if odometer_df is not None and extra_odo is not None:
            odometer_df = pd.concat(
                [odometer_df, extra_odo[list(_ODOMETER_RESOLVED_RENAMES)].rename(columns=_ODOMETER_RESOLVED_RENAMES)],
                ignore_index=True,
            )
        # FreshBus/ZingBus use a fixed fleet size as the active-% denominator;
        # grow it by the extra buses so their active % can't exceed 100%.
        added = extra_dim["customer_name"].value_counts()
        for cust, base in _BASE_FIXED_FLEET_SIZE.items():
            metrics.FIXED_FLEET_SIZE[cust] = base + int(added.get(cust, 0))
        logger.info("Local overlay: added %d extra vehicle(s) from %s", len(extra_dim), _EXTRA_DIR)

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
    _soh_cache.invalidate()


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
    df_clean = apply_fleetx_distance(df_clean)  # LOCAL-ONLY, no-op unless LOCAL_FLEETX_DIR is set
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
        active_stats = _with_energy(metrics.build_active_stats(df_clean, mileage_valid, observation_days))

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
    active_stats = _with_energy(metrics.build_active_stats(df_clean, mileage_valid, observation_days))
    return {
        "df_clean": df_clean,
        "mileage_valid": mileage_valid,
        "active_stats": active_stats,
        "observation_days": observation_days,
    }


def _load_soh_odometer() -> pd.DataFrame:
    """vehicle_soh_latest (the morning Realtime API ping) joined to each
    vehicle's customer/type and its latest resolved odometer from history --
    the latter only to flag a live odometer that disagrees with it. Empty
    until the first `ingest-soh` run creates the table."""
    settings = get_settings()
    sql = f"""
        WITH history AS (
          SELECT base_license_plate, report_date, closing_odometer_clean,
                 ROW_NUMBER() OVER (PARTITION BY base_license_plate ORDER BY report_date DESC) AS rn
          FROM `{settings.odometer_resolved_table_ref}`
          WHERE closing_odometer_clean > 0
        )
        SELECT s.*, d.customer_name, d.vehicle_type, d.oem, d.vehicle_model,
               h.closing_odometer_clean AS history_odometer_km, h.report_date AS history_odometer_date
        FROM `{settings.soh_latest_table_ref}` s
        JOIN `{settings.dim_vehicle_table_ref}` d USING (base_license_plate)
        LEFT JOIN history h ON h.base_license_plate = s.base_license_plate AND h.rn = 1
    """
    try:
        df = _query_df(get_client(), sql)
    except NotFound:
        df = pd.DataFrame()

    # LOCAL-ONLY overlay (see _extra): same columns as the query above.
    extra_soh, extra_dim, extra_odo = _extra("soh_latest"), _extra("dim_vehicle"), _extra("odometer_resolved")
    if extra_soh is not None and extra_dim is not None and not extra_soh.empty:
        extra = extra_soh.merge(
            extra_dim[["base_license_plate", "customer_name", "vehicle_type", "oem", "vehicle_model"]],
            on="base_license_plate",
        )
        if extra_odo is not None:
            hist = (
                extra_odo[extra_odo["closing_odometer_clean"] > 0]
                .sort_values("report_date")
                .groupby("base_license_plate")
                .last()[["closing_odometer_clean", "report_date"]]
                .rename(columns={"closing_odometer_clean": "history_odometer_km", "report_date": "history_odometer_date"})
                .reset_index()
            )
            extra = extra.merge(hist, on="base_license_plate", how="left")
        df = pd.concat([df, extra], ignore_index=True)

    # LOCAL-ONLY Fleetx mirror: odometer and SoH as the app's vehicle card
    # shows them (merged view), where it has a value.
    live = _fleetx("live")
    if live is not None and not live.empty and not df.empty:
        lv = live.set_index("base_license_plate")
        plates = df["base_license_plate"]
        odo = plates.map(lv["odometer_km"])
        df["odometer_km"] = odo.where(odo.notna() & (odo > 0), df["odometer_km"])
        df["odometer_raw_km"] = df["odometer_km"]
        soh = plates.map(lv["soh_pct"])
        df["soh_pct"] = soh.where(soh.notna(), df["soh_pct"])
    return df


def get_soh_odometer() -> pd.DataFrame:
    return _soh_cache.get(_load_soh_odometer)
