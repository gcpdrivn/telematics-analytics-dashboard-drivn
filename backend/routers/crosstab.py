from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException, Query

from backend import data_loader, metrics

router = APIRouter()

VALID_CUSTOMERS = {"All", *metrics.CUSTOMERS}


@router.get("/crosstab-matrix")
def crosstab_matrix(
    customer: str = Query("All"),
    start_date: date | None = None,
    end_date: date | None = None,
):
    if customer not in VALID_CUSTOMERS:
        raise HTTPException(400, f"customer must be one of {sorted(VALID_CUSTOMERS)}")

    matrix = data_loader.get_crosstab_matrix(start_date, end_date)
    entry = matrix["customers"][customer]
    return {"customer": customer, "dates": matrix["dates"], **entry}
