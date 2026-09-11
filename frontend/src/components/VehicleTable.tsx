import { useMemo, useState } from "react"
import type { VehicleRecord } from "../api/types"
import { Sparkline } from "./Sparkline"

type SortKey = keyof Pick<
  VehicleRecord,
  | "plate"
  | "customer"
  | "active_rate_pct"
  | "avg_km_per_day"
  | "avg_hours_per_day"
  | "total_distance"
  | "total_hours"
  | "final_odometer"
  | "mileage_km_soc"
  | "active_cv_pct"
>

/** No sort/filter exists on the original static page (verified against its
 * client JS) -- this is net-new. The roster is small (<=40 rows), so a
 * client-side sort is simplest, no backend round-trip needed. */
export function VehicleTable({ vehicles, showType = false }: { vehicles: VehicleRecord[]; showType?: boolean }) {
  const [sortKey, setSortKey] = useState<SortKey | null>(null)
  const [sortDir, setSortDir] = useState<1 | -1>(1)

  const rows = useMemo(() => {
    if (!sortKey) return vehicles
    const copy = [...vehicles]
    copy.sort((a, b) => {
      const av = a[sortKey]
      const bv = b[sortKey]
      const an = av === null ? -Infinity : (av as number)
      const bn = bv === null ? -Infinity : (bv as number)
      if (typeof an === "string" || typeof bn === "string") {
        return sortDir * String(av).localeCompare(String(bv))
      }
      return sortDir * (an - bn)
    })
    return copy
  }, [vehicles, sortKey, sortDir])

  function headerClick(key: SortKey) {
    if (sortKey === key) {
      setSortDir((d) => (d === 1 ? -1 : 1))
    } else {
      setSortKey(key)
      setSortDir(1)
    }
  }

  return (
    <div className="table-container">
      <table>
        <thead>
          <tr>
            <th>#</th>
            <th onClick={() => headerClick("plate")}>License Plate</th>
            {showType && <th>Type</th>}
            <th onClick={() => headerClick("customer")}>Customer</th>
            <th>Model</th>
            <th onClick={() => headerClick("active_rate_pct")}>Active Days (% Avail)</th>
            <th onClick={() => headerClick("avg_km_per_day")}>Avg km/day</th>
            <th onClick={() => headerClick("avg_hours_per_day")}>Daily Hours</th>
            <th onClick={() => headerClick("total_distance")}>Total Distance</th>
            <th onClick={() => headerClick("total_hours")}>Total Hours</th>
            <th onClick={() => headerClick("final_odometer")}>Odometer</th>
            <th onClick={() => headerClick("mileage_km_soc")}>Mileage (km/SoC)</th>
            <th>Est. Daily SoC</th>
            <th onClick={() => headerClick("active_cv_pct")}>Daily KM Volatility</th>
            <th>30-Day Trend</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((v) => (
            <tr key={v.plate} className={v.is_below_80 ? "row-below-80" : ""}>
              <td className="rank-cell">{v.rank}</td>
              <td className="plate-cell">{v.plate}</td>
              {showType && (
                <td>
                  <span className={`type-pill ${v.vehicle_type === "Bus" ? "bus" : "truck"}`}>
                    {v.vehicle_type}
                  </span>
                </td>
              )}
              <td style={{ fontWeight: 600, color: "var(--accent-brand)" }}>{v.customer}</td>
              <td>{v.vehicle_model}</td>
              <td style={{ textAlign: "center" }}>
                {v.active_days}/{Math.round(v.total_days)} d{" "}
                <span className={`rate-pill ${v.is_below_80 ? "rate-alert" : "rate-ok"}`}>
                  {v.is_below_80 ? "⚠️ " : ""}
                  {v.active_rate_pct.toFixed(1)}%
                </span>
              </td>
              <td style={{ textAlign: "right", fontWeight: 600 }}>{v.avg_km_per_day.toFixed(0)} km/d</td>
              <td style={{ textAlign: "right" }}>{v.avg_hours_per_day.toFixed(1)} h/d</td>
              <td style={{ textAlign: "right" }}>{v.total_distance.toLocaleString()} km</td>
              <td style={{ textAlign: "right" }}>{v.total_hours.toFixed(1)} h</td>
              <td style={{ textAlign: "right", fontWeight: 600 }}>
                {v.final_odometer !== null ? `${v.final_odometer.toLocaleString()} km` : "—"}
              </td>
              <td className="mileage-center">
                {v.mileage_km_soc !== null ? `${v.mileage_km_soc.toFixed(1)} km/SoC` : "—"}
              </td>
              <td className="soc-center">
                {v.est_daily_soc_pct !== null ? `${v.est_daily_soc_pct.toFixed(0)}% SoC/d` : "—"}
              </td>
              <td className="volatility-center">
                {v.active_days_count > 1 ? (
                  <span
                    className={`volatility-tag ${v.volatility_class}`}
                    title={`${v.volatility_tier} • σ = ±${v.active_std_dist.toFixed(0)} km`}
                  >
                    {v.active_cv_pct.toFixed(0)}%
                  </span>
                ) : (
                  <span className="muted" style={{ fontSize: "0.75rem" }}>
                    —
                  </span>
                )}
              </td>
              <td style={{ textAlign: "center", padding: "4px 6px" }}>
                <Sparkline values={v.daily_distance_history} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
