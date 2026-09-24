"""Environment-driven configuration for the ingestion pipeline."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent

load_dotenv(REPO_ROOT / ".env")


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"Missing required environment variable '{name}'. "
            f"Copy .env.example to .env and fill it in."
        )
    return value


@dataclass(frozen=True)
class Settings:
    gcp_project_id: str
    bq_dataset: str
    bq_location: str
    bq_utilization_table: str
    bq_utilization_api_table: str
    bq_ingestion_log_table: str
    bq_mileage_soc_table: str
    bq_dim_customer_table: str
    bq_dim_vehicle_table: str
    bq_odometer_resolved_table: str
    raw_utilization_dir: Path
    mileage_soc_file: Path
    dim_vehicle_master_file: Path
    fleetx_vehicle_map_file: Path
    # Deployed dashboard backend (e.g. the Cloud Run URL), so a vehicle
    # backfill can clear its in-process cache and show the new data
    # immediately. Optional -- unset means "wait for the cache TTL".
    backend_url: str | None = None

    @property
    def dataset_ref(self) -> str:
        return f"{self.gcp_project_id}.{self.bq_dataset}"

    @property
    def utilization_table_ref(self) -> str:
        return f"{self.dataset_ref}.{self.bq_utilization_table}"

    @property
    def utilization_api_table_ref(self) -> str:
        """Shadow table for the Fleetx-API-sourced pipeline (Phase 1/2 of the
        Excel->API migration) -- same schema as utilization_daily, never
        read by backend/, so the Excel pipeline and existing dashboard are
        completely unaffected while the two sources are validated against
        each other."""
        return f"{self.dataset_ref}.{self.bq_utilization_api_table}"

    @property
    def ingestion_log_table_ref(self) -> str:
        return f"{self.dataset_ref}.{self.bq_ingestion_log_table}"

    @property
    def mileage_soc_table_ref(self) -> str:
        return f"{self.dataset_ref}.{self.bq_mileage_soc_table}"

    @property
    def dim_customer_table_ref(self) -> str:
        return f"{self.dataset_ref}.{self.bq_dim_customer_table}"

    @property
    def dim_vehicle_table_ref(self) -> str:
        return f"{self.dataset_ref}.{self.bq_dim_vehicle_table}"

    @property
    def odometer_resolved_table_ref(self) -> str:
        """Derived/backfilled odometer-based daily distance -- see
        ingestion/odometer_resolver.py. Never a source of truth for raw
        telemetry; fully recomputable from utilization_daily_api."""
        return f"{self.dataset_ref}.{self.bq_odometer_resolved_table}"


def _resolve_path(env_name: str, default: str) -> Path:
    raw = os.environ.get(env_name, default)
    path = Path(raw)
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path


def load_settings() -> Settings:
    return Settings(
        gcp_project_id=_required("GCP_PROJECT_ID"),
        bq_dataset=os.environ.get("BQ_DATASET", "telematics"),
        bq_location=os.environ.get("BQ_LOCATION", "asia-south1"),
        bq_utilization_table=os.environ.get(
            "BQ_UTILIZATION_TABLE", "utilization_daily"
        ),
        bq_utilization_api_table=os.environ.get(
            "BQ_UTILIZATION_API_TABLE", "utilization_daily_api"
        ),
        bq_ingestion_log_table=os.environ.get(
            "BQ_INGESTION_LOG_TABLE", "ingestion_log"
        ),
        bq_mileage_soc_table=os.environ.get(
            "BQ_MILEAGE_SOC_TABLE", "vehicle_mileage_soc"
        ),
        bq_dim_customer_table=os.environ.get("BQ_DIM_CUSTOMER_TABLE", "dim_customer"),
        bq_dim_vehicle_table=os.environ.get("BQ_DIM_VEHICLE_TABLE", "dim_vehicle"),
        bq_odometer_resolved_table=os.environ.get(
            "BQ_ODOMETER_RESOLVED_TABLE", "odometer_daily_resolved"
        ),
        raw_utilization_dir=_resolve_path(
            "RAW_UTILIZATION_DIR", "data/raw/utilization"
        ),
        mileage_soc_file=_resolve_path(
            "MILEAGE_SOC_FILE", "data/raw/vehicle_mileage_soc.xlsx"
        ),
        dim_vehicle_master_file=_resolve_path(
            "DIM_VEHICLE_MASTER_FILE", "data/raw/dim_vehicle_master.xlsx"
        ),
        fleetx_vehicle_map_file=_resolve_path(
            "FLEETX_VEHICLE_MAP_FILE", "data/raw/Vehicle_Update_uploader.xlsx"
        ),
        backend_url=(os.environ.get("BACKEND_URL") or "").strip().rstrip("/") or None,
    )
