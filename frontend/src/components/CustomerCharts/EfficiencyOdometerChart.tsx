import { useMemo, useState } from "react"
import Plot from "../../plotly-shim"
import type { CrosstabCustomer, CustomerName, EnergyWindow, VehicleRecord } from "../../api/types"
import { ALL_CUSTOMERS, CUSTOMER_COLORS, PLOT_LAYOUT_BASE } from "./colors"

type VehicleType = "Bus" | "Truck"
type Period = "lifetime" | EnergyWindow

const PERIOD_OPTIONS: { value: Period; label: string }[] = [
  { value: "lifetime", label: "Lifetime" },
  { value: "15", label: "Last 15 days" },
  { value: "7", label: "Last 7 days" },
  { value: "3", label: "Last 3 days" },
]

interface EffPoint {
  v: VehicleRecord
  odometer: number
  kmPerKwh: number
  kwh: number | null
}

/** A vehicle is plotted only with both a positive odometer and a positive
 * km/kWh for the selected period: lifetime km ÷ lifetime kWh, or km ÷ kWh
 * over the last 3 / 7 / 15 complete days, all from Fleetx trips. A vehicle
 * with no energy trips in the period lands in "not plotted" rather than
 * being drawn at a made-up value. */
function toPoint(v: VehicleRecord, period: Period): EffPoint | null {
  // Morning-ping odometer first; the history-derived one only as a fallback.
  const odometer = v.live_odometer_km != null && v.live_odometer_km > 0 ? v.live_odometer_km : v.final_odometer
  const win = period === "lifetime" ? null : v.energy_windows?.[period] ?? null
  const kmPerKwh = period === "lifetime" ? v.energy_km_per_kwh ?? null : win?.km_per_kwh ?? null
  const kwh = period === "lifetime" ? v.energy_kwh ?? null : win?.kwh ?? null
  if (odometer == null || !(odometer > 0) || kmPerKwh == null || !(kmPerKwh > 0)) return null
  return { v, odometer, kmPerKwh, kwh }
}

function formatDay(iso: string | null | undefined): string {
  if (!iso) return ""
  return new Date(`${iso}T00:00:00`).toLocaleDateString("en-IN", { day: "numeric", month: "short" })
}

function periodText(period: Period, end: string | null | undefined): string {
  if (period === "lifetime") return "lifetime km ÷ lifetime kWh"
  const n = Number(period)
  if (!end) return `km ÷ kWh over the last ${n} days`
  const start = new Date(`${end}T00:00:00`)
  start.setDate(start.getDate() - (n - 1))
  return `km ÷ kWh over the last ${n} days (${formatDay(start.toISOString().slice(0, 10))} – ${formatDay(end)})`
}

function median(values: number[]): number | null {
  if (values.length === 0) return null
  const s = [...values].sort((a, b) => a - b)
  const mid = Math.floor(s.length / 2)
  return s.length % 2 ? s[mid] : (s[mid - 1] + s[mid]) / 2
}

const fmtKm = (n: number) => `${Math.round(n).toLocaleString("en-IN")} km`
const fmtEff = (n: number) => `${n.toFixed(2)} km/kWh`

/** Energy Efficiency vs Odometer (design option C): one row per vehicle type,
 * each a scatter (lifetime odometer × lifetime km/kWh, one dot per vehicle,
 * one trace per customer) beside a rail with each customer's median km/kWh
 * and the three least efficient vehicles. Not date-ranged: both axes are
 * lifetime figures. Both rows share one y-range so the same height means the
 * same km/kWh for a bus and a truck. */
export function EfficiencyOdometerChart({
  buses,
  trucks,
  customer,
}: {
  buses: VehicleRecord[]
  trucks: VehicleRecord[]
  customer: CrosstabCustomer
}) {
  const [showTable, setShowTable] = useState(false)
  const [period, setPeriod] = useState<Period>("lifetime")

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
  const hasWindows = windowEnd != null

  // Shared y-range across both rows, padded 10% each side.
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
      {hasWindows && (
        <div className="filter-bar" style={{ padding: 0, marginBottom: "0.5rem" }}>
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
        </div>
      )}
      <p className="muted" style={{ fontSize: "0.8rem", margin: "0 0 1rem" }}>
        One dot per vehicle · km/kWh = {periodText(period, windowEnd)} from Fleetx · odometer as of the morning ping ·
        not filtered by the page's date range · ● bus ◆ truck · click a customer in a legend to hide it, double-click
        to isolate
      </p>

      <EfficiencySection title="EV Buses" type="Bus" rows={busRows} points={busPoints} yRange={yRange} period={period} />
      <EfficiencySection title="Trucks" type="Truck" rows={truckRows} points={truckPoints} yRange={yRange} period={period} />

      {allPoints.length > 0 && (
        <div style={{ marginTop: "0.75rem" }}>
          <button className="seg-pill" onClick={() => setShowTable((v) => !v)}>
            {showTable ? "Hide data table" : "View as table"}
          </button>
          {showTable && <EfficiencyTable points={allPoints} period={period} />}
        </div>
      )}
    </>
  )
}

function EfficiencySection({
  title,
  type,
  rows,
  points,
  yRange,
  period,
}: {
  title: string
  type: VehicleType
  rows: VehicleRecord[]
  points: EffPoint[]
  yRange: [number, number] | undefined
  period: Period
}) {
  const symbol = type === "Truck" ? "diamond" : "circle"
  const kwhLabel = period === "lifetime" ? "kWh consumed to date" : `kWh in the last ${period} days`

  // Fixed roster order, so a customer keeps its colour and legend slot.
  const byCustomer = ALL_CUSTOMERS.map((c) => ({ c, pts: points.filter((p) => p.v.customer === c) })).filter(
    (g) => g.pts.length > 0
  )

  const traces = byCustomer.map(({ c, pts }) => ({
    type: "scatter" as const,
    mode: "markers" as const,
    name: c,
    x: pts.map((p) => p.odometer),
    y: pts.map((p) => p.kmPerKwh),
    marker: { size: 11, color: CUSTOMER_COLORS[c], symbol, line: { width: 1.5, color: "#ffffff" } },
    customdata: pts.map((p) => [
      p.v.plate,
      [p.v.vehicle_type, p.v.vehicle_model].filter(Boolean).join(" · "),
      p.kwh != null ? `${Math.round(p.kwh).toLocaleString("en-IN")} ${kwhLabel}` : "",
    ]),
    hovertemplate:
      "<b>%{customdata[0]}</b> · " +
      c +
      "<br>%{customdata[1]}<br><b>%{y:.2f} km/kWh</b> at <b>%{x:,.0f} km</b>" +
      "<br>%{customdata[2]}<extra></extra>",
  }))

  const fleetMedian = median(points.map((p) => p.kmPerKwh))

  const customerMedians = byCustomer
    .map(({ c, pts }) => ({ c, med: median(pts.map((p) => p.kmPerKwh)) as number, n: pts.length }))
    .sort((a, b) => b.med - a.med)
  const maxMed = Math.max(0, ...customerMedians.map((m) => m.med))

  const lowest = [...points].sort((a, b) => a.kmPerKwh - b.kmPerKwh).slice(0, 3)

  const plotted = new Set(points.map((p) => p.v.plate))
  const missing = rows.filter((v) => !plotted.has(v.plate))
  const missingByCustomer = ALL_CUSTOMERS.map(
    (c) => [c, missing.filter((v) => v.customer === c).length] as [CustomerName, number]
  ).filter(([, n]) => n > 0)

  return (
    <section className="eff-section">
      <div style={{ minWidth: 0 }}>
        <div className="eff-section-head">
          <h3>{title}</h3>
          <span className="muted" style={{ fontSize: "0.8rem" }}>
            {points.length} plotted of {rows.length}
          </span>
        </div>

        {points.length === 0 ? (
          <div className="eff-empty muted">
            {rows.length === 0
              ? "No vehicles of this type match the customer filter."
              : "No vehicle has lifetime energy (kWh) from Fleetx yet, so there is nothing to plot. Dots appear here once the backend serves energy_km_per_kwh."}
          </div>
        ) : (
          <Plot
            data={traces}
            layout={{
              ...PLOT_LAYOUT_BASE,
              height: 360,
              margin: { l: 60, r: 20, t: 10, b: 50 },
              hovermode: "closest",
              uirevision: `${type}|${rows.length}|${period}`,
              xaxis: {
                title: { text: "Lifetime odometer (km)" },
                rangemode: "tozero",
                tickformat: ",.0f",
                zeroline: false,
                gridcolor: "rgba(148,163,184,0.18)",
              },
              yaxis: {
                title: { text: "Energy efficiency (km/kWh)" },
                range: yRange,
                tickformat: ".1f",
                zeroline: false,
                gridcolor: "rgba(148,163,184,0.18)",
              },
              shapes:
                fleetMedian != null
                  ? [
                      {
                        type: "line",
                        xref: "paper",
                        x0: 0,
                        x1: 1,
                        y0: fleetMedian,
                        y1: fleetMedian,
                        line: { color: "rgba(100,116,139,0.8)", width: 1, dash: "dash" },
                      },
                    ]
                  : [],
              annotations:
                fleetMedian != null
                  ? [
                      {
                        xref: "paper",
                        x: 1,
                        y: fleetMedian,
                        xanchor: "right",
                        yanchor: "bottom",
                        showarrow: false,
                        text: `Median ${fleetMedian.toFixed(2)}`,
                        font: { size: 11, color: "rgba(100,116,139,1)" },
                      },
                    ]
                  : [],
              showlegend: true,
              legend: { orientation: "h", x: 0, y: 1.02, yanchor: "bottom", itemclick: "toggle", itemdoubleclick: "toggleothers" },
            }}
            config={{ responsive: true, displayModeBar: false }}
            style={{ width: "100%" }}
            useResizeHandler
          />
        )}

        {missingByCustomer.length > 0 && (
          <div className="muted" style={{ fontSize: "0.8rem", marginTop: "0.5rem" }}>
            Not plotted, no kWh from Fleetx {period === "lifetime" ? "yet" : `in the last ${period} days`}:{" "}
            {missingByCustomer.map(([c, n]) => `${c} (${n})`).join(", ")}.
          </div>
        )}
      </div>

      <aside className="eff-rail">
        <div>
          <h4>Median km/kWh by customer</h4>
          <span className="muted" style={{ fontSize: "0.75rem" }}>
            Best to worst
          </span>
        </div>
        {customerMedians.length === 0 ? (
          <span className="muted" style={{ fontSize: "0.8rem" }}>
            No efficiency data yet.
          </span>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: "0.6rem" }}>
            {customerMedians.map(({ c, med, n }) => (
              <div className="eff-bar-row" key={c} title={`${c}: median of ${n} vehicle${n === 1 ? "" : "s"}`}>
                <span className="eff-bar-name">{c}</span>
                <span className="eff-bar-track">
                  <span
                    className="eff-bar-fill"
                    style={{ width: `${maxMed > 0 ? (med / maxMed) * 100 : 0}%`, background: CUSTOMER_COLORS[c] }}
                  />
                </span>
                <span className="eff-bar-value">{med.toFixed(2)}</span>
              </div>
            ))}
          </div>
        )}

        <div className="eff-rail-divider" />

        <h4>Lowest efficiency, check first</h4>
        {lowest.length === 0 ? (
          <span className="muted" style={{ fontSize: "0.8rem" }}>
            Nothing to rank yet.
          </span>
        ) : (
          <ol className="eff-lowest">
            {lowest.map((p) => (
              <li key={p.v.plate}>
                <b>{p.v.plate}</b> · {p.v.customer} · {fmtEff(p.kmPerKwh)}
              </li>
            ))}
          </ol>
        )}
      </aside>
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
            <th style={{ textAlign: "right" }}>{period === "lifetime" ? "Energy to date" : `Energy, last ${period} days`}</th>
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
