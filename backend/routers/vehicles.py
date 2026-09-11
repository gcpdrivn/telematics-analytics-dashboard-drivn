from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException, Query

from backend import data_loader, metrics

router = APIRouter()

VALID_CATEGORIES = {"all", "Bus", "Truck"}


@router.get("/vehicles")
def vehicles(
    category: str = Query("all"),
    start_date: date | None = None,
    end_date: date | None = None,
):
    if category not in VALID_CATEGORIES:
        raise HTTPException(400, f"category must be one of {sorted(VALID_CATEGORIES)}")

    ctx = data_loader.get_clean_context(start_date, end_date)
    active_stats = ctx["active_stats"]
    if category != "all":
        active_stats = active_stats[active_stats["vehicle_type"] == category]

    roster = metrics.build_vehicle_roster(active_stats)
    below_80 = sum(1 for v in roster if v["is_below_80"])
    benchmark_km = round(sum(v["avg_km_per_day"] for v in roster) / len(roster), 1) if roster else 0.0
    benchmark_hrs = (
        round(sum(v["avg_hours_per_day"] for v in roster) / len(roster), 1) if roster else 0.0
    )

    return {
        "total_count": len(roster),
        "category": category,
        "below_80_count": below_80,
        "benchmark_mean_km_day": benchmark_km,
        "benchmark_mean_hrs_day": benchmark_hrs,
        "vehicles": roster,
    }
