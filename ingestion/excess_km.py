"""Excess-km billing: loads each customer's contracted monthly km and excess
km rate from 'Excess KM & Battery Replacement.xlsx' into dim_vehicle, and
(re)creates the two views that turn odometer_daily_resolved into monthly
excess km and cost.

Terms are per customer but applied per vehicle: every vehicle of a customer
gets the same monthly_available_km / excess_km_rate, and excess is computed
per vehicle, per calendar month -- one vehicle's unused km never offsets
another's excess. The full allowance applies even to a vehicle's first or
last (partial) month; is_partial_month shows where that happened.

Billable km come only from readings the odometer resolver trusts:
- a day's distance_by_odometer when its fill_method is RAW_VALID,
  INTERPOLATED or MANUAL_OVERRIDE (both ends anchored on real readings);
- boundary_gap_distance -- km driven while the device was silent between two
  RAW_VALID rows -- spread evenly over the silent days. The resolver only
  records a gap averaging under its MAX_KM_PER_DAY (e.g. AP39WN7302's
  26 Jun - 13 Jul jump implies ~1,900 km/day, a counter re-base), so a blank
  gap between two RAW_VALID rows means "over the limit".
DISTANCE_FALLBACK / UNRESOLVED days, untrusted gaps and trailing silent days
are never billed; they lower the month's reliable coverage instead. A month
with low coverage, or whose odometer km disagree with the reported Distance
on the same days, is flagged needs_review -- flagged only, still costed.

Only completed calendar months are reported, so the in-progress month shows
up on its own once it closes.
"""

from __future__ import annotations

import datetime as dt
import logging
import math
import sys
from pathlib import Path
from typing import Callable

import pandas as pd
from google.cloud import bigquery

from ingestion import bq_client
from ingestion.odometer_resolver import MAX_KM_PER_DAY
from ingestion.config import Settings

logger = logging.getLogger(__name__)

BACKUP_RETENTION_DAYS = 30

# Sheet 'Operator' (matched case-insensitively, whitespace-trimmed) ->
# dim_vehicle customer_name. Only customers billed for excess km are listed:
# BillionE and AVG LOGISTICS carry a 0 INR/km rate, and the sheet's other
# operators have no vehicles on the platform yet. Add a row here to bill one.
SHEET_OPERATORS = {
    "FRESHBUS": "FreshBus",
    "ZINGBUS": "ZingBus",
    "SWITCHLABS": "SWITCHLABS",
}

OPERATOR_COLUMN = "Operator"
AVAILABLE_KM_COLUMN = "Monthly Available KM"
RATE_COLUMN = "Excess KM Rate (INR/KM)"

# Silent-gap km averaging this much per day or more is not billed (enforced
# by the resolver, which leaves such a gap blank).
MAX_GAP_KM_PER_DAY = MAX_KM_PER_DAY
# Below this share of in-service days backed by trusted readings (or a
# trusted silent gap), the month is flagged for review.
MIN_RELIABLE_COVERAGE = 0.90
# Odometer km vs reported Distance over the same trusted days: a healthy
# vehicle sits within ~5%; beyond this the odometer itself is suspect
# (e.g. AP39WN7281 in Aug: 9,664 km by odometer vs 19,353 reported).
MAX_ODOMETER_DISTANCE_DIVERGENCE = 0.15

RELIABLE_FILL_METHODS = ("RAW_VALID", "INTERPOLATED", "MANUAL_OVERRIDE")


class TermsFileError(RuntimeError):
    """The terms sheet is missing, malformed, or lacks a billed customer's
    terms. Nothing has been written when this is raised."""


def read_excess_km_terms(path: Path) -> dict[str, dict[str, float]]:
    """customer_name -> {"monthly_available_km", "excess_km_rate"} for every
    customer in SHEET_OPERATORS. Raises TermsFileError rather than guessing
    when a billed customer's row is missing or not numeric (e.g. 'TBD')."""
    if not path.exists():
        raise TermsFileError(f"Excess km terms file '{path}' not found.")
    df = pd.read_excel(path)
    missing = [c for c in (OPERATOR_COLUMN, AVAILABLE_KM_COLUMN, RATE_COLUMN) if c not in df.columns]
    if missing:
        raise TermsFileError(f"'{path.name}' is missing column(s): {', '.join(missing)}.")

    by_operator: dict[str, list[pd.Series]] = {}
    for _, row in df.iterrows():
        if isinstance(row[OPERATOR_COLUMN], str):
            by_operator.setdefault(row[OPERATOR_COLUMN].strip().upper(), []).append(row)

    terms: dict[str, dict[str, float]] = {}
    for operator, customer in SHEET_OPERATORS.items():
        rows = by_operator.get(operator, [])
        if len(rows) != 1:
            raise TermsFileError(
                f"Expected exactly one '{operator}' row in '{path.name}', found {len(rows)}."
            )
        km = pd.to_numeric(rows[0][AVAILABLE_KM_COLUMN], errors="coerce")
        rate = pd.to_numeric(rows[0][RATE_COLUMN], errors="coerce")
        if pd.isna(km) or km <= 0 or pd.isna(rate) or rate < 0:
            raise TermsFileError(
                f"'{operator}' in '{path.name}' has no usable terms "
                f"({AVAILABLE_KM_COLUMN}={rows[0][AVAILABLE_KM_COLUMN]!r}, "
                f"{RATE_COLUMN}={rows[0][RATE_COLUMN]!r})."
            )
        terms[customer] = {"monthly_available_km": float(km), "excess_km_rate": float(rate)}
    return terms


def _value(val) -> float | None:
    if val is None or (isinstance(val, float) and math.isnan(val)) or pd.isna(val):
        return None
    return float(val)


def plan_changes(current: pd.DataFrame, terms: dict[str, dict[str, float]]) -> list[tuple]:
    """(plate, customer, field, old, new) for every dim_vehicle value that
    differs from the sheet. A vehicle whose customer isn't billed is planned
    as NULL, so a customer dropped from SHEET_OPERATORS stops being billed."""
    changes = []
    for row in current.sort_values("base_license_plate").to_dict("records"):
        target = terms.get(row["customer_name"]) or {}
        for field in ("monthly_available_km", "excess_km_rate"):
            old, new = _value(row.get(field)), target.get(field)
            if old is None and new is None:
                continue
            if old is None or new is None or not math.isclose(old, new, abs_tol=1e-6):
                changes.append((row["base_license_plate"], row["customer_name"], field, old, new))
    return changes


def render_changes(terms: dict[str, dict[str, float]], changes: list[tuple], source: Path) -> str:
    fmt = lambda v: "(blank)" if v is None else f"{v:,.2f}"
    out = [
        "=" * 72,
        "dim_vehicle excess km terms -- preview",
        "=" * 72,
        f"Terms file: {source}",
    ]
    for customer, t in sorted(terms.items()):
        out.append(
            f"  {customer:<14} {t['monthly_available_km']:>10,.2f} km/month  "
            f"{t['excess_km_rate']:>6,.2f} INR/km"
        )
    out.append("")
    if not changes:
        out.append("No changes -- dim_vehicle already matches the terms file.")
    else:
        out.append(f"~  {len(changes)} value change(s):")
        for plate, customer, field, old, new in changes:
            out.append(f"   ~ {plate:<12} {customer:<14} {field}: {fmt(old)} -> {fmt(new)}")
    return "\n".join(out)


def _apply_terms(client: bigquery.Client, settings: Settings, terms: dict[str, dict[str, float]]) -> None:
    """One UPDATE statement, so every vehicle's terms change together."""
    params: list[bigquery.ScalarQueryParameter] = []
    km_cases, rate_cases = [], []
    for i, (customer, t) in enumerate(sorted(terms.items())):
        params += [
            bigquery.ScalarQueryParameter(f"c{i}", "STRING", customer),
            bigquery.ScalarQueryParameter(f"k{i}", "FLOAT64", t["monthly_available_km"]),
            bigquery.ScalarQueryParameter(f"r{i}", "FLOAT64", t["excess_km_rate"]),
        ]
        km_cases.append(f"WHEN @c{i} THEN @k{i}")
        rate_cases.append(f"WHEN @c{i} THEN @r{i}")
    sql = f"""
        UPDATE `{settings.dim_vehicle_table_ref}`
        SET monthly_available_km = CASE customer_name {' '.join(km_cases)} ELSE NULL END,
            excess_km_rate = CASE customer_name {' '.join(rate_cases)} ELSE NULL END
        WHERE TRUE
    """
    client.query(sql, job_config=bigquery.QueryJobConfig(query_parameters=params)).result()


def _daily_ctes(settings: Settings) -> str:
    """WITH ... daily: one row per vehicle per in-service day, classed as
    reliable / bridged (trusted silent gap) / unverified / rejected_gap /
    unbridged, with its billable odometer and gap km. Shared by the monthly
    view and the export's per-day audit sheet."""
    reliable = ", ".join(f"'{m}'" for m in RELIABLE_FILL_METHODS)
    return f"""
WITH vehicles AS (
  SELECT base_license_plate, customer_name, monthly_available_km, excess_km_rate, deactivated_at
  FROM `{settings.dim_vehicle_table_ref}`
  WHERE monthly_available_km IS NOT NULL
),
resolved AS (
  SELECT r.*,
    LAG(r.report_date) OVER (PARTITION BY r.base_license_plate ORDER BY r.report_date) AS prev_date,
    LAG(r.fill_method) OVER (PARTITION BY r.base_license_plate ORDER BY r.report_date) AS prev_fill_method
  FROM `{settings.odometer_resolved_table_ref}` r
  JOIN vehicles USING (base_license_plate)
),
-- Each stretch between two consecutive rows. Its gap km is spread evenly
-- over the silent days plus the closing day, so a gap spanning a month
-- boundary is split between both months.
gaps AS (
  SELECT base_license_plate, prev_date, report_date AS closing_date,
    DATE_DIFF(report_date, prev_date, DAY) AS span_days,
    boundary_gap_distance IS NOT NULL AS trusted,
    -- Both ends clean yet no gap recorded: the resolver rejected it as
    -- {MAX_GAP_KM_PER_DAY:,.0f} km/day or more.
    boundary_gap_distance IS NULL AND fill_method = 'RAW_VALID' AND prev_fill_method = 'RAW_VALID' AS over_limit,
    boundary_gap_distance
  FROM resolved
  WHERE prev_date IS NOT NULL
),
-- In service from the vehicle's first reading to the end of the last
-- completed month (or its deactivation).
service_days AS (
  SELECT r.base_license_plate, day
  FROM (SELECT base_license_plate, MIN(report_date) AS first_date FROM resolved GROUP BY 1) r
  JOIN vehicles v USING (base_license_plate),
  UNNEST(GENERATE_DATE_ARRAY(
    r.first_date,
    LEAST(
      DATE_SUB(DATE_TRUNC(CURRENT_DATE('Asia/Kolkata'), MONTH), INTERVAL 1 DAY),
      IFNULL(DATE(v.deactivated_at, 'Asia/Kolkata'), DATE '9999-12-31')
    )
  )) AS day
),
daily AS (
  SELECT d.base_license_plate, d.day,
    CASE
      WHEN r.fill_method IN ({reliable}) THEN 'reliable'
      WHEN r.fill_method IS NOT NULL THEN 'unverified'
      WHEN g.trusted THEN 'bridged'
      WHEN g.over_limit THEN 'rejected_gap'
      ELSE 'unbridged'
    END AS status,
    IF(r.fill_method IN ({reliable}), r.distance_by_odometer, 0) AS odometer_km,
    IF(r.fill_method IN ({reliable}), r.distance_reported, 0) AS reported_km,
    IF(r.fill_method NOT IN ({reliable}), IFNULL(r.distance_by_odometer, r.distance_reported), 0) AS unverified_km,
    IF(g.trusted, g.boundary_gap_distance / g.span_days, 0) AS gap_km,
    r.fill_method, r.opening_odometer_clean, r.closing_odometer_clean,
    r.distance_by_odometer, r.distance_reported
  FROM service_days d
  LEFT JOIN resolved r ON r.base_license_plate = d.base_license_plate AND r.report_date = d.day
  LEFT JOIN gaps g ON g.base_license_plate = d.base_license_plate
    AND d.day > g.prev_date AND d.day <= g.closing_date
)"""


def vehicle_monthly_view_sql(settings: Settings) -> str:
    return _daily_ctes(settings) + f""",
monthly AS (
  SELECT base_license_plate, DATE_TRUNC(day, MONTH) AS month,
    COUNT(*) AS days_in_service,
    COUNTIF(status = 'reliable') AS reliable_days,
    COUNTIF(status = 'bridged') AS bridged_days,
    COUNTIF(status = 'unverified') AS unverified_days,
    COUNTIF(status = 'rejected_gap') AS rejected_gap_days,
    COUNTIF(status = 'unbridged') AS unbridged_days,
    SUM(odometer_km) AS odometer_km,
    SUM(gap_km) AS gap_km,
    SUM(reported_km) AS reported_km,
    SUM(unverified_km) AS unverified_km
  FROM daily
  GROUP BY 1, 2
),
scored AS (
  SELECT v.customer_name, m.*,
    m.odometer_km + m.gap_km AS billable_km,
    SAFE_DIVIDE(m.reliable_days + m.bridged_days, m.days_in_service) AS reliable_coverage,
    SAFE_DIVIDE(m.odometer_km, m.reported_km) AS odometer_to_reported_ratio,
    m.days_in_service < EXTRACT(DAY FROM LAST_DAY(m.month)) AS is_partial_month,
    v.monthly_available_km, v.excess_km_rate
  FROM monthly m JOIN vehicles v USING (base_license_plate)
)
SELECT
  customer_name, base_license_plate, month,
  days_in_service, is_partial_month,
  reliable_days, bridged_days, unverified_days, rejected_gap_days, unbridged_days,
  ROUND(reliable_coverage, 4) AS reliable_coverage,
  ROUND(odometer_km, 1) AS odometer_km,
  ROUND(gap_km, 1) AS gap_km,
  ROUND(billable_km, 1) AS billable_km,
  ROUND(unverified_km, 1) AS unverified_km,
  ROUND(reported_km, 1) AS reported_km,
  ROUND(odometer_to_reported_ratio, 3) AS odometer_to_reported_ratio,
  monthly_available_km,
  ROUND(GREATEST(billable_km - monthly_available_km, 0), 1) AS excess_km,
  excess_km_rate,
  ROUND(GREATEST(billable_km - monthly_available_km, 0) * excess_km_rate, 2) AS excess_cost_inr,
  ARRAY_CONCAT(
    IF(reliable_coverage < {MIN_RELIABLE_COVERAGE}, ['low_reliable_coverage'], []),
    IF(ABS(odometer_to_reported_ratio - 1) > {MAX_ODOMETER_DISTANCE_DIVERGENCE},
       ['odometer_distance_mismatch'], [])
  ) AS review_flags,
  reliable_coverage < {MIN_RELIABLE_COVERAGE}
    OR IFNULL(ABS(odometer_to_reported_ratio - 1) > {MAX_ODOMETER_DISTANCE_DIVERGENCE}, FALSE)
    AS needs_review
FROM scored
"""


def daily_rows_sql(settings: Settings) -> str:
    """Per-day audit rows behind excess_km_monthly for one month (@month, a
    DATE on the 1st)."""
    return _daily_ctes(settings) + """
SELECT v.customer_name, d.base_license_plate, d.day, d.status, d.fill_method,
  d.opening_odometer_clean, d.closing_odometer_clean, d.distance_by_odometer,
  d.distance_reported, d.odometer_km, d.gap_km, d.odometer_km + d.gap_km AS billable_km
FROM daily d JOIN vehicles v USING (base_license_plate)
WHERE DATE_TRUNC(d.day, MONTH) = @month
"""


def customer_monthly_view_sql(settings: Settings) -> str:
    return f"""
SELECT
  customer_name, month,
  COUNT(*) AS vehicles,
  COUNTIF(excess_km > 0) AS vehicles_over_allowance,
  COUNTIF(needs_review) AS vehicles_needing_review,
  ROUND(SUM(billable_km), 1) AS billable_km,
  ROUND(SUM(excess_km), 1) AS excess_km,
  ROUND(SUM(excess_cost_inr), 2) AS excess_cost_inr,
  ROUND(SUM(IF(needs_review, excess_cost_inr, 0)), 2) AS excess_cost_needing_review_inr
FROM `{settings.excess_km_monthly_view_ref}`
GROUP BY customer_name, month
"""


def ensure_views(client: bigquery.Client, settings: Settings) -> None:
    for ref, sql, description in (
        (
            settings.excess_km_monthly_view_ref,
            vehicle_monthly_view_sql(settings),
            "Excess km and cost per vehicle per completed month, from trusted odometer "
            "readings only. See ingestion/excess_km.py.",
        ),
        (
            settings.excess_km_monthly_customer_view_ref,
            customer_monthly_view_sql(settings),
            "excess_km_monthly rolled up per customer per month.",
        ),
    ):
        client.query(
            f'CREATE OR REPLACE VIEW `{ref}` OPTIONS (description = "{description}") AS {sql}'
        ).result()


class SyncAborted(RuntimeError):
    """Not confirmed, or refused -- nothing has been written."""


def run(
    settings: Settings,
    *,
    dry_run: bool = False,
    assume_yes: bool = False,
    interactive: bool | None = None,
    prompt: Callable[[str], str] = input,
    client=None,
) -> str:
    """Returns 'no-changes', 'dry-run' or 'applied'. Views are (re)created on
    every non-dry run, since they hold no data of their own."""
    if interactive is None:
        interactive = sys.stdin.isatty()
    try:
        terms = read_excess_km_terms(settings.excess_km_terms_file)
    except TermsFileError as exc:
        raise SyncAborted(str(exc)) from exc
    client = client or bq_client.get_client(settings)
    if not dry_run:
        bq_client.ensure_dim_vehicle_table(client, settings)  # adds the two columns if missing

    current = bq_client.get_table_rows(client, settings.dim_vehicle_table_ref)
    changes = plan_changes(current, terms)
    print(render_changes(terms, changes, settings.excess_km_terms_file))

    if dry_run:
        print("\nDry run -- nothing written.")
        return "dry-run"

    status = "no-changes"
    if changes:
        if not assume_yes:
            if not interactive:
                raise SyncAborted("Changes need confirmation -- re-run with --yes after reviewing the preview.")
            if prompt("Apply these changes? Type 'yes' to continue: ").strip().lower() != "yes":
                raise SyncAborted("Not confirmed -- nothing was written.")
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        snapshot = bq_client.snapshot_table(client, settings.dim_vehicle_table_ref, stamp, BACKUP_RETENTION_DAYS)
        _apply_terms(client, settings, terms)
        print(f"\nApplied {len(changes)} change(s). Backup kept {BACKUP_RETENTION_DAYS} days; to undo:")
        print(f"  CREATE OR REPLACE TABLE `{settings.dim_vehicle_table_ref}` CLONE `{snapshot}`;")
        status = "applied"

    ensure_views(client, settings)
    print(f"Views ready: {settings.excess_km_monthly_view_ref}, {settings.excess_km_monthly_customer_view_ref}")
    return status
