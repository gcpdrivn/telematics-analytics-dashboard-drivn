"""Derives odometer-based daily distance from utilization_daily_api and
backfills faulty readings, writing the result to odometer_daily_resolved.

Every value that isn't a straight pass-through of a clean raw reading records
which method produced it (fill_method) and how much to trust it (confidence)
-- see the module docstring in ingestion/schema.py's ODOMETER_RESOLVED_SCHEMA
for the column contract, and /home/yogesh/.claude/plans/rustling-munching-spindle.md
for the analysis that justified the specific thresholds below.

Detection is deliberately row-internal: each day is judged on its own
Opening/Closing Odometer only. The reported Distance field is not reliable
enough to judge the odometer by, so no flag uses it (it is still the
last-resort fallback value for a day no odometer anchor can repair). A
reading is only discarded outright when it's the known overflow sentinel,
negative, or past GARBAGE_ABS_THRESHOLD_KM (1.5 million km). Implausibility is
a single rule applied everywhere: MAX_KM_PER_DAY, per day of the row itself,
per calendar day of a silent gap between rows, and per calendar day of an
anchor-to-anchor bracket.

Known accepted limitation: a "quiet" day sitting inside a multi-day reset,
whose own Opening/Closing happen to imply a small, internally-plausible delta
(e.g. AP39WN7276's 2026-07-12/07-13, coasting from a reset-to-near-zero
counter at ~2-14 km/day before the counter jumps back on 07-14), passes every
row-internal check and is treated as RAW_VALID even though its true driven
distance that day was much higher. The boundary days of the same reset (the
huge negative/positive jumps) are still caught, and the excess-km billing
flags such a month through its odometer-vs-Distance review check.
"""

from __future__ import annotations

import datetime as dt
import logging

import numpy as np
import pandas as pd
from google.cloud import bigquery

from backend import metrics
from backend.data_loader import _UTILIZATION_RENAMES
from ingestion import bq_client
from ingestion.config import Settings

logger = logging.getLogger(__name__)

RESOLVER_VERSION = "2.0.0"

# The device firmware's known counter-overflow bug repeats this exact value
# (2**21 - 1, a 21-bit rollover) regardless of the vehicle's real odometer --
# it carries no information, so it's nulled by exact match rather than
# folded into the general garbage-magnitude check below.
OVERFLOW_SENTINEL_KM = 2097151.88
_SENTINEL_TOLERANCE_KM = 1.0

# A reading past this is a device fault, not an odometer (e.g. the
# ~100,000,514 km seen on MH02GS4224); discarded like the sentinel above.
# Real readings in this fleet are far below it, but can pass 5 lakh km.
GARBAGE_ABS_THRESHOLD_KM = 1_500_000.0

# No vehicle in this fleet can drive this far in a day: an odometer change of
# this much or more per day is a device fault (reset, re-base, jump), whether
# it's one row's own Closing - Opening, a silent gap between two rows, or the
# span between two anchors a bracket of bad days is interpolated across.
MAX_KM_PER_DAY = 1500.0


def _implausible(km: float, days: float) -> bool:
    return abs(km) / max(days, 1.0) >= MAX_KM_PER_DAY


def _is_sentinel(x: float) -> bool:
    return pd.notna(x) and abs(x - OVERFLOW_SENTINEL_KM) < _SENTINEL_TOLERANCE_KM


def _sanitize(df: pd.DataFrame) -> pd.DataFrame:
    """Nulls Opening/Closing Odometer independently (either can be bad
    without the other being bad) and records which per-row anomaly flags
    apply. Returns df with opening_sanitized/closing_sanitized/delta/
    anomaly_flags/is_raw_valid columns added."""
    df = df.copy()
    df["opening_odometer_raw"] = df["Opening Odometer"]
    df["closing_odometer_raw"] = df["Closing Odometer"]

    opening_is_sentinel = df["Opening Odometer"].apply(_is_sentinel)
    closing_is_sentinel = df["Closing Odometer"].apply(_is_sentinel)
    opening_bad = (
        opening_is_sentinel
        | (df["Opening Odometer"] > GARBAGE_ABS_THRESHOLD_KM)
        | (df["Opening Odometer"] < 0)
    )
    closing_bad = (
        closing_is_sentinel
        | (df["Closing Odometer"] > GARBAGE_ABS_THRESHOLD_KM)
        | (df["Closing Odometer"] < 0)
    )
    df["opening_sanitized"] = df["Opening Odometer"].where(~opening_bad)
    df["closing_sanitized"] = df["Closing Odometer"].where(~closing_bad)
    df["_had_sentinel"] = opening_is_sentinel | closing_is_sentinel

    df["delta"] = df["closing_sanitized"] - df["opening_sanitized"]

    flags: list[list[str]] = []
    for _, row in df.iterrows():
        row_flags: list[str] = []
        if row["_had_sentinel"]:
            row_flags.append("overflow_sentinel")
        if pd.isna(row["opening_sanitized"]) or pd.isna(row["closing_sanitized"]):
            row_flags.append("missing_reading")
        else:
            if row["delta"] < 0:
                row_flags.append("non_monotonic")
            if _implausible(row["delta"], 1):
                row_flags.append("physically_implausible")
        flags.append(row_flags)
    df["anomaly_flags"] = flags
    df["is_raw_valid"] = df["anomaly_flags"].apply(len) == 0
    return df


def _compute_boundary_gaps(df: pd.DataFrame) -> pd.Series:
    """Distance implied between a row's own Opening Odometer and the
    immediately preceding row's Closing Odometer, for the same vehicle --
    covers both the small ~10-25 km residual between plain adjacent days (a
    reporting-window artifact) and a genuine multi-day silent gap (the
    device just didn't report for a few days), since both have the same
    shape: two adjacent ROWS in the data, however far apart their dates
    happen to be. A row between them that was itself flagged bad breaks
    that adjacency, so this never bridges past a row the waterfall above
    already had to backfill -- only across a real gap or a plain boundary.

    NaN unless both rows are RAW_VALID and the gap averages under
    MAX_KM_PER_DAY over the calendar days between the two rows' dates -- this also means a
    'quiet day inside a reset' that slipped through as RAW_VALID (this
    module's documented accepted limitation) still has its neighboring gap
    correctly suppressed here, even though the day itself wasn't caught.

    df must already be sorted by ["Base License Plate", "Report Date"]
    (resolve() guarantees this) and have gone through _sanitize()."""
    gaps = pd.Series(np.nan, index=df.index)
    for _, idx in df.groupby("Base License Plate").groups.items():
        idx = list(idx)
        for pos in range(1, len(idx)):
            i, prev = idx[pos], idx[pos - 1]
            if not (df.at[i, "is_raw_valid"] and df.at[prev, "is_raw_valid"]):
                continue
            gap = df.at[i, "opening_sanitized"] - df.at[prev, "closing_sanitized"]
            days = (df.at[i, "Report Date"] - df.at[prev, "Report Date"]).days
            if not _implausible(gap, days):
                gaps.at[i] = gap
    return gaps


def _apply_distance_only(df: pd.DataFrame, i: int, note_prefix: str) -> None:
    dist = df.at[i, "Distance"]
    if pd.notna(dist):
        df.at[i, "distance_by_odometer"] = float(dist)
        df.at[i, "fill_method"] = "DISTANCE_FALLBACK"
        df.at[i, "confidence"] = "Low"
        df.at[i, "notes"] = f"{note_prefix} used reported Distance directly."
    else:
        df.at[i, "fill_method"] = "UNRESOLVED"
        df.at[i, "confidence"] = "Unresolved"
        df.at[i, "notes"] = f"{note_prefix} reported Distance is also missing."


def _fill_no_before_anchor(df: pd.DataFrame, bad_idx: list[int], anchor_idx: int) -> None:
    """Rows before a vehicle's first RAW_VALID row -- no earlier anchor
    exists at all, so walk backward from the anchor's own opening value,
    subtracting each day's Distance as we go back in time."""
    running = df.at[anchor_idx, "opening_odometer_clean"]
    for i in reversed(bad_idx):
        dist = df.at[i, "Distance"]
        d = float(dist) if pd.notna(dist) else 0.0
        df.at[i, "closing_odometer_clean"] = running
        df.at[i, "opening_odometer_clean"] = running - d
        df.at[i, "distance_by_odometer"] = d
        df.at[i, "fill_method"] = "DISTANCE_FALLBACK"
        df.at[i, "confidence"] = "Low"
        note = "No odometer anchor exists before this day yet; back-computed from Distance."
        if pd.isna(dist):
            note += " Distance also missing this day; treated as 0 km."
        df.at[i, "notes"] = note
        running -= d


def _fill_no_after_anchor(df: pd.DataFrame, bad_idx: list[int], anchor_idx: int) -> None:
    """Rows after a vehicle's last RAW_VALID row -- no later anchor exists
    yet (may resolve retroactively once one does, on a future rerun), so
    walk forward from the anchor's own closing value."""
    running = df.at[anchor_idx, "closing_odometer_clean"]
    for i in bad_idx:
        dist = df.at[i, "Distance"]
        d = float(dist) if pd.notna(dist) else 0.0
        df.at[i, "opening_odometer_clean"] = running
        df.at[i, "closing_odometer_clean"] = running + d
        df.at[i, "distance_by_odometer"] = d
        df.at[i, "fill_method"] = "DISTANCE_FALLBACK"
        df.at[i, "confidence"] = "Low"
        note = "No odometer anchor exists after this day yet; forward-computed from Distance."
        if pd.isna(dist):
            note += " Distance also missing this day; treated as 0 km."
        df.at[i, "notes"] = note
        running += d


def _interpolate_between(df: pd.DataFrame, anchor_before: int, anchor_after: int, bad_idx: list[int]) -> None:
    """Bad rows bracketed by a RAW_VALID anchor on each side. A single bad
    row absorbs the whole anchor-to-anchor delta unambiguously (High
    confidence, nothing to split); more than one splits it by each day's own
    Distance as a weight (Medium confidence -- the split itself is a
    judgment call, even though the total is solid).

    Before trusting either of those, the anchor PAIR's own change gets the
    same MAX_KM_PER_DAY check applied to individual rows, per calendar day
    strictly between the two anchors. Two rows can each independently pass their own
    within-row check yet still not really bracket the same continuous
    counter -- e.g. a reset that coasts from near-zero for a few days before
    a single huge catch-up jump (AP39WN7276's real pattern): every row in
    between looks locally plausible, so the badness only shows up as an
    impossible AVERAGE between the two 'good' anchors. That's not evidence
    the total km is real and just needs splitting -- it's evidence the two
    anchors are on opposite sides of a counter re-basing (e.g. a device
    swap), and 'end_val' is not really 92,000-odd km ahead of 'start_val' at
    all. Falls back to Distance alone per bad day in that case, same as if
    no anchor existed."""
    start_val = df.at[anchor_before, "closing_odometer_clean"]
    end_val = df.at[anchor_after, "opening_odometer_clean"]
    total_delta = end_val - start_val
    n_bad = len(bad_idx)

    bracket_days = (df.at[anchor_after, "Report Date"] - df.at[anchor_before, "Report Date"]).days - 1
    if _implausible(total_delta, bracket_days):
        for i in bad_idx:
            _apply_distance_only(
                df, i,
                f"Anchors exist on both sides, but they differ by {MAX_KM_PER_DAY:,.0f} "
                "km/day or more across the days between them (likely a counter "
                "re-basing/device swap partway through) -- not used;",
            )
        return

    if n_bad == 1:
        i = bad_idx[0]
        df.at[i, "opening_odometer_clean"] = start_val
        df.at[i, "closing_odometer_clean"] = end_val
        df.at[i, "distance_by_odometer"] = total_delta
        df.at[i, "fill_method"] = "INTERPOLATED"
        df.at[i, "confidence"] = "High"
        df.at[i, "notes"] = (
            f"Sole bad day between two valid anchors; assigned the full "
            f"anchor-to-anchor delta ({total_delta:.2f} km)."
        )
        return

    dists = [df.at[i, "Distance"] for i in bad_idx]
    weights = [float(d) if pd.notna(d) and d > 0 else 0.0 for d in dists]
    total_weight = sum(weights)
    if total_weight <= 0:
        weights = [1.0] * n_bad
        total_weight = float(n_bad)

    running = start_val
    for i, w in zip(bad_idx, weights):
        share = total_delta * (w / total_weight)
        df.at[i, "opening_odometer_clean"] = running
        df.at[i, "closing_odometer_clean"] = running + share
        df.at[i, "distance_by_odometer"] = share
        df.at[i, "fill_method"] = "INTERPOLATED"
        df.at[i, "confidence"] = "Medium"
        df.at[i, "notes"] = (
            f"Interpolated across a {n_bad}-day bracket between two valid "
            f"anchors (total {total_delta:.2f} km), weighted by reported Distance."
        )
        running += share


def _resolve_vehicle(df: pd.DataFrame, idx: list[int]) -> None:
    """idx is one vehicle's row positions, already date-sorted. Mutates df
    in place for every non-RAW_VALID row in idx."""
    valid_positions = [p for p, i in enumerate(idx) if df.at[i, "is_raw_valid"]]

    if not valid_positions:
        for i in idx:
            _apply_distance_only(df, i, "No odometer anchor exists for this vehicle at all;")
        return

    first, last = valid_positions[0], valid_positions[-1]
    _fill_no_before_anchor(df, idx[:first], idx[first])
    _fill_no_after_anchor(df, idx[last + 1 :], idx[last])
    for a, b in zip(valid_positions, valid_positions[1:]):
        bad_between = idx[a + 1 : b]
        if bad_between:
            _interpolate_between(df, idx[a], idx[b], bad_between)


def resolve(df_clean: pd.DataFrame, *, source_table: str) -> pd.DataFrame:
    """df_clean is the output of backend.metrics.clean_and_join (already
    deduped to one row per (Base License Plate, Report Date)). Returns a
    dataframe in ODOMETER_RESOLVED_SCHEMA's column order/shape, one row per
    input row -- this never fabricates rows for calendar dates that have no
    underlying utilization row at all, only resolves rows that exist."""
    cols = [
        "Base License Plate", "Report Date", "Distance",
        "Opening Odometer", "Closing Odometer", "Start Date", "End Date",
    ]
    df = df_clean[cols].sort_values(["Base License Plate", "Report Date"]).reset_index(drop=True)
    df = _sanitize(df)
    df["boundary_gap_distance"] = _compute_boundary_gaps(df)

    df["opening_odometer_clean"] = np.nan
    df["closing_odometer_clean"] = np.nan
    df["distance_by_odometer"] = np.nan
    df["fill_method"] = pd.Series([None] * len(df), dtype=object)
    df["confidence"] = pd.Series([None] * len(df), dtype=object)
    df["notes"] = pd.Series([None] * len(df), dtype=object)

    valid_mask = df["is_raw_valid"]
    df.loc[valid_mask, "opening_odometer_clean"] = df.loc[valid_mask, "opening_sanitized"]
    df.loc[valid_mask, "closing_odometer_clean"] = df.loc[valid_mask, "closing_sanitized"]
    df.loc[valid_mask, "distance_by_odometer"] = df.loc[valid_mask, "delta"]
    df.loc[valid_mask, "fill_method"] = "RAW_VALID"
    df.loc[valid_mask, "confidence"] = "High"

    for _, idx in df.groupby("Base License Plate").groups.items():
        _resolve_vehicle(df, list(idx))

    now = dt.datetime.now(dt.timezone.utc)
    return pd.DataFrame(
        {
            "base_license_plate": df["Base License Plate"],
            "report_date": df["Report Date"].dt.date,
            "opening_odometer_raw": df["opening_odometer_raw"],
            "closing_odometer_raw": df["closing_odometer_raw"],
            "opening_odometer_clean": df["opening_odometer_clean"],
            "closing_odometer_clean": df["closing_odometer_clean"],
            "distance_by_odometer": df["distance_by_odometer"],
            "distance_reported": df["Distance"],
            "boundary_gap_distance": df["boundary_gap_distance"],
            "fill_method": df["fill_method"],
            "anomaly_flags": df["anomaly_flags"],
            "confidence": df["confidence"],
            "notes": df["notes"],
            "source_table": source_table,
            "resolver_version": RESOLVER_VERSION,
            "resolved_at": now,
        }
    )


def _query_df(client: bigquery.Client, sql: str) -> pd.DataFrame:
    rows = client.query(sql).result()
    return pd.DataFrame([dict(row) for row in rows])


def run(settings: Settings, *, dry_run: bool = False) -> dict:
    """Always recomputes over the vehicle's full history (not just an
    incremental window): a reset event that closes after a previous run
    should be able to retroactively upgrade an earlier DISTANCE_FALLBACK day,
    which only a full recompute can do. Cheap at this fleet's current
    size (a few thousand rows)."""
    client = bq_client.get_client(settings)
    if not dry_run:
        bq_client.ensure_odometer_resolved_table(client, settings)

    raw_df = _query_df(
        client,
        f"SELECT * FROM `{settings.utilization_api_table_ref}` "
        "WHERE report_date < CURRENT_DATE('Asia/Kolkata')",
    ).rename(columns=_UTILIZATION_RENAMES)
    dim_vehicle = _query_df(client, f"SELECT * FROM `{settings.dim_vehicle_table_ref}`")
    dim_customer = _query_df(client, f"SELECT * FROM `{settings.dim_customer_table_ref}`")

    df_clean = metrics.clean_and_join(raw_df, dim_vehicle, dim_customer)
    resolved = resolve(df_clean, source_table=settings.bq_utilization_api_table)

    summary = (
        resolved.groupby(["fill_method", "confidence"]).size().rename("row_count").reset_index()
    )
    logger.info("Resolved %d row(s):\n%s", len(resolved), summary.to_string(index=False))

    if dry_run:
        return {"row_count": len(resolved), "summary": summary.to_dict(orient="records")}

    manual_overrides = bq_client.get_manual_overrides(client, settings)
    if not manual_overrides.empty:
        preserved_keys = set(
            zip(manual_overrides["base_license_plate"], manual_overrides["report_date"])
        )
        keep_mask = ~resolved.apply(
            lambda r: (r["base_license_plate"], r["report_date"]) in preserved_keys, axis=1
        )
        resolved = resolved[keep_mask]
        logger.info(
            "Preserving %d existing MANUAL_OVERRIDE row(s) through this recompute.",
            len(manual_overrides),
        )

    bq_client.load_odometer_resolved_rows(client, settings, resolved)
    if not manual_overrides.empty:
        bq_client.append_odometer_resolved_rows(client, settings, manual_overrides)

    total = len(resolved) + len(manual_overrides)
    logger.info("Loaded %d row(s) into %s", total, settings.odometer_resolved_table_ref)
    return {"row_count": total, "summary": summary.to_dict(orient="records")}
