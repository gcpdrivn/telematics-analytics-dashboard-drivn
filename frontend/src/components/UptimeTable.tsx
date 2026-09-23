import { useEffect, useMemo, useState } from "react"
import { downloadUptimeExport, getVehicleUptime } from "../api/client"
import type { DateRange } from "../api/client"
import type { CrosstabCustomer, UptimeResponse, VehicleUptime } from "../api/types"

type SortKey = "vehicle_number" | "uptime_pct" | "ran_days" | "not_run_days" | "not_sure_days"

function formatDateHeader(iso: string): string {
  const [, m, d] = iso.split("-")
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
  return `${d} ${months[Number(m) - 1]}`
}

function contrastText(hex: string): string {
  const c = hex.replace("#", "")
  const r = parseInt(c.substring(0, 2), 16)
  const g = parseInt(c.substring(2, 4), 16)
  const b = parseInt(c.substring(4, 6), 16)
  const luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255
  return luminance > 0.6 ? "#1a1a1a" : "#ffffff"
}

export function UptimeTable({ range, customer }: { range: DateRange; customer: CrosstabCustomer }) {
  const [data, setData] = useState<UptimeResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [exporting, setExporting] = useState(false)
  const [sortKey, setSortKey] = useState<SortKey>("uptime_pct")
  const [sortDir, setSortDir] = useState<1 | -1>(1)

  useEffect(() => {
    let cancelled = false
    setData(null)
    setError(null)
    getVehicleUptime(customer, range)
      .then((d) => !cancelled && setData(d))
      .catch((e) => !cancelled && setError(String(e)))
    return () => {
      cancelled = true
    }
  }, [customer, range.start, range.end])

  const rows = useMemo(() => {
    if (!data) return []
    const copy = [...data.vehicles]
    copy.sort((a, b) => {
      const av = a[sortKey]
      const bv = b[sortKey]
      const an = av === null ? -Infinity : (av as number)
      const bn = bv === null ? -Infinity : (bv as number)
      if (typeof av === "string" || typeof bv === "string") {
        return sortDir * String(av).localeCompare(String(bv))
      }
      return sortDir * (an - bn)
    })
    return copy
  }, [data, sortKey, sortDir])

  function headerClick(key: SortKey) {
    if (sortKey === key) {
      setSortDir((d) => (d === 1 ? -1 : 1))
    } else {
      setSortKey(key)
      setSortDir(key === "vehicle_number" ? 1 : -1)
    }
  }

  async function handleExport() {
    setExporting(true)
    try {
      await downloadUptimeExport(customer, range)
    } catch (e) {
      setError(String(e))
    } finally {
      setExporting(false)
    }
  }

  const legend = data ? data.legend_combined : []
  const detailedLabels = data ? Object.fromEntries(data.legend_detailed.map((l) => [l.status, l.label])) : {}

  return (
    <div className="panel">
      <div className="panel-header">Vehicle Uptime Calendar</div>
      <div className="panel-body">
        <div className="uptime-toolbar">
          <div className="uptime-export-actions">
            <button className="btn-action" disabled={exporting} onClick={() => handleExport()}>
              {exporting ? "Exporting…" : "⬇️ Export"}
            </button>
          </div>
        </div>

        <div className="uptime-legend">
          {legend.map((item) => (
            <span key={item.status} className="uptime-legend-item">
              <span
                className={`uptime-swatch ${item.color ? "" : "uptime-swatch-blank"}`}
                style={item.color ? { background: item.color, color: contrastText(item.color) } : undefined}
              >
                {item.index}
              </span>
              {item.label}
            </span>
          ))}
        </div>

        {error && <div className="error-box">Failed to load: {error}</div>}
        {!error && !data && <div className="loading">Loading uptime calendar…</div>}

        {data && data.vehicles.length === 0 && (
          <div className="muted" style={{ padding: "1rem 0" }}>
            No vehicles found for this filter/date range.
          </div>
        )}

        {data && data.vehicles.length > 0 && (
          <div className="table-container uptime-table-container">
            <table className="uptime-table">
              <thead>
                <tr>
                  <th className="uptime-sticky-col" onClick={() => headerClick("vehicle_number")}>
                    Vehicle Number
                  </th>
                  <th className="uptime-sticky-col uptime-col-2">Type</th>
                  <th className="uptime-sticky-col uptime-col-3">Model</th>
                  <th className="uptime-sticky-col uptime-col-4">Customer</th>
                  <th className="uptime-sticky-col uptime-col-5" onClick={() => headerClick("uptime_pct")}>
                    Uptime %
                  </th>
                  {data.dates.map((d) => (
                    <th key={d} className="uptime-date-col">
                      {formatDateHeader(d)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((v: VehicleUptime) => (
                  <tr key={v.vehicle_number}>
                    <td className="uptime-sticky-col plate-cell">{v.vehicle_number}</td>
                    <td className="uptime-sticky-col uptime-col-2">
                      <span className={`type-pill ${v.vehicle_type === "Bus" ? "bus" : "truck"}`}>
                        {v.vehicle_type}
                      </span>
                    </td>
                    <td className="uptime-sticky-col uptime-col-3">{v.vehicle_model}</td>
                    <td className="uptime-sticky-col uptime-col-4">{v.customer_name}</td>
                    <td className="uptime-sticky-col uptime-col-5" style={{ fontWeight: 700 }}>
                      {v.uptime_pct !== null ? `${v.uptime_pct.toFixed(1)}%` : "—"}
                    </td>
                    {v.daily.map((day) => {
                      const item = legend.find((l) => l.status === day.combined_status)
                      const detail = detailedLabels[day.detailed_status] ?? day.detailed_status
                      return (
                        <td
                          key={day.date}
                          className="uptime-day-cell"
                          style={item?.color ? { background: item.color } : undefined}
                          title={day.note ?? `${day.date}: ${item?.label ?? day.combined_status} (${detail})`}
                        />
                      )
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
