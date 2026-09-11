from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException, Query

from backend import data_loader, metrics

router = APIRouter()

VALID_SCOPES = {"all", "Bus", "Truck", "customers"}


@router.get("/kpi-summary")
def kpi_summary(
    scope: str = Query(..., description="all | Bus | Truck | customers"),
    start_date: date | None = None,
    end_date: date | None = None,
):
    if scope not in VALID_SCOPES:
        raise HTTPException(400, f"scope must be one of {sorted(VALID_SCOPES)}")

    ctx = data_loader.get_clean_context(start_date, end_date)
    active_stats = ctx["active_stats"]
    customers_all = metrics.build_customer_profiles(active_stats, ctx["dim_customer"])
    kpi_scopes = metrics.build_kpi_scopes(
        active_stats, ctx["dim_customer"], ctx["observation_days"], customers_all
    )
    return kpi_scopes[scope]
