"""Pure planning logic for syncing dim_vehicle / dim_customer -- no BigQuery.

seed_dimensions.py gathers the inputs (Fleetx vehicle export, current table
contents, vehicle master sheet, telemetry-derived values), calls
plan_vehicles()/plan_customers() here to work out the complete target state
plus a diff against what's live, shows render_plan() to the operator, and only
writes after confirmation.

Rules this module enforces:
- A vehicle's customer comes from its Fleetx customer tag, mapped through
  CUSTOMER_TAGS. Nothing is inferred from plate numbers.
- Nothing is ever deleted. A vehicle absent from the export is planned as
  is_active = False (a *deactivation*, flagged for confirmation) and keeps
  every other field.
- Hand-entered data wins: vehicle master sheet > value already in the table >
  derived value (starting_odometer: master sheet > derived > table, since it's
  recomputed from telemetry). A filled-in value is never replaced by a blank.
- Anything ambiguous (untagged, unknown tag, conflicting tags) is reported as
  an issue and resolved conservatively -- an existing vehicle keeps its
  current customer, a new one is skipped -- never guessed.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field

import pandas as pd

from ingestion.fleetx_vehicle_map import ExportVehicle
from ingestion.vehicle_master import clean_oem

# Fleetx customer tag (matched case-insensitively, whitespace-trimmed) ->
# canonical customer_name. The customer_name values must match
# backend/metrics.py's _CUSTOMER_REGISTRY. A tag not listed here (e.g.
# FLYTTA) is reported, never turned into a customer automatically -- add it
# here, and to the backend registry, to onboard that customer.
CUSTOMER_TAGS = {
    "FRESHBUS": "FreshBus",
    "ZINGBUS": "ZingBus",
    "BILLION ELECTRIC MOBILITY": "BillionE",
    "BILLIONE": "BillionE",
    "AVG LOGISTICS": "AVG LOGISTICS",
    "SWITCHLABS": "SWITCHLABS",
}

# Default dim_customer attributes. Only used to fill a customer that's new or
# has a blank field -- a value already in dim_customer (possibly edited
# directly in BigQuery) is never overwritten.
CUSTOMER_DEFAULTS = {
    "FreshBus": {"oem": "Azad (Bus)", "routes_description": "Guntur - Hyderabad, Guntur - Vizag"},
    "ZingBus": {"oem": "JBM / Azad (Bus)", "routes_description": "Delhi - Dehradun, Delhi - Amritsar"},
    "BillionE": {"oem": "TATA (Truck)", "routes_description": "Rajasthan - Surat"},
    "AVG LOGISTICS": {"oem": "IPL", "routes_description": None},
    "SWITCHLABS": {"oem": "TATA", "routes_description": None},
}

VEHICLE_COLUMNS = [
    "base_license_plate",
    "customer_name",
    "oem",
    "vehicle_type",
    "vehicle_model",
    "device_installation_date",
    "fleetx_id",
    "starting_odometer",
    "is_active",
    "first_seen_at",
    "deactivated_at",
]
CUSTOMER_COLUMNS = ["customer_name", "oem", "routes_description"]

# Bookkeeping fields: set on every planned row but not shown as a per-field
# change -- they follow from the add/deactivate/reactivate category itself.
_LIFECYCLE_FIELDS = {"is_active", "first_seen_at", "deactivated_at"}


def map_tag(tag: str) -> str | None:
    return CUSTOMER_TAGS.get(tag.strip().upper())


def _missing(val) -> bool:
    if val is None:
        return True
    if isinstance(val, float) and math.isnan(val):
        return True
    try:
        return bool(pd.isna(val))
    except (TypeError, ValueError):
        return False


def _norm(val):
    """Comparable, BigQuery-loadable plain Python value: NaN/NaT/None -> None,
    numpy scalars -> Python, whole floats that are really ids -> int."""
    if _missing(val):
        return None
    if isinstance(val, pd.Timestamp):
        return val.to_pydatetime()
    if hasattr(val, "item"):  # numpy scalar
        val = val.item()
    return val


def _first(*vals):
    for v in vals:
        v = _norm(v)
        if v is not None and v != "":
            return v
    return None


def _same(a, b) -> bool:
    a, b = _norm(a), _norm(b)
    if isinstance(a, float) and isinstance(b, float):
        return math.isclose(a, b, rel_tol=0, abs_tol=1e-6)
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return float(a) == float(b)
    if isinstance(a, dt.datetime) and isinstance(b, dt.date) and not isinstance(b, dt.datetime):
        return a.date() == b
    if isinstance(b, dt.datetime) and isinstance(a, dt.date) and not isinstance(a, dt.datetime):
        return b.date() == a
    return a == b


@dataclass
class VehicleInputs:
    current: pd.DataFrame
    export: dict[str, ExportVehicle]
    master: dict[str, dict] = field(default_factory=dict)
    telemetry_type_model: dict[str, dict] = field(default_factory=dict)
    starting_odometer: dict[str, float | None] = field(default_factory=dict)
    fleetx_id_overrides: dict[str, int] = field(default_factory=dict)


@dataclass
class Plan:
    rows: pd.DataFrame
    added: list[str] = field(default_factory=list)
    reactivated: list[str] = field(default_factory=list)
    deactivated: list[str] = field(default_factory=list)
    # plate -> [(field, old, new), ...]
    changed: dict[str, list[tuple[str, object, object]]] = field(default_factory=dict)
    # Existing rows that only need lifecycle columns filled in (first run
    # after those columns were added) -- a write, but not a data change.
    backfilled: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return bool(self.added or self.reactivated or self.deactivated or self.changed or self.backfilled)

    @property
    def needs_confirmation(self) -> bool:
        return self.has_changes


def _current_by_key(current: pd.DataFrame, key: str) -> dict[str, dict]:
    if current is None or current.empty:
        return {}
    return {str(r[key]): r for r in current.to_dict("records")}


def plan_vehicles(inputs: VehicleInputs, now: dt.datetime) -> Plan:
    current = _current_by_key(inputs.current, "base_license_plate")
    issues: list[str] = []
    planned: dict[str, dict] = {}

    # Resolve each export vehicle's customer from its tag(s).
    export_customer: dict[str, str | None] = {}
    for plate, vehicle in sorted(inputs.export.items()):
        mapped = {map_tag(t) for t in vehicle.tags} - {None}
        unknown = sorted(t for t in vehicle.tags if map_tag(t) is None)
        existing = current.get(plate)
        if len(mapped) == 1:
            export_customer[plate] = mapped.pop()
            continue
        if len(mapped) > 1:
            reason = f"conflicting customer tags {sorted(vehicle.tags)}"
        elif unknown:
            reason = f"tag(s) {unknown} not in CUSTOMER_TAGS"
        else:
            reason = "no customer tag on any of its devices"
        if existing is not None:
            issues.append(f"{plate}: {reason} -- keeping current customer '{existing['customer_name']}'.")
            export_customer[plate] = str(existing["customer_name"])
        else:
            issues.append(f"{plate}: {reason} -- not added.")
            export_customer[plate] = None

    for plate in sorted(set(current) | {p for p, c in export_customer.items() if c}):
        existing = current.get(plate) or {}
        vehicle = inputs.export.get(plate)
        in_export = vehicle is not None and export_customer.get(plate) is not None
        master = inputs.master.get(plate) or {}
        derived = inputs.telemetry_type_model.get(plate) or {}
        customer = export_customer.get(plate) if in_export else existing.get("customer_name")

        if master.get("customer_name") and master["customer_name"] != customer:
            issues.append(
                f"{plate}: vehicle master sheet says customer '{master['customer_name']}' but the "
                f"Fleetx tag says '{customer}' -- using the tag. Fix one of them."
            )

        export_id = vehicle.fleetx_id if vehicle is not None else None
        if vehicle is not None and export_id is None and _norm(existing.get("fleetx_id")) is not None:
            issues.append(
                f"{plate}: export has no usable primary device ({vehicle.fleetx_reason}) -- "
                f"keeping current fleetx_id {_norm(existing.get('fleetx_id'))}."
            )
        fleetx_id = _first(inputs.fleetx_id_overrides.get(plate), export_id, existing.get("fleetx_id"))
        fleetx_id = int(fleetx_id) if fleetx_id is not None else None

        was_active = _norm(existing.get("is_active")) is not False if existing else None
        row = {
            "base_license_plate": plate,
            "customer_name": customer,
            "oem": _first(
                master.get("oem"),
                existing.get("oem"),
                clean_oem(vehicle.vehicle_maker) if vehicle else None,
                clean_oem((CUSTOMER_DEFAULTS.get(customer) or {}).get("oem")),
            ),
            "vehicle_type": _first(
                master.get("vehicle_type"),
                existing.get("vehicle_type"),
                derived.get("vehicle_type"),
                vehicle.vehicle_type if vehicle else None,
            ),
            "vehicle_model": _first(
                master.get("vehicle_model"),
                existing.get("vehicle_model"),
                derived.get("vehicle_model"),
                vehicle.vehicle_model if vehicle else None,
            ),
            "device_installation_date": _first(
                master.get("device_installation_date"), existing.get("device_installation_date")
            ),
            "fleetx_id": fleetx_id,
            "starting_odometer": _first(
                master.get("starting_odometer"),
                inputs.starting_odometer.get(plate),
                existing.get("starting_odometer"),
            ),
            "is_active": in_export,
            "first_seen_at": _first(existing.get("first_seen_at"), now),
            "deactivated_at": None if in_export else _first(
                existing.get("deactivated_at") if was_active is False else None, now
            ),
        }
        if isinstance(row["device_installation_date"], dt.datetime):
            row["device_installation_date"] = row["device_installation_date"].date()
        planned[plate] = row

    plan = Plan(rows=pd.DataFrame([planned[p] for p in sorted(planned)], columns=VEHICLE_COLUMNS))
    plan.issues = issues
    for plate, row in planned.items():
        existing = current.get(plate)
        if existing is None:
            plan.added.append(plate)
            continue
        was_active = _norm(existing.get("is_active")) is not False
        if was_active and not row["is_active"]:
            plan.deactivated.append(plate)
        elif not was_active and row["is_active"]:
            plan.reactivated.append(plate)
        diffs = [
            (col, _norm(existing.get(col)), row[col])
            for col in VEHICLE_COLUMNS
            if col not in _LIFECYCLE_FIELDS and col != "base_license_plate" and not _same(existing.get(col), row[col])
        ]
        if diffs:
            plan.changed[plate] = diffs
        elif (
            plate not in plan.deactivated
            and plate not in plan.reactivated
            and any(not _same(existing.get(c), row[c]) for c in ("is_active", "first_seen_at", "deactivated_at"))
        ):
            plan.backfilled.append(plate)
    for lst in (plan.added, plan.reactivated, plan.deactivated, plan.backfilled):
        lst.sort()
    return plan


def plan_customers(current: pd.DataFrame, customer_names: set[str]) -> Plan:
    """Upsert-only: every customer already in dim_customer is kept as is
    (never deleted, never overwritten); CUSTOMER_DEFAULTS only fills in a
    customer that's new or a field that's blank."""
    existing = _current_by_key(current, "customer_name")
    names = sorted(set(existing) | customer_names)
    plan = Plan(rows=pd.DataFrame(columns=CUSTOMER_COLUMNS))
    rows = []
    for name in names:
        cur = existing.get(name) or {}
        defaults = CUSTOMER_DEFAULTS.get(name) or {}
        row = {
            "customer_name": name,
            "oem": _first(cur.get("oem"), defaults.get("oem")),
            "routes_description": _first(cur.get("routes_description"), defaults.get("routes_description")),
        }
        rows.append(row)
        if not cur:
            plan.added.append(name)
            continue
        diffs = [(c, _norm(cur.get(c)), row[c]) for c in ("oem", "routes_description") if not _same(cur.get(c), row[c])]
        if diffs:
            plan.changed[name] = diffs
    plan.rows = pd.DataFrame(rows, columns=CUSTOMER_COLUMNS)
    return plan


def _fmt(val) -> str:
    val = _norm(val)
    if val is None:
        return "(blank)"
    if isinstance(val, float) and val.is_integer() and abs(val) >= 1e5:
        return str(int(val))  # an id (fleetx_id) upcast to float by a NULL in its column
    if isinstance(val, float):
        return f"{val:,.2f}"
    return str(val)


def render_plan(vehicles: Plan, customers: Plan, summary: dict) -> str:
    rows = {r["base_license_plate"]: r for r in vehicles.rows.to_dict("records")}
    out = [
        "=" * 72,
        "dim_vehicle / dim_customer sync -- preview",
        "=" * 72,
        f"Vehicle export: {summary.get('export_file')}",
        f"  {summary.get('export_vehicles', 0)} plated vehicle(s); "
        f"{summary.get('export_skipped_non_plate', 0)} device row(s) without a real plate ignored",
        f"dim_vehicle now: {summary.get('current_total', 0)} vehicle(s) "
        f"({summary.get('current_active', 0)} active)",
        "",
    ]

    if vehicles.deactivated:
        out.append(f"!! DEACTIVATE {len(vehicles.deactivated)} vehicle(s) -- missing from the vehicle export.")
        out.append("   Row and history are kept; the daily Fleetx pull stops for these.")
        out.append("   If the export is incomplete or wrong, answer NO and fix the export.")
        for p in vehicles.deactivated:
            r = rows[p]
            out.append(f"   - {p:<12} {r['customer_name']:<14} fleetx_id={_fmt(r['fleetx_id'])}")
        out.append("")
    if vehicles.added:
        out.append(f"+  ADD {len(vehicles.added)} vehicle(s):")
        for p in vehicles.added:
            r = rows[p]
            out.append(f"   + {p:<12} {r['customer_name']:<14} fleetx_id={_fmt(r['fleetx_id'])}")
        out.append("")
    if vehicles.reactivated:
        out.append(f"^  REACTIVATE {len(vehicles.reactivated)} vehicle(s) -- back in the export:")
        for p in vehicles.reactivated:
            out.append(f"   ^ {p:<12} {rows[p]['customer_name']}")
        out.append("")
    if vehicles.changed:
        out.append(f"~  CHANGE {len(vehicles.changed)} vehicle(s):")
        for p in sorted(vehicles.changed):
            for col, old, new in vehicles.changed[p]:
                out.append(f"   ~ {p:<12} {col}: {_fmt(old)} -> {_fmt(new)}")
        out.append("")
    if vehicles.backfilled:
        out.append(
            f"   Lifecycle columns (is_active/first_seen_at) filled in for "
            f"{len(vehicles.backfilled)} existing vehicle(s) -- no data change."
        )
        out.append("")
    if customers.added or customers.changed:
        out.append("Customers:")
        for name in customers.added:
            out.append(f"   + {name}")
        for name, diffs in sorted(customers.changed.items()):
            for col, old, new in diffs:
                out.append(f"   ~ {name}: {col}: {_fmt(old)} -> {_fmt(new)}")
        out.append("")
    if vehicles.issues:
        out.append(f"Warnings ({len(vehicles.issues)}) -- nothing guessed for these:")
        out.extend(f"   * {i}" for i in vehicles.issues)
        out.append("")
    if not vehicles.has_changes and not customers.has_changes:
        out.append("No changes -- dim_vehicle and dim_customer already match the export.")
    return "\n".join(out)
