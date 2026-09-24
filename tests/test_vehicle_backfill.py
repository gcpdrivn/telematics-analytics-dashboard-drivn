"""Tests for per-vehicle backfill: the pipeline's plate scoping (never
touching other vehicles' rows) and vehicle_backfill.backfill's chunking and
follow-up steps -- no BigQuery or Fleetx calls."""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pandas as pd

from ingestion import api_pipeline, bq_client, vehicle_backfill
from ingestion.api_pipeline import VehicleResult


def _settings(**kw):
    base = dict(
        utilization_api_table_ref="p.d.utilization_daily_api",
        fleetx_vehicle_map_file=SimpleNamespace(exists=lambda: False),
        backend_url=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


# --- bq_client.load_utilization_api_rows ------------------------------------


class RecordingClient:
    def __init__(self):
        self.queries = []
        self.loaded = None

    def query(self, sql, job_config=None):
        self.queries.append((sql, job_config))
        return SimpleNamespace(result=lambda: [])

    def load_table_from_dataframe(self, df, ref, job_config=None):
        self.loaded = df
        return SimpleNamespace(result=lambda: None)


def _df(*plates):
    return pd.DataFrame({"base_license_plate": list(plates), "report_date": [dt.date(2026, 9, 1)] * len(plates)})


def test_plate_scoped_delete_only_touches_those_plates():
    client = RecordingClient()
    bq_client.load_utilization_api_rows(
        client, _settings(), _df("A", "B"), dt.date(2026, 9, 1), dt.date(2026, 9, 2), plates=["A"]
    )
    sql, cfg = client.queries[0]
    assert "base_license_plate IN UNNEST(@plates)" in sql
    plates_param = next(p for p in cfg.query_parameters if p.name == "plates")
    assert plates_param.values == ["A"]
    assert list(client.loaded["base_license_plate"]) == ["A"]


def test_unscoped_delete_is_unchanged_for_the_daily_run():
    client = RecordingClient()
    bq_client.load_utilization_api_rows(client, _settings(), _df("A"), dt.date(2026, 9, 1), dt.date(2026, 9, 2))
    assert "UNNEST" not in client.queries[0][0]


def test_empty_plate_scope_writes_nothing():
    client = RecordingClient()
    bq_client.load_utilization_api_rows(client, _settings(), _df("A"), dt.date(2026, 9, 1), dt.date(2026, 9, 2), plates=[])
    assert client.queries == [] and client.loaded is None


# --- api_pipeline.run(plates=...) --------------------------------------------


def test_pipeline_fetches_only_requested_plates_and_skips_failed_in_delete(monkeypatch):
    fetched, loads = [], []
    monkeypatch.setattr(bq_client, "get_client", lambda s: object())
    monkeypatch.setattr(bq_client, "ensure_utilization_api_table", lambda c, s: None)
    monkeypatch.setattr(bq_client, "get_vehicle_fleetx_ids", lambda c, s: [("A", 1), ("B", 2), ("C", 3)])
    monkeypatch.setattr(api_pipeline.fleetx_client, "login", lambda: "tok")

    def fake_fetch(token, fid, f, t):
        fetched.append(fid)
        if fid == 2:
            raise api_pipeline.requests.RequestException("boom")
        return [{"fake": True}], token

    monkeypatch.setattr(api_pipeline, "_fetch_trips_relogin_once", fake_fetch)
    monkeypatch.setattr(
        api_pipeline.api_transform, "aggregate_trips_to_daily",
        lambda trips, plate, fid: pd.DataFrame({"base_license_plate": [plate], "report_date": [dt.date(2026, 9, 1)]}),
    )
    monkeypatch.setattr(
        bq_client, "load_utilization_api_rows",
        lambda c, s, df, a, b, plates=None: loads.append(plates),
    )
    results = api_pipeline.run(
        _settings(), start_date=dt.date(2026, 9, 1), end_date=dt.date(2026, 9, 1), plates=["A", "B"]
    )
    assert fetched == [1, 2]  # C never fetched
    assert {r.base_license_plate: r.status for r in results} == {"A": "loaded", "B": "failed"}
    assert loads == [["A"]]  # B's existing rows are not deleted


# --- vehicle_backfill.backfill -----------------------------------------------


def test_backfill_chunks_and_runs_follow_ups():
    calls, resolved, refreshed = [], [], []

    def pipeline(settings, start_date, end_date, plates):
        calls.append((start_date, end_date, tuple(plates)))
        return [VehicleResult(p, 1, "loaded", row_count=3) for p in plates]

    report = vehicle_backfill.backfill(
        _settings(), ["B", "A", "A"],
        start_date=dt.date(2026, 7, 1), end_date=dt.date(2026, 9, 10),
        run_pipeline=pipeline,
        resolve_odometer=lambda s: resolved.append(1),
        refresh_cache=lambda s: refreshed.append(1) or True,
    )
    assert [(a, b) for a, b, _ in calls] == [
        (dt.date(2026, 7, 1), dt.date(2026, 7, 31)),
        (dt.date(2026, 8, 1), dt.date(2026, 8, 31)),
        (dt.date(2026, 9, 1), dt.date(2026, 9, 10)),
    ]
    assert all(p == ("A", "B") for *_, p in calls)
    assert report.rows_by_plate == {"A": 9, "B": 9}
    assert resolved and refreshed and report.cache_refreshed


def test_backfill_reports_failures_and_skips_follow_ups_without_data():
    resolved = []
    report = vehicle_backfill.backfill(
        _settings(), ["A"],
        start_date=dt.date(2026, 9, 1), end_date=dt.date(2026, 9, 2),
        run_pipeline=lambda s, start_date, end_date, plates: [VehicleResult("A", 1, "failed", error="timeout")],
        resolve_odometer=lambda s: resolved.append(1),
        refresh_cache=lambda s: True,
    )
    assert "A" in report.failed and not resolved
    assert "FAILED" in report.summary()


def test_backfill_survives_follow_up_errors():
    def boom(settings):
        raise RuntimeError("down")

    report = vehicle_backfill.backfill(
        _settings(), ["A"],
        start_date=dt.date(2026, 9, 1), end_date=dt.date(2026, 9, 1),
        run_pipeline=lambda s, start_date, end_date, plates: [VehicleResult("A", 1, "loaded", row_count=1)],
        resolve_odometer=boom, refresh_cache=boom,
    )
    assert not report.odometer_resolved and not report.cache_refreshed
    assert any("resolve-odometer failed" in n for n in report.notes)


def test_refresh_cache_is_a_no_op_without_backend_url():
    assert vehicle_backfill.refresh_dashboard_cache(_settings(backend_url=None)) is False
