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
    bq_ingestion_log_table: str
    bq_mileage_soc_table: str
    bq_dim_customer_table: str
    bq_dim_vehicle_table: str
    raw_utilization_dir: Path
    mileage_soc_file: Path

    @property
    def dataset_ref(self) -> str:
        return f"{self.gcp_project_id}.{self.bq_dataset}"

    @property
    def utilization_table_ref(self) -> str:
        return f"{self.dataset_ref}.{self.bq_utilization_table}"

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
        bq_ingestion_log_table=os.environ.get(
            "BQ_INGESTION_LOG_TABLE", "ingestion_log"
        ),
        bq_mileage_soc_table=os.environ.get(
            "BQ_MILEAGE_SOC_TABLE", "vehicle_mileage_soc"
        ),
        bq_dim_customer_table=os.environ.get("BQ_DIM_CUSTOMER_TABLE", "dim_customer"),
        bq_dim_vehicle_table=os.environ.get("BQ_DIM_VEHICLE_TABLE", "dim_vehicle"),
        raw_utilization_dir=_resolve_path(
            "RAW_UTILIZATION_DIR", "data/raw/utilization"
        ),
        mileage_soc_file=_resolve_path(
            "MILEAGE_SOC_FILE", "data/raw/vehicle_mileage_soc.xlsx"
        ),
    )
