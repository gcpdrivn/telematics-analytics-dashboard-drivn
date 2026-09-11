"""CLI entry points: `uv run ingest-utilization`, `ingest-mileage-soc`, `seed-dimensions`."""

from __future__ import annotations

import argparse
import logging
import sys

from ingestion import mileage, pipeline, seed_dimensions
from ingestion.config import load_settings


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def main() -> None:
    """`uv run ingest-utilization`"""
    parser = argparse.ArgumentParser(description="Ingest utilization Excel reports into BigQuery.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and clean files but do not write to BigQuery or the ingestion log.",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable debug logging."
    )
    args = parser.parse_args()
    _configure_logging(args.verbose)

    settings = load_settings()
    results = pipeline.run(settings, dry_run=args.dry_run)

    loaded = [r for r in results if r.status in ("loaded", "dry_run_would_load")]
    skipped = [r for r in results if r.status == "skipped_already_ingested"]
    failed = [r for r in results if r.status == "failed"]

    print(
        f"\nDone. {len(loaded)} file(s) loaded, {len(skipped)} skipped "
        f"(already ingested), {len(failed)} failed."
    )
    for r in failed:
        print(f"  FAILED: {r.path.name}: {r.error}")

    if failed:
        sys.exit(1)


def main_mileage() -> None:
    """`uv run ingest-mileage-soc`"""
    parser = argparse.ArgumentParser(
        description="Load vehicle_mileage_soc.xlsx into BigQuery (full refresh)."
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging.")
    args = parser.parse_args()
    _configure_logging(args.verbose)

    settings = load_settings()
    try:
        row_count = mileage.run(settings)
    except FileNotFoundError as exc:
        print(f"FAILED: {exc}")
        sys.exit(1)

    print(f"\nDone. Loaded {row_count} row(s) into {settings.mileage_soc_table_ref}.")


def main_seed_dimensions() -> None:
    """`uv run seed-dimensions`"""
    parser = argparse.ArgumentParser(
        description="Seed dim_customer / dim_vehicle from the known customer mapping. "
        "Run after utilization data has been ingested (BillionE plates are "
        "discovered from utilization_daily, not hardcoded)."
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging.")
    args = parser.parse_args()
    _configure_logging(args.verbose)

    settings = load_settings()
    seed_dimensions.run(settings)
    print("\nDone.")


if __name__ == "__main__":
    main()
