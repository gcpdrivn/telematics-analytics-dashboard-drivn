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
