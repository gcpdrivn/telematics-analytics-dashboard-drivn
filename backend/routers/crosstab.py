from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException, Query

from backend import data_loader

router = APIRouter()

VALID_CUSTOMERS = {"All", "FreshBus", "ZingBus", "BillionE"}


@router.get("/crosstab-matrix")
def crosstab_matrix(
    customer: str = Query("All"),
    start_date: date | None = None,
    end_date: date | None = None,
):
    if customer not in VALID_CUSTOMERS:
        raise HTTPException(400, f"customer must be one of {sorted(VALID_CUSTOMERS)}")

    ctx = data_loader.get_clean_context(start_date, end_date)
    matrix = ctx["crosstab_matrix"]
    entry = matrix["customers"][customer]
    return {"customer": customer, "dates": matrix["dates"], **entry}
