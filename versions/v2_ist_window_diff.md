# v2 vs v1 — what changed in the data (29 Sep 2026)

**Change:** the daily Fleetx `/trips/` query window is built at IST midnight
(`ingestion/api_pipeline.py`). On Cloud Run (UTC) it ran 05:30–05:30 IST, so
an overnight trip ending before 05:30 IST was dropped from every day.
Everything below compares data up to 28 Sep 2026.

## Which days changed

- **Only 21–28 Sep 2026.** Those are the rows loaded by the scheduled
  Cloud Run job (from 22 Sep). The bulk backfill (7 Apr – 20 Sep, loaded
  22 Sep) is identical in v1 and v2, so it must have run on an IST clock.
- **August is unchanged:** KPIs and odometer km are identical, so the
  August excess-km workbook is unaffected.

## Daily data (`utilization_daily_api`)

| | v1 | v2 |
|---|---|---|
| Rows (to 28 Sep) | 3,996 | 3,984 |
| Rows with changed values | – | 228 rows, 68 vehicles |
| Reported distance, total | 1,899,199 km | 1,927,888 km (+28,689) |
| Days whose opening ≠ previous closing | 1,058 | 886 |

- **v1-only rows (14):** all 0 km. These are the same kind of trip that never
  closes, which Fleetx returns whatever the query dates
  (the trucks HR55BE9129, 1961, 2626, 5316 and others). v2 doesn't pick them up.
- **v2-only rows (2):** HR55BE1589 on 24 Sep (304.8 km) and HR55BE5467 on
  26 Sep (245.0 km reported). v1 had missed both days.

## Odometer resolver (`odometer_daily_resolved`)

| | v1 | v2 |
|---|---|---|
| Odometer km, Sep 1–28 | 504,510 | 517,990 (+13,480) |
| Km between days, not tied to a day ("boundary gaps") | 86,977 | 69,116 |
| RAW_VALID / INTERPOLATED / DISTANCE_FALLBACK / UNRESOLVED | 3,865 / 74 / 54 / 3 | 3,881 / 52 / 47 / 4 |

- **Km move from the gaps between days into the days themselves.** For
  example, DL1PD8669 on 28 Sep: v1 had 253.75 km plus 302.12 km of gap;
  v2 has 555.87 km and no gap.
- **Removed:** fake single-day km on new AVG LOGISTICS trucks. v1 bridged
  from a stray "odometer 0" row to the truck's first real reading and put it
  all on one day (HR55BE5316 4,051 km on 22 Sep, HR55BE0128 2,832 km on
  25 Sep, HR55BE1961 2,612 km on 22 Sep). v2 records 0–2 km for those days.

## Dashboard (local, v1 on :8001 vs v2 on :8000)

| | v1 | v2 |
|---|---|---|
| Fleet total distance (all dates) | 1,927,439 km | 1,922,289 km |
| Fleet km/day | 10,665 | 10,742 |
| Buses: total km / km per vehicle per day | 1,576,502 / 576 | 1,576,444 / 585 |
| Trucks: total km / km per vehicle per day | 350,936 / 266 | 345,844 / 237 |
| Header odometer total | 3,609,241 | 3,610,857 |

Customer totals (the Customers page sums daily km, without the gaps):

| Customer | v1 km | v2 km | Diff |
|---|---|---|---|
| FreshBus | 958,063 | 964,233 | +6,170 |
| ZingBus | 595,350 | 606,102 | +10,752 |
| BillionE | 265,847 | 272,910 | +7,063 |
| AVG LOGISTICS | 41,440 | 30,869 | −10,571 (fake truck km removed) |
| SWITCHLABS | 5,599 | 5,666 | +67 |

- **Bus totals are almost unchanged.** The fleet total already added the
  gaps between days, so v2 mainly moves km into the right day. Per-day
  figures (km/day, hours/day) rise.
- **The truck total falls** because of the fake AVG LOGISTICS km above.
- **SoH vs odometer chart: no change.** It reads `vehicle_soh_latest`, which
  both versions share. DL1PD8669 still shows 2,312.75 km, unflagged.

## Not changed by v2

- `drivn-ingest-daily` still runs the old code and writes to the v1 tables
  only. The `_v2` tables stop at 28 Sep until the job is redeployed.
- The `excess_km_monthly*` views and the excess-km export read v1.
- DL1PD8669's odometer counter reset (25 Sep) is uncorrected in both versions.
