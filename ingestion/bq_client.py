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
    """Shadow table for the Fleetx-API pipeline -- same UTILIZATION_SCHEMA as
    utilization_daily, so the two can be compared/diffed column-for-column
    during validation."""
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


def ensure_dim_vehicle_table(client: bigquery.Client, settings: Settings) -> None:
    table = bigquery.Table(settings.dim_vehicle_table_ref, schema=DIM_VEHICLE_SCHEMA)
    client.create_table(table, exists_ok=True)


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
    client: bigquery.Client, settings: Settings, df: pd.DataFrame
) -> None:
    """Full-refresh load: the shadow table is regenerated wholesale on every
    run for a given date range, not appended to -- it exists purely for
    validation against utilization_daily, not as a production stream."""
    df = df.copy()
    df["ingested_at"] = dt.datetime.now(dt.timezone.utc)

    job_config = bigquery.LoadJobConfig(
        schema=UTILIZATION_SCHEMA,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
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
    """(base_license_plate, fleetx_id) pairs for every dim_vehicle row that
    has a resolved fleetx_id -- the vehicles the API pipeline can pull."""
    query = f"""
        SELECT base_license_plate, fleetx_id
        FROM `{settings.dim_vehicle_table_ref}`
        WHERE fleetx_id IS NOT NULL
    """
    rows = client.query(query).result()
    return [(row.base_license_plate, row.fleetx_id) for row in rows]


def get_distinct_plates_by_prefix(
    client: bigquery.Client, settings: Settings, prefix: str
) -> list[str]:
    """Distinct base_license_plate values from utilization_daily matching a
    prefix, e.g. reproducing the script's `startswith('MH02')` BillionE rule
    as a live lookup instead of a hardcoded list."""
    query = f"""
        SELECT DISTINCT base_license_plate
        FROM `{settings.utilization_table_ref}`
        WHERE base_license_plate LIKE @prefix_pattern
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("prefix_pattern", "STRING", f"{prefix}%")
        ]
    )
    rows = client.query(query, job_config=job_config).result()
    return sorted(row.base_license_plate for row in rows)


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


def load_dim_customer_rows(
    client: bigquery.Client, settings: Settings, df: pd.DataFrame
) -> None:
    job_config = bigquery.LoadJobConfig(
        schema=DIM_CUSTOMER_SCHEMA,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
    )
    job = client.load_table_from_dataframe(
        df, settings.dim_customer_table_ref, job_config=job_config
    )
    job.result()


def load_dim_vehicle_rows(
    client: bigquery.Client, settings: Settings, df: pd.DataFrame
) -> None:
    job_config = bigquery.LoadJobConfig(
        schema=DIM_VEHICLE_SCHEMA,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
    )
    job = client.load_table_from_dataframe(
        df, settings.dim_vehicle_table_ref, job_config=job_config
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
