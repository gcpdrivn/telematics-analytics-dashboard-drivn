"""CLI entry points: `uv run ingest-utilization`, `ingest-mileage-soc`, `seed-dimensions`."""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

from ingestion import (
    api_pipeline,
    api_validation,
    bq_client,
    mileage,
    odometer_resolver,
    pipeline,
    seed_dimensions,
    vehicle_backfill,
)
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


def _yesterday_ist() -> dt.date:
    return (dt.datetime.now(ZoneInfo("Asia/Kolkata")) - dt.timedelta(days=1)).date()


def main_ingest_api() -> None:
    """`uv run ingest-utilization-api` -- pulls Fleetx API data into
    utilization_daily_api, the live dashboard's data source. Never touches
    utilization_daily or the Excel pipeline."""
    parser = argparse.ArgumentParser(
        description="Pull Fleetx History Report trips into utilization_daily_api. "
        "With no --from/--to, defaults to yesterday (IST) -- the daily "
        "scheduled-job case. Pass both for an ad-hoc backfill."
    )
    parser.add_argument(
        "--from", dest="start_date", type=dt.date.fromisoformat, default=None,
        help="Start date, inclusive, YYYY-MM-DD. Defaults to yesterday (IST).",
    )
    parser.add_argument(
        "--to", dest="end_date", type=dt.date.fromisoformat, default=None,
        help="End date, inclusive, YYYY-MM-DD. Defaults to yesterday (IST).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and aggregate trips but do not write to BigQuery.",
    )
    parser.add_argument(
        "--plate", dest="plates", action="append", default=None,
        help="Only this vehicle (repeatable). Only its rows are replaced.",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable debug logging."
    )
    args = parser.parse_args()
    _configure_logging(args.verbose)

    start_date = args.start_date or _yesterday_ist()
    end_date = args.end_date or _yesterday_ist()

    settings = load_settings()
    results = api_pipeline.run(
        settings, start_date=start_date, end_date=end_date, dry_run=args.dry_run, plates=args.plates
    )

    loaded = [r for r in results if r.status == "loaded"]
    no_trips = [r for r in results if r.status == "no_trips"]
    failed = [r for r in results if r.status == "failed"]

    print(
        f"\nDone. {len(loaded)} vehicle(s) loaded "
        f"({sum(r.row_count for r in loaded)} row(s) total), "
        f"{len(no_trips)} with no trips in range, {len(failed)} failed."
    )
    for r in failed:
        print(f"  FAILED: {r.base_license_plate} (fleetx_id={r.fleetx_id}): {r.error}")

    if failed:
        sys.exit(1)


def main_validate_api() -> None:
    """`uv run validate-api-pipeline` -- Phase 2. Compares utilization_daily
    (Excel) against utilization_daily_api (Fleetx API shadow table, filled
    in by `ingest-utilization-api`) for a date range. Read-only."""
    parser = argparse.ArgumentParser(
        description="Compare utilization_daily against utilization_daily_api "
        "for a date range and report coverage/value differences."
    )
    parser.add_argument(
        "--from", dest="start_date", required=True, type=dt.date.fromisoformat,
        help="Start date, inclusive, YYYY-MM-DD.",
    )
    parser.add_argument(
        "--to", dest="end_date", required=True, type=dt.date.fromisoformat,
        help="End date, inclusive, YYYY-MM-DD.",
    )
    parser.add_argument(
        "--out", default="data/validation",
        help="Directory to write the full row-by-row comparison CSV to (default: data/validation).",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable debug logging."
    )
    args = parser.parse_args()
    _configure_logging(args.verbose)

    settings = load_settings()
    client = bq_client.get_client(settings)

    excel_df = bq_client.get_daily_rows(
        client, settings, settings.utilization_table_ref, args.start_date, args.end_date
    )
    api_df = bq_client.get_daily_rows(
        client, settings, settings.utilization_api_table_ref, args.start_date, args.end_date
    )
    comparison = api_validation.compare_daily(excel_df, api_df)
    summary = api_validation.summarize(comparison)

    print(f"\nComparing {args.start_date} to {args.end_date}:")
    print(f"  Excel rows: {summary.excel_rows}  |  API rows: {summary.api_rows}  |  Common: {summary.common_rows}")
    print(f"  Excel-only (API missing these days): {summary.excel_only_rows}")
    print(f"  API-only (Excel missing these days):  {summary.api_only_rows}")
    print(f"  Vehicles compared: {summary.vehicles_compared}")
    print("\n  Column            Mean abs diff   Median % diff (of common rows)")
    for col in api_validation.COMPARE_COLUMNS:
        mean_diff = summary.mean_abs_diff[col]
        pct_diff = summary.median_pct_diff[col]
        print(f"  {col:18} {str(mean_diff):>14}  {str(pct_diff):>14}")

    print("\n  Top 5 largest distance_km mismatches:")
    print(api_validation.top_mismatches(comparison, "distance_km", n=5).to_string(index=False))

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"comparison_{args.start_date}_{args.end_date}.csv"
    comparison.to_csv(out_path, index=False)
    print(f"\nFull row-by-row comparison written to {out_path}")


def main_resolve_odometer() -> None:
    """`uv run resolve-odometer` -- rebuilds odometer_daily_resolved from
    utilization_daily_api: sanitizes known device faults (overflow sentinel,
    garbage magnitudes, physically-impossible single-row jumps) and backfills
    them, recording the method used per row. Always recomputes full history
    (see ingestion/odometer_resolver.run's docstring for why) and preserves
    any existing MANUAL_OVERRIDE rows through the recompute."""
    parser = argparse.ArgumentParser(
        description="Resolve/backfill odometer-based daily distance into odometer_daily_resolved."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute and print the fill_method/confidence breakdown but do not write to BigQuery.",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable debug logging."
    )
    args = parser.parse_args()
    _configure_logging(args.verbose)

    settings = load_settings()
    result = odometer_resolver.run(settings, dry_run=args.dry_run)

    print(f"\n{'[dry-run] ' if args.dry_run else ''}Resolved {result['row_count']} row(s).")
    print("  fill_method            confidence    row_count")
    for row in result["summary"]:
        print(f"  {row['fill_method']:<22} {row['confidence']:<12}  {row['row_count']}")


def main_backfill_vehicles() -> None:
    """`uv run backfill-vehicles PLATE [PLATE ...]`"""
    parser = argparse.ArgumentParser(
        description="Pull the full Fleetx history of specific vehicles into utilization_daily_api "
        "(only their rows are replaced), re-run resolve-odometer, and clear the dashboard cache. "
        "seed-dimensions does this automatically for vehicles it adds; use this to retry."
    )
    parser.add_argument("plates", nargs="+", help="base_license_plate(s), e.g. HR55BE2131")
    parser.add_argument(
        "--from", dest="start_date", type=dt.date.fromisoformat, default=None,
        help="Start date, inclusive. Defaults to the earliest date on the dashboard.",
    )
    parser.add_argument(
        "--to", dest="end_date", type=dt.date.fromisoformat, default=None,
        help="End date, inclusive. Defaults to yesterday (IST).",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging.")
    args = parser.parse_args()
    _configure_logging(args.verbose)

    report = vehicle_backfill.backfill(
        load_settings(), args.plates, start_date=args.start_date, end_date=args.end_date
    )
    print(report.summary())
    if report.failed:
        sys.exit(1)


def main_seed_dimensions() -> None:
    """`uv run seed-dimensions`"""
    parser = argparse.ArgumentParser(
        description="Sync dim_customer / dim_vehicle from the Fleetx vehicle export "
        "(Vehicle_Update_uploader.xlsx). Shows a preview of every change and asks "
        "for confirmation before writing; backs both tables up first."
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Show the preview only; write nothing."
    )
    parser.add_argument(
        "--yes", action="store_true",
        help="Apply without the interactive prompt (for unattended runs). Review a --dry-run first.",
    )
    parser.add_argument(
        "--no-backfill", action="store_true",
        help="Don't pull the history of newly added/reactivated vehicles after applying.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging.")
    args = parser.parse_args()
    _configure_logging(args.verbose)

    settings = load_settings()
    try:
        seed_dimensions.run(
            settings, dry_run=args.dry_run, assume_yes=args.yes, backfill=not args.no_backfill
        )
    except seed_dimensions.SyncAborted as exc:
        print(f"\nAborted: {exc}", file=sys.stderr)
        sys.exit(1)
    print("\nDone.")


if __name__ == "__main__":
    main()
