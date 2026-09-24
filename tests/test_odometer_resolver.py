"""Pure-pandas tests for ingestion/odometer_resolver.py -- no BigQuery needed.

Each case below reproduces a real pattern found in the fleet's actual data
during this session's analysis (see the referenced plate for the real-world
shape), reduced to the minimum rows needed to exercise one behavior.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from ingestion.odometer_resolver import OVERFLOW_SENTINEL_KM, resolve


def _row(plate, date, distance, opening, closing, hours=20.0):
    start = dt.datetime(2026, 1, 1) + dt.timedelta(days=(pd.Timestamp(date) - pd.Timestamp("2026-01-01")).days)
    return {
        "Base License Plate": plate,
        "Report Date": pd.Timestamp(date),
        "Distance": distance,
        "Opening Odometer": opening,
        "Closing Odometer": closing,
        "Start Date": start,
        "End Date": start + dt.timedelta(hours=hours),
    }


def _resolved_row(resolved: pd.DataFrame, date: str) -> pd.Series:
    match = resolved[resolved["report_date"] == pd.Timestamp(date).date()]
    assert len(match) == 1, f"expected exactly one row for {date}, found {len(match)}"
    return match.iloc[0]


def test_clean_vehicle_is_all_raw_valid():
    """DL1PD8523-style: consecutive plausible days, small opening/closing
    day-boundary gaps that are normal, not faults."""
    df = pd.DataFrame(
        [
            _row("DL1PD8523", "2026-08-20", 129.52, 184073.00, 184204.52),
            _row("DL1PD8523", "2026-08-21", 291.15, 184206.88, 184510.00),
            _row("DL1PD8523", "2026-08-22", 601.39, 184510.00, 185137.13),
        ]
    )
    resolved = resolve(df, source_table="test")
    assert (resolved["fill_method"] == "RAW_VALID").all()
    assert (resolved["confidence"] == "High").all()
    row = _resolved_row(resolved, "2026-08-21")
    assert row["distance_by_odometer"] == pytest.approx(184510.00 - 184206.88)
    assert row["anomaly_flags"] == []


def test_overflow_sentinel_is_nulled_and_flagged():
    df = pd.DataFrame(
        [
            _row("AP39WN7302", "2026-05-25", 793.36, 46784.50, OVERFLOW_SENTINEL_KM),
        ]
    )
    resolved = resolve(df, source_table="test")
    row = resolved.iloc[0]
    assert "overflow_sentinel" in row["anomaly_flags"]
    assert "missing_reading" in row["anomaly_flags"]
    assert row["fill_method"] in ("DISTANCE_FALLBACK", "UNRESOLVED")


def test_single_bad_day_bracket_is_high_confidence():
    """A lone bad day between two valid anchors, where the anchors' own
    total implies a plausible average rate, has nothing to split -- should
    be INTERPOLATED at High confidence, not Medium."""
    df = pd.DataFrame(
        [
            _row("TESTPLATE1", "2026-07-10", 700.0, 1000.00, 1700.00),
            _row("TESTPLATE1", "2026-07-11", None, OVERFLOW_SENTINEL_KM, 2100.00),  # bad opening
            _row("TESTPLATE1", "2026-07-12", 690.0, 2400.00, 3090.00),
        ]
    )
    resolved = resolve(df, source_table="test")
    bad = _resolved_row(resolved, "2026-07-11")
    assert bad["fill_method"] == "INTERPOLATED"
    assert bad["confidence"] == "High"
    assert bad["distance_by_odometer"] == pytest.approx(2400.00 - 1700.00)


def test_plausible_multi_day_gap_is_medium_confidence():
    """A normal multi-day reporting gap (both bracketing anchors valid, and
    the total between them is a physically plausible average) should split
    proportionally by each day's own Distance, at Medium confidence."""
    df = pd.DataFrame(
        [
            _row("TESTPLATE2", "2026-07-10", 700.0, 1000.00, 1700.00),
            _row("TESTPLATE2", "2026-07-11", 650.0, 1700.00, OVERFLOW_SENTINEL_KM),  # bad closing
            _row("TESTPLATE2", "2026-07-12", 300.0, OVERFLOW_SENTINEL_KM, 4000.00),  # bad opening
            _row("TESTPLATE2", "2026-07-13", 690.0, 4300.00, 4990.00),
        ]
    )
    resolved = resolve(df, source_table="test")
    d1 = _resolved_row(resolved, "2026-07-11")
    d2 = _resolved_row(resolved, "2026-07-12")
    for row in (d1, d2):
        assert row["fill_method"] == "INTERPOLATED"
        assert row["confidence"] == "Medium"
    total = d1["distance_by_odometer"] + d2["distance_by_odometer"]
    assert total == pytest.approx(4300.00 - 1700.00)
    # Weighted by each day's own Distance (650 vs 300), not split evenly.
    assert d1["distance_by_odometer"] > d2["distance_by_odometer"]


def test_reset_with_implausible_bracket_falls_back_to_distance():
    """AP39WN7276-style: counter resets to ~0, coasts there a couple of
    days, then jumps back. The two boundary days (the huge negative drop and
    the huge positive catch-up) are each implausible on their own and must
    fall back to their own reported Distance -- NOT be treated as bookends
    of one real ~92,000 km delta to split (that average is itself
    impossible; see the bracket-plausibility check in _interpolate_between).

    The two quiet days in between (07-12/07-13) are a known, accepted
    detection gap: their own Opening/Closing imply a small, internally
    plausible delta, so row-internal checks alone can't tell they're really
    part of the same reset -- see this module's docstring. They come out
    RAW_VALID with their bogus tiny deltas, which this test asserts
    explicitly so a future change to that trade-off is a deliberate one."""
    df = pd.DataFrame(
        [
            _row("AP39WN7276", "2026-07-10", 432.05, 4202.64, 4646.54),
            _row("AP39WN7276", "2026-07-11", 640.69, 4646.69, 3.31),
            _row("AP39WN7276", "2026-07-12", 794.63, 0.00, 14.49),
            _row("AP39WN7276", "2026-07-13", 691.59, 0.00, 2.66),
            _row("AP39WN7276", "2026-07-14", 678.94, 0.00, 96755.59),
            _row("AP39WN7276", "2026-07-15", 776.55, 96769.25, 97573.38),
        ]
    )
    resolved = resolve(df, source_table="test")

    boundary_11 = _resolved_row(resolved, "2026-07-11")
    boundary_14 = _resolved_row(resolved, "2026-07-14")
    for row, expected_distance in ((boundary_11, 640.69), (boundary_14, 678.94)):
        assert row["fill_method"] == "DISTANCE_FALLBACK"
        assert row["confidence"] == "Low"
        assert len(row["anomaly_flags"]) > 0
        assert row["distance_by_odometer"] == pytest.approx(expected_distance)

    # Known accepted gap: these pass row-internal checks and stay RAW_VALID,
    # with their bogus tiny (post-reset) deltas taken at face value.
    quiet_12 = _resolved_row(resolved, "2026-07-12")
    quiet_13 = _resolved_row(resolved, "2026-07-13")
    for row in (quiet_12, quiet_13):
        assert row["fill_method"] == "RAW_VALID"
        assert row["anomaly_flags"] == []

    good_before = _resolved_row(resolved, "2026-07-10")
    good_after = _resolved_row(resolved, "2026-07-15")
    assert good_before["fill_method"] == "RAW_VALID"
    assert good_after["fill_method"] == "RAW_VALID"


def test_no_anchor_falls_back_to_distance():
    """MH02GS4224-style: the vehicle's only-ever row has a garbage absolute
    reading but a perfectly plausible Distance -- daily distance should
    still recover even with zero usable odometer anchors."""
    df = pd.DataFrame(
        [
            _row("MH02GS4224", "2026-07-23", 523.80, 99999991.0, 100000514.8),
        ]
    )
    resolved = resolve(df, source_table="test")
    row = resolved.iloc[0]
    assert row["fill_method"] == "DISTANCE_FALLBACK"
    assert row["confidence"] == "Low"
    assert row["distance_by_odometer"] == pytest.approx(523.80)
    assert pd.isna(row["opening_odometer_clean"])
    assert pd.isna(row["closing_odometer_clean"])


def test_no_anchor_and_no_distance_is_unresolved():
    df = pd.DataFrame(
        [
            _row("MH02GS4224", "2026-07-23", None, 99999991.0, 100000514.8),
        ]
    )
    resolved = resolve(df, source_table="test")
    row = resolved.iloc[0]
    assert row["fill_method"] == "UNRESOLVED"
    assert row["confidence"] == "Unresolved"
    assert pd.isna(row["distance_by_odometer"])


def test_no_anchor_vehicle_each_day_handled_independently():
    """A vehicle with zero usable odometer readings anywhere in this window
    (every row's odometer fields are corrupted) resolves day-by-day from
    each day's own Distance -- never chained into a single multi-day delta,
    since there's no RAW_VALID anchor anywhere to chain from."""
    df = pd.DataFrame(
        [
            _row("BADPLATE", "2026-09-17", None, 0.00, OVERFLOW_SENTINEL_KM),
            _row("BADPLATE", "2026-09-18", 363.84, 0.00, OVERFLOW_SENTINEL_KM),
            _row("BADPLATE", "2026-09-19", None, 0.00, OVERFLOW_SENTINEL_KM),
        ]
    )
    resolved = resolve(df, source_table="test")
    with_distance = _resolved_row(resolved, "2026-09-18")
    assert with_distance["fill_method"] == "DISTANCE_FALLBACK"
    assert with_distance["distance_by_odometer"] == pytest.approx(363.84)

    without_distance = _resolved_row(resolved, "2026-09-17")
    assert without_distance["fill_method"] == "UNRESOLVED"


def test_boundary_gap_small_adjacent_day_residual():
    """DL1PD8523-style: two adjacent RAW_VALID days, Opening doesn't quite
    match the previous day's Closing -- a small, plausible reporting-window
    residual, not a fault."""
    df = pd.DataFrame(
        [
            _row("DL1PD8523", "2026-08-20", 129.52, 184073.00, 184204.52),
            _row("DL1PD8523", "2026-08-21", 291.15, 184206.88, 184510.00),
        ]
    )
    resolved = resolve(df, source_table="test")
    row = _resolved_row(resolved, "2026-08-21")
    assert row["boundary_gap_distance"] == pytest.approx(184206.88 - 184204.52)


def test_boundary_gap_multi_day_silence_still_plausible():
    """A multi-day gap in reporting (no rows at all in between) between two
    RAW_VALID days -- the same formula, just a bigger, still-plausible
    number since more time elapsed."""
    df = pd.DataFrame(
        [
            _row("TESTPLATE3", "2026-07-01", 700.0, 1000.00, 1700.00),
            _row("TESTPLATE3", "2026-07-05", 720.0, 5000.00, 5720.00),
        ]
    )
    resolved = resolve(df, source_table="test")
    row = _resolved_row(resolved, "2026-07-05")
    assert row["boundary_gap_distance"] == pytest.approx(5000.00 - 1700.00)


def test_boundary_gap_implausible_is_excluded():
    """Two individually-RAW_VALID rows whose gap alone implies an impossible
    rate -- must be left NULL, not recorded as a fabricated jump."""
    df = pd.DataFrame(
        [
            _row("TESTPLATE4", "2026-07-01", 700.0, 1000.00, 1700.00),
            _row("TESTPLATE4", "2026-07-02", 720.0, 50000.00, 50720.00),
        ]
    )
    resolved = resolve(df, source_table="test")
    row = _resolved_row(resolved, "2026-07-02")
    assert pd.isna(row["boundary_gap_distance"])


def test_boundary_gap_not_computed_across_a_bad_row():
    """No gap when the immediately preceding row isn't RAW_VALID -- the
    bracket/fallback logic already accounts for that transition."""
    df = pd.DataFrame(
        [
            _row("TESTPLATE1", "2026-07-10", 700.0, 1000.00, 1700.00),
            _row("TESTPLATE1", "2026-07-11", None, OVERFLOW_SENTINEL_KM, 2100.00),
            _row("TESTPLATE1", "2026-07-12", 690.0, 2400.00, 3090.00),
        ]
    )
    resolved = resolve(df, source_table="test")
    row = _resolved_row(resolved, "2026-07-12")
    assert pd.isna(row["boundary_gap_distance"])


def test_stuck_sensor_flag_is_one_directional():
    """Odometer flat while Distance shows real movement -> flagged (the
    odometer is what's wrong). Distance flat/zero while odometer shows
    plausible movement should NOT be flagged -- Distance is the field
    believed to be less accurate, so it alone disagreeing with the odometer
    is not evidence the odometer is wrong."""
    stuck = pd.DataFrame(
        [_row("STUCKPLATE", "2026-09-01", 350.0, 1000.00, 1000.05)]
    )
    resolved_stuck = resolve(stuck, source_table="test")
    assert "stuck_sensor" in resolved_stuck.iloc[0]["anomaly_flags"]

    distance_wrong = pd.DataFrame(
        [_row("DISTWRONGPLATE", "2026-09-01", 0.0, 1000.00, 1350.00)]
    )
    resolved_distance_wrong = resolve(distance_wrong, source_table="test")
    assert resolved_distance_wrong.iloc[0]["fill_method"] == "RAW_VALID"
    assert "stuck_sensor" not in resolved_distance_wrong.iloc[0]["anomaly_flags"]
