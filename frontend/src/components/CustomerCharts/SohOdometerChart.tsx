import { useMemo, useState } from "react"
import Plot from "../../plotly-shim"
import type { CrosstabCustomer, SohOdometerPoint, SohOdometerResponse } from "../../api/types"
import { ALL_CUSTOMERS, CUSTOMER_COLORS, PLOT_LAYOUT_BASE } from "./colors"

type VehicleTypeFilter = "All" | "Bus" | "Truck"

const TYPE_OPTIONS: { value: VehicleTypeFilter; label: string }[] = [
  { value: "All", label: "All vehicles" },
  { value: "Bus", label: "Buses" },
  { value: "Truck", label: "Trucks" },
]

function formatIst(iso: string | null): string {
  if (!iso) return "—"
  return new Date(iso).toLocaleString("en-IN", {
    timeZone: "Asia/Kolkata", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit",
  })
}

/** Latest SoH vs latest live odometer, one point per vehicle. Filtered by
 * the page's customer filter and this chart's own Bus/Truck pills; within
 * that, clicking a legend entry hides/shows a customer (double-click
 * isolates it). Shape carries vehicle type (circle = bus, diamond = truck);
 * a hollow marker is a flagged reading -- hover says why. */
export function SohOdometerChart({ data, customer }: { data: SohOdometerResponse; customer: CrosstabCustomer }) {
  const [vehicleType, setVehicleType] = useState<VehicleTypeFilter>("All")
  const [showTable, setShowTable] = useState(false)

  const matches = (r: { customer: string; vehicle_type: string }) =>
    (customer === "All" || r.customer === customer) && (vehicleType === "All" || r.vehicle_type === vehicleType)

  const points = useMemo(() => data.rows.filter(matches), [data, customer, vehicleType])
  const missing = useMemo(() => data.missing_soh.filter(matches), [data, customer, vehicleType])

  // Fixed roster order, so a customer keeps its color and legend slot however the filters change.
  const traces = ALL_CUSTOMERS.map((c) => ({ c, rows: points.filter((r) => r.customer === c) }))
    .filter((t) => t.rows.length > 0)
    .map(({ c, rows }) => {
      const color = CUSTOMER_COLORS[c]
      return {
        type: "scatter" as const,
        mode: "markers" as const,
        name: c,
        x: rows.map((r) => r.odometer_km),
        y: rows.map((r) => r.soh_pct),
        marker: {
          size: 11,
          color,
          symbol: rows.map((r) => (r.vehicle_type === "Truck" ? "diamond" : "circle") + (r.flagged ? "-open" : "")),
          line: { width: rows.map((r) => (r.flagged ? 2 : 1.5)), color: rows.map((r) => (r.flagged ? color : "#ffffff")) },
        },
        customdata: rows.map((r) => [
          r.plate,
          [r.vehicle_type, r.oem, r.vehicle_model].filter(Boolean).join(" · "),
          `Read ${formatIst(r.soh_reported_at)} from ${r.device}`,
          r.notes.length ? `<br><i>${r.notes.join("<br>")}</i>` : "",
        ]),
        hovertemplate:
          "<b>%{customdata[0]}</b> · " + c +
          "<br>%{customdata[1]}<br>SoH <b>%{y:.1f}%</b> at <b>%{x:,.0f} km</b>" +
          "<br>%{customdata[2]}%{customdata[3]}<extra></extra>",
      }
    })

  const flaggedCount = points.filter((r) => r.flagged).length
  const missingByCustomer = ALL_CUSTOMERS.map((c) => [c, missing.filter((m) => m.customer === c).length] as const).filter(
    ([, n]) => n > 0
  )

  return (
    <>
      <div className="filter-bar" style={{ padding: 0, marginBottom: "0.75rem" }}>
        <div className="customer-nav">
          {TYPE_OPTIONS.map((o) => (
            <button
              key={o.value}
              className={`seg-pill ${vehicleType === o.value ? "active" : ""}`}
              onClick={() => setVehicleType(o.value)}
            >
              {o.label}
            </button>
          ))}
        </div>
        <span className="muted" style={{ fontSize: "0.8rem" }}>
          {points.length} vehicle{points.length === 1 ? "" : "s"} · as of the morning ping, {formatIst(data.last_pinged_at)} IST
          · ● bus ◆ truck · hollow = flagged, hover for why{flaggedCount ? ` (${flaggedCount})` : ""}
        </span>
      </div>

      {points.length === 0 ? (
        <div className="muted" style={{ padding: "2rem 0", textAlign: "center" }}>
          {data.rows.length === 0
            ? "No SoH data yet — it fills in after the first morning ping."
            : "No vehicles with a reported SoH match these filters."}
        </div>
      ) : (
        <Plot
          data={traces}
          layout={{
            ...PLOT_LAYOUT_BASE,
            height: 420,
            margin: { l: 60, r: 20, t: 10, b: 50 },
            hovermode: "closest",
            // Keeps legend show/hide choices until a filter changes the set of points.
            uirevision: `${customer}|${vehicleType}`,
            xaxis: { title: { text: "Latest odometer (km)" }, rangemode: "tozero", tickformat: ",.0f", zeroline: false, gridcolor: "rgba(148,163,184,0.18)" },
            yaxis: { title: { text: "Battery state of health (%)" }, ticksuffix: "%", zeroline: false, gridcolor: "rgba(148,163,184,0.18)" },
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
          Not plotted, no SoH reported by their devices yet:{" "}
          {missingByCustomer.map(([c, n]) => `${c} (${n})`).join(", ")}. They appear here once a morning ping picks one up.
        </div>
      )}

      {points.length > 0 && (
        <div style={{ marginTop: "0.75rem" }}>
          <button className="seg-pill" onClick={() => setShowTable((v) => !v)}>
            {showTable ? "Hide data table" : "View as table"}
          </button>
          {showTable && <SohTable rows={points} />}
        </div>
      )}
    </>
  )
}

function SohTable({ rows }: { rows: SohOdometerPoint[] }) {
  const sorted = [...rows].sort((a, b) => a.soh_pct - b.soh_pct || b.odometer_km - a.odometer_km)
  return (
    <div className="table-container" style={{ maxHeight: 360, marginTop: "0.75rem" }}>
      <table>
        <thead>
          <tr>
            <th>Vehicle</th>
            <th>Customer</th>
            <th>Type</th>
            <th style={{ textAlign: "right" }}>SoH</th>
            <th style={{ textAlign: "right" }}>Odometer</th>
            <th>Read at (IST)</th>
            <th>Notes</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((r) => (
            <tr key={r.plate}>
              <td>{r.plate}</td>
              <td>{r.customer}</td>
              <td>{r.vehicle_type}</td>
              <td style={{ textAlign: "right" }}>{r.soh_pct.toFixed(1)}%</td>
              <td style={{ textAlign: "right" }}>{Math.round(r.odometer_km).toLocaleString("en-IN")} km</td>
              <td>{formatIst(r.soh_reported_at)}</td>
              <td style={{ whiteSpace: "normal", color: "var(--text-secondary)" }}>{r.notes.join("; ") || "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
