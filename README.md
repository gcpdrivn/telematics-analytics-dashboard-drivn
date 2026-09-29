# Telematics Analytics Platform

## Utilization report ingestion

Ingests daily "Utilization report" Excel exports into BigQuery, applying the
same cleaning rules developed in `Analysis/data_consolidation_v1.ipynb`
(plate normalization, duration parsing, DashCam-row exclusion), while keeping
both the raw and cleaned license plate columns. Re-running the pipeline over
a folder never re-inserts a file that was already loaded — each file's
content hash is checked against a BigQuery-backed ingestion log before load.

### Setup

```bash
uv sync
gcloud auth application-default login   # one-time, for BigQuery access
cp .env.example .env                    # then fill in GCP_PROJECT_ID etc.
```

### Usage

1. Drop (or copy) daily `Utilization report*.xlsx` files into
   `data/raw/utilization/`.
2. Run the pipeline:

```bash
uv run ingest-utilization           # loads new files into BigQuery
uv run ingest-utilization --dry-run # parse/clean only, no BigQuery writes
uv run ingest-utilization -v        # verbose logging
```

The pipeline will:
- Create the `BQ_DATASET` dataset and `utilization_daily` / `ingestion_log`
  tables if they don't already exist (see `ingestion/schema.py`).
- Skip any file whose SHA-256 hash is already present in `ingestion_log`.
- Clean and load every new file, then record it in `ingestion_log`.

### Mileage/SoC benchmarks and customer/vehicle dimensions

Two more occasional commands for the small reference tables:

```bash
uv run ingest-mileage-soc          # full-refresh: data/raw/vehicle_mileage_soc.xlsx -> vehicle_mileage_soc
uv run seed-dimensions --dry-run   # preview a dim_customer + dim_vehicle sync, write nothing
uv run seed-dimensions             # preview, ask for confirmation, back up, then apply
```

`seed-dimensions` syncs `dim_vehicle` / `dim_customer` from the Fleetx vehicle export
(`data/raw/Vehicle_Update_uploader.xlsx`). Each vehicle's customer comes from its Fleetx
customer tag, mapped in `ingestion/dimension_sync.py`'s `CUSTOMER_TAGS`. An untagged vehicle,
or one with an unmapped tag, is reported and skipped, never guessed. The sync is safe to run
against a bad export:

- It refuses a malformed export (missing sheet/columns, no rows, no known customer tags).
- It prints every add / change / deactivation first, and writes nothing without a typed
  `yes` (or `--yes` for unattended runs).
- It never deletes: a vehicle missing from the export is marked `is_active = FALSE`, keeping
  its history. The daily API pull skips inactive vehicles.
- It snapshots both tables (kept 30 days) before writing, and prints the one-line restore
  command. The write itself is a single atomic `MERGE`.
- Hand-entered data wins: `dim_vehicle_master.xlsx` > the value already in the table > derived
  values, and a filled-in value is never replaced by a blank.
- New or reactivated vehicles get their full history pulled right after the sync (only their
  rows are written), then `resolve-odometer` runs and the dashboard cache is cleared (if
  `BACKEND_URL` is set), so they show up immediately. Skip with `--no-backfill`; retry with
  `uv run backfill-vehicles PLATE ...`.

### Excess km billing

```bash
uv run sync-excess-km --dry-run   # preview dim_vehicle term changes from the terms sheet
uv run sync-excess-km             # preview, confirm, back up, apply; (re)create the views
```

Loads each billed customer's `Monthly Available KM` and `Excess KM Rate (INR/KM)` from
`data/raw/Excess KM & Battery Replacement.xlsx` into `dim_vehicle.monthly_available_km` /
`excess_km_rate` (same values for every vehicle of that customer; billed customers are listed in
`ingestion/excess_km.py`'s `SHEET_OPERATORS`). `seed-dimensions` fills the same columns, so newly
onboarded vehicles get their customer's terms.

Two BigQuery views compute the result per completed calendar month (the current month appears once
it closes):

- `excess_km_monthly` has one row per vehicle-month. Billable km come from trusted odometer days
  (RAW_VALID / INTERPOLATED / MANUAL_OVERRIDE), plus km driven while the device was silent, counted
  only when that averages under 1,500 km/day. Excess km is `max(0, billable km − allowance)`, and cost
  is excess km × rate.
- A month is flagged `needs_review` in two cases, but is still costed:
  - under 90% of its in-service days are backed by trusted readings;
  - its odometer km differ from the reported Distance by more than 15%.
- `excess_km_monthly_customer` is the per-customer rollup.

Each month, after the month closes:

```bash
uv run export-excess-km                  # last month -> data/exports/excess_km_<YYYY-MM>.xlsx
uv run export-excess-km --month 2026-08  # a specific completed month
```

It rebuilds `odometer_daily_resolved` first (skip with `--no-refresh`), then writes a workbook with
these tabs:
- Summary
- Vehicle Detail
- Daily Readings (every day and how it was counted)
- Terms
- Method

Excess km, cost and review flags are Excel formulas driven by the Terms tab. `data/exports/` is
gitignored.

### Battery SoH (morning ping)

```bash
uv run ingest-soh --dry-run   # ping Fleetx's Realtime API, show what would change, write nothing
uv run ingest-soh             # upsert into vehicle_soh_latest
```

Pings `GET /api/v1/analytics/live` once and stores each vehicle's latest battery SoH and live
odometer in `vehicle_soh_latest` (one row per vehicle), which feeds the Customers page's
"Battery State of Health vs Latest Odometer" chart. It is meant to run every morning:

- DashCam devices are skipped. Of a vehicle's other devices, the one reporting a SoH wins, and
  SoH and odometer are both read from it.
- A vehicle seen for the first time is added. One already stored is updated, but a value is only
  replaced by a good new one: a missing SoH, or an odometer that is a device fault (past 15 lakh
  km) or has gone backwards by more than 1%, keeps the stored value. The raw reading and the
  reason are still recorded (`odometer_raw_km`, `odometer_flag`).
- A vehicle missing from the ping keeps its row untouched. Nothing is ever deleted.

The dashboard draws a point hollow when its SoH is the flat 100% default some truck devices
report, or when the live odometer is far below the vehicle's own odometer history (a device
reset). Hovering says which.

### Layout

```
ingestion/
  config.py          # env-var driven settings (.env)
  schema.py          # BigQuery table schemas
  transform.py       # Utilization Excel -> cleaned DataFrame (ported from the notebook)
  mileage.py          # Mileage/SoC Excel -> cleaned DataFrame + full-refresh load
  seed_dimensions.py   # Safe sync of dim_customer / dim_vehicle (preview, confirm, backup, MERGE)
  dimension_sync.py    # Pure planning/diff rules for that sync, incl. CUSTOMER_TAGS
  vehicle_backfill.py  # Pulls full history for specific (newly onboarded) vehicles
  soh_snapshot.py      # Morning Realtime API ping -> vehicle_soh_latest (SoH + live odometer)
  bq_client.py          # dataset/table provisioning, dedup lookups, loading
  pipeline.py            # orchestrates one utilization ingestion run
  cli.py                  # `ingest-utilization` / `ingest-mileage-soc` / `seed-dimensions` / `ingest-soh` / ...
data/raw/utilization/       # drop raw Excel reports here (not committed)
data/raw/vehicle_mileage_soc.xlsx  # single benchmark workbook (not committed)
```

## Backend (FastAPI) + Frontend (React)

The dashboard itself is `backend/` (reads the BigQuery tables above and serves REST JSON)
plus `frontend/` (a Vite/React app with real routed pages — `/`, `/buses`, `/trucks`,
`/customers` — instead of the old single-page tab show/hide). `backend/metrics.py` is a
near-verbatim port of `Analysis/generate_presentation_report.py`'s pandas logic (tenure,
active-day volatility, odometer resolution, distance-band crosstab, etc.) — see
`/home/yogesh/.claude/plans/swirling-baking-spark.md` for the full rationale and the specific
places it deliberately differs (dynamic dimension tables instead of hardcoded plate lists,
computed insight text instead of stale hardcoded prose, dynamic SoC-trajectory vehicle
sampling instead of a hardcoded plate list).

### Running it

```bash
# Terminal 1 -- backend
uv run uvicorn backend.main:app --reload --port 8000

# Terminal 2 -- frontend
cd frontend
npm install   # first time only
npm run dev   # http://localhost:5173, proxies /api -> localhost:8000
```

Backend config lives in the same root `.env` as the ingestion pipeline (`GCP_PROJECT_ID`,
`BQ_DATASET`, etc.) plus two optional additions: `BACKEND_CACHE_TTL_SECONDS` (default 300 —
how long BigQuery reads are cached in-process before the next request re-queries) and
`BACKEND_CORS_ORIGINS` (default allows the Vite dev server). After running the ingestion
pipeline, either wait for the cache TTL or `curl -X POST localhost:8000/api/admin/refresh-cache`
to see new data immediately.

### Layout

```
backend/
  main.py            # FastAPI app + CORS + router registration
  data_loader.py      # BigQuery reads with an in-process TTL cache
  metrics.py            # ported metric computation (see plan doc)
  routers/                # one file per endpoint group (kpi, customers, crosstab, vehicles, trajectories)
frontend/src/
  api/                  # typed fetch client + response types
  components/            # KpiCards, VehicleTable, Sparkline, CrosstabMatrix, chart wrappers
  pages/                   # OverviewPage, BusesPage/TrucksPage (share CategoryPage), CustomersPage
  global.css                # design tokens ported from report.html (light + dark theme)
```

One known rough edge: `react-plotly.js`/`plotly.js`'s CJS export shape doesn't survive Vite's
bundler interop cleanly (worked around in `frontend/src/plotly-shim.ts` with a defensive
unwrap) — if a future dependency bump breaks chart rendering again with an "Element type is
invalid" or "is not a function" error in the browser console, that shim is the first place to
look.

## Deployment

Netlify hosts the frontend fine (it's just a static build), but it's a poor fit for the
backend: Netlify Functions are stateless/short-lived (AWS Lambda-style), which defeats the
in-process caching `backend/data_loader.py` relies on, isn't a native fit for FastAPI's ASGI
interface, and our dependency tree (pandas/pyarrow/grpc) is large enough to strain a
serverless function's package size and cold-start budget. **Split it**: frontend on Netlify,
backend on Cloud Run (same GCP project as BigQuery already — no new credentials to manage,
see below).

### 1. Backend -> Cloud Run

Built and smoke-tested locally already (`Dockerfile` at the repo root, verified against real
BigQuery data). To actually deploy:

```bash
gcloud run deploy drivn-backend \
  --source . \
  --region asia-south1 \
  --allow-unauthenticated \
  --service-account YOUR_SERVICE_ACCOUNT@YOUR_PROJECT.iam.gserviceaccount.com \
  --set-env-vars GCP_PROJECT_ID=YOUR_PROJECT_ID,BQ_DATASET=telematics,BQ_LOCATION=asia-south1,BACKEND_CORS_ORIGINS=https://YOUR-SITE.netlify.app
```

- **Service account**: create one (`gcloud iam service-accounts create drivn-backend`) and
  grant it `BigQuery Data Viewer` + `BigQuery Job User` on the project
  (`gcloud projects add-iam-policy-binding ...`). Attaching it to the Cloud Run service means
  `bigquery.Client()`'s Application Default Credentials resolve automatically via Cloud Run's
  metadata server — same ADC mechanism as your local `gcloud auth application-default login`,
  just via an attached identity instead of a personal login. No key file, ever.
- **`--allow-unauthenticated`**: makes the API publicly reachable (needed since the browser
  calls it directly through Netlify's redirect). If you want it locked down instead, that's a
  bigger change (signed requests from Netlify, or an API gateway) — ask if you want that
  explored.
- `gcloud run deploy` prints the service URL (`https://drivn-backend-xxxxx-asia-south1.a.run.app`)
  on completion — you need that for step 2.
- Re-running the same command redeploys; Cloud Run keeps the previous revision serving traffic
  until the new one is healthy (zero-downtime by default).

### 2. Frontend -> Netlify

1. Put the Cloud Run URL from step 1 into `netlify.toml`'s `/api/*` redirect (replacing the
   `REPLACE-WITH-YOUR-CLOUD-RUN-URL` placeholder).
2. Connect the repo in Netlify's UI (or `netlify deploy` via the CLI) — it auto-detects
   `netlify.toml`'s `base = "frontend"` / build command / publish dir, no extra config needed.
3. Once you have the real `https://YOUR-SITE.netlify.app` URL, go back and update the backend's
   `BACKEND_CORS_ORIGINS` env var on Cloud Run to that value (`gcloud run services update
   drivn-backend --update-env-vars BACKEND_CORS_ORIGINS=https://YOUR-SITE.netlify.app`) — it's a
   bit chicken-and-egg on the very first deploy, but only needs fixing once.

### What doesn't change

`ingestion/` isn't part of either deployment — keep running `uv run ingest-utilization` etc.
locally (or wire it into a separate scheduled job later) exactly as before. The caching
behavior we tuned (backend §"Backend/frontend caching") works the same on Cloud Run as it did
locally, since Cloud Run keeps a container instance warm across requests instead of
cold-starting per-request the way Lambda-style functions do — that's the whole reason it's the
right home for this backend and Netlify Functions isn't.
