"""Per-vehicle, day-by-day uptime classification (ran / did not run / not
sure), filling telemetry gaps via odometer-delta inference.

Classification rules (see /home/yogesh/.claude/plans/wobbly-cooking-rain.md):
- A day with a reported row is unambiguous: Distance > 0 -> ran, else did not run.
- A day with no reported row (a gap in the plate's full history, not just the
  display window) is resolved by comparing the Closing Odometer just before
  the gap to the Opening Odometer just after it:
    - 1-day gap, odometers differ  -> ran (inferred)
    - 1-day gap, odometers match   -> did not run (inferred)
    - >1-day gap, odometers match  -> did not run for every day in the gap
    - >1-day gap, odometers differ -> cannot be determined (not sure)
    - gap has no later reported row at all (still ongoing, relative to that
      vehicle's own most recent record) -> did not run
- A day before the vehicle's onboarding date is excluded from the uptime
  denominator entirely (not onboarded yet), not counted as not-sure. Onboarding
  date is dim_vehicle.device_installation_date when it's on file, falling back
  to the vehicle's first-ever reported row only when it isn't.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd

from backend import metrics

ODOMETER_TOLERANCE_KM = 0.1

# detailed status -> {general status, color, label}
STATUS_INFO: dict[str, dict] = {
    "RAN_CONFIRMED": {
        "general": "RAN",
        "color": "#2f9e44",
        "label": "Ran (reported)",
    },
    "RAN_INFERRED": {
        "general": "RAN",
        "color": "#74c69d",
        "label": "Ran (inferred from odometer across a gap)",
    },
    "NOT_RUN_CONFIRMED": {
        "general": "NOT_RUN",
        "color": "#e03131",
        "label": "Did not run (reported)",
    },
    "NOT_RUN_INFERRED_SINGLE": {
        "general": "NOT_RUN",
        "color": "#f08c00",
        "label": "Did not run (1-day gap, odometer unchanged)",
    },
    "NOT_RUN_INFERRED_MULTI": {
        "general": "NOT_RUN",
        "color": "#e8590c",
        "label": "Did not run (multi-day gap, odometer unchanged)",
    },
    "NOT_RUN_ONGOING": {
        "general": "NOT_RUN",
        "color": "#c2255c",
        "label": "Did not run (gap still ongoing, no report since)",
    },
    "INDETERMINATE": {
        "general": "NOT_SURE",
        "color": "#f1c40f",
        "label": "Not sure (multi-day gap, odometer changed)",
    },
    "NO_DATA": {
        "general": "NO_DATA",
        "color": "#adb5bd",
        "label": "Not onboarded yet",
    },
}

GENERAL_COLORS = {"RAN": "#2f9e44", "NOT_RUN": "#e03131", "NOT_SURE": "#f1c40f", "NO_DATA": "#adb5bd"}
GENERAL_LABELS = {
    "RAN": "Ran",
    "NOT_RUN": "Did not run",
    "NOT_SURE": "Not sure",
    "NO_DATA": "Not onboarded yet",
}

_DETAILED_ORDER = [
    "RAN_CONFIRMED",
    "RAN_INFERRED",
    "NOT_RUN_CONFIRMED",
    "NOT_RUN_INFERRED_SINGLE",
    "NOT_RUN_INFERRED_MULTI",
    "NOT_RUN_ONGOING",
    "INDETERMINATE",
    "NO_DATA",
]
_GENERAL_ORDER = ["RAN", "NOT_RUN", "NOT_SURE", "NO_DATA"]

LEGEND_GENERAL = [
    {"index": i, "status": k, "label": GENERAL_LABELS[k], "color": GENERAL_COLORS[k]}
    for i, k in enumerate(_GENERAL_ORDER, start=1)
]
LEGEND_DETAILED = [
    {"index": i, "status": k, "label": STATUS_INFO[k]["label"], "color": STATUS_INFO[k]["color"]}
    for i, k in enumerate(_DETAILED_ORDER, start=1)
]
GENERAL_INDEX = {item["status"]: item["index"] for item in LEGEND_GENERAL}
DETAILED_INDEX = {item["status"]: item["index"] for item in LEGEND_DETAILED}

# Calendar-facing view: the 8 detailed statuses collapsed into 4 buckets
# (detailed index -> bucket): 1 -> RUNNING_STABLE, {2, 7} -> RUNNING_UNSTABLE,
# {3, 4, 5, 6} -> STOPPED_STABLE, 8 -> NOT_ONBOARDED. RUNNING_STABLE is the
# expected/default day so it carries no fill; NOT_ONBOARDED keeps NO_DATA's
# original grey.
_COMBINED_ORDER = ["RUNNING_STABLE", "RUNNING_UNSTABLE", "STOPPED_STABLE", "NOT_ONBOARDED"]
COMBINED_LABELS = {
    "RUNNING_STABLE": "Running, stable",
    "RUNNING_UNSTABLE": "Running, unstable",
    "STOPPED_STABLE": "Stopped, stable",
    "NOT_ONBOARDED": "Not onboarded yet",
}
COMBINED_COLORS = {
    "RUNNING_STABLE": None,
    "RUNNING_UNSTABLE": "#f08c00",
    "STOPPED_STABLE": "#e03131",
    "NOT_ONBOARDED": "#adb5bd",
}
DETAILED_TO_COMBINED = {
    "RAN_CONFIRMED": "RUNNING_STABLE",
    "RAN_INFERRED": "RUNNING_UNSTABLE",
    "NOT_RUN_CONFIRMED": "STOPPED_STABLE",
    "NOT_RUN_INFERRED_SINGLE": "STOPPED_STABLE",
    "NOT_RUN_INFERRED_MULTI": "STOPPED_STABLE",
    "NOT_RUN_ONGOING": "STOPPED_STABLE",
    "INDETERMINATE": "RUNNING_UNSTABLE",
    "NO_DATA": "NOT_ONBOARDED",
}
LEGEND_COMBINED = [
    {"index": i, "status": k, "label": COMBINED_LABELS[k], "color": COMBINED_COLORS[k]}
    for i, k in enumerate(_COMBINED_ORDER, start=1)
]
COMBINED_INDEX = {item["status"]: item["index"] for item in LEGEND_COMBINED}


def _resolve_odo(row: dict | None, prefer: str, fallback: str) -> float | None:
    if row is None:
        return None
    val = row.get(prefer)
    if val is not None and not pd.isna(val):
        return float(val)
    val = row.get(fallback)
    if val is not None and not pd.isna(val):
        return float(val)
    return None


def _classify_vehicle(
    sub: pd.DataFrame, start_date: dt.date, end_date: dt.date
) -> dict[dt.date, dict]:
    sub = sub.sort_values("Report Date")
    row_by_date: dict[dt.date, dict] = dict(
        zip(sub["Report Date"].dt.date, sub.to_dict("records"))
    )
    known_dates = sorted(row_by_date)
    first_ever = known_dates[0]
    last_ever = known_dates[-1]
    known_set = set(known_dates)

    # dim_vehicle.device_installation_date is ground truth for onboarding,
    # when filled in -- falls back to "first reported date" (the previous
    # approximation) only for a plate that isn't in the vehicle master file
    # yet. Clipped to no later than first_ever so a bad/future install-date
    # entry can never mark an actual reported day as not-onboarded.
    install_ts = sub["Device Installation Date"].iloc[0] if not sub.empty else None
    if pd.notna(install_ts):
        onboarded_date = min(install_ts.date(), first_ever)
        onboarded_note = f"Vehicle not yet onboarded (device installed {install_ts.date().isoformat()})"
    else:
        onboarded_date = first_ever
        onboarded_note = "Vehicle not yet onboarded (approximated -- no device installation date on file)"

    display_dates = [start_date + dt.timedelta(days=i) for i in range((end_date - start_date).days + 1)]

    daily: dict[dt.date, dict] = {}
    n = len(display_dates)
    i = 0
    while i < n:
        d = display_dates[i]

        if d < onboarded_date:
            daily[d] = {"detailed": "NO_DATA", "distance": None, "note": onboarded_note}
            i += 1
            continue

        if d in known_set:
            row = row_by_date[d]
            raw_distance = row.get("Distance")
            distance = float(raw_distance) if raw_distance is not None and not pd.isna(raw_distance) else 0.0
            status = "RAN_CONFIRMED" if distance > 0 else "NOT_RUN_CONFIRMED"
            daily[d] = {"detailed": status, "distance": distance, "note": None}
            i += 1
            continue

        # Start of a gap block within the display window.
        block_start = i
        while i < n and display_dates[i] not in known_set:
            i += 1
        block_dates = display_dates[block_start:i]

        prev_known = max((kd for kd in known_dates if kd < block_dates[0]), default=None)
        next_known = min((kd for kd in known_dates if kd > block_dates[-1]), default=None)

        if next_known is None:
            for d2 in block_dates:
                daily[d2] = {
                    "detailed": "NOT_RUN_ONGOING",
                    "distance": None,
                    "note": f"No report since {last_ever.isoformat()} -- treated as stationary",
                }
            continue

        prev_row = row_by_date.get(prev_known)
        next_row = row_by_date.get(next_known)
        prev_closing = _resolve_odo(prev_row, "Closing Odometer", "Opening Odometer")
        next_opening = _resolve_odo(next_row, "Opening Odometer", "Closing Odometer")

        if prev_closing is None or next_opening is None:
            # prev_known can itself be None here -- e.g. a gap between a
            # known device_installation_date and the vehicle's first-ever
            # reported row, before which no odometer reference exists at
            # all -- so this must be checked before touching prev_known.
            status = "INDETERMINATE"
            note = "Odometer reading unavailable on one side of the gap -- cannot determine"
        else:
            # The true gap length is between the two known dates in FULL
            # history, not just the part of the gap visible inside the
            # display window -- classification must reflect what actually
            # happened.
            full_block_len = (next_known - prev_known).days - 1
            diff = abs(next_opening - prev_closing)
            if full_block_len == 1:
                if diff > ODOMETER_TOLERANCE_KM:
                    status = "RAN_INFERRED"
                    note = f"1-day gap: odometer {prev_closing:g} -> {next_opening:g}, vehicle moved"
                else:
                    status = "NOT_RUN_INFERRED_SINGLE"
                    note = f"1-day gap: odometer {prev_closing:g} -> {next_opening:g}, no change"
            else:
                if diff <= ODOMETER_TOLERANCE_KM:
                    status = "NOT_RUN_INFERRED_MULTI"
                    note = f"{full_block_len}-day gap: odometer {prev_closing:g} -> {next_opening:g}, no change"
                else:
                    status = "INDETERMINATE"
                    note = f"{full_block_len}-day gap: odometer {prev_closing:g} -> {next_opening:g}, cannot determine"

        for d2 in block_dates:
            daily[d2] = {"detailed": status, "distance": None, "note": note}

    return daily


def build_vehicle_uptime(
    df_clean_full: pd.DataFrame, start_date: dt.date, end_date: dt.date
) -> list[dict]:
    """df_clean_full must be the FULL (not date-filtered) cleaned/joined
    history -- gap boundaries just outside the display window still need to
    be resolved correctly."""
    veh_type_clubbed = metrics.clubbed_vehicle_type(df_clean_full)
    display_dates_desc = list(
        reversed([start_date + dt.timedelta(days=i) for i in range((end_date - start_date).days + 1)])
    )

    results = []
    for plate, sub in df_clean_full.groupby("Base License Plate"):
        daily_map = _classify_vehicle(sub, start_date, end_date)

        model_series = sub["Vehicle Model"].dropna()
        vehicle_model = model_series.iloc[0] if not model_series.empty else "Standard"
        customer_name = sub["Customer"].iloc[0]
        vehicle_type = veh_type_clubbed.get(plate, "Truck")

        ran = not_run = not_sure = not_onboarded = 0
        daily_list = []
        for d in display_dates_desc:
            info = daily_map[d]
            detailed = info["detailed"]
            general = STATUS_INFO[detailed]["general"]
            if general == "RAN":
                ran += 1
            elif general == "NOT_RUN":
                not_run += 1
            elif general == "NOT_SURE":
                not_sure += 1
            else:
                not_onboarded += 1
            daily_list.append(
                {
                    "date": d.isoformat(),
                    "general_status": general,
                    "detailed_status": detailed,
                    "combined_status": DETAILED_TO_COMBINED[detailed],
                    "distance": info["distance"],
                    "note": info["note"],
                }
            )

        total_days = len(display_dates_desc) - not_onboarded
        uptime_pct = (ran / total_days * 100.0) if total_days > 0 else None

        results.append(
            {
                "vehicle_number": plate,
                "vehicle_type": vehicle_type,
                "vehicle_model": vehicle_model,
                "customer_name": customer_name,
                "uptime_pct": round(uptime_pct, 1) if uptime_pct is not None else None,
                "ran_days": ran,
                "not_run_days": not_run,
                "not_sure_days": not_sure,
                "not_onboarded_days": not_onboarded,
                "total_days": total_days,
                "daily": daily_list,
            }
        )

    results.sort(key=lambda r: (r["uptime_pct"] if r["uptime_pct"] is not None else -1.0))
    return results
