from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException, Query

from backend import data_loader, metrics

router = APIRouter()

VALID_CUSTOMERS = {"FreshBus", "ZingBus", "BillionE"}


@router.get("/vehicle-trajectories")
def vehicle_trajectories(
    customer: str = Query(...),
    start_date: date | None = None,
    end_date: date | None = None,
):
    if customer not in VALID_CUSTOMERS:
        raise HTTPException(400, f"customer must be one of {sorted(VALID_CUSTOMERS)}")

    ctx = data_loader.get_clean_context(start_date, end_date)
    active_stats = ctx["active_stats"]
    traj = metrics.build_vehicle_trajectories(ctx["df_clean"], active_stats, ctx["mileage_valid"])
    cust_data = traj["customers"].get(customer, {"notice": "", "vehicles": []})
    return {"customer": customer, "dates": traj["dates"], **cust_data}
