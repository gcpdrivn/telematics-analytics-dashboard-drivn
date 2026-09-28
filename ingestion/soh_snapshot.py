"""Morning ping of Fleetx's Realtime API: each vehicle's latest battery SoH
and live odometer, upserted into vehicle_soh_latest (one row per vehicle)
for the Customers page's SoH vs odometer chart.

Per vehicle, the Realtime feed has one record per physical device. DashCam
devices are skipped (their odometers are unrelated to the vehicle's). Of the
rest, the device reporting a SoH wins, most recently updated first; SoH and
odometer are both read from that one device so the pair is consistent.

Upsert rules (combine()):
- a vehicle seen for the first time is added;
- a vehicle in today's ping has each value replaced only by a good new one --
  a missing SoH, or an odometer that is a known fault or has gone backwards,
  keeps the stored value (the raw reading and the reason are still recorded);
- a vehicle missing from today's ping keeps its row untouched.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from ingestion import bq_client, fleetx_client
from ingestion.config import Settings
from ingestion.fleetx_vehicle_map import _base_plate
from ingestion.odometer_resolver import GARBAGE_ABS_THRESHOLD_KM
from ingestion.schema import SOH_LATEST_SCHEMA

logger = logging.getLogger(__name__)

CAMERA_DEVICE_TYPES = {"LCD603-2CH-CAT1", "FX20-L-2CH"}

# In order of preference. evSOH is what the bus CAN devices report;
# evExtraParameters is a JSON string inside otherAttributes, flattened to
# evExtra.<key> before the lookup.
SOH_ALIASES = ["evSOH", "batterySoh", "evExtra.soh_percentage", "soh"]

# Odometers only go up. A live reading more than this fraction below the
# stored one is a device reset/swap (e.g. DL1PD8669's device reading 1,843 km
# on a ~174,000 km bus), not the vehicle's odometer.
BACKWARDS_TOLERANCE = 0.01

_SOH_COLUMNS = ["soh_pct", "soh_field", "soh_reported_at"]
_ODOMETER_COLUMNS = ["odometer_km", "odometer_reported_at"]
_COLUMNS = [f.name for f in SOH_LATEST_SCHEMA]


def plate_key(plate: str) -> str:
    """Matching key shared by Fleetx vehicleNumbers and dim_vehicle plates:
    device suffixes stripped (_API, _CAM, OBD, a trailing '_') and Fleetx's
    DL01 vs DL1 spelling difference folded (DL01PD9317 vs DL1PD9317)."""
    base = _base_plate(plate).upper()
    return "DL1" + base[4:] if base.startswith("DL01") else base


def _attributes(device: dict[str, Any]) -> dict[str, Any]:
    attrs = dict(device.get("otherAttributes") or {})
    if attrs.get("evExtraParameters"):
        try:
            for key, value in json.loads(attrs["evExtraParameters"]).items():
                attrs[f"evExtra.{key}"] = value
        except (ValueError, AttributeError):
            pass
    return attrs


def _soh(attrs: dict[str, Any]) -> tuple[float | None, str | None]:
    for alias in SOH_ALIASES:
        value = attrs.get(alias)
        if value in (None, ""):
            continue
        try:
            soh = round(float(value), 1)
        except (TypeError, ValueError):
            continue
        if 0 < soh <= 100:
            return soh, alias
    return None, None


def _is_camera(device: dict[str, Any], attrs: dict[str, Any]) -> bool:
    return attrs.get("deviceType") in CAMERA_DEVICE_TYPES or str(device.get("vehicleNumber", "")).upper().endswith("_CAM")


def _timestamp(epoch_ms) -> dt.datetime | None:
    return dt.datetime.fromtimestamp(epoch_ms / 1000, tz=dt.timezone.utc) if epoch_ms else None


def extract_rows(live_devices: list[dict[str, Any]], vehicles: list[tuple[str, int | None]]) -> pd.DataFrame:
    """One row per dim_vehicle plate found in the live feed: the chosen
    device's SoH and raw odometer. `vehicles` is (base_license_plate,
    fleetx_id) -- the fleetx_id device is the tie-breaker when no device
    reports a SoH."""
    plates = {plate_key(p): (p, fid) for p, fid in vehicles}
    candidates: dict[str, list[dict[str, Any]]] = {}
    for device in live_devices:
        key = plate_key(str(device.get("vehicleNumber", "")))
        attrs = _attributes(device)
        if key not in plates or _is_camera(device, attrs):
            continue
        soh, soh_field = _soh(attrs)
        odometer = device.get("totalOdometer")
        candidates.setdefault(key, []).append(
            {
                "fleetx_device": device.get("vehicleNumber"),
                "fleetx_vehicle_id": device.get("vehicleId"),
                "soh_pct": soh,
                "soh_field": soh_field,
                "odometer_raw_km": float(odometer) if odometer is not None else None,
                "reported_at": _timestamp(device.get("lastUpdatedAt")),
            }
        )

    epoch = dt.datetime.min.replace(tzinfo=dt.timezone.utc)
    rows = []
    for key, devices in candidates.items():
        plate, fleetx_id = plates[key]
        best = max(
            devices,
            key=lambda d: (d["soh_pct"] is not None, d["fleetx_vehicle_id"] == fleetx_id, d["reported_at"] or epoch),
        )
        reported_at = best.pop("reported_at")
        rows.append(
            {
                "base_license_plate": plate,
                **best,
                "soh_reported_at": reported_at if best["soh_pct"] is not None else None,
                "odometer_reported_at": reported_at,
            }
        )
    return pd.DataFrame(rows)


def _odometer_problem(raw: float | None, stored: float | None) -> str | None:
    if raw is None or pd.isna(raw) or raw <= 0:
        return "no reading"
    if raw >= GARBAGE_ABS_THRESHOLD_KM:
        return f"device fault: {raw:,.0f} km is past the {GARBAGE_ABS_THRESHOLD_KM:,.0f} km sanity limit"
    if stored is not None and not pd.isna(stored) and raw < stored * (1 - BACKWARDS_TOLERANCE):
        return f"went backwards: {raw:,.0f} km is below the stored {stored:,.0f} km"
    return None


def _none_if_na(value):
    return None if value is None or (not isinstance(value, str) and pd.isna(value)) else value


@dataclass
class CombineReport:
    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    not_pinged: list[str] = field(default_factory=list)
    no_soh: list[str] = field(default_factory=list)
    odometer_rejected: dict[str, str] = field(default_factory=dict)


def combine(existing: pd.DataFrame, fresh: pd.DataFrame, now: dt.datetime) -> tuple[pd.DataFrame, CombineReport]:
    """The table's new full contents: `existing` upserted with `fresh`
    (extract_rows() output) under the rules in the module docstring."""
    report = CombineReport()
    stored = {r["base_license_plate"]: r for r in existing.to_dict("records")} if not existing.empty else {}
    out = {plate: {c: _none_if_na(row.get(c)) for c in _COLUMNS} for plate, row in stored.items()}

    for new in fresh.to_dict("records"):
        plate = new["base_license_plate"]
        old = out.get(plate)
        row = dict(old) if old else {c: None for c in _COLUMNS}
        row.update(
            base_license_plate=plate,
            fleetx_device=new["fleetx_device"],
            fleetx_vehicle_id=_none_if_na(new["fleetx_vehicle_id"]),
            odometer_raw_km=_none_if_na(new["odometer_raw_km"]),
            first_seen_at=row["first_seen_at"] or now,
            last_pinged_at=now,
        )
        if _none_if_na(new["soh_pct"]) is not None:
            row.update({c: new[c] for c in _SOH_COLUMNS})
        elif row["soh_pct"] is None:
            report.no_soh.append(plate)

        problem = _odometer_problem(_none_if_na(new["odometer_raw_km"]), row["odometer_km"])
        if problem is None:
            row.update(odometer_km=new["odometer_raw_km"], odometer_reported_at=new["odometer_reported_at"], odometer_flag=None)
        else:
            row["odometer_flag"] = problem
            report.odometer_rejected[plate] = problem

        (report.updated if old else report.added).append(plate)
        out[plate] = row

    seen = set(fresh["base_license_plate"]) if not fresh.empty else set()
    report.not_pinged = sorted(p for p in stored if p not in seen)
    report.added.sort(), report.updated.sort(), report.no_soh.sort()
    df = pd.DataFrame(list(out.values()), columns=_COLUMNS).sort_values("base_license_plate", ignore_index=True)
    return df, report


def run(settings: Settings, dry_run: bool = False) -> CombineReport:
    client = bq_client.get_client(settings)
    bq_client.ensure_soh_latest_table(client, settings)
    vehicles = bq_client.get_active_vehicles(client, settings)
    live = fleetx_client.get_live(fleetx_client.login())
    fresh = extract_rows(live, vehicles)
    logger.info("Live feed: %d device record(s), %d matched vehicle(s).", len(live), len(fresh))

    existing = bq_client.get_table_rows(client, settings.soh_latest_table_ref)
    table, report = combine(existing, fresh, dt.datetime.now(dt.timezone.utc))
    if not dry_run:
        bq_client.load_soh_latest_rows(client, settings, table)
    return report
