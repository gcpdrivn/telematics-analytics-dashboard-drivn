# Telematics Analytics Platform: Backend Data Engineering & Dynamic System Specification
**Target Dashboard:** `report.html` (Drivn Commercial EV Telematics Platform)  
**Document Status:** Final Technical Architecture & Data Engineering Specification  
**Document Version:** 1.0.0 (Production Blueprint)

---

## 1. Executive Overview & System Architecture

### 1.1 Objective
This document specifies the end-to-end data pipeline, mathematical formulation, entity relationships, and API contracts required to transform the current static presentation dashboard (`report.html`, generated via `generate_presentation_report.py`) into a fully dynamic, production-grade telemetry backend.

The dynamic system must ingest continuous raw IoT/telematics feeds, run automated data cleansing and deduplication, compute operational utilization and volatility metrics, and serve high-performance JSON payloads to interactive frontend visualizations (Plotly charts, cross-tabulation matrices, KPI strips, and vehicle rosters).

### 1.2 Architectural Topology

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                                   DATA INGESTION                                       │
│   ┌───────────────────────────────────┐    ┌───────────────────────────────────────┐   │
│   │   Raw Daily Telematics Feed       │    │      Digitalized Mileage (SoC)        │   │
│   │   (IoT Tracker / OBD Platform)    │    │      (Manual Benchmarks / BMS Tests)  │   │
│   └─────────────────┬─────────────────┘    └──────────────────┬────────────────────┘   │
└─────────────────────┼─────────────────────────────────────────┼────────────────────────┘
                      ▼                                         ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                              CLEANSING & DEDUPLICATION                                 │
│   • Parse Timestamps & Standardize Plates (Strip CAM suffixes, whitespace)             │
│   • CAM Suffix Hardware Resolution (Drop CAM duplicates if primary device exists)      │
│   • Composite Key Deduplication: (Base License Plate, Report Date)                     │
│   • Customer Entity Resolution (FreshBus, ZingBus, BillionE)                           │
│   • Vehicle Classification Clubbing ('Heavy Puller' -> 'Truck')                        │
│   • Sensor Calibration Filter (Mileage_Km_per_SoC >= 10.0 treated as NULL)             │
└─────────────────────────────────────┬──────────────────────────────────────────────────┘
                                      ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                            CORE METRICS COMPUTATION ENGINE                             │
│   • Fleet Tenure Resolution: 30-day fixed (Bus) vs. First-Reading Tenure (Truck)       │
│   • Active Day Definition: Distance > 0 km                                             │
│   • Active Utilization & Availability: Active Days / Total Fleet Tenure                │
│   • Operating Density: Distance / Active Days & Running Hours / Active Days            │
│   • Standardized Volatility Engine: Sample std (ddof=1) & CV% on Active Days Only      │
│   • Dynamic Eligible Fleet Denominators (Time-varying active fleet fractions)          │
│   • Distance Compartment Categorization (0 km, 1–200, 201–400, 401–600, 601–800, 800+) │
│   • Closing Odometer Selection (Chronological fallback from Closing to Opening Odo)   │
└─────────────────────────────────────┬──────────────────────────────────────────────────┘
                                      ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                         ANALYTICAL STORAGE & MATERIALIZATION                           │
│   ┌───────────────────────────────────┐    ┌───────────────────────────────────────┐   │
│   │   Fact & Dimension Tables         │    │   Pre-aggregated Materialized Views   │   │
│   │   (PostgreSQL / ClickHouse)       │    │   (Redis Cache / In-memory Aggs)      │   │
│   └─────────────────┬─────────────────┘    └──────────────────┬────────────────────┘   │
└─────────────────────┼─────────────────────────────────────────┼────────────────────────┘
                      ▼                                         ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                              DYNAMIC REST API GATEWAY                                  │
│   • GET /api/v1/telematics/kpi-summary?scope={all|Bus|Truck|customers}                │
│   • GET /api/v1/telematics/customers/overview                                          │
│   • GET /api/v1/telematics/charts/customer-analytics                                   │
│   • GET /api/v1/telematics/charts/crosstab-matrix?customer={All|FreshBus|...}          │
│   • GET /api/v1/telematics/vehicles?category={all|Bus|Truck}&sort_by=active_rate_pct   │
│   • GET /api/v1/telematics/vehicles/{plate_id}/duty-history                            │
└─────────────────────────────────────┬──────────────────────────────────────────────────┘
                                      ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                               FRONTEND CLIENT CONSUMER                                 │
│   Dynamic UI (`report.html` SPA): Dynamic Top KPI Cards, Plotly Visualizations,       │
│   200 km Daily Fleet Dispersion Cross-Tab Matrix, and Vehicle Duty Inspector Drawer    │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Raw Source Data Inventory & Ingestion Schemas

The telemetry system currently processes two primary source datasets located in `extracted_sheets/`.

### 2.1 Dataset 1: Daily Vehicle Utilization Telemetry
* **Source File:** `extracted_sheets/utilization_report_10Aug_8Sep.xlsx` (or `.parquet` equivalent)
* **Granularity:** One record per vehicle per calendar day (subject to device duplication).
* **Observation Window:** 30 Calendar Days (`2026-08-09` to `2026-09-07`).
* **Total Raw Rows:** 1,187 records.

#### Column-by-Column Data Dictionary

| # | Column Name in Raw File | Data Type | Nullable | Business Definition | Sample Value | Extraction / Downstream Usage |
|---|---|---|---|---|---|---|
| 1 | `License Plate / Vehicle Number` | String | No | Raw telematics device vehicle identifier. May contain `'CAM'` suffix if connected to auxiliary camera/dashcam OBD. | `'AP39WN7281'`, `'MH02GS4332-CAM'` | Used in CAM deduplication logic (`is_cam`). |
| 2 | `Distance` | Float | No | Total GPS/CAN distance traveled by the vehicle on the specified report date in kilometers. | `718.15`, `0.0` | **Primary Metric**. Used in active days calculation, avg daily km, volatility CV%, and crosstab compartments. |
| 3 | `Vehicle Name` | String | Yes | Fleet internal short asset code. | `'7281'`, `'501'` | Informational / Secondary metadata. |
| 4 | `Running Time (in hours)` | Float | Yes | Total duration ignition was ON and vehicle was in motion. | `18.53`, `0.0` | **Primary Metric**. Used to calculate total operating hours and average operating hours per active day. |
| 5 | `Group Name` | String | Yes | Telematics hardware grouping. | `'OBD'`, `'Default'` | System metadata. |
| 6 | `GPS Disconnection count` | Integer | Yes | Count of hardware GPS antenna disconnection events. | `0`, `3` | Telematics reliability indicator. |
| 7 | `Vehicle Type` | String | No | Raw asset classification category from telematics platform. | `'Bus'`, `'Heavy Puller'`, `'Truck'` | Mapped via clubbing: `'Heavy Puller'` $\rightarrow$ `'Truck'`. |
| 8 | `Vehicle Maker` | String | Yes | Manufacturer identifier from telematics system. | `'Bus'`, `'Tata'` | Informational metadata. |
| 9 | `Vehicle Model` | String | Yes | Vehicle chassis / model string. | `'A12.5C'`, `'Tata 55T'` | Displayed in Vehicle Performance tables and Duty Inspector cards. |
| 10 | `Avg. Daily Operating Time (in hours)` | Float | Yes | Running average operating time reported by telematics system. | `21.28` | Not directly used (recalculated from raw running hours). |
| 11 | `Stoppages Count` | Integer | Yes | Number of discrete vehicle stoppage events during the day. | `4`, `12` | Informational operational metric. |
| 12 | `Max Speed` | Float | Yes | Maximum instantaneous GPS speed recorded in km/h. | `93.0`, `68.5` | Aggregated per vehicle (`max_speed = max(Max Speed)`). |
| 13 | `Start Location` | String | Yes | Reverse-geocoded address of the first ignition ON event. | `'Guntur Municipal Corporation...'` | Spatial tracking metadata. |
| 14 | `End Location` | String | Yes | Reverse-geocoded address of the last ignition OFF event. | `'NH 16 Service Road, Koppuravuru...'` | Spatial tracking metadata. |
| 15 | `Average Speed` | Float | Yes | Harmonic / arithmetic mean speed while in motion in km/h. | `35.9`, `0.0` | Aggregated over positive speeds (`avg_speed = mean(Average Speed > 0)`). |
| 16 | `Start Date` | Datetime | Yes | Timestamp of initial vehicle movement on report date. | `2026-08-23 00:00:00` | Operational scheduling log. |
| 17 | `End Date` | Datetime | Yes | Timestamp of final trip completion on report date. | `2026-08-23 21:45:39` | Operational scheduling log. |
| 18 | `Closing Odometer` | Float | Yes | End-of-day cumulative vehicle odometer reading in km. | `109695.0`, `NaN` | **Primary Metric**. Used in final cumulative odometer resolution. |
| 19 | `Opening Odometer` | Float | Yes | Start-of-day cumulative vehicle odometer reading in km. | `108977.0`, `NaN` | Secondary fallback for final odometer if Closing Odometer is missing. |
| 20 | `Report Date` | Datetime | No | Calendar date of telemetry aggregation window (00:00:00). | `2026-08-23 00:00:00` | **Primary Partitioning Key**. Used for time-series, DoW, and cross-tab matrix. |
| 21 | `Source File` | String | Yes | Originating batch file ingestion log identifier. | `'Utilization report-4086...xls'` | Data lineage metadata. |
| 22 | `Base License Plate` | String | No | Cleaned vehicle registration plate without hardware device suffixes. | `'AP39WN7281'`, `'MH02GS4332'` | **Primary Vehicle Foreign Key**. Join key for customer, mileage, and grouping. |

---

### 2.2 Dataset 2: Digitalized Vehicle Mileage & Energy Efficiency
* **Source File:** `extracted_sheets/vehicle_mileage_soc.xlsx`
* **Granularity:** One record per vehicle license plate.
* **Total Rows:** 16 records.

#### Column-by-Column Data Dictionary

| # | Column Name | Data Type | Nullable | Business Definition | Sample Value | Handling / Outlier Rules |
|---|---|---|---|---|---|---|
| 1 | `Vehicle_Short_No` | Integer | Yes | Short asset fleet number. | `7306`, `9284` | Informational identifier. |
| 2 | `License_Plate` | String | No | Primary vehicle registration number. | `'AP39WN7306'`, `'DL1PD9284'` | Join key to `Base License Plate`. |
| 3 | `Customer` | String | No | Operating fleet customer name. | `'FreshBus'`, `'ZingBus'` | Customer verification key. |
| 4 | `OEM` | String | Yes | Bus / Truck Body Builder / Powertrain OEM. | `'Azad'`, `'JBM'` | Displayed in customer cards and tables. |
| 5 | `Mileage_Km_per_SoC` | Float | Yes | Energy efficiency metric: Kilometers delivered per 1% of State-of-Charge (SoC) consumption ($km / \% \Delta SoC$). | `5.5`, `3.2`, `12.5` | **Critical Validation Rule:** Values $\ge 10.0$ (such as `12.5` recorded due to BMS reset/calibration failure) are treated as invalid and **set to `NULL` / `NaN`**. |
| 6 | `Status` | String | Yes | Operational validation status of the bench test. | `'Valid'`, `'Pending Verification'` | Ingestion audit log. |
| 7 | `Raw_Note` | String | Yes | Field technician operational comments. | `'5.5'`, `'Under review'` | Audit metadata. |

---

## 3. Data Cleansing, Normalization & Filtering Pipeline

The dynamic backend ingestion pipeline must execute the following deterministic transformations in strict sequence:

```
Raw Telematics Ingestion
         │
         ▼
[Step 1: CAM Suffix & Dual-Device Deduplication]
         │
         ▼
[Step 2: Primary Composite Key Deduplication]
         │
         ▼
[Step 3: Customer Scope Filtering & Plate Attribution]
         │
         ▼
[Step 4: Vehicle Type Normalization (Heavy Puller -> Truck)]
         │
         ▼
[Step 5: Energy Sensor Mileage Validation (< 10.0 km/%SoC)]
         │
         ▼
Cleaned Telemetry Fact Stream (df_clean)
```

### Step 1: CAM Suffix & Hardware Device Deduplication
In fleets equipped with dual telematics hardware (e.g. primary telematics gateway plus an auxiliary dashcam unit), two rows are produced for the same `Base License Plate` on the same `Report Date`.
Auxiliary dashcam units append `-CAM` or `CAM` to the `License Plate / Vehicle Number`.

```python
# Identification of CAM records
is_cam = df['License Plate / Vehicle Number'].astype(str).str.contains('CAM', case=False)

# Check if an alternative non-CAM record exists for the same plate and date
has_alternative = df.groupby(['Base License Plate', 'Report Date'])['License Plate / Vehicle Number'].transform(
    lambda s: (~s.astype(str).str.contains('CAM', case=False)).any()
)

# Filter: Drop CAM record ONLY IF a non-CAM primary device record exists
df_clean = df[~(is_cam & has_alternative)].copy()
```

### Step 2: Primary Composite Key Deduplication
After resolving CAM conflicts, enforce record uniqueness on the composite key `(Base License Plate, Report Date)`:
```python
df_clean = df_clean.drop_duplicates(subset=['Base License Plate', 'Report Date'], keep='first')
```

### Step 3: Customer Entity Resolution & Scope Filtering
The commercial report is strictly scoped to the 3 major commercial enterprise fleet customers. Non-commercial or pilot test accounts (`Enviiiro`, `Flytta`) are strictly excluded.

#### Customer Plate Attribution Dictionary:
1. **FreshBus (10 Electric Intercity Buses):**
   * Plates: `AP39WN7273`, `AP39WN7275`, `AP39WN7276`, `AP39WN7280`, `AP39WN7281`, `AP39WN7301`, `AP39WN7302`, `AP39WN7305`, `AP39WN7306`, `AP39WN7322`
   * OEM: `Azad (Bus)`
   * Operating Corridors: `Guntur - Hyderabad, Guntur - Vizag`
2. **ZingBus (10 Electric Intercity Buses):**
   * Plates: `DL1PD9284`, `DL1PD9369`, `DL1PD9317`, `DL1PD9309`, `DL1PD8669`, `DL1PD8652`, `HR55AY7626`, `HR55AY9237`, `DL1PD8523`, `DL1PD8509` (and legacy identifier `DL01PD9317` resolving to `DL1PD9317`)
   * OEM: `JBM / Azad (Bus)`
   * Operating Corridors: `Delhi - Dehradun, Delhi - Amritsar`
3. **BillionE (20 Heavy Commercial Electric Trucks):**
   * Rule: All active assets whose `Base License Plate` begins with `'MH02'` (e.g., `MH02GS4233`, `MH02GS4332`, `MH02GS4334`, etc.)
   * OEM: `Tata Motors (55T Heavy Puller/Truck)`
   * Operating Corridors: `Rajasthan - Surat`

```python
# Apply filter: retain strictly FreshBus, ZingBus, and BillionE
df_clean = df_clean[df_clean['Customer'].isin(['FreshBus', 'ZingBus', 'BillionE'])].copy()
```

### Step 4: Vehicle Classification Normalization
Heavy industrial electric haulers are classified inconsistently in raw telematics feeds as `'Heavy Puller'` or `'Truck'`. The backend must normalize all `'Heavy Puller'` designations to `'Truck'`:
```python
# Derive modal vehicle type per plate, then club
veh_type_raw = df_clean.groupby('Base License Plate')['Vehicle Type'].agg(lambda s: s.mode()[0])
veh_type_clubbed = veh_type_raw.apply(lambda t: 'Truck' if t == 'Heavy Puller' else t)
```

### Step 5: Mileage Sensor Outlier Exclusion
In `vehicle_mileage_soc.xlsx`, any reading $\ge 10.0\text{ km/\% SoC}$ represents a telemetry communication error / division by zero in the vehicle CAN bus adapter during rapid charging.
```python
mileage_df.loc[mileage_df['Mileage_Km_per_SoC'] >= 10.0, 'Mileage_Km_per_SoC'] = np.nan
valid_mileage = mileage_df.dropna(subset=['Mileage_Km_per_SoC']).set_index('License_Plate')['Mileage_Km_per_SoC'].to_dict()
```

---

## 4. Intermediate Metrics & Mathematical Calculation Engine

Every KPI, table column, and chart coordinate displayed on the dashboard is derived from the cleaned telemetry fact stream. The mathematical definitions below must be implemented in the backend query/aggregation layer.

### 4.1 Fleet Tenure vs. Calendar Observation Window ($\text{total\_days}_v$)

$$\text{total\_days}_v = \begin{cases} 30.0 & \text{if } \text{vehicle\_type}_v = \text{'Bus'} \\ (\text{end\_date} - \text{first\_telemetry\_date}_v) + 1 & \text{if } \text{vehicle\_type}_v = \text{'Truck'} \end{cases}$$

* **Operational Rationale:**
  * **Buses:** Operating on established intercity passenger concessions throughout the entire 30-day window (`2026-08-09` to `2026-09-07`). Fixed denominator = $30.0$ calendar days.
  * **Trucks:** Heavy freight fleet undergoing progressive industrial deployment and phased commissioning. Calculating active availability against a static 30-day window penalizes newly onboarded vehicles that only entered the fleet mid-month. Tenure is measured from the vehicle's first active transmission date ($\text{first\_telemetry\_date}_v$) to $\text{end\_date}$ (`2026-09-07`).

---

### 4.2 Active Operating Days ($\text{active\_days}_v$)

$$\text{active\_days}_v = \sum_{t=1}^{T} \mathbb{I}(d_{v,t} > 0)$$

Where:
* $d_{v,t}$ is the distance recorded by vehicle $v$ on date $t$.
* $\mathbb{I}(\cdot)$ is the indicator function ($\mathbb{I}=1$ if $d_{v,t} > 0$, else $0$).
* Telemetry records where $d_{v,t} = 0$ (vehicle parked in yard, maintenance holding, or charging) do **not** increment active days.

---

### 4.3 Active Availability Rate ($\text{active\_rate\_pct}_v$) & Alert Threshold

$$\text{active\_rate\_pct}_v = \left( \frac{\text{active\_days}_v}{\text{total\_days}_v} \right) \times 100\%$$

* **Alert Condition (`is_below_80`):**
  $$\text{is\_below\_80}_v = \begin{cases} \text{TRUE} & \text{if } \text{active\_rate\_pct}_v < 80.0\% \\ \text{FALSE} & \text{otherwise} \end{cases}$$
* **UI Action:** Vehicles triggering `is_below_80 = TRUE` are rendered with an alert badge (`⚠️ XX.X%`) and highlighted with warning background styling.
* **Empirical Fleet Benchmark:** Exactly 6 of the 40 active vehicles fall below 80% active availability:
  1. `MH02GS4334` (BillionE Truck): $2 / 29\text{ days} = 6.9\%$
  2. `MH02GS4233` (BillionE Truck): $10 / 22\text{ days} = 45.5\%$
  3. `MH02GS4332` (BillionE Truck): $17 / 30\text{ days} = 56.7\%$
  4. `DL1PD8509` (ZingBus Bus): $19 / 30\text{ days} = 63.3\%$
  5. `DL1PD8523` (ZingBus Bus): $19 / 30\text{ days} = 63.3\%$
  6. `HR55AY9237` (ZingBus Bus): $23 / 30\text{ days} = 76.7\%$

---

### 4.4 Active Daily Operating Intensity (Distance & Hours)

$$\text{avg\_km\_per\_day}_v = \frac{\sum_{t} d_{v,t}}{\text{active\_days}_v} = \frac{\text{total\_distance}_v}{\text{active\_days}_v}$$

$$\text{avg\_hours\_per\_day}_v = \frac{\sum_{t} h_{v,t}}{\text{active\_days}_v} = \frac{\text{total\_hours}_v}{\text{active\_days}_v}$$

* **Critical Implementation Rule:** The denominator is strictly $\text{active\_days}_v$ (days when the vehicle actually turned wheels), **not** total calendar days. This ensures that operational driving intensity on active duty days is not diluted by planned depot maintenance or non-dispatch days.

---

### 4.5 Standardized Active Volatility Engine (Coefficient of Variation)

To measure route stability and dispatch predictability without penalizing planned maintenance rest days, volatility is computed **strictly across days with positive distance ($d_{v,t} > 0$)**:

Let $D_v = \{ d_{v,t} \mid d_{v,t} > 0 \}$ be the set of active distance records for vehicle $v$, and $n_v = |D_v| = \text{active\_days}_v$.

1. **Active Mean Distance ($\mu_v$):**
   $$\mu_v = \frac{1}{n_v} \sum_{d \in D_v} d$$

2. **Active Sample Standard Deviation ($\sigma_v$ with Bessel's Correction $ddof=1$):**
   $$\sigma_v = \begin{cases} \sqrt{\frac{1}{n_v - 1} \sum_{d \in D_v} (d - \mu_v)^2} & \text{if } n_v > 1 \\ 0.0 & \text{if } n_v \le 1 \end{cases}$$

3. **Coefficient of Variation ($CV_v$):**
   $$CV_v = \begin{cases} \left( \frac{\sigma_v}{\mu_v} \right) \times 100\% & \text{if } \mu_v > 0 \text{ and } n_v > 1 \\ 0.0 & \text{otherwise} \end{cases}$$

4. **Volatility Tier Classification:**
   $$\text{Volatility Tier} = \begin{cases} 
   \text{'Stable'} & (CV_v < 25.0\%) \implies \text{CSS Class: } \texttt{vol-low} \\ 
   \text{'Moderate'} & (25.0\% \le CV_v < 50.0\%) \implies \text{CSS Class: } \texttt{vol-mid} \\ 
   \text{'Volatile'} & (CV_v \ge 50.0\%) \implies \text{CSS Class: } \texttt{vol-high} \\ 
   \text{'Single Day'} & (n_v = 1) \implies \text{CSS Class: } \texttt{vol-neutral} 
   \end{cases}$$

---

### 4.6 Energy Efficiency & Battery SoC Consumption Estimation

$$\text{est\_daily\_soc\_pct}_v = \frac{\text{avg\_km\_per\_day}_v}{\text{mileage\_km\_soc}_v}$$

Where:
* $\text{mileage\_km\_soc}_v$ is the validated bench efficiency ($km$ delivered per $1\% \Delta SoC$) from `vehicle_mileage_soc.xlsx`.
* If $\text{mileage\_km\_soc}_v$ is missing, null, or $\ge 10.0$, $\text{est\_daily\_soc\_pct}_v$ must evaluate to `NULL` (rendered as `'—'`).

---

### 4.7 Chronological Final Odometer Resolution

$$\text{final\_odometer}_v = \begin{cases} 
\text{Closing Odometer of } \max(ReportDate) \text{ where Closing Odometer is NOT NULL} \\ 
\text{Opening Odometer of } \max(ReportDate) \text{ where Opening Odometer is NOT NULL} \quad (\text{fallback}) \\ 
\text{NULL} \quad (\text{if no odometer telemetry exists}) 
\end{cases}$$

* Values are rounded to the nearest integer in the UI (`formatIn(final_odometer, 0) + ' km'`).

---

### 4.8 Daily Distance Compartment Indexing (Fleet Dispersion)

The system categorizes daily vehicle distances into operating distance compartments. **0 km is not separated into an isolated box; ranges start at 0–200 km (buses) or 0–150 km (trucks).**

#### A. Commercial Bus Fleet & Overall Fleet (`All`, `FreshBus`, `ZingBus`): 200 km Bins
| Band Index (`b_idx`) | Compartment ID | Distance Range ($d$) | Display Label | Operational Meaning |
|---|---|---|---|---|
| `0` | `b0` | $0 \le d \le 200.0\text{ km}$ | `0–200 km` | Yard Stoppage, Maintenance, Local Shunting, Partial Duty |
| `1` | `b1` | $200.0 < d \le 400.0\text{ km}$ | `201–400 km` | Regional Short-Haul / Single Corridor Loop |
| `2` | `b2` | $400.0 < d \le 600.0\text{ km}$ | `401–600 km` | Standard Line-Haul / Medium Intercity Transit |
| `3` | `b3` | $600.0 < d \le 800.0\text{ km}$ | `601–800 km` | Extended Double-Shift Line-Haul |
| `4` | `b4` | $d > 800.0\text{ km}$ | `800+ km` | Ultra Long-Haul / Rapid Intercity Relay |

#### B. Commercial Heavy Truck Fleet (`BillionE`): 150 km Bins (Max Value 600 km)
| Band Index (`b_idx`) | Compartment ID | Distance Range ($d$) | Display Label | Operational Meaning |
|---|---|---|---|---|
| `0` | `b0` | $0 \le d \le 150.0\text{ km}$ | `0–150 km` | Loading Dock Holding, Port Drayage, Local Shunting |
| `1` | `b1` | $150.0 < d \le 300.0\text{ km}$ | `151–300 km` | Short-Haul Industrial Route |
| `2` | `b2` | $300.0 < d \le 450.0\text{ km}$ | `301–450 km` | Medium-Haul Line Freight (Single Driver Shift) |
| `3` | `b3` | $450.0 < d \le 600.0\text{ km}$ | `451–600 km` | Peak Heavy Line-Haul (Rajasthan–Surat Corridor, Max 600 km) |


---

### 4.9 Day-of-Week Seasonality Decomposition

For each customer account $C \in \{\text{FreshBus}, \text{ZingBus}, \text{BillionE}\}$ and day-of-week $W \in \{\text{Mon}, \text{Tue}, \text{Wed}, \text{Thu}, \text{Fri}, \text{Sat}, \text{Sun}\}$:

$$\text{mean\_dist}_{C,W} = \frac{1}{|D_{C,W}|} \sum_{d \in D_{C,W}} d \quad (d > 0)$$

$$\text{std\_dist}_{C,W} = \sqrt{\frac{1}{|D_{C,W}| - 1} \sum_{d \in D_{C,W}} (d - \text{mean\_dist}_{C,W})^2}$$

$$\text{volatility\_pct}_{C,W} = \left( \frac{\text{std\_dist}_{C,W}}{\text{mean\_dist}_{C,W}} \right) \times 100\%$$

---

### 4.10 Dynamic Active Availability Timeline with Fleet Expansion Denominators

For each observation date $t \in [t_1, t_{30}]$:
1. **Active Vehicle Count:**
   $$\text{active\_count}_{C,t} = \left| \{ v \in \text{Fleet}_C \mid d_{v,t} > 0 \} \right|$$

2. **Eligible Fleet Denominator ($\text{eligible\_size}_{C,t}$):**
   * **For FreshBus & ZingBus:** Fixed $\text{eligible\_size}_{C,t} = 10$.
   * **For BillionE:** Dynamic tenure expansion:
     $$\text{eligible\_size}_{\text{BillionE},t} = \left| \{ v \in \text{Fleet}_{\text{BillionE}} \mid \text{first\_telemetry\_date}_v \le t \} \right|$$
   * **For Fleet Total:**
     $$\text{eligible\_size}_{\text{Total},t} = 10 + 10 + \text{eligible\_size}_{\text{BillionE},t}$$

3. **Daily Active Percentage:**
   $$\text{pct}_{C,t} = \left( \frac{\text{active\_count}_{C,t}}{\text{eligible\_size}_{C,t}} \right) \times 100\%$$

---

### 4.11 Sparkline SVG Vector Path Generation Algorithm

For each vehicle $v$, an inline SVG path of dimensions $96\text{px} \times 22\text{px}$ is dynamically rendered across the ordered list of dates $T = [t_1, t_2, \dots, t_{30}]$:

```python
def make_sparkline_svg(values, width=96, height=22):
    if not values or max(values) == 0:
        return f'<svg width="{width}" height="{height}" class="sparkline-svg"><line x1="0" y1="{height/2}" x2="{width}" y2="{height/2}" stroke="#94a3b8" stroke-width="1.5" stroke-dasharray="2,2"/></svg>'
    
    max_val = max(values)
    pts = []
    n = len(values)
    dx = (width - 4) / (n - 1) if n > 1 else width
    for i, v in enumerate(values):
        x = 2 + i * dx
        y = height - 3 - (v / max_val) * (height - 6) if max_val > 0 else height / 2
        pts.append(f"{x:.1f},{y:.1f}")
        
    polyline = " ".join(pts)
    first_pt = pts[0].split(',')
    last_pt = pts[-1].split(',')
    area_pts = f"{first_pt[0]},{height-1} " + polyline + f" {last_pt[0]},{height-1}"
    
    return (
        f'<svg width="{width}" height="{height}" class="sparkline-svg" viewBox="0 0 {width} {height}">'
        f'<polygon fill="url(#spk-grad)" points="{area_pts}"/>'
        f'<polyline fill="none" stroke="var(--accent-brand)" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" points="{polyline}"/>'
        f'</svg>'
    )
```

---

## 5. Component-by-Component Frontend Data Dependency Matrix

This section provides the exact mapping connecting backend calculations to every DOM ID, Plotly chart, and table in `report.html`.

### 5.1 Top Fleet KPI Summary Cards

The dashboard features four top-level KPI cards that dynamically switch based on the active tab scope (`all`, `Bus`, `Truck`, `customers`).

| DOM Element ID | Scope = `all` (Total Fleet) | Scope = `Bus` (Buses Only) | Scope = `Truck` (Trucks Only) | Scope = `customers` (Account View) |
|---|---|---|---|---|
| **Card 1: Daily Distance** | | | | |
| `#kpi-label-dist` | `"Total Fleet Distance / Day"` | `"Bus Fleet Distance / Day"` | `"Truck Fleet Distance / Day"` | `"Commercial Fleet Distance / Day"` |
| `#kpi-val-dist` | $\text{round}(\sum d / 30) = 14,244\text{ km}$ | $\text{round}(\sum d_{\text{bus}} / 30) = 10,795\text{ km}$ | $\text{round}(\sum d_{\text{trk}} / 30) = 3,449\text{ km}$ | $\text{round}(\sum d_{\text{cust}} / 30) = 14,227\text{ km}$ |
| `#kpi-sub-dist` | `⚡ 454 km / day / vehicle` | `⚡ 539 km / day / bus` | `⚡ 238 km / day / truck` | `⚡ 454 km / day / vehicle` |
| `#kpi-prior-dist` | `Total: 4,27,314 km` | `Total: 3,23,837 km` | `Total: 1,03,477 km` | `Total: 4,26,811 km` |
| **Card 2: Daily Hours** | | | | |
| `#kpi-label-hours` | `"Total Operating Time / Day"` | `"Bus Operating Time / Day"` | `"Truck Operating Time / Day"` | `"Commercial Operating Time / Day"` |
| `#kpi-val-hours` | $385.1\text{ hrs}$ | $302.2\text{ hrs}$ | $82.9\text{ hrs}$ | $384.8\text{ hrs}$ |
| `#kpi-sub-hours` | `⏱️ 12.3 hrs / day / vehicle` | `⏱️ 15.1 hrs / day / bus` | `⏱️ 5.7 hrs / day / truck` | `⏱️ 12.3 hrs / day / vehicle` |
| `#kpi-prior-hours` | `Total: 11,552.2 hrs` | `Total: 9,065.1 hrs` | `Total: 2,487.1 hrs` | `Total: 11,544.6 hrs` |
| **Card 3: Active Fleet Size** | | | | |
| `#kpi-label-fleet` | `"Active Commercial Fleet"` | `"Active Bus Fleet"` | `"Active Truck Fleet"` | `"Total Commercial Fleet"` |
| `#kpi-val-fleet` | `40` | `20` | `20` | `40` |
| `#kpi-sub-fleet` | `20 Buses • 20 Trucks` | `20 Buses (FreshBus: 10, ZingBus: 10)` | `20 Trucks (BillionE: 20 Tata 55T)` | `20 Buses • 20 Trucks` |
| `#kpi-prior-fleet` | `40 Active Commercial Assets` | `100% Intercity Commercial Fleet` | `20 Active Commercial Trucks` | `40 Active Commercial Assets` |
| **Card 4: Peak Asset** | | | | |
| `#kpi-label-top` | `"Peak Active Vehicle"` | `"Peak Active Bus"` | `"Peak Active Truck"` | `"Top Customer Account"` |
| `#kpi-val-top` | `AP39WN7281` | `AP39WN7281` | `MH02GS4335` | `FreshBus` |
| `#kpi-sub-top` | `753 km/d • 30/30 d (100%)` | `753 km/d • 30/30 d (100%)` | `378 km/d • 29/29 d (100%)` | `1,99,529 km • 665 km/d per veh` |
| `#kpi-prior-top` | `Total: 22,576 km` | `Total: 22,576 km` | `Total: 10,958 km` | `19.4 hrs / day / bus • 30/30 d Active` |

---

### 5.2 Customer Telematics Analytics Visualizations (Tab: `#section-customers`)

#### 1. Daily Distance Trajectory (`#chart-cust-daily`)
* **Plotly Chart Type:** Grouped / Categorical Vertical Bar (`bar`).
* **Source Data Field:** `reportData.customers_all[].avg_km_day_per_veh`.
* **X-Axis:** `['FreshBus', 'ZingBus', 'BillionE']`.
* **Y-Axis:** `[665.1, 412.3, 237.9]`.
* **Text Overlay:** `Math.round(v) + ' km/d'`.
* **Color Palette:** FreshBus: `#5cb030`, ZingBus: `#0284c7`, BillionE: `#d97706`.

#### 2. Fleet Distance Share (`#chart-cust-share`)
* **Plotly Chart Type:** Single-axis Horizontal Stacked Progress Bar (`bar`, `orientation: 'h'`).
* **Source Data Field:** `reportData.customers_all[].share_pct`.
* **Traces (3 Traces):**
  * FreshBus: $X = [53.3\%]$, Color: `#5cb030`
  * ZingBus: $X = [29.8\%]$, Color: `#0284c7`
  * BillionE: $X = [16.9\%]$, Color: `#d97706`
* **Layout:** `barmode: 'stack'`, `xaxis: { range: [0, 100], ticksuffix: '%' }`.

#### 3. Dispatch Variance & Distance Dispersion Boxplot (`#chart-cust-boxplot`)
* **Plotly Chart Type:** Box Plot (`box`).
* **Source Data Field:** `reportData.customer_box_data[cust]` (Array of individual positive daily distance runs $d > 0$).
  * FreshBus: 300 active run records ($N=10\times 30$).
  * ZingBus: 266 active run records.
  * BillionE: 247 active run records.
* **Trace Parameters:** `boxpoints: 'outliers'`, `jitter: 0.28`, `pointpos: -1.6`.
* **Y-Axis:** Active Daily Distance in km.

#### 4. Day-of-Week Operational Rhythm (`#chart-cust-seasonality`)
* **Plotly Chart Type:** Multi-trace Scatter Plot with Error Bars (`scatter`, `mode: 'lines+markers'`).
* **Source Data Field:** `reportData.dow_profiles[cust]`.
* **X-Axis:** `['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']`.
* **Y-Axis:** `dow_profiles[cust][].mean_dist` (Mean active km for that day of week).
* **Error Bars (`error_y`):** `dow_profiles[cust][].std_dist` ($\pm \sigma$).
* **Hover Text:** Includes Mean km/d, Daily KM Volatility $\pm \sigma$ km, CV%, and record count.

#### 5. Daily Active Commercial Vehicle Percentage Timeline (`#chart-cust-active-timeline`)
* **Plotly Chart Type:** Time-Series Line with Markers (`scatter`, `mode: 'lines+markers'`).
* **Source Data Field:** `reportData.active_timeline`.
* **X-Axis:** 30 dates formatted as `'DD Mon'` (`'09 Aug'` to `'07 Sep'`).
* **Y-Axis:** `active_timeline[cust].pcts` (Daily Active Availability % with dynamic tenure denominators).
* **Y-Range:** `[0, 105]`, `ticksuffix: '%'`.
* **Hover Text:** Formatted with Active count / Total eligible fleet, inactive count, and tenure onboarding notes.

---

### 5.3 Daily Distance Compartments & Fleet Dispersion Cross-Tab Matrix (`#crosstab-matrix-wrapper`)

The cross-tabulation matrix presents a heat-mapped grid displaying the distribution of vehicles across operating distance bands for each calendar date.
* **Bus Fleet & Overall View (`All`, `FreshBus`, `ZingBus`):** 5 rows (`800+ km`, `601–800 km`, `401–600 km`, `201–400 km`, `0–200 km`).
* **Truck Fleet View (`BillionE`):** 4 rows (`451–600 km`, `301–450 km`, `151–300 km`, `0–150 km`) with 150 km bin increments up to a max value of 600 km.

#### Dynamic Matrix Header KPIs (`#km-matrix-kpis`):
Updates dynamically when the user clicks customer filter buttons (`All`, `FreshBus`, `ZingBus`, `BillionE`):
* **Evaluated Vehicle Runs:** `custData.total_runs` (Total vehicle-days: All: 1,021, FreshBus: 300, ZingBus: 275, BillionE: 446).
* **Long-Haul Share:**
  * Buses / All (`>600 km`): $\text{long\_share} = \frac{\text{counts}[3] + \text{counts}[4]}{\text{total\_runs}} \times 100\%$.
  * Trucks (`>450 km`): $\text{long\_share} = \frac{\text{counts}[3]}{\text{total\_runs}} \times 100\%$.
* **Mid-Haul Share:**
  * Buses / All (`200–600 km`): $\text{mid\_share} = \frac{\text{counts}[1] + \text{counts}[2]}{\text{total\_runs}} \times 100\%$.
  * Trucks (`150–450 km`): $\text{mid\_share} = \frac{\text{counts}[1] + \text{counts}[2]}{\text{total\_runs}} \times 100\%$.
* **Short-Haul Share:**
  * Buses / All (`≤200 km`): $\text{short\_share} = \frac{\text{counts}[0]}{\text{total\_runs}} \times 100\%$.
  * Trucks (`≤150 km`): $\text{short\_share} = \frac{\text{counts}[0]}{\text{total\_runs}} \times 100\%$.
* **Peak Daily Vehicle Run:** `custData.peak_run` (Max distance run in dataset, e.g. `AP39WN7281 • 792 km • 27-Aug (FreshBus)`).

#### Cross-Tab Grid Cell Formulation (`#crosstab-table`):
For Row $b \in [0..\text{bands.length}-1]$ (rendered top-to-bottom in descending distance order) and Column date $t \in [t_{30}..t_1]$ (**ordered latest on left to oldest on right**):
* **Date Ordering:** Columns are sorted in descending chronological sequence: leftmost column is latest date (`07 Sep (Mon)`), rightmost column is oldest date (`09 Aug (Sun)`).
* **Total Evaluated Vehicles:** $N_t = \text{total vehicles evaluated on date } t$.
* **Compartment Vehicle Count:** $C_{b,t} = \text{number of vehicles whose distance falls in band } b$.
* **Compartment Percentage:** $P_{b,t} = \frac{C_{b,t}}{N_t} \times 100\%$.
* **Cell Background Color (Dynamic Alpha Fill):**
  $$\text{RGBA}(\text{band\_r, band\_g, band\_b}, \alpha) \quad \text{where } \alpha = \max\left(0.12, \frac{P_{b,t}}{100.0} \times 0.95\right)$$
* **Cell Border Color:** $\text{RGBA}(\text{band\_r, band\_g, band\_b}, \min(1.0, \alpha + 0.35))$.
* **Cell Display Value:** If $P_{b,t} > 0$, display $P_{b,t}\text{ (e.g. } \mathbf{70\%})$ and vehicle count ($7\text{ veh}$); if $0\%$, render a muted `'—'`.
* **Sticky Row Summary Cell:** Leftmost column shows aggregate period frequency:
  $$\text{summary\_pct}_b = \frac{\sum_t C_{b,t}}{\text{total\_runs}} \times 100\%$$

#### Interactive Duty Inspector Drawer (`#crosstab-inspector`):
When a user clicks cell $(b, t)$:
* Backend returns `entry.veh_details[b]`: Array of vehicle objects operating in band $b$ on date $t$:
  ```json
  [
    {
      "p": "AP39WN7281",
      "km": 718.2,
      "hrs": 18.5,
      "spd": 35.9,
      "c": "FreshBus",
      "m": "A12.5C"
    }
  ]
  ```
* Renders inspector cards displaying plate number, customer badge, trip distance, engine operating hours, and average speed.

---

### 5.3b Vehicle Distance Trajectory (by SoC) (`#chart-veh-trajectory`)

A multi-series time-series scatter/line visualization tracking daily distance behavior across 30 days for 5 sampled vehicles per customer account, with **latest dates on the left (`07 Sep`) and oldest on the right (`09 Aug`)**.

#### Vehicle Sampling & Battery SoC Stratification Engine:
1. **FreshBus (Full SoC Distribution Available):**
   * Exactly 5 vehicles sampled across the full range of 9 bench-tested assets:
     - `AP39WN7305` (5.0 km/SoC - Minimum Benchmark)
     - `AP39WN7306` (5.5 km/SoC - 25th Percentile)
     - `AP39WN7280` (5.7 km/SoC - Median Benchmark)
     - `AP39WN7281` (6.0 km/SoC - 75th Percentile)
     - `AP39WN7273` (6.3 km/SoC - Maximum Benchmark)
   * **Notice:** Validated benchmarked battery mileage spanning 5.0 to 6.3 km/SoC.

2. **ZingBus (Partial SoC Distribution - 4 Assets Available):**
   * Only 4 vehicles have digitalized BMS benchmarks in `vehicle_mileage_soc.xlsx`:
     - `HR55AY7626` (3.9 km/SoC)
     - `HR55AY9237` (3.9 km/SoC)
     - `DL1PD8669` (4.0 km/SoC)
     - `DL1PD8652` (5.0 km/SoC)
     - `DL1PD8523` (5th vehicle added without benchmarked SoC • `SoC: N/A`)
   * **Notice:** Explicit callout specifying that only 4 vehicles have benchmarked SoC in the digitalized BMS log, with the 5th line plotted without SoC.

3. **BillionE (Unbenchmarked Fleet - 0 Assets Available):**
   * Digitalized BMS mileage benchmarks are currently unlogged in the source BMS dataset.
   * Exactly 5 vehicles sampled across operational utilization intensity tiers:
     - `MH02GS4337` (Avg 150 km/d)
     - `MH02GS4339` (Avg 186 km/d)
     - `MH02GS4227` (Avg 199 km/d)
     - `MH02GS4340` (Avg 246 km/d)
     - `MH02GS4229` (Avg 271 km/d)
   * **Notice:** Explicit callout specifying that benchmarked battery mileage (km/% SoC) is currently not available for BillionE commercial electric trucks in the BMS dataset, with vehicles sampled across operational intensity tiers.

#### Visualization Parameters:
* **X-Axis:** Dates ordered from latest on the left (`07 Sep`) to oldest on the right (`09 Aug`), formatted as `DD Mon (DayOfWeek)` (e.g. `07 Sep (Mon)`), matching the column tick styling of the cross-tab matrix.
* **Y-Axis:** `Daily Distance Covered (km)` ($d \ge 0$).
* **Traces:** 5 individual line traces per customer, with line width 2.2px, markers (size 5px).
* **Graph Legend:** Each legend badge explicitly mentions the vehicle registration plate alongside its benchmarked battery SoC mileage:
  * For benchmarked assets: `Plate (SoC: X.X km/SoC)` (e.g. `AP39WN7305 (SoC: 5.0 km/SoC)`).
  * For unbenchmarked assets: `Plate (SoC: N/A)` (e.g. `DL1PD8523 (SoC: N/A)` or `MH02GS4337 (SoC: N/A)`).
* **Filtering:** Customer toggle buttons (`FreshBus`, `ZingBus`, `BillionE`) re-rendering the 5 vehicle traces, legend tags, and dynamic status notice.



---

### 5.4 Vehicle Fleet Performance Data Tables & Top/Bottom Charts

Located in `#section-Bus`, `#section-Truck`, and `#section-all`.

#### Top 5 & Bottom 5 Horizontal Bar Charts:
* **Elements:** `#chart-bus-top`, `#chart-bus-bottom`, `#chart-truck-top`, `#chart-truck-bottom`.
* **Function:** `createCleanBarPlot(containerId, items, isTop, benchmarkVal)`.
* **X-Axis:** `avg_km_per_day` of the vehicle.
* **Y-Axis:** `Base License Plate`.
* **Benchmark Reference Line:** Vertical dashed reference line at $\text{benchmarkVal} = \text{mean}(\text{avg\_km\_per\_day})$ of that category (Buses: $539\text{ km/d}$, Trucks: $238\text{ km/d}$).
* **Colors:** Top 5: `#5cb030` (Green), Bottom 5: `#ef4444` (Coral Red).

#### Master Vehicle Table Column Mapping (`.cat-table`):

| # | Header Label | Source Field / Calculation | Formatting / Render Logic |
|---|---|---|---|
| 1 | `#` | Row Rank | `r['rank']` (1 to 20 for Bus/Truck, 1 to 40 for Master table). |
| 2 | `License Plate` | `r['Base License Plate']` | Bold monospace plate string. |
| 3 | `Type` | `r['vehicle_type']` | *(Master table only)* Badge: `Bus` (blue) or `Truck` (amber). |
| 4 | `Customer` | `r['Customer']` | Customer name in brand green. |
| 5 | `Model` | `r['vehicle_model']` | Chassis model (e.g. `A12.5C`, `Tata 55T`). |
| 6 | `Active Days (% Avail)` | `r['active_days']`, `r['total_days']`, `r['active_rate_pct']` | Format: `"{active_days}/{total_days} d"`. If $<80\%$, red badge `⚠️ XX.X%`, else green badge `XX.X%`. |
| 7 | `Avg km/day` | `r['avg_km_per_day']` | `formatIn(r['avg_km_per_day'], 0) + ' km/d'`. |
| 8 | `Daily Hours` | `r['avg_hours_per_day']` | `formatIn(r['avg_hours_per_day'], 1) + ' h/d'`. |
| 9 | `Total Distance` | `r['total_distance']` | `formatIn(r['total_distance'], 0) + ' km'`. |
| 10 | `Total Hours` | `r['total_hours']` | `formatIn(r['total_hours'], 1) + ' h'`. |
| 11 | `Current Odometer Reading` | `r['final_odometer']` | `formatIn(r['final_odometer'], 0) + ' km'` or `'—'`. |
| 12 | `Mileage (km/SoC)` | `r['mileage_km_soc']` | `f"{r['mileage_km_soc']:.1f} km/SoC"` or `'—'`. |
| 13 | `Est. Daily SoC` | `r['est_daily_soc_pct']` | `f"{r['est_daily_soc_pct']:.0f}% SoC/d"` or `'—'`. |
| 14 | `Daily KM Volatility (%)` | `r['active_cv_pct']`, `r['volatility_class']` | Pill badge: `f"{r['active_cv_pct']:.0f}%"`. Tooltip: `f"{r['volatility_tier']} • σ = ±{r['active_std_dist']} km"`. |
| 15 | `Volatility Chart` | `r['sparkline_svg']` | 30-day inline SVG sparkline vector. |

---

## 6. Recommended Target Relational Data Model (DDL)

To support dynamic range querying, fleet filtering, and real-time aggregation, the raw files should be ingested into the following normalized PostgreSQL / ClickHouse database schema:

```sql
-- 1. Dim Customer
CREATE TABLE dim_customer (
    customer_id VARCHAR(50) PRIMARY KEY,
    customer_name VARCHAR(100) NOT NULL,
    primary_oem VARCHAR(100),
    routes_description TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 2. Dim Vehicle
CREATE TABLE dim_vehicle (
    license_plate VARCHAR(20) PRIMARY KEY,
    customer_id VARCHAR(50) REFERENCES dim_customer(customer_id),
    vehicle_type VARCHAR(20) NOT NULL CHECK (vehicle_type IN ('Bus', 'Truck')),
    vehicle_model VARCHAR(50) DEFAULT 'Standard',
    short_fleet_no VARCHAR(20),
    first_telemetry_date DATE,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 3. Fact Vehicle Mileage Benchmark (BMS)
CREATE TABLE fact_vehicle_mileage (
    license_plate VARCHAR(20) PRIMARY KEY REFERENCES dim_vehicle(license_plate),
    mileage_km_per_soc NUMERIC(5, 2),
    benchmark_status VARCHAR(50),
    notes TEXT,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 4. Fact Daily Telemetry (Cleaned Fact Table)
CREATE TABLE fact_telemetry_daily (
    report_date DATE NOT NULL,
    license_plate VARCHAR(20) NOT NULL REFERENCES dim_vehicle(license_plate),
    distance_km NUMERIC(10, 2) NOT NULL DEFAULT 0.0,
    running_hours NUMERIC(6, 2) DEFAULT 0.0,
    average_speed_kmh NUMERIC(5, 2),
    max_speed_kmh NUMERIC(5, 2),
    opening_odometer_km NUMERIC(12, 2),
    closing_odometer_km NUMERIC(12, 2),
    stoppages_count INTEGER DEFAULT 0,
    -- Standard 200 km compartments starting from 0–200 km:
    distance_band_idx SMALLINT GENERATED ALWAYS AS (
        CASE 
            WHEN distance_km <= 200.0 THEN 0
            WHEN distance_km <= 400.0 THEN 1
            WHEN distance_km <= 600.0 THEN 2
            WHEN distance_km <= 800.0 THEN 3
            ELSE 4
        END
    ) STORED,
    PRIMARY KEY (report_date, license_plate)
);

-- Note: For Truck fleet (BillionE), dynamic aggregation query partitions into 150 km bins up to 600 km:
-- CASE WHEN distance_km <= 150.0 THEN 0 WHEN distance_km <= 300.0 THEN 1 WHEN distance_km <= 450.0 THEN 2 ELSE 3 END


CREATE INDEX idx_fact_telemetry_date ON fact_telemetry_daily(report_date);
CREATE INDEX idx_fact_telemetry_plate ON fact_telemetry_daily(license_plate);
CREATE INDEX idx_fact_telemetry_band ON fact_telemetry_daily(distance_band_idx);
```

---

## 7. Dynamic REST API Specifications & JSON Payloads

The dynamic backend should expose clean, RESTful endpoints that return the structured JSON expected by the frontend JavaScript engine (`reportData`).

### 7.1 `GET /api/v1/telematics/kpi-summary`
Returns top-level KPI metrics evaluated over a specified date range for a given scope.

* **Query Parameters:**
  * `scope` (string, required): `all` | `Bus` | `Truck` | `customers`
  * `start_date` (date string, optional): e.g. `2026-08-09`
  * `end_date` (date string, optional): e.g. `2026-09-07`

* **Response Schema (200 OK):**
```json
{
  "scope": "all",
  "total_vehicles": 40,
  "total_distance": 427314,
  "daily_distance": 14244,
  "per_veh_daily_dist": 454,
  "total_hours": 11552.2,
  "daily_hours": 385.1,
  "per_veh_daily_hrs": 12.3,
  "dist_label": "Total Fleet Distance / Day",
  "hours_label": "Total Operating Time / Day",
  "dist_unit_sub": "km / day / vehicle",
  "hours_unit_sub": "hrs / day / vehicle",
  "fleet_label": "Active Commercial Fleet",
  "fleet_breakdown": "20 Buses (FreshBus: 10, ZingBus: 10) • 20 Trucks (BillionE: 20)",
  "fleet_prior": "40 Active Commercial Assets (FreshBus, ZingBus, BillionE)",
  "peak_label": "Peak Active Vehicle",
  "peak_num": "AP39WN7281",
  "peak_sub": "753 km/d • 30/30 d (100%)",
  "peak_prior": "Total: 22,576 km"
}
```

---

### 7.2 `GET /api/v1/telematics/customers/analytics`
Returns aggregate customer analytics feeding Charts 1 to 5.

* **Query Parameters:**
  * `start_date` (date, optional)
  * `end_date` (date, optional)

* **Response Schema (200 OK):**
```json
{
  "customers_all": [
    {
      "customer": "FreshBus",
      "route": "Guntur - Hyderabad, Guntur - Vizag",
      "vehicle_count": 10,
      "total_distance": 199528.8,
      "total_hours": 5831.3,
      "avg_km_day_per_veh": 665.1,
      "avg_hrs_day_per_veh": 19.4,
      "active_days_avg": 30.0,
      "total_days_avg": 30.0,
      "share_pct": 53.3,
      "oem": "Azad (Bus)",
      "avg_mileage_soc": 5.4,
      "avg_std_active": 107.5,
      "avg_cv_pct": 16.2,
      "vol_tier": "Stable",
      "vol_class": "vol-low",
      "insight": "⚡ <b>Dispatch Stability:</b> Ultra-consistent intercity schedule..."
    }
  ],
  "customer_box_data": {
    "FreshBus": [718.15, 650.2, 680.4],
    "ZingBus": [420.5, 380.0, 510.2],
    "BillionE": [220.0, 310.5, 0.0]
  },
  "dow_labels": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
  "dow_profiles": {
    "FreshBus": [
      {
        "DayOfWeek": "Monday",
        "mean_dist": 672.4,
        "std_dist": 95.2,
        "samples": 40,
        "volatility_pct": 14.2
      }
    ]
  },
  "active_timeline": {
    "dates": ["2026-08-09", "2026-08-10"],
    "FreshBus": { "counts": [10, 10], "pcts": [100.0, 100.0], "fleet_size": 10 },
    "ZingBus": { "counts": [9, 10], "pcts": [90.0, 100.0], "fleet_size": 10 },
    "BillionE": { "counts": [8, 12], "pcts": [80.0, 85.7], "fleet_sizes": [10, 14], "fleet_size": 20 },
    "total": { "counts": [27, 32], "pcts": [90.0, 94.1], "fleet_sizes": [30, 34], "fleet_size": 40 }
  }
}
```

---

### 7.3 `GET /api/v1/telematics/charts/crosstab-matrix`
Returns the $6 \times N$ compartment heat-map matrix and vehicle inspection detail arrays.

* **Query Parameters:**
  * `customer` (string, required): `All` | `FreshBus` | `ZingBus` | `BillionE`
  * `start_date` (date, optional)
  * `end_date` (date, optional)

* **Response Schema (200 OK):**
```json
{
  "customer": "All",
  "total_runs": 1021,
  "summary_counts": [221, 292, 218, 269, 21],
  "summary_pcts": [21.6, 28.6, 21.4, 26.3, 2.1],
  "long_share": 28.4,
  "mid_share": 50.0,
  "short_share": 21.6,
  "long_title": ">600 km",
  "mid_title": "200–600 km",
  "short_title": "≤200 km",
  "peak_run": {
    "plate": "AP39WN7281",
    "km": 792.0,
    "date": "27-Aug",
    "cust": "FreshBus"
  },
  "bands": [
    { "id": "b0", "name": "0–200 km" },
    { "id": "b1", "name": "201–400 km" },
    { "id": "b2", "name": "401–600 km" },
    { "id": "b3", "name": "601–800 km" },
    { "id": "b4", "name": "800+ km" }
  ],
  "dates": ["2026-08-09", "2026-08-10"],
  "by_date": [
    {
      "date": "2026-08-09",
      "total": 40,
      "counts": [16, 7, 8, 9, 0],
      "pcts": [40.0, 17.5, 20.0, 22.5, 0.0],
      "kms": [452.1, 2140.5, 4120.0, 6210.4, 0.0],
      "vehs": [
        ["MH02GS4334", "DL1PD8509", "MH02GS4233"],
        ["AP39WN7273"],
        ["AP39WN7275"],
        ["AP39WN7281"],
        []
      ],
      "veh_details": [
        [
          {
            "p": "MH02GS4334",
            "km": 0.0,
            "hrs": 0.0,
            "spd": 0.0,
            "c": "BillionE",
            "m": "Tata 55T"
          }
        ]
      ]
    }
  ]
}
```

*Note: For `customer=BillionE` (Trucks), `bands` returns 4 compartments: `0–150 km`, `151–300 km`, `301–450 km`, and `451–600 km`, with `long_title: ">450 km"`, `mid_title: "150–450 km"`, and `short_title: "≤150 km"`.*


---

### 7.4 `GET /api/v1/telematics/vehicles`
Returns the ranked vehicle roster for tabular display and Top 5 / Bottom 5 charting.

* **Query Parameters:**
  * `category` (string, optional): `all` | `Bus` | `Truck`
  * `sort_by` (string, default: `active_rate_pct`): Column to order by
  * `order` (string, default: `asc`): `asc` | `desc`
  * `start_date` (date, optional)
  * `end_date` (date, optional)

* **Response Schema (200 OK):**
```json
{
  "total_count": 40,
  "category": "Bus",
  "below_80_count": 3,
  "benchmark_mean_km_day": 539.2,
  "benchmark_mean_hrs_day": 15.1,
  "vehicles": [
    {
      "rank": 1,
      "plate": "DL1PD8509",
      "vehicle_type": "Bus",
      "customer": "ZingBus",
      "vehicle_model": "JBM 12M",
      "active_days": 19,
      "total_days": 30,
      "active_rate_pct": 63.3,
      "is_below_80": true,
      "avg_km_per_day": 384.2,
      "avg_hours_per_day": 14.2,
      "total_distance": 7300.0,
      "total_hours": 269.8,
      "final_odometer": 48210.0,
      "mileage_km_soc": 3.8,
      "est_daily_soc_pct": 101.0,
      "active_days_count": 19,
      "active_mean_dist": 384.2,
      "active_std_dist": 98.4,
      "active_cv_pct": 25.6,
      "volatility_tier": "Moderate",
      "volatility_class": "vol-mid",
      "daily_distance_history": [350.0, 410.2, 0.0]
    }
  ],
  "top5": [],
  "bottom5": []
}
```

---

### 7.5 `GET /api/v1/telematics/charts/vehicle-trajectories`
Returns the 30-day daily distance time-series for 5 sampled vehicles per customer stratified across the battery SoC efficiency distribution, with dates sorted latest-first (left to right).

* **Query Parameters:**
  * `customer` (string, required): `FreshBus` | `ZingBus` | `BillionE`
  * `start_date` (date, optional)
  * `end_date` (date, optional)

* **Response Schema (200 OK):**
```json
{
  "customer": "FreshBus",
  "dates": ["2026-09-07", "2026-09-06", "2026-09-05", "2026-08-09"],
  "notice": "⚡ <b>SoC Efficiency Coverage:</b> All 5 vehicles have validated benchmarked battery mileage, spanning the full distribution from <b>5.0 km/SoC</b> (low) to <b>6.3 km/SoC</b> (high).",
  "vehicles": [
    {
      "plate": "AP39WN7305",
      "soc": 5.0,
      "soc_str": "5.0 km/SoC",
      "avg_km": 665.1,
      "distances": [555.5, 787.6, 787.9, 766.1]
    },
    {
      "plate": "AP39WN7306",
      "soc": 5.5,
      "soc_str": "5.5 km/SoC",
      "avg_km": 679.3,
      "distances": [610.2, 792.0, 750.4, 720.0]
    },
    {
      "plate": "AP39WN7280",
      "soc": 5.7,
      "soc_str": "5.7 km/SoC",
      "avg_km": 668.9,
      "distances": [640.0, 780.5, 770.2, 715.4]
    },
    {
      "plate": "AP39WN7281",
      "soc": 6.0,
      "soc_str": "6.0 km/SoC",
      "avg_km": 609.5,
      "distances": [718.2, 750.0, 785.1, 690.5]
    },
    {
      "plate": "AP39WN7273",
      "soc": 6.3,
      "soc_str": "6.3 km/SoC",
      "avg_km": 652.6,
      "distances": [680.4, 765.2, 790.0, 705.8]
    }
  ]
}
```
*Note: If `customer=ZingBus`, `notice` alerts that only 4 vehicles have SoC benchmarks and the 5th has `soc: null, soc_str: "SoC: N/A"`. If `customer=BillionE`, `notice` alerts that no digitalized SoC benchmarks are available and vehicles are sampled across operational distance tiers.*


## 8. Dynamic Frontend Transition & Refactoring Checklist

To migrate `report.html` into a fully reactive dynamic web application:

1. **State Management & Fetch Lifecycle:**
   * Replace inline embedded `var reportData = { ... };` with an asynchronous initialization sequence:
     ```javascript
     let reportData = null;
     async function loadDashboardData(startDate, endDate) {
         showLoadingSkeleton();
         const [kpis, analytics, crosstab, vehicles] = await Promise.all([
             fetch(`/api/v1/telematics/kpi-summary?start_date=${startDate}&end_date=${endDate}`).then(r => r.json()),
             fetch(`/api/v1/telematics/customers/analytics?start_date=${startDate}&end_date=${endDate}`).then(r => r.json()),
             fetch(`/api/v1/telematics/charts/crosstab-matrix?customer=All&start_date=${startDate}&end_date=${endDate}`).then(r => r.json()),
             fetch(`/api/v1/telematics/vehicles?start_date=${startDate}&end_date=${endDate}`).then(r => r.json())
         ]);
         reportData = assembleReportState(kpis, analytics, crosstab, vehicles);
         renderAllPlots();
         hideLoadingSkeleton();
     }
     ```
2. **Dynamic Date Range & Customer Filters:**
   * Bind the date-picker UI component to trigger `loadDashboardData(picker.start, picker.end)`.
   * Bind the customer pills in the cross-tab section (`setKmMatrixCust(cust)`) to fetch `/api/v1/telematics/charts/crosstab-matrix?customer=${cust}` without full-page reload.
3. **Table Re-rendering:**
   * Refactor static table HTML rows (`bus_rows_html`, `truck_rows_html`, `all_rows_html`) into client-side virtualized rendering functions consuming the `/api/v1/telematics/vehicles` response array.
4. **Caching & Latency Optimization:**
   * Implement Redis caching on the backend for `kpi-summary` and `crosstab-matrix` with a 5-minute TTL.
   * Add database indexing on `(report_date, license_plate)` to guarantee sub-50ms query execution across millions of daily telematics records.
