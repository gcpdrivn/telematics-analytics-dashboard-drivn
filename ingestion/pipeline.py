"""Orchestrates the utilization-report ingestion run."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path

from ingestion import bq_client, transform
from ingestion.config import Settings

logger = logging.getLogger(__name__)

REPORT_TYPE = "utilization"


@dataclass
class FileResult:
    path: Path
    status: str  # "loaded" | "skipped_already_ingested" | "failed"
    row_count: int = 0
    error: str | None = None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _discover_files(raw_dir: Path) -> list[Path]:
    if not raw_dir.exists():
        raise FileNotFoundError(f"Raw utilization directory not found: {raw_dir}")
    return sorted(
        p for p in raw_dir.glob("*.xls*") if not p.name.startswith("~$")
    )


def run(settings: Settings, *, dry_run: bool = False) -> list[FileResult]:
    client = bq_client.get_client(settings)
    bq_client.ensure_schema(client, settings)

    files = _discover_files(settings.raw_utilization_dir)
    logger.info("Found %d Excel file(s) in %s", len(files), settings.raw_utilization_dir)

    already_ingested = bq_client.get_ingested_hashes(client, settings, REPORT_TYPE)
    logger.info("%d file(s) already ingested for report_type=%s", len(already_ingested), REPORT_TYPE)

    results: list[FileResult] = []
    for path in files:
        file_hash = _sha256(path)
        if file_hash in already_ingested:
            logger.info("Skipping '%s' (already ingested, hash=%s)", path.name, file_hash[:12])
            results.append(FileResult(path=path, status="skipped_already_ingested"))
            continue

        try:
            raw_df = transform.read_utilization_file(path)
            clean_df = transform.clean_utilization(
                raw_df, source_file_name=path.name, source_file_hash=file_hash
            )
        except Exception as exc:  # noqa: BLE001 - surface per-file, keep processing others
            logger.exception("Failed to process '%s'", path.name)
            results.append(FileResult(path=path, status="failed", error=str(exc)))
            continue

        if dry_run:
            logger.info(
                "[dry-run] Would load %d row(s) from '%s'", len(clean_df), path.name
            )
            results.append(
                FileResult(path=path, status="dry_run_would_load", row_count=len(clean_df))
            )
            continue

        bq_client.load_utilization_rows(client, settings, clean_df)
        bq_client.record_ingested_file(
            client,
            settings,
            file_hash=file_hash,
            file_name=path.name,
            report_type=REPORT_TYPE,
            row_count=len(clean_df),
        )
        logger.info("Loaded %d row(s) from '%s'", len(clean_df), path.name)
        results.append(FileResult(path=path, status="loaded", row_count=len(clean_df)))

    return results
