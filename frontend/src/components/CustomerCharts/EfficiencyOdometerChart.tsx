import { useMemo, useState } from "react"
import { Link } from "react-router-dom"
import Plot from "../../plotly-shim"
import type { CrosstabCustomer, CustomerName, EnergyWindow, VehicleRecord } from "../../api/types"
import { ALL_CUSTOMERS, CUSTOMER_COLORS, PLOT_LAYOUT_BASE } from "./colors"

type VehicleType = "Bus" | "Truck"
type Period = Extract<EnergyWindow, "7" | "3">

const PERIOD_OPTIONS: { value: Period; label: string }[] = [
  { value: "7", label: "Last 7 days" },
  { value: "3", label: "Last 3 days" },
]

interface EffPoint {
  v: VehicleRecord
  odometer: number
  kmPerKwh: number
  kwh: number | null
}

/** Plotted only with a positive odometer and a positive km/kWh for the
 * selected period (km ÷ kWh over the last 7 or 3 complete days, from Fleetx).
 * A vehicle with no energy trips in the period is listed as not plotted. */
function toPoint(v: VehicleRecord, period: Period): EffPoint | null {
  const odometer = v.live_odometer_km != null && v.live_odometer_km > 0 ? v.live_odometer_km : v.final_odometer
  const win = v.energy_windows?.[period] ?? null
  const kmPerKwh = win?.km_per_kwh ?? null
  if (odometer == null || !(odometer > 0) || kmPerKwh == null || !(kmPerKwh > 0)) return null
  return { v, odometer, kmPerKwh, kwh: win?.kwh ?? null }
}

function formatDay(iso: string): string {
  return new Date(`${iso}T00:00:00`).toLocaleDateString("en-IN", { day: "numeric", month: "short" })
}

function periodText(period: Period, end: string | null): string {
  const n = Number(period)
  if (!end) return `km ÷ kWh over the last ${n} days`
  const start = new Date(`${end}T00:00:00`)
  start.setDate(start.getDate() - (n - 1))
  const startIso = `${start.getFullYear()}-${String(start.getMonth() + 1).padStart(2, "0")}-${String(start.getDate()).padStart(2, "0")}`
  return `km ÷ kWh over the last ${n} days (${formatDay(startIso)} – ${formatDay(end)})`
}

const fmtKm = (n: number) => `${Math.round(n).toLocaleString("en-IN")} km`
const fmtEff = (n: number) => `${n.toFixed(2)} km/kWh`

/** Energy Efficiency vs Odometer: EV Buses and Trucks side by side, one dot
 * per vehicle (odometer × km/kWh), one colour per customer. Both charts share
 * one y-range so the same height means the same km/kWh. */
export function EfficiencyOdometerChart({
  buses,
  trucks,
  customer,
  oemByCustomer = {},
}: {
  buses: VehicleRecord[]
  trucks: VehicleRecord[]
  customer: CrosstabCustomer
  oemByCustomer?: Partial<Record<CustomerName, string>>
}) {
  const [showTable, setShowTable] = useState(false)
  const [period, setPeriod] = useState<Period>("7")

  const inScope = (v: VehicleRecord) => customer === "All" || v.customer === customer
  const busRows = useMemo(() => buses.filter(inScope), [buses, customer])
  const truckRows = useMemo(() => trucks.filter(inScope), [trucks, customer])

  const toPoints = (rows: VehicleRecord[]) =>
    rows.map((v) => toPoint(v, period)).filter((p): p is EffPoint => p !== null)
  const busPoints = useMemo(() => toPoints(busRows), [busRows, period])
  const truckPoints = useMemo(() => toPoints(truckRows), [truckRows, period])

  const windowEnd = useMemo(
    () => [...buses, ...trucks].find((v) => v.energy_window_end)?.energy_window_end ?? null,
    [buses, trucks]
  )

  const yRange = useMemo<[number, number] | undefined>(() => {
    const ys = [...busPoints, ...truckPoints].map((p) => p.kmPerKwh)
    if (ys.length === 0) return undefined
    const lo = Math.min(...ys)
    const hi = Math.max(...ys)
    const pad = Math.max((hi - lo) * 0.1, 0.05)
    return [Math.max(0, lo - pad), hi + pad]
  }, [busPoints, truckPoints])

  const allPoints = [...busPoints, ...truckPoints]

  return (
    <>
      <div className="eff-top">
        <p className="muted eff-subtitle">
          One dot per vehicle · {periodText(period, windowEnd)} from Fleetx · odometer as of the last morning ping, not
          filtered by date range
        </p>
        <div className="eff-top-actions">
          <div className="customer-nav">
            {PERIOD_OPTIONS.map((o) => (
              <button
                key={o.value}
                className={`seg-pill ${period === o.value ? "active" : ""}`}
                onClick={() => setPeriod(o.value)}
              >
                {o.label}
              </button>
            ))}
          </div>
          <Link className="eff-help" to="/glossary#page-by-page">
            What is km/kWh?
          </Link>
        </div>
      </div>

      <div className="eff-grid">
        <EfficiencyCard
          title="EV Buses"
          type="Bus"
          rows={busRows}
          points={busPoints}
          yRange={yRange}
          period={period}
          oemByCustomer={oemByCustomer}
        />
        <EfficiencyCard
          title="Trucks"
          type="Truck"
          rows={truckRows}
          points={truckPoints}
          yRange={yRange}
          period={period}
          oemByCustomer={oemByCustomer}
        />
      </div>

      <div className="eff-bottom">
        {allPoints.length > 0 ? (
          <button className="btn-action" onClick={() => setShowTable((v) => !v)}>
            {showTable ? "Hide table" : "View as table"}
          </button>
        ) : (
          <span />
        )}
        <span className="muted eff-note">
          Both charts share one y-axis range · click a customer to hide it, double-click to isolate
        </span>
      </div>
      {showTable && allPoints.length > 0 && <EfficiencyTable points={allPoints} period={period} />}
    </>
  )
}

function EfficiencyCard({
  title,
  type,
  rows,
  points,
  yRange,
  period,
  oemByCustomer,
}: {
  title: string
  type: VehicleType
  rows: VehicleRecord[]
  points: EffPoint[]
  yRange: [number, number] | undefined
  period: Period
  oemByCustomer: Partial<Record<CustomerName, string>>
}) {
  const [hidden, setHidden] = useState<Set<CustomerName>>(new Set())
  const symbol = type === "Truck" ? "diamond" : "circle"

  // Every customer of this type in scope gets a chip (fixed roster order), even
  // if none of its vehicles is plotted, so the colour key stays stable.
  const chipCustomers = ALL_CUSTOMERS.filter((c) => rows.some((v) => v.customer === c))

  const toggle = (c: CustomerName) =>
    setHidden((prev) => {
      const next = new Set(prev)
      if (next.has(c)) next.delete(c)
      else next.add(c)
      return next
    })
  const isolate = (c: CustomerName) =>
    setHidden((prev) => {
      const others = chipCustomers.filter((x) => x !== c)
      const alreadyIsolated = !prev.has(c) && others.every((x) => prev.has(x))
      return alreadyIsolated ? new Set() : new Set(others)
    })

  const traces = chipCustomers
    .map((c) => ({ c, pts: points.filter((p) => p.v.customer === c) }))
    .filter(({ c, pts }) => pts.length > 0 && !hidden.has(c))
    .map(({ c, pts }) => ({
      type: "scatter" as const,
      mode: "markers" as const,
      name: c,
      x: pts.map((p) => p.odometer),
      y: pts.map((p) => p.kmPerKwh),
      marker: { size: 11, color: CUSTOMER_COLORS[c], symbol, line: { width: 1.5, color: "#ffffff" } },
      customdata: pts.map((p) => [
        p.v.plate,
        [p.v.vehicle_type, (oemByCustomer[c] ?? "").replace(/\s*\((Bus|Truck)\)\s*$/, ""), p.v.vehicle_model]
          .filter(Boolean)
          .join(" · "),
        p.kwh != null ? `${Math.round(p.kwh).toLocaleString("en-IN")} kWh in the last ${period} days` : "",
      ]),
      hovertemplate:
        `<b>%{customdata[0]} · ${c}</b>` +
        '<br><span style="color:#94a3b8">%{customdata[1]}</span>' +
        "<br><b>%{y:.2f} km/kWh</b> at <b>%{x:,.0f} km</b>" +
        '<br><span style="color:#94a3b8">%{customdata[2]}</span><extra></extra>',
    }))

  const plotted = new Set(points.map((p) => p.v.plate))
  const missingByCustomer = ALL_CUSTOMERS.map(
    (c) => [c, rows.filter((v) => v.customer === c && !plotted.has(v.plate)).length] as [CustomerName, number]
  ).filter(([, n]) => n > 0)

  return (
    <section className="eff-card">
      <div className="eff-card-head">
        <h3>{title}</h3>
        <span className="muted">
          {points.length} plotted of {rows.length} · {type === "Truck" ? "◆ diamond" : "● circle"}
        </span>
      </div>

      {chipCustomers.length > 0 && (
        <div className="eff-chips">
          {chipCustomers.map((c) => (
            <button
              key={c}
              className={`eff-chip ${hidden.has(c) ? "off" : ""}`}
              onClick={() => toggle(c)}
              onDoubleClick={() => isolate(c)}
              title="Click to hide, double-click to isolate"
            >
              <span className={`eff-chip-mark ${type === "Truck" ? "diamond" : ""}`} style={{ background: CUSTOMER_COLORS[c] }} />
              {c}
            </button>
          ))}
        </div>
      )}

      {points.length === 0 ? (
        <div className="eff-empty muted">
          {rows.length === 0
            ? "No vehicles of this type match the customer filter."
            : `No vehicle has kWh from Fleetx in the last ${period} days, so there is nothing to plot.`}
        </div>
      ) : (
        <Plot
          data={traces}
          layout={{
            ...PLOT_LAYOUT_BASE,
            height: 340,
            margin: { l: 60, r: 16, t: 10, b: 50 },
            hovermode: "closest",
            hoverlabel: {
              bgcolor: "#0f172a",
              bordercolor: "#0f172a",
              font: { color: "#ffffff", size: 12 },
              align: "left",
            },
            uirevision: `${type}|${rows.length}|${period}`,
            showlegend: false,
            xaxis: {
              title: { text: "Lifetime odometer (km)" },
              rangemode: "tozero",
              tickformat: ",.0f",
              zeroline: false,
              showgrid: false,
            },
            yaxis: {
              title: { text: "Energy efficiency (km/kWh)" },
              range: yRange,
              tickformat: ".1f",
              zeroline: false,
              gridcolor: "rgba(148,163,184,0.25)",
            },
          }}
          config={{ responsive: true, displayModeBar: false }}
          style={{ width: "100%" }}
          useResizeHandler
        />
      )}

      {missingByCustomer.length > 0 && (
        <div className="muted eff-foot">
          Not plotted, no kWh from Fleetx in the last {period} days:{" "}
          {missingByCustomer.map(([c, n]) => `${c} (${n})`).join(", ")}
        </div>
      )}
    </section>
  )
}

function EfficiencyTable({ points, period }: { points: EffPoint[]; period: Period }) {
  const sorted = [...points].sort((a, b) => a.kmPerKwh - b.kmPerKwh)
  return (
    <div className="table-container" style={{ maxHeight: 360, marginTop: "0.75rem" }}>
      <table>
        <thead>
          <tr>
            <th>Vehicle</th>
            <th>Customer</th>
            <th>Type</th>
            <th style={{ textAlign: "right" }}>Lifetime odometer</th>
            <th style={{ textAlign: "right" }}>Energy, last {period} days</th>
            <th style={{ textAlign: "right" }}>Efficiency</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((p) => (
            <tr key={p.v.plate}>
              <td>{p.v.plate}</td>
              <td>{p.v.customer}</td>
              <td>{p.v.vehicle_type}</td>
              <td style={{ textAlign: "right" }}>{fmtKm(p.odometer)}</td>
              <td style={{ textAlign: "right" }}>
                {p.kwh != null ? `${Math.round(p.kwh).toLocaleString("en-IN")} kWh` : "—"}
              </td>
              <td style={{ textAlign: "right" }}>{fmtEff(p.kmPerKwh)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
