"""BigQuery plumbing: dataset/table provisioning, dedup lookups, loading."""

from __future__ import annotations

import datetime as dt

import pandas as pd
from google.cloud import bigquery

from ingestion.config import Settings
from ingestion.schema import (
    DIM_CUSTOMER_SCHEMA,
    DIM_VEHICLE_SCHEMA,
    INGESTION_LOG_SCHEMA,
    MILEAGE_SOC_SCHEMA,
    ODOMETER_RESOLVED_SCHEMA,
    UTILIZATION_SCHEMA,
)


def get_client(settings: Settings) -> bigquery.Client:
    return bigquery.Client(project=settings.gcp_project_id)


def ensure_dataset(client: bigquery.Client, settings: Settings) -> None:
    dataset = bigquery.Dataset(f"{settings.gcp_project_id}.{settings.bq_dataset}")
    dataset.location = settings.bq_location
    client.create_dataset(dataset, exists_ok=True)


def ensure_utilization_table(client: bigquery.Client, settings: Settings) -> None:
    table = bigquery.Table(settings.utilization_table_ref, schema=UTILIZATION_SCHEMA)
    table.time_partitioning = bigquery.TimePartitioning(
        type_=bigquery.TimePartitioningType.MONTH, field="report_date"
    )
    table.clustering_fields = ["base_license_plate"]
    client.create_table(table, exists_ok=True)


def ensure_utilization_api_table(client: bigquery.Client, settings: Settings) -> None:
    """The Fleetx-API-sourced utilization table -- same UTILIZATION_SCHEMA as
    utilization_daily (originally so the two could be compared/diffed
    column-for-column during validation; now also the live dashboard's
    source table post-cutover)."""
    table = bigquery.Table(settings.utilization_api_table_ref, schema=UTILIZATION_SCHEMA)
    table.time_partitioning = bigquery.TimePartitioning(
        type_=bigquery.TimePartitioningType.MONTH, field="report_date"
    )
    table.clustering_fields = ["base_license_plate"]
    client.create_table(table, exists_ok=True)


def ensure_ingestion_log_table(client: bigquery.Client, settings: Settings) -> None:
    table = bigquery.Table(
        settings.ingestion_log_table_ref, schema=INGESTION_LOG_SCHEMA
    )
    table.clustering_fields = ["report_type"]
    client.create_table(table, exists_ok=True)


def ensure_mileage_soc_table(client: bigquery.Client, settings: Settings) -> None:
    table = bigquery.Table(settings.mileage_soc_table_ref, schema=MILEAGE_SOC_SCHEMA)
    client.create_table(table, exists_ok=True)


def ensure_dim_customer_table(client: bigquery.Client, settings: Settings) -> None:
    table = bigquery.Table(settings.dim_customer_table_ref, schema=DIM_CUSTOMER_SCHEMA)
    client.create_table(table, exists_ok=True)
    _add_missing_columns(client, settings.dim_customer_table_ref, DIM_CUSTOMER_SCHEMA)


def ensure_dim_vehicle_table(client: bigquery.Client, settings: Settings) -> None:
    table = bigquery.Table(settings.dim_vehicle_table_ref, schema=DIM_VEHICLE_SCHEMA)
    client.create_table(table, exists_ok=True)
    _add_missing_columns(client, settings.dim_vehicle_table_ref, DIM_VEHICLE_SCHEMA)


def _add_missing_columns(
    client: bigquery.Client, table_ref: str, schema: list[bigquery.SchemaField]
) -> None:
    """create_table(..., exists_ok=True) is a no-op against an already-existing
    table -- it never alters that table's live schema. So a field added to a
    schema.py definition after the table was first created (e.g.
    DIM_VEHICLE_SCHEMA's starting_odometer) needs an explicit ALTER TABLE, or
    every later load_*_rows() WRITE_TRUNCATE call fails with a schema
    mismatch. NULLABLE-only: BigQuery can't ADD COLUMN a REQUIRED field to a
    table that may already have rows, but nothing in DIM_VEHICLE_SCHEMA (or
    any schema this project has added a field to after the fact) needs that."""
    live_columns = {f.name for f in client.get_table(table_ref).schema}
    missing = [f for f in schema if f.name not in live_columns]
    if not missing:
        return
    clauses = ", ".join(f"ADD COLUMN {f.name} {f.field_type}" for f in missing)
    client.query(f"ALTER TABLE `{table_ref}` {clauses}").result()


def ensure_odometer_resolved_table(client: bigquery.Client, settings: Settings) -> None:
    table = bigquery.Table(
        settings.odometer_resolved_table_ref, schema=ODOMETER_RESOLVED_SCHEMA
    )
    table.clustering_fields = ["base_license_plate"]
    client.create_table(table, exists_ok=True)
    _add_missing_columns(client, settings.odometer_resolved_table_ref, ODOMETER_RESOLVED_SCHEMA)


def ensure_schema(client: bigquery.Client, settings: Settings) -> None:
    ensure_dataset(client, settings)
    ensure_utilization_table(client, settings)
    ensure_ingestion_log_table(client, settings)
    ensure_mileage_soc_table(client, settings)
    ensure_dim_customer_table(client, settings)
    ensure_dim_vehicle_table(client, settings)


def get_ingested_hashes(
    client: bigquery.Client, settings: Settings, report_type: str
) -> set[str]:
    """File hashes already recorded as ingested for this report type."""
    query = f"""
        SELECT file_hash
        FROM `{settings.ingestion_log_table_ref}`
        WHERE report_type = @report_type
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("report_type", "STRING", report_type)
        ]
    )
    rows = client.query(query, job_config=job_config).result()
    return {row.file_hash for row in rows}


def load_utilization_rows(
    client: bigquery.Client, settings: Settings, df: pd.DataFrame
) -> None:
    df = df.copy()
    df["ingested_at"] = dt.datetime.now(dt.timezone.utc)

    job_config = bigquery.LoadJobConfig(
        schema=UTILIZATION_SCHEMA,
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
    )
    job = client.load_table_from_dataframe(
        df, settings.utilization_table_ref, job_config=job_config
    )
    job.result()


def load_mileage_soc_rows(
    client: bigquery.Client, settings: Settings, df: pd.DataFrame
) -> None:
    """Full-refresh load: this is a small reference table, not an
    append-only stream, so each run replaces it wholesale."""
    df = df.copy()
    df["ingested_at"] = dt.datetime.now(dt.timezone.utc)

    job_config = bigquery.LoadJobConfig(
        schema=MILEAGE_SOC_SCHEMA,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
    )
    job = client.load_table_from_dataframe(
        df, settings.mileage_soc_table_ref, job_config=job_config
    )
    job.result()


def load_utilization_api_rows(
    client: bigquery.Client,
    settings: Settings,
    df: pd.DataFrame,
    start_date: dt.date,
    end_date: dt.date,
    plates: list[str] | None = None,
) -> None:
    """Scoped replace: deletes any existing rows in [start_date, end_date]
    -- only for `plates` when given (a per-vehicle backfill must never touch
    any other vehicle's rows), otherwise for every vehicle --
    (cheap -- the table is MONTH-partitioned on report_date, see
    ensure_utilization_api_table) then appends the freshly-pulled rows.
    Makes both a daily incremental run (yesterday only) and an ad-hoc
    backfill (an arbitrary --from/--to range) safe to re-run -- no date ever
    accumulates duplicates, and nothing outside [start_date, end_date] is
    touched.

    [start_date, end_date] here must be the caller's actual delete/insert
    window, already widened for any known spillover tolerance (see
    api_pipeline.DATE_TOLERANCE) -- deliberately NOT derived from df's own
    report_date values. A trip that never closes comes back from Fleetx on
    every query for that vehicle regardless of the from/to window asked
    for, landing on its real (old) date via aggregate_trips_to_daily()'s
    sDate fallback -- trusting df's date range once let a single still-open
    trip from a month earlier widen this delete across everything in
    between, deleting real data. api_pipeline.run() now filters df to
    [start_date, end_date] before this is ever called, so df's own dates
    should already agree -- this function just doesn't re-derive the delete
    window from data it doesn't fully control.

    (Not atomic across a mid-run crash: if the load fails right after the
    delete, that range reads empty until the next successful run. Acceptable
    for a nightly job -- it self-heals the next run rather than needing a
    multi-statement transaction here.)"""
    df = df.copy()
    df["ingested_at"] = dt.datetime.now(dt.timezone.utc)

    params = [
        bigquery.ScalarQueryParameter("start_date", "DATE", start_date),
        bigquery.ScalarQueryParameter("end_date", "DATE", end_date),
    ]
    plate_filter = ""
    if plates is not None:
        if not plates:
            return
        params.append(bigquery.ArrayQueryParameter("plates", "STRING", sorted(plates)))
        plate_filter = " AND base_license_plate IN UNNEST(@plates)"
        df = df[df["base_license_plate"].isin(plates)]
    client.query(
        f"DELETE FROM `{settings.utilization_api_table_ref}` "
        f"WHERE report_date BETWEEN @start_date AND @end_date{plate_filter}",
        job_config=bigquery.QueryJobConfig(query_parameters=params),
    ).result()

    job_config = bigquery.LoadJobConfig(
        schema=UTILIZATION_SCHEMA,
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
    )
    job = client.load_table_from_dataframe(
        df, settings.utilization_api_table_ref, job_config=job_config
    )
    job.result()


def get_daily_rows(
    client: bigquery.Client,
    settings: Settings,
    table_ref: str,
    start_date: dt.date,
    end_date: dt.date,
) -> pd.DataFrame:
    """Per-(plate, day) utilization rows for validating one source against
    another. QUALIFY defends against duplicate rows for the same plate/day
    -- possible on utilization_daily (WRITE_APPEND, if overlapping Excel
    reports were ever loaded); utilization_daily_api is WRITE_TRUNCATE per
    run so can't have them, but the same query works for both tables."""
    query = f"""
        SELECT base_license_plate, report_date, distance_km, running_time_hours,
               opening_odometer, closing_odometer
        FROM `{table_ref}`
        WHERE report_date BETWEEN @start_date AND @end_date
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY base_license_plate, report_date ORDER BY ingested_at DESC
        ) = 1
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("start_date", "DATE", start_date),
            bigquery.ScalarQueryParameter("end_date", "DATE", end_date),
        ]
    )
    return client.query(query, job_config=job_config).result().to_dataframe()


def get_vehicle_fleetx_ids(
    client: bigquery.Client, settings: Settings
) -> list[tuple[str, int]]:
    """(base_license_plate, fleetx_id) pairs for every active dim_vehicle row
    that has a resolved fleetx_id -- the vehicles the API pipeline can pull.
    A NULL is_active (row from before that column existed) counts as active,
    and so does every row if the column hasn't been added yet (seed-dimensions
    not run since it was introduced)."""
    columns = {f.name for f in client.get_table(settings.dim_vehicle_table_ref).schema}
    active_filter = "AND COALESCE(is_active, TRUE)" if "is_active" in columns else ""
    query = f"""
        SELECT base_license_plate, fleetx_id
        FROM `{settings.dim_vehicle_table_ref}`
        WHERE fleetx_id IS NOT NULL {active_filter}
    """
    rows = client.query(query).result()
    return [(row.base_license_plate, row.fleetx_id) for row in rows]


def get_vehicle_type_model_rows(
    client: bigquery.Client, settings: Settings, plates: list[str]
) -> pd.DataFrame:
    """Raw (base_license_plate, vehicle_type, vehicle_model) rows from
    utilization_daily for the given plates -- one row per day reported, not
    deduped -- so the caller can derive one canonical type/model per vehicle
    the same way backend/metrics.py's clubbed_vehicle_type() does (mode of
    vehicle_type, first non-null vehicle_model)."""
    if not plates:
        return pd.DataFrame(columns=["base_license_plate", "vehicle_type", "vehicle_model"])
    query = f"""
        SELECT base_license_plate, vehicle_type, vehicle_model
        FROM `{settings.utilization_table_ref}`
        WHERE base_license_plate IN UNNEST(@plates)
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ArrayQueryParameter("plates", "STRING", plates)]
    )
    rows = client.query(query, job_config=job_config).result()
    return pd.DataFrame(
        [dict(r) for r in rows], columns=["base_license_plate", "vehicle_type", "vehicle_model"]
    )


def get_odometer_rows_by_plate(client: bigquery.Client, settings: Settings) -> pd.DataFrame:
    """Raw (base_license_plate, report_date, opening_odometer,
    closing_odometer) rows from utilization_daily_api for every vehicle --
    deliberately the API table (fresher, authoritative), not utilization_daily
    -- so seed_dimensions.py can derive each vehicle's first-ever valid
    odometer reading (its pre-existing mileage before this fleet's telemetry
    began), the same source ingestion/odometer_resolver.py reads."""
    query = f"""
        SELECT base_license_plate, report_date, opening_odometer, closing_odometer
        FROM `{settings.utilization_api_table_ref}`
        ORDER BY base_license_plate, report_date
    """
    rows = client.query(query).result()
    return pd.DataFrame(
        [dict(r) for r in rows],
        columns=["base_license_plate", "report_date", "opening_odometer", "closing_odometer"],
    )


def get_utilization_api_min_date(client: bigquery.Client, settings: Settings) -> dt.date | None:
    """Earliest report_date in utilization_daily_api -- the start of the
    dashboard's history, used as a new vehicle's default backfill start."""
    rows = list(
        client.query(f"SELECT MIN(report_date) AS d FROM `{settings.utilization_api_table_ref}`").result()
    )
    return rows[0].d if rows else None


def get_table_rows(client: bigquery.Client, table_ref: str) -> pd.DataFrame:
    """Full current contents of a (small) table, e.g. dim_vehicle -- the
    baseline seed_dimensions.py diffs its planned rows against."""
    rows = client.query(f"SELECT * FROM `{table_ref}`").result()
    return pd.DataFrame([dict(r) for r in rows])


def snapshot_table(
    client: bigquery.Client, table_ref: str, stamp: str, retention_days: int
) -> str:
    """Zero-copy BigQuery table snapshot of `table_ref` (billed only for data
    that later changes), auto-expiring after `retention_days`. Returns the
    snapshot's table ref. Restore with:
        CREATE OR REPLACE TABLE `<table_ref>` CLONE `<snapshot_ref>`"""
    snapshot_ref = f"{table_ref}_backup_{stamp}"
    client.query(
        f"""
        CREATE SNAPSHOT TABLE `{snapshot_ref}`
        CLONE `{table_ref}`
        OPTIONS (
          expiration_timestamp = TIMESTAMP_ADD(CURRENT_TIMESTAMP(), INTERVAL {int(retention_days)} DAY)
        )
        """
    ).result()
    return snapshot_ref


def merge_rows(
    client: bigquery.Client,
    table_ref: str,
    df: pd.DataFrame,
    schema: list[bigquery.SchemaField],
    key: str,
) -> None:
    """Upserts `df` into `table_ref` on `key`: loads it into a temporary
    staging table, then applies a single MERGE (atomic -- either every row
    lands or none do). Deliberately has no WHEN NOT MATCHED BY SOURCE
    clause: a row missing from `df` is left untouched, never deleted, so the
    caller has to mark retirements explicitly (dim_vehicle.is_active)."""
    staging_ref = f"{table_ref}__staging"
    job_config = bigquery.LoadJobConfig(
        schema=schema,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
    )
    client.load_table_from_dataframe(df, staging_ref, job_config=job_config).result()
    try:
        columns = [f.name for f in schema]
        updates = ", ".join(f"T.{c} = S.{c}" for c in columns if c != key)
        client.query(
            f"""
            MERGE `{table_ref}` T
            USING `{staging_ref}` S
            ON T.{key} = S.{key}
            WHEN MATCHED THEN UPDATE SET {updates}
            WHEN NOT MATCHED THEN INSERT ({", ".join(columns)})
              VALUES ({", ".join(f"S.{c}" for c in columns)})
            """
        ).result()
    finally:
        client.delete_table(staging_ref, not_found_ok=True)


def get_manual_overrides(client: bigquery.Client, settings: Settings) -> pd.DataFrame:
    """Rows a human has already corrected (fill_method = 'MANUAL_OVERRIDE'),
    read back before load_odometer_resolved_rows() truncates the table, so
    they can be re-inserted after the fresh computation instead of being
    silently wiped by the next automated run. Empty (not missing) if the
    table doesn't exist yet or has no such rows."""
    query = f"""
        SELECT *
        FROM `{settings.odometer_resolved_table_ref}`
        WHERE fill_method = 'MANUAL_OVERRIDE'
    """
    try:
        rows = client.query(query).result()
    except Exception:
        return pd.DataFrame()
    return pd.DataFrame([dict(row) for row in rows])


def load_odometer_resolved_rows(
    client: bigquery.Client, settings: Settings, df: pd.DataFrame
) -> None:
    """Full-refresh load (WRITE_TRUNCATE): this table is always fully
    recomputed from utilization_daily_api, not appended to -- a reset event
    that closes after this run should retroactively upgrade an earlier
    DISTANCE_FALLBACK day, which only a full recompute can do. `df` must not
    already contain MANUAL_OVERRIDE rows -- the caller (odometer_resolver.run)
    fetches those separately via get_manual_overrides() and appends them
    after this load so they survive the truncate."""
    job_config = bigquery.LoadJobConfig(
        schema=ODOMETER_RESOLVED_SCHEMA,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
    )
    job = client.load_table_from_dataframe(
        df, settings.odometer_resolved_table_ref, job_config=job_config
    )
    job.result()


def append_odometer_resolved_rows(
    client: bigquery.Client, settings: Settings, df: pd.DataFrame
) -> None:
    """WRITE_APPEND companion to load_odometer_resolved_rows(), used only to
    re-insert preserved MANUAL_OVERRIDE rows after a truncate. Separate
    function so the truncate/append pair is explicit at the call site rather
    than a mode flag."""
    if df.empty:
        return
    job_config = bigquery.LoadJobConfig(
        schema=ODOMETER_RESOLVED_SCHEMA,
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
    )
    job = client.load_table_from_dataframe(
        df, settings.odometer_resolved_table_ref, job_config=job_config
    )
    job.result()


def record_ingested_file(
    client: bigquery.Client,
    settings: Settings,
    *,
    file_hash: str,
    file_name: str,
    report_type: str,
    row_count: int,
) -> None:
    log_df = pd.DataFrame(
        [
            {
                "file_hash": file_hash,
                "file_name": file_name,
                "report_type": report_type,
                "row_count": row_count,
                "ingested_at": dt.datetime.now(dt.timezone.utc),
            }
        ]
    )
    job_config = bigquery.LoadJobConfig(
        schema=INGESTION_LOG_SCHEMA,
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
    )
    job = client.load_table_from_dataframe(
        log_df, settings.ingestion_log_table_ref, job_config=job_config
    )
    job.result()
