"""Prints raw Fleetx History Report trips for one vehicle over a date range,
for manually verifying api_transform.py's aggregation against the live API
-- e.g. confirming a "stuck-open trip session" (see
MAX_PLAUSIBLE_DAILY_DISTANCE_KM in api_transform.py) by eyeballing the raw
sDate/eDate/duration/odometer fields Fleetx actually returned.

Run: uv run python -m ingestion.inspect_trips MH02GS5194 --from 2026-09-10 --to 2026-09-17
"""

from __future__ import annotations

import argparse
import datetime as dt

from ingestion import api_pipeline, bq_client, fleetx_client
from ingestion.config import load_settings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plate", help="base_license_plate, e.g. MH02GS5194")
    parser.add_argument("--from", dest="start_date", required=True, type=dt.date.fromisoformat)
    parser.add_argument("--to", dest="end_date", required=True, type=dt.date.fromisoformat)
    args = parser.parse_args()

    settings = load_settings()
    client = bq_client.get_client(settings)
    vehicles = dict(bq_client.get_vehicle_fleetx_ids(client, settings))
    fleetx_id = vehicles.get(args.plate)
    if fleetx_id is None:
        raise SystemExit(f"{args.plate}: no resolved fleetx_id in dim_vehicle.")

    token = fleetx_client.login()
    from_ms = api_pipeline._to_epoch_ms(args.start_date)
    to_ms = api_pipeline._to_epoch_ms(args.end_date + dt.timedelta(days=1))
    trips = fleetx_client.get_trips(token, fleetx_id, from_ms, to_ms)

    print(f"{args.plate} (fleetx_id={fleetx_id}): {len(trips)} trip(s) {args.start_date}..{args.end_date}\n")

    day_totals: dict[str, float] = {}
    for t in sorted(trips, key=lambda t: t.get("sDate") or ""):
        s_date, e_date = t.get("sDate"), t.get("eDate") or t.get("sDate")
        duration_ms = t.get("duration") or 0
        s_odo, e_odo = t.get("sOdo"), t.get("eOdo")
        dist = (e_odo - s_odo) if s_odo is not None and e_odo is not None else None
        hours = duration_ms / 3_600_000
        report_date = (e_date or "?")[:10]
        day_totals[report_date] = day_totals.get(report_date, 0.0) + hours
        flag = "  <-- single trip >24h" if hours > 24 else ""
        print(
            f"[{report_date}] trip {t.get('tripId')[:8]}: {s_date} -> {e_date}  "
            f"duration={hours:.2f}h  odo={s_odo}->{e_odo} (dist={dist}){flag}"
        )

    print("\nPer-day summed duration (report_date = trip's eDate):")
    for d, hrs in sorted(day_totals.items()):
        flag = "  <-- exceeds 24h/day, sum of overlapping/duplicate trips" if hrs > 24 else ""
        print(f"  {d}: {hrs:.1f}h{flag}")


if __name__ == "__main__":
    main()
