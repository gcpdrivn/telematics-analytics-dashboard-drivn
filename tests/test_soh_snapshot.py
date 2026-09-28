"""Tests for the morning SoH ping: device choice from the Realtime feed and
the upsert rules -- no BigQuery or Fleetx calls."""

from __future__ import annotations

import datetime as dt
import json

import pandas as pd

from ingestion import soh_snapshot
from ingestion.soh_snapshot import combine, extract_rows

MON = dt.datetime(2026, 9, 28, 3, 0, tzinfo=dt.timezone.utc)
TUE = MON + dt.timedelta(days=1)


def _device(number, vid, odo=None, updated=1_790_000_000_000, **attrs):
    return {"vehicleNumber": number, "vehicleId": vid, "totalOdometer": odo, "lastUpdatedAt": updated, "otherAttributes": attrs}


# --- extract_rows -----------------------------------------------------------


def test_skips_cameras_and_reads_soh_and_odometer_from_the_same_device():
    live = [
        _device("DL1PD8509", 1, odo=19_033, deviceType="LCD603-2CH-CAT1"),
        _device("DL1PD8509OBD", 2, odo=203_851, deviceType="S-2437", evSOH="94.4"),
    ]
    row = extract_rows(live, [("DL1PD8509", 1)]).iloc[0]
    assert (row.fleetx_device, row.soh_pct, row.soh_field, row.odometer_raw_km) == ("DL1PD8509OBD", 94.4, "evSOH", 203_851)


def test_prefers_the_device_that_reports_soh():
    live = [
        _device("DL1PD9284", 1, odo=39_324, updated=1_790_000_900_000, deviceType="S-2437"),
        _device("DL1PD9284_API", 2, odo=87_122, deviceType="BHARAT101", evSOH="97"),
    ]
    assert extract_rows(live, [("DL1PD9284", 1)]).iloc[0].fleetx_device == "DL1PD9284_API"


def test_matches_dl01_spelling_and_reads_ev_extra_parameters():
    live = [_device("DL1PD9317_API", 1, odo=88_447, evExtraParameters=json.dumps({"soh_percentage": 96.5}))]
    row = extract_rows(live, [("DL01PD9317", None)]).iloc[0]
    assert (row.base_license_plate, row.soh_pct, row.soh_field) == ("DL01PD9317", 96.5, "evExtra.soh_percentage")


def test_unknown_plates_and_out_of_range_soh_are_ignored():
    live = [_device("XX00AA0000", 1, evSOH="95"), _device("MH02GS4222", 2, odo=16_411, evSOH="0")]
    df = extract_rows(live, [("MH02GS4222", 2)])
    assert list(df.base_license_plate) == ["MH02GS4222"]
    assert pd.isna(df.iloc[0].soh_pct) and pd.isna(df.iloc[0].soh_reported_at)


# --- combine ----------------------------------------------------------------


def _fresh(plate, soh=None, odo=None, at=MON):
    return pd.DataFrame([{
        "base_license_plate": plate, "fleetx_device": plate, "fleetx_vehicle_id": 1,
        "soh_pct": soh, "soh_field": "evSOH" if soh is not None else None,
        "soh_reported_at": at if soh is not None else None,
        "odometer_raw_km": odo, "odometer_reported_at": at,
    }])


def _row(table, plate):
    return table.set_index("base_license_plate").loc[plate]


def test_first_ping_adds_the_vehicle():
    table, report = combine(pd.DataFrame(), _fresh("A", soh=95.0, odo=100_000), MON)
    row = _row(table, "A")
    assert report.added == ["A"] and (row.soh_pct, row.odometer_km, row.first_seen_at) == (95.0, 100_000, MON)


def test_later_ping_updates_but_keeps_first_seen():
    first, _ = combine(pd.DataFrame(), _fresh("A", soh=95.0, odo=100_000), MON)
    table, report = combine(first, _fresh("A", soh=94.8, odo=100_600, at=TUE), TUE)
    row = _row(table, "A")
    assert report.updated == ["A"]
    assert (row.soh_pct, row.odometer_km, row.first_seen_at, row.last_pinged_at) == (94.8, 100_600, MON, TUE)


def test_missing_soh_keeps_the_stored_one():
    first, _ = combine(pd.DataFrame(), _fresh("A", soh=95.0, odo=100_000), MON)
    table, _ = combine(first, _fresh("A", soh=None, odo=100_600, at=TUE), TUE)
    row = _row(table, "A")
    assert (row.soh_pct, row.soh_reported_at, row.odometer_km) == (95.0, MON, 100_600)


def test_faulty_or_backwards_odometer_keeps_the_stored_one_and_says_why():
    first, _ = combine(pd.DataFrame(), _fresh("A", soh=95.0, odo=174_000), MON)
    table, report = combine(first, _fresh("A", odo=1_843, at=TUE), TUE)
    row = _row(table, "A")
    assert (row.odometer_km, row.odometer_raw_km, row.odometer_reported_at) == (174_000, 1_843, MON)
    assert "backwards" in row.odometer_flag and "A" in report.odometer_rejected

    table, _ = combine(first, _fresh("A", odo=99_999_990, at=TUE), TUE)
    assert _row(table, "A").odometer_km == 174_000 and "device fault" in _row(table, "A").odometer_flag


def test_small_backwards_wobble_is_accepted_and_clears_the_flag():
    first, _ = combine(pd.DataFrame(), _fresh("A", odo=100_000), MON)
    first.loc[0, "odometer_flag"] = "went backwards: ..."
    table, _ = combine(first, _fresh("A", odo=100_000 * (1 - soh_snapshot.BACKWARDS_TOLERANCE / 2), at=TUE), TUE)
    assert _row(table, "A").odometer_flag is None


def test_vehicle_missing_from_the_ping_is_kept_untouched():
    first, _ = combine(pd.DataFrame(), pd.concat([_fresh("A", soh=95.0, odo=1), _fresh("B", soh=96.0, odo=2)]), MON)
    table, report = combine(first, _fresh("A", soh=94.0, odo=3, at=TUE), TUE)
    assert report.not_pinged == ["B"]
    assert (_row(table, "B").soh_pct, _row(table, "B").last_pinged_at) == (96.0, MON)
