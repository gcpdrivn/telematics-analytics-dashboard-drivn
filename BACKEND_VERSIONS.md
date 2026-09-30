# Backend & data versions

One place to see which backend/data version is where, what is in it, and
the commits behind it. Add a new entry at the top of **Versions** for every
change that alters the data the dashboard shows (new tables, a pipeline or
resolver fix, a re-backfill). UI-only changes don't need an entry.

Check what a running backend reads: `GET /api/health` returns
`utilization_api_table` and `odometer_resolved_table`.

## Where things stand (30 Sep 2026)

Fully moved onto the `_v2` tables -- not a back-patch of the production-named
ones, a permanent switch. Every consumer (dashboard, daily jobs, excess-km
billing) reads/writes `_v2` now.

| | Production |
|---|---|
| Code | `main` at `01d1a88` |
| Backend | Cloud Run `drivn-backend-00020-mdj` -- `BQ_UTILIZATION_API_TABLE=utilization_daily_api_v2`, `BQ_ODOMETER_RESOLVED_TABLE=odometer_daily_resolved_v2`. Confirmed via `GET /api/health`. |
| Daily jobs (IST) | 08:30 `drivn-ingest-daily`, 08:30 `drivn-soh-daily` (unaffected -- writes `vehicle_soh_latest`), 09:00 `drivn-resolve-odometer` -- the two utilization-related jobs updated + manually executed to confirm, both target `_v2` |
| Tables read (dashboard + billing) | `utilization_daily_api_v2`, `odometer_daily_resolved_v2`, plus `dim_vehicle`, `dim_customer`, `vehicle_mileage_soc`, `vehicle_soh_latest` |
| Excess-km billing | `excess_km_monthly` / `excess_km_monthly_customer` views recreated against `odometer_daily_resolved_v2` (`sync-excess-km`, no `dim_vehicle` term changes needed) |
| `_v2` data coverage | `utilization_daily_api_v2`: 7 Apr – 30 Sep. `odometer_daily_resolved_v2`: rebuilt full-history, current through 29 Sep (resolver excludes today, same as v1 always did). |
| v1 (original tables) | Frozen at their 30 Sep state, no longer written to. Kept as-is plus explicit 90-day snapshots: `utilization_daily_api_backup_20260930T091207Z`, `odometer_daily_resolved_backup_20260930T091207Z`. Restore either way: `CREATE OR REPLACE TABLE \`<table>\` CLONE \`<backup-or-v1-table>\`` |

### What was fixed getting `_v2` current (30 Sep 2026)

- `utilization_daily_api_v2`'s existing 29 Sep row was stale/partial (49
  vehicles, 6,543 km total) vs. production's already-correct 29 Sep (64
  vehicles, 21,036 km -- production's own daily job had already picked up
  the IST-window fix by its 30 Sep run, since `drivn-ingest-daily` was
  redeployed 29 Sep 16:53 UTC, before that morning's run). Re-ingested 29-30
  Sep into `_v2` via `ingest-utilization-api --from 2026-09-29 --to
  2026-09-30` (env-overridden to the `_v2` table); 29 Sep now matches
  production exactly.
- `odometer_daily_resolved_v2` fully rebuilt from the corrected
  `utilization_daily_api_v2` (4,048 rows, current through 29 Sep).

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
