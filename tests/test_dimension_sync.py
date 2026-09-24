"""Tests for the dim_vehicle / dim_customer sync -- no BigQuery needed.

Covers the planning rules (ingestion/dimension_sync.py), export validation
(fleetx_vehicle_map.read_export_vehicles) and the confirm/backup/merge flow
of seed_dimensions.run() against a recording fake of bq_client.
"""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pandas as pd
import pytest

from ingestion import dimension_sync, fleetx_vehicle_map, seed_dimensions
from ingestion.dimension_sync import VehicleInputs, plan_customers, plan_vehicles
from ingestion.fleetx_vehicle_map import ExportFileError, ExportVehicle, read_export_vehicles

NOW = dt.datetime(2026, 9, 24, 10, 0, tzinfo=dt.timezone.utc)
EARLIER = dt.datetime(2026, 9, 1, 10, 0, tzinfo=dt.timezone.utc)


def _ev(plate, *tags, fleetx_id=100, maker="TATA", model="M1", vtype="Truck", reason="group==OBD"):
    return ExportVehicle(
        plate=plate,
        tags=frozenset(tags),
        fleetx_id=fleetx_id,
        fleetx_reason=reason,
        vehicle_maker=maker,
        vehicle_model=model,
        vehicle_type=vtype,
    )


def _cur(plate, customer, **extra):
    row = {
        "base_license_plate": plate,
        "customer_name": customer,
        "oem": "TATA",
        "vehicle_type": "Truck",
        "vehicle_model": "M1",
        "device_installation_date": None,
        "fleetx_id": 100,
        "starting_odometer": 10.0,
        "is_active": True,
        "first_seen_at": EARLIER,
        "deactivated_at": None,
    }
    row.update(extra)
    return row


def _plan(current_rows, export, **kwargs):
    current = pd.DataFrame(current_rows) if current_rows else pd.DataFrame()
    return plan_vehicles(VehicleInputs(current=current, export=export, **kwargs), NOW)


def _row(plan, plate):
    match = plan.rows[plan.rows["base_license_plate"] == plate]
    assert len(match) == 1
    return match.iloc[0]


# --- customer from tag ----------------------------------------------------


def test_customer_comes_from_tag_case_insensitively():
    plan = _plan([], {"MH02GS5194": _ev("MH02GS5194", " switchlabs ")})
    assert plan.added == ["MH02GS5194"]
    assert _row(plan, "MH02GS5194")["customer_name"] == "SWITCHLABS"


def test_plate_prefix_is_ignored_tag_decides():
    """MH02GS5194 used to be swept into BillionE by its MH02 prefix."""
    plan = _plan([_cur("MH02GS5194", "BillionE")], {"MH02GS5194": _ev("MH02GS5194", "SWITCHLABS")})
    assert plan.changed["MH02GS5194"] == [("customer_name", "BillionE", "SWITCHLABS")]


def test_untagged_new_plate_is_skipped_with_warning():
    plan = _plan([], {"HR55BE7776": _ev("HR55BE7776")})
    assert plan.added == [] and plan.rows.empty
    assert any("HR55BE7776" in i and "no customer tag" in i for i in plan.issues)


def test_unknown_tag_is_not_turned_into_a_customer():
    plan = _plan([], {"CHASSIS6626": _ev("CHASSIS6626", "FLYTTA")})
    assert plan.rows.empty
    assert any("FLYTTA" in i for i in plan.issues)


def test_existing_vehicle_that_lost_its_tag_keeps_customer_and_stays_active():
    plan = _plan([_cur("DL1PD8523", "ZingBus")], {"DL1PD8523": _ev("DL1PD8523")})
    row = _row(plan, "DL1PD8523")
    assert row["customer_name"] == "ZingBus" and row["is_active"]
    assert not plan.has_changes
    assert any("DL1PD8523" in i for i in plan.issues)


def test_conflicting_tags_never_guess():
    new = _plan([], {"AB12CD1234": _ev("AB12CD1234", "FRESHBUS", "Zingbus")})
    assert new.rows.empty and new.issues
    existing = _plan(
        [_cur("AB12CD1234", "FreshBus")], {"AB12CD1234": _ev("AB12CD1234", "FRESHBUS", "Zingbus")}
    )
    assert _row(existing, "AB12CD1234")["customer_name"] == "FreshBus"
    assert not existing.changed


# --- never hard delete ----------------------------------------------------


def test_vehicle_missing_from_export_is_deactivated_not_deleted():
    plan = _plan(
        [_cur("A1", "BillionE"), _cur("MH02GS4222", "BillionE", device_installation_date=dt.date(2026, 7, 1))],
        {"A1": _ev("A1", "BILLION ELECTRIC MOBILITY")},
    )
    assert plan.deactivated == ["MH02GS4222"]
    row = _row(plan, "MH02GS4222")
    assert not row["is_active"]
    assert row["deactivated_at"] == NOW
    assert row["customer_name"] == "BillionE"
    assert row["device_installation_date"] == dt.date(2026, 7, 1)
    assert plan.needs_confirmation


def test_already_inactive_vehicle_keeps_original_deactivation_time():
    plan = _plan([_cur("A1", "BillionE", is_active=False, deactivated_at=EARLIER)], {})
    assert plan.deactivated == [] and not plan.has_changes
    assert _row(plan, "A1")["deactivated_at"] == EARLIER


def test_vehicle_back_in_export_is_reactivated():
    plan = _plan(
        [_cur("A1", "BillionE", is_active=False, deactivated_at=EARLIER)],
        {"A1": _ev("A1", "BILLION ELECTRIC MOBILITY")},
    )
    assert plan.reactivated == ["A1"]
    row = _row(plan, "A1")
    assert row["is_active"] and row["deactivated_at"] is None


# --- protect hand-entered data --------------------------------------------


def test_master_sheet_beats_existing_beats_derived():
    plan = _plan(
        [_cur("A1", "BillionE", vehicle_model="Existing model", oem=None)],
        {"A1": _ev("A1", "BILLION ELECTRIC MOBILITY", maker="Export maker", model="Export model")},
        master={"A1": {"customer_name": "BillionE", "oem": "Master OEM", "vehicle_type": None,
                       "vehicle_model": None, "device_installation_date": dt.date(2026, 8, 5)}},
        telemetry_type_model={"A1": {"vehicle_type": "Bus", "vehicle_model": "Telemetry model"}},
    )
    row = _row(plan, "A1")
    assert row["oem"] == "Master OEM"
    assert row["vehicle_model"] == "Existing model"
    assert row["vehicle_type"] == "Truck"  # existing beats telemetry
    assert row["device_installation_date"] == dt.date(2026, 8, 5)


def test_filled_in_values_are_never_blanked():
    plan = _plan(
        [_cur("A1", "BillionE", fleetx_id=555, starting_odometer=1234.5,
              device_installation_date=dt.date(2026, 7, 1))],
        {"A1": _ev("A1", "BILLION ELECTRIC MOBILITY", fleetx_id=None, reason="ambiguous: 2")},
        starting_odometer={"A1": None},
    )
    row = _row(plan, "A1")
    assert row["fleetx_id"] == 555
    assert row["starting_odometer"] == 1234.5
    assert row["device_installation_date"] == dt.date(2026, 7, 1)
    assert not plan.changed
    assert any("keeping current fleetx_id 555" in i for i in plan.issues)


def test_master_starting_odometer_overrides_derived_value():
    plan = _plan(
        [_cur("DL1PD8677", "ZingBus", starting_odometer=0.05)],
        {"DL1PD8677": _ev("DL1PD8677", "Zingbus")},
        master={"DL1PD8677": {"customer_name": "ZingBus", "oem": None, "vehicle_type": None,
                              "vehicle_model": None, "device_installation_date": None,
                              "starting_odometer": 71539.4}},
        starting_odometer={"DL1PD8677": 0.05},
    )
    assert plan.changed["DL1PD8677"] == [("starting_odometer", 0.05, 71539.4)]


def test_new_vehicle_falls_back_to_export_and_customer_defaults():
    plan = _plan([], {"HR55BE9999": _ev("HR55BE9999", "AVG LOGISTICS", maker=None, model="RIHNO", vtype="Truck")})
    row = _row(plan, "HR55BE9999")
    assert row["oem"] == "IPL" and row["vehicle_model"] == "RIHNO" and row["vehicle_type"] == "Truck"
    assert row["first_seen_at"] == NOW


def test_fleetx_override_wins():
    plan = _plan(
        [], {"DL1PD9284": _ev("DL1PD9284", "Zingbus", fleetx_id=2494780)},
        fleetx_id_overrides={"DL1PD9284": 2543818},
    )
    assert _row(plan, "DL1PD9284")["fleetx_id"] == 2543818


def test_master_customer_disagreement_is_warned_not_applied():
    plan = _plan(
        [], {"A1": _ev("A1", "FRESHBUS")},
        master={"A1": {"customer_name": "ZingBus", "oem": None, "vehicle_type": None,
                       "vehicle_model": None, "device_installation_date": None}},
    )
    assert _row(plan, "A1")["customer_name"] == "FreshBus"
    assert any("master sheet says customer 'ZingBus'" in i for i in plan.issues)


# --- change detection -----------------------------------------------------


def test_rows_from_before_lifecycle_columns_are_backfilled_not_changed():
    row = _cur("A1", "BillionE")
    for col in ("is_active", "first_seen_at", "deactivated_at"):
        row.pop(col)
    plan = _plan([row], {"A1": _ev("A1", "BILLION ELECTRIC MOBILITY")})
    assert plan.backfilled == ["A1"] and not plan.changed
    assert _row(plan, "A1")["is_active"]
    assert plan.has_changes


def test_applying_a_plan_then_replanning_is_a_no_op():
    export = {
        "A1": _ev("A1", "BILLION ELECTRIC MOBILITY"),
        "B2": _ev("B2", "FRESHBUS", fleetx_id=7),
    }
    first = _plan([_cur("C3", "ZingBus")], export, starting_odometer={"A1": 5.0, "B2": 6.0})
    assert first.has_changes
    second = _plan(first.rows.to_dict("records"), export, starting_odometer={"A1": 5.0, "B2": 6.0})
    assert not second.has_changes


def test_float_ids_and_nan_from_bigquery_frames_compare_equal():
    plan = _plan(
        [_cur("A1", "BillionE", fleetx_id=100.0, starting_odometer=float("nan"), oem=float("nan"))],
        {"A1": _ev("A1", "BILLION ELECTRIC MOBILITY", maker=None)},
    )
    # Only the blank OEM gets filled in (from the customer default); the
    # 100.0-vs-100 id and NaN-vs-None odometer are not reported as changes.
    assert plan.changed == {"A1": [("oem", None, "TATA")]}


# --- customers ------------------------------------------------------------


def test_customers_are_upserted_never_overwritten_or_deleted():
    current = pd.DataFrame([
        {"customer_name": "FreshBus", "oem": "Edited in BQ", "routes_description": None},
        {"customer_name": "OldCustomer", "oem": "X", "routes_description": "Y"},
    ])
    plan = plan_customers(current, {"FreshBus", "SWITCHLABS"})
    rows = plan.rows.set_index("customer_name")
    assert rows.loc["FreshBus", "oem"] == "Edited in BQ"
    assert rows.loc["FreshBus", "routes_description"] == "Guntur - Hyderabad, Guntur - Vizag"
    assert "OldCustomer" in rows.index
    assert plan.added == ["SWITCHLABS"]
    assert list(plan.changed) == ["FreshBus"]


def test_render_plan_lists_deactivations_first_with_warning():
    plan = _plan([_cur("A1", "BillionE")], {"B2": _ev("B2", "FRESHBUS")})
    text = dimension_sync.render_plan(plan, plan_customers(pd.DataFrame(), set()), {})
    assert text.index("DEACTIVATE") < text.index("ADD")
    assert "A1" in text and "B2" in text
    assert "fleetx_id=100 " in text or text.rstrip().endswith("fleetx_id=100") or "fleetx_id=100\n" in text


def test_render_shows_ids_as_integers_even_when_column_is_float():
    plan = _plan([], {"B2": _ev("B2", "FRESHBUS", fleetx_id=3010294), "C3": _ev("C3", "FRESHBUS", fleetx_id=None)})
    text = dimension_sync.render_plan(plan, plan_customers(pd.DataFrame(), set()), {})
    assert "fleetx_id=3010294" in text and "3,010,294" not in text


# --- export validation ----------------------------------------------------


def _write_export(path, rows, sheet=fleetx_vehicle_map.SHEET_NAME, drop=()):
    cols = ["id", "number", "group", "tags", "vehicleMaker", "vehicleModel", "vehicleType"]
    df = pd.DataFrame(rows, columns=cols).drop(columns=list(drop))
    df.to_excel(path, sheet_name=sheet, index=False)
    return path


def test_export_tags_are_unioned_across_devices(tmp_path):
    path = _write_export(tmp_path / "x.xlsx", [
        [1, "DL1PD8677_CAM", None, "Zingbus", "AZAD", "A12", "Bus"],
        [2, "DL1PD8677", None, None, "AZAD", "A12", "Bus"],
        [3, "CHASSIS6626", None, "FLYTTA", "TATA", "", "Truck"],
        [4, "MH02GS0001", "TEST", "TEST", "TATA", "", "Truck"],
    ])
    vehicles, skipped = read_export_vehicles(path)
    assert set(vehicles) == {"DL1PD8677"}
    assert vehicles["DL1PD8677"].tags == frozenset({"Zingbus"})
    assert vehicles["DL1PD8677"].fleetx_id == 2  # non-CAM device
    assert skipped == 1


def test_export_prefers_own_tracker_over_oem_api_device(tmp_path):
    path = _write_export(tmp_path / "x.xlsx", [
        [10, "HR55BE0128", None, "AVG LOGISTICS", "IPL", "", "Truck"],
        [11, "HR55BE0128_API", None, None, "TATA", "", "Truck"],
        [20, "HR55BE2131_API", None, "AVG LOGISTICS", "TATA", "", "Truck"],
        [30, "AB12CD0001", None, "FRESHBUS", "", "", "Bus"],
        [31, "AB12CD0001", None, "FRESHBUS", "", "", "Bus"],
    ])
    vehicles, _ = read_export_vehicles(path)
    assert vehicles["HR55BE0128"].fleetx_id == 10
    assert vehicles["HR55BE0128"].tags == frozenset({"AVG LOGISTICS"})
    assert vehicles["HR55BE2131"].fleetx_id == 20  # API device is the only one
    assert vehicles["AB12CD0001"].fleetx_id is None  # genuinely ambiguous, not guessed


def test_export_missing_file_is_rejected(tmp_path):
    with pytest.raises(ExportFileError, match="not found"):
        read_export_vehicles(tmp_path / "nope.xlsx")


def test_export_missing_columns_is_rejected(tmp_path):
    path = _write_export(tmp_path / "x.xlsx", [[1, "A", None, "FRESHBUS", "", "", ""]], drop=("tags",))
    with pytest.raises(ExportFileError, match="missing expected columns"):
        read_export_vehicles(path)


def test_export_wrong_sheet_or_empty_is_rejected(tmp_path):
    with pytest.raises(ExportFileError, match="Could not read sheet"):
        read_export_vehicles(_write_export(tmp_path / "a.xlsx", [], sheet="Other"))
    with pytest.raises(ExportFileError, match="no vehicle rows"):
        read_export_vehicles(_write_export(tmp_path / "b.xlsx", []))


# --- run(): confirm / backup / merge --------------------------------------


class FakeBQ:
    def __init__(self, vehicles, customers):
        self.tables = {"proj.ds.dim_vehicle": vehicles, "proj.ds.dim_customer": customers}
        self.calls: list[tuple] = []

    def install(self, monkeypatch):
        bq = seed_dimensions.bq_client
        monkeypatch.setattr(bq, "ensure_schema", lambda c, s: self.calls.append(("ensure_schema",)))
        monkeypatch.setattr(bq, "get_table_rows", lambda c, ref: self.tables[ref].copy())
        monkeypatch.setattr(
            bq, "get_vehicle_type_model_rows",
            lambda c, s, plates: pd.DataFrame(columns=["base_license_plate", "vehicle_type", "vehicle_model"]),
        )
        monkeypatch.setattr(
            bq, "get_odometer_rows_by_plate",
            lambda c, s: pd.DataFrame(columns=["base_license_plate", "report_date", "opening_odometer", "closing_odometer"]),
        )
        monkeypatch.setattr(
            bq, "snapshot_table",
            lambda c, ref, stamp, days: self.calls.append(("snapshot", ref)) or f"{ref}_backup_{stamp}",
        )
        monkeypatch.setattr(
            bq, "merge_rows",
            lambda c, ref, df, schema, key: self.calls.append(("merge", ref, len(df))),
        )

    @property
    def writes(self):
        return [c for c in self.calls if c[0] in ("snapshot", "merge")]


@pytest.fixture
def env(tmp_path, monkeypatch):
    export = _write_export(tmp_path / "export.xlsx", [
        [1, "AP39WN7281", "OBD", "FRESHBUS", "AZAD", "A12", "Bus"],
        [2, "HR55BE9999", None, "AVG LOGISTICS", "Market", "", "Truck"],
    ])
    settings = SimpleNamespace(
        fleetx_vehicle_map_file=export,
        dim_vehicle_master_file=tmp_path / "missing_master.xlsx",
        dim_vehicle_table_ref="proj.ds.dim_vehicle",
        dim_customer_table_ref="proj.ds.dim_customer",
    )
    fake = FakeBQ(
        pd.DataFrame([_cur("AP39WN7281", "FreshBus", fleetx_id=1), _cur("MH02GS4222", "BillionE")]),
        pd.DataFrame([{"customer_name": "FreshBus", "oem": "Azad (Bus)", "routes_description": "r"}]),
    )
    fake.install(monkeypatch)
    fake.backfilled = []
    monkeypatch.setattr(
        seed_dimensions.vehicle_backfill, "backfill",
        lambda settings, plates, client=None: fake.backfilled.append(list(plates))
        or SimpleNamespace(summary=lambda: "backfill summary", rows_by_plate={p: 0 for p in plates}),
    )
    return settings, fake


def test_dry_run_writes_nothing(env):
    settings, fake = env
    assert seed_dimensions.run(settings, dry_run=True, client=object()) == "dry-run"
    assert fake.writes == [] and ("ensure_schema",) not in fake.calls


def test_declined_confirmation_writes_nothing(env):
    settings, fake = env
    with pytest.raises(seed_dimensions.SyncAborted, match="Not confirmed"):
        seed_dimensions.run(settings, interactive=True, prompt=lambda q: "no", client=object())
    assert fake.writes == []


def test_deactivation_prompt_names_the_count(env):
    settings, fake = env
    asked = []
    with pytest.raises(seed_dimensions.SyncAborted):
        seed_dimensions.run(settings, interactive=True, prompt=lambda q: asked.append(q) or "", client=object())
    assert "1 vehicle(s) will be DEACTIVATED" in asked[0]


def test_non_interactive_without_yes_refuses(env):
    settings, fake = env
    with pytest.raises(seed_dimensions.SyncAborted, match="--yes"):
        seed_dimensions.run(settings, interactive=False, client=object())
    assert fake.writes == []


def test_confirmed_run_backs_up_both_tables_before_merging(env):
    settings, fake = env
    assert seed_dimensions.run(settings, interactive=True, prompt=lambda q: "yes", client=object()) == "applied"
    kinds = [c[0] for c in fake.writes]
    assert kinds == ["snapshot", "snapshot", "merge", "merge"]
    assert {c[1] for c in fake.writes if c[0] == "snapshot"} == {"proj.ds.dim_vehicle", "proj.ds.dim_customer"}
    # All 3 vehicles are written: kept, added, and the deactivated one (never dropped).
    assert ("merge", "proj.ds.dim_vehicle", 3) in fake.writes


def test_added_vehicles_are_backfilled_after_apply(env):
    settings, fake = env
    seed_dimensions.run(settings, assume_yes=True, client=object())
    # HR55BE9999 is new; AP39WN7281 existed; MH02GS4222 was deactivated.
    assert fake.backfilled == [["HR55BE9999"]]


def test_starting_odometer_is_refreshed_after_backfill(env, monkeypatch):
    settings, fake = env
    monkeypatch.setattr(
        seed_dimensions.vehicle_backfill, "backfill",
        lambda settings, plates, client=None: SimpleNamespace(summary=lambda: "", rows_by_plate={"HR55BE9999": 1}),
    )
    odo = pd.DataFrame({
        "base_license_plate": ["HR55BE9999"], "report_date": [dt.date(2026, 9, 17)],
        "opening_odometer": [1677.5], "closing_odometer": [0.0],
    })
    seed_dimensions.run(settings, assume_yes=True, client=object())
    # The fake get_odometer_rows_by_plate returned nothing at plan time; now it has the backfilled row.
    monkeypatch.setattr(seed_dimensions.bq_client, "get_odometer_rows_by_plate", lambda c, s: odo)
    merged = []
    monkeypatch.setattr(
        seed_dimensions.bq_client, "merge_rows",
        lambda c, ref, df, schema, key: merged.append(df),
    )
    seed_dimensions._refresh_starting_odometer(
        object(), settings,
        pd.DataFrame([{**_cur("HR55BE9999", "AVG LOGISTICS"), "starting_odometer": None}]),
        {"HR55BE9999": 1},
    )
    assert merged and merged[0]["starting_odometer"].tolist() == [1677.5]


def test_no_backfill_flag_and_dry_run_skip_backfill(env):
    settings, fake = env
    seed_dimensions.run(settings, dry_run=True, client=object())
    seed_dimensions.run(settings, assume_yes=True, backfill=False, client=object())
    assert fake.backfilled == []


def test_declined_run_does_not_backfill(env):
    settings, fake = env
    with pytest.raises(seed_dimensions.SyncAborted):
        seed_dimensions.run(settings, interactive=True, prompt=lambda q: "no", client=object())
    assert fake.backfilled == []


def test_rerun_after_apply_writes_nothing(env, monkeypatch):
    """Idempotency against the exact frames that would be loaded into
    BigQuery (Int64 ids, tz-aware timestamps), not just the plan rows."""
    settings, fake = env
    merged = {}
    monkeypatch.setattr(
        seed_dimensions.bq_client, "merge_rows",
        lambda c, ref, df, schema, key: merged.__setitem__(ref, df),
    )
    assert seed_dimensions.run(settings, assume_yes=True, client=object()) == "applied"
    fake.tables = merged
    fake.install(monkeypatch)
    fake.calls.clear()
    assert seed_dimensions.run(settings, assume_yes=True, client=object()) == "no-changes"
    assert fake.writes == []


def test_corrupt_or_untagged_export_aborts_before_touching_bigquery(env, tmp_path):
    settings, fake = env
    settings.fleetx_vehicle_map_file = _write_export(tmp_path / "bad.xlsx", [[1, "A", None, None, "", "", ""]], drop=("group",))
    with pytest.raises(seed_dimensions.SyncAborted, match="rejected"):
        seed_dimensions.run(settings, assume_yes=True, client=object())
    settings.fleetx_vehicle_map_file = _write_export(tmp_path / "untagged.xlsx", [[1, "MH02GS4222", None, None, "", "", ""]])
    with pytest.raises(seed_dimensions.SyncAborted, match="known customer tag"):
        seed_dimensions.run(settings, assume_yes=True, client=object())
    assert fake.calls == []


def test_refresh_after_backfill_respects_master_override(monkeypatch):
    merged = []
    monkeypatch.setattr(seed_dimensions.bq_client, "get_odometer_rows_by_plate", lambda c, s: pd.DataFrame(
        {"base_license_plate": ["A1"], "report_date": [dt.date(2026, 9, 1)],
         "opening_odometer": [0.05], "closing_odometer": [0.1]}))
    monkeypatch.setattr(seed_dimensions.bq_client, "merge_rows", lambda *a: merged.append(a))
    seed_dimensions._refresh_starting_odometer(
        object(), SimpleNamespace(dim_vehicle_table_ref="t"),
        pd.DataFrame([{**_cur("A1", "ZingBus"), "starting_odometer": 71539.4}]),
        {"A1": 5}, master={"A1": {"starting_odometer": 71539.4}},
    )
    assert merged == []


def test_vehicle_master_starting_odometer_column_is_optional(tmp_path):
    from ingestion import vehicle_master
    cols = ["Vehicle Number", "Customer Name", "OEM", "Vehicle Type", "Vehicle Model", "Device Installation Date"]
    path = tmp_path / "m.xlsx"
    pd.DataFrame([["A1", "ZingBus", None, None, None, None]], columns=cols).to_excel(
        path, sheet_name=vehicle_master.SHEET_NAME, index=False)
    assert vehicle_master.read_and_clean_vehicle_master(path)["starting_odometer"].isna().all()
    pd.DataFrame([["A1", "ZingBus", None, None, None, None, "71,539.4"]], columns=cols + ["Starting Odometer"]).to_excel(
        path, sheet_name=vehicle_master.SHEET_NAME, index=False)
    assert vehicle_master.read_and_clean_vehicle_master(path)["starting_odometer"].tolist() == [71539.4]
