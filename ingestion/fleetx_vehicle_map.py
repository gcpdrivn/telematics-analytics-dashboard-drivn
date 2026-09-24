"""Reads Vehicle_Update_uploader.xlsx (Fleetx's own vehicle admin export) into
one record per plated vehicle: its customer tag(s) and the Fleetx vehicleId
of its primary device, for calling the Fleetx API. seed_dimensions.py syncs
dim_vehicle from these records.

Each physical vehicle can have multiple registered devices (OBD, DashCam,
a separate API/AIS140 tracker), each with its own vehicleId. DashCam devices
report unreliable utilization data -- the existing Excel pipeline already
excludes them (transform.py's `Group Name != 'DashCam'` filter) -- so this
picks the same kind of device: the one tagged 'OBD', falling back to any
non-DashCam device if 'OBD' isn't present, in order of decreasing confidence.
Plates where that's still ambiguous are left unresolved rather than guessed.

Note: Fleetx's own Realtime Analytics 'merged' vehicleId (mergeDevices=true)
is NOT a substitute for this -- in testing it picked the DashCam device as
canonical for vehicles with a clean OBD+DashCam pair (e.g. AP39WN7281 merged
to its _CAM device's id, not its OBD device's id).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

SHEET_NAME = "Vehicle-Upload-report-Sheet"

_STRIP_KEYWORDS = ("OBD", "API", "CAM")
_ROW_COLUMNS = ["id", "number", "group", "tags", "vehicleMaker", "vehicleModel", "vehicleType"]

# Real Indian registration plates: 2 state letters, 1-2 RTO digits, 1-3
# series letters, 3-4 number digits (e.g. MH02GS4222, HR55BE0128,
# DL1PD8664). Deliberately excludes chassis numbers (CHASSIS6626), VINs
# (LVBS6P5B2TT503481, MAT883001T3A01733), PO-tracking ids
# (PO-DTPLPO202627007-1) and bare short codes (6903, CH6634) -- vehicles
# that aren't plated yet and have no base_license_plate to key on.
_PLATE_RE = re.compile(r"^[A-Z]{2}\d{1,2}[A-Z]{1,3}\d{3,4}$")

# Fleetx-side placeholder value seen in vehicleMaker for incompletely filled
# in vehicle records -- not a real OEM, never trust it.
_JUNK_MAKER = "Market"


@dataclass(frozen=True)
class Resolution:
    fleetx_id: int | None
    reason: str
    tags: str | None = None
    vehicle_maker: str | None = None
    vehicle_model: str | None = None
    vehicle_type: str | None = None


def looks_like_plate(base_plate: str) -> bool:
    return bool(_PLATE_RE.match(base_plate))


def _base_plate(number: str) -> str:
    text = str(number).split("_")[0]
    for kw in _STRIP_KEYWORDS:
        text = text.replace(kw, "")
    return text.strip()


def _clean(val) -> str | None:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    s = str(val).strip()
    return s or None


def _row_to_resolution(row: pd.Series, reason: str) -> Resolution:
    maker = _clean(row.get("vehicleMaker"))
    model = _clean(row.get("vehicleModel"))
    if maker == _JUNK_MAKER:
        # Vehicle record isn't fully filled in on the Fleetx side --
        # maker/model are placeholder junk, not real values.
        maker = None
        model = None
    return Resolution(
        fleetx_id=int(row["id"]),
        reason=reason,
        tags=_clean(row.get("tags")),
        vehicle_maker=maker,
        vehicle_model=model,
        vehicle_type=_clean(row.get("vehicleType")),
    )


def _is_api_device(rows: pd.DataFrame) -> pd.Series:
    return rows["number"].astype(str).str.upper().str.endswith("_API") | (rows["group"] == "API")


def _resolve_group(rows: pd.DataFrame) -> Resolution:
    obd_group = rows[rows["group"] == "OBD"]
    if len(obd_group) == 1:
        return _row_to_resolution(obd_group.iloc[0], "group==OBD")

    is_cam = rows["number"].astype(str).str.contains("CAM") | (rows["group"] == "DashCam")
    candidates = rows[~is_cam]
    if candidates.empty:
        return Resolution(None, "no non-DashCam candidate device")

    has_obd_hint = candidates["number"].astype(str).str.contains("OBD")
    obd_hint = candidates[has_obd_hint]
    if len(obd_hint) == 1:
        return _row_to_resolution(obd_hint.iloc[0], "OBD-in-name tiebreak")
    if len(candidates) == 1:
        return _row_to_resolution(candidates.iloc[0], "single non-DashCam candidate")
    # A "<plate>_API" device alongside the vehicle's own tracker is an OEM
    # push feed, not a trip source: the TATA OEM devices added for all AVG
    # trucks in Sep 2026 each reported exactly one snapshot trip over a week
    # (a single odometer reading, then 0), while the original trackers carry
    # the real trips. Prefer the non-API device when that leaves exactly one.
    # (A vehicle whose API device *is* its live source, like DL1PD9284, is
    # handled by seed_dimensions.FLEETX_ID_MANUAL_OVERRIDES.)
    non_api = candidates[~_is_api_device(candidates)]
    if len(non_api) == 1:
        return _row_to_resolution(non_api.iloc[0], "non-API tiebreak")
    return Resolution(None, f"ambiguous: {len(candidates)} non-DashCam candidates")


class ExportFileError(ValueError):
    """The uploader file is missing, unreadable, or not shaped like a Fleetx
    vehicle export -- seed_dimensions.py aborts on this rather than syncing
    dim_vehicle against a bad file."""


def _load(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise ExportFileError(f"Vehicle export '{path}' not found.")
    try:
        df = pd.read_excel(path, sheet_name=SHEET_NAME)
    except ValueError as exc:  # missing sheet, unreadable workbook
        raise ExportFileError(f"Could not read sheet '{SHEET_NAME}' from '{path.name}': {exc}") from exc
    df.columns = [str(c).strip() for c in df.columns]
    missing = [c for c in _ROW_COLUMNS if c not in df.columns]
    if missing:
        raise ExportFileError(f"'{path.name}' is missing expected columns: {missing}")
    df = df[_ROW_COLUMNS].copy()
    if df.empty:
        raise ExportFileError(f"'{path.name}' has no vehicle rows.")
    if df["id"].isna().any():
        raise ExportFileError(f"'{path.name}' has {int(df['id'].isna().sum())} row(s) with a blank id.")
    df["base_plate"] = df["number"].apply(_base_plate)
    return df


@dataclass(frozen=True)
class ExportVehicle:
    """One plated vehicle from the export, with every device registered to
    it folded together. `tags` is the union across all its devices: the
    customer tag is often only on one of them (e.g. DL1PD8677's tag sits on
    its _CAM device, not the OBD one we pull data from)."""

    plate: str
    tags: frozenset[str]
    fleetx_id: int | None
    fleetx_reason: str
    vehicle_maker: str | None
    vehicle_model: str | None
    vehicle_type: str | None


def _is_test(rows: pd.DataFrame) -> pd.Series:
    return (rows["group"] == "TEST") | (rows["tags"].astype(str).str.strip().str.upper() == "TEST")


def read_export_vehicles(path: Path) -> tuple[dict[str, ExportVehicle], int]:
    """Every real-plate vehicle in the export, keyed by base plate, plus the
    number of device rows skipped for not having a real plate (chassis
    numbers, VINs, PO-tracking ids -- see _PLATE_RE). TEST devices are
    dropped entirely. Raises ExportFileError on a malformed file."""
    df = _load(path)
    df = df[~_is_test(df)]
    is_plate = df["base_plate"].apply(looks_like_plate)
    skipped_non_plate = int((~is_plate).sum())

    vehicles: dict[str, ExportVehicle] = {}
    for plate, rows in df[is_plate].groupby("base_plate"):
        resolution = _resolve_group(rows)
        tags = frozenset(t for t in (_clean(v) for v in rows["tags"]) if t)
        vehicles[plate] = ExportVehicle(
            plate=plate,
            tags=tags,
            fleetx_id=resolution.fleetx_id,
            fleetx_reason=resolution.reason,
            vehicle_maker=resolution.vehicle_maker,
            vehicle_model=resolution.vehicle_model,
            vehicle_type=resolution.vehicle_type,
        )
    return vehicles, skipped_non_plate


def non_dashcam_candidate_ids(path: Path, plate: str) -> list[int]:
    """All non-DashCam device ids registered for this plate, in the same
    preference order read_export_vehicles() uses (OBD-labeled first, then
    OBD-in-name, then non-API devices, then everything else), for use as live fallbacks when the
    resolved primary id turns out to be dead -- e.g. a stale 'OBD' label on
    hardware that's since failed (found for DL1PD9284: its labeled OBD
    device returns zero trips, while its DashCam and API devices are both
    active). DashCam is never included, even as a last resort -- the Excel
    pipeline already treats DashCam telemetry as unreliable (transform.py's
    `Group Name != 'DashCam'` filter)."""
    df = _load(path)
    rows = df[df["base_plate"] == plate]
    if rows.empty:
        return []

    is_cam = rows["number"].astype(str).str.contains("CAM") | (rows["group"] == "DashCam")
    candidates = rows[~is_cam]

    ordered_ids: list[int] = []
    for subset in (
        candidates[candidates["group"] == "OBD"],
        candidates[candidates["number"].astype(str).str.contains("OBD")],
        candidates[~_is_api_device(candidates)],
        candidates,
    ):
        for fleetx_id in subset["id"].astype(int):
            if fleetx_id not in ordered_ids:
                ordered_ids.append(fleetx_id)
    return ordered_ids
