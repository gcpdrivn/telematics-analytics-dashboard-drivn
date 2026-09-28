from __future__ import annotations

from datetime import date

from fastapi import APIRouter

from backend import data_loader, metrics

router = APIRouter()


@router.get("/customers/analytics")
def customers_analytics(start_date: date | None = None, end_date: date | None = None):
    ctx = data_loader.get_clean_context(start_date, end_date)
    active_stats = ctx["active_stats"]
    customers_all = metrics.build_customer_profiles(active_stats, ctx["dim_customer"])
    return {
        "customers_all": customers_all,
        "customers_top3": customers_all[:3],
        "customer_box_data": metrics.build_customer_box_data(ctx["df_clean"]),
        "dow_labels": metrics.DOW_ORDER,
        "dow_profiles": metrics.build_dow_profiles(ctx["df_clean"]),
        "active_timeline": metrics.build_active_timeline(ctx["df_clean"]),
    }


@router.get("/customers/soh-odometer")
def customers_soh_odometer():
    """Latest battery SoH vs latest live odometer, one point per vehicle.
    Takes no date range: both values are "as of the last morning ping", not
    aggregates over a window. Customer/vehicle-type filtering happens in
    the browser -- the whole fleet is under a hundred rows."""
    return metrics.build_soh_odometer(data_loader.get_soh_odometer())
