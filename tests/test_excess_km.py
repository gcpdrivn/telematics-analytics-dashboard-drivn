"""Tests for the excess km terms loader (ingestion/excess_km.py) and how
dimension_sync carries the terms -- no BigQuery needed."""

from __future__ import annotations

import pandas as pd
import pytest

from ingestion import excess_km
from ingestion.dimension_sync import VehicleInputs, plan_vehicles
from tests.test_dimension_sync import NOW, _cur, _ev, _row


def _sheet(tmp_path, rows):
    path = tmp_path / "terms.xlsx"
    pd.DataFrame(
        rows, columns=["Operator", "Monthly Available KM", "Excess KM Rate (INR/KM)", "Annual KM"]
    ).to_excel(path, index=False)
    return path


GOOD_ROWS = [
    ["Zingbus", 15833.33, 18.96, 190000],
    ["Freshbus ", 19270.83, 10, 231000],
    ["Switchlabs", 13500, 10.5, 162000],
    ["BillionE", 16320, 0, 195840],
    ["Neeta", "TBD", "TBD", "TBD"],
]


def test_reads_only_billed_customers(tmp_path):
    terms = excess_km.read_excess_km_terms(_sheet(tmp_path, GOOD_ROWS))
    assert terms == {
        "ZingBus": {"monthly_available_km": 15833.33, "excess_km_rate": 18.96},
        "FreshBus": {"monthly_available_km": 19270.83, "excess_km_rate": 10.0},
        "SWITCHLABS": {"monthly_available_km": 13500.0, "excess_km_rate": 10.5},
    }


def test_rejects_non_numeric_terms_for_billed_customer(tmp_path):
    rows = [r if r[0] != "Zingbus" else ["Zingbus", "TBD", 18.96, 0] for r in GOOD_ROWS]
    with pytest.raises(excess_km.TermsFileError, match="ZINGBUS"):
        excess_km.read_excess_km_terms(_sheet(tmp_path, rows))


def test_rejects_missing_billed_customer(tmp_path):
    rows = [r for r in GOOD_ROWS if r[0] != "Switchlabs"]
    with pytest.raises(excess_km.TermsFileError, match="SWITCHLABS"):
        excess_km.read_excess_km_terms(_sheet(tmp_path, rows))


def test_rejects_missing_column(tmp_path):
    path = tmp_path / "terms.xlsx"
    pd.DataFrame({"Operator": ["Zingbus"]}).to_excel(path, index=False)
    with pytest.raises(excess_km.TermsFileError, match="missing column"):
        excess_km.read_excess_km_terms(path)


TERMS = {"FreshBus": {"monthly_available_km": 19270.83, "excess_km_rate": 10.0}}


def test_plan_changes_sets_billed_and_clears_unbilled():
    current = pd.DataFrame([
        _cur("AP1", "FreshBus"),
        _cur("AP2", "FreshBus", monthly_available_km=19270.83, excess_km_rate=10.0),
        _cur("MH1", "BillionE", monthly_available_km=16320.0, excess_km_rate=0.0),
    ])
    changes = excess_km.plan_changes(current, TERMS)
    assert changes == [
        ("AP1", "FreshBus", "monthly_available_km", None, 19270.83),
        ("AP1", "FreshBus", "excess_km_rate", None, 10.0),
        ("MH1", "BillionE", "monthly_available_km", 16320.0, None),
        ("MH1", "BillionE", "excess_km_rate", 0.0, None),
    ]


def _plan(current_rows, export, **kwargs):
    return plan_vehicles(VehicleInputs(current=pd.DataFrame(current_rows), export=export, **kwargs), NOW)


def test_sync_gives_new_vehicle_its_customers_terms():
    plan = _plan([_cur("AP1", "FreshBus")], {"AP9": _ev("AP9", "FRESHBUS")}, excess_km_terms=TERMS)
    row = _row(plan, "AP9")
    assert row["monthly_available_km"] == 19270.83
    assert row["excess_km_rate"] == 10.0


def test_sync_keeps_current_terms_when_sheet_unavailable():
    current = [_cur("AP1", "FreshBus", monthly_available_km=100.0, excess_km_rate=5.0)]
    plan = _plan(current, {"AP1": _ev("AP1", "FRESHBUS")}, excess_km_terms=None)
    row = _row(plan, "AP1")
    assert (row["monthly_available_km"], row["excess_km_rate"]) == (100.0, 5.0)
    assert "AP1" not in plan.changed
