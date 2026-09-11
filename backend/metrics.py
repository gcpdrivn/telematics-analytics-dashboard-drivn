"""Metric computation, ported near-verbatim from Analysis/generate_presentation_report.py.

Deliberate differences from that script (see /home/yogesh/.claude/plans/swirling-baking-spark.md):
- Customer/OEM/route come from dim_customer/dim_vehicle DataFrames (BigQuery-backed via
  data_loader.py), not a hardcoded Python dict. CAM dedup, composite-key dedup, customer
  scope filtering and Heavy-Puller->Truck clubbing are still ported verbatim -- they're
  cleaning rules, not master data.
- Insight sentences, KPI fleet-breakdown/peak strings, and vehicle-trajectory sampling are
  computed live from data instead of hardcoded literals (the original had numbers baked
  into prose that could silently drift from the real data -- verified during research).
- Sparkline SVGs aren't generated here; build_vehicle_roster returns the raw
  daily_distance_history array and the frontend renders it.
- Daily KPI figures divide by the actual number of distinct report dates rather than a
  literal 30, so a future date-range filter produces correct per-day averages.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

CUSTOMERS = ["FreshBus", "ZingBus", "BillionE"]

# FreshBus/ZingBus are contractually fixed 10-bus fleets, used as the active-availability
# timeline's fixed denominator. Deliberately NOT derived from dim_vehicle row counts: that
# table carries a legacy plate alias for ZingBus (DL01PD9317 -> DL1PD9317) that would
# otherwise overcount it as 11. BillionE has no such fixed size -- it's a growing fleet,
# handled dynamically via first-telemetry-date tenure below.
FIXED_FLEET_SIZE = {"FreshBus": 10, "ZingBus": 10}

DOW_ORDER = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

BUS_BANDS = [
    {"id": "b0", "name": "0–200 km"},
    {"id": "b1", "name": "201–400 km"},
    {"id": "b2", "name": "401–600 km"},
    {"id": "b3", "name": "601–800 km"},
    {"id": "b4", "name": "800+ km"},
]
TRUCK_BANDS = [
    {"id": "b0", "name": "0–150 km"},
    {"id": "b1", "name": "151–300 km"},
    {"id": "b2", "name": "301–450 km"},
    {"id": "b3", "name": "451–600 km"},
]


def format_indian(val, decimals: int = 1) -> str:
    """Indian lakh/crore comma grouping, e.g. 426811 -> '4,26,811'."""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return "-"
    try:
        val = float(val)
    except (TypeError, ValueError):
        return str(val)
    is_neg = val < 0
    val = abs(val)
    if decimals > 0:
        fmt = f"{val:.{decimals}f}"
        int_p, dec_p = fmt.split(".")
    else:
        int_p = str(int(round(val)))
        dec_p = ""

    if len(int_p) <= 3:
        res = int_p
    else:
        last3 = int_p[-3:]
        rem = int_p[:-3]
        groups = []
        while len(rem) > 2:
            groups.insert(0, rem[-2:])
            rem = rem[:-2]
        if rem:
            groups.insert(0, rem)
        res = ",".join(groups) + "," + last3
    if is_neg:
        res = "-" + res
    return res + ("." + dec_p if dec_p else "")


def get_bus_band_idx(km: float) -> int:
    if km <= 200.0:
        return 0
    elif km <= 400.0:
        return 1
    elif km <= 600.0:
        return 2
    elif km <= 800.0:
        return 3
    return 4


def get_truck_band_idx(km: float) -> int:
    if km <= 150.0:
        return 0
    elif km <= 300.0:
        return 1
    elif km <= 450.0:
        return 2
    return 3


# ---------------------------------------------------------------------------
# Cleaning: CAM dedup, composite-key dedup, customer join/filter (Steps 1-3
# of the original script), Heavy Puller -> Truck clubbing (Step 4).
# ---------------------------------------------------------------------------


def clean_and_join(
    raw_df: pd.DataFrame, dim_vehicle: pd.DataFrame, dim_customer: pd.DataFrame
) -> pd.DataFrame:
    df = raw_df.copy()
    df["Report Date"] = pd.to_datetime(df["Report Date"])

    is_cam = df["License Plate / Vehicle Number"].astype(str).str.contains("CAM", case=False)
    has_alternative = df.groupby(["Base License Plate", "Report Date"])[
        "License Plate / Vehicle Number"
    ].transform(lambda s: (~s.astype(str).str.contains("CAM", case=False)).any())
    df_clean = df[~(is_cam & has_alternative)].copy()
    df_clean = df_clean.drop_duplicates(subset=["Base License Plate", "Report Date"], keep="first")

    vehicle_customer = dim_vehicle.set_index("base_license_plate")["customer_name"]
    df_clean["Customer"] = df_clean["Base License Plate"].map(vehicle_customer).fillna("Unassigned")

    customer_oem = dim_customer.set_index("customer_name")["oem"]
    df_clean["Customer_OEM"] = df_clean["Customer"].map(customer_oem).fillna("Standard")

    # Only the 3 commercial accounts are in scope -- anything unassigned
    # (or a pilot/test account not in dim_vehicle) is dropped here.
    df_clean = df_clean[df_clean["Customer"].isin(CUSTOMERS)].copy()
    return df_clean


def clubbed_vehicle_type(df_clean: pd.DataFrame) -> pd.Series:
    veh_type_raw = df_clean.groupby("Base License Plate")["Vehicle Type"].agg(lambda s: s.mode()[0])
    return veh_type_raw.apply(lambda t: "Truck" if t == "Heavy Puller" else t)


def valid_mileage_map(mileage_df: pd.DataFrame) -> dict[str, float]:
    """mileage_df already has mileage_km_per_soc nulled for >=10.0 readings
    (done at ingestion, see ingestion/mileage.py)."""
    return (
        mileage_df.dropna(subset=["mileage_km_per_soc"])
        .set_index("license_plate")["mileage_km_per_soc"]
        .to_dict()
    )


# ---------------------------------------------------------------------------
# Per-vehicle aggregation (Steps 3/5/5b): active-day volatility, tenure,
# odometer resolution, daily distance history.
# ---------------------------------------------------------------------------


def calc_active_volatility(sub_df: pd.DataFrame) -> pd.Series:
    active_distances = sub_df[sub_df["Distance"] > 0]["Distance"]
    n_act = len(active_distances)
    if n_act <= 1:
        mean_act = float(active_distances.iloc[0]) if n_act == 1 else 0.0
        std_act = 0.0
        cv_act = 0.0
        tier, tier_class = "Single Day", "vol-neutral"
    else:
        mean_act = float(active_distances.mean())
        std_act = float(active_distances.std(ddof=1))
        cv_act = float((std_act / mean_act) * 100.0) if mean_act > 0 else 0.0
        if cv_act < 25.0:
            tier, tier_class = "Stable", "vol-low"
        elif cv_act < 50.0:
            tier, tier_class = "Moderate", "vol-mid"
        else:
            tier, tier_class = "Volatile", "vol-high"
    return pd.Series(
        {
            "active_days_count": n_act,
            "active_mean_dist": mean_act,
            "active_std_dist": std_act,
            "active_cv_pct": cv_act,
            "volatility_tier": tier,
            "volatility_class": tier_class,
        }
    )


def get_final_odometer(sub_df: pd.DataFrame) -> float:
    """Latest date with a non-null Closing Odometer; else latest date with a
    non-null Opening Odometer. NOT a naive per-date coalesce -- if the most
    recent date has a null Closing but an earlier date doesn't, this returns
    the earlier date's Closing value, only falling to Opening if *no* row has
    a non-null Closing at all."""
    sub_closing = sub_df.dropna(subset=["Closing Odometer"]).sort_values("Report Date")
    if not sub_closing.empty:
        return float(sub_closing.iloc[-1]["Closing Odometer"])
    sub_opening = sub_df.dropna(subset=["Opening Odometer"]).sort_values("Report Date")
    if not sub_opening.empty:
        return float(sub_opening.iloc[-1]["Opening Odometer"])
    return np.nan


def build_active_stats(df_clean: pd.DataFrame, mileage_valid: dict[str, float]) -> pd.DataFrame:
    """One row per vehicle with total_distance > 0. The base DataFrame every
    other builder below works from."""
    end_date = df_clean["Report Date"].max()
    veh_type_clubbed = clubbed_vehicle_type(df_clean)
    vol_by_plate = df_clean.groupby("Base License Plate").apply(calc_active_volatility)

    stats = (
        df_clean.groupby(["Base License Plate", "Customer"])
        .agg(
            total_distance=("Distance", "sum"),
            total_hours=("Running Time (in hours)", "sum"),
            active_days=("Distance", lambda s: (s > 0).sum()),
            first_date=("Report Date", "min"),
            vehicle_model=(
                "Vehicle Model",
                lambda s: s.dropna().iloc[0] if not s.dropna().empty else "Standard",
            ),
            oem_desc=("Customer_OEM", "first"),
        )
        .reset_index()
    )
    stats["vehicle_type"] = stats["Base License Plate"].map(veh_type_clubbed)

    active_stats = stats[stats["total_distance"] > 0].copy()
    active_stats["total_days"] = active_stats.apply(
        lambda r: 30.0 if r["vehicle_type"] == "Bus" else float((end_date - r["first_date"]).days + 1),
        axis=1,
    )
    active_stats["avg_km_per_day"] = active_stats.apply(
        lambda r: (r["total_distance"] / r["active_days"]) if r["active_days"] > 0 else 0.0, axis=1
    )
    active_stats["avg_hours_per_day"] = active_stats.apply(
        lambda r: (r["total_hours"] / r["active_days"]) if r["active_days"] > 0 else 0.0, axis=1
    )
    active_stats["active_rate_pct"] = (active_stats["active_days"] / active_stats["total_days"]) * 100.0

    active_stats = active_stats.merge(
        vol_by_plate, left_on="Base License Plate", right_index=True, how="left"
    )

    active_stats["mileage_km_soc"] = active_stats["Base License Plate"].map(mileage_valid)
    active_stats["est_daily_soc_pct"] = active_stats.apply(
        lambda r: (r["avg_km_per_day"] / r["mileage_km_soc"])
        if pd.notna(r["mileage_km_soc"]) and r["mileage_km_soc"] > 0
        else np.nan,
        axis=1,
    )

    odo_by_plate = df_clean.groupby("Base License Plate").apply(get_final_odometer).to_dict()
    active_stats["final_odometer"] = active_stats["Base License Plate"].map(odo_by_plate)

    all_dates = sorted(df_clean["Report Date"].dt.strftime("%Y-%m-%d").unique())
    history_by_plate: dict[str, list[float]] = {}
    for p, sub in df_clean.groupby("Base License Plate"):
        date_dist_map = dict(zip(sub["Report Date"].dt.strftime("%Y-%m-%d"), sub["Distance"]))
        history_by_plate[p] = [float(date_dist_map.get(d, 0.0)) for d in all_dates]
    active_stats["daily_distance_history"] = active_stats["Base License Plate"].map(history_by_plate)

    return active_stats.reset_index(drop=True)


# ---------------------------------------------------------------------------
# KPI scopes (all / Bus / Truck / customers)
# ---------------------------------------------------------------------------


def _fleet_breakdown_and_prior(
    sub_fleet: pd.DataFrame, scope_key: str, dim_customer: pd.DataFrame
) -> tuple[str, str]:
    counts = sub_fleet.groupby("Customer").size()
    routes = dim_customer.set_index("customer_name")["routes_description"].to_dict()
    n_veh = len(sub_fleet)

    def counts_str(customers: list[str]) -> str:
        return ", ".join(f"{c}: {int(counts.get(c, 0))}" for c in customers)

    if scope_key == "Bus":
        breakdown = f"{n_veh} Buses ({counts_str(['FreshBus', 'ZingBus'])})"
        prior = "100% Intercity Commercial Fleet"
    elif scope_key == "Truck":
        breakdown = f"{n_veh} Trucks ({counts_str(['BillionE'])})"
        route = routes.get("BillionE")
        prior = f"{n_veh} Active Commercial Trucks" + (f" ({route} Route)" if route else "")
    else:  # 'all' or 'customers'
        n_bus = int((sub_fleet["vehicle_type"] == "Bus").sum())
        n_truck = int((sub_fleet["vehicle_type"] == "Truck").sum())
        breakdown = (
            f"{n_bus} Buses ({counts_str(['FreshBus', 'ZingBus'])}) • "
            f"{n_truck} Trucks ({counts_str(['BillionE'])})"
        )
        present = [c for c in CUSTOMERS if counts.get(c, 0) > 0]
        prior = f"{n_veh} Active Commercial Assets ({', '.join(present)})"
    return breakdown, prior


_KPI_LABELS = {
    "all": (
        "Total Fleet Distance / Day", "Total Operating Time / Day", "Active Commercial Fleet",
        "Peak Active Vehicle", "km / day / vehicle", "hrs / day / vehicle",
    ),
    "Bus": (
        "Bus Fleet Distance / Day", "Bus Operating Time / Day", "Active Bus Fleet",
        "Peak Active Bus", "km / day / bus", "hrs / day / bus",
    ),
    "Truck": (
        "Truck Fleet Distance / Day", "Truck Operating Time / Day", "Active Truck Fleet",
        "Peak Active Truck", "km / day / truck", "hrs / day / truck",
    ),
    "customers": (
        "Commercial Fleet Distance / Day", "Commercial Operating Time / Day", "Total Commercial Fleet",
        "Top Customer Account", "km / day / vehicle", "hrs / day / vehicle",
    ),
}


def generate_kpis_for_scope(
    sub_fleet: pd.DataFrame,
    scope_key: str,
    dim_customer: pd.DataFrame,
    observation_days: float,
    customers_all: list[dict] | None = None,
) -> dict:
    n_veh = len(sub_fleet)
    tot_dist = float(sub_fleet["total_distance"].sum())
    tot_hrs = float(sub_fleet["total_hours"].sum())
    daily_dist = tot_dist / observation_days if observation_days > 0 else 0.0
    daily_hrs = tot_hrs / observation_days if observation_days > 0 else 0.0
    per_veh_daily_dist = float(sub_fleet["avg_km_per_day"].mean()) if n_veh else 0.0
    per_veh_daily_hrs = float(sub_fleet["avg_hours_per_day"].mean()) if n_veh else 0.0

    fleet_breakdown, fleet_prior = _fleet_breakdown_and_prior(sub_fleet, scope_key, dim_customer)
    dist_label, hours_label, fleet_label, peak_label, dist_unit_sub, hours_unit_sub = _KPI_LABELS[
        scope_key
    ]

    if scope_key == "customers":
        top_cust = customers_all[0] if customers_all else None
        if top_cust:
            peak_num = top_cust["customer"]
            peak_sub = (
                f"{format_indian(top_cust['total_distance'], 0)} km • "
                f"{format_indian(top_cust['avg_km_day_per_veh'], 0)} km/d per veh"
            )
            peak_prior = (
                f"{format_indian(top_cust['avg_hrs_day_per_veh'], 1)} hrs / day / vehicle • "
                f"{top_cust['active_days_avg']:.0f}/{top_cust['total_days_avg']:.0f} d Active"
            )
        else:
            peak_num, peak_sub, peak_prior = "-", "-", "-"
    elif n_veh > 0:
        top_asset = sub_fleet.sort_values(["active_days", "avg_km_per_day"], ascending=[False, False]).iloc[0]
        peak_num = str(top_asset["Base License Plate"])
        peak_sub = (
            f"{format_indian(top_asset['avg_km_per_day'], 0)} km/d • "
            f"{int(top_asset['active_days'])}/{int(top_asset['total_days'])} d "
            f"({top_asset['active_rate_pct']:.0f}%)"
        )
        peak_prior = f"Total: {format_indian(top_asset['total_distance'], 0)} km"
    else:
        peak_num, peak_sub, peak_prior = "-", "-", "-"

    return {
        "total_vehicles": n_veh,
        "total_distance": round(tot_dist),
        "daily_distance": round(daily_dist),
        "per_veh_daily_dist": round(per_veh_daily_dist),
        "total_hours": tot_hrs,
        "daily_hours": daily_hrs,
        "per_veh_daily_hrs": per_veh_daily_hrs,
        "dist_label": dist_label,
        "hours_label": hours_label,
        "dist_unit_sub": dist_unit_sub,
        "hours_unit_sub": hours_unit_sub,
        "fleet_label": fleet_label,
        "fleet_breakdown": fleet_breakdown,
        "fleet_prior": fleet_prior,
        "peak_label": peak_label,
        "peak_num": peak_num,
        "peak_sub": peak_sub,
        "peak_prior": peak_prior,
    }


def build_kpi_scopes(
    active_stats: pd.DataFrame,
    dim_customer: pd.DataFrame,
    observation_days: float,
    customers_all: list[dict],
) -> dict:
    return {
        "all": generate_kpis_for_scope(active_stats, "all", dim_customer, observation_days),
        "Bus": generate_kpis_for_scope(
            active_stats[active_stats["vehicle_type"] == "Bus"], "Bus", dim_customer, observation_days
        ),
        "Truck": generate_kpis_for_scope(
            active_stats[active_stats["vehicle_type"] == "Truck"], "Truck", dim_customer, observation_days
        ),
        "customers": generate_kpis_for_scope(
            active_stats, "customers", dim_customer, observation_days, customers_all=customers_all
        ),
    }


# ---------------------------------------------------------------------------
# Customer profiles + dynamic insight text
# ---------------------------------------------------------------------------


def _dynamic_insight(customer: str, vol_tier: str, cv_pct: float) -> str:
    if vol_tier == "Stable":
        return (
            f"⚡ <b>Dispatch Stability:</b> Highly consistent day-to-day distance "
            f"({cv_pct:.1f}% Daily KM Volatility) — predictable schedule supports reliable depot charging."
        )
    if vol_tier == "Moderate":
        return (
            f"⚡ <b>Dispatch Variability:</b> Moderate day-to-day swings "
            f"({cv_pct:.1f}% Daily KM Volatility) — some route or demand flexibility worth monitoring."
        )
    if vol_tier == "Volatile":
        return (
            f"⚡ <b>Dispatch Volatility:</b> High day-to-day variability "
            f"({cv_pct:.1f}% Daily KM Volatility) — irregular dispatch pattern, worth investigating."
        )
    return (
        "ℹ️ <b>Limited Data:</b> Not enough multi-day activity yet to assess "
        "dispatch volatility for this account."
    )


def build_customer_profiles(active_stats: pd.DataFrame, dim_customer: pd.DataFrame) -> list[dict]:
    routes = dim_customer.set_index("customer_name")["routes_description"].to_dict()
    total_fleet_dist = float(active_stats["total_distance"].sum())
    profiles = []

    for cust in CUSTOMERS:
        c_df = active_stats[active_stats["Customer"] == cust]
        if c_df.empty:
            continue
        c_dist = float(c_df["total_distance"].sum())
        c_hours = float(c_df["total_hours"].sum())
        c_avg_daily_km_per_veh = float(c_df["avg_km_per_day"].mean())
        c_avg_daily_hrs_per_veh = float(c_df["avg_hours_per_day"].mean())
        c_act_days_avg = float(c_df["active_days"].mean())
        c_tot_days_avg = float(c_df["total_days"].mean())
        share_pct = (c_dist / total_fleet_dist) * 100.0 if total_fleet_dist > 0 else 0.0
        c_mileage_mean = c_df["mileage_km_soc"].dropna().mean()

        valid_vols = c_df[c_df["active_days_count"] > 1]
        if not valid_vols.empty:
            c_avg_std = float(valid_vols["active_std_dist"].mean())
            c_avg_cv = float(valid_vols["active_cv_pct"].mean())
            if c_avg_cv < 25.0:
                c_vol_tier, c_vol_class = "Stable", "vol-low"
            elif c_avg_cv < 50.0:
                c_vol_tier, c_vol_class = "Moderate", "vol-mid"
            else:
                c_vol_tier, c_vol_class = "Volatile", "vol-high"
        else:
            c_avg_std, c_avg_cv = 0.0, 0.0
            c_vol_tier, c_vol_class = "Yard Holding", "vol-neutral"

        profiles.append(
            {
                "customer": cust,
                "route": routes.get(cust, "Regional Line-haul"),
                "vehicle_count": len(c_df),
                "total_distance": c_dist,
                "total_hours": c_hours,
                "avg_km_day_per_veh": c_avg_daily_km_per_veh,
                "avg_hrs_day_per_veh": c_avg_daily_hrs_per_veh,
                "active_days_avg": c_act_days_avg,
                "total_days_avg": c_tot_days_avg,
                "share_pct": share_pct,
                "oem": c_df["oem_desc"].iloc[0],
                "avg_mileage_soc": float(c_mileage_mean) if pd.notna(c_mileage_mean) else None,
                "avg_std_active": c_avg_std,
                "avg_cv_pct": c_avg_cv,
                "vol_tier": c_vol_tier,
                "vol_class": c_vol_class,
                "insight": _dynamic_insight(cust, c_vol_tier, c_avg_cv),
            }
        )

    profiles.sort(key=lambda x: x["total_distance"], reverse=True)
    return profiles


def build_customer_box_data(df_clean: pd.DataFrame) -> dict[str, list[float]]:
    return {
        cust: [
            round(float(v), 2)
            for v in df_clean[(df_clean["Customer"] == cust) & (df_clean["Distance"] > 0)]["Distance"]
        ]
        for cust in CUSTOMERS
    }


def build_dow_profiles(df_clean: pd.DataFrame) -> dict:
    df_clean = df_clean.copy()
    df_clean["DayOfWeek"] = df_clean["Report Date"].dt.day_name()
    profiles = {}
    for cust in CUSTOMERS:
        c_df = df_clean[(df_clean["Customer"] == cust) & (df_clean["Distance"] > 0)]
        dow_agg = (
            c_df.groupby("DayOfWeek")
            .agg(
                mean_dist=("Distance", "mean"),
                std_dist=("Distance", lambda s: s.std(ddof=1) if len(s) > 1 else 0.0),
                samples=("Distance", "count"),
            )
            .reindex(DOW_ORDER)
            .fillna(0.0)
            .reset_index()
        )
        dow_agg["volatility_pct"] = np.where(
            dow_agg["mean_dist"] > 0, (dow_agg["std_dist"] / dow_agg["mean_dist"]) * 100.0, 0.0
        )
        profiles[cust] = dow_agg.to_dict(orient="records")
    return profiles


def build_active_timeline(df_clean: pd.DataFrame) -> dict:
    all_dates = sorted(df_clean["Report Date"].dt.strftime("%Y-%m-%d").unique())
    first_dates_by_plate = df_clean.groupby("Base License Plate")["Report Date"].min().to_dict()
    billion_e_plates = df_clean[df_clean["Customer"] == "BillionE"]["Base License Plate"].unique()

    active_timeline: dict = {"dates": all_dates}
    total_active_counts = [0] * len(all_dates)
    total_eligible_denoms = [0] * len(all_dates)

    for cust in CUSTOMERS:
        c_df = df_clean[(df_clean["Customer"] == cust) & (df_clean["Distance"] > 0)]
        counts = c_df.groupby(c_df["Report Date"].dt.strftime("%Y-%m-%d"))["Base License Plate"].nunique().to_dict()
        cnt_list = [int(counts.get(d, 0)) for d in all_dates]

        if cust == "BillionE":
            denom_list = []
            pct_list = []
            for d in all_dates:
                d_ts = pd.to_datetime(d)
                eligible = [p for p in billion_e_plates if first_dates_by_plate.get(p) <= d_ts]
                denom = len(eligible)
                denom_list.append(denom)
                c = counts.get(d, 0)
                pct_list.append(round((c / denom * 100.0), 1) if denom > 0 else 0.0)
            fleet_size_total = len(billion_e_plates)
        else:
            fixed = FIXED_FLEET_SIZE[cust]
            denom_list = [fixed] * len(all_dates)
            pct_list = [round((c / fixed) * 100.0, 1) for c in cnt_list]
            fleet_size_total = fixed

        active_timeline[cust] = {
            "counts": cnt_list,
            "pcts": pct_list,
            "fleet_sizes": denom_list,
            "fleet_size": fleet_size_total,
        }
        for i in range(len(all_dates)):
            total_active_counts[i] += cnt_list[i]
            total_eligible_denoms[i] += denom_list[i]

    active_timeline["total"] = {
        "counts": total_active_counts,
        "pcts": [
            round((total_active_counts[i] / total_eligible_denoms[i] * 100.0), 1)
            if total_eligible_denoms[i] > 0
            else 0.0
            for i in range(len(all_dates))
        ],
        "fleet_sizes": total_eligible_denoms,
        "fleet_size": sum(FIXED_FLEET_SIZE.values()) + len(billion_e_plates),
    }
    return active_timeline


# ---------------------------------------------------------------------------
# Crosstab matrix. Operates on ALL records (including 0-km days) -- unlike
# volatility/DOW, this is deliberately not filtered to Distance > 0.
# ---------------------------------------------------------------------------


def build_crosstab_matrix(df_clean: pd.DataFrame) -> dict:
    matrix_dates = sorted(df_clean["Report Date"].dt.strftime("%Y-%m-%d").unique(), reverse=True)
    matrix_data: dict = {"bands": BUS_BANDS, "dates": matrix_dates, "customers": {}}

    for c in ["All"] + CUSTOMERS:
        c_df = df_clean if c == "All" else df_clean[df_clean["Customer"] == c]
        is_truck = c == "BillionE"
        bands = TRUCK_BANDS if is_truck else BUS_BANDS
        n_bands = len(bands)
        get_idx_fn = get_truck_band_idx if is_truck else get_bus_band_idx
        b_indices = c_df["Distance"].apply(get_idx_fn)

        total_runs = len(c_df)
        band_counts_tot = [int((b_indices == i).sum()) for i in range(n_bands)]
        band_pcts_tot = [
            round((cnt / total_runs * 100.0), 1) if total_runs > 0 else 0.0 for cnt in band_counts_tot
        ]
        peak_row = c_df.sort_values("Distance", ascending=False).iloc[0] if not c_df.empty else None

        if is_truck:
            long_share = round(band_counts_tot[3] / total_runs * 100.0, 1) if total_runs > 0 else 0.0
            mid_share = (
                round((band_counts_tot[1] + band_counts_tot[2]) / total_runs * 100.0, 1)
                if total_runs > 0
                else 0.0
            )
            short_share = round(band_counts_tot[0] / total_runs * 100.0, 1) if total_runs > 0 else 0.0
            long_title, mid_title, short_title = ">450 km", "150–450 km", "≤150 km"
        else:
            long_share = (
                round((band_counts_tot[3] + band_counts_tot[4]) / total_runs * 100.0, 1)
                if total_runs > 0
                else 0.0
            )
            mid_share = (
                round((band_counts_tot[1] + band_counts_tot[2]) / total_runs * 100.0, 1)
                if total_runs > 0
                else 0.0
            )
            short_share = round(band_counts_tot[0] / total_runs * 100.0, 1) if total_runs > 0 else 0.0
            long_title, mid_title, short_title = ">600 km", "200–600 km", "≤200 km"

        by_date = []
        for d in matrix_dates:
            d_mask = c_df["Report Date"].dt.strftime("%Y-%m-%d") == d
            d_df = c_df[d_mask]
            d_indices = b_indices[d_mask]
            total_veh = len(d_df)
            counts = [0] * n_bands
            vehs: list[list[str]] = [[] for _ in range(n_bands)]
            veh_details: list[list[dict]] = [[] for _ in range(n_bands)]
            kms = [0.0] * n_bands
            for (_, row), idx in zip(d_df.iterrows(), d_indices):
                counts[idx] += 1
                plate_str = str(row["Base License Plate"])
                vehs[idx].append(plate_str)
                veh_details[idx].append(
                    {
                        "p": plate_str,
                        "km": round(float(row["Distance"]), 1),
                        "hrs": round(float(row["Running Time (in hours)"]), 1)
                        if pd.notna(row["Running Time (in hours)"])
                        else 0.0,
                        "spd": round(float(row["Average Speed"]), 1)
                        if pd.notna(row["Average Speed"]) and row["Average Speed"] > 0
                        else 0.0,
                        "c": str(row["Customer"]),
                        "m": str(row["Vehicle Model"]) if pd.notna(row["Vehicle Model"]) else "Standard",
                    }
                )
                kms[idx] += round(float(row["Distance"]), 1)
            pcts = (
                [round((cnt / total_veh * 100.0), 1) for cnt in counts] if total_veh > 0 else [0.0] * n_bands
            )
            by_date.append(
                {
                    "date": d,
                    "total": total_veh,
                    "counts": counts,
                    "pcts": pcts,
                    "vehs": vehs,
                    "veh_details": veh_details,
                    "kms": [round(k, 1) for k in kms],
                }
            )

        matrix_data["customers"][c] = {
            "bands": bands,
            "total_runs": total_runs,
            "summary_counts": band_counts_tot,
            "summary_pcts": band_pcts_tot,
            "long_share": long_share,
            "mid_share": mid_share,
            "short_share": short_share,
            "long_title": long_title,
            "mid_title": mid_title,
            "short_title": short_title,
            "peak_run": (
                {
                    "plate": str(peak_row["Base License Plate"]),
                    "km": round(float(peak_row["Distance"]), 1),
                    "date": peak_row["Report Date"].strftime("%d-%b"),
                    "cust": str(peak_row["Customer"]),
                }
                if peak_row is not None
                else None
            ),
            "by_date": by_date,
        }
    return matrix_data


# ---------------------------------------------------------------------------
# Vehicle trajectory sampling: dynamic replacement for the original script's
# hardcoded per-customer plate lists.
# ---------------------------------------------------------------------------


def _percentile_indices(n_items: int, n_picks: int) -> list[int]:
    """n_picks indices spanning [0, n_items-1] evenly, deduplicated -- used to
    sample vehicles across a distribution (SoC mileage or km/day intensity)
    rather than just taking the top N."""
    if n_items <= n_picks:
        return list(range(n_items))
    positions = np.linspace(0, n_items - 1, n_picks)
    result: list[int] = []
    for p in positions:
        idx = int(round(p))
        if idx not in result:
            result.append(idx)
    return result


def select_trajectory_vehicles(
    cust_active: pd.DataFrame, mileage_valid: dict[str, float], n: int = 5
) -> list[dict]:
    plates = cust_active["Base License Plate"].tolist()
    with_soc = sorted(
        ((p, mileage_valid[p]) for p in plates if p in mileage_valid), key=lambda x: x[1]
    )

    if len(with_soc) >= n:
        idxs = _percentile_indices(len(with_soc), n)
        return [{"plate": with_soc[i][0], "soc": with_soc[i][1]} for i in idxs]

    selected = [{"plate": p, "soc": soc} for p, soc in with_soc]
    remaining = [p for p in plates if p not in mileage_valid]
    km_by_plate = cust_active.set_index("Base License Plate")["avg_km_per_day"].to_dict()
    remaining_sorted = sorted(remaining, key=lambda p: km_by_plate.get(p, 0.0), reverse=True)
    slots_left = n - len(selected)
    selected += [{"plate": p, "soc": None} for p in remaining_sorted[:slots_left]]
    return selected


def build_vehicle_trajectories(
    df_clean: pd.DataFrame, active_stats: pd.DataFrame, mileage_valid: dict[str, float], n: int = 5
) -> dict:
    matrix_dates = sorted(df_clean["Report Date"].dt.strftime("%Y-%m-%d").unique(), reverse=True)
    plate_date_keys = list(
        zip(df_clean["Base License Plate"], df_clean["Report Date"].dt.strftime("%Y-%m-%d"))
    )
    # No default on these .get() lookups: a missing (plate, date) key means the
    # vehicle's device never reported that day at all (a transmission gap),
    # which is a different situation from a real row reporting Distance == 0
    # (device reported fine, vehicle just didn't move). Collapsing both to 0.0
    # made them indistinguishable on the trajectory chart.
    dist_map = dict(zip(plate_date_keys, df_clean["Distance"]))
    gps_map = dict(zip(plate_date_keys, df_clean["GPS Disconnection count"]))

    out: dict = {"dates": matrix_dates, "customers": {}}
    for cust in CUSTOMERS:
        cust_active = active_stats[active_stats["Customer"] == cust]
        if cust_active.empty:
            continue
        selection = select_trajectory_vehicles(cust_active, mileage_valid, n=n)
        soc_values = [s["soc"] for s in selection if s["soc"] is not None]

        if len(soc_values) >= n:
            notice = (
                f"⚡ <b>SoC Efficiency Coverage:</b> All {n} vehicles have validated "
                f"benchmarked battery mileage, spanning {min(soc_values):.1f} km/SoC (low) to "
                f"{max(soc_values):.1f} km/SoC (high)."
            )
        elif soc_values:
            notice = (
                f"ℹ️ <b>SoC Availability Notice:</b> Only {len(soc_values)} of {n} vehicles "
                f"have benchmarked SoC in the digitalized BMS log ({min(soc_values):.1f} to "
                f"{max(soc_values):.1f} km/SoC); the rest are plotted without benchmarked SoC."
            )
        else:
            notice = (
                f"⚠️ <b>SoC Availability Notice:</b> Benchmarked battery mileage "
                f"(km/% SoC) is currently not available for {cust} vehicles in the BMS log; "
                f"{n} vehicles are plotted across operational utilization tiers."
            )

        v_list = []
        for s in selection:
            p = s["plate"]
            dists: list[float | None] = []
            gps_disconnections: list[int | None] = []
            for d in matrix_dates:
                raw = dist_map.get((p, d))
                dists.append(round(float(raw), 1) if raw is not None and not pd.isna(raw) else None)
                gps_raw = gps_map.get((p, d))
                gps_disconnections.append(
                    int(gps_raw) if gps_raw is not None and not pd.isna(gps_raw) else None
                )
            nonzero = [x for x in dists if x is not None and x > 0]
            avg_act = round(float(sum(nonzero) / len(nonzero)), 1) if nonzero else 0.0
            soc_val = s["soc"]
            soc_str = f"SoC: {soc_val:.1f} km/SoC" if soc_val is not None else "SoC: N/A"
            v_list.append(
                {
                    "plate": p,
                    "soc": soc_val,
                    "soc_str": soc_str,
                    "avg_km": avg_act,
                    "distances": dists,
                    "gps_disconnections": gps_disconnections,
                }
            )

        out["customers"][cust] = {"notice": notice, "vehicles": v_list}
    return out


# ---------------------------------------------------------------------------
# Vehicle roster for the master tables (replaces the original's server-
# rendered <tr> HTML + baked sparkline SVGs).
# ---------------------------------------------------------------------------


def build_vehicle_roster(active_stats: pd.DataFrame) -> list[dict]:
    sorted_df = active_stats.sort_values(
        ["active_rate_pct", "avg_km_per_day"], ascending=[True, True]
    ).reset_index(drop=True)

    records = []
    for i, r in sorted_df.iterrows():
        records.append(
            {
                "rank": i + 1,
                "plate": r["Base License Plate"],
                "vehicle_type": r["vehicle_type"],
                "customer": r["Customer"],
                "vehicle_model": r["vehicle_model"],
                "active_days": int(r["active_days"]),
                "total_days": r["total_days"],
                "active_rate_pct": round(float(r["active_rate_pct"]), 1),
                "is_below_80": bool(r["active_rate_pct"] < 80.0),
                "avg_km_per_day": round(float(r["avg_km_per_day"]), 1),
                "avg_hours_per_day": round(float(r["avg_hours_per_day"]), 1),
                "total_distance": round(float(r["total_distance"]), 1),
                "total_hours": round(float(r["total_hours"]), 1),
                "final_odometer": float(r["final_odometer"]) if pd.notna(r["final_odometer"]) else None,
                "mileage_km_soc": float(r["mileage_km_soc"]) if pd.notna(r["mileage_km_soc"]) else None,
                "est_daily_soc_pct": float(r["est_daily_soc_pct"])
                if pd.notna(r["est_daily_soc_pct"])
                else None,
                "active_days_count": int(r["active_days_count"]),
                "active_mean_dist": round(float(r["active_mean_dist"]), 1),
                "active_std_dist": round(float(r["active_std_dist"]), 1),
                "active_cv_pct": round(float(r["active_cv_pct"]), 1),
                "volatility_tier": r["volatility_tier"],
                "volatility_class": r["volatility_class"],
                "daily_distance_history": r["daily_distance_history"],
            }
        )
    return records
