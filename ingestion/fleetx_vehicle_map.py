"""Resolves each plate's Fleetx vehicleId from Vehicle_Update_uploader.xlsx
(Fleetx's own vehicle admin export), for calling the Fleetx API. Also
discovers plates present in that export but not yet in any known roster
(new vehicles to onboard).

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
    return Resolution(None, f"ambiguous: {len(candidates)} non-DashCam candidates")


def _load(path: Path) -> pd.DataFrame:
    df = pd.read_excel(path, sheet_name=SHEET_NAME)
    df.columns = [str(c).strip() for c in df.columns]
    df = df[_ROW_COLUMNS].copy()
    df["base_plate"] = df["number"].apply(_base_plate)
    return df


def resolve_fleetx_ids(path: Path, plates: list[str]) -> dict[str, Resolution]:
    """Returns one Resolution per requested plate ('not in uploader file' for
    plates absent from the sheet entirely)."""
    df = _load(path)
    results: dict[str, Resolution] = {}
    for plate in plates:
        rows = df[df["base_plate"] == plate]
        if rows.empty:
            results[plate] = Resolution(None, "not in uploader file")
        else:
            results[plate] = _resolve_group(rows)
    return results


def discover_new_vehicles(path: Path, known_plates: set[str]) -> dict[str, Resolution]:
    """Finds plates in the uploader file that aren't in `known_plates`,
    excluding anything that doesn't look like a real registration plate
    (chassis numbers, VINs, PO-tracking ids -- see _PLATE_RE) and anything
    tagged/grouped 'TEST'. Callers should still check `tags` on the result:
    a handful of otherwise-valid new plates have no tags filled in on the
    Fleetx side and shouldn't be auto-assigned a customer."""
    df = _load(path)
    df = df[
        (df["group"] != "TEST")
        & (df["tags"].astype(str).str.upper() != "TEST")
        & df["base_plate"].apply(looks_like_plate)
        & ~df["base_plate"].isin(known_plates)
    ]

    results: dict[str, Resolution] = {}
    for plate, rows in df.groupby("base_plate"):
        results[plate] = _resolve_group(rows)
    return results
