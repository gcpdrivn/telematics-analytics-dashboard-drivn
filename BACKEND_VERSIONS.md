# Backend & data versions

One place to see which backend/data version is where, what is in it, and
the commits behind it. Add a new entry at the top of **Versions** for every
change that alters the data the dashboard shows (new tables, a pipeline or
resolver fix, a re-backfill). UI-only changes don't need an entry.

Check what a running backend reads: `GET /api/health` returns
`utilization_api_table` and `odometer_resolved_table`.

## Where things stand (29 Sep 2026)

| | Production |
|---|---|
| Code | `main` at `b92f776` |
| Backend | Cloud Run `drivn-backend-00019-49g`, from `3458852` (backend unchanged since) |
| Daily jobs (IST) | 08:30 `drivn-ingest-daily` (from `3458852`), 08:30 `drivn-soh-daily` (unchanged), 09:00 `drivn-resolve-odometer` (from `b92f776`) |
| Tables read | `utilization_daily_api`, `odometer_daily_resolved`, plus `dim_vehicle`, `dim_customer`, `vehicle_mileage_soc`, `vehicle_soh_latest` |
| Data in those tables | v1 for 7 Apr – 28 Sep; v2 from 29 Sep onward (first fixed ingest 30 Sep 08:30 IST) |
| v2 copies | `utilization_daily_api_v2`, `odometer_daily_resolved_v2` (7 Apr – 28 Sep, not updated daily) |

## Versions

### v2 — IST query window (29 Sep 2026)

- **Commits:** `6b90b89` (fix + test), `3458852` (`/api/health` table
  names), `03759ba` (resolver: 1,500 km/day, 1.5M km cap), `b92f776`
  (excess km billing). Merged to `main` (fast-forward from `f439e54`).
- **Fix:** `api_pipeline._to_epoch_ms` builds the Fleetx `/trips/` window at
  IST midnight. It used the host clock, so on Cloud Run (UTC) each day's
  window ran 05:30–05:30 IST and overnight trips ending before 05:30 IST
  were dropped from every day.
- **Deployed:** `drivn-ingest-daily` and `drivn-backend-00019-49g` from
  `3458852`; new job `drivn-resolve-odometer` (09:00 IST daily, trigger
  `drivn-resolve-odometer-trigger`) from `b92f776`. Before this the
  resolver only ran when someone ran it by hand.
- **Data:** `utilization_daily_api_v2` re-fetched from Fleetx for
  7 Apr – 28 Sep 2026; `odometer_daily_resolved_v2` rebuilt from it with the
  resolver from `03759ba` (`resolver_version` 2.0.0, same as v1's).
- **Difference from v1:** [versions/v2_ist_window_diff.md](versions/v2_ist_window_diff.md)
  (only 21–28 Sep changes; August identical).
- **Pending:** copy the `_v2` tables over the production tables (back them
  up first) so 21–28 Sep is corrected on the live dashboard. Do it before
  a daily ingest runs, or re-ingest from 29 Sep afterwards.
- **Not in this version:** DL1PD8669's odometer counter reset (25 Sep) is
  uncorrected; the SoH vs odometer chart still shows ~2,400 km for it.

### v1 — before 29 Sep 2026

- **Commits:** backend `drivn-backend-00018-qdz`, deployed 28 Sep 16:37 IST
  from the working tree just before `14a6514` was committed at 16:44
  (Cloud Run source deploys record no commit hash).
- **Tables:** `utilization_daily_api` (bulk backfill 7 Apr – 20 Sep loaded
  22 Sep; then one day per night by `drivn-ingest-daily`),
  `odometer_daily_resolved` (rebuilt 29 Sep with the resolver that is now
  `03759ba`: 1,500 km/day rule, 1.5M km cap).
- **Backend env:** `UTILIZATION_SOURCE=api`, `DISTANCE_SOURCE=odometer`.
- **Known issue:** days loaded by the Cloud Run job (21 Sep onward) miss
  overnight trips; fixed in v2.

## Milestones before versioning

| Date | Commit | Change |
|---|---|---|
| 28 Sep 2026 | `14a6514` | SoH vs odometer chart (`vehicle_soh_latest`, `drivn-soh-daily`) |
| 24 Sep 2026 | `9b5e8b5` | Tag-driven `dim_vehicle`/`dim_customer` sync with auto-backfill |
| 24 Sep 2026 | `f758cfe` | Odometer resolver (`odometer_daily_resolved`), `DISTANCE_SOURCE` |
| 22 Sep 2026 | `391e8d5` | `utilization_daily_api` daily ingestion made safe to re-run |
| 18 Sep 2026 | `a916c98` | Dashboard excludes today (IST) |
| 17 Sep 2026 | `be5b04c` | Backend source switchable, Excel → API (`UTILIZATION_SOURCE`) |
| 17 Sep 2026 | `31ecbc0` | Fleetx API pipeline added alongside Excel |
| 11 Sep 2026 | `c4a83d8` | FastAPI backend on BigQuery |
| 11 Sep 2026 | `571a99a` | BigQuery ingestion pipeline (Excel) |
