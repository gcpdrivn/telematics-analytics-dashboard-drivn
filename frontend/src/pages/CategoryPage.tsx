import { useEffect, useState } from "react"
import { getKpiSummary, getVehicles } from "../api/client"
import type { Category, KpiSummary, Scope, VehiclesResponse } from "../api/types"
import { KpiCards } from "../components/KpiCards"
import { TopBottomBarChart } from "../components/TopBottomBarChart"
import { VehicleTable } from "../components/VehicleTable"

/** Shared shell for /buses and /trucks -- same layout (KPI cards, top5/bottom5
 * bar charts, master table), different category filter. */
export function CategoryPage({ category, label }: { category: Category; label: string }) {
  const [kpi, setKpi] = useState<KpiSummary | null>(null)
  const [vehicles, setVehicles] = useState<VehiclesResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setKpi(null)
    setVehicles(null)
    Promise.all([getKpiSummary(category as Scope), getVehicles(category)])
      .then(([k, v]) => {
        if (cancelled) return
        setKpi(k)
        setVehicles(v)
      })
      .catch((e) => !cancelled && setError(String(e)))
    return () => {
      cancelled = true
    }
  }, [category])

  if (error) return <div className="error-box">Failed to load: {error}</div>
  if (!kpi || !vehicles) return <div className="loading">Loading {label.toLowerCase()}…</div>

  const benchmark = vehicles.benchmark_mean_km_day

  return (
    <>
      <KpiCards kpi={kpi} />

      <div className="charts-grid">
        <div className="panel">
          <div className="panel-header">Top 5 by Avg km/day</div>
          <div className="panel-body">
            <TopBottomBarChart vehicles={vehicles.vehicles} isTop benchmarkVal={benchmark} />
          </div>
        </div>
        <div className="panel">
          <div className="panel-header">Bottom 5 by Avg km/day</div>
          <div className="panel-body">
            <TopBottomBarChart vehicles={vehicles.vehicles} isTop={false} benchmarkVal={benchmark} />
          </div>
        </div>
      </div>

      <div className="panel">
        <div className="panel-header">
          {label} ({vehicles.total_count}) — {vehicles.below_80_count} below 80% active
        </div>
        <VehicleTable vehicles={vehicles.vehicles} />
      </div>
    </>
  )
}
