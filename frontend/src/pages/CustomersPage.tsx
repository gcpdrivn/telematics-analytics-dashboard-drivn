import { useEffect, useMemo, useState } from "react"
import { getCrosstabMatrix, getCustomerAnalytics, getKpiSummary, getSohOdometer, getVehicles } from "../api/client"
import type {
  CrosstabResponse,
  CustomerAnalytics,
  KpiSummary,
  SohOdometerResponse,
  VehicleRecord,
} from "../api/types"
import { CustomerCard } from "../components/CustomerCard"
import { CustomerSelector } from "../components/CustomerSelector"
import { DailyDistanceBar } from "../components/CustomerCharts/DailyDistanceBar"
import { ShareDonut } from "../components/CustomerCharts/ShareDonut"
import { SohOdometerChart } from "../components/CustomerCharts/SohOdometerChart"
import { EfficiencyOdometerChart } from "../components/CustomerCharts/EfficiencyOdometerChart"
import { CrosstabMatrix } from "../components/CrosstabMatrix"
import { KpiCards } from "../components/KpiCards"
import { UptimeTable } from "../components/UptimeTable"
import { useCustomerFilter } from "../hooks/useCustomerFilter"
import { useDateRange } from "../hooks/useDateRange"

export function CustomersPage() {
  const [range] = useDateRange()
  const [customer, setCustomer] = useCustomerFilter()
  const [kpi, setKpi] = useState<KpiSummary | null>(null)
  const [analytics, setAnalytics] = useState<CustomerAnalytics | null>(null)
  const [error, setError] = useState<string | null>(null)

  const [crosstab, setCrosstab] = useState<CrosstabResponse | null>(null)

  // Not date-ranged: SoH and odometer are both "as of the last morning ping".
  const [soh, setSoh] = useState<SohOdometerResponse | null>(null)
  const [sohError, setSohError] = useState<string | null>(null)
  useEffect(() => {
    getSohOdometer().then(setSoh).catch((e) => setSohError(String(e)))
  }, [])

  // Not date-ranged either: both axes are lifetime figures (odometer, kWh to date).
  const [eff, setEff] = useState<{ buses: VehicleRecord[]; trucks: VehicleRecord[] } | null>(null)
  const [effError, setEffError] = useState<string | null>(null)
  useEffect(() => {
    Promise.all([getVehicles("Bus"), getVehicles("Truck")])
      .then(([b, t]) => setEff({ buses: b.vehicles, trucks: t.vehicles }))
      .catch((e) => setEffError(String(e)))
  }, [])

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

  const filteredCustomers = useMemo(() => {
    if (!analytics) return []
    return customer === "All" ? analytics.customers_all : analytics.customers_all.filter((c) => c.customer === customer)
  }, [analytics, customer])

  if (error) return <div className="error-box">Failed to load: {error}</div>
  if (!kpi || !analytics) return <div className="loading">Loading customer analytics…</div>

  return (
    <>
      <KpiCards kpi={kpi} hideFleetBreakdown hidePeak />

      <div className="panel filter-bar">
        <span className="filter-bar-label">Filter by Customer</span>
        <CustomerSelector
          value={customer}
          onChange={setCustomer}
          counts={Object.fromEntries(analytics.customers_all.map((c) => [c.customer, c.vehicle_count]))}
        />
      </div>

      <div className="panel" id="key-customer-accounts">
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
        <div className="panel" id="daily-distance-trajectory">
          <div className="panel-header">Daily Distance Trajectory</div>
          <div className="panel-body">
            <DailyDistanceBar customers={filteredCustomers} />
          </div>
        </div>
        <div className="panel donut-panel" id="fleet-distance-share">
          <div className="panel-body">
            <ShareDonut customers={analytics.customers_all} highlight={customer === "All" ? null : customer} />
          </div>
        </div>
      </div>

      <div className="panel" id="soh-odometer">
        <div className="panel-header">Battery State of Health vs Latest Odometer</div>
        <div className="panel-body">
          {sohError && <div className="error-box">Failed to load SoH data: {sohError}</div>}
          {!soh && !sohError && <div className="loading">Loading SoH data…</div>}
          {soh && <SohOdometerChart data={soh} customer={customer} />}
        </div>
      </div>

      <div className="panel" id="distance-compartments">
        <div className="panel-header">Daily Distance Compartments & Fleet Dispersion</div>
        <div className="panel-body">
          {crosstab && <CrosstabMatrix data={crosstab} customer={customer} />}
        </div>
      </div>

      <div className="panel" id="efficiency-odometer">
        <div className="panel-header">Energy Efficiency vs Odometer</div>
        <div className="panel-body">
          {effError && <div className="error-box">Failed to load efficiency data: {effError}</div>}
          {!eff && !effError && <div className="loading">Loading efficiency data…</div>}
          {eff && (
            <EfficiencyOdometerChart
              buses={eff.buses}
              trucks={eff.trucks}
              customer={customer}
              oemByCustomer={Object.fromEntries(analytics.customers_all.map((c) => [c.customer, c.oem]))}
            />
          )}
        </div>
      </div>
    </>
  )
}
