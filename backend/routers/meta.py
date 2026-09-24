from __future__ import annotations

from fastapi import APIRouter

from backend import data_loader, metrics
from ingestion.odometer_resolver import GARBAGE_ABS_THRESHOLD_KM, OVERFLOW_SENTINEL_KM

router = APIRouter()


@router.get("/date-range")
def date_range():
    """The actual min/max report dates currently in the data (unfiltered),
    so the frontend's date picker has real bounds instead of a hardcoded
    range that goes stale the next time more data is ingested."""
    ctx = data_loader.get_clean_context()
    dates = ctx["df_clean"]["Report Date"]
    return {
        "min_date": dates.min().strftime("%Y-%m-%d"),
        "max_date": dates.max().strftime("%Y-%m-%d"),
    }


@router.get("/fleet-odometer-total")
def fleet_odometer_total():
    """Whole-fleet sum of final odometer readings till date. Deliberately takes
    no date/scope/customer params -- the header shows it as a filter-proof
    figure, so it's always computed over the full unfiltered data."""
    ctx = data_loader.get_clean_context()
    return metrics.build_fleet_odometer_total(
        ctx["df_clean"], OVERFLOW_SENTINEL_KM, GARBAGE_ABS_THRESHOLD_KM
    )
