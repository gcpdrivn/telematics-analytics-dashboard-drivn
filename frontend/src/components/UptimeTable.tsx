import { useEffect, useMemo, useRef, useState } from "react"
import { downloadUptimeExport, getVehicleUptime } from "../api/client"
import type { DateRange } from "../api/client"
import type { CombinedUptimeStatus, CrosstabCustomer, UptimeDay, UptimeResponse, VehicleUptime } from "../api/types"
import "./UptimeTable.css"

/* Vehicle Uptime Calendar -- "Design A: refined uptime calendar".
 * Same props, same API calls and same export as before; only the presentation
 * and the in-panel filters changed. Styles live in UptimeTable.css (all classes
 * prefixed "uc-") so nothing else on the page is affected. */

type StatusFilter = "all" | CombinedUptimeStatus
type SortKey = "vehicle_number" | "uptime_pct"
type WindowSize = 7 | 14 | 30 | "all"

const STATUS_CLASS: Record<CombinedUptimeStatus, string> = {
  RUNNING_STABLE: "uc-s",
  RUNNING_UNSTABLE: "uc-u",
  STOPPED_STABLE: "uc-x",
  NOT_ONBOARDED: "uc-n",
}

const FALLBACK_LABEL: Record<CombinedUptimeStatus, string> = {
  RUNNING_STABLE: "Running, stable",
  RUNNING_UNSTABLE: "Running, unstable",
  STOPPED_STABLE: "Stopped, stable",
  NOT_ONBOARDED: "Not onboarded yet",
}

const STATUS_ORDER: CombinedUptimeStatus[] = ["STOPPED_STABLE", "RUNNING_UNSTABLE", "RUNNING_STABLE", "NOT_ONBOARDED"]
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
const WEEKDAYS = ["S", "M", "T", "W", "T", "F", "S"]

function parseIso(iso: string) {
  const [y, m, d] = iso.split("-").map(Number)
  const wd = new Date(Date.UTC(y, m - 1, d)).getUTCDay()
  return { day: d, mon: MONTHS[m - 1], wd, label: `${d} ${MONTHS[m - 1]}` }
}

function uptimeTone(p: number | null): "low" | "mid" | "high" | "none" {
  if (p === null) return "none"
  return p < 20 ? "low" : p < 35 ? "mid" : "high"
}

export function UptimeTable({ range, customer }: { range: DateRange; customer: CrosstabCustomer }) {
  const [data, setData] = useState<UptimeResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [exporting, setExporting] = useState(false)

  const [search, setSearch] = useState("")
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all")
  const [localCustomer, setLocalCustomer] = useState<string>("all")
  const [groupByCustomer, setGroupByCustomer] = useState(false)
  const [sortKey, setSortKey] = useState<SortKey>("uptime_pct")
  const [sortDir, setSortDir] = useState<1 | -1>(1)
  const [windowSize, setWindowSize] = useState<WindowSize>(30)

  const scrollRef = useRef<HTMLDivElement>(null)

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

  // Page-level customer filter changed -> reset the in-panel customer filter.
  useEffect(() => setLocalCustomer("all"), [customer])

  const labels = useMemo(() => {
    const out: Record<string, string> = { ...FALLBACK_LABEL }
    data?.legend_combined.forEach((l) => (out[l.status] = l.label))
    return out
  }, [data])

  const detailedLabels = useMemo(
    () => (data ? Object.fromEntries(data.legend_detailed.map((l) => [l.status, l.label])) : {}) as Record<string, string>,
    [data]
  )

  // The API may send dates newest-first; always work oldest -> newest so the
  // latest day sits on the right and the window picks the most recent days.
  const sortedDates = useMemo(() => (data ? [...data.dates].sort() : []), [data])

  // Visible date window (always the most recent N days of the loaded range).
  const visibleDates = useMemo(() => {
    if (windowSize === "all" || sortedDates.length <= windowSize) return sortedDates
    return sortedDates.slice(sortedDates.length - windowSize)
  }, [sortedDates, windowSize])

  const latestDate = sortedDates.length ? sortedDates[sortedDates.length - 1] : null

  // Index each vehicle's days by date so cells can never drift out of step with headers.
  const dayIndex = useMemo(() => {
    const m = new Map<string, Map<string, UptimeDay>>()
    data?.vehicles.forEach((v) => m.set(v.vehicle_number, new Map(v.daily.map((d) => [d.date, d]))))
    return m
  }, [data])

  const latestStatus = (v: VehicleUptime): CombinedUptimeStatus =>
    (latestDate && dayIndex.get(v.vehicle_number)?.get(latestDate)?.combined_status) || "NOT_ONBOARDED"

  const customersInData = useMemo(() => {
    if (!data) return []
    return Array.from(new Set(data.vehicles.map((v) => v.customer_name))).sort((a, b) =>
      a.toLowerCase().localeCompare(b.toLowerCase())
    )
  }, [data])

  const summary = useMemo(() => {
    const counts: Record<CombinedUptimeStatus, number> = {
      RUNNING_STABLE: 0,
      RUNNING_UNSTABLE: 0,
      STOPPED_STABLE: 0,
      NOT_ONBOARDED: 0,
    }
    if (!data) return { counts, avg: null as number | null, below20: 0, total: 0 }
    data.vehicles.forEach((v) => counts[latestStatus(v)]++)
    const withPct = data.vehicles.filter((v) => v.uptime_pct !== null)
    const avg = withPct.length ? withPct.reduce((t, v) => t + (v.uptime_pct as number), 0) / withPct.length : null
    const below20 = withPct.filter((v) => (v.uptime_pct as number) < 20).length
    return { counts, avg, below20, total: data.vehicles.length }
  }, [data, dayIndex, latestDate])

  const rows = useMemo(() => {
    if (!data) return []
    const q = search.trim().toLowerCase()
    const list = data.vehicles.filter((v) => {
      if (statusFilter !== "all" && latestStatus(v) !== statusFilter) return false
      if (localCustomer !== "all" && v.customer_name !== localCustomer) return false
      if (
        q &&
        !v.vehicle_number.toLowerCase().includes(q) &&
        !v.customer_name.toLowerCase().includes(q) &&
        !v.vehicle_model.toLowerCase().includes(q)
      )
        return false
      return true
    })
    list.sort((a, b) => {
      if (sortKey === "vehicle_number") return sortDir * a.vehicle_number.localeCompare(b.vehicle_number)
      const an = a.uptime_pct ?? -Infinity
      const bn = b.uptime_pct ?? -Infinity
      return sortDir * (an - bn)
    })
    return list
  }, [data, dayIndex, latestDate, search, statusFilter, localCustomer, sortKey, sortDir])

  const groups = useMemo(() => {
    if (!groupByCustomer) return null
    return customersInData
      .map((c) => {
        const vs = rows.filter((v) => v.customer_name === c)
        const pcts = vs.filter((v) => v.uptime_pct !== null).map((v) => v.uptime_pct as number)
        const avg = pcts.length ? pcts.reduce((t, p) => t + p, 0) / pcts.length : null
        return { name: c, vehicles: vs, avg }
      })
      .filter((g) => g.vehicles.length > 0)
  }, [groupByCustomer, customersInData, rows])

  // Keep the most recent day (leftmost) in view whenever the data or window changes.
  useEffect(() => {
    const el = scrollRef.current
    if (el) el.scrollLeft = 0
  }, [data, windowSize, groupByCustomer])

  function sortBy(key: SortKey) {
    if (sortKey === key) setSortDir((d) => (d === 1 ? -1 : 1))
    else {
      setSortKey(key)
      setSortDir(1)
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

  // Displayed newest -> oldest (latest day on the left).
  const displayDates = [...visibleDates].reverse()
  const headers = displayDates.map((iso, i) => {
    const p = parseIso(iso)
    return {
      iso,
      ...p,
      showMonth: i === 0 || parseIso(displayDates[i - 1]).mon !== p.mon,
      // Week gap sits between Monday (left) and the previous Sunday (right).
      weekStart: p.wd === 0 && i > 0,
      // Wider gap where the month changes.
      monthStart: i > 0 && parseIso(displayDates[i - 1]).mon !== p.mon,
      isLatest: iso === latestDate,
    }
  })

  const sizeClass = windowSize === 7 ? "uc-w7" : windowSize === 14 ? "uc-w14" : "uc-w30"
  const latestLabel = latestDate ? parseIso(latestDate).label : ""
  const rangeLabel =
    // Newest first, matching the calendar's column order.
    visibleDates.length > 0 ? `${parseIso(visibleDates[visibleDates.length - 1]).label} – ${parseIso(visibleDates[0]).label}` : ""

  const renderRow = (v: VehicleUptime) => {
    const days = dayIndex.get(v.vehicle_number)
    const tone = uptimeTone(v.uptime_pct)
    return (
      <div className="uc-row" key={v.vehicle_number}>
        <div className="uc-left">
          <div className="uc-vehicle">
            <div className="uc-plate-line">
              <span className="uc-plate">{v.vehicle_number}</span>
              <span className={`type-pill ${v.vehicle_type === "Bus" ? "bus" : "truck"}`}>{v.vehicle_type}</span>
            </div>
            <span className="uc-model" title={v.vehicle_model}>
              {v.vehicle_model}
            </span>
          </div>
          <span className="uc-customer" title={v.customer_name}>
            {v.customer_name}
          </span>
          <div className="uc-uptime">
            <span className={`uc-pct uc-tone-${tone}`}>{v.uptime_pct !== null ? `${v.uptime_pct.toFixed(1)}%` : "—"}</span>
            <div className="uc-bar">
              <i className={`uc-bar-fill uc-fill-${tone}`} style={{ width: `${Math.max(0, Math.min(100, v.uptime_pct ?? 0))}%` }} />
            </div>
          </div>
        </div>
        <div className="uc-days">
          {headers.map((h) => {
            const day = days?.get(h.iso)
            const status = day?.combined_status ?? "NOT_ONBOARDED"
            const detail = day ? detailedLabels[day.detailed_status] ?? day.detailed_status : "No data"
            const tip = day?.note ?? `${v.vehicle_number} · ${h.label} · ${labels[status]} (${detail})`
            return (
              <span
                key={h.iso}
                className={`uc-cell ${STATUS_CLASS[status]}${h.weekStart ? " uc-wk" : ""}${h.monthStart ? " uc-mo" : ""}${h.isLatest ? " uc-latest" : ""}`}
                title={tip}
              />
            )
          })}
        </div>
      </div>
    )
  }

  const kpis = [
    {
      key: "avg",
      label: "Fleet avg. uptime",
      value: summary.avg !== null ? `${summary.avg.toFixed(1)}%` : "—",
      sub: `Across ${summary.total} vehicles`,
      sw: "uc-sw-neutral",
    },
    { key: "x", label: "Stopped", value: summary.counts.STOPPED_STABLE, sub: `on ${latestLabel}`, sw: "uc-x" },
    { key: "u", label: "Running, unstable", value: summary.counts.RUNNING_UNSTABLE, sub: `on ${latestLabel}`, sw: "uc-u" },
    { key: "s", label: "Running, stable", value: summary.counts.RUNNING_STABLE, sub: `on ${latestLabel}`, sw: "uc-s" },
    { key: "low", label: "Below 20% uptime", value: summary.below20, sub: "Need attention", sw: "uc-x" },
  ]

  const chips: { key: StatusFilter; label: string; n: number; sw: string }[] = [
    { key: "all", label: "All vehicles", n: summary.total, sw: "uc-sw-neutral" },
    ...STATUS_ORDER.map((s) => ({ key: s as StatusFilter, label: labels[s], n: summary.counts[s], sw: STATUS_CLASS[s] })),
  ]

  return (
    <div className="panel uc-panel" id="uptime-calendar">
      <div className="panel-header uc-header">
        <div className="uc-title">
          <span>Vehicle Uptime Calendar</span>
          {data && (
            <span className="uc-subtitle">
              {summary.total} vehicles · {customersInData.length} customers · {rangeLabel}
            </span>
          )}
        </div>
        <div className="uc-header-actions">
          <div className="uc-seg" role="group" aria-label="Days shown">
            {([7, 14, 30, "all"] as WindowSize[]).map((w) => (
              <button
                key={String(w)}
                type="button"
                className={`uc-seg-btn${windowSize === w ? " on" : ""}`}
                aria-pressed={windowSize === w}
                onClick={() => setWindowSize(w)}
              >
                {w === "all" ? "All" : `${w}d`}
              </button>
            ))}
          </div>
          <button className="btn-action" disabled={exporting} onClick={() => handleExport()}>
            {exporting ? "Exporting…" : "⬇️ Export"}
          </button>
        </div>
      </div>

      <div className="panel-body uc-body">
        {error && <div className="error-box">Failed to load: {error}</div>}
        {!error && !data && <div className="loading">Loading uptime calendar…</div>}

        {data && data.vehicles.length === 0 && (
          <div className="muted" style={{ padding: "1rem 0" }}>
            No vehicles found for this filter/date range.
          </div>
        )}

        {data && data.vehicles.length > 0 && (
          <>
            <div className="uc-kpis">
              {kpis.map((k) => (
                <div className="uc-kpi" key={k.key}>
                  <div className="uc-kpi-label">
                    <span className={`uc-sw ${k.sw}`} />
                    {k.label}
                  </div>
                  <div className="uc-kpi-value">{k.value}</div>
                  <div className="uc-kpi-sub">{k.sub}</div>
                </div>
              ))}
            </div>

            <div className="uc-card">
              <div className="uc-toolbar">
                <div className="uc-chips">
                  {chips.map((c) => (
                    <button
                      key={c.key}
                      type="button"
                      className={`uc-chip${statusFilter === c.key ? " on" : ""}`}
                      aria-pressed={statusFilter === c.key}
                      onClick={() => setStatusFilter(c.key)}
                    >
                      <span className={`uc-sw ${c.sw}`} />
                      {c.label}
                      <b>{c.n}</b>
                    </button>
                  ))}
                </div>
                <div className="uc-filters">
                  <input
                    type="search"
                    className="uc-input"
                    aria-label="Search vehicles"
                    placeholder="Search plate, model, customer…"
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                  />
                  {customersInData.length > 1 && (
                    <select
                      className="uc-input"
                      aria-label="Customer"
                      value={localCustomer}
                      onChange={(e) => setLocalCustomer(e.target.value)}
                    >
                      <option value="all">All customers</option>
                      {customersInData.map((c) => (
                        <option key={c} value={c}>
                          {c}
                        </option>
                      ))}
                    </select>
                  )}
                  <button
                    type="button"
                    className={`uc-toggle${groupByCustomer ? " on" : ""}`}
                    aria-pressed={groupByCustomer}
                    onClick={() => setGroupByCustomer((g) => !g)}
                  >
                    Group by customer
                  </button>
                </div>
              </div>

              <div className="uc-legend">
                {STATUS_ORDER.map((s) => (
                  <span key={s} className="uc-legend-item">
                    <span className={`uc-sw ${STATUS_CLASS[s]}`} />
                    {labels[s]}
                  </span>
                ))}
                <span className="uc-legend-hint">Outlined cell = latest day · hover a cell for details</span>
              </div>

              <div className="uc-scroll" ref={scrollRef}>
                <div className={`uc-grid ${sizeClass}`}>
                  <div className="uc-row uc-head">
                    <div className="uc-left">
                      <button type="button" className="uc-sort uc-vehicle" onClick={() => sortBy("vehicle_number")}>
                        Vehicle{sortKey === "vehicle_number" ? (sortDir === 1 ? " ↑" : " ↓") : ""}
                      </button>
                      <span className="uc-customer">Customer</span>
                      <button type="button" className="uc-sort uc-uptime" onClick={() => sortBy("uptime_pct")}>
                        Uptime{sortKey === "uptime_pct" ? (sortDir === 1 ? " ↑" : " ↓") : ""}
                      </button>
                    </div>
                    <div className="uc-days">
                      {headers.map((h) => (
                        <div
                          key={h.iso}
                          className={`uc-hd${h.weekStart ? " uc-wk" : ""}${h.monthStart ? " uc-mo" : ""}${h.isLatest ? " uc-hd-latest" : ""}`}
                        >
                          <span className="uc-hd-mon">{h.showMonth ? h.mon : ""}</span>
                          <span>{WEEKDAYS[h.wd]}</span>
                          <span>{h.day}</span>
                        </div>
                      ))}
                    </div>
                  </div>

                  {groups
                    ? groups.map((g) => (
                        <div key={g.name}>
                          <div className="uc-group">
                            <span className="uc-group-name">{g.name}</span>
                            <span className="uc-group-meta">
                              {g.vehicles.length} {g.vehicles.length === 1 ? "vehicle" : "vehicles"}
                              {g.avg !== null ? ` · avg uptime ${g.avg.toFixed(1)}%` : ""}
                            </span>
                          </div>
                          {g.vehicles.map(renderRow)}
                        </div>
                      ))
                    : rows.map(renderRow)}

                  {rows.length === 0 && <div className="uc-empty">No vehicles match these filters.</div>}
                </div>
              </div>

              <div className="uc-footer">
                Showing {rows.length} of {summary.total} vehicles
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
