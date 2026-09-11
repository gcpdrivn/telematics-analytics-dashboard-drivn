from __future__ import annotations

from fastapi import APIRouter

from backend import data_loader

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
