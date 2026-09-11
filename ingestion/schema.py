"""BigQuery table schemas for the ingestion pipeline."""

from __future__ import annotations

from google.cloud import bigquery

# One row per (base_license_plate, report_date), mirroring the cleaned
# utilization dataframe produced by transform.clean_utilization().
UTILIZATION_SCHEMA = [
    bigquery.SchemaField("license_plate_raw", "STRING", mode="NULLABLE",
                          description="Raw device identifier as reported by telematics, e.g. 'AP39WN7281_CAM'."),
    bigquery.SchemaField("base_license_plate", "STRING", mode="REQUIRED",
                          description="Cleaned registration plate with device/hardware suffixes stripped. Join key."),
    bigquery.SchemaField("distance_km", "FLOAT64", mode="NULLABLE"),
    bigquery.SchemaField("vehicle_name", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("running_time_hours", "FLOAT64", mode="NULLABLE"),
    bigquery.SchemaField("group_name", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("gps_disconnection_count", "INT64", mode="NULLABLE"),
    bigquery.SchemaField("vehicle_type", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("vehicle_maker", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("vehicle_model", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("avg_daily_operating_hours", "FLOAT64", mode="NULLABLE"),
    bigquery.SchemaField("stoppages_count", "INT64", mode="NULLABLE"),
    bigquery.SchemaField("max_speed", "FLOAT64", mode="NULLABLE"),
    bigquery.SchemaField("start_location", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("end_location", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("average_speed", "FLOAT64", mode="NULLABLE"),
    bigquery.SchemaField("start_date", "TIMESTAMP", mode="NULLABLE"),
    bigquery.SchemaField("end_date", "TIMESTAMP", mode="NULLABLE"),
    bigquery.SchemaField("closing_odometer", "FLOAT64", mode="NULLABLE"),
    bigquery.SchemaField("opening_odometer", "FLOAT64", mode="NULLABLE"),
    bigquery.SchemaField("report_date", "DATE", mode="REQUIRED",
                          description="Calendar date of telemetry aggregation window. Partitioning key."),
    bigquery.SchemaField("source_file_name", "STRING", mode="REQUIRED",
                          description="Basename of the originating Excel report, for lineage."),
    bigquery.SchemaField("source_file_hash", "STRING", mode="REQUIRED",
                          description="SHA-256 of the raw file bytes. Used for ingestion dedup."),
    bigquery.SchemaField("ingested_at", "TIMESTAMP", mode="REQUIRED"),
]

# Control table recording which source files have already been loaded, so
# re-running the pipeline over the same folder never re-inserts a file's rows.
INGESTION_LOG_SCHEMA = [
    bigquery.SchemaField("file_hash", "STRING", mode="REQUIRED",
                          description="SHA-256 of the raw file bytes. Primary dedup key."),
    bigquery.SchemaField("file_name", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("report_type", "STRING", mode="REQUIRED",
                          description="e.g. 'utilization', 'alarm'."),
    bigquery.SchemaField("row_count", "INT64", mode="REQUIRED"),
    bigquery.SchemaField("ingested_at", "TIMESTAMP", mode="REQUIRED"),
]

# One row per vehicle. Small reference table, replaced wholesale
# (WRITE_TRUNCATE) on every ingest run rather than appended/deduped -- there's
# no meaningful history to keep for a benchmark table like this.
MILEAGE_SOC_SCHEMA = [
    bigquery.SchemaField("vehicle_short_no", "INT64", mode="NULLABLE"),
    bigquery.SchemaField("license_plate", "STRING", mode="REQUIRED",
                          description="Join key to dim_vehicle.base_license_plate."),
    bigquery.SchemaField("mileage_km_per_soc", "FLOAT64", mode="NULLABLE",
                          description="Km delivered per 1% SoC consumption. Readings >= 10.0 are NULLed as sensor error, matching generate_presentation_report.py Step 5."),
    bigquery.SchemaField("status", "STRING", mode="NULLABLE",
                          description="Human audit trail from the source workbook. Lineage only, never used for filtering."),
    bigquery.SchemaField("raw_note", "STRING", mode="NULLABLE",
                          description="Human audit trail from the source workbook. Lineage only, never used for filtering."),
    bigquery.SchemaField("ingested_at", "TIMESTAMP", mode="REQUIRED"),
]

# Static reference data, seeded once from the same customer/OEM/route facts
# hardcoded in generate_presentation_report.py. WRITE_TRUNCATE, safe to re-run.
DIM_CUSTOMER_SCHEMA = [
    bigquery.SchemaField("customer_name", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("oem", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("routes_description", "STRING", mode="NULLABLE"),
]

# One row per plate -> customer. FreshBus/ZingBus rows are the enumerated
# plate lists from the script; BillionE rows are discovered from
# utilization_daily (base_license_plate LIKE 'MH02%') rather than hardcoded,
# reproducing the script's startswith('MH02') rule as real rows.
DIM_VEHICLE_SCHEMA = [
    bigquery.SchemaField("base_license_plate", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("customer_name", "STRING", mode="REQUIRED"),
]
