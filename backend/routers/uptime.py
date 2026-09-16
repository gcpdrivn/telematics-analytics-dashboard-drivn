from __future__ import annotations

import datetime as dt
import io

import pandas as pd
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from backend import data_loader, metrics
from backend.excel_export import build_uptime_workbook
from backend.uptime import LEGEND_DETAILED, LEGEND_GENERAL, build_vehicle_uptime

router = APIRouter()

VALID_MODES = {"general", "detailed"}
VALID_CUSTOMERS = ["All", *metrics.CUSTOMERS]


def _load_scoped(customer: str | None) -> pd.DataFrame:
    """Full (unfiltered-by-date) cleaned/joined history, optionally scoped to
    one customer -- gap-boundary lookups need dates outside the eventual
    display window to still be resolvable."""
    tables = data_loader.get_tables()
    df_full = metrics.clean_and_join(tables["raw_utilization"], tables["dim_vehicle"], tables["dim_customer"])
    if customer and customer != "All":
        if customer not in metrics.CUSTOMERS:
            raise HTTPException(400, f"customer must be one of {VALID_CUSTOMERS}")
        df_full = df_full[df_full["Customer"] == customer]
    return df_full


def _resolve_range(
    start_date: dt.date | None, end_date: dt.date | None, df_full: pd.DataFrame
) -> tuple[dt.date, dt.date]:
    dates = df_full["Report Date"]
    start = start_date or dates.min().date()
    end = end_date or dates.max().date()
    if start > end:
        raise HTTPException(400, "start_date must not be after end_date")
    return start, end


def _dates_desc(start: dt.date, end: dt.date) -> list[str]:
    return [(end - dt.timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]


@router.get("/uptime")
def uptime(
    start_date: dt.date | None = None,
    end_date: dt.date | None = None,
    customer: str | None = Query(None),
):
    df_full = _load_scoped(customer)
    if df_full.empty:
        return {"dates": [], "vehicles": [], "legend_general": LEGEND_GENERAL, "legend_detailed": LEGEND_DETAILED}

    start, end = _resolve_range(start_date, end_date, df_full)
    vehicles = build_vehicle_uptime(df_full, start, end)
    return {
        "dates": _dates_desc(start, end),
        "vehicles": vehicles,
        "legend_general": LEGEND_GENERAL,
        "legend_detailed": LEGEND_DETAILED,
    }


@router.get("/uptime/export")
def uptime_export(
    start_date: dt.date | None = None,
    end_date: dt.date | None = None,
    customer: str | None = Query(None),
    mode: str = Query("general"),
):
    if mode not in VALID_MODES:
        raise HTTPException(400, f"mode must be one of {sorted(VALID_MODES)}")

    df_full = _load_scoped(customer)
    if df_full.empty:
        raise HTTPException(404, "No data available for the selected filters")

    start, end = _resolve_range(start_date, end_date, df_full)
    vehicles = build_vehicle_uptime(df_full, start, end)
    dates_desc = _dates_desc(start, end)
    content = build_uptime_workbook(vehicles, dates_desc, mode)  # type: ignore[arg-type]

    mode_slug = "running-status" if mode == "general" else "device-status"
    filename = f"vehicle-uptime-{mode_slug}-{start.isoformat()}-{end.isoformat()}.xlsx"
    return StreamingResponse(
        io.BytesIO(content),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
