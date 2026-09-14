"""Excel (.xlsx) generation for the vehicle uptime calendar. Cell coloring and
comments need per-cell control that pandas.to_excel doesn't give, so this
builds the workbook directly with openpyxl."""

from __future__ import annotations

import datetime as dt
import io
from typing import Literal

from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from backend.uptime import GENERAL_COLORS, LEGEND_DETAILED, LEGEND_GENERAL, STATUS_INFO

FIXED_HEADERS = ["Vehicle Number", "Vehicle Type", "Vehicle Model", "Customer Name", "Uptime %"]

_CODE_MAP = {
    "RAN": "R",
    "NOT_RUN": "N",
    "NOT_SURE": "?",
    "NO_DATA": "-",
    "RAN_CONFIRMED": "R",
    "RAN_INFERRED": "R~",
    "NOT_RUN_CONFIRMED": "N",
    "NOT_RUN_INFERRED_SINGLE": "N~1",
    "NOT_RUN_INFERRED_MULTI": "N~",
    "NOT_RUN_ONGOING": "N!",
    "INDETERMINATE": "?",
}


def _code(status: str) -> str:
    return _CODE_MAP.get(status, "")


def _fill(hex_color: str) -> PatternFill:
    c = hex_color.lstrip("#").upper()
    return PatternFill(start_color=f"FF{c}", end_color=f"FF{c}", fill_type="solid")


def _pct_color(pct: float | None) -> str:
    if pct is None:
        return "FF808080"
    if pct >= 90:
        return "FF2F9E44"
    if pct >= 70:
        return "FFE8590C"
    return "FFE03131"


def _add_legend_sheet(wb: Workbook, mode: str) -> None:
    ws = wb.create_sheet("Legend")
    ws.append(["Color", "Status", "Meaning"])
    for cell in ws[1]:
        cell.font = Font(bold=True)
    legend = LEGEND_GENERAL if mode == "general" else LEGEND_DETAILED
    for r, item in enumerate(legend, start=2):
        swatch = ws.cell(row=r, column=1, value=_code(item["status"]))
        swatch.fill = _fill(item["color"])
        swatch.alignment = Alignment(horizontal="center")
        ws.cell(row=r, column=2, value=item["status"])
        ws.cell(row=r, column=3, value=item["label"])
    ws.column_dimensions["A"].width = 10
    ws.column_dimensions["B"].width = 28
    ws.column_dimensions["C"].width = 55


def build_uptime_workbook(
    vehicles: list[dict], dates_desc: list[str], mode: Literal["general", "detailed"]
) -> bytes:
    """dates_desc: ISO date strings, latest first (matches the column order
    the user asked for). vehicles: build_vehicle_uptime() output."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Uptime"

    ws.append(FIXED_HEADERS + [dt.date.fromisoformat(d).strftime("%d-%b-%y") for d in dates_desc])
    header_font = Font(bold=True)
    for cell in ws[1]:
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center")

    for r, v in enumerate(vehicles, start=2):
        ws.cell(row=r, column=1, value=v["vehicle_number"])
        ws.cell(row=r, column=2, value=v["vehicle_type"])
        ws.cell(row=r, column=3, value=v["vehicle_model"])
        ws.cell(row=r, column=4, value=v["customer_name"])
        pct_cell = ws.cell(row=r, column=5, value=v["uptime_pct"])
        pct_cell.number_format = "0.0"
        pct_cell.font = Font(bold=True, color=_pct_color(v["uptime_pct"]))
        pct_cell.alignment = Alignment(horizontal="center")

        by_date = {day["date"]: day for day in v["daily"]}
        for c, d in enumerate(dates_desc, start=6):
            info = by_date.get(d)
            if info is None:
                continue
            status = info["general_status"] if mode == "general" else info["detailed_status"]
            color = GENERAL_COLORS[status] if mode == "general" else STATUS_INFO[status]["color"]
            cell = ws.cell(row=r, column=c, value=_code(status))
            cell.fill = _fill(color)
            cell.alignment = Alignment(horizontal="center")
            if mode == "detailed" and info.get("note"):
                cell.comment = Comment(info["note"], "Drivn Uptime Report")

    ws.freeze_panes = "F2"
    ws.column_dimensions["A"].width = 16
    ws.column_dimensions["B"].width = 10
    ws.column_dimensions["C"].width = 16
    ws.column_dimensions["D"].width = 14
    ws.column_dimensions["E"].width = 10
    for c in range(6, 6 + len(dates_desc)):
        ws.column_dimensions[get_column_letter(c)].width = 7

    _add_legend_sheet(wb, mode)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
