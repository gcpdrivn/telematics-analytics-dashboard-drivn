export type Scope = "all" | "Bus" | "Truck" | "customers"
export type Category = "all" | "Bus" | "Truck"
export type CustomerName = "FreshBus" | "ZingBus" | "BillionE"
export type CrosstabCustomer = "All" | CustomerName

export interface KpiSummary {
  total_vehicles: number
  total_distance: number
  daily_distance: number
  per_veh_daily_dist: number
  total_hours: number
  daily_hours: number
  per_veh_daily_hrs: number
  dist_label: string
  hours_label: string
  dist_unit_sub: string
  hours_unit_sub: string
  fleet_label: string
  fleet_breakdown: string
  fleet_prior: string
  peak_label: string
  peak_num: string
  peak_sub: string
  peak_prior: string
}

export interface CustomerProfile {
  customer: CustomerName
  route: string
  vehicle_count: number
  total_distance: number
  total_hours: number
  avg_km_day_per_veh: number
  avg_hrs_day_per_veh: number
  active_days_avg: number
  total_days_avg: number
  share_pct: number
  oem: string
  avg_mileage_soc: number | null
  avg_std_active: number
  avg_cv_pct: number
  vol_tier: string
  vol_class: string
  insight: string
}

export interface DowProfileEntry {
  DayOfWeek: string
  mean_dist: number
  std_dist: number
  samples: number
  volatility_pct: number
}

export interface ActiveTimelineSeries {
  counts: number[]
  pcts: number[]
  fleet_sizes: number[]
  fleet_size: number
}

export interface ActiveTimeline {
  dates: string[]
  FreshBus: ActiveTimelineSeries
  ZingBus: ActiveTimelineSeries
  BillionE: ActiveTimelineSeries
  total: ActiveTimelineSeries
}

export interface CustomerAnalytics {
  customers_all: CustomerProfile[]
  customers_top3: CustomerProfile[]
  customer_box_data: Record<CustomerName, number[]>
  dow_labels: string[]
  dow_profiles: Record<CustomerName, DowProfileEntry[]>
  active_timeline: ActiveTimeline
}

export interface DistanceBand {
  id: string
  name: string
}

export interface VehicleDetail {
  p: string
  km: number
  hrs: number
  spd: number
  c: string
  m: string
}

export interface CrosstabByDate {
  date: string
  total: number
  counts: number[]
  pcts: number[]
  vehs: string[][]
  veh_details: VehicleDetail[][]
  kms: number[]
}

export interface PeakRun {
  plate: string
  km: number
  date: string
  cust: string
}

export interface CrosstabResponse {
  customer: CrosstabCustomer
  dates: string[]
  bands: DistanceBand[]
  total_runs: number
  summary_counts: number[]
  summary_pcts: number[]
  long_share: number
  mid_share: number
  short_share: number
  long_title: string
  mid_title: string
  short_title: string
  peak_run: PeakRun | null
  by_date: CrosstabByDate[]
}

export interface VehicleRecord {
  rank: number
  plate: string
  vehicle_type: "Bus" | "Truck"
  customer: CustomerName
  vehicle_model: string
  active_days: number
  total_days: number
  active_rate_pct: number
  is_below_80: boolean
  avg_km_per_day: number
  avg_hours_per_day: number
  total_distance: number
  total_hours: number
  final_odometer: number | null
  mileage_km_soc: number | null
  est_daily_soc_pct: number | null
  active_days_count: number
  active_mean_dist: number
  active_std_dist: number
  active_cv_pct: number
  volatility_tier: string
  volatility_class: string
  daily_distance_history: number[]
}

export interface VehiclesResponse {
  total_count: number
  category: Category
  below_80_count: number
  benchmark_mean_km_day: number
  benchmark_mean_hrs_day: number
  vehicles: VehicleRecord[]
}

export interface TrajectoryVehicle {
  plate: string
  soc: number | null
  soc_str: string
  avg_km: number
  distances: number[]
}

export interface TrajectoryResponse {
  customer: CustomerName
  dates: string[]
  notice: string
  vehicles: TrajectoryVehicle[]
}
