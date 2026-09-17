"""Compares utilization_daily (Excel) against utilization_daily_api (Fleetx
API shadow table) for a given date range, to validate the API source before
any pipeline cutover. Read-only -- never writes to BigQuery.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

COMPARE_COLUMNS = ["distance_km", "running_time_hours", "opening_odometer", "closing_odometer"]


@dataclass
class ComparisonSummary:
    excel_rows: int
    api_rows: int
    common_rows: int
    excel_only_rows: int
    api_only_rows: int
    vehicles_compared: int
    mean_abs_diff: dict[str, float | None]
    median_pct_diff: dict[str, float | None]


def compare_daily(excel_df: pd.DataFrame, api_df: pd.DataFrame) -> pd.DataFrame:
    """Outer-joins the two sources on (base_license_plate, report_date).
    Rows present in only one source are kept (diffs NaN), flagged via
    `coverage`, so gaps are visible rather than silently dropped."""
    merged = excel_df.merge(
        api_df,
        on=["base_license_plate", "report_date"],
        how="outer",
        suffixes=("_excel", "_api"),
        indicator=True,
    )
    merged["coverage"] = merged["_merge"].map(
        {"left_only": "excel_only", "right_only": "api_only", "both": "both"}
    )
    merged = merged.drop(columns=["_merge"])

    for col in COMPARE_COLUMNS:
        excel_col, api_col = f"{col}_excel", f"{col}_api"
        merged[f"{col}_diff"] = merged[api_col] - merged[excel_col]
        denom = merged[excel_col].where(merged[excel_col] != 0)
        merged[f"{col}_pct_diff"] = (merged[f"{col}_diff"] / denom) * 100

    return merged.sort_values(["base_license_plate", "report_date"]).reset_index(drop=True)


def summarize(comparison_df: pd.DataFrame) -> ComparisonSummary:
    both = comparison_df[comparison_df["coverage"] == "both"]
    return ComparisonSummary(
        excel_rows=int((comparison_df["coverage"] != "api_only").sum()),
        api_rows=int((comparison_df["coverage"] != "excel_only").sum()),
        common_rows=len(both),
        excel_only_rows=int((comparison_df["coverage"] == "excel_only").sum()),
        api_only_rows=int((comparison_df["coverage"] == "api_only").sum()),
        vehicles_compared=comparison_df["base_license_plate"].nunique(),
        mean_abs_diff={
            col: round(both[f"{col}_diff"].abs().mean(), 2) if not both.empty else None
            for col in COMPARE_COLUMNS
        },
        median_pct_diff={
            col: round(both[f"{col}_pct_diff"].abs().median(), 1) if not both.empty else None
            for col in COMPARE_COLUMNS
        },
    )


def top_mismatches(comparison_df: pd.DataFrame, column: str, n: int = 10) -> pd.DataFrame:
    both = comparison_df[comparison_df["coverage"] == "both"].copy()
    both["_abs_diff"] = both[f"{column}_diff"].abs()
    cols = [
        "base_license_plate", "report_date",
        f"{column}_excel", f"{column}_api", f"{column}_diff", f"{column}_pct_diff",
    ]
    return both.sort_values("_abs_diff", ascending=False)[cols].head(n)
