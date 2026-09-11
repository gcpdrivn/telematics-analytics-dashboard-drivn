import { useState } from "react"
import type { CrosstabCustomer, CrosstabResponse } from "../api/types"

/** Hand-rolled heatmap table (not Plotly) -- ports the exact cell alpha
 * formula and click-to-lock Duty Inspector behavior found in report.html's
 * client JS: alpha = max(0.12, pct/100*0.95), border = min(1, alpha+0.35). */

const BAND_RGBA = {
  truck: ["100, 116, 139", "59, 130, 246", "6, 182, 212", "92, 176, 48"],
  bus: ["100, 116, 139", "59, 130, 246", "6, 182, 212", "92, 176, 48", "139, 92, 246"],
}

const CUSTOMER_FILTERS: CrosstabCustomer[] = ["All", "FreshBus", "ZingBus", "BillionE"]

function formatShortDate(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number)
  const dt = new Date(y, m - 1, d)
  const dayNames = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
  const monthNames = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
  ]
  return `${String(d).padStart(2, "0")} ${monthNames[m - 1]} (${dayNames[dt.getDay()]})`
}

export function CrosstabMatrix({
  data,
  customer,
  onCustomerChange,
}: {
  data: CrosstabResponse
  customer: CrosstabCustomer
  onCustomerChange: (c: CrosstabCustomer) => void
}) {
  const [selected, setSelected] = useState<{ date: string; bandIdx: number } | null>(null)
  const [hover, setHover] = useState<{ date: string; bandIdx: number; x: number; y: number } | null>(null)

  const isTruck = customer === "BillionE"
  const palette = isTruck ? BAND_RGBA.truck : BAND_RGBA.bus
  const bandOrderDesc = [...data.bands.keys()].reverse()

  function cellClick(date: string, bandIdx: number) {
    setSelected((prev) => (prev && prev.date === date && prev.bandIdx === bandIdx ? null : { date, bandIdx }))
  }

  const selectedEntry = selected ? data.by_date.find((d) => d.date === selected.date) ?? null : null

  return (
    <div>
      <div className="customer-nav">
        {CUSTOMER_FILTERS.map((c) => (
          <button key={c} className={`seg-pill ${customer === c ? "active" : ""}`} onClick={() => onCustomerChange(c)}>
            {c}
          </button>
        ))}
      </div>

      <div className="kpi-row" style={{ marginBottom: "1rem" }}>
        <div className="kpi-box">
          <div className="kpi-label">Evaluated Vehicle Runs</div>
          <div className="kpi-num" style={{ fontSize: "1.4rem" }}>{data.total_runs}</div>
        </div>
        <div className="kpi-box">
          <div className="kpi-label">Long-Haul Share ({data.long_title})</div>
          <div className="kpi-num" style={{ fontSize: "1.4rem" }}>{data.long_share}%</div>
        </div>
        <div className="kpi-box">
          <div className="kpi-label">Mid-Haul Share ({data.mid_title})</div>
          <div className="kpi-num" style={{ fontSize: "1.4rem" }}>{data.mid_share}%</div>
        </div>
        <div className="kpi-box">
          <div className="kpi-label">Peak Daily Run</div>
          <div className="kpi-num" style={{ fontSize: "1.05rem" }}>
            {data.peak_run ? `${data.peak_run.plate} • ${data.peak_run.km} km` : "—"}
          </div>
          {data.peak_run && (
            <div className="kpi-sub">
              {data.peak_run.date} ({data.peak_run.cust})
            </div>
          )}
        </div>
      </div>

      <div className="table-container" style={{ maxHeight: 480 }}>
        <table>
          <thead>
            <tr>
              <th>Band</th>
              {data.dates.map((d) => (
                <th key={d} style={{ textAlign: "center" }}>
                  {formatShortDate(d)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {bandOrderDesc.map((bandIdx) => (
              <tr key={data.bands[bandIdx].id}>
                <td style={{ fontWeight: 700 }}>{data.bands[bandIdx].name}</td>
                {data.by_date.map((entry) => {
                  const cnt = entry.counts[bandIdx]
                  const pct = entry.pcts[bandIdx]
                  const isZero = cnt === 0
                  const alpha = isZero ? 0 : Math.max(0.12, (pct / 100) * 0.95)
                  const borderAlpha = isZero ? 0.08 : Math.min(1, alpha + 0.35)
                  const rgb = palette[bandIdx]
                  const isSelected = selected?.date === entry.date && selected?.bandIdx === bandIdx
                  return (
                    <td
                      key={entry.date}
                      onClick={() => cellClick(entry.date, bandIdx)}
                      onMouseEnter={(e) => setHover({ date: entry.date, bandIdx, x: e.clientX, y: e.clientY })}
                      onMouseLeave={() => setHover(null)}
                      style={{
                        textAlign: "center",
                        cursor: "pointer",
                        backgroundColor: isZero ? "var(--bg-subtle)" : `rgba(${rgb}, ${alpha.toFixed(2)})`,
                        border: `1px solid ${isZero ? "var(--border-subtle)" : `rgba(${rgb}, ${borderAlpha.toFixed(2)})`}`,
                        outline: isSelected ? "2px solid var(--text-primary)" : "none",
                        outlineOffset: -2,
                        color: !isZero && pct > 55 ? "#0f172a" : "var(--text-primary)",
                        fontWeight: 600,
                        fontSize: "0.75rem",
                      }}
                    >
                      {pct > 0 ? `${pct}%` : "—"}
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
        {hover && (
          <div
            style={{
              position: "fixed",
              left: hover.x + 12,
              top: hover.y + 12,
              background: "var(--bg-surface)",
              border: "1px solid var(--border-strong)",
              borderRadius: 6,
              padding: "6px 10px",
              fontSize: "0.75rem",
              boxShadow: "var(--shadow-hover)",
              pointerEvents: "none",
              zIndex: 10,
            }}
          >
            {(() => {
              const entry = data.by_date.find((d) => d.date === hover.date)
              if (!entry) return null
              return (
                <>
                  <div style={{ fontWeight: 700 }}>{data.bands[hover.bandIdx].name}</div>
                  <div>
                    {formatShortDate(hover.date)}: {entry.counts[hover.bandIdx]} vehicles (
                    {entry.pcts[hover.bandIdx]}%)
                  </div>
                </>
              )
            })()}
          </div>
        )}
      </div>

      <div className="panel" style={{ marginTop: "1rem" }}>
        <div className="panel-header">Duty Inspector</div>
        <div className="panel-body">
          {!selected || !selectedEntry ? (
            <div className="muted">Click a cell in the matrix above to inspect vehicles on that day/band.</div>
          ) : (
            <div style={{ display: "flex", flexWrap: "wrap", gap: "0.75rem" }}>
              {selectedEntry.veh_details[selected.bandIdx].map((v) => (
                <div key={v.p} className="panel" style={{ margin: 0, padding: "0.6rem 0.85rem", minWidth: 180 }}>
                  <div style={{ fontWeight: 700 }}>{v.p}</div>
                  <div className="muted" style={{ fontSize: "0.75rem" }}>
                    {v.c} • {v.m}
                  </div>
                  <div style={{ fontSize: "0.8rem", marginTop: 4 }}>
                    {v.km} km • {v.hrs} h • {v.spd} km/h
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
