import { useEffect, useMemo, useState } from "react"
import { getCrosstabMatrix, getCustomerAnalytics, getKpiSummary, getVehicleTrajectories } from "../api/client"
import type { CrosstabResponse, CustomerAnalytics, CustomerName, KpiSummary, TrajectoryResponse } from "../api/types"
import { CustomerCard } from "../components/CustomerCard"
import { CustomerSelector } from "../components/CustomerSelector"
import { BoxplotChart } from "../components/CustomerCharts/BoxplotChart"
import { DailyDistanceBar } from "../components/CustomerCharts/DailyDistanceBar"
import { ShareDonut } from "../components/CustomerCharts/ShareDonut"
import { ActiveTimelineChart } from "../components/CustomerCharts/ActiveTimelineChart"
import { SeasonalityChart } from "../components/CustomerCharts/SeasonalityChart"
import { ALL_CUSTOMERS } from "../components/CustomerCharts/colors"
import { CrosstabMatrix } from "../components/CrosstabMatrix"
import { KpiCards } from "../components/KpiCards"
import { UptimeTable } from "../components/UptimeTable"
import { VehicleTrajectoryChart } from "../components/VehicleTrajectoryChart"
import { useCustomerFilter } from "../hooks/useCustomerFilter"
import { useDateRange } from "../hooks/useDateRange"

export function CustomersPage() {
  const [range] = useDateRange()
  const [customer, setCustomer] = useCustomerFilter()
  const [kpi, setKpi] = useState<KpiSummary | null>(null)
  const [analytics, setAnalytics] = useState<CustomerAnalytics | null>(null)
  const [error, setError] = useState<string | null>(null)

  const [crosstab, setCrosstab] = useState<CrosstabResponse | null>(null)

  // The trajectory endpoint requires one specific customer -- there's no
  // "All" for it, so picking "All" in the page filter defaults it to the
  // roster's first customer rather than leaving it unfiltered.
  const trajCust: CustomerName = customer === "All" ? ALL_CUSTOMERS[0] : customer
  const [trajectory, setTrajectory] = useState<TrajectoryResponse | null>(null)

  useEffect(() => {
    let cancelled = false
    setKpi(null)
    setAnalytics(null)
    Promise.all([getKpiSummary("customers", range), getCustomerAnalytics(range)])
      .then(([k, a]) => {
        if (cancelled) return
        setKpi(k)
        setAnalytics(a)
      })
      .catch((e) => !cancelled && setError(String(e)))
    return () => {
      cancelled = true
    }
  }, [range.start, range.end])

  useEffect(() => {
    let cancelled = false
    setCrosstab(null)
    getCrosstabMatrix(customer, range).then((d) => !cancelled && setCrosstab(d))
    return () => {
      cancelled = true
    }
  }, [customer, range.start, range.end])

  useEffect(() => {
    let cancelled = false
    setTrajectory(null)
    getVehicleTrajectories(trajCust, range).then((d) => !cancelled && setTrajectory(d))
    return () => {
      cancelled = true
    }
  }, [trajCust, range.start, range.end])

  const filteredCustomers = useMemo(() => {
    if (!analytics) return []
    return customer === "All" ? analytics.customers_all : analytics.customers_all.filter((c) => c.customer === customer)
  }, [analytics, customer])

  const filteredBoxData = useMemo(() => {
    if (!analytics) return {} as CustomerAnalytics["customer_box_data"]
    if (customer === "All") return analytics.customer_box_data
    return { [customer]: analytics.customer_box_data[customer] } as CustomerAnalytics["customer_box_data"]
  }, [analytics, customer])

  const filteredDowProfiles = useMemo(() => {
    if (!analytics) return {} as CustomerAnalytics["dow_profiles"]
    if (customer === "All") return analytics.dow_profiles
    return { [customer]: analytics.dow_profiles[customer] } as CustomerAnalytics["dow_profiles"]
  }, [analytics, customer])

  const filteredTimelineCustomers: CustomerName[] = customer === "All" ? ALL_CUSTOMERS : [customer]

  if (error) return <div className="error-box">Failed to load: {error}</div>
  if (!kpi || !analytics) return <div className="loading">Loading customer analytics…</div>

  return (
    <>
      <KpiCards kpi={kpi} />

      <div className="panel">
        <div className="panel-header">Filter by Customer</div>
        <div className="panel-body">
          <CustomerSelector value={customer} onChange={setCustomer} />
        </div>
      </div>

      <div className="panel">
        <div className="panel-header">Key Customer Accounts</div>
        <div className="panel-body">
          <div className="customer-grid">
            {filteredCustomers.map((c) => (
              <CustomerCard key={c.customer} customer={c} />
            ))}
          </div>
        </div>
      </div>

      <UptimeTable range={range} customer={customer} />

      <div className="charts-grid">
        <div className="panel">
          <div className="panel-header">Daily Distance Trajectory</div>
          <div className="panel-body">
            <DailyDistanceBar customers={filteredCustomers} />
          </div>
        </div>
        <div className="panel">
          <div className="panel-header">Fleet Distance Share</div>
          <div className="panel-body">
            <ShareDonut customers={analytics.customers_all} highlight={customer === "All" ? null : customer} />
          </div>
        </div>
        <div className="panel">
          <div className="panel-header">Dispatch Variance (Daily Distance Distribution)</div>
          <div className="panel-body">
            <BoxplotChart boxData={filteredBoxData} />
          </div>
        </div>
        <div className="panel">
          <div className="panel-header">Day-of-Week Operational Rhythm</div>
          <div className="panel-body">
            <SeasonalityChart profiles={filteredDowProfiles} dowLabels={analytics.dow_labels} />
          </div>
        </div>
      </div>

      <div className="panel">
        <div className="panel-header">Daily Active Commercial Vehicle % Timeline</div>
        <div className="panel-body">
          <ActiveTimelineChart timeline={analytics.active_timeline} customers={filteredTimelineCustomers} />
        </div>
      </div>

      <div className="panel">
        <div className="panel-header">Daily Distance Compartments & Fleet Dispersion</div>
        <div className="panel-body">
          {crosstab && <CrosstabMatrix data={crosstab} customer={customer} />}
        </div>
      </div>

      <div className="panel">
        <div className="panel-header">Vehicle Distance Trajectory (by SoC)</div>
        <div className="panel-body">
          {customer === "All" && (
            <div className="muted" style={{ marginBottom: "0.75rem" }}>
              Showing {trajCust} (first customer) — pick a specific customer above to see theirs.
            </div>
          )}
          {trajectory && (
            <>
              <div className="insight-box" dangerouslySetInnerHTML={{ __html: trajectory.notice }} />
              <VehicleTrajectoryChart data={trajectory} />
            </>
          )}
        </div>
      </div>
    </>
  )
}
