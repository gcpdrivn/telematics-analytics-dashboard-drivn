import { useEffect, useState } from "react"
import { getCrosstabMatrix, getCustomerAnalytics, getKpiSummary, getVehicleTrajectories } from "../api/client"
import type {
  CrosstabCustomer,
  CrosstabResponse,
  CustomerAnalytics,
  CustomerName,
  KpiSummary,
  TrajectoryResponse,
} from "../api/types"
import { CustomerCard } from "../components/CustomerCard"
import { BoxplotChart } from "../components/CustomerCharts/BoxplotChart"
import { DailyDistanceBar } from "../components/CustomerCharts/DailyDistanceBar"
import { ShareDonut } from "../components/CustomerCharts/ShareDonut"
import { ActiveTimelineChart } from "../components/CustomerCharts/ActiveTimelineChart"
import { SeasonalityChart } from "../components/CustomerCharts/SeasonalityChart"
import { CrosstabMatrix } from "../components/CrosstabMatrix"
import { KpiCards } from "../components/KpiCards"
import { VehicleTrajectoryChart } from "../components/VehicleTrajectoryChart"

const TRAJECTORY_CUSTOMERS: CustomerName[] = ["FreshBus", "ZingBus", "BillionE"]

export function CustomersPage() {
  const [kpi, setKpi] = useState<KpiSummary | null>(null)
  const [analytics, setAnalytics] = useState<CustomerAnalytics | null>(null)
  const [error, setError] = useState<string | null>(null)

  const [crosstabCust, setCrosstabCust] = useState<CrosstabCustomer>("All")
  const [crosstab, setCrosstab] = useState<CrosstabResponse | null>(null)

  const [trajCust, setTrajCust] = useState<CustomerName>("FreshBus")
  const [trajectory, setTrajectory] = useState<TrajectoryResponse | null>(null)

  useEffect(() => {
    let cancelled = false
    Promise.all([getKpiSummary("customers"), getCustomerAnalytics()])
      .then(([k, a]) => {
        if (cancelled) return
        setKpi(k)
        setAnalytics(a)
      })
      .catch((e) => !cancelled && setError(String(e)))
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    getCrosstabMatrix(crosstabCust).then((d) => !cancelled && setCrosstab(d))
    return () => {
      cancelled = true
    }
  }, [crosstabCust])

  useEffect(() => {
    let cancelled = false
    getVehicleTrajectories(trajCust).then((d) => !cancelled && setTrajectory(d))
    return () => {
      cancelled = true
    }
  }, [trajCust])

  if (error) return <div className="error-box">Failed to load: {error}</div>
  if (!kpi || !analytics) return <div className="loading">Loading customer analytics…</div>

  return (
    <>
      <KpiCards kpi={kpi} />

      <div className="panel">
        <div className="panel-header">Key Customer Accounts</div>
        <div className="panel-body">
          <div className="customer-grid">
            {analytics.customers_all.map((c) => (
              <CustomerCard key={c.customer} customer={c} />
            ))}
          </div>
        </div>
      </div>

      <div className="charts-grid">
        <div className="panel">
          <div className="panel-header">Daily Distance Trajectory</div>
          <div className="panel-body">
            <DailyDistanceBar customers={analytics.customers_all} />
          </div>
        </div>
        <div className="panel">
          <div className="panel-header">Fleet Distance Share</div>
          <div className="panel-body">
            <ShareDonut customers={analytics.customers_all} />
          </div>
        </div>
        <div className="panel">
          <div className="panel-header">Dispatch Variance (Daily Distance Distribution)</div>
          <div className="panel-body">
            <BoxplotChart boxData={analytics.customer_box_data} />
          </div>
        </div>
        <div className="panel">
          <div className="panel-header">Day-of-Week Operational Rhythm</div>
          <div className="panel-body">
            <SeasonalityChart profiles={analytics.dow_profiles} dowLabels={analytics.dow_labels} />
          </div>
        </div>
      </div>

      <div className="panel">
        <div className="panel-header">Daily Active Commercial Vehicle % Timeline</div>
        <div className="panel-body">
          <ActiveTimelineChart timeline={analytics.active_timeline} />
        </div>
      </div>

      <div className="panel">
        <div className="panel-header">Daily Distance Compartments & Fleet Dispersion</div>
        <div className="panel-body">
          {crosstab && (
            <CrosstabMatrix data={crosstab} customer={crosstabCust} onCustomerChange={setCrosstabCust} />
          )}
        </div>
      </div>

      <div className="panel">
        <div className="panel-header">Vehicle Distance Trajectory (by SoC)</div>
        <div className="panel-body">
          <div className="customer-nav">
            {TRAJECTORY_CUSTOMERS.map((c) => (
              <button
                key={c}
                className={`seg-pill ${trajCust === c ? "active" : ""}`}
                onClick={() => setTrajCust(c)}
              >
                {c}
              </button>
            ))}
          </div>
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
