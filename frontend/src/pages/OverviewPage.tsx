import { useEffect, useState } from "react"
import { getKpiSummary, getVehicles } from "../api/client"
import type { KpiSummary, VehiclesResponse } from "../api/types"
import { KpiCards } from "../components/KpiCards"
import { VehicleTable } from "../components/VehicleTable"
import { useDateRange } from "../hooks/useDateRange"

export function OverviewPage() {
  const [range] = useDateRange()
  const [kpi, setKpi] = useState<KpiSummary | null>(null)
  const [vehicles, setVehicles] = useState<VehiclesResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setKpi(null)
    setVehicles(null)
    Promise.all([getKpiSummary("all", range), getVehicles("all", range)])
      .then(([k, v]) => {
        if (cancelled) return
        setKpi(k)
        setVehicles(v)
      })
      .catch((e) => !cancelled && setError(String(e)))
    return () => {
      cancelled = true
    }
  }, [range.start, range.end])

  if (error) return <div className="error-box">Failed to load: {error}</div>
  if (!kpi || !vehicles) return <div className="loading">Loading fleet overview…</div>

  return (
    <>
      <KpiCards kpi={kpi} />
      <div className="panel">
        <div className="panel-header">
          All Commercial Vehicles ({vehicles.total_count}) — {vehicles.below_80_count} below 80% active
        </div>
        <VehicleTable vehicles={vehicles.vehicles} showType />
      </div>
    </>
  )
}
